import time
import logging
import pandas as pd
import ta
from datetime import datetime, timezone
import requests
from database import init_db, insert_candle, get_candles, upsert_indicators
from config import COINBASE_API_KEY, COINBASE_API_SECRET, SYMBOL, DB_PATH

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s')
log = logging.getLogger('observer')

COINBASE_REST = "https://api.coinbase.com/api/v3/brokerage"

def get_candles_coinbase(product_id, granularity, limit=300):
    """Fetch OHLCV candles from Coinbase Advanced REST API."""
    url = f"{COINBASE_REST}/market/products/{product_id}/candles"
    params = {"granularity": granularity, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("candles", [])
        candles = []
        for c in data:
            candles.append({
                "timestamp": datetime.fromtimestamp(int(c["start"]), tz=timezone.utc).isoformat(),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"])
            })
        return sorted(candles, key=lambda x: x["timestamp"])
    except Exception as e:
        log.error(f"Candle fetch error {product_id}: {e}")
        return []

def compute_indicators(candles_1h, candles_6h, candles_1d, btc_candles_1d):
    """Compute all technical indicators from candle data."""
    if len(candles_1h) < 50:
        log.warning("Insufficient 1h candles for indicators")
        return None

    df1h = pd.DataFrame(candles_1h)
    df6h = pd.DataFrame(candles_6h) if candles_6h else pd.DataFrame()
    df1d = pd.DataFrame(candles_1d) if candles_1d else pd.DataFrame()
    dfbtc = pd.DataFrame(btc_candles_1d) if btc_candles_1d else pd.DataFrame()

    for df in [df1h, df6h, df1d, dfbtc]:
        if not df.empty:
            df['close'] = pd.to_numeric(df['close'])
            df['high'] = pd.to_numeric(df['high'])
            df['low'] = pd.to_numeric(df['low'])
            df['volume'] = pd.to_numeric(df['volume'])

    result = {"timestamp": df1h.iloc[-1]["timestamp"], "timeframe": "1h"}

    # VWAP (1h data, rolling 24 periods)
    try:
        typical = (df1h['high'] + df1h['low'] + df1h['close']) / 3
        vwap = (typical * df1h['volume']).rolling(24).sum() / df1h['volume'].rolling(24).sum()
        result['vwap'] = round(float(vwap.iloc[-1]), 6)
        result['vwap_dev_pct'] = round(
            (df1h['close'].iloc[-1] - vwap.iloc[-1]) / vwap.iloc[-1] * 100, 4)
    except Exception as e:
        log.warning(f"VWAP error: {e}")

    # ATR and vol regime (1h)
    try:
        atr = ta.volatility.AverageTrueRange(df1h['high'], df1h['low'], df1h['close'], window=14)
        atr_series = atr.average_true_range()
        result['atr'] = round(float(atr_series.iloc[-1]), 6)
        pct = atr_series.rank(pct=True).iloc[-1] * 100
        result['atr_percentile'] = round(float(pct), 2)
        from config import VOL_REGIME_LOW_PCT, VOL_REGIME_HIGH_PCT
        if pct < VOL_REGIME_LOW_PCT:
            result['vol_regime'] = 'LOW'
        elif pct > VOL_REGIME_HIGH_PCT:
            result['vol_regime'] = 'HIGH'
        else:
            result['vol_regime'] = 'MEDIUM'
    except Exception as e:
        log.warning(f"ATR error: {e}")

    # Autocorrelation (1h returns)
    try:
        returns = df1h['close'].pct_change().dropna()
        result['autocorr_1h'] = round(float(returns.autocorr(lag=1)), 4)
        result['autocorr_4h'] = round(float(returns.autocorr(lag=4)), 4)
    except Exception as e:
        log.warning(f"Autocorr error: {e}")

    # EMA 50/200 daily
    if len(df1d) >= 50:
        try:
            result['ema_50'] = round(float(ta.trend.EMAIndicator(df1d['close'], window=50).ema_indicator().iloc[-1]), 6)
            result['ema_200'] = round(float(ta.trend.EMAIndicator(df1d['close'], window=200).ema_indicator().iloc[-1]), 6) if len(df1d) >= 200 else None
        except Exception as e:
            log.warning(f"EMA error: {e}")

    # ADX daily
    if len(df1d) >= 14:
        try:
            adx = ta.trend.ADXIndicator(df1d['high'], df1d['low'], df1d['close'], window=14)
            result['adx'] = round(float(adx.adx().iloc[-1]), 4)
            result['adx_pos'] = round(float(adx.adx_pos().iloc[-1]), 4)
            result['adx_neg'] = round(float(adx.adx_neg().iloc[-1]), 4)
        except Exception as e:
            log.warning(f"ADX error: {e}")

    # ROC 6h
    if len(df6h) >= 6:
        try:
            result['roc_6h'] = round(float(ta.momentum.ROCIndicator(df6h['close'], window=6).roc().iloc[-1]), 4)
        except Exception as e:
            log.warning(f"ROC error: {e}")

    # Bollinger Band Width daily
    if len(df1d) >= 20:
        try:
            bb = ta.volatility.BollingerBands(df1d['close'], window=20, window_dev=2)
            result['bb_width'] = round(float(bb.bollinger_wband().iloc[-1]), 6)
            result['bb_upper'] = round(float(bb.bollinger_hband().iloc[-1]), 6)
            result['bb_lower'] = round(float(bb.bollinger_lband().iloc[-1]), 6)
        except Exception as e:
            log.warning(f"BB error: {e}")

    # BTC EMA context
    if len(dfbtc) >= 50:
        try:
            result['btc_ema_50'] = round(float(ta.trend.EMAIndicator(dfbtc['close'], window=50).ema_indicator().iloc[-1]), 2)
            result['btc_ema_200'] = round(float(ta.trend.EMAIndicator(dfbtc['close'], window=200).ema_indicator().iloc[-1]), 2) if len(dfbtc) >= 200 else None
        except Exception as e:
            log.warning(f"BTC EMA error: {e}")

    return result

def poll_cycle():
    """One full data collection cycle."""
    log.info("Poll cycle starting")

    # Fetch candles
    xrp_1h = get_candles_coinbase("XRP-USD", "ONE_HOUR", 300)
    xrp_6h = get_candles_coinbase("XRP-USD", "SIX_HOUR", 100)
    xrp_1d = get_candles_coinbase("XRP-USD", "ONE_DAY", 300)
    btc_1d = get_candles_coinbase("BTC-USD", "ONE_DAY", 300)

    # Write candles to DB
    for c in xrp_1h:
        insert_candle(c['timestamp'], '1h', c['open'], c['high'], c['low'], c['close'], c['volume'])
    for c in xrp_1d:
        insert_candle(c['timestamp'], '1d', c['open'], c['high'], c['low'], c['close'], c['volume'])

    # Compute and store indicators
    indicators = compute_indicators(xrp_1h, xrp_6h, xrp_1d, btc_1d)
    if indicators:
        ts = indicators.pop('timestamp')
        tf = indicators.pop('timeframe')
        upsert_indicators(ts, tf, indicators)
        log.info(f"Indicators updated — vol_regime={indicators.get('vol_regime')} vwap_dev={indicators.get('vwap_dev_pct')}")
    else:
        log.warning("No indicators computed this cycle")

def run_daemon(interval_seconds=3600):
    """Run observer as daemon, polling every interval."""
    init_db()
    log.info(f"Observer daemon starting — poll interval {interval_seconds}s")
    while True:
        try:
            poll_cycle()
        except Exception as e:
            log.error(f"Poll cycle error: {e}")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    init_db()
    poll_cycle()
    log.info("Single poll complete.")
