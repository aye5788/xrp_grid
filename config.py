import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET")
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET")

# --- Trading Parameters ---
SYMBOL = "XRP-USD"
EXCHANGE = "kraken"   # "coinbase" or "kraken"
GRID_LEVELS_DEFAULT = 10
GRID_LEVEL_VARIANTS = [6, 8, 10, 12, 14, 16]
# Shadow spacing variants — combined with GRID_LEVEL_VARIANTS yields
# 4 × 6 = 24 shadow configurations. Each evaluates both realized
# (rolling_pnl_pct) and expected (closed-form per-round-trip)
# economics so Melchior can compare configurations directly.
SPACING_VARIANTS = [0.010, 0.015, 0.020, 0.025]
GRID_SWITCH_THRESHOLD_PCT = 0.10   # min P&L% margin to trigger a level switch
GRID_SWITCH_MIN_FILLS = 20         # both live and candidate need at least this many fills
GRID_SWITCH_MIN_HOURS = 24         # rolling window for P&L comparison
GRID_SPACING_PCT = 0.025  # Default spacing — matches MAX_GRID_SPACING_PCT
MAX_GRID_SPACING_PCT = 0.025   # Hard ceiling: 2.5% spacing. Beyond this,
                                # WIDEN is skipped and MAINTAIN is applied.
MIN_GRID_SPACING_PCT = 0.003   # Hard floor: 0.3% spacing. Below this,
                                # TIGHTEN is skipped and MAINTAIN is applied.
# GRID_PAUSE: cancel orders and wait, triggered by regime gate in
# magi/orchestrator.check_regime_gate(). Different from HALT: does not trip
# kill switch, re-evaluates each cycle and releases automatically when the
# structural-downtrend conditions (price <8% of EMA200, EMA50<EMA200,
# vol HIGH, vwap_dev<-2%) no longer all hold.
REGIME_GATE_ENABLED = False  # Set True for live trading, False for paper validation
GRID_CENTRE_DEFAULT = None
MAX_INVENTORY_USD = 50.0
TAKER_FEE = 0.0026  # Kraken XRP/USD tier-0 taker: 0.26%
MAKER_FEE = 0.0016  # Kraken XRP/USD tier-0 maker: 0.16%

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
# DEPRECATED: use DAILY_LOSS_LIMIT_PCT instead. Kept for backward reference only.
DAILY_LOSS_LIMIT_USD = 10.0         # Auto-HALT if net daily P&L below -$10

# Daily loss limit as percentage of total universe at start of UTC day.
# 0.15 = trip when total_universe_usd drops more than 15% from midnight UTC value.
# Total universe = xrp_held * current_price + usd_held.
DAILY_LOSS_LIMIT_PCT = 0.15
COINBASE_RATE_LIMIT_BACKOFF = 5     # Seconds to wait after a 429
KILL_SWITCH_FILE = '/root/xrp_grid/HALT'  # If this file exists, system halts
LIVE_CONFIRMATION_FILE = "/root/xrp_grid/CONFIRM_LIVE"
LIVE_CONFIRMATION_TOKEN = "I_UNDERSTAND_THIS_IS_REAL_MONEY\n"
LIVE_CONFIRMATION_ENV_VAR = "MAGI_LIVE_CONFIRM"
LIVE_CONFIRMATION_ENV_VALUE = "YES"

# --- Alerts ---
# Background sweep period for scanning Letta steps for credit/auth/error
# stop_reasons that the live hook in council.py might have missed
# (summarization steps, retries, etc.). Set to 0 to disable.
LETTA_STEPS_SWEEP_INTERVAL_MIN = 30

# --- Cost tracking ---
LLM_MONTHLY_BUDGET_USD = 5.00
DO_DROPLET_MONTHLY_USD = 6.00
DO_API_TOKEN = os.getenv("DO_API_TOKEN", "")
ANTHROPIC_CREDIT_REMAINING = 6.66
OPENAI_CREDIT_REMAINING = 2.88
GOOGLE_CREDIT_REMAINING = 9.44
