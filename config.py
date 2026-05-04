import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET")

# --- Trading Parameters ---
SYMBOL = "XRP-USD"
GRID_LEVELS = 10
GRID_SPACING_PCT = 0.005
GRID_CENTRE_DEFAULT = None
MAX_INVENTORY_USD = 50.0
TAKER_FEE = 0.006
MAKER_FEE = 0.004

# --- MAGI Supervision Schedule ---
MORNING_CYCLE_HOUR = 9
AFTERNOON_CYCLE_HOUR = 14
LEARNING_CYCLE_HOUR = 17

# --- Volatility Regime Thresholds ---
VOL_REGIME_LOW_PCT = 33
VOL_REGIME_HIGH_PCT = 66

# --- Grid Safety ---
MIN_SPREAD_PCT = 0.0015
AUTOCORR_TREND_THRESHOLD = 0.3

# --- Database ---
DB_PATH = "/root/xrp_grid/observer.db"

# --- Dashboard ---
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 5000
DASHBOARD_REFRESH_SECONDS = 30

# --- Guardrails ---
DAILY_LOSS_LIMIT_USD = 10.0         # Auto-HALT if net daily P&L below -$10
COINBASE_RATE_LIMIT_BACKOFF = 5     # Seconds to wait after a 429
KILL_SWITCH_FILE = '/root/xrp_grid/HALT'  # If this file exists, system halts
