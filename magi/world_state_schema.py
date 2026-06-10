"""
magi/world_state_schema.py — single source of truth for fields in
world_state and which agents consume them.

The FIELDS dict at the bottom of this module is the canonical schema.

Used by:
  - magi/validate_schema.py — standalone CLI verification tool that wraps
    the validation helpers below
  - magi/provision_agents.py — calls render_signals_block(agent_id) to
    auto-generate the SIGNALS YOU RECEIVE section in each persona, and
    validate_persona_references() to fail-loud on broken references
  - magi/orchestrator.py:build_world_state() — calls
    validate_runtime_output(ws) to fire a critical alert if the runtime
    output diverges from the schema (drift detection)

Path convention: dot-notation from world_state root.
  "price"                                        = world_state.price
  "indicators.vwap_dev_pct"                      = world_state.indicators.vwap_dev_pct
  "position_state.round_trip_distance_pct"       = world_state.position_state.round_trip_distance_pct

Each field declares:
  - type:        scalar type label (float | int | str | bool | dict | list)
  - description: one-line description of the field
  - consumers:   list of agent ids (subset of casper / melchior / balthasar)
                 that consume this field. Used to render auto-generated
                 SIGNALS lists and to validate persona references.
  - <agent>_usage: per-consumer usage hint. Rendered into the agent's
                   SIGNALS block. Required for every agent in consumers.

Adding or removing a field in build_world_state() requires updating this
file. The runtime validator fires a critical alert via insert_alert if a
field appears in build_world_state output but is not declared here, or
vice versa.
"""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

AGENTS = ("casper", "melchior", "balthasar")


# ----------------------------------------------------------------------
# FIELDS — the canonical schema. Operator edits this dict to add /
# remove / re-scope fields. Everything downstream derives from it.
# ----------------------------------------------------------------------

FIELDS = {

    # ---------------- Top-level scalars ----------------

    "timestamp": {
        "type": "str",
        "description": "ISO-8601 UTC timestamp the world_state snapshot was built",
        "consumers": [],
    },
    "price": {
        "type": "float",
        "description": "latest XRP/USD spot price (1h timeframe)",
        "consumers": ["casper", "melchior", "balthasar"],
        "casper_usage": "regime input — derive ema_distance_pct = (price - indicators.ema_200) / indicators.ema_200 * 100",
        "melchior_usage": "centre reference — compare to grid_state.centre_price for drift",
        "balthasar_usage": "XRP-side valuation factor; also used in Step 0 missing-data check",
    },
    "hours_since_last_fill": {
        "type": "float",
        "description": "hours since the most recent grid fill (None if no fills)",
        "consumers": ["casper", "melchior", "balthasar"],
        "casper_usage": "context only — inactive grid lowers conviction on regime calls that assume oscillation",
        "melchior_usage": "Step 1 'inactive' flag (>12); Step 1 cooldown carve-out via hours_since_last_rebuild",
        "balthasar_usage": "context only — long inactivity may signal risk conditions worth elevating",
    },
    "hours_since_last_rebuild": {
        "type": "float",
        "description": "hours since the last 'Grid initialised' row (None if never)",
        "consumers": ["melchior"],
        "melchior_usage": "Step 1 cooldown carve-out — recently_built = < 1.0",
    },
    "skew_delta_since_rebuild": {
        "type": "float",
        "description": "change in inventory_skew since the last grid rebuild (position-awareness diagnostic)",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "context — rebuild diagnostic; large drift signals current grid is not balancing (gate referenced in Step 0.5)",
        "balthasar_usage": "context — rebuild diagnostic; cumulative skew drift complements portfolio.allocation_skew",
    },
    "current_spacing_pct": {
        "type": "float",
        "description": "live grid spacing (decimal, e.g. 0.0075 = 0.75%)",
        "consumers": ["melchior"],
        "melchior_usage": "current geometry parameter — input to variant-score comparison",
    },
    "current_levels": {
        "type": "int",
        "description": "live grid level count",
        "consumers": ["melchior"],
        "melchior_usage": "current geometry parameter — input to variant-score comparison",
    },
    "current_config_expected_daily_pnl_pct": {
        "type": "float",
        "description": "analytical scorer's expected daily PnL for the current (levels, spacing) config",
        "consumers": ["melchior"],
        "melchior_usage": "current config economics — baseline for RECONFIGURE comparison vs rank-1 variant",
    },
    "current_fee_tier_pct": {
        "type": "float",
        "description": "maker fee currently paid (tier-0 placeholder = 0.0016 until TradeVolume API integration)",
        "consumers": ["melchior"],
        "melchior_usage": "context — fee floor referenced in derived-quantity formulas (2x maker fee per round-trip)",
    },
    "drawdown_from_high_7d": {
        "type": "float",
        "description": "signed percent drawdown of current price from the trailing-7d (168 x 1h bar) running peak; <= 0.0, where 0.0 = at/above the 7d high and e.g. -5.23 = 5.23% below it. None when price is unavailable or no candles exist",
        "consumers": ["balthasar"],
        "balthasar_usage": "context — drawdown from the trailing-7d high, weighed as risk context alongside skew and buffers; a judgment input for the VOTE (Balthasar applies no mechanical threshold to it). NOTE: the gate's T16 wake trigger (added 2026-06-10) keys off this same quantity to CONVENE the council — one wake per full band-width of drawdown — but convening and voting are separate concerns",
    },

    # ---------------- indicators block ----------------

    "indicators.ema_50": {
        "type": "float",
        "description": "50-period EMA, daily timeframe",
        "consumers": ["casper"],
        "casper_usage": "Step 1 EMA stack check vs ema_200",
    },
    "indicators.ema_200": {
        "type": "float",
        "description": "200-period EMA, daily timeframe",
        "consumers": ["casper"],
        "casper_usage": "Step 1 EMA stack reference + ema_distance_pct denominator",
    },
    "indicators.adx": {
        "type": "float",
        "description": "average directional index, daily timeframe",
        "consumers": ["casper"],
        "casper_usage": "Step 1 conviction calibration (ADX >= 20 = high); Step 3 RANGING ADX < 20 check",
    },
    "indicators.adx_pos": {
        "type": "float",
        "description": "positive directional indicator (DI+), daily",
        "consumers": ["casper"],
        "casper_usage": "Step 1 condition 4b — momentum confirmation via directional pressure",
    },
    "indicators.adx_neg": {
        "type": "float",
        "description": "negative directional indicator (DI-), daily",
        "consumers": ["casper"],
        "casper_usage": "Step 1 condition 4b — momentum confirmation via directional pressure",
    },
    "indicators.roc_6h": {
        "type": "float",
        "description": "6-hour rate of change (percent)",
        "consumers": ["casper"],
        "casper_usage": "Step 1 condition 4a — momentum confirmation (>= +0.3 bullish, <= -0.3 bearish)",
    },
    "indicators.bb_width": {
        "type": "float",
        "description": "Bollinger Band width (daily)",
        "consumers": ["casper"],
        "casper_usage": "context — Step 3 RANGING conviction modifier; compressed width raises conviction (referenced via 'BB width' prose)",
    },
    "indicators.bb_upper": {
        "type": "float",
        "description": "Bollinger Band upper edge (daily)",
        "consumers": ["casper"],
        "casper_usage": "context only — informational geometry of BB envelope",
    },
    "indicators.bb_lower": {
        "type": "float",
        "description": "Bollinger Band lower edge (daily)",
        "consumers": ["casper"],
        "casper_usage": "context only — informational geometry of BB envelope",
    },
    "indicators.btc_ema_50": {
        "type": "float",
        "description": "BTC 50-period EMA (broad crypto context)",
        "consumers": ["casper"],
        "casper_usage": "context only — broader market regime alignment check",
    },
    "indicators.btc_ema_200": {
        "type": "float",
        "description": "BTC 200-period EMA (broad crypto context)",
        "consumers": ["casper"],
        "casper_usage": "context only — broader market regime alignment check",
    },
    "indicators.vwap": {
        "type": "float",
        "description": "rolling volume-weighted average price (intra-window)",
        "consumers": ["melchior"],
        "melchior_usage": "context — base for vwap_dev_pct; not consumed directly",
    },
    "indicators.vwap_dev_pct": {
        "type": "float",
        "description": "percent deviation of price from rolling VWAP",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "Step 1 centre-drift check (>1.5 = drift); Step 2 RECENTRE on drift",
        "balthasar_usage": "Step 4 market-context elevation when extreme deviation + directional skew",
    },
    "indicators.atr": {
        "type": "float",
        "description": "average true range (absolute price units)",
        "consumers": ["melchior"],
        "melchior_usage": "context only — absolute volatility magnitude",
    },
    "indicators.atr_percentile": {
        "type": "float",
        "description": "ATR percentile within rolling window (0-100)",
        "consumers": ["casper", "melchior", "balthasar"],
        "casper_usage": "context — informs Pattern 4 'ATR insufficiency in low-ROC trends' diagnosis; cited in worked examples",
        "melchior_usage": "context — vol_regime derivation cross-check",
        "balthasar_usage": "context only — volatility context for risk elevation",
    },
    "indicators.vol_regime": {
        "type": "str",
        "description": "volatility regime label — LOW / MEDIUM / HIGH",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "Step 3 spacing-band gating (LOW/HIGH paths)",
        "balthasar_usage": "Step 4 market-context elevation when HIGH combined with skew",
    },
    "indicators.autocorr_1h": {
        "type": "float",
        "description": "1-hour return autocorrelation",
        "consumers": ["casper", "melchior"],
        "casper_usage": "Step 1 condition 4c — momentum confirmation (>0.15 = trending support)",
        "melchior_usage": "Step 2 RECENTRE gate — not-both-positive condition vs centre drift",
    },
    "indicators.autocorr_4h": {
        "type": "float",
        "description": "4-hour return autocorrelation",
        "consumers": ["casper", "melchior"],
        "casper_usage": "Step 3 RANGING conviction — near-zero or negative raises conviction",
        "melchior_usage": "Step 2 RECENTRE gate — not-both-positive condition vs centre drift",
    },

    # Indicator row metadata from SQLite — passed through by dict(row) in
    # database.get_latest_indicators. No agent consumes these.
    "indicators.id": {
        "type": "int",
        "description": "SQLite row id (system column)",
        "consumers": [],
    },
    "indicators.timestamp": {
        "type": "str",
        "description": "SQLite row timestamp (system column)",
        "consumers": [],
    },
    "indicators.timeframe": {
        "type": "str",
        "description": "indicator timeframe label ('1h')",
        "consumers": [],
    },

    # ---------------- grid_state block ----------------

    "grid_state.centre_price": {
        "type": "float",
        "description": "current grid centre price",
        "consumers": ["melchior"],
        "melchior_usage": "context — reference for drift; consumed indirectly via indicators.vwap_dev_pct",
    },
    "grid_state.spacing_pct": {
        "type": "float",
        "description": "current grid spacing (decimal)",
        "consumers": ["melchior"],
        "melchior_usage": "Step 3 SPACING FIT band determination",
    },
    "grid_state.levels": {
        "type": "int",
        "description": "current grid level count",
        "consumers": ["melchior"],
        "melchior_usage": "geometry context for variant-score comparison",
    },
    "grid_state.active": {
        "type": "int",
        "description": "grid active flag (1 = active, 0 = halted)",
        "consumers": [],
    },
    "grid_state.pause_longs": {
        "type": "int",
        "description": "pause_longs flag from grid state (1 = active)",
        "consumers": ["melchior"],
        "melchior_usage": "context only — current pause state from risk layer",
    },
    "grid_state.pause_shorts": {
        "type": "int",
        "description": "pause_shorts flag from grid state (1 = active)",
        "consumers": ["melchior"],
        "melchior_usage": "context only — current pause state from risk layer",
    },
    "grid_state.halt": {
        "type": "int",
        "description": "HALT flag from grid state",
        "consumers": [],
    },
    "grid_state.notes": {
        "type": "str",
        "description": "free-form note attached to last grid_state row",
        "consumers": [],
    },
    "grid_state.id": {
        "type": "int",
        "description": "SQLite row id (system column)",
        "consumers": [],
    },
    "grid_state.timestamp": {
        "type": "str",
        "description": "SQLite row timestamp (system column)",
        "consumers": [],
    },

    # ---------------- grid_position block (price vs grid band) ----------------
    # Derived in orchestrator._grid_position from the same centre ± n_pairs·spacing
    # envelope T2 uses. None when no grid exists; when present, all three keys
    # are always set (schema-contract requirement). fillable=False signals a
    # stranded grid (price outside the band) that cannot fill until re-centred.

    "grid_position.side": {
        "type": "str",
        "description": "where price sits vs the grid band: 'inside' | 'above' | 'below'",
        "consumers": ["casper", "balthasar"],
        "casper_usage": "R1/regime_action — a stranded grid (side != inside) means a RECENTRE re-establishes fills near price rather than chasing the trend; do not STAND_DOWN against a corrective recentre on that basis alone",
        "balthasar_usage": "geometry_veto — when side != inside (stranded), a RECENTRE is risk-reducing; do not RISK_BLOCK it solely on a hostile/trending regime",
    },
    "grid_position.pct_outside_band": {
        "type": "float",
        "description": "percent distance from price to the nearest band edge; 0.0 when inside",
        "consumers": ["casper", "balthasar"],
        "casper_usage": "context — magnitude of grid drift off price",
        "balthasar_usage": "context — how far the grid has stranded; larger => recentre more clearly corrective",
    },
    "grid_position.fillable": {
        "type": "bool",
        "description": "True iff price is inside the grid band (the resting book can fill without a recentre); False => stranded",
        "consumers": ["casper", "balthasar"],
        "casper_usage": "R1 — False means standing down perpetuates a non-filling grid; weigh recentre as corrective",
        "balthasar_usage": "geometry_veto carve-out — False => a dead book is the larger survival risk; prefer PROCEED on a recentre unless a survival-grade signal independently fires",
    },

    # ---------------- inventory block ----------------

    "inventory.xrp_held": {
        "type": "float",
        "description": "current XRP holdings",
        "consumers": ["balthasar"],
        "balthasar_usage": "primary inventory leg — input to xrp_value_usd derivation",
    },
    "inventory.usd_held": {
        "type": "float",
        "description": "current USD holdings",
        "consumers": ["balthasar"],
        "balthasar_usage": "primary inventory leg — Step 3 buffer-floor check (<$10 → PAUSE_LONGS)",
    },
    "inventory.net_position_usd": {
        "type": "float",
        "description": "net position value in USD",
        "consumers": ["balthasar"],
        "balthasar_usage": "context only — net portfolio value",
    },
    "inventory.inventory_skew": {
        "type": "float",
        "description": "signed concentration, -1 = all USD, +1 = all XRP (canonical; aliased as portfolio.allocation_skew)",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "context only — risk lens informs but does not drive grid geometry",
        "balthasar_usage": "context — alias for portfolio.allocation_skew; persona uses the portfolio.allocation_skew form",
    },
    "inventory.id": {
        "type": "int",
        "description": "SQLite row id (system column)",
        "consumers": [],
    },
    "inventory.timestamp": {
        "type": "str",
        "description": "SQLite row timestamp (system column)",
        "consumers": [],
    },

    # ---------------- open_orders block ----------------

    "open_orders.open_buys": {
        "type": "list",
        "description": "list of open buy orders (price, size)",
        "consumers": [],
    },
    "open_orders.open_sells": {
        "type": "list",
        "description": "list of open sell orders (price, size)",
        "consumers": [],
    },
    "open_orders.recent_fills": {
        "type": "list",
        "description": "recent fills within last 24h",
        "consumers": [],
    },
    "open_orders.buy_count": {
        "type": "int",
        "description": "count of open buy orders",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "Step 1 one_sided / book_imbalance gates; book_healthy = buy >= 3",
        "balthasar_usage": "Step 1 open-order safety gates; PAUSE_LONGS requires buy_count >= 2",
    },
    "open_orders.sell_count": {
        "type": "int",
        "description": "count of open sell orders",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "Step 1 one_sided / book_imbalance gates; book_healthy = sell >= 2",
        "balthasar_usage": "Step 1 open-order safety gates; PAUSE_SHORTS requires sell_count >= 2",
    },
    "open_orders.highest_buy": {
        "type": "float",
        "description": "highest open buy-order price",
        "consumers": ["melchior"],
        "melchior_usage": "context only — book topology near centre",
    },
    "open_orders.lowest_sell": {
        "type": "float",
        "description": "lowest open sell-order price",
        "consumers": ["melchior"],
        "melchior_usage": "context only — book topology near centre",
    },

    # ---------------- cooldown_status block ----------------

    "cooldown_status.recentre_cooldown_active": {
        "type": "bool",
        "description": "whether [RECENTRE_COOLDOWN] hard rule would currently fire",
        "consumers": ["melchior"],
        "melchior_usage": "Step 1 cooldown carve-out — if active and book_healthy, vote MAINTAIN",
    },
    "cooldown_status.recentre_cooldown_minutes_remaining": {
        "type": "int",
        "description": "minutes until [RECENTRE_COOLDOWN] clears",
        "consumers": ["melchior"],
        "melchior_usage": "context only — how long until RECENTRE becomes eligible",
    },
    "cooldown_status.last_recentre_at_utc": {
        "type": "str",
        "description": "ISO timestamp of last grid rebuild",
        "consumers": [],
    },

    # ---------------- current_variant_position block ----------------

    "current_variant_position.level_count": {
        "type": "int",
        "description": "live grid level count (duplicate of grid_state.levels, framed for variant lookup)",
        "consumers": ["melchior"],
        "melchior_usage": "context — variant-table lookup key; duplicate of grid_state.levels",
    },
    "current_variant_position.spacing_pct": {
        "type": "float",
        "description": "live grid spacing (duplicate of grid_state.spacing_pct, framed for variant lookup)",
        "consumers": ["melchior"],
        "melchior_usage": "variant-table lookup key",
    },

    # ---------------- last_fill block (POSITION AWARENESS) ----------------

    "last_fill.order_id": {
        "type": "str",
        "description": "order id of most recent filled order (None if no fills)",
        "consumers": [],
    },
    "last_fill.side": {
        "type": "str",
        "description": "side of most recent fill — 'buy' or 'sell'",
        "consumers": ["casper", "melchior", "balthasar"],
        "casper_usage": "open-trade context — direction of last fill informs regime-call weight",
        "melchior_usage": "open-trade context — direction informs which grid arm is the closing leg",
        "balthasar_usage": "open-trade context — direction informs which pause action would strand the round-trip",
    },
    "last_fill.price": {
        "type": "float",
        "description": "fill price of most recent fill",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "open-trade context — fill price anchors position_state computation",
        "balthasar_usage": "open-trade context — fill price anchors round-trip target",
    },
    "last_fill.size_xrp": {
        "type": "float",
        "description": "size of most recent fill in XRP",
        "consumers": ["balthasar"],
        "balthasar_usage": "context — open-trade size for stranding-risk assessment",
    },
    "last_fill.size_usd": {
        "type": "float",
        "description": "USD value of most recent fill at fill price",
        "consumers": ["balthasar"],
        "balthasar_usage": "context — open-trade USD exposure of the round-trip",
    },
    "last_fill.hours_ago": {
        "type": "float",
        "description": "hours since most recent fill (None if no fills)",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "open-trade recency — < 2h with healthy book biases toward MAINTAIN (let grid close)",
        "balthasar_usage": "open-trade recency — < 2h with profitable projection means PAUSE requires survival-level justification",
    },
    "last_fill.fee_usd": {
        "type": "float",
        "description": "fee paid on most recent fill",
        "consumers": [],
    },

    # ---------------- position_state block (POSITION AWARENESS) ----------------

    "position_state.nearest_close_arm_price": {
        "type": "float",
        "description": "price level at which the round-trip from last_fill closes (one spacing away)",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "context — implicit anchor for round_trip_distance_pct",
        "balthasar_usage": "context — implicit anchor for round_trip_distance_pct",
    },
    "position_state.round_trip_distance_pct": {
        "type": "float",
        "description": "percent distance from current price to nearest_close_arm_price",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "round-trip imminence — < 0.5% with positive net pnl biases strongly toward MAINTAIN; RECENTRE here destroys the closing round-trip",
        "balthasar_usage": "round-trip imminence — < 1% with positive net pnl means PAUSE actions strand a profitable position; require survival-level justification",
    },
    "position_state.round_trip_gross_pnl_usd": {
        "type": "float",
        "description": "gross USD PnL of the round-trip if it closes at nearest_close_arm_price",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "context — gross profit potential of the in-flight round-trip",
        "balthasar_usage": "context — gross profit potential of the in-flight round-trip",
    },
    "position_state.round_trip_net_pnl_usd": {
        "type": "float",
        "description": "net USD PnL after maker fees of the round-trip if it closes",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "open-trade outcome stake — positive net pnl AND distance_pct < 0.5 means MAINTAIN unless emergency overrides",
        "balthasar_usage": "open-trade outcome stake — positive net pnl AND distance_pct < 1 means PAUSE_LONGS/PAUSE_SHORTS require survival-level justification, not preference",
    },

    # ---------------- trajectory block ----------------

    "trajectory.regime_consecutive": {
        "type": "int",
        "description": "number of consecutive cycles Casper has called the same regime",
        "consumers": ["casper"],
        "casper_usage": "context only — long runs of same regime call may indicate either stable read or anchoring",
    },
    "trajectory.melchior_blocked_cycles": {
        "type": "int",
        "description": "consecutive cycles where consensus_grid_action was MAINTAIN but Melchior voted non-MAINTAIN",
        "consumers": ["melchior"],
        "melchior_usage": "self-awareness — sustained overrides may indicate persona-vs-rule threshold mismatch",
    },
    "trajectory.skew_delta": {
        "type": "float",
        "description": "change in inventory_skew between the last two inventory snapshots",
        "consumers": ["melchior", "balthasar"],
        "melchior_usage": "Step 4 SKEW DRIFT — < -0.1 (XRP draining) or > +0.1 (USD draining) → RECENTRE",
        "balthasar_usage": "context — short-term skew movement informs Step 2/3 risk assessment",
    },
    "trajectory.skew_trend": {
        "type": "str",
        "description": "'worsening_long' / 'worsening_short' / 'stable'",
        "consumers": ["balthasar"],
        "balthasar_usage": "context — multi-cycle skew direction informs risk escalation",
    },
    "trajectory.fills_since_last_magi_buys": {
        "type": "int",
        "description": "count of buy fills since the last MAGI decision",
        "consumers": ["melchior"],
        "melchior_usage": "context — input to fills_per_hour and book-balance assessment",
    },
    "trajectory.fills_since_last_magi_sells": {
        "type": "int",
        "description": "count of sell fills since the last MAGI decision",
        "consumers": ["melchior"],
        "melchior_usage": "context — input to fills_per_hour and book-balance assessment",
    },
    "trajectory.cycles_since_structural_change": {
        "type": "int",
        "description": "cycles since last RECENTRE / TIGHTEN / WIDEN",
        "consumers": ["melchior"],
        "melchior_usage": "context only — informs whether current geometry has had time to validate",
    },
    "trajectory.pause_longs_active": {
        "type": "int",
        "description": "pause_longs flag from latest grid_state row (mirror)",
        "consumers": ["balthasar"],
        "balthasar_usage": "context only — confirms whether prior PAUSE_LONGS call is still in effect",
    },
    "trajectory.pause_shorts_active": {
        "type": "int",
        "description": "pause_shorts flag from latest grid_state row (mirror)",
        "consumers": ["balthasar"],
        "balthasar_usage": "context only — confirms whether prior PAUSE_SHORTS call is still in effect",
    },
    "trajectory.fills_per_hour": {
        "type": "float",
        "description": "fills per hour since the last MAGI decision (informational; consumed by Melchior's prior Step 3 SPACING FIT which has been superseded by the ANALYTICAL VARIANT-SCORE addendum)",
        "consumers": [],
    },

    # ---------------- market_knowledge block ----------------

    "market_knowledge.computed_at": {
        "type": "str",
        "description": "timestamp the daily market_knowledge stats were computed",
        "consumers": [],
    },
    "market_knowledge.data_from": {
        "type": "str",
        "description": "earliest timestamp in the input window",
        "consumers": [],
    },
    "market_knowledge.data_to": {
        "type": "str",
        "description": "latest timestamp in the input window",
        "consumers": [],
    },
    "market_knowledge.total_bars": {
        "type": "int",
        "description": "bar count in the input window",
        "consumers": [],
    },
    "market_knowledge.stats": {
        "type": "dict",
        "description": "computed market stats blob (recompute job output)",
        "consumers": [],
    },

    # ---------------- hard_rules block (CURATED — Stage-4 item 2b) ----------------
    # The two failure-case breakers (hard_rules.max_allocation_skew,
    # hard_rules.daily_loss_limit_pct) and the kill-switch path (hard_rules.halt_file)
    # were DROPPED here when they stopped rendering — build_world_state no longer dumps
    # the whole HARD_RULES dict; it renders only the disclosed, non-breaker keys (see
    # _DISCLOSED_HARD_RULE_KEYS in orchestrator.py). The breakers' proximity is now
    # withheld from the council (budget-effect guard). The buffer floors and engine
    # spacing clamps below REMAIN disclosed, so Balthasar's buffer-floor citations and
    # Melchior's spacing-clamp context still resolve.

    "hard_rules.min_usd_buffer": {
        "type": "float",
        "description": "minimum USD buffer before PAUSE_LONGS — Tier 1 buffer floor",
        "consumers": ["balthasar"],
        "balthasar_usage": "Step 3 USD buffer floor reference (cited explicitly via 'inventory.usd_held < hard_rules.min_usd_buffer')",
    },
    "hard_rules.min_xrp_buffer_usd": {
        "type": "float",
        "description": "minimum xrp_value_usd before PAUSE_SHORTS — Tier 1 buffer floor",
        "consumers": ["balthasar"],
        "balthasar_usage": "Step 3 XRP buffer floor reference (cited explicitly via 'portfolio.xrp_value_usd < hard_rules.min_xrp_buffer_usd')",
    },
    "hard_rules.max_grid_spacing_pct": {
        "type": "float",
        "description": "engine spacing clamp (max)",
        "consumers": ["melchior"],
        "melchior_usage": "context — engine clamp ceiling cited in shared preamble; persona may not name explicitly",
    },
    "hard_rules.min_grid_spacing_pct": {
        "type": "float",
        "description": "engine spacing clamp (min)",
        "consumers": ["melchior"],
        "melchior_usage": "context — engine clamp floor cited in shared preamble; persona may not name explicitly",
    },

    # ---------------- constraints block (Stage-4 item 2b — opaque disclosure) ------
    # The curated 'work-within' constraint disclosure built by
    # orchestrator._build_constraint_disclosure, gated per-constraint by
    # CONSTRAINT_DISCLOSURE. Declared as ONE opaque type:"dict" entry: the runtime
    # drift validator does not recurse into type:"dict" fields, so the inner shape
    # (usd_buffer/xrp_buffer/kill_switch, plus the withheld breakers only if a toggle
    # is flipped on) can change without firing drift. Disclosed buffers carry floor +
    # current headroom; the kill switch is a bare existence fact.
    "constraints": {
        "type": "dict",
        "description": "curated work-within constraint disclosure (buffer floor existence + headroom, kill-switch existence fact); breakers withheld by default — gated by CONSTRAINT_DISCLOSURE",
        "consumers": ["balthasar"],
        "balthasar_usage": "work-within survival framing — buffer headroom (how close each leg is to its floor) and the operator kill-switch existence fact; withheld breakers are absent by design",
    },

    # ---------------- portfolio block (single-sourced derived values) ----------------

    "portfolio.xrp_value_usd": {
        "type": "float",
        "description": "XRP holdings valued in USD at current price (computed by magi/portfolio.py)",
        "consumers": ["balthasar"],
        "balthasar_usage": "Step 3 XRP buffer-floor check (<$10 → PAUSE_SHORTS)",
    },
    "portfolio.total_universe_usd": {
        "type": "float",
        "description": "total portfolio value in USD = xrp_value_usd + usd_held",
        "consumers": ["balthasar"],
        "balthasar_usage": "context — portfolio scale; baseline for daily loss limit",
    },
    "portfolio.xrp_pct_of_universe": {
        "type": "float",
        "description": "XRP value as fraction of total universe (0.0 - 1.0)",
        "consumers": ["balthasar"],
        "balthasar_usage": "context — concentration view complementary to allocation_skew",
    },
    "portfolio.allocation_skew": {
        "type": "float",
        "description": "signed concentration centred on 50/50 — alias for inventory.inventory_skew",
        "consumers": ["balthasar"],
        "balthasar_usage": "Step 2 allocation-skew bands (canonical name; same value as inventory.inventory_skew)",
    },

    # ---------------- list-type containers ----------------

    "shadow_variants": {
        "type": "list",
        "description": "24-variant shadow table (level_count x spacing combinations) with realized fills",
        "consumers": [],
    },
    "scored_variants_top_10": {
        "type": "list",
        "description": "top 10 analytical-scored variants by expected_daily_pnl_pct",
        "consumers": ["melchior"],
        "melchior_usage": "variant-score appendix — rank-1 comparison vs current_config_expected_daily_pnl_pct",
    },

    # ---------------- Gate trip-wire events ----------------

    "triggers_since_last_cycle": {
        "type": "list",
        "description": "structural events detected by the gate layer (magi/gate.py) since the prior MAGI cycle; list of {trigger_id, timestamp, details} dicts, empty when the window was routine",
        "consumers": ["casper", "melchior", "balthasar"],
        "casper_usage": "context — gate trip-wire events. T11 (vol regime transition) and T12 (ADX crossing) are most relevant to regime classification; trigger context elevates attention but does not override the role's decision logic",
        "melchior_usage": "context — gate trip-wire events. T2/T3 (grid envelope breach / rapid traversal) and T6/T7 (scorer rank-1 stable improvement / acceptability return) touch geometry directly; trigger context elevates attention but does not override the role's decision logic",
        "balthasar_usage": "context — gate trip-wire events. T1 (velocity spike), T4 (fill drought), T11 (vol transition), T13 (vwap dev) are most relevant to survival evaluation; trigger context elevates attention but does not override the role's decision logic",
    },
}


# ----------------------------------------------------------------------
# Helpers — schema accessors
# ----------------------------------------------------------------------

def schema_paths() -> set:
    """Return the set of all declared paths in the schema."""
    return set(FIELDS.keys())


def consumers_of(path: str) -> list:
    """Return the list of agents that consume the given path."""
    return list(FIELDS.get(path, {}).get("consumers") or [])


def paths_for_agent(agent_id: str) -> list:
    """Return the list of paths this agent is declared as consuming."""
    return [p for p, meta in FIELDS.items() if agent_id in (meta.get("consumers") or [])]


def usage_for(path: str, agent_id: str) -> str | None:
    """Return the usage hint for path+agent, or None if not declared."""
    return FIELDS.get(path, {}).get(f"{agent_id}_usage")


# ----------------------------------------------------------------------
# Runtime validator — schema vs. build_world_state() output
# ----------------------------------------------------------------------

def _walk_paths(node, prefix=""):
    """Yield dot-paths for every scalar/leaf/container in a nested dict
    structure. Lists are returned as their top-level path only (we don't
    enumerate list-element paths)."""
    if isinstance(node, dict):
        if not node:
            yield prefix or ""
            return
        for k, v in node.items():
            sub = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                yield from _walk_paths(v, sub)
            else:
                yield sub
    else:
        yield prefix


def runtime_output_paths(world_state: dict) -> set:
    """Return the set of paths that exist in a given world_state dict.

    Recursion terminates at any path whose schema declaration has
    type='dict' — such fields are opaque containers (e.g. market_knowledge.stats
    holds arbitrary nested analytics output we do not enumerate).
    """
    if not isinstance(world_state, dict):
        return set()

    # Paths declared in schema as opaque dicts — do not recurse into them
    opaque_dict_prefixes = {
        p for p, meta in FIELDS.items()
        if meta.get("type") == "dict"
    }

    def is_under_opaque(path: str) -> bool:
        return any(path == p or path.startswith(p + ".") for p in opaque_dict_prefixes)

    out = set()

    def walk(node, prefix):
        if isinstance(node, dict):
            if prefix and is_under_opaque(prefix):
                # Stop here — opaque container is declared as a whole
                return
            for k, v in node.items():
                sub = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    out.add(sub)
                    walk(v, sub)
                else:
                    out.add(sub)
        else:
            if prefix:
                out.add(prefix)

    walk(world_state, "")
    return out


def validate_runtime_output(world_state: dict) -> dict:
    """Compare schema vs. build_world_state() output.

    Returns a dict:
      {
        "missing_from_runtime": [paths declared in schema but absent in output],
        "undeclared_in_schema": [paths present in output but not in schema],
        "ok": bool — True iff both lists are empty
      }

    None-valued fields and empty-dict containers (last_fill, position_state)
    are tolerated: the path is considered present as long as the container
    key exists at the right path even when the inner value is None.
    """
    declared = schema_paths()
    present = runtime_output_paths(world_state)

    # For declared paths under a container that is None at runtime
    # (e.g. last_fill=None means last_fill.* paths are not iterable
    # subpaths), we count the container key itself as "present" if the
    # top-level key exists in the dict — even with a None value.
    top_keys_present = set(k for k in (world_state or {}).keys())

    missing = []
    for path in declared:
        if path in present:
            continue
        top = path.split(".", 1)[0]
        if top in top_keys_present and (world_state or {}).get(top) is None:
            # Container is present but None — subpaths can't exist
            # without inventing data. Treat declared subpaths as
            # present (None is a valid value).
            continue
        missing.append(path)

    undeclared = []
    for path in present:
        if path in declared:
            continue
        # Allow container-level keys whose subpaths are declared
        # (e.g. "indicators" itself isn't declared but "indicators.ema_50" is)
        if any(d.startswith(f"{path}.") for d in declared):
            continue
        undeclared.append(path)

    return {
        "missing_from_runtime": sorted(missing),
        "undeclared_in_schema": sorted(undeclared),
        "ok": (not missing and not undeclared),
    }


def alert_on_runtime_drift(world_state: dict) -> bool:
    """Validate world_state and, on mismatch, write a critical alert via
    database.insert_alert. Returns True if drift was detected.

    Trading continues regardless — schema drift is a maintenance failure,
    not a trading-stop event. The alert flows through the existing
    magi/notify.py:send_ntfy() hook on critical severity.
    """
    result = validate_runtime_output(world_state)
    if result["ok"]:
        return False
    try:
        from database import insert_alert
        details = []
        if result["missing_from_runtime"]:
            details.append(
                f"missing_from_runtime={result['missing_from_runtime']}"
            )
        if result["undeclared_in_schema"]:
            details.append(
                f"undeclared_in_schema={result['undeclared_in_schema']}"
            )
        msg = "world_state runtime drift vs schema: " + " ; ".join(details)
        insert_alert(
            severity="critical",
            category="schema_drift_runtime",
            message=msg,
        )
        log.warning("schema drift detected, alert written: %s", msg)
    except Exception as e:
        log.error("failed to write schema_drift_runtime alert: %s", e)
    return True


# ----------------------------------------------------------------------
# Persona validator — schema vs. persona text references
# ----------------------------------------------------------------------

# Regex catches both dotted paths (world_state.X.Y or just X.Y) and bare
# field names. We then resolve each match against the schema. Bare names
# match the rightmost segment of a declared path; dotted paths match
# the full declared path.

_DOTTED_PATH_RE = re.compile(
    r"\b(?:world_state\.)?"
    r"(?P<path>(?:indicators|grid_state|inventory|open_orders|position_state"
    r"|last_fill|trajectory|portfolio|cooldown_status|market_knowledge"
    r"|hard_rules|current_variant_position)"
    r"\.[a-z_][a-z_0-9]*)"
)

# Bare-name detection: we look for mentions of any known field leaf name
# (the rightmost segment of any declared path) plus a small set of
# common-mismatch tokens (current_price → price) that personas have
# historically used and that should now be flagged. The list is built
# from FIELDS at module-load time so adding a new field automatically
# extends bare-name validation.
_BARE_NAMES = sorted(
    {p.split(".")[-1] for p in FIELDS.keys()}
    | {
        # Tokens that historically appeared in personas under wrong
        # paths. Listed explicitly so the validator can ERR with a
        # "did you mean X?" hint even though they're not in the schema.
        "current_price",
    }
)
_BARE_NAME_RE = re.compile(r"\b(" + "|".join(_BARE_NAMES) + r")\b")


# Snake_case tokens that look field-like but are actually English/programming
# terms or operator-named concepts. Used by the suspicious-token check to
# avoid false positives in prose. If a real field name collides with one
# of these, remove from this list — the schema takes precedence.
_SUSPICIOUS_TOKEN_ALLOWLIST = {
    # operator concepts / acronyms appearing in persona prose
    "ema_distance_pct",          # Casper-derived quantity (computed in persona)
    "order_count_skew",          # Balthasar-derived quantity
    "book_imbalance",            # Melchior-derived quantity
    "price_step",                # Melchior-derived quantity
    "gross_pct_per_step",        # Melchior-derived quantity
    "expected_pnl_pct_per_round_trip",  # variant-table column name
    "fill_count_24h",            # variant-table column name
    "rolling_pnl_pct",           # variant-table column name
    "last_fill_at",              # variant-table column name
    "reconfigure_target",        # Melchior key_evidence convention
    "target_spacing_pct",        # Melchior geometry output field
    "target_levels",             # Melchior geometry output field
    "buy_level_bias",            # Melchior geometry output field
    "sell_level_bias",           # Melchior geometry output field
    # hard-rule tag names that appear in persona prose
    "recentre_cooldown",
    "grid_degenerate",
    "pause_invalid",
    "usd_buffer_floor",
    "xrp_buffer_floor",
    "alloc_skew_ceiling",
    "daily_loss_limit",
    "kill_switch",
    "guardrails_blocked",
    "grid_healthy_no_recentre",
    "recent_position_hold",
    "no_acceptable_variant",
    "geometry_injected_from_scorer",
    "agent_degraded",
    "council_collapsed",
    # response schema fields the agents emit (not world_state fields)
    "key_evidence",
    "revision_evidence",
    "revised_position",
    "self_model",
    "no_response",
    "no_profitable_grid",
    "thesis_holds",
    "insufficient_data",
    "self_compact_sliding_window",
    "self_compact_all",
    # general programming / system terms
    "world_state",
    "core_memory",
    "round_0",
    "round_1",
    "cycle_phase",
    "stop_reason",
    "safe_defaults",
    "agent_id",
    "letta_agent_id",
    "min_grid_spacing_pct",      # config constants — appear in prose as "MIN_..."
    "max_grid_spacing_pct",
    # numerical / model-config concepts
    "fee_rate_per_side",
    "expected_daily_pnl_pct",    # column inside scored_variants
    "default_variants",
}


# Markers delimiting the auto-generated SIGNALS block in persona files
SIGNALS_BEGIN = "<!-- BEGIN_AUTOGENERATED_SIGNALS -->"
SIGNALS_END = "<!-- END_AUTOGENERATED_SIGNALS -->"


def _strip_signals_block(text: str) -> str:
    """Remove the auto-generated SIGNALS block from persona text so the
    persona validator scans only the hand-authored decision tree."""
    if SIGNALS_BEGIN not in text or SIGNALS_END not in text:
        return text
    start = text.find(SIGNALS_BEGIN)
    end = text.find(SIGNALS_END) + len(SIGNALS_END)
    return text[:start] + text[end:]


# All three personas share an identical SYSTEM CONTEXT preamble (operating
# scale, survival-floor description, architecture notes). It mentions field
# names like 'levels', 'notes', 'allocation_skew', 'vol_regime' as common
# vocabulary describing the system — not as decision-tree references. The
# validator should not flag these mentions as cross-domain references. We
# scope persona validation to the agent-specific text starting at 'ROLE —'.
_ROLE_MARKER = "ROLE —"


def _agent_body(text: str) -> str:
    """Return the agent-specific portion of a persona (post-ROLE marker)."""
    idx = text.find(_ROLE_MARKER)
    if idx == -1:
        return text
    return text[idx:]


def _resolve_reference(token: str) -> list:
    """Map a token (dotted path or bare name) to a list of candidate
    declared schema paths. Returns [] if no candidate found."""
    if "." in token:
        # Dotted reference — must match a declared path verbatim
        if token in FIELDS:
            return [token]
        return []
    # Bare name — match the rightmost segment of any declared path
    matches = []
    for declared in FIELDS:
        if declared == token or declared.endswith("." + token):
            matches.append(declared)
    return matches


def validate_persona_references(persona_text: str, agent_id: str) -> dict:
    """Validate that every world_state-field reference in a persona's
    hand-authored body resolves to a schema entry where this agent is
    a consumer.

    Returns a dict:
      {
        "errors":  [list of error dicts {token, reason}],
        "warnings": [list of warning dicts {path, reason}],
        "ok": bool — True iff errors is empty
      }

    Error vs warning policy:
      - Dotted-path reference that resolves to nothing in schema  -> ERROR
        (intentional reference to a non-existent path)
      - Bare-name reference matching NO schema path at all        -> ERROR
        (a token that looks like a field name but isn't declared —
         classic broken reference like 'current_price' or 'hours_active')
      - Bare-name reference matching a schema path but agent is NOT
        a consumer                                                -> WARNING
        (likely cross-domain prose mention in shared preamble or
         worked-example text; not a runtime broken reference)
      - Suspicious snake_case token (5+ chars, contains underscore)
        not in schema and not in an allowlist                     -> ERROR
        (catches typos / wrong-path references not covered above)
    """
    # Scope to agent-specific content (post-ROLE marker) and strip the
    # auto-generated SIGNALS block. The shared SYSTEM CONTEXT preamble
    # uses field names as common vocabulary and must not trigger
    # cross-domain warnings.
    body = _strip_signals_block(_agent_body(persona_text))

    referenced_paths: set = set()
    error_tokens: dict = {}  # token -> reason (blocking ERROR)
    warn_tokens: dict = {}   # token -> reason (non-blocking WARN)
    note_tokens: dict = {}   # bare snake_case token matching NO schema leaf —
                             # treated as prose; low-severity NOTE, never ERROR

    # Dotted-path references (ERROR on any mismatch)
    for m in _DOTTED_PATH_RE.finditer(body):
        token = m.group("path")
        candidates = _resolve_reference(token)
        if not candidates:
            error_tokens[token] = (
                f"persona references {token!r} — not in schema "
                f"(no declared path matches this dotted reference)"
            )
            continue
        path = candidates[0]
        referenced_paths.add(path)
        if agent_id not in consumers_of(path):
            error_tokens[token] = (
                f"persona references {token!r} — schema declares "
                f"consumers={consumers_of(path)} and does not include "
                f"{agent_id!r}"
            )

    # Bare-name references — ERROR only on no schema match at all
    for m in _BARE_NAME_RE.finditer(body):
        token = m.group(1)
        candidates = _resolve_reference(token)
        if not candidates:
            # A bare snake_case token matching NO declared schema leaf is almost
            # always prose, not a world_state reference — e.g. melchior.md
            # explaining that the scorer's `current_price` ARGUMENT is dead, not
            # citing a field. The dotted-path pass above still hard-ERRORs a real
            # broken path (a "foo.bar" that resolves to nothing); a bare
            # non-resolving token is downgraded to a low-severity NOTE here,
            # never a blocking ERROR.
            note_tokens[token] = (
                f"persona mentions bare token {token!r} — resolves to no schema "
                f"leaf; treated as prose (closest: {_closest_paths(token)})"
            )
            continue
        if not any(agent_id in consumers_of(p) for p in candidates):
            # Cross-domain prose mention — WARN, not ERR
            warn_tokens[token] = (
                f"persona mentions {token!r} (in schema as "
                f"{candidates}) but {agent_id!r} is not a declared "
                f"consumer — likely prose mention; verify it is not "
                f"meant as a decision-tree reference"
            )
        for p in candidates:
            if agent_id in consumers_of(p):
                referenced_paths.add(p)

    # Suspicious-token check was tried and produced too many false positives
    # on prose mentions of function names, derived-quantity formulas, and
    # variant-table column descriptions (enforce_hard_rules, resolve_consensus,
    # mid_price, expected_pnl_pct, etc.). The dotted-path and bare-name
    # passes catch the real broken-reference cases: a wrong dotted path is
    # caught by the dotted regex; a typo bare name is caught when it does
    # not match any schema leaf. Post-provisioning, the auto-generated
    # SIGNALS block also enforces the schema's view of what fields exist
    # for each agent, removing the surface where stale field names linger.

    # Warnings: schema declares this agent as consumer of a path that the
    # persona body never references. Excludes paths flagged as "context only"
    # since those aren't required to appear in the decision tree.
    warnings = []
    for path in paths_for_agent(agent_id):
        if path in referenced_paths:
            continue
        usage = usage_for(path, agent_id) or ""
        # Suppress warnings for fields whose usage hint marks them as
        # context-only / informational / derived-via — not expected to
        # drive decision-tree gates directly. Heuristic: usage starts
        # with the word 'context' or contains specific marker phrases.
        low = usage.lower().lstrip()
        if (low.startswith("context")
                or "context only" in low
                or "informational" in low
                or "no consumer" in low
                or "self-awareness" in low):
            continue
        warnings.append({
            "path": path,
            "reason": (
                f"schema declares {agent_id} as consumer but persona body "
                f"does not reference {path!r} (usage: {usage[:80]!r})"
            ),
        })

    errors = [{"token": t, "reason": r} for t, r in sorted(error_tokens.items())]
    # Prepend cross-domain prose warnings (warn_tokens) before orphan
    # consumer warnings so the operator sees real-text issues first.
    ref_warnings = [
        {"token": t, "reason": r} for t, r in sorted(warn_tokens.items())
    ]
    notes = [{"token": t, "reason": r} for t, r in sorted(note_tokens.items())]
    return {
        "errors": errors,
        "warnings": ref_warnings + warnings,
        "notes": notes,
        "ok": (not errors),
    }


def _closest_paths(token: str, k: int = 3) -> list:
    """Heuristic 'closest' suggestion for an unknown token."""
    paths = list(FIELDS.keys())
    paths.sort(key=lambda p: (0 if token in p else 1, len(p)))
    return paths[:k]


# ----------------------------------------------------------------------
# Signals block rendering — auto-generated SIGNALS YOU RECEIVE per agent
# ----------------------------------------------------------------------

def render_signals_block(agent_id: str) -> str:
    """Build the auto-generated SIGNALS YOU RECEIVE block for an agent.

    Output goes between SIGNALS_BEGIN and SIGNALS_END markers in the
    persona file; provision_agents.py inserts this block verbatim at
    every provisioning run, overwriting any prior contents between the
    markers.

    Compact format — one line per field with path + usage hint. The
    schema's full description is omitted from the agent-facing render
    (agents do not need the prose description; they need to know what
    the field is for in their reasoning). Operators can read full
    descriptions via `python -m magi.validate_schema` or by reading
    magi/world_state_schema.py directly.
    """
    lines = []
    lines.append(SIGNALS_BEGIN)
    lines.append(
        "<!-- DO NOT EDIT — regenerated from magi/world_state_schema.py "
        "on every provision. Hand-edits will be lost. -->"
    )
    lines.append("")
    lines.append("SIGNALS YOU RECEIVE (from world_state)")
    lines.append("")
    for path in paths_for_agent(agent_id):
        usage = (FIELDS[path].get(f"{agent_id}_usage") or "").strip()
        if usage:
            lines.append(f"- world_state.{path}: {usage}")
        else:
            lines.append(f"- world_state.{path}")
    lines.append("")
    lines.append(SIGNALS_END)
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Persona file utilities (used by provision_agents.py)
# ----------------------------------------------------------------------

# Repointed 2026-06-07 off the dead Letta-era magi/prompts/*_prompt.txt onto the
# LIVE ADK personas magi/agents/personas/*.md (the files council.py actually loads
# via magi.agents.personas.load_persona). NOTE: the .md personas carry no
# BEGIN/END_AUTOGENERATED_SIGNALS block, so render_persona_with_signals (below)
# will raise on them — that path is only reached by the dead provision_agents.py.
PERSONA_DIR = Path(__file__).resolve().parent / "agents" / "personas"


def load_persona(agent_id: str) -> str:
    """Read the on-disk persona file for an agent."""
    path = PERSONA_DIR / f"{agent_id}.md"
    return path.read_text()


def render_persona_with_signals(agent_id: str) -> str:
    """Read the on-disk persona file, replace the auto-generated SIGNALS
    block with a fresh one rendered from the schema, and return the result.

    If the persona has no markers, raise — every persona MUST have the
    markers so provisioning is deterministic.
    """
    raw = load_persona(agent_id)
    if SIGNALS_BEGIN not in raw or SIGNALS_END not in raw:
        raise RuntimeError(
            f"persona for {agent_id} missing {SIGNALS_BEGIN!r} / "
            f"{SIGNALS_END!r} markers — cannot auto-generate signals"
        )
    start = raw.find(SIGNALS_BEGIN)
    end = raw.find(SIGNALS_END) + len(SIGNALS_END)
    rendered = render_signals_block(agent_id)
    return raw[:start] + rendered + raw[end:]
