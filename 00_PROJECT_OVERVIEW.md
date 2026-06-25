# MAGI — XRP Grid Bot — Project Overview

> **STATUS — AGENT LAYER MIGRATED TO GOOGLE ADK (in code, 2026-05-31); NOT RUN LIVE.**
> MAGI is still shut down at the service level (stopped + disabled 2026-05-28; no
> live orders). The council's agent-call layer has been **rebuilt off Letta onto
> Google ADK** — `magi/council.py` rewritten; three native ADK `LlmAgent`s in
> `magi/agents/`; **stateless per cycle**; Melchior emits an economic verdict
> (THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID). Code-complete, offline-validated;
> no model invoked, nothing deployed.
>
> **DIRECTION SHIFTED 2026-06-06:** the decision layer is now a **hand-rolled
> orchestrator** (direct vendor-SDK calls + owned SQLite state + per-cycle world_state;
> NOT CrewAI, NOT an ADK framework). The three seats are **proven standalone but not
> wired**; that ADK `council.py` is **unchanged and superseded**. The two sentences
> above are the historical 2026-05-31 record — for current architecture see the **STATE
> LEDGER** at the top of `01_CURRENT_STATE.md` and the CLAUDE.md STATUS block.
>
> **This SUPERSEDES the 2026-05-29 scoping below in two ways:** (1) agents are
> **STATELESS**, not vendor-stateful — the "vendor owns agent memory" line is
> reversed; a controlled SQLite-sourced recall layer is scoped, not built. (2)
> Cadence is gate-driven (free gate decides whether the paid council wakes; floor ≈
> 1 call/day), the 4h timer only a backstop. The "Migration target architecture"
> section at the foot (Agent Studio / Memory Bank / Responses+Conversations /
> Managed Agents) is now HISTORICAL — the build used ADK `LlmAgent`s with stateless
> prompt-injection instead. The engine / gate / SQLite / dashboard layers below are
> current. Do NOT restart services, deploy, or cancel the Letta subscription
> without operator direction; pre-migration originals are archived in
> `archive/pre_adk_migration_2026-05-31/`.
>
> **What is actually RUNNING vs. merely built/experimental (updated 2026-06-09):**
> **MAGI is RUNNING ON PAPER** — `magi.service` is active (scheduler → observer →
> `council_v2` arbiter → hard rules → engine in PAPER mode, no real Kraken orders) on
> the gate-primary cadence, and `magi-dashboard.service` serves the MAGI dashboard.
> The tape-collection stack is stood down (data retained). Letta is fully decoupled
> (key disarmed, registry UUIDs blanked, live-path call sites deleted). For the
> authoritative live-vs-experimental map, read the **STATE LEDGER at the top of
> `01_CURRENT_STATE.md`**.

## What this system is

MAGI is a grid-trading bot for XRP/USD on Kraken, **was live 2026-05-23 →
2026-05-28** (paper⇄live was a single env-var toggle — `MAGI_LIVE_CONFIRM=YES`
in `.env` + the `CONFIRM_LIVE` gate file). The end goal is profitable live
trading at meaningful scale. Hard constraint: right >50% of the time AND
profitable after fees.

**What it is becoming.** The same three-layer adaptive grid, with the council
rebuilt natively on three vendor platforms (Casper→Google, Melchior→OpenAI,
Balthasar→Anthropic) and the Letta runtime dropped. The north star is unchanged
and is the whole point: an *adaptive* grid that detects regime change and acts on
it faster than a static grid would. The migration is scoped and the vendor mapping
locked as of 2026-05-29 — see "Migration target architecture" below.

## Architecture

> **2026-05-31 — agent layer is ADK now.** This section's "three Letta Cloud
> agents", "R1 always fires", "stateful Letta agents", per-agent Letta memory
> blocks, and the every-4h cadence are HISTORICAL. Current: native ADK `LlmAgent`s,
> stateless per cycle, R1 novelty-gated, gate-driven cadence, Melchior emits a
> verdict. The three layers and hard-rule logic below are unchanged. See
> `01_CURRENT_STATE.md` "Session 2026-05-31 (later) — ADK migration" for the
> authoritative council description; the corrected per-item facts are inlined below.

Three layers, complementary by design:

1. **Council (judgment)** — three agents vote independently each cycle (Round 0),
   then conditionally synthesise (Round 1, **novelty-gated** since 2026-05-27 — was
   "always fires"). As of 2026-06-06 the decision layer is a **hand-rolled orchestrator**
   (direct vendor-SDK calls + owned SQLite state + per-cycle world_state; NOT CrewAI,
   NOT an ADK framework); the three seats are proven standalone (was Letta Cloud, then a
   now-superseded ADK `council.py`). Each owns its vote vocabulary; Casper + Balthasar
   emit a structural vote field the engine reads via hard rule 0d:
   - Casper → regime: `RANGING | TRENDING | UNCERTAIN` +
     `regime_action: EXECUTE | DEFER_STRUCTURAL | STAND_DOWN`
   - Melchior → economic **verdict**: `THESIS_HOLDS | RECONFIGURE |
     NO_PROFITABLE_GRID` (+ nested `geometry` on RECONFIGURE). `enforce_hard_rules`
     maps it to the engine's action: THESIS_HOLDS→MAINTAIN, RECONFIGURE→RECENTRE,
     NO_PROFITABLE_GRID→GRID_PAUSE. (Was: grid action `MAINTAIN | RECENTRE |
     TIGHTEN | WIDEN`.)
   - Balthasar → risk action: `CLEAR | PAUSE_LONGS | PAUSE_SHORTS | HALT` +
     `geometry_veto: PROCEED | HOLD_GEOMETRY | RISK_BLOCK`
2. **Hard rules (survival)** — `magi/orchestrator.py:enforce_hard_rules`
   applies Python-enforced overrides on top of council consensus. These are
   non-negotiable; the council can be overridden silently and there is no
   penalty for being overridden.
3. **Engine (execution)** — `grid/engine.py` builds and maintains the ladder,
   places paper orders, tracks fills.

**Cadence (corrected 2026-05-31): GATE-DRIVEN, not a fixed clock.** The always-on
gate (`magi/gate.py`, deterministic, zero API cost) runs every observer loop and
decides whether the paid council wakes at all. Floor ≈ 1 council call/day; ceiling
= how often the grid breaches gate rules. The `MAGI_HOURS_EST = [0,4,8,12,16,20]`
4h timer is a BACKSTOP, not the primary trigger. **IMPLEMENTED 2026-06-09 (BU-2):**
`scheduler.py` now fires ONE daily clock-floor call (`MAGI_DAILY_HOUR_EST = 20`,
20:00 EST, date-deduped) plus a 25h max-silence backstop
(`MAGI_MAX_SILENCE_HOURS`); the `MAGI_HOURS_EST = [0,4,8,12,16,20]` schedule is
DELETED (its dashboard ETA duplicate now mirrors the single daily hour). Every call
in between comes only from gate wake-class triggers — T14 (book one-sided/stranded),
T2 (allocation-skew breach), T11 (volatility-regime flip), and T16 (drawdown rung,
added 2026-06-10: drawdown from the 7d high banded into 3%-wide rungs anchored to
grid geometry, so a *deepening* downtrend convenes the council but a static one
doesn't) — bounded by a 60-min throttle, 15-min dwell, non-trading suppression, and
per-episode guards (added 2026-06-10: T2 wakes once per breach episode, T16 once
per same-or-deeper rung; a standing breach never re-wakes the council hourly).
(Historical: framed as "every 4 hours, 6 cycles/day",
reduced from hourly 2026-05-18 to fit the $20/mo Letta budget — that cost model no
longer applies post-Letta.) Cost is tuned via gate breach sensitivity, not the
cadence constant.

Agents are **stateless per cycle** (ADK `include_contents="none"`) as of 2026-05-31
— each call gets persona (instruction) + freshly-injected world_state, no
persistent vendor memory/threads. (Historical: stateful Letta Cloud agents on
api.letta.com whose memory survived across cycles/restarts.) Cross-cycle
"memory" — what the system remembers — lives in `observer.db` (debate_records,
trajectory, etc.); a controlled per-agent recall layer (SQLite→prompt-injection)
is scoped but not yet built. See `01_CURRENT_STATE.md` Session 2026-05-31 (later).

### The three agents (seats proven standalone; orchestrator hand-rolled, 2026-06-06)
| agent_id | Model | Status | Role / vote |
|---|---|---|---|
| casper | `gemini-2.5-flash` (native Gemini) | proven standalone, not wired | Regime classifier — `RANGING / TRENDING / UNCERTAIN` + `regime_action` |
| melchior | **`deepseek-v4-pro`** (DeepSeek Anthropic-compat, `thinking` disabled) | proven standalone, not wired | Grid economist — verdict `THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID` + geometry |
| balthasar | `anthropic/claude-sonnet-4-6` | proven standalone, not wired | Risk / survival — `CLEAR / PAUSE_LONGS / PAUSE_SHORTS / HALT` + `geometry_veto`; also owns downtrend/capital-erosion risk (corrected persona) |

> Each seat is proven standalone via a read-only probe through
> `magi/agents/schema_tools.py:schema_for_tool` (e.g. `magi/agents/melchior_deepseek.py`
> for DeepSeek); instructions from `magi/agents/personas/*.md`, votes forced by
> `magi/agents/schemas.py` (`RegimeVote` / `GridVote` / `RiskVote`). **None is wired
> into an orchestrator yet** — the hand-rolled orchestrator that assembles them (direct
> vendor-SDK calls + owned SQLite state + per-cycle world_state) is the next build (see
> `02_NEXT_BUILD_TASKS.md`). Sonnet is the DECIDED Balthasar tier (the live Letta agent
> ran haiku-4-5); DeepSeek is the DECIDED Melchior seat (the gpt-4o → DeepSeek swap is
> done as a proof). The 2026-05-31 ADK `council.py` is unchanged and superseded.
> (Historical Letta models: casper `google_ai/gemini-3-flash-preview`, melchior
> `openai/gpt-4o`, balthasar `anthropic/claude-haiku-4-5` — no longer applicable.)

> **SUPERSEDED (design) 2026-06-04:** the synthesizer/final-call role moves from Melchior
> to **Balthasar** (risk-arbiter). Melchior stays the grid economist (advisory); Balthasar
> synthesizes regime+economics and decides. NOT yet built — see 04_EXPERIMENTAL_IDEAS.md
> "Session 2026-06-04 — Council redesign". The text above describes the current ADK build.

The three providers are chosen **by design**, not by accident. Each model has
known biases (Gemini favours structural classification; GPT-4o anchors on
prior responses; Haiku defaults toward risk-conservative). The council's
strength is that one agent's blind spot is another's signal — the diversity
is the architecture. R1-always-fires + the two structural vote fields
(`regime_action`, `geometry_veto`) operationalise that diversity at the
engine layer — hard rule 0d reads them additively with the rule-based
overrides. The goal is NOT to engineer all three into producing identical
outputs. See `CLAUDE.md` §3 for the full framing.

Lowercase `agent_id` is the canonical key everywhere in code, database,
CONFLICT_MATRIX, and column prefixes in debate_records. Capitalized display
names appear only in the Letta dashboard.

### Per-agent memory blocks (7)
- `persona` — full role spec from `/root/xrp_grid/magi/prompts/<agent>_prompt.txt`
- `self_model` — agent-editable scratchpad for self-reflection
- `world_state` — shared, updated by orchestrator each cycle
- `casper_r0_output` / `melchior_r0_output` / `balthasar_r0_output` — shared, each agent's latest Round 0 response
- `cycle_phase` — round_0 or round_1

## Cycle protocol

1. **build_world_state()** (orchestrator.py) assembles indicators, inventory,
   open orders, market knowledge, gate triggers (`triggers_since_last_cycle`),
   HARD_RULES → writes to the shared `world_state` block.
2. **Round 0 (parallel)** — all three agents receive the cycle prompt and
   return JSON. Casper + Balthasar emit their structural vote field
   (`regime_action`, `geometry_veto`) alongside `{position, conviction,
   key_evidence, crux}`. Outputs are written to the three shared
   `*_r0_output` blocks so peers can read them in Round 1.
3. **Freshness validator** (`council.py:_validate_r0_response`) — checks
   each numeric evidence value matches `world_state` to 2dp. On mismatch,
   retry once with inline correction injection (Change A); SAFE_DEFAULTS +
   `severity='warn'` `freshness_retry_failed` alert if the retry also fails
   (Change B). Per-agent retry log persists in `debate_records.freshness_retries`.
4. **Round 1 (always fires)** — each agent receives peers' R0 outputs
   pasted into a synthesis prompt (`_R1_FRAMING_PER_AGENT`) and re-emits
   the full R0 schema, refining `regime_action` / `grid_action` /
   `geometry_veto` deliberately given peer positions. `CONFLICT_MATRIX`
   and `detect_conflict` are retained as dead code for backward import
   compat only — not consulted.
5. **resolve_consensus()** — reads R1 with R0 fallback. Emits `regime_action`
   and `geometry_veto` into the consensus dict the engine sees. `debate_triggered`
   is True iff R1 differs from R0 (not based on `CONFLICT_MATRIX` anymore).
6. **enforce_hard_rules()** — Python clamps the consensus against HARD_RULES
   (max spacing, min buffers, HALT file, etc.) before the grid engine sees it.
   New rule 0d (council veto) reads `consensus["regime_action"]` and
   `consensus["geometry_veto"]` additively with the existing rule-based
   downgrades (see "Hard rules" below).

## The learning loop (outcome backfill)

`observer.py` runs `backfill_outcomes()` each poll cycle. For every completed
cycle, it computes fills + P&L at 1h / 6h / 24h windows and writes them to
`debate_records`. At the 6h backfill, it sends an "Outcome for cycle X..."
user-role message to each agent's persistent Letta thread. Over time the
agents accumulate experience inside their persistent context.

## Memory rotation (thread → self_model distillation)

`magi/memory_lifecycle.py` rotates each agent's accumulated thread
context into its `self_model` block on a 30-cycle cadence (~5 days at
6 cycles/day). The agent self-compacts via
`client.agents.messages.compact()` with a `DISTILL_PROMPT` that asks
for up to 2 new `## Pattern N: <title>` blocks citing specific cycle
ids and `world_state` field names. The validator (strict — no
softening) requires ≥1 heading match and ≥1 `cyc_\d+` reference; any
agent whose output fails validation that rotation is skipped (status
recorded, thread not reset). Successful patterns are renumbered
server-side from the agent's existing `max_N + 1`, then pushed via
`client.blocks.update()`. The thread is then reset
(`messages.reset(agent_id, add_default_initial_messages=False)`)
only after the merge succeeds.

Cadence is driven by `rotation_cycle_counter` in the `system_state`
table; `scheduler.run_magi_cycle` increments it after every attempted
MAGI cycle (success or fail) and calls `maybe_rotate(counter)` only
on success. Guardrail-blocked cycles do NOT increment — no council
ran, no thread accumulated. `self_model` is hard-capped at
`SELF_MODEL_CHAR_CAP = 5000`; eviction of the lowest-numbered
`## Pattern N` block is the relief valve when a merge would exceed
that cap.

Per-rotation accounting in `memory_rotations` (one row per agent per
attempt). Status vocabulary: `success` / `validation_failed` /
`merge_failed` / `snapshot_failed` / `compact_failed` / `skipped` /
`error`. Pre-write snapshots live at
`/tmp/self_model_pre_rotation_<agent>_<YYYYMMDD>.json`.

## Hard rules (enforced in Python, not by agents)

Thresholds (in `magi/orchestrator.HARD_RULES`):
- `max_allocation_skew`: 0.85
- `min_usd_buffer`: $10
- `min_xrp_buffer_usd`: $10
- `daily_loss_limit_pct`: 0.15
- `halt_file`: `/root/xrp_grid/HALT`
- `max_grid_spacing_pct`: 0.025
- `min_grid_spacing_pct`: 0.003

Override tags applied inside `enforce_hard_rules` (emitted in cycle notes
and stored in `debate_records.hard_rule_overrides` as a JSON-encoded list):
- `[KILL_SWITCH]` — HALT file present
- `[DAILY_LOSS_LIMIT]` — daily PnL below the limit
- `[ALLOC_SKEW_CEILING]` — |skew| > 0.85
- `[USD_BUFFER_FLOOR]` — usd_held < $10 → upgrade CLEAR to PAUSE_LONGS
- `[XRP_BUFFER_FLOOR]` — xrp_value_usd < $10 → upgrade CLEAR to PAUSE_SHORTS
- `[GRID_DEGENERATE]` — buy_count=0 OR sell_count=0 OR (hours_since_last_fill > 24 AND last rebuild > 4h ago) → force RECENTRE + CLEAR
- `[RECENTRE_COOLDOWN]` — council voted RECENTRE within 1h of a fresh healthy rebuild (≥3 buys, ≥2 sells) → downgrade to MAINTAIN + CLEAR
- `[GRID_HEALTHY_NO_RECENTRE]` — book bilateral AND |price − centre| / centre < spacing → downgrade RECENTRE to MAINTAIN + CLEAR (time-independent complement to RECENTRE_COOLDOWN)
- `[RECENT_POSITION_HOLD]` — hours_since_last_fill < 2.0 AND |inventory_skew| > 0.15 AND book bilateral → downgrade RECENTRE/TIGHTEN/WIDEN to MAINTAIN + CLEAR (protects an open round-trip from being force-closed by a premature rebuild)
- `[PAUSE_INVALID]` — Balthasar voted PAUSE_X on a thin / balanced book that does not actually warrant a pause → downgrade to CLEAR
- `[GEOMETRY_INJECTED_FROM_SCORER]` — on RECENTRE/TIGHTEN/WIDEN with null agent geometry, inject scorer rank-1 into Melchior's R0 geometry block; `debate_records.geometry_source = 'scorer_fallback'`
- `[NO_ACCEPTABLE_VARIANT]` — scorer has no acceptable rank-1 on a rebuild action → force `grid_action = GRID_PAUSE` (engine cancels all orders and idles)
- `[GUARDRAILS_BLOCKED]` — pre-cycle `check_all_guardrails` failed; HALT
- `[AGENT_DEGRADED:<agent_id>]` — one of casper/melchior/balthasar returned SAFE_DEFAULTS (conviction=0, crux=`(no response)`) on each of the last 2 historical cycles → freeze: force `grid_action=MAINTAIN` + `risk_action=CLEAR`. Existing orders sit and fill; no RECENTRE/TIGHTEN/WIDEN. Rule 6 (`[GRID_DEGENERATE]`) explicitly skips while this freeze is active.
- `[COUNCIL_COLLAPSED]` — 2 or 3 agents matched SAFE_DEFAULTS on each of the last 2 historical cycles → force HALT. Edge-triggered alerts (severity=critical, ntfy push) fire only on the cycle that transitions into a higher tier; persisted via `system_state['last_degraded_tier']`.
- **Council-veto branch (rule 0d, new 2026-05-22)** — reads the two new
  structural vote fields and additively downgrades RECENTRE/TIGHTEN/WIDEN
  to MAINTAIN. Each tag fires independently; multiple may co-fire on the
  same cycle.
  - `[REGIME_DEFER]` — Casper said `regime_action=DEFER_STRUCTURAL`
  - `[REGIME_STANDDOWN]` — Casper said `regime_action=STAND_DOWN`
  - `[BALTHASAR_HOLD_GEOMETRY]` — Balthasar said `geometry_veto=HOLD_GEOMETRY`
  - `[BALTHASAR_RISK_BLOCK]` — Balthasar said `geometry_veto=RISK_BLOCK`

## Anchor-then-arms mechanic (engine layer, separate from MAGI)

Grid initialisation is two-stage. Stage 1: `engine._execute_anchor()`
places a single market order at current spot (direction follows
inventory skew, sized as one rung). Paper-mode fill is synchronous at
spot with TAKER_FEE; **live-mode places a real Kraken market order**
(`add_market_order`) and polls `query_order` for fill, then reconciles
inventory from `get_balances()` (shipped + exercised 2026-05-23). Stage
2: arm limit orders are built around the **actual anchor fill price**,
not the caller-supplied target centre. If the anchor fails, no arms are
placed; grid stays theoretical.

Spacing is never derived from a static default. First-boot grids
pull from `magi.spacing_evaluator` rank-1; subsequent rebuilds use
Melchior's emitted geometry or the `[GEOMETRY_INJECTED_FROM_SCORER]`
hard-rule fallback. `MAX_/MIN_GRID_SPACING_PCT` remain only as
safety clamps.

## Data layout

- **observer.db** SQLite — canonical tables:
  - `debate_records` (one row per cycle, including
    `hard_rule_overrides` JSON column, `freshness_retries` JSON column,
    `geometry_source`, `regime_action`, `geometry_veto`, and
    `outcome_{1,6,24}h_backfilled` flags)
  - `agent_registry` (logical agent ↔ Letta UUID)
  - `magi_gate_events` (one row per trigger evaluation; `trigger_id`,
    `fired`, `details`, `consumed_in_cycle`). Populated by the gate
    layer (`magi/gate.py`); consumed via `world_state.triggers_since_last_cycle`.
  - `ws_health` (Kraken WebSocket v2 substrate health rows;
    `last_heartbeat_age_sec`, `reconnect_count_1h`, `last_tick_age_sec`,
    `state`, `notes`)
  - `magi_alerts` (capture point for all `severity` rows; ntfy fires
    on critical only)
  - `memory_rotations` (one row per agent per rotation attempt;
    status, char counts before/after, patterns_added, snapshot path,
    `degraded_count_in_window` from the rotation pre-gate)
  - `system_state` (generic key/value with `updated_at`; users:
    `rotation_cycle_counter`, `last_degraded_tier`)
  - `magi_eval_runs` (Letta Evals Option A results)
  - `grid_state`, `grid_orders`, `inventory`, `indicators`, `candles`,
    `market_knowledge`, `letta_status`, `pnl_daily`, `token_usage`,
    `shadow_grid_state`
  - Legacy `magi_decisions` — **dual-written** for back-compat readers
    (`learning.py`, `extract_test_cases.py`, two dashboard panels not yet
    migrated). `debate_records` is canonical; do not introduce new
    `magi_decisions` readers.
- **market_tape.db** SQLite — **separate standalone collector, NOT observer.db.**
  Lives at `/root/xrp_grid/tape/market_tape.db`; written by the standalone Kraken
  XRP/USD market-tape collector (own systemd units: `tape-collector.service`,
  `tape-backup.service` + `tape-backup.timer`). Tables: `ohlc_1m`, `trades`,
  `spread`, `rollup_bars` (5m/1h/6h/1d derived), `collector_health`, `events`
  (plus a schema-defined `book_l2` that is currently EMPTY — the `book` channel is
  off upstream). Imports nothing from `magi/`, `grid/`, `observer.py`,
  `database.py`, or `scheduler.py`; runs whether MAGI is up or down. Online-backup
  → gzip → GCS (`gs://xrp-grid-tape-backups-ayn88`). **Documentation-of-record:**
  `github.com/aye5788/market-tape` (full operational writeup in `tape/README.md`).
  This is the uncontaminated replay/backtest substrate referenced in
  `04_EXPERIMENTAL_IDEAS.md` §6. The long-range historical series lives in the
  SEPARATE **history.db** warehouse documented next; the historical→live stitch is
  VERIFIED (2026-06-04) — see that bullet.
- **history.db** SQLite — **the contiguous long-range warehouse, SEPARATE from both
  observer.db and the live tape** (`/root/xrp_grid/tape/history.db`, ~776 MB). Built
  by `tape/warehouse.py` as one gap-free 1-minute XRP/USD series: **Bitstamp 2016-12
  → tape start** (`source=3`; OHLC-only — vwap is a close stand-in, `trades` NULL)
  stitched to **live Kraken bars** (`source=0` ws / `1` silent-minute backfill / `2`
  REST-recovered, 2026-06-02→now). ~4.98M `ohlc_1m` bars + permanent `rollup_bars`
  (5/60/360/1440-min). One table, one `source` flag; Kraken wins any overlap — query
  as a single series. **Seam VERIFIED (2026-06-04):** Bitstamp last bar 2026-06-02
  12:22 → Kraken first 12:23, exactly 1 min apart, no gap / no overlap / no dup
  (`ts_begin` PK). Flow is ONE-WAY from `market_tape.db` via `warehouse-append.timer`
  (hourly, local, gap-aware over a 7-day reconcile window so out-of-order backfills
  self-heal — was a strict MAX() watermark that silently skipped them, fixed
  2026-06-04) + `warehouse-backup.timer` (daily → GCS `…/history/history.db.gz`). As
  of 2026-06-04 the warehouse also carries the live **trades + spread** tape forward
  (one-time catch-up 2026-06-04: ~100.9k trades + ~64.6k spread back to collector start
  2026-06-02), so `rollup_bars` order-flow/spread columns populate for the live span
  (NULL on the pre-tape Bitstamp span; `book_l2` not captured). Append dedups via
  INSERT OR IGNORE over the 7-day window; the warehouse does NOT prune (downtime
  instrument; the live tape keeps its own 60-day prune). Residual: out-of-order spread
  arrivals below the ts watermark can be missed (acceptable, short-horizon). **Backtests:** OHLCV is continuous across the full
  ~9.5-year series; vwap / trade-count / order-flow / spread are live-span-only —
  filter by `source` for those fields. **Derived signals history (`signals_1h`,
  added 2026-06-07):** the warehouse also stores the dashboard's GRID CONDITIONS
  metrics as an **hourly time series** — one row per hour, *as of* that hour, holding
  the overall verdict plus each metric's value+status (realized volatility, regime
  efficiency-ratio, **drawdown-from-high**, harvest rate, flow imbalance). It is a
  pure deterministic replay of `tape/conditions.report()` over the stored bars, so it
  rebuilds at will and can never disagree with what the dashboard renders. **Backfilled
  2026-06-07** (`python -m tape.warehouse build-signals`): **83,017 hourly rows,
  2016-12-17 → 2026-06-07** (the first ~24h lacks a full trailing window, skipped); the
  hourly `warehouse-append` writes new rows going forward (no new service). A `source`
  flag marks each row backfilled(1)/live(0). **Flow imbalance is the one field NULL
  before 2026-06-02** — it needs the trade tape (live-era only; 122 populated rows at
  backfill); the other four signals AND the overall verdict (which excludes flow)
  reconstruct full-depth across the whole history. (The `drawdown-from-high` metric is new this session — a *directional*
  downtrend-bleed read added alongside the direction-blind regime ratio on the
  dashboard, using MAGI's own `drawdown_from_high` definition; see `tape/README.md`.)
- **Offline corpus sources for agent decision-tests (clarification, 2026-06-06).**
  Both `observer.db` candles (~8yr **hourly** XRP) and `tape/history.db` (~9.5yr
  **1-minute** contiguous XRP) are **reconstruction** substrates — you rebuild a past
  `world_state` from candles. NEITHER is "real live states": MAGI traded live only ~5
  days (2026-05-23→28) on a ~$67 book, and that decision data (`debate_records`) is
  **contaminated** (Letta-era) and barred as labels/baseline. **Forward-labeling**
  (label each reconstructed state by its forward price path — the proven 2026-06-06
  Balthasar method) is a *labeling method applied to candles*; it works on EITHER
  source. So the DB choice is **resolution/coverage (hourly vs 1-minute)**, NOT
  "live-vs-reconstructed" or "unlabeled-vs-labeled" — framing observer.db as "live
  unlabeled states" vs history.db as "labeled reconstructions" is a false split. (For
  the downtrend brake specifically, `02` item 0★ treats `drawdown_from_high_7d` as a
  Balthasar *judgment input* he weighs and cites — NOT a fitted `dd7d ≤ −X ⇒ PAUSE`
  threshold — and that field is not yet wired, so an offline Balthasar tuning corpus is
  secondary, not the next step.)
- **Letta Cloud** (api.letta.com) — agent state, memory blocks, message threads.
  Authenticated via `LETTA_API_KEY` in `/root/xrp_grid/.env`. LLM config
  knobs synced via `magi/provision_agents.AGENT_CONFIG`.

## Services
Verified against `systemctl` 2026-06-06. **The only things RUNNING are the market-tape
stack** (a separate data-collection concern); **MAGI trading is fully stopped.** See the
STATE LEDGER at the top of `01_CURRENT_STATE.md` for the full live-vs-experimental map.

| Service | State NOW (2026-06-25) | Role |
|---|---|---|
| `magi.service` | **inactive** | MAGI trading (scheduler, observer, council cycles) — engine deliberately SHUT DOWN (paper hold); will NOT auto-start |
| `magi-dashboard.service` | **active + enabled** | Serves the **MAGI dashboard** (waitress :5000, public `https://api.ethobs.uk`, app-side cookie auth). Restored to MAGI 2026-06-09; the public tunnel was rebuilt 2026-06-25 — see `05_COUNCIL_REDESIGN.md` §7 |
| `cloudflared.service` | **active + enabled** | Public tunnel `eth-observer` (`e4d95b41…`), locally-managed via on-disk `/etc/cloudflared/config.yml` (`api.ethobs.uk → localhost:5000`) |
| `tape-collector.service` | **inactive** | Tape collector STOOD DOWN 2026-06-09 for the paper bring-up (code intact) |
| `warehouse-append.timer` / `tape-backup.timer` / `warehouse-backup.timer` | **inactive** | Tape/warehouse timers stopped with the tape stand-down |
| `letta.service` | inactive + disabled | Self-hosted Letta Docker — dormant, kept for rollback only |

The MAGI **trading engine** (`magi.service`) is stopped (paper hold) and will NOT
auto-start on reboot; `magi-dashboard.service` runs and drives the **MAGI dashboard**
(restored to MAGI on 2026-06-09; the tape stack has been stood down since then). Do
not restart `magi.service`
without operator direction. (Historical restart command, reference only:
`systemctl restart magi.service`.)

## Out of scope / dead code
- Self-hosted Letta Docker (dormant; `/root/xrp_grid/letta/` and pgdata preserved for rollback)
- Old stateless `apply_consensus()` three-agent orchestrator (replaced)
- Supervisor / override authority concept (rejected; removed from dashboard)
- Mem0, Graphiti, persistent thread-only approaches (rejected — Letta Cloud is the runtime)
- ETH futures system (dead — do not reference)
- krakenex, python-kraken-sdk, any third-party Kraken wrapper (banned)

## Migration target architecture

Locked 2026-05-29 (scoping complete; see `01_CURRENT_STATE.md` Session 2026-05-29
and `LETTA_SURFACE_AUDIT.md`). This is the system being built, replacing the Letta
runtime described above.

**North star (the purpose, not a nice-to-have).** MAGI exists to be an *adaptive*
grid bot that solves the fatal weakness of a static grid: regime-blindness in
directional markets — a static grid catches falling knives in downtrends, sells
into rallies, and bleeds in any sustained one-way move. Every migration decision
must preserve or improve MAGI's ability to detect a regime change and act on it
faster than a static grid would. The "Highest priority 0★" item in
`02_NEXT_BUILD_TASKS.md` (grid bleeds in downtrends) is exactly this problem in the
old system; the migration is the chance to fix it with better-tuned native agents.
**This is not a cost-optimization project** — cost was the trigger, adaptiveness is
the purpose.

**Vendor mapping (locked).** Each agent rebuilds natively and *stateful* on its own
vendor platform; the vendor owns that agent's memory, persona, self_model
equivalent, and thread history. We do NOT mirror agent memory locally.
- **Casper → Google** — Gemini Enterprise Agent Platform / Gemini API Managed
  Agents, with Memory Bank for persistent memory and Sessions for in-cycle state.
  Build in progress in Google **Agent Studio**; the "Details" panel inputs
  (Description + Instructions) and the R0 output-contract spec are drafted in
  `casper_gcp/` (2026-05-31, M1 partial — see `01_CURRENT_STATE.md` Session
  2026-05-31).
- **Melchior → OpenAI** — Responses API + Conversations API; extended
  `prompt_cache_retention` (up to 24h) available.
- **Balthasar → Anthropic** — Claude Managed Agents (beta header
  `managed-agents-2026-04-01`); native scheduled memory consolidation ("dreaming")
  in research preview; session-hour runtime billed at $0.08/hr, opened per-cycle
  (not 24/7).

Each agent is seeded from `snapshots/letta_shutdown_2026-05-28/` — persona text,
self_model contents, and accumulated thread history (casper 825 / melchior 466 /
balthasar 704 messages) — and tuned on its vendor platform before going live in the
new infrastructure. **Exception — Casper's self_model is NOT carried forward**
(contaminated; being left behind, decided 2026-05-31). For Casper only, the persona
is the sole seed (curated into the Agent Studio Instructions field); the self_model
is dropped, not deconstructed into Memory Bank.

**State ownership.** Vendors own all *agent* state. We own SQLite (`observer.db`)
for everything non-agent: `debate_records`, `magi_gate_events`, `magi_alerts`,
`grid_state`, inventory, all trading-side state. The audit confirmed this is a
small move: of the 8 Letta blocks, only `world_state`, `recent_outcomes`, and
`cycle_phase` need re-delivery as per-call prompt content (rebuilt fresh from
SQLite each cycle); persona / self_model / thread history live vendor-side
natively. Per-call payload drops from the pre-shutdown ~80–114k tokens to ~5–8k
tokens.

**Cadence / call model (event-driven, not time-driven).** The gate calls the
council when conditions justify it; otherwise silence. The 4h `MAGI_HOURS_EST`
schedule is a backstop, not the primary trigger. Target steady state:
- **No active grid:** ~1 call/day at a designated time for assessment / daily
  recap; the gate keeps monitoring continuously.
- **Active grid:** scheduled cycles + gate-driven wakes, with the existing
  wake-class trigger curation (`WAKE_REQUIRES_ACTIVE_GRID`, `WAKE_DWELL_MINUTES`,
  `WAKE_MIN_INTERVAL_MIN`, conditional R1 via `should_run_r1`) continuing to bound
  cost.

This is the architecture the gate-tuning work has been building toward — and as of
2026-06-09 (BU-2) it is the IMPLEMENTED scheduler behavior, not just the target:
daily floor 20:00 EST + 25h max-silence backstop + gate wakes, running live on the
paper bring-up.

## See also

- `CLAUDE.md` — operating discipline, architecture intent, recurring failure
  patterns (auto-loaded at session start by Claude Code)
- `01_CURRENT_STATE.md` — verified facts, live agent IDs, session change log
- `02_NEXT_BUILD_TASKS.md` — work queue
- `03_INSTRUCTIONS_TO_CLAUDE.md` — tone, workflow, forbidden moves
