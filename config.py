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
# Shadow spacing is no longer a variant dimension. Melchior's analytical
# scorer (magi.spacing_evaluator) picks spacing each cycle from the full
# DEFAULT_VARIANTS grid; the shadow sim is reduced to a level-count
# sanity check that runs at the LIVE grid's current spacing (propagated
# via ShadowSimulator.rebuild/update_centre on every initialise_grid).
# Keeping a single-element list here so the existing fan-out init path
# still produces one variant per level count without restructuring.
SPACING_VARIANTS = [0.025]  # placeholder; overridden on first ShadowSimulator.rebuild with the live grid's spacing
GRID_SWITCH_THRESHOLD_PCT = 0.10   # min P&L% margin to trigger a level switch
GRID_SWITCH_MIN_FILLS = 20         # both live and candidate need at least this many fills
GRID_SWITCH_MIN_HOURS = 24         # rolling window for P&L comparison
# No static GRID_SPACING_PCT default. Spacing always comes from a real
# source: scorer rank-1 (magi.spacing_evaluator) for first boot, then
# Melchior's emitted geometry or the [GEOMETRY_INJECTED_FROM_SCORER]
# fallback per MAGI cycle. If no acceptable variant exists, the
# orchestrator forces GRID_PAUSE — the engine does not fabricate.
MAX_GRID_SPACING_PCT = 0.025   # Hard ceiling: 2.5%. SAFETY CLAMP ONLY —
                                # never used as the actual spacing value.
                                # Scorer variants above this are filtered;
                                # agent geometry above this is clamped down.
MIN_GRID_SPACING_PCT = 0.003   # Hard floor: 0.3%. SAFETY CLAMP ONLY —
                                # never used as the actual spacing value.
                                # Scorer variants below this are filtered;
                                # agent geometry below this is clamped up.
# GRID_PAUSE: cancel orders and wait, triggered by regime gate in
# magi/orchestrator.check_regime_gate(). Different from HALT: does not trip
# kill switch, re-evaluates each cycle and releases automatically when the
# structural-downtrend conditions (price <8% of EMA200, EMA50<EMA200,
# vol HIGH, vwap_dev<-2%) no longer all hold.
REGIME_GATE_ENABLED = False  # Set True for live trading, False for paper validation

# Gate-wake precondition guards (scheduler.py). These govern whether an
# off-schedule wake-class gate trigger (T2 grid breach, T11 vol-regime flip,
# T14 book one-sided) is allowed to wake the LLM council. They do NOT change
# whether the gate DETECTS the condition — gate.py detection is unchanged.
# Both are credit-burn defenses, not trading logic.
#
# WAKE_REQUIRES_ACTIVE_GRID — when True, a wake is suppressed when the most
# recent MAGI cycle ended with a blocking hard-rule override (PAUSE_INVALID,
# REGIME_STANDDOWN, HALT, GRID_DEGENERATE, KILL_SWITCH, etc.) or
# geometry_veto=RISK_BLOCK, or grid_state.halt=1 — i.e. the grid is not in an
# active trading state, so waking the council on ANY trigger produces no
# actionable change and only burns Letta credits. The event is consumed
# without waking. Applies to all wake-class triggers (T2/T11/T14). Set False
# to disable (e.g. during edge-case investigation) without code changes.
WAKE_REQUIRES_ACTIVE_GRID = True
#
# WAKE_DWELL_MINUTES — a wake-class condition must PERSIST this many minutes
# before it is allowed to wake the council. Filters transient breaches/flips
# (a wick outside the band, a one-bar regime flip, a book that refills within
# a couple of minutes) that would otherwise spend a cycle on a condition
# already gone by the time the council runs. Measured on 1m candles (T2) or
# the trigger's live state + event age (T11/T14). 0 disables the dwell. 15min
# is short relative to the 1/hr wake throttle and 4h scheduled cadence, so it
# adds little latency while removing intraperiod noise.
WAKE_DWELL_MINUTES = 15

GRID_CENTRE_DEFAULT = None
MAX_INVENTORY_USD = 50.0
# Per-order size is FIXED at the Kraken XRP minimum. Every grid order
# (buy, sell, and the executed anchor) is exactly this many XRP regardless
# of holdings or level count, and never exceeds it. Operator directive
# 2026-05-24: smallest risk per fill, most round-trips per deployed dollar.
# To deploy more capital, raise the level count (more orders of this size),
# not the order size. At XRP > ~$0.30 this clears Kraken's $0.50 notional
# minimum; below that a 1.65 XRP order would be sub-minimum-notional.
ORDER_SIZE_XRP = 1.65
TAKER_FEE = 0.0040  # Kraken tier-0 spot (≤$10k 30d volume). Verified via live test order 2026-05-23.
MAKER_FEE = 0.0025  # Kraken tier-0 spot (≤$10k 30d volume). Verified via live test order 2026-05-23.
# Fee basis for per-LEVEL grid economics (spacing_evaluator acceptability +
# ranking). Resting limit arms fill as MAKER (0.25%), so a grid round-trip
# costs 2*MAKER_FEE — this is the recurring per-level cost. The scorer MUST
# use this, not TAKER_FEE: charging taker (0.40%) wrongly rejected genuinely
# fee-positive tight grids (0.75% nets +0.25% maker-maker) and pinned the
# tightest selectable spacing at 1.0%, which under-fills in low vol. The
# one-time anchor IS a taker market order, but that's a setup cost amortized
# over the grid's life, not part of recurring per-level economics — so it
# does not belong in the per-level fee-positivity floor. Set 2026-05-24.
GRID_LEVEL_FEE_PER_SIDE = MAKER_FEE

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

# --- Per-agent recall (the "Journal"; database.get_agent_recall) ---
# Deterministic SQLite read injected as per-seat prompt context: each agent
# recalls only its OWN past calls, scored by its OWN per-role metric. These bound
# what is injected — most-recent RECALL_MAX_ITEMS calls within RECALL_LOOKBACK_DAYS
# of the cycle, after the config-version and scored-only filters. Named here (not
# as literals at the call site) so the lever is visible and tunable in one place.
RECALL_MAX_ITEMS = 6
RECALL_LOOKBACK_DAYS = 21

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

# (Letta-era blocks removed 2026-06-09, BU-3: the LETTA_STEPS_SWEEP
# alert sweep and the memory-rotation constants — ROTATION_CADENCE /
# ROTATION_WINDOW_PCT / SELF_MODEL_CHAR_CAP / MAX_NEW_PATTERNS — served
# magi/memory_lifecycle.py, archived with the rest of the Letta layer.
# Per-agent CREDIT_REMAINING / DigitalOcean billing constants went with
# the dashboard Costs panel; spend is traced in Langfuse now.)

# --- Cost tracking ---
# Operator's LLM spend ceiling. Observability is Langfuse's job; this
# constant documents the budget the gate-driven cadence is tuned against.
LLM_MONTHLY_BUDGET_USD = 5.00
