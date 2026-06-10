# CLAUDE.md

This file loads automatically at the start of every Claude Code session
in this repo. It encodes operating context and intent. State (what is
built, what is broken, what is next) lives in the five handoff docs at
repo root; read those after this one.

Handoff docs to read at session start:
- `00_PROJECT_OVERVIEW.md` — system shape, components, data layout
- `01_CURRENT_STATE.md`    — what is built and verified; do-not-re-derive facts
- `02_NEXT_BUILD_TASKS.md` — work queue
- `03_INSTRUCTIONS_TO_CLAUDE.md` — workflow rules and forbidden moves
- `04_EXPERIMENTAL_IDEAS.md` — design directions under consideration (NOT adopted;
  exploratory holding pen — if it conflicts with `01`/`02`, those win)

CLAUDE.md is intent and discipline. The handoff docs are state. If they
disagree, the handoff docs win for state; this file wins for how to work.

> **STATUS — MAGI IS RUNNING ON PAPER (started 2026-06-09 21:04 UTC). LETTA FULLY
> DECOUPLED. Decision layer = the hand-rolled arbiter orchestrator
> (`magi/council_v2.py`), live on a GATE-PRIMARY cadence.**
> `magi.service` and `magi-dashboard.service` are **active + enabled**. The engine is
> in PAPER mode — the live toggle is disarmed (`.env` `MAGI_LIVE_CONFIRM=NO`,
> `CONFIRM_LIVE` renamed `CONFIRM_LIVE.disarmed.20260609`), so no real Kraken orders
> are placed; fills are SIMULATED against real market prices into a paper ledger
> (Kraken has no demo accounts — the paper layer is our own; real balances are read
> only for the startup fund-detection gate and price data). The paper run started
> from a freshly reset book: stale pre-shutdown orders cancelled, paper inventory
> rebased to the real Kraken balances (~30.0 XRP + ~$27.18), and the scorer built a
> 5-level 0.75% grid around ~$1.141. The prior LIVE run (2026-05-23 → 2026-05-28
> shutdown, snapshot in `snapshots/letta_shutdown_2026-05-28/`) keeps its own PnL
> record, separate from the paper scope (see §4 PnL scoping).
>
> The council's agent-call layer was **rebuilt off Letta** and the decision layer is
> the **HAND-ROLLED arbiter orchestrator** `magi/council_v2.py` (Stage 3, 2026-06-08)
> — direct vendor-SDK calls + owned SQLite state + per-cycle world_state, sequential
> six-call choreography (Casper → Melchior → Balthasar openings, Casper+Melchior
> rebuttal, Balthasar synthesis as arbiter). It is **NOT CrewAI and NOT an ADK
> framework layer**: CrewAI's only valuable IP was its schema layer, now rebuilt as
> `magi/agents/schema_tools.py`, and CrewAI's strict pipeline was proven to break the
> conditional `GridVote.geometry` contract. (An ADK `council.py` exists from the
> 2026-05-31 migration but is **unchanged and superseded**.)
> Seats are **stateless per cycle**, each emitting a Pydantic `output_schema` vote;
> Melchior was redesigned from an action-selector into an economic-verdict judge
> (THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID).
>
> **COUNCIL LINEUP — authoritative; all three seats WIRED into `council_v2` and
> RUNNING on paper (2026-06-09):**
> - **Casper → `gemini-2.5-flash`** (native Gemini).
> - **Balthasar → `anthropic/claude-sonnet-4-6`** — Sonnet is the DECIDED tier (the
>   old live Letta agent historically ran `claude-haiku-4-5`).
> - **Melchior → `deepseek-v4-pro`** (`magi/agents/melchior_deepseek.py`; DeepSeek
>   Anthropic-compat endpoint, `thinking` disabled).
>
> Model names you meet in the *historical* sections below — Casper
> `gemini-3-flash-preview`, Melchior `gpt-4o`, Balthasar `claude-haiku-4-5` — are the
> **Letta-era** lineup; do not read them as current.
>
> **LIVE vs. EXPERIMENTAL — do not conflate them.** MAGI is trading **ON PAPER** as of
> 2026-06-09: `magi.service` runs the scheduler → observer → `council_v2` → hard rules
> → engine chain on the gate-primary cadence; `magi-dashboard.service` serves the MAGI
> dashboard (gutted of Letta-era panels). The tape-collection stack is STOOD DOWN
> (data retained; warehouse timers still run). Offline scaffolds (Casper tuning
> scaffold, the Balthasar drawdown decision-test, `optimize/`) remain experimental and
> run nowhere. For the precise breakdown read the **STATE LEDGER at the top of
> `01_CURRENT_STATE.md`** — it is the authoritative live-vs-experimental map. The public boundary is
> preserved so `orchestrator.py` is unchanged in shape; new agent code lives in
> `magi/agents/` (schemas + personas).
>
> **Two direction changes from the 2026-05-29 scoping, now authoritative (both
> EXECUTED):**
> (1) Agents are **stateless**, NOT vendor-stateful — the "vendor owns memory/
> self_model/thread history" line is REVERSED. Letta's stateful agent layer caused
> thread-anchoring, the freshness-retry tax, and self_model corruption; statelessness
> is the deliberate fix. The controlled, SQLite-sourced, prompt-injected **Journal
> recall layer** is BUILT and wired into `council_v2` (committed `cebccb5`,
> 2026-06-09 — deterministic, config-version-filtered, NOT vendor memory).
> (2) Cadence is **gate-driven**, not a 4h clock — IMPLEMENTED 2026-06-09 (BU-2):
> `scheduler.py` fires ONE daily clock-floor council call (`MAGI_DAILY_HOUR_EST = 20`,
> i.e. 20:00 EST end-of-day assessment, grid or no grid) plus a 25h max-silence
> backstop (`MAGI_MAX_SILENCE_HOURS`); every call in between comes only from the
> always-on free gate's wake-class triggers — **T14 / T2 / T11 / T16** (60-min
> throttle, 15-min dwell, non-trading suppression, plus per-episode guards added
> 2026-06-10: T2 wakes once per breach episode, the new T16 drawdown trigger wakes
> once per 3%-wide drawdown rung — a standing breach never re-wakes the council
> hourly). The Letta-era `MAGI_HOURS_EST = [0,4,8,12,16,20]` 4h
> schedule is DELETED (a duplicate of the daily hour lives in `dashboard.py:
> _next_magi_eta` — change both).
>
> **Services RUNNING (paper).** Do not flip to live trading, change cadence
> constants, or stop/redeploy services without explicit operator direction.
> Pre-migration originals are archived under `archive/pre_adk_migration_2026-05-31/`;
> the Letta-era live-path modules removed in the 2026-06-09 decoupling are under
> `archive/letta_decoupling_2026-06-09/` (config_validator, memory_lifecycle, costs).
> `LETTA_API_KEY` is commented out in `.env` and the `agent_registry` Letta UUIDs are
> blanked — nothing on the box can reach Letta Cloud. (The schema-400 /
> `additionalProperties` guard is a live invariant — see §4 below.)

## 1. What MAGI is

> Updated 2026-06-09: MAGI is RUNNING ON PAPER (see STATUS above). The real-money
> description below is the system as it ran live 2026-05-23 → 2026-05-28; the same
> goal and mechanics now apply to the paper validation run.

MAGI is an XRP/USD spot grid bot running on Kraken — **was live 2026-05-23
→ 2026-05-28** (paper⇄live was a single env-var toggle: `MAGI_LIVE_CONFIRM=YES`
in `.env` plus the `CONFIRM_LIVE` gate file) — with a three-agent LLM council
advising structural decisions (rebuilt off Letta; see the STATUS COUNCIL LINEUP
block above for the current seats). Real account balances as of the 2026-06-09
paper reset: ~30.0 XRP + ~$27.18 USD ≈ $61.5 — the paper ledger was rebased to
exactly these. The goal is a
profitable adaptive grid: net-positive PnL after Kraken tier-0 fees
(maker 0.25%, taker 0.40%) with >50% directional accuracy on the bot's
trade actions, surviving without manual intervention.

This is a trading system, not a research project, not a learning
exercise, not a demo of agentic AI. Every change should be defensible
against the operating goal: filling more, accumulating fees, not
deadlocking, not damaging the book. "Cleaner" architecture that does not
move filling/surviving/adapting is a lower priority than the operator's
stated goal.

## 2. Architecture intent

Three layers, complementary by design.

**Layer 1 — Council judgment.** Three agents vote independently each cycle
(Round 0). As of 2026-06-06 the decision layer is a **hand-rolled orchestrator**
(direct vendor-SDK calls, owned SQLite state, per-cycle world_state, sequence
gate→Casper→Melchior→Balthasar — NOT CrewAI, NOT an ADK framework); the three seats are
proven standalone, stateless per cycle, each emitting a Pydantic `output_schema` vote
built via `schema_for_tool`. (The 2026-05-31 ADK `council.py` is unchanged and
superseded by this direction; its R1/conflict logic described below carries forward.)
Round 1 is a synthesis pass where each agent revises after seeing peers' R0; it is
CONDITIONAL (2026-05-27) — `magi/council.py:should_run_r1` fires it only when a
genuine position/lever conflict exists AND that conflict is new vs. the prior
cycle. Aligned cycles and frozen standoffs skip R1 (the hard-rule layer resolves
both regardless), which bounds council cost. The council
exists for cases where multiple defensible answers exist — regime
classification, when to recentre vs. tighten, when concentration risk has
crossed a judgment threshold. Judgment is delegated here precisely because it
cannot be encoded as a rule without losing nuance.

**Layer 2 — Hard rules.** `magi/orchestrator.py:enforce_hard_rules`
applies Python-enforced overrides on top of council consensus. These
are non-negotiable survival constraints: `[RECENTRE_COOLDOWN]`,
`[GRID_DEGENERATE]`, `[PAUSE_INVALID]`, `[USD_BUFFER_FLOOR]`,
`[XRP_BUFFER_FLOOR]`, `[ALLOC_SKEW_CEILING]`, `[DAILY_LOSS_LIMIT]`,
`[KILL_SWITCH]`. Hard rules exist for known-deterministic survival
conditions where there is one correct answer regardless of judgment.
The council can be overridden silently; there is no penalty for being
overridden. There is a penalty for voting strategically to avoid
overrides instead of reading the data.

**Layer 3 — Execution.** `grid/engine.py` builds and maintains the
ladder, places paper orders, tracks fills. The engine is downstream of
the council and the rule layer. When behavior looks broken, suspect
the engine and `world_state` inputs **before** suspecting the prompts.
The brain is downstream of the hands.

The two upper layers are not in tension. The rule layer catches what
the council reliably gets wrong (especially under model-compliance
limits); the council catches what the rule layer cannot encode without
losing context. Trying to push everything into hard rules collapses
judgment; trying to push everything into the council ignores known
model failures. Both are load-bearing.

## 3. The council is architectural diversity, not three voices saying the same thing

The three agents run different LLM providers by design, not by
accident (seats proven standalone; orchestrator hand-rolled, 2026-06-06):

- **Casper / `gemini-2.5-flash` (native Gemini)** — regime classification; tends
  to favour structural classification. Stateless per cycle: world_state is
  injected fresh into the prompt each call (no persistent self_model block). Proven
  standalone via a read-only probe through `schema_for_tool`.
- **Melchior / `deepseek-v4-pro`** — grid economist. Proven standalone
  (`magi/agents/melchior_deepseek.py`, 2026-06-05 probe), called via the DeepSeek
  Anthropic-compat endpoint with `thinking` explicitly DISABLED. Redesigned to emit an
  economic VERDICT (THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID), not an action.
  (The seat historically ran GPT-4o, which anchored on Letta thread history;
  statelessness + the model swap remove that.)
- **Balthasar / `anthropic/claude-sonnet-4-6`** — risk/survival; defaults
  risk-conservative. Proven standalone. Sonnet is the DECIDED tier for the rebuild (the
  live Letta Balthasar historically ran `claude-haiku-4-5`). Now also owns
  downtrend/capital-erosion risk via a corrected persona (see `02` item 0★).

These known biases are the architectural diversity — one agent's blind spot is
another's signal. When the three genuinely diverge, the verdict-aware
`_r0_conflict` → conditional Round 1 surfaces it productively. The goal is NOT to
engineer identical outputs. Treat the model mix as a feature; encode known biases
into per-agent personas (`magi/agents/personas/`). NOTE: the agents no longer hold
vendor-side memory or self_model — per-agent learning, when built, is the
deterministic SQLite-sourced recall layer (stateless agent + injected context),
NOT a model-owned block.

## 4. Source-of-truth facts

Things that have been re-derived wastefully across sessions; do not
re-derive these.

- `debate_records` (in `observer.db`) is the **canonical** write target
  for council decisions in Phase 5. One row per cycle.
- `magi_decisions` is **dual-written** for legacy consumers. It is not the
  canonical source; it is maintained for backward compat. The dual-write
  justification rests on `learning.py` (live-path verification pending —
  `02_NEXT_BUILD_TASKS.md` item M6) and the dashboard panels not yet migrated off
  `magi_decisions`. NOTE (verified 2026-05-29): `extract_test_cases.py` is a
  **one-off manual script, not an active consumer** — top-to-bottom executable, no
  `__main__` guard, imported by nothing, invoked by no service/cron — so it is NOT
  a reason to keep the dual-write. (Prior doc text listing it as a live consumer
  was overstated; see `01_CURRENT_STATE.md` Session 2026-05-29 §7-H.)
- **Agent layer is a HAND-ROLLED orchestrator as of 2026-06-06; the Letta layer is
  gone.** The decision layer is ~150 lines of direct vendor-SDK calls + owned SQLite
  state + per-cycle world_state assembly (sequence gate→Casper→Melchior→Balthasar) —
  NOT CrewAI, NOT an ADK framework. The three seats are proven standalone via probes
  through `schema_for_tool`; the orchestrator that assembles them is the next build.
  (The 2026-05-31 ADK `council.py` exists but is unchanged and superseded.) The Letta
  facts elsewhere in this file (agent UUIDs in `agent_registry`, `provision_agents.py`
  block sync, self_model / recent_outcomes / world_state Letta blocks, the freshness
  validator, the step/token sweep) are HISTORICAL — they describe the replaced layer.
  Seats are built in code from `magi/agents/personas/*.md` (instruction) +
  `magi/agents/schemas.py` (output_schema) via `magi/agents/schema_tools.py`; there are
  no Letta blocks to provision and no vendor-side agent state. `agent_registry` may
  still back the dashboard AGENT HEALTH chip but no longer maps to Letta UUIDs.
- **Invariants (learned the hard way — do NOT regress).**
  - **Seat schemas are always built via `magi/agents/schema_tools.py:schema_for_tool()`,
    never CrewAI `generate_model_description`.** CrewAI's strict pipeline forces every
    field `required` and strips `null` — it breaks the conditional `GridVote.geometry`
    contract (geometry present iff verdict==RECONFIGURE). `schema_for_tool` preserves the
    real optional/nullable shape.
  - **`schema_for_tool` strips `additionalProperties` centrally**, so the native-Gemini
    `additionalProperties` 400 stays dead and any seat schema may use `extra="forbid"`
    safely. **Do not remove that strip.**
  - **DeepSeek / Melchior: `thinking` is explicitly DISABLED.** v4-pro defaults thinking
    ON server-side, which 400s under a forced `tool_choice`.
  - **Spend / action gates are HARD STOPS for the operator to clear — never thresholds
    the agent self-clears.** Under-ceiling is not a go.
- **Melchior emits a verdict, not an action** (2026-05-31). `resolve_consensus`
  returns `grid_verdict ∈ {THESIS_HOLDS, RECONFIGURE, NO_PROFITABLE_GRID}`;
  `enforce_hard_rules` translates it (THESIS_HOLDS→MAINTAIN, RECONFIGURE→RECENTRE
  +geometry, NO_PROFITABLE_GRID→GRID_PAUSE stand-down). The regime_action /
  geometry_veto veto ladder (rule 0d) still gates a RECONFIGURE unchanged.
- Hard rules live in `magi/orchestrator.py:HARD_RULES` plus the steps
  inside `enforce_hard_rules`. There is no Supervisor concept; it was
  rejected and removed. Do not re-introduce it.
- Live database is the truth for live data. Repo code is the truth
  for code behavior. Handoff docs and this file describe intent; if
  they disagree with the live state, the live state is current and
  the docs are stale.
- **Publishing the handoff docs.** The six docs (`CLAUDE.md`,
  `00`–`04`) are published to the private repo `aye5788/magi-docs` by
  running `bash /root/magi_docs/sync.sh` — it copies the current docs
  out of `/root/xrp_grid`, commits, and pushes `origin main`. That is
  the definitive doc-publish path. `aye5788/xrp_grid` is the code repo
  only; never push docs there. **These published docs have two readers — a future
  Claude Code session (in-repo) and a claude.ai chat that has ONLY the markdown (no
  repo/code/DB/tools). Write every doc update to be self-contained for that code-blind
  reader: state facts and reasoning in the prose, never as "go check the code." See
  the two-readers rule in `03_INSTRUCTIONS_TO_CLAUDE.md`.**
- Grid spacing clamps: `MIN_GRID_SPACING_PCT = 0.003`,
  `MAX_GRID_SPACING_PCT = 0.025` (0.3% to 2.5%). Set in `config.py`.
- **Per-order size is FIXED at `ORDER_SIZE_XRP = 1.65`** (config.py, the
  Kraken XRP minimum). Operator directive 2026-05-24: every grid order —
  buy, sell, and the executed anchor — is exactly 1.65 XRP, never more,
  regardless of holdings or level count. `engine.compute_order_size`
  returns this flat constant (or `0.0` when a side has no levels). The
  prior holdings-division model (`xrp/N` / `(usd/N)/centre`, floored at
  the min, capped at half-inventory) is **removed** — do not re-introduce
  it; it produced 14–24 XRP orders on the small live book. To deploy more
  capital, raise the level count (more 1.65-XRP orders), not the size.
  Buy and sell sides are equal-sized, so round trips match 1:1 under
  FIFO. The shadow sim's level-switch is size-invariant (its PnL metric
  is a ratio), so it was deliberately left on its own sizing model.
- Asset analysis (already complete): DOGE, XRP, SOL viable; ADA
  eliminated. XRP optimal spacing is 1.5%.
- Kraken tier-0 fees: maker 0.25% (`MAKER_FEE`), taker 0.40%
  (`TAKER_FEE`). Round-trip break-even revised: maker-maker round-trip
  ~0.50%; taker-taker ~0.80%. At XRP's 1.5% optimal spacing the grid is
  still net-positive but margin is thinner than prior doctrine assumed.
  Verified via live test order 2026-05-23.
- **Per-level scorer fee basis is MAKER, not taker** (`config.py:
  GRID_LEVEL_FEE_PER_SIDE = MAKER_FEE`, set 2026-05-24). The
  `spacing_evaluator` acceptability/ranking is the enforcement point for
  "every grid level must be net-positive after fees, or stand down"
  (`acceptable iff spacing > 2*fee AND total_pnl_pct > 0`). The recurring
  per-level round-trip is two MAKER fills (resting arms), so the floor is
  `2*MAKER_FEE = 0.50%` — NOT taker. All three scorer call sites
  (`scheduler.py` first-boot, `orchestrator.py` Melchior world_state +
  current-config, `gate.py`) pass `GRID_LEVEL_FEE_PER_SIDE`. Do not revert
  to `TAKER_FEE` here: taker pinned the tightest selectable spacing at 1.0%
  and made the grid stand down in low vol; maker unlocks the fee-positive
  0.75% grid (validated: ~3–6× more fills on live candles, all net-positive).
  The one-time anchor IS a taker market order, but it is an amortized setup
  cost handled in `engine._execute_anchor`, deliberately outside the
  per-level fee-positivity model. This is also why "Melchior should author
  spacing" is the wrong frame — the deterministic scorer owns fee-positive
  spacing (GPT-4o can't reliably author geometry); the lever is the scorer's
  fee input, which was the actual miscalibration.
- **Live execution path** (shipped 2026-05-23): `engine._execute_anchor`
  places a real Kraken market order via `KrakenExchange.add_market_order`,
  polls `query_order` (QueryOrders) for fill, reconciles inventory from
  `get_balances()`. Resting arms: live `place_order` persists to
  `grid_orders` by Kraken txid; `engine.reconcile_live_fills_from_kraken()`
  (live counterpart to paper `simulate_fills`, called from the observer
  cycle when `not engine.paper`) matches our open txids against
  `get_closed_orders` (ClosedOrders) and marks fills with Kraken's real
  price/fee. Inventory is always truth-of-record via `get_balances()`,
  never recomputed from the fee constants.
- **PnL is SCOPE-SPLIT: live vs paper (2026-06-09).** `grid/pnl.py:get_pnl_snapshot`
  discriminates by order-id shape: Kraken txids (`O…-…-…`) are LIVE fills; internal
  hex UUIDs are paper fills. `paper=True` returns the PAPER scope — non-txid fills at
  or after the `system_state['paper_run_started_utc']` cutoff (set at the 2026-06-09
  paper book reset, so the May paper-era fills stay excluded) — with the identical
  equity-based model (baseline anchored at the first in-scope fill). The dashboard
  passes `paper=engine.paper`, and the tile is labeled "Paper P&L" in paper mode.
  The live record (23 fills, total −$6.95 at scope-split time) is preserved
  untouched behind the default scope. Do not commingle the scopes — commingling was
  the original ~$10 PnL overstatement bug.
- **Dashboard auth** is app-side: a Flask signed-cookie session in
  `dashboard.py` (`/login`, `/logout`, `before_request` gate; password in
  `.env:DASHBOARD_PASSWORD`, `SECRET_KEY` in `.env`; 365-day cookie).
  nginx `auth_basic` was removed — the cloudflared tunnel hits Flask:5000
  directly, so nginx was never in the public path. Token-authenticated
  API calls (`X-Magi-Token`) bypass the login gate for automation.
- **Letta-era operational facts removed 2026-06-06** (restart cost in Letta
  credits, the Letta SDK `model_settings` note, `provision_agents.py:AGENT_CONFIG`
  sync, the Letta Evals suites/`run_all.sh`) described the replaced agent layer.
  The agent layer is now a hand-rolled orchestrator — see the §1 STATUS block and
  the STATE LEDGER in `01_CURRENT_STATE.md`; those Letta mechanics no longer apply.
- **Council-degradation contingency** (closed out 2026-05-20). Three
  hooks share a single fingerprint: an R0 row with
  `conviction == 0.0 AND crux LIKE '(no response)%'` is a degradation
  marker (matches `magi/council.py:SAFE_DEFAULTS`).
  1. **Degraded-mode hard rule** at the top of
     `enforce_hard_rules` (item -1): queries the last 2 historical
     `debate_records` rows (current cycle not yet inserted — write order
     verified) and counts agents that hit SAFE_DEFAULTS in BOTH rows.
     1 degraded → `[AGENT_DEGRADED:<agent_id>]` freeze
     (force `grid_action=MAINTAIN`, `risk_action=CLEAR`). 2-3 degraded →
     `[COUNCIL_COLLAPSED]` HALT. Rule 6 (`GRID_DEGENERATE`) skips while
     this freeze is in effect — a degraded council cannot supply
     trustworthy geometry. Edge-triggered alerts fire only on
     tier-up transitions; tier persisted in
     `system_state['last_degraded_tier']` (values `"0"` / `"1"` / `"2"`).
  2. **Observer backfill-notify alerting — REMOVED 2026-05-27 (P4).** This
     hook counted consecutive `client.agents.messages.create` failures from
     the 6h outcome-notify. That notify was retired: the 6h outcome now writes
     to the shared read-only `recent_outcomes` block
     (`observer._record_outcome_to_block`) instead of posting a per-agent
     thread message — no inference cost, no thread bloat, no "update your
     self_model" prompt (which had fed self_model corruption). With no
     `messages.create` in the backfill path there is no streak to count;
     `_backfill_failure_streak` and `category='backfill_notify_failed'` are
     gone. Agent unreachability is still caught on the R0 path
     (`_check_steps_for_alerts` / `_alert_exception` in `council.send_round_0`).
  3. **Memory-rotation pre-gate**: in
     `magi/memory_lifecycle.py:rotate_agent_memory`, step 0 (before
     snapshot) counts SAFE_DEFAULTS in the last 30 R0 rows for the
     target agent. If `>= 12/30` (40%), abort that agent's rotation
     with `status='skipped_degraded'` — no snapshot, no compact, no
     merge, no reset. New `memory_rotations.degraded_count_in_window`
     column records the count for every attempt (success or skip).
     `magi_alerts` row written at `severity='warn'`,
     `category='rotation_skipped_degraded'` — dashboard-only, no ntfy.
  - All three use `database.insert_alert` as the single capture point.
    Items 2 + 3 are `warn` severity (dashboard-only). Item 1 is
    `critical` (fires `magi/notify.py:send_ntfy` → phone).
- **READINESS panel** (decision-support, not control logic). One
  live-readiness gate set evaluated on every dashboard render via
  `magi/readiness.py:evaluate()`; rendered below the INVENTORY +
  PAPER P&L pair as a chip-grid matching the AGENT HEALTH style.
  - **Live readiness** — should real capital be deployed? Nine gates
    (L1–L9) evaluated against entire trading history since first
    fill. Verdict: 0 fails → GREEN, 1-2 → YELLOW, 3+ → RED.
  - The obsolete **renewal-decision** gate set (R1–R7, trailing-14d,
    "renew the $20/mo Letta plan by 2026-06-03?") was **removed
    2026-05-23** once the bot went live — it answered a question that
    no longer applies. `evaluate()` now returns only `{'live': …,
    'generated_at_utc': …}`. Don't re-introduce it.
  - Round-trip counting uses `grid.pnl._fifo_match` GLOBALLY for the
    lifetime gates — correct for inventory accounting.
  - HALT-state tracking does NOT exist; the fill-gap gate (L6)
    therefore counts HALT-induced gaps as if they were system
    downtime. Surfaced in the gate `detail` string. If HALT
    timestamps ever get logged, update `_max_fill_gap_hours` to
    exclude them.
  - API: `/api/readiness` — JSON shape `{live:{verdict,gates},
    generated_at_utc}`, matches the panel structure. Click-to-expand
    on each chip writes the full gate dict to browser console.log.
  - The verdict enforces nothing. The operator decides.
- **Push notifications** layer: `magi/notify.py:send_ntfy()` fires HTTPS
  POSTs to `ntfy.sh` for `severity='critical'` rows written via
  `database.insert_alert`. Topic URL is read from `.env`
  (`NTFY_TOPIC_URL`, full HTTPS URL form `https://ntfy.sh/<slug>`).
  Severity gating: `critical` → priority 5 (bypasses iOS DND);
  `warn`/`warning` → priority 3 (sent only if a caller invokes
  `send_ntfy` directly — `insert_alert` itself only fires on critical);
  `info` → never sent. Unset or empty `NTFY_TOPIC_URL` → silent no-op
  (alert capture must keep working without the notification layer). The
  3s timeout + blanket `except` inside `send_ntfy` make notification
  failure non-blocking by design. **ntfy.sh topics are public** —
  anyone who guesses the slug can read everything sent. The body
  intentionally contains ONLY severity / agent_id / category + "open
  dashboard" — never raw provider payloads, balance numbers, API keys,
  or anything operationally sensitive. Dashboard chip panel
  (`AGENT HEALTH`, served above ALERTS) is the visual companion: green/
  yellow/red per agent computed from the last 3 R0 rows in
  `debate_records`, where degraded = `conviction=0.0 AND crux LIKE
  '(no response)%'` (same SAFE_DEFAULTS fingerprint as `council.py`).
  API: `/api/agent_health`.
- **Hard-rule precedence ladder** (added 2026-05-24).
  `enforce_hard_rules` runs its rules in a fixed order, and a LATER rule may
  overwrite an EARLIER rule's `grid_action` — survival/integrity rules outrank
  council judgment, by design, not a bug. Order: council-degradation freeze →
  RECENTRE block (0a–0c) → council veto (0d → MAINTAIN) → kill switch /
  daily-loss / alloc-skew (→ HALT) → USD/XRP buffer floors → grid-degenerate
  (→ RECENTRE) → PAUSE_INVALID → geometry-injection / no-acceptable-variant
  (→ GRID_PAUSE). Canonical detail lives in the precedence-ladder paragraph in
  the `enforce_hard_rules` docstring (`magi/orchestrator.py`). This is the
  relationship icontract Invariant 1 models via `_RULE_0D_SUPERSEDING_TAGS`
  (see Session 2026-05-24 in `01_CURRENT_STATE.md`).

## 5. Operating discipline

### Schema contract — world_state fields are declared, not inferred

`magi/world_state_schema.py:FIELDS` is the single source of truth for
which fields appear in `world_state` and which agents consume each.
Adding/removing a field in `build_world_state()` MUST be matched by
a schema entry. Two enforcement layers:

- **Runtime:** `magi/world_state_schema.py:alert_on_runtime_drift(ws)`
  runs at the end of every `build_world_state()` call. On any
  mismatch (schema field missing from output, or output path not
  in schema), writes a `magi_alerts` row with `severity='critical',
  category='schema_drift_runtime'`. The existing ntfy push fires
  immediately. Trading continues — schema drift is a maintenance
  failure, not a trading-stop event.
- **Provisioning:** `magi/provision_agents.py` calls
  `magi/validate_schema.py:main()` BEFORE any Letta API call. Any
  ERROR aborts provisioning with exit 1. There is no
  `--allow-broken-refs` flag; fix the schema or the persona.

Personas reference world_state via two mechanisms:
1. The **auto-generated SIGNALS YOU RECEIVE** block, rendered from
   the schema between `<!-- BEGIN_AUTOGENERATED_SIGNALS -->` and
   `<!-- END_AUTOGENERATED_SIGNALS -->` markers in each persona
   file. The first line inside the block is a `DO NOT EDIT` comment.
   Hand-edits between the markers are silently overwritten at every
   provisioning run — the schema is the source of truth.
2. The **hand-authored decision tree** (everything outside the
   markers). References in the decision tree are validated:
   broken dotted-path references and broken bare names produce
   ERRORs; cross-domain prose mentions in the shared SYSTEM CONTEXT
   preamble are scoped out of validation.

To change what an agent sees: edit `FIELDS` (consumers list +
per-agent usage hint), then run `python -m magi.provision_agents`.
Don't edit the SIGNALS block in persona files directly.

CLI: `python -m magi.validate_schema` exits 0 on PASS, 1 on ERROR.

### General discipline



Discipline that prevents wasted work.

- **Verify before acting on assumptions.** The persona files on disk
  in `magi/prompts/` and the live persona blocks in Letta can differ
  (Letta normalises whitespace; orphan blocks linger at project
  scope). Check via the Letta SDK before trusting either as
  authoritative. The same applies to `debate_records` rows vs.
  `magi_decisions` rows for the same cycle.
- **Engine and council are separate concerns.** A status check ("is
  `magi.service` running, is the dashboard returning 200") is not an
  audit ("is the bot actually trading"). Always confirm both.
- **Vital signs at session start.** Before proposing any change, pull:
  `buy_count`, `sell_count`, `hours_since_last_fill`,
  `hours_since_last_rebuild`, order skew, distance from current price
  to the nearest fill level, recent hard-rule overrides. If any of
  these is abnormal, that is the work for this session, regardless
  of what the user requested. State that explicitly to the user.
- **Engine-first when behavior looks broken.** When the bot is not
  filling, suspect the engine (`grid/engine.py`), the indicator
  pipeline (`observer.py`), and the `world_state` builder
  (`orchestrator.build_world_state`) **before** suspecting the
  persona prompts. Past wasted-time pattern: persona iteration while
  an engine bug was the actual cause.
- **Surface similarity is not alignment.** When evaluating whether an
  agent's reflection / vote / decision matches the persona, run the
  current `world_state` through the persona's actual gating rules and
  check whether the persona-prescribed action matches what the agent
  produced. Do not check whether the wording sounds similar.
  Alignment proves itself in input→action correspondence, not in
  vocabulary.
- **Snapshot before mutating Letta state.** self_model blocks, agent
  configs, persona blocks all hold accumulated state. If you are
  about to overwrite one, write the current value to a file in
  `/tmp/` first with the current date in the filename, so the
  operator can review or restore.
- **Match deliverable shape to request.** Surgical edits → str_replace.
  Full-file rewrites → only when the operator asks, or when the
  alternative is a fragile multi-step edit. When shipping code for
  the operator to push, provide the final file, not a snippet plus
  navigation instructions.
- **Persona edits — no live eval gate on the rebuilt stack yet.** The old
  Letta eval harness (`evals/run_all.sh`, throwaway agents in the `magi-evals`
  Letta project) was retired and **removed with the Letta layer** — do not look
  for it. Persona text now lives in `magi/agents/personas/*.md` and is edited
  directly (there is no `provision_agents.py` step — that was Letta
  provisioning). Discipline before a persona edit: snapshot the file first
  (dated `.bak`), and keep the schema contract in sync —
  `world_state_schema.py:FIELDS` ↔ the auto-generated SIGNALS block (see §5
  above). The only per-agent offline scaffold that currently exists is Casper's
  forward-realized labeler under `optimize/casper/`; there is no
  Balthasar/Melchior scaffold and no full-council eval harness on the new stack
  (that lands with the hand-rolled orchestrator).

## 6. Forbidden moves

These have been rejected before; do not re-propose them.

- Self-hosted Letta. Decommissioned; files preserved for rollback
  only. Stay on Letta Cloud.
- Mem0, Graphiti, vector DBs, or any persistence layer the operator
  did not ask for. `observer.db` SQLite is the persistence layer.
- `krakenex` or any third-party Kraken wrapper. The system uses the
  Kraken REST API directly via the existing client.
- Coinbase One, Kraken+, or any paid subscription. The operator
  validates on the free tier.
- Scaling dollar amounts up to "make results more visible." The
  operator's goal is validation, not size.
- The Supervisor / override-authority concept. Removed; do not
  re-introduce.
- Anything that references the dead ETH futures system.
- Re-doing already-completed work. Search prior session context
  (conversation_search if available; session transcripts otherwise)
  before re-deriving facts already in `01_CURRENT_STATE.md`'s
  "Verified facts" section.
- Asking clarifying questions the handoff docs already answer.
- New external services, frameworks, or processes.

## 7. What constitutes a real audit

A status check confirms components are alive. An audit measures
against the design goal.

| | Status check | Audit |
|---|---|---|
| Question | Are services running? | Is the bot trading as designed? |
| Signals | systemctl active, HTTP 200, rows being written, no exceptions in journal | Fill rate, fee-positive PnL, council variety, debate fire rate, hard-rule override frequency, grid liveness over time |
| Verdict | binary | comparison against `>50% accuracy`, fee-positive, surviving |

The bot has been "humming" — services up, rows being written, dashboard
rendering — while not earning for >49 hours. A status check passes this;
an audit does not. When the operator asks for an audit, deliver an
audit. Do not substitute a status check.

Specifically, before declaring an audit complete, confirm these against
the design goal:
- Has the grid filled in the audit window? If not, why?
- Are the three agents producing varied positions, or reciting cached
  responses across cycles?
- Is `debate_triggered` rate non-zero over the recent window? If
  always zero, why?
- Are hard rules firing because the council is genuinely wrong, or
  because the council and rules are aligned and the rule layer is
  redundant?
- Does the consensus action this cycle actually move the bot closer
  to filling, given the current world state?

## 8. Recurring failure patterns

Patterns that have cost real time. Lesson per item.

- **Smart agents on a broken engine produce smart-sounding decisions on
  a dead system.** Engine bugs in scheduler replacement pricing and
  grid-degeneracy handling were the actual fill blockers while
  council architecture upgrades were the visible work. *Lesson:*
  before iterating on the council, verify the engine is actually
  capable of acting on a correct decision.

- **Surface similarity is not alignment.** Casper's evidence text in
  recent cycles read like the new persona's "stale base" doctrine,
  but the actual `world_state` inputs (`roc_6h=-0.98`) no longer
  matched the conditions the reflection was learned under. Alignment
  was coincidental. *Lesson:* check that the current inputs produce
  the persona-prescribed output, not that the wording sounds similar.

- **Prompt edits do not reliably move GPT-4o behavior.** Verbose
  persona sections produced zero observable effect under an A/B test
  in stationary conditions. GPT-4o anchors on conversation-history
  patterns over current persona text. *Lesson:* design around model
  compliance limits with hard rules and conversation-history
  awareness; prompt tightening is the weakest lever and the slowest
  to validate.

- **Casual orphan accumulation in Letta blocks.** Re-provisioning
  agents leaves stale persona / human / decisions / self_model
  blocks at project scope that the Letta web UI surfaces but no
  current agent reads. *Lesson:* verify which blocks an agent
  actually uses via `c.agents.blocks.list(agent_id)`, not by
  browsing the project-level Memory blocks UI.

- **Effort imbalance across symmetric work.** When fixing three
  per-agent files (one per agent), polish drifted toward Balthasar
  (Sonnet's home model). The operator caught it and called it out.
  *Lesson:* when work is symmetric across the three agents, set a
  target shape (line count, section structure, examples per agent)
  before writing, and equalise up to the highest standard rather
  than down to the lowest.

- **Self_model anchoring under persona change.** When the persona is
  rewritten but self_model reflections from the prior persona
  remain, the R0 prompt's "revise away from prior failure modes"
  directive elevates stale reflections to authoritative. *Lesson:*
  when changing a persona materially, audit each agent's self_model
  for entries that contradict the new persona and curate them
  explicitly. Snapshot before curating.

- **Conversation-history persistence in Letta agent threads.** GPT-4o
  in particular reproduces prior-cycle responses byte-for-byte even
  when world_state values have moved. *Lesson:* identical evidence
  lists across consecutive cycles are a smell. If you see them,
  check whether the agent is reading the current world_state at all
  by comparing cited numbers against the current `world_state`
  block.

- **"Round 1 debate" framed as the council's main resolution
  mechanism, but fired 1 of 38 cycles in early Phase 5.** The
  framing in handoff docs did not match observed behavior. *Lesson:*
  when the docs describe a mechanism, verify it fires at expected
  frequency before assuming it is doing work.

## 9. Operator preferences

Encoded once so they do not need to be restated each session.

- **Provide full edited files when shipping code for the operator
  to push.** Not snippets with "add this at line 247" instructions.
- **Use surgical edits (str_replace / Edit tool) when iterating.**
  Full-file rewrites waste tokens and review effort.
- **Use your own judgment when the operator asks you to.** Do not
  turn judgment questions into multiple-choice menus. Recommend a
  call and defend it.
- **Be honest about defaulting to easier paths.** When the operator
  catches a corner being cut, restart with the correct approach;
  do not minimise.
- **Plain, direct tone.** No apology spirals. No "great question",
  "you're absolutely right", or other sycophancy openers. No
  performative reassurance.
- **No clarifying questions the docs answer.** Spend up to a minute
  on read-only investigation (grep, file read, SDK query) before
  asking. If the answer is in `00`–`04` or this file, do not ask.
- **The operator notices effort imbalance.** Symmetric work
  (three agents, three personas, three self_models) gets symmetric
  attention. Do not invest more polish in one than the others.
- **Verify before claiming.** "I updated X" should be followed by
  evidence that X is updated, not just by the tool call that
  attempted the update.
- **Stop on unexplained errors.** Do not paper over.
- **No commits.** The operator pushes manually.

## Cross-reference

| Need to know… | Read… |
|---|---|
| What components exist, what writes where | `00_PROJECT_OVERVIEW.md` |
| What is built and verified, what facts are already established | `01_CURRENT_STATE.md` |
| What is next on the work queue | `02_NEXT_BUILD_TASKS.md` |
| Tone, workflow patterns, forbidden moves (detail) | `03_INSTRUCTIONS_TO_CLAUDE.md` |
| Why the architecture is what it is, how to work in it | this file |
| Design directions under consideration (not adopted) | `04_EXPERIMENTAL_IDEAS.md` |
| Live data | `observer.db` |
| Agent layer + model lineup (Letta dropped) | `magi/council.py` + `magi/agents/` |
