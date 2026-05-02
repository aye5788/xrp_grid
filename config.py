# =============================================================
# ETH OBSERVER BOT - CONFIGURATION
# All settings live here. Edit only this file to change behavior.
# =============================================================

import json
import os

# ----- API KEY -----
# Path to your Coinbase CDP JSON key file
CDP_KEY_FILE = os.path.expanduser("~/eth_observer/cdp_key.json")

def load_api_credentials():
    """Load API key name and private key from the JSON file."""
    with open(CDP_KEY_FILE, "r") as f:
        key_data = json.load(f)
    return key_data["name"], key_data["privateKey"]

# ----- PRODUCTS TO WATCH -----
ETH_PERP_ID = "ETP-20DEC30-CDE"   # ETH nano perpetual futures
ETH_SPOT_ID = "ETH-USD"            # ETH spot (for premium proxy)
BTC_SPOT_ID = "BTC-USD"            # BTC (concurrent move context)

# Kraken ETH/USD for premium proxy calculation
KRAKEN_URL  = "https://api.kraken.com/0/public/OHLC"
KRAKEN_PAIR = "ETHUSD"

# ----- DATABASE -----
DB_PATH = os.path.expanduser("~/eth_observer/observer.db")

# ----- LOG FILES -----
# Rotating logs - max 5MB per file, keep 3 files = 15MB max forever
LOG_PATH     = os.path.expanduser("~/eth_observer/observer.log")
LOG_MAX_MB   = 5
LOG_BACKUPS  = 3

# ----- SIGNAL PARAMETERS -----
# Based on research findings - do not optimize these against history.
# They are set based on structural logic only.

# VWAP lookback window in hours
VWAP_LOOKBACK_HOURS = 24

# VWAP deviation threshold to flag a signal event (%)
# From research: meaningful deviation starts around 0.5%
VWAP_DEV_THRESHOLD = 0.5

# BTC concurrent move filter:
# If BTC moved more than this % in the same hour, don't flag signal
# (ETH move is BTC-driven, not ETH-specific)
BTC_MOVE_FILTER = 0.8

# Vol regime threshold:
# Rolling 24h std of hourly returns above this = high vol regime
# From research: 0.83% was the high-vol threshold
VOL_HIGH_THRESHOLD = 0.83

# How often to poll Coinbase REST for funding rate (minutes)
FUNDING_POLL_MINUTES = 15

# How often to poll Kraken for premium proxy (minutes)
KRAKEN_POLL_MINUTES  = 15

# How often to poll Deribit vol / put-call ratio (minutes)
DERIBIT_POLL_MINUTES = 60

# How often to poll Etherscan exchange netflow (minutes)
NETFLOW_POLL_MINUTES = 60

# How often to poll FRED macro data (minutes) — FRED updates once daily
FRED_POLL_MINUTES = 240

# ----- DERIBIT -----
DERIBIT_DVOL_URL      = "https://www.deribit.com/api/v2/public/get_index_price"
DERIBIT_DVOL_INDEX    = "ethdvol_usdc"  # Deribit's name for the ETH implied vol index
DERIBIT_OPTIONS_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"

# ----- ETHERSCAN -----
ETHERSCAN_URL = "https://api.etherscan.io/v2/api"

# Known exchange ETH deposit/hot-wallet addresses (mainnet)
EXCHANGE_ADDRESSES = {
    'binance':  '0x28C6c06298d514Db089934071355E5743bf21d60',
    'coinbase': '0x71660c4005BA85c37ccec55d0C4493E66Fe775d3',
    'kraken':   '0xDA9dfA130Df4dE4673b89022EE50ff26f6EA73Cf',
    'okx':      '0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b',
}


def load_env_vars():
    """Load key=value pairs from ~/eth_observer/.env into a dict."""
    env_path = os.path.expanduser("~/eth_observer/.env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
    return env


_env = load_env_vars()
ETHERSCAN_API_KEY = os.environ.get('ETHERSCAN_API_KEY') or _env.get('ETHERSCAN_API_KEY', '')
FRED_API_KEY      = os.environ.get('FRED_API_KEY')      or _env.get('FRED_API_KEY', '')

# ----- COINGLASS -----
COINGLASS_MCP_URL  = "https://api-mcp.coinglass.com/mcp"
COINGLASS_API_KEY  = os.environ.get('COINGLASS_API_KEY') or _env.get('COINGLASS_API_KEY', '')
COINGLASS_ALL_EXCHANGES = (
    "OKX,Binance,HTX,Bitmex,Bitfinex,Bybit,Deribit,Gate,Kraken,"
    "KuCoin,Bitget,dYdX,CoinEx,BingX,Coinbase,Crypto.com,Hyperliquid,"
    "Bitunix,MEXC"
)

# ----- LIQUIDATION SIGNAL PARAMETERS -----
# How often the liquidation monitor polls CoinGlass (hours)
LIQ_POLL_HOURS = 4

# Historical 90th-percentile short liquidation threshold (from 165-day research dataset)
LIQ_P90_USD = 6_330_461

# OI-weighted funding rate median threshold (from research: 0.00202%)
LIQ_FUNDING_MEDIAN = 0.00202

# Failed squeeze detection thresholds
LIQ_SHORT_DOMINANCE = 2.0   # short_liq_usd must exceed this × long_liq_usd
LIQ_FOLLOWTHROUGH   = 0.5   # price 4h return must be below this % to classify as failed

# ----- LIQUIDATION TRADE PARAMETERS -----
# Calibrated via stop_calibration.py / stop_calibration2.py against 20 MAGI-approved signals.
#
# Finding: immediate entry is NOT viable regardless of stop width — 80-90% of signals
# touch +0.75-1.5% within the first 4h before the intended -2% move materializes.
# The correct approach is a +3h delayed entry with a 36h hold window, which achieves
# +0.18% expectancy and 60% win rate (vs -2.0% with immediate entry).
#
# ARCHITECTURE NOTE: Delayed entry requires execution.py to schedule the fill
# 3 hours after the MAGI decision rather than immediately. Until that is implemented,
# live trades will underperform the backtested expectancy.
LIQ_ENTRY_DELAY_HOURS = 3       # hours to wait after signal before entering
LIQ_STOP_PCT          = 0.0150  # +1.50% above entry (stop for short)
LIQ_TARGET_PCT        = 0.0200  # -2.00% below entry (target for short)
LIQ_MAX_HOLD_HOURS    = 36      # exit at time stop if neither level hit
