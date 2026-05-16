import os
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import ta
from dotenv import load_dotenv

from database import init_db, insert_candle, get_candles, upsert_indicators
from config import COINBASE_API_KEY, COINBASE_API_SECRET, SYMBOL, DB_PATH

# Load /root/xrp_grid/.env so LETTA_BASE_URL / LETTA_SERVER_PASSWORD are
# available for the outcome-backfill agent notifications.
load_dotenv()

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s')
log = logging.getLogger('observer')

COINBASE_REST = "https://api.coinbase.com/api/v3/brokerage"

# Lazy-initialised exchange instance for XRP candle fetches when EXCHANGE != "coinbase"
_xrp_exchange = None


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


def get_candles_xrp(granularity: str, limit: int = 300) -> list:
    """Fetch XRP candles from whichever exchange is configured.

    BTC candles always stay on Coinbase — BTC is a market-context signal only.
    """
    global _xrp_exchange
    from config import EXCHANGE
    if EXCHANGE == "coinbase":
        return get_candles_coinbase("XRP-USD", granularity, limit)
    elif EXCHANGE == "kraken":
        if _xrp_exchange is None:
            from grid.exchanges.kraken import KrakenExchange
            _xrp_exchange = KrakenExchange(symbol="XRP-USD")
        return _xrp_exchange.get_candles(granularity, limit)
    else:
        log.error(f"Unknown EXCHANGE for XRP candles: {EXCHANGE}")
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

    # Initialise all indicator keys to None so upsert always writes every column,
    # preventing stale values from persisting when a computation fails.
    result['vwap'] = None
    result['vwap_dev_pct'] = None
    result['atr'] = None
    result['atr_percentile'] = None
    result['vol_regime'] = None
    result['autocorr_1h'] = None
    result['autocorr_4h'] = None
    result['ema_50'] = None
    result['ema_200'] = None
    result['adx'] = None
    result['adx_pos'] = None
    result['adx_neg'] = None
    result['roc_6h'] = None
    result['bb_width'] = None
    result['bb_upper'] = None
    result['bb_lower'] = None
    result['btc_ema_50'] = None
    result['btc_ema_200'] = None

    # VWAP (1h data, rolling 24 periods)
    try:
        typical = (df1h['high'] + df1h['low'] + df1h['close']) / 3
        vwap = (typical * df1h['volume']).rolling(24).sum() / df1h['volume'].rolling(24).sum()
        result['vwap'] = round(float(vwap.iloc[-1]), 6)
        result['vwap_dev_pct'] = round(
            (df1h['close'].iloc[-1] - vwap.iloc[-1]) / vwap.iloc[-1] * 100, 4)
    except Exception as e:
        log.warning(f"VWAP error: {e}")
        result['vwap'] = None
        result['vwap_dev_pct'] = None

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
        result['atr'] = None
        result['atr_percentile'] = None
        result['vol_regime'] = None

    # Autocorrelation (1h returns)
    try:
        returns = df1h['close'].pct_change().dropna()
        result['autocorr_1h'] = round(float(returns.autocorr(lag=1)), 4)
        result['autocorr_4h'] = round(float(returns.autocorr(lag=4)), 4)
    except Exception as e:
        log.warning(f"Autocorr error: {e}")
        result['autocorr_1h'] = None
        result['autocorr_4h'] = None

    # EMA 50/200 daily
    if len(df1d) >= 50:
        try:
            result['ema_50'] = round(float(ta.trend.EMAIndicator(df1d['close'], window=50).ema_indicator().iloc[-1]), 6)
            result['ema_200'] = round(float(ta.trend.EMAIndicator(df1d['close'], window=200).ema_indicator().iloc[-1]), 6) if len(df1d) >= 200 else None
        except Exception as e:
            log.warning(f"EMA error: {e}")
            result['ema_50'] = None
            result['ema_200'] = None

    # ADX daily
    if len(df1d) >= 14:
        try:
            adx = ta.trend.ADXIndicator(df1d['high'], df1d['low'], df1d['close'], window=14)
            result['adx'] = round(float(adx.adx().iloc[-1]), 4)
            result['adx_pos'] = round(float(adx.adx_pos().iloc[-1]), 4)
            result['adx_neg'] = round(float(adx.adx_neg().iloc[-1]), 4)
        except Exception as e:
            log.warning(f"ADX error: {e}")
            result['adx'] = None
            result['adx_pos'] = None
            result['adx_neg'] = None

    # ROC 6h
    if len(df6h) >= 6:
        try:
            result['roc_6h'] = round(float(ta.momentum.ROCIndicator(df6h['close'], window=6).roc().iloc[-1]), 4)
        except Exception as e:
            log.warning(f"ROC error: {e}")
            result['roc_6h'] = None

    # Bollinger Band Width daily
    if len(df1d) >= 20:
        try:
            bb = ta.volatility.BollingerBands(df1d['close'], window=20, window_dev=2)
            result['bb_width'] = round(float(bb.bollinger_wband().iloc[-1]), 6)
            result['bb_upper'] = round(float(bb.bollinger_hband().iloc[-1]), 6)
            result['bb_lower'] = round(float(bb.bollinger_lband().iloc[-1]), 6)
        except Exception as e:
            log.warning(f"BB error: {e}")
            result['bb_width'] = None
            result['bb_upper'] = None
            result['bb_lower'] = None

    # BTC EMA context — always from Coinbase regardless of EXCHANGE setting
    if len(dfbtc) >= 50:
        try:
            result['btc_ema_50'] = round(float(ta.trend.EMAIndicator(dfbtc['close'], window=50).ema_indicator().iloc[-1]), 2)
            result['btc_ema_200'] = round(float(ta.trend.EMAIndicator(dfbtc['close'], window=200).ema_indicator().iloc[-1]), 2) if len(dfbtc) >= 200 else None
        except Exception as e:
            log.warning(f"BTC EMA error: {e}")
            result['btc_ema_50'] = None
            result['btc_ema_200'] = None

    return result

# --- Phase 5: outcome backfill for debate_records ---

WINDOW_HOURS = {"1h": 1, "6h": 6, "24h": 24}

# Lazy module-level Letta client — initialised on first 6h notification.
_letta_client = None


def _get_letta_client():
    """Lazy-init a Letta Cloud client. Returns None if env var is missing or
    the SDK can't be imported / connected — caller logs and moves on.
    Letta Cloud is the SDK default when only api_key is passed."""
    global _letta_client
    if _letta_client is not None:
        return _letta_client
    api_key = os.environ.get("LETTA_API_KEY")
    if not api_key:
        log.warning("LETTA_API_KEY missing — Letta notifications disabled")
        return None
    try:
        from letta_client import Letta
        _letta_client = Letta(api_key=api_key)
    except Exception as e:
        log.warning(f"Could not init Letta client: {e}")
        return None
    return _letta_client


def _parse_iso_safe(ts):
    """Parse ISO timestamp from DB. Returns naive UTC datetime, or None."""
    if not ts:
        return None
    try:
        s = ts.replace('Z', '+00:00') if ts.endswith('Z') else ts
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception as e:
        log.warning(f"Could not parse timestamp {ts!r}: {e}")
        return None


def _compute_window_metrics(cycle_start, cycle_end):
    """
    Return (fills_count, realized_pnl) for fills with filled_at (or timestamp
    fallback) in [cycle_start, cycle_end). Realized P&L is FIFO-matched across
    the FULL fills history (so sells in window can match buys from before the
    window), then summed only for the sells whose fill time fell within window.
    """
    from database import get_conn
    from grid.pnl import _fifo_match

    conn = get_conn()
    rows = conn.execute('''
        SELECT order_id, side, price, size, fill_price, fee, filled_at, timestamp
        FROM grid_orders
        WHERE status='filled'
        ORDER BY COALESCE(filled_at, timestamp) ASC
    ''').fetchall()
    conn.close()

    fills = []
    for r in rows:
        f = dict(r)
        ft = f.get('filled_at') or f.get('timestamp')
        f['_dt'] = _parse_iso_safe(ft)
        if f['_dt'] is None:
            continue
        fills.append(f)

    in_window = [f for f in fills if cycle_start <= f['_dt'] < cycle_end]
    fills_count = len(in_window)

    if not fills:
        return 0, 0.0

    matched, _unmatched = _fifo_match(fills)
    pnl_per_sell = {}
    for t in matched:
        pnl_per_sell[t['sell_id']] = pnl_per_sell.get(t['sell_id'], 0.0) + t['contribution']

    realized = 0.0
    for f in in_window:
        if f['side'] == 'sell':
            realized += pnl_per_sell.get(f['order_id'], 0.0)

    return fills_count, round(realized, 4)


def _get_skew_at_or_before(timestamp_dt):
    """Most recent inventory.inventory_skew row with timestamp <= given dt.
    Returns float or None. Inventory timestamps are written naive UTC ISO,
    matching cycle timestamps — lexicographic compare works correctly."""
    if timestamp_dt is None:
        return None
    from database import get_conn
    iso = (timestamp_dt.replace(tzinfo=None) if timestamp_dt.tzinfo
           else timestamp_dt).isoformat()
    conn = get_conn()
    row = conn.execute(
        "SELECT inventory_skew FROM inventory WHERE timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (iso,)
    ).fetchone()
    conn.close()
    if row and row['inventory_skew'] is not None:
        return float(row['inventory_skew'])
    return None


def _build_outcome_message(agent_id, cycle_id, r0_row, fills_count,
                            pnl, skew_delta, grid_alive):
    """Per-agent outcome notification text. Frames the agent's r0 call by
    its appropriate role term (regime / grid_action / risk_action)."""
    role_term = {
        'casper':    'regime',
        'melchior':  'grid_action',
        'balthasar': 'risk_action',
    }[agent_id]
    position   = r0_row.get(f"{agent_id}_r0_position") or "?"
    conviction = r0_row.get(f"{agent_id}_r0_conviction") or 0.0
    crux       = r0_row.get(f"{agent_id}_r0_crux") or "(no crux)"
    skew_str   = f"{float(skew_delta):+.3f}" if skew_delta is not None else "n/a"
    return (
        f"Outcome for cycle {cycle_id} (your call: {role_term}={position}, "
        f"conviction={float(conviction):.2f}, crux=\"{crux}\"): over the next "
        f"6 hours the grid produced {fills_count} fills with P&L "
        f"${float(pnl):.4f} and skew_delta {skew_str}. "
        f"Grid alive: {'yes' if grid_alive else 'no'}. Consider whether to "
        f"update your self_model based on this outcome."
    )


def _send_outcome_to_agent(client, agent_id, letta_agent_id, message):
    """Send the outcome notification as a USER message. Returns (ok, err)."""
    try:
        client.agents.messages.create(
            letta_agent_id,
            messages=[{"role": "user", "content": message}],
            timeout=30.0,
        )
        return True, None
    except Exception as e:
        return False, repr(e)


def _notify_agents_6h(cycle_id, fills_count, pnl, skew_delta, grid_alive):
    """Notify all three Letta agents in parallel with per-agent error isolation."""
    from database import get_conn, get_letta_agent_id

    client = _get_letta_client()
    if client is None:
        return

    conn = get_conn()
    r0_row = conn.execute(
        "SELECT casper_r0_position, casper_r0_conviction, casper_r0_crux,"
        " melchior_r0_position, melchior_r0_conviction, melchior_r0_crux,"
        " balthasar_r0_position, balthasar_r0_conviction, balthasar_r0_crux"
        " FROM debate_records WHERE cycle_id=?",
        (cycle_id,)
    ).fetchone()
    conn.close()
    if not r0_row:
        log.warning(f"backfill: cycle {cycle_id} missing from debate_records — "
                    "skipping agent notifications")
        return
    r0_row = dict(r0_row)

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for agent_id in ('casper', 'melchior', 'balthasar'):
            letta_id = get_letta_agent_id(agent_id)
            if not letta_id:
                log.warning(f"backfill: no letta_agent_id for {agent_id}")
                continue
            msg = _build_outcome_message(
                agent_id, cycle_id, r0_row, fills_count, pnl, skew_delta, grid_alive
            )
            futures[pool.submit(
                _send_outcome_to_agent, client, agent_id, letta_id, msg
            )] = agent_id

        for fut, a in futures.items():
            try:
                ok, err = fut.result()
            except Exception as e:
                ok, err = False, repr(e)
            if ok:
                log.info(f"backfill: notified {a} of cycle {cycle_id} 6h outcome")
            else:
                log.warning(f"backfill: failed to notify {a} of {cycle_id}: {err}")


def backfill_outcomes():
    """
    Update debate_records with realised outcomes for the 1h / 6h / 24h
    windows whose timestamps are now mature. Only the 6h backfill notifies
    Letta agents — we want one outcome message per cycle per agent, not
    three.
    """
    from database import get_pending_outcome_backfills, update_debate_outcomes

    for window in ('1h', '6h', '24h'):
        try:
            pending = get_pending_outcome_backfills(window)
        except Exception as e:
            log.error(f"backfill: get_pending_outcome_backfills({window}) failed: {e}")
            continue

        if not pending:
            continue

        log.info(f"backfill: {len(pending)} cycle(s) pending {window} backfill")
        hours = WINDOW_HOURS[window]

        for row in pending:
            cycle_id = row['cycle_id']
            cycle_start = _parse_iso_safe(row['timestamp'])
            if cycle_start is None:
                log.warning(f"backfill: bad timestamp for {cycle_id} — skip")
                continue
            cycle_end = cycle_start + timedelta(hours=hours)

            try:
                fills_count, pnl_value = _compute_window_metrics(
                    cycle_start, cycle_end
                )
            except Exception as e:
                log.error(f"backfill: metrics for {cycle_id} {window} failed: {e}")
                continue

            try:
                if window == '6h':
                    skew_start = _get_skew_at_or_before(cycle_start)
                    skew_end   = _get_skew_at_or_before(cycle_end)
                    skew_delta = ((skew_end - skew_start)
                                  if (skew_start is not None and skew_end is not None)
                                  else None)
                    grid_alive = 1 if fills_count > 0 else 0
                    update_debate_outcomes(
                        cycle_id, '6h', fills_count, pnl_value,
                        skew_delta=skew_delta, grid_alive=grid_alive,
                    )
                    log.info(
                        f"backfill: {cycle_id} 6h → fills={fills_count} "
                        f"pnl=${pnl_value:.4f} skew_delta={skew_delta} "
                        f"grid_alive={grid_alive}"
                    )
                    _notify_agents_6h(
                        cycle_id, fills_count, pnl_value,
                        skew_delta if skew_delta is not None else 0.0,
                        grid_alive,
                    )
                else:
                    update_debate_outcomes(cycle_id, window, fills_count, pnl_value)
                    log.info(
                        f"backfill: {cycle_id} {window} → fills={fills_count} "
                        f"pnl=${pnl_value:.4f}"
                    )
            except Exception as e:
                log.error(f"backfill: update for {cycle_id} {window} failed: {e}")


def poll_cycle():
    """One full data collection cycle."""
    log.info("Poll cycle starting")

    # Fetch candles — XRP from configured exchange, BTC always from Coinbase
    xrp_1h = get_candles_xrp("ONE_HOUR", 300)
    xrp_6h = get_candles_xrp("SIX_HOUR", 100)
    xrp_1d = get_candles_xrp("ONE_DAY", 300)
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

    # Phase 5: backfill realised outcomes for matured debate_records cycles.
    # Failures here must not break the rest of the poll cycle.
    try:
        backfill_outcomes()
    except Exception as e:
        log.error(f"backfill_outcomes failed: {e}")

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
