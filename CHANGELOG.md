# CHANGELOG
All significant changes to the MAGI XRP Grid Bot.
Format: [DATE] [FILE(S)] — Description

---

## 2026-05-06
- [grid/engine.py, grid/exchanges/kraken.py] — Spot-realistic paper
  simulation. simulate_fills() now checks inventory before applying
  fills. build_grid_levels() trims ladders to coverable inventory.
  Impossible negative inventory states can no longer occur.
- [grid/engine.py] — Auto-rebase on impossible inventory state at
  startup. load_state() detects negative balances and queries live
  Kraken for corrected values.
- [magi/balthasar.py, magi/prompts/balthasar_prompt.txt] — Allocation-
  based risk math. allocation_skew replaces pct_deployed as primary
  risk signal. Thresholds: HALT ±0.85, PAUSE ±0.6.

## 2026-05-07
- [guardrails.py] — Daily loss guardrail switched to total-universe
  delta vs midnight baseline. Old formula summed cash flow and
  misread buy-into-dip as catastrophic loss.
- [grid/engine.py] — Concentration cap in place_order switched from
  size-based to allocation_skew directional check at ±0.90.
- [magi/melchior.py, magi/orchestrator.py] — inventory_skew wired
  into Melchior's context. Three-line bug fixed.
- [magi/casper.py, magi/prompts/casper_prompt.txt] — Biased chop
  detection rule added. EMA structure co-primary with ADX.

## 2026-05-08
- [magi/balthasar.py, magi/casper.py, magi/melchior.py] — Historical
  base rates injected into all three agent prompts from 46,300 bar
  XRP/USD backtest (Jan 2021 - May 2026).
- [magi/balthasar.py, magi/casper.py, magi/melchior.py] — Open order
  book context added. get_open_orders_summary() feeds order ladder
  and recent fills to Melchior and Balthasar.

## 2026-05-10
- [grid/engine.py] — PAUSE_LONGS/PAUSE_SHORTS now cancel resting
  orders on the relevant side. Previously logged but never executed.
- [database.py] — get_trajectory_context() added. All three agents
  receive regime_consecutive_cycles, cycles_since_structural_change,
  fills_since_last_magi, pause flags.
- [magi/orchestrator.py] — Sequential agent ordering with Casper
  regime passed to Melchior. Conviction weighting in apply_consensus.
- [grid/engine.py] — Melchior's recentre_target and
  spacing_adjustment_pct now used by engine instead of hardcoded
  fallbacks.
- [config.py] — MAKER_FEE corrected to 0.0016 (was 0.004).
- [dashboard.py] — trigger_magi now calls apply_magi_decision().
  Auth token hook added (MAGI_TRIGGER_TOKEN).

## 2026-05-11
- [database.py] — insert_candle switched from INSERT OR IGNORE to
  ON CONFLICT DO UPDATE SET high=MAX, low=MIN. Candle H/L now
  updates within the hour. Previously suppressed fills.
- [grid/engine.py] — simulate_fills() accepts candle_high and
  candle_low. Observer passes 1h candle H/L each cycle.
- [scheduler.py] — Self-replenishing grid. On every fill, replacement
  order placed at order['price'] * (1 ± spacing_pct).
- [grid/engine.py] — WIDEN recentres to current market price before
  rebuilding. Previously used stale centre causing one-directional
  XRP drain.
- [config.py] — MAX_GRID_SPACING_PCT=0.025, MIN_GRID_SPACING_PCT=
  0.003 added. WIDEN/TIGHTEN respect ceiling/floor.
- [grid/shadow_simulator.py] — Inventory guard added to process_tick.
  Shadow sim P&L reset to clean baseline.
- [magi/prompts/melchior_prompt.txt] — Rules 6+7 added (spacing
  saturation check, trajectory check). Empirical XRP base rates
  injected from 4-asset backtest (Mar 2022 - May 2026).
- [scheduler.py, dashboard.py] — IPC architecture. Scheduler hosts
  localhost:5001/internal/trigger_magi. Dashboard forwards to it.
  Dashboard engine is now read-only.
- [dashboard.py] — MAGI_TRIGGER_TOKEN set in .env. DO API billing
  integration with 5-minute cache. LLM runway display per agent.

## 2026-05-12
- [magi/market_knowledge.py] — NEW. Market knowledge module. Computes
  regime-conditional stats (forward returns, hit rates, drawdown) from
  full candles table daily. Replaces static HISTORICAL BASE RATES in
  all three agent prompts with dynamic injection at each MAGI cycle.
- [database.py] — market_knowledge table added.
- [scheduler.py] — Daily recompute trigger at midnight UTC.
- [candles table] — Bootstrapped with 69,503 Bitstamp hourly bars
  (May 2018 - Apr 2026). Observer appends Kraken candles hourly.
- [magi/market_knowledge.py] — ADX formula corrected to Wilder
  smoothed (ewm alpha=1/14). Regime classifier collapsed to 4 labels.
  Timestamp handling fixed for mixed tz-naive/tz-aware candle rows.
  Drawdown computation: random sampling (seed=42) and rolling-max
  reference peak.
- [magi/melchior.py] — Regime lookup replaced from static map to
  indicator-based 4-regime logic matching Casper/Balthasar.
- [magi/melchior.py] — grid_spacing_pct multiplied by 100 before
  context injection. Melchior was reading 0.027% instead of 2.675%.
