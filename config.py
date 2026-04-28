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
