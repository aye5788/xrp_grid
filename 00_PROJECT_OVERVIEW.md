# MAGI — XRP Grid Bot — Project Overview

> **STATUS — AGENT LAYER MIGRATED TO GOOGLE ADK (in code, 2026-05-31); NOT RUN LIVE.**
> MAGI is still shut down at the service level (stopped + disabled 2026-05-28; no
> live orders). The council's agent-call layer has been **rebuilt off Letta onto
> Google ADK** — `magi/council.py` rewritten; three native ADK `LlmAgent`s in
> `magi/agents/`; **stateless per cycle**; Melchior emits an economic verdict
> (THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID). Code-complete, offline-validated;
> no model invoked, nothing deployed. See `01_CURRENT_STATE.md` "Session 2026-05-31
> (later) — ADK migration" for the authoritative description.
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
   "always fires"). As of 2026-05-31 these are native ADK `LlmAgent`s (was Letta
   Cloud). Each owns its vote vocabulary; Casper + Balthasar emit a structural vote
   field the engine reads via hard rule 0d:
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
4h timer is a BACKSTOP, not the primary trigger. (Historical: framed as "every 4
hours, 6 cycles/day", reduced from hourly 2026-05-18 to fit the $20/mo Letta
budget — that cost model no longer applies post-Letta.) Cost is tuned via gate
breach sensitivity, not the cadence constant.

Agents are **stateless per cycle** (ADK `include_contents="none"`) as of 2026-05-31
— each call gets persona (instruction) + freshly-injected world_state, no
persistent vendor memory/threads. (Historical: stateful Letta Cloud agents on
api.letta.com whose memory survived across cycles/restarts.) Cross-cycle
"memory" — what the system remembers — lives in `observer.db` (debate_records,
trajectory, etc.); a controlled per-agent recall layer (SQLite→prompt-injection)
is scoped but not yet built. See `01_CURRENT_STATE.md` Session 2026-05-31 (later).

### The three agents (ADK wiring, 2026-05-31)
| agent_id | Model (ADK) | Role / vote |
|---|---|---|
| casper | `gemini-2.5-flash` (native Gemini) | Regime classifier — `RANGING / TRENDING / UNCERTAIN` + `regime_action` |
| melchior | `LiteLlm("openai/gpt-4o")` | Grid economist — verdict `THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID` + geometry |
| balthasar | `LiteLlm("anthropic/claude-sonnet-4-6")` | Risk / survival — `CLEAR / PAUSE_LONGS / PAUSE_SHORTS / HALT` + `geometry_veto` |

> Built in `magi/council.py`; instructions from `magi/agents/personas/*.md`; votes
> forced by `magi/agents/schemas.py` (`RegimeVote` / `GridVote` / `RiskVote`).
> Balthasar's tier (Sonnet here vs the haiku-4-5 the live Letta agent ran) to be
> re-confirmed before live spend. (Historical Letta models: casper
> `google_ai/gemini-3-flash-preview`, melchior `openai/gpt-4o`, balthasar
> `anthropic/claude-haiku-4-5` with `model_settings` knobs — no longer applicable.)

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
- **Letta Cloud** (api.letta.com) — agent state, memory blocks, message threads.
  Authenticated via `LETTA_API_KEY` in `/root/xrp_grid/.env`. LLM config
  knobs synced via `magi/provision_agents.AGENT_CONFIG`.

## Services
| Service | State since 2026-05-28 shutdown | Pre-shutdown role |
|---|---|---|
| `magi.service` | **inactive + disabled** | active (scheduler, observer, MAGI cycles) |
| `magi-dashboard.service` | **inactive + disabled** | active (Flask :5000; public via cloudflared tunnel → api.ethobs.uk, app-side Flask cookie auth — see `/login`) |
| `letta.service` | inactive + disabled (self-hosted Docker, dormant for rollback only) | unchanged |

Both MAGI services were stopped + disabled on 2026-05-28 for the Letta migration
and will NOT auto-start on reboot. Do not restart without operator direction.
(Historical restart command, for reference only:
`systemctl restart magi.service magi-dashboard.service`.)

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

This is the architecture the gate-tuning work has been building toward — it is the
target, not a dial to re-derive from the cost-reduction history.

## See also

- `CLAUDE.md` — operating discipline, architecture intent, recurring failure
  patterns (auto-loaded at session start by Claude Code)
- `01_CURRENT_STATE.md` — verified facts, live agent IDs, session change log
- `02_NEXT_BUILD_TASKS.md` — work queue
- `03_INSTRUCTIONS_TO_CLAUDE.md` — tone, workflow, forbidden moves
