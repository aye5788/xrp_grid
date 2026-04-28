#!/usr/bin/env python3
# =============================================================
# ETH OBSERVER BOT - MAIN
# Runs continuously on your Droplet.
# Collects ETH perp + BTC data, computes signals, logs to SQLite.
# NO trading - observation and data collection only.
# =============================================================

import json
import logging
import logging.handlers
import math
import os
import statistics
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests
from coinbase.websocket import WSClient

import config
import database

# =============================================================
# LOGGING SETUP
# Rotating file handler - max 15MB total, never fills disk
# =============================================================
def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Rotating file handler - 5MB max, keep 3 files
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_PATH,
        maxBytes=config.LOG_MAX_MB * 1024 * 1024,
        backupCount=config.LOG_BACKUPS
    )
    file_handler.setLevel(logging.INFO)

    # Console handler - so you can see output when running manually
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logging.getLogger(__name__)


# =============================================================
# IN-MEMORY STATE
# Small buffers - never accumulate unboundedly
# =============================================================
class MarketState:
    def __init__(self):
        # Current best prices (updated every WebSocket tick)
        self.eth_perp_bid   = None
        self.eth_perp_ask   = None
        self.eth_perp_mid   = None
        self.eth_spot_price = None
        self.btc_price      = None

        # Minute-level aggregation buffer (max 60 entries, one per minute)
        # We build 1-minute candles from ticks, then discard
        self.minute_highs   = deque(maxlen=60)
        self.minute_lows    = deque(maxlen=60)
        self.minute_volumes = deque(maxlen=60)
        self.minute_spreads = deque(maxlen=60)  # spread % each minute

        # Hourly OHLCV accumulators (reset each hour)
        self.hour_open      = None
        self.hour_high      = None
        self.hour_low       = None
        self.hour_volume    = 0.0
        self.btc_hour_open  = None

        # Rolling 24h history for VWAP and vol regime (max 24 entries)
        self.eth_closes_24h = deque(maxlen=24)  # last 24 hourly closes
        self.eth_rets_24h   = deque(maxlen=24)  # last 24 hourly returns

        # Latest polling data
        self.kraken_eth     = None
        self.funding_rate   = None
        self.last_premium   = None  # previous hour premium

        # Current minute tracker
        self.current_minute = None
        self.minute_eth_prices = []  # prices seen this minute

        # Current hour tracker
        self.current_hour   = None

        # Lock for thread safety (polling runs in separate threads)
        self.lock = threading.Lock()


state = MarketState()
logger = None  # set after setup_logging()


# =============================================================
# WEBSOCKET HANDLERS
# =============================================================
def on_message(msg):
    """
    Called for every WebSocket message from Coinbase.
    We process ticker updates for ETH perp, ETH spot, and BTC.
    """
    global state

    try:
        data = json.loads(msg)
        events = data.get("events", [])

        for event in events:
            if event.get("type") != "update":
                continue

            for ticker in event.get("tickers", []):
                product_id = ticker.get("product_id", "")
                price_str  = ticker.get("price") or ticker.get("best_bid")

                if not price_str:
                    continue

                price = float(price_str)
                now   = datetime.now(timezone.utc)

                with state.lock:
                    if product_id == config.ETH_PERP_ID:
                        bid = float(ticker.get("best_bid", price))
                        ask = float(ticker.get("best_ask", price))
                        mid = (bid + ask) / 2

                        state.eth_perp_bid = bid
                        state.eth_perp_ask = ask
                        state.eth_perp_mid = mid
                        state.minute_eth_prices.append(mid)

                        # Track spread
                        if ask > 0:
                            spread_pct = (ask - bid) / mid * 100
                            # We'll average spreads at end of minute

                    elif product_id == config.ETH_SPOT_ID:
                        state.eth_spot_price = price

                    elif product_id == config.BTC_SPOT_ID:
                        state.btc_price = price
                        if state.btc_hour_open is None:
                            state.btc_hour_open = price

                # Check if we've crossed into a new minute
                minute_key = now.strftime("%Y-%m-%d %H:%M")
                if minute_key != state.current_minute:
                    on_minute_close(state.current_minute, now)
                    state.current_minute = minute_key
                    state.minute_eth_prices = []

                # Check if we've crossed into a new hour
                hour_key = now.strftime("%Y-%m-%d %H:00")
                if hour_key != state.current_hour:
                    if state.current_hour is not None:
                        on_hour_close(state.current_hour)
                    state.current_hour = hour_key
                    with state.lock:
                        state.hour_open    = state.eth_perp_mid
                        state.hour_high    = state.eth_perp_mid
                        state.hour_low     = state.eth_perp_mid
                        state.hour_volume  = 0.0
                        state.btc_hour_open = state.btc_price

    except Exception as e:
        if logger:
            logger.error("Error processing WebSocket message: %s", e)


def on_minute_close(minute_str, now):
    """
    Called when a minute completes.
    Aggregates tick data to a 1-minute candle and writes to ticks table.
    """
    if not minute_str or not state.minute_eth_prices:
        return

    with state.lock:
        prices = list(state.minute_eth_prices)
        bid    = state.eth_perp_bid
        ask    = state.eth_perp_ask
        mid    = state.eth_perp_mid
        btc    = state.btc_price
        spot   = state.eth_spot_price

    if not prices or not mid:
        return

    spread     = (ask - bid) if ask and bid else None
    spread_pct = (spread / mid * 100) if spread and mid else None

    # Update hourly trackers
    with state.lock:
        if state.hour_high is None or mid > state.hour_high:
            state.hour_high = mid
        if state.hour_low is None or mid < state.hour_low:
            state.hour_low = mid

        if spread_pct:
            state.minute_spreads.append(spread_pct)

    tick = {
        'timestamp':    minute_str + ":00",
        'eth_perp':     mid,
        'eth_bid':      bid,
        'eth_ask':      ask,
        'eth_spread':   spread,
        'eth_spread_pct': spread_pct,
        'btc_price':    btc,
        'eth_spot':     spot,
        'volume_1m':    None,  # Coinbase ticker doesn't give per-tick volume
    }

    database.write_tick(tick)


def on_hour_close(hour_str):
    """
    Called when an hour completes.
    Computes all signals and writes the hourly row.
    Also checks for signal events.
    """
    if not hour_str:
        return

    logger.info("--- Hour closing: %s ---", hour_str)

    with state.lock:
        eth_close  = state.eth_perp_mid
        eth_open   = state.hour_open
        eth_high   = state.hour_high
        eth_low    = state.hour_low
        btc_close  = state.btc_price
        btc_open   = state.btc_hour_open
        spreads    = list(state.minute_spreads)
        kraken     = state.kraken_eth
        funding    = state.funding_rate
        last_prem  = state.last_premium
        spot       = state.eth_spot_price

    if not eth_close or not eth_open:
        logger.warning("Missing price data for hour %s - skipping", hour_str)
        return

    # --- RETURNS ---
    eth_ret = (eth_close - eth_open) / eth_open * 100 if eth_open else None
    btc_ret = (btc_close - btc_open) / btc_open * 100 if btc_open and btc_close else None

    # ETH/BTC ratio return (ETH-specific component)
    eth_btc_ratio_ret = None
    if btc_ret is not None and eth_ret is not None:
        eth_btc_ratio_ret = eth_ret - btc_ret

    # Update rolling history
    with state.lock:
        state.eth_closes_24h.append(eth_close)
        if eth_ret is not None:
            state.eth_rets_24h.append(eth_ret)

    closes = list(state.eth_closes_24h)
    rets   = list(state.eth_rets_24h)

    # --- VWAP (simple price average, no volume weights - consistent with research) ---
    vwap_24h    = statistics.mean(closes) if len(closes) >= 2 else eth_close
    vwap_dev    = (eth_close - vwap_24h) / vwap_24h * 100 if vwap_24h else None

    # --- VOL REGIME ---
    vol_std = statistics.stdev(rets) if len(rets) >= 3 else None
    if vol_std is None:
        vol_regime = 'unknown'
    elif vol_std >= config.VOL_HIGH_THRESHOLD:
        vol_regime = 'high'
    elif vol_std >= config.VOL_HIGH_THRESHOLD * 0.6:
        vol_regime = 'medium'
    else:
        vol_regime = 'low'

    # --- SPREAD ---
    avg_spread = statistics.mean(spreads) if spreads else None

    # --- PREMIUM PROXY ---
    premium = None
    premium_change = None
    if kraken and eth_close:
        premium = (eth_close - kraken) / kraken * 100
        if last_prem is not None:
            premium_change = premium - last_prem
        with state.lock:
            state.last_premium = premium

    # --- FUNDING ---
    funding_change = None  # We'd need last hour's funding to compute this

    # --- PARSE HOUR DETAILS ---
    try:
        hour_dt     = datetime.strptime(hour_str, "%Y-%m-%d %H:00")
        hour_of_day = hour_dt.hour
        day_of_week = hour_dt.weekday()
    except Exception:
        hour_of_day = None
        day_of_week = None

    # --- SIGNAL DETECTION ---
    signal_long  = 0
    signal_short = 0
    btc_filtered = 0

    if vwap_dev is not None:
        # BTC filter: is this move BTC-driven or ETH-specific?
        btc_too_strong = (
            btc_ret is not None and
            abs(btc_ret) > config.BTC_MOVE_FILTER
        )

        if btc_too_strong:
            btc_filtered = 1
            logger.info("Signal filtered: BTC move %.3f%% too strong", btc_ret)

        elif vwap_dev < -config.VWAP_DEV_THRESHOLD:
            # ETH below VWAP - potential long signal
            signal_long = 1

        elif vwap_dev > config.VWAP_DEV_THRESHOLD:
            # ETH above VWAP - potential short signal
            signal_short = 1

    # --- WRITE HOURLY ROW ---
    hourly_data = {
        'timestamp':        hour_str,
        'hour_of_day':      hour_of_day,
        'day_of_week':      day_of_week,
        'eth_open':         eth_open,
        'eth_high':         eth_high,
        'eth_low':          eth_low,
        'eth_close':        eth_close,
        'eth_volume':       None,
        'btc_open':         btc_open,
        'btc_close':        btc_close,
        'eth_ret_pct':      eth_ret,
        'btc_ret_pct':      btc_ret,
        'eth_btc_ratio_ret': eth_btc_ratio_ret,
        'vwap_24h':         vwap_24h,
        'vwap_dev_pct':     vwap_dev,
        'vol_24h_std':      vol_std,
        'vol_regime':       vol_regime,
        'avg_spread_pct':   avg_spread,
        'kraken_eth':       kraken,
        'premium_pct':      premium,
        'premium_change':   premium_change,
        'funding_rate':     funding,
        'funding_change':   funding_change,
        'signal_long':      signal_long,
        'signal_short':     signal_short,
        'btc_filtered':     btc_filtered,
    }

    database.write_hourly(hourly_data)

    # --- WRITE SIGNAL EVENT IF CONDITIONS MET ---
    if (signal_long or signal_short) and not btc_filtered:
        direction = 'long' if signal_long else 'short'
        event = {
            'timestamp':     hour_str,
            'direction':     direction,
            'eth_price':     eth_close,
            'vwap_dev_pct':  vwap_dev,
            'btc_ret_pct':   btc_ret,
            'premium_pct':   premium,
            'premium_rising': 1 if (premium_change and premium_change > 0) else 0,
            'vol_regime':    vol_regime,
            'funding_rate':  funding,
            'avg_spread_pct': avg_spread,
            'notes':         f"vol_std={vol_std:.4f}" if vol_std else None,
        }
        database.write_signal_event(event)
        logger.info("SIGNAL: %s | ETH=%.2f | VWAP dev=%.3f%% | Vol=%s",
                    direction.upper(), eth_close, vwap_dev, vol_regime)

    # --- UPDATE FORWARD OUTCOMES ---
    database.update_signal_outcomes()

    # --- DAILY CLEANUP (runs at midnight) ---
    if hour_of_day == 0:
        database.prune_old_ticks(days_to_keep=7)
        summary = database.get_signal_summary()
        logger.info("Daily summary: %s", summary)

    logger.info(
        "Hour complete | ETH=%.2f | ret=%.3f%% | BTC ret=%.3f%% | "
        "VWAP dev=%.3f%% | Vol=%s | Premium=%.4f%% | Funding=%.8f",
        eth_close,
        eth_ret or 0,
        btc_ret or 0,
        vwap_dev or 0,
        vol_regime,
        premium or 0,
        funding or 0,
    )


# =============================================================
# REST POLLERS (run in background threads)
# =============================================================
def poll_funding_rate():
    """
    Poll Coinbase REST API for current ETH perp funding rate.
    Uses the public get_product endpoint - no elevated permissions needed.
    Funding rate lives in future_product_details.funding_rate.
    Runs every FUNDING_POLL_MINUTES minutes in a background thread.
    """
    api_key_name, api_key_secret = config.load_api_credentials()

    while True:
        try:
            from coinbase.rest import RESTClient
            client = RESTClient(api_key=api_key_name, api_secret=api_key_secret)

            product = client.get_product(config.ETH_PERP_ID)

            # future_product_details is a plain dict inside the response
            # Access via dict key, not attribute
            details = product['future_product_details']
            if details and 'funding_rate' in details:
                funding = float(details['funding_rate'])
                with state.lock:
                    state.funding_rate = funding
                logger.debug("Funding rate updated: %.8f", funding)

        except Exception as e:
            logger.warning("Funding rate poll failed: %s", e)

        time.sleep(config.FUNDING_POLL_MINUTES * 60)


def poll_kraken_price():
    """
    Poll Kraken public API for ETH/USD price.
    Used to compute Coinbase premium proxy.
    Runs every KRAKEN_POLL_MINUTES minutes in a background thread.
    """
    while True:
        try:
            resp = requests.get(
                config.KRAKEN_URL,
                params={'pair': config.KRAKEN_PAIR, 'interval': 1},
                timeout=15
            )
            data = resp.json()

            if not data.get('error'):
                result = data.get('result', {})
                pair_key = [k for k in result.keys() if k != 'last']
                if pair_key:
                    ticks = result[pair_key[0]]
                    if ticks:
                        # Most recent tick close price
                        kraken_price = float(ticks[-1][4])
                        with state.lock:
                            state.kraken_eth = kraken_price
                        logger.debug("Kraken ETH updated: %.2f", kraken_price)

        except Exception as e:
            logger.warning("Kraken poll failed: %s", e)

        time.sleep(config.KRAKEN_POLL_MINUTES * 60)


# =============================================================
# DERIBIT IMPLIED VOL + PUT/CALL RATIO POLLER
# =============================================================
def _fetch_deribit_vol():
    """Fetch DVOL and put/call OI ratio from Deribit public API and write to market_context."""
    resp = requests.get(
        config.DERIBIT_DVOL_URL,
        params={"index_name": config.DERIBIT_DVOL_INDEX},
        timeout=15,
    )
    resp.raise_for_status()
    eth_dvol = float(resp.json()["result"]["index_price"])

    resp2 = requests.get(
        config.DERIBIT_OPTIONS_URL,
        params={"currency": "ETH", "kind": "option"},
        timeout=30,
    )
    resp2.raise_for_status()
    instruments = resp2.json().get("result", [])

    call_oi = sum(
        float(i.get("open_interest", 0))
        for i in instruments
        if i.get("instrument_name", "").endswith("-C")
    )
    put_oi = sum(
        float(i.get("open_interest", 0))
        for i in instruments
        if i.get("instrument_name", "").endswith("-P")
    )

    put_call_ratio = (put_oi / call_oi) if call_oi > 0 else None
    put_call_direction = None
    if put_call_ratio is not None:
        put_call_direction = "above_1" if put_call_ratio > 1.0 else "below_1"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00")
    database.upsert_market_context(ts, {
        "eth_dvol":           eth_dvol,
        "put_call_ratio":     put_call_ratio,
        "put_call_direction": put_call_direction,
    })
    logger.info(
        "Deribit: DVOL=%.2f | P/C=%.4f (%s)",
        eth_dvol, put_call_ratio or 0.0, put_call_direction,
    )
    return eth_dvol, put_call_ratio, put_call_direction


def poll_deribit_vol():
    """Background thread: poll Deribit DVOL + put/call ratio every DERIBIT_POLL_MINUTES."""
    while True:
        try:
            _fetch_deribit_vol()
        except Exception as e:
            logger.warning("Deribit vol poll failed: %s", e)
        time.sleep(config.DERIBIT_POLL_MINUTES * 60)


# =============================================================
# ETHERSCAN ETH EXCHANGE NETFLOW POLLER
# =============================================================
def _fetch_etherscan_netflow():
    """
    Query the last 4h of ETH transactions for known exchange addresses.
    Writes net_flow (outflow - inflow) to market_context.
    Negative net_flow = net inflow to exchanges = bearish signal.
    """
    cutoff_ts     = int(time.time()) - 4 * 3600
    total_inflow  = 0.0
    total_outflow = 0.0

    for exchange, address in config.EXCHANGE_ADDRESSES.items():
        try:
            resp = requests.get(
                config.ETHERSCAN_URL,
                params={
                    "chainid":    1,
                    "module":     "account",
                    "action":     "txlist",
                    "address":    address,
                    "startblock": 0,
                    "endblock":   99999999,
                    "sort":       "desc",
                    "apikey":     config.ETHERSCAN_API_KEY,
                },
                timeout=30,
            )
            data = resp.json()

            if data.get("status") != "1":
                logger.warning(
                    "Etherscan %s: status=%s msg=%s",
                    exchange, data.get("status"), data.get("message"),
                )
                time.sleep(0.25)
                continue

            addr_lower = address.lower()
            for tx in data.get("result", []):
                tx_time = int(tx.get("timeStamp", 0))
                if tx_time < cutoff_ts:
                    break  # results are desc; everything after is older
                if tx.get("isError") == "1":
                    continue

                value_eth = int(tx.get("value", "0")) / 1e18

                if tx.get("to", "").lower() == addr_lower:
                    total_inflow += value_eth      # ETH arriving at exchange
                elif tx.get("from", "").lower() == addr_lower:
                    total_outflow += value_eth     # ETH leaving exchange

        except Exception as e:
            logger.warning("Etherscan poll failed for %s: %s", exchange, e)

        time.sleep(0.25)  # stay well within 5 req/s Etherscan rate limit

    net_flow = total_outflow - total_inflow
    if net_flow > 0.001:
        direction = "outflow"
    elif net_flow < -0.001:
        direction = "inflow"
    else:
        direction = "neutral"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00")
    database.upsert_market_context(ts, {
        "eth_netflow_4h":        net_flow,
        "eth_netflow_direction": direction,
    })
    logger.info(
        "Netflow 4h: %.4f ETH (%s) | inflow=%.4f outflow=%.4f",
        net_flow, direction, total_inflow, total_outflow,
    )
    return net_flow, direction


def poll_etherscan_netflow():
    """Background thread: poll Etherscan exchange netflow every NETFLOW_POLL_MINUTES."""
    while True:
        try:
            _fetch_etherscan_netflow()
        except Exception as e:
            logger.warning("Etherscan netflow poll failed: %s", e)
        time.sleep(config.NETFLOW_POLL_MINUTES * 60)


# =============================================================
# FRED MACRO DATA POLLER (DXY + 10Y TREASURY YIELD)
# =============================================================
_FRED_SERIES = {
    "dxy_value": "DTWEXBGS",
    "yield_10y": "DGS10",
}
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def _fetch_fred_macro():
    """Fetch DXY and 10Y yield from FRED and write to market_context."""
    results = {}
    for col, series_id in _FRED_SERIES.items():
        resp = requests.get(
            _FRED_BASE,
            params={
                "series_id":  series_id,
                "api_key":    config.FRED_API_KEY,
                "sort_order": "desc",
                "limit":      10,
                "file_type":  "json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        # FRED uses "." for missing values — take the first valid float
        value = None
        for obs in observations:
            raw = obs.get("value", ".")
            if raw != ".":
                try:
                    value = float(raw)
                    break
                except ValueError:
                    continue
        results[col] = value

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00")
    database.upsert_market_context(ts, results)
    logger.info(
        "FRED macro: DXY=%.4f | 10Y yield=%.4f",
        results.get("dxy_value") or 0.0,
        results.get("yield_10y") or 0.0,
    )
    return results


def poll_fred_macro():
    """Background thread: poll FRED macro data every FRED_POLL_MINUTES."""
    while True:
        try:
            _fetch_fred_macro()
        except Exception as e:
            logger.warning("FRED macro poll failed: %s", e)
        time.sleep(config.FRED_POLL_MINUTES * 60)


# =============================================================
# MAIN
# =============================================================
def main():
    global logger

    # Setup
    setup_logging()
    logger = logging.getLogger(__name__)

    # --test: run both new polls once, print last market_context row, exit
    if '--test' in sys.argv:
        logger.info("Test mode: single poll run, no WebSocket")
        database.initialize_database()

        logger.info("--- Deribit poll ---")
        try:
            _fetch_deribit_vol()
        except Exception as e:
            logger.error("Deribit fetch failed: %s", e)

        logger.info("--- Etherscan netflow poll ---")
        try:
            _fetch_etherscan_netflow()
        except Exception as e:
            logger.error("Etherscan fetch failed: %s", e)

        logger.info("--- FRED macro poll ---")
        try:
            _fetch_fred_macro()
        except Exception as e:
            logger.error("FRED fetch failed: %s", e)

        logger.info("--- Last market_context row ---")
        row = database.get_last_market_context()
        if row:
            for k, v in row.items():
                logger.info("  %-25s %s", k, v)
        else:
            logger.warning("No market_context rows found")
        return

    logger.info("=" * 55)
    logger.info("ETH OBSERVER BOT STARTING")
    logger.info("=" * 55)
    logger.info("ETH Perp: %s", config.ETH_PERP_ID)
    logger.info("Database: %s", config.DB_PATH)
    logger.info("Log:      %s", config.LOG_PATH)

    # Initialize database
    database.initialize_database()

    # Load API credentials
    try:
        api_key_name, api_key_secret = config.load_api_credentials()
        logger.info("API key loaded successfully")
    except Exception as e:
        logger.error("Failed to load API key from %s: %s", config.CDP_KEY_FILE, e)
        sys.exit(1)

    # Start background polling threads
    funding_thread = threading.Thread(
        target=poll_funding_rate, daemon=True, name="FundingPoller"
    )
    funding_thread.start()
    logger.info("Funding rate poller started")

    kraken_thread = threading.Thread(
        target=poll_kraken_price, daemon=True, name="KrakenPoller"
    )
    kraken_thread.start()
    logger.info("Kraken price poller started")

    deribit_thread = threading.Thread(
        target=poll_deribit_vol, daemon=True, name="DeribitPoller"
    )
    deribit_thread.start()
    logger.info("Deribit vol/put-call poller started")

    netflow_thread = threading.Thread(
        target=poll_etherscan_netflow, daemon=True, name="NetflowPoller"
    )
    netflow_thread.start()
    logger.info("Etherscan netflow poller started")

    fred_thread = threading.Thread(
        target=poll_fred_macro, daemon=True, name="FredPoller"
    )
    fred_thread.start()
    logger.info("FRED macro poller started (DXY + 10Y yield)")

    # Give pollers 5 seconds to get first readings before starting WebSocket
    time.sleep(5)

    # Start WebSocket connection
    logger.info("Connecting to Coinbase WebSocket...")

    ws_client = WSClient(
        api_key=api_key_name,
        api_secret=api_key_secret,
        on_message=on_message,
    )

    # Subscribe to ticker channels for all three products
    products = [config.ETH_PERP_ID, config.ETH_SPOT_ID, config.BTC_SPOT_ID]

    try:
        ws_client.open()
        ws_client.subscribe(product_ids=products, channels=["ticker"])
        logger.info("Subscribed to ticker: %s", products)
        logger.info("Observer running. Press Ctrl+C to stop.")

        # Keep alive loop with reconnection logic
        while True:
            time.sleep(30)

            # Log heartbeat every 30 minutes
            now = datetime.now(timezone.utc)
            if now.minute % 30 == 0 and now.second < 30:
                with state.lock:
                    eth = state.eth_perp_mid
                    btc = state.btc_price
                    funding = state.funding_rate
                logger.info(
                    "Heartbeat | ETH=%.2f | BTC=%.2f | Funding=%.8f",
                    eth or 0, btc or 0, funding or 0
                )

    except KeyboardInterrupt:
        logger.info("Shutting down gracefully...")
        ws_client.close()
        logger.info("Observer stopped.")

    except Exception as e:
        logger.error("WebSocket error: %s - will restart in 60s", e)
        time.sleep(60)
        # Restart by re-running main
        main()


if __name__ == "__main__":
    main()
