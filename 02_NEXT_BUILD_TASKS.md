# Next Build Tasks

Tasks 1 (Budget-aware Balthasar) and 2 (Kraken refactor) completed 2026-05-05. The paper simulator was fixed for spot semantics on 2026-05-06 (impossible-fill bug, inventory-aware grid construction, auto-rebase on startup). The risk wake mechanism was considered and deferred — see current priority section below. See `01_CURRENT_STATE.md` for full system state.

---

## Current priority — observation period

The simulator fix on 2026-05-06 is a significant correctness change. The first ~14 hours of Kraken paper data (May 5 evening through May 6 ~13:20 UTC) was generated against a broken simulator and should be treated as diagnostic data only. True spot-realistic paper trading begins 2026-05-06 ~13:20 UTC after the rebase.

Watch the dashboard over the next 24–48 hours specifically for:

- **Buy fills as XRP retraces.** With the corrected simulator, buys accumulate XRP into paper inventory and incrementally unlock more sell capacity on the next grid rebuild.
- **Asymmetric grid rebuilds.** When MAGI rebuilds the grid, log lines should show `Grid asymmetric — N buys + M sells` when XRP held can't cover a full symmetric sell ladder. This is expected and correct behavior.
- **Balthasar handling long-side skew.** As buys fill and inventory grows long, Balthasar should respond appropriately (PAUSE_LONGS or HALT at concentration thresholds). The prompt logic is symmetric — it should handle this correctly, but confirm with real data.

Beyond the immediate post-fix window, the same observation questions from before still apply:

1. **Shadow simulation fill accumulation:** do all 6 variants accumulate fills across observer cycles, or does some path silently drop ticks?
2. **Shadow variant P&L differentiation:** does any variant generate consistent positive rolling P&L%, or do all converge near zero?
3. **Level-count switching:** does the engine ever switch level_count based on shadow P&L, or do the gates (20 fills, 24h window, 0.10% margin) mean no switch ever fires in practice?
4. **Live grid fills:** does the paper grid generate fills during normal XRP trading hours?
5. **Balthasar escalation:** does Balthasar ever escalate beyond CLEAR? If never, the budget reasoning may be too conservative or the inventory never gets large enough.
6. **Casper regime variance:** does Casper's regime call change meaningfully across cycles, or consistently output RANGING?
7. **Operational issues:** any Kraken downtime, rate-limit hits, post-only rejections, or connectivity errors?
8. **Actual LLM cost per day:** 2 scheduled cycles + occasional manual triggers.

---

## Next tasks

### Paper inventory auto-rebalance
Paper mode has no rebalancing mechanism. When fills skew heavily to one side, the grid loses the ability to place orders on the other side (USD depleted = no buys; XRP depleted = no sells). Required manual reset on 2026-05-08. Options: (a) periodic rebalance back to 50/50 when skew exceeds threshold, or (b) synthetic inventory floor in paper mode that prevents either leg from hitting zero. Decision: option (b) preferred — simpler, doesn't interfere with P&L accounting.

### XRP news feed for Balthasar (requires FMP re-subscription)
Wire `search-crypto-news` (XRPUSD) into the MAGI cycle. One FMP API call per cycle, last 5 headlines, passed as `news_headlines` field in Balthasar's build_context(). Gives Balthasar visibility into tail-risk events (SEC rulings, exchange delistings, Ripple news) that technical indicators don't capture. Implementation: add call in magi/orchestrator.py before Balthasar prompt construction. No schema changes required. Blocked on active FMP subscription.

### Weekly synthesis / learning loop (deferred — wait for live data)
Offline weekly job that reads last 7 days of magi_decisions + grid_orders fills, runs a single LLM call asking for pattern synthesis, writes output to magi/knowledge/weekly_synthesis.md (capped at ~500 words, full rewrite not append). File injected into all three agent prompts. Concept: same as Anthropic's "Dreams" product but hand-rolled, multi-model compatible, SQLite-backed. Key design decision: replacement loop not accumulation loop — file stays bounded regardless of history length. Raw data stays in SQLite forever (cheap); distilled knowledge stays flat. DO NOT BUILD until 2-3 months of clean live trading data exists — synthesizing from paper data or broken-period data risks encoding artifacts as market wisdom.

### Shadow simulator spot fix

The shadow simulator (`grid/shadow_simulator.py`) has the same impossible-fill bug pattern that was fixed in the live engine. It accepts fills regardless of its own simulated inventory — XRP supply is effectively unlimited in its model. Shadow rolling P&L percentages are directional comparisons between variants, not spot-realistic P&L figures.

**When urgent:** not urgent. The shadow sim only drives level-count switching gates, not order placement. The switching gates already require 20+ fills and 24h of history. Fix when convenient after the live simulator has produced a few weeks of real data and the switching behavior is understood. Do not fix before the live engine's corrected behavior is validated.

### Risk wake mechanism

A threshold-triggered MAGI cycle that fires when inventory skew breaches a danger level (e.g., |skew| > 0.8), independent of the scheduled 9 AM / 2 PM windows. The original motivation was the -1.025 skew event on May 6, which was caused by the simulator bug — not a real risk pattern that the scheduled cycle failed to catch in time.

**When urgent:** deferred. We don't yet have evidence from the corrected simulator that scheduled cycles are too slow for genuinely-possible spot risk events. The wake mechanism should be designed against observed failure modes, not hypotheticals. Revisit after 1–2 weeks of corrected-simulator data; if a real skew-concentration event appears that the scheduled cycle handled too slowly, that's the design input.

---

## Task 3 — deferred items

These were identified and deferred during the Kraken refactor sprint. Priority order below is a starting suggestion; adjust based on observation period findings.

### Stop-loss on entire grid
Cancel all paper orders and HALT if XRP spot price drops X% below grid centre. Prevents the grid from accumulating unlimited long inventory into a sustained downtrend.

**When urgent:** required before flipping `paper=False`. Not urgent during paper validation — the guardrails (daily loss limit + Balthasar HALT) provide partial coverage in paper mode, but a sustained drop will hit the daily loss limit rather than cutting cleanly at the grid level.

### Two-factor paper→live confirmation
When `engine.paper` is set to `False`, require an explicit second confirmation step (e.g., a separate `--live` flag at startup plus a prompt, or a confirmation token written to a file). Prevents accidental live trading from a config typo.

**When urgent:** required before any live flip. Should be the first thing built once paper validation is satisfactory.

### Email or SMS alerts on HALT events
When guardrails or Balthasar fire a HALT, send an out-of-band alert to the operator. Currently, HALT events are only visible in the dashboard or journal logs.

**When urgent:** nice-to-have for paper, important for live. If the system halts at 2 AM and the operator doesn't check the dashboard for 12 hours, the halt sits silent.

### Exchange downtime detection
Currently, if Kraken is unreachable, the observer and scheduler will log errors and continue retrying on the next scheduled cycle. There is no clean pause-and-resume path. A sustained Kraken outage could produce a partially-cancelled grid with stale open orders.

**When urgent:** nice-to-have for paper (the retry behavior is safe if ugly), required for live.

### Backtest framework
Run the grid strategy + shadow simulator against historical XRP OHLCV data to validate parameter choices (spacing, level count, centre logic) without waiting for live cycles. Most valuable for calibrating GRID_SWITCH_THRESHOLD_PCT and GRID_SWITCH_MIN_FILLS.

**When urgent:** worth building if the shadow simulation observation period (2 weeks) doesn't generate enough fills to differentiate between variants. If 20 fills per variant requires months of paper data at normal XRP volatility, the backtest becomes the only practical calibration tool.

---

## Operator preferences

- **Paper first, always.** No live trading until the thesis is validated: the system is right >50% of the time and profitable after fees over a meaningful sample of fills. Two weeks of paper data minimum; likely more.
- **Correctness over features.** If the system produces a wrong answer (stale indicators, ghost orders, misrouted credentials), fix it before adding anything new. The observation period reveals correctness bugs — fix them as they appear.
- **Minimal blast radius on changes.** Prefer surgical edits to one file over refactors across multiple files. If a fix requires touching more than two files, reconsider the approach.
- **No live credentials in code.** All secrets via `.env` + `load_dotenv()`. Never hardcoded, never in git.
- **Ring-fence the budget.** The $50 bot universe is isolated from other Kraken holdings. The bot queries only `XXRP` and `ZUSD`. This constraint is intentional and must be preserved in any refactor.
- **Read before write.** Before any change to a file that touches live state (engine.py, kraken.py, scheduler.py, database.py), read the current version first. Don't patch from memory.
- **Verify before declaring done.** Code changes to trading logic require a verification step — either a log line in the journal confirming the new path executed, or a direct query against the DB confirming the expected state. "It should work" is not verification.
- **Flag discrepancies.** If a fact in the docs conflicts with what the code actually does, flag it rather than silently propagating the error. The docs are for debugging — inaccurate docs are worse than no docs.

---

## Deferred from 2026-05-07 architectural session

- **Recent-context analytics layer.** New module `magi/analytics.py` running SQL summaries against existing tables (candles, indicators, magi_decisions, grid_orders, inventory) producing short text summaries injected into each agent's prompt context. Per-agent isolation. Captures recent indicator trajectories, recent agent decision history, recent fill outcomes. Hand-rolled, not framework-based — every framework surveyed (Mem0, Letta, ByteRover, Hindsight, LangMem, Hermes-holographic, smysle/agent-memory) was wrong shape for our case (vector DB overhead, runtime takeover, wrong language, or built for chatbot conversation memory we don't have). Our shape: structured JSON decisions per cycle, recency-ordered retrieval, per-agent isolation, SQLite-already-present.

- **Historical base-rate analysis — COMPLETED 2026-05-08.** 46,300 hours of XRP/USD hourly OHLCV pulled from FMP API (Jan 2021 - May 2026). Six analyses completed. Base rates injected into all three agent prompts as static calibration block. Dataset in operator's Colab/Drive (xrp_hourly.csv). Refresh quarterly or when regime changes materially.

- **Architectural framing recorded from this session:** agents currently make decisions against state snapshots with no shared sense of trajectory or strategy outcome. Today's failure (Casper RANGING + grid bleed) was a symptom of this gap, not just a Casper-prompt bug. The recent-context analytics layer addresses tactical recall. The historical base-rate analysis addresses pattern knowledge. Both are needed; Casper prompt fix was the minimum bounded change to address tonight's specific failure.

- **Forbidden moves reaffirmed:** no Letta or agent runtime adoption (rejected last week, still off the table). No Mem0 or vector-DB-backed memory framework (overhead wrong-shaped for our scale and structure). No LangGraph migration (would replace working orchestrator). No krakenex / python-kraken-sdk. No third-party historical-data wrappers — Kraken's static CSV archive is the data source.
