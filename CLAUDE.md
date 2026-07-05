# CLAUDE.md

> ⚠️ **DECISION LAYER REDESIGNED 2026-06-24 — READ `05_COUNCIL_REDESIGN.md` FIRST.**
> The council was rebuilt from the *arbiter relay* into a **blind-review,
> equal-seats** council (no arbiter; Balthasar is now `claude-haiku-4-5`, not
> sonnet). It lives on branch **`council-redesign`** (`fc62b93`, pushed; **not**
> merged to `main`). The council/decision-layer descriptions in §2–§3 below and in
> `00`/`01`/`02` are **stale** — `05_COUNCIL_REDESIGN.md` is authoritative for the
> council and lists exactly what's superseded, the current state, and the one known
> bug (Casper's Gemini propose 400). Everything else here (engine, hard rules, gate,
> data, paper/live) is still accurate.

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

> **STATUS — MAGI IS RUNNING ON PAPER (paper run started 2026-06-09 21:04 UTC;
> STOPPED 2026-06-11 by operator order for the five-fix rebuild; RESTARTED
> 2026-06-12 after a full reactivation audit cleared 4 blockers — rule-6
> exposure-cap gating, sub-floor book guard at startup, capped-rebuild
> 2-rung abort threshold, gate-event contamination cleanup — plus 3 degraded
> items: gate scorer-state candles carry `close` so T6/T7 detect again,
> gate-eval dead-man's switch in observer.poll_cycle, seat-accuracy Langfuse
> scores delivered convergently; observer.db converted to WAL + 30s busy
> timeouts; pytz dropped from scheduler). LETTA FULLY
> DECOUPLED. Decision layer = the hand-rolled arbiter orchestrator
> (`magi/council_v2.py`), live on a GATE-PRIMARY cadence.**
> **As of 2026-07-04 (CURRENT — supersedes the 2026-06-28 block below): `magi.service`
> is RUNNING on paper, uninterrupted since the 2026-07-02 20:54 UTC restart. The
> 2026-07-02 sessions shipped and DEPLOYED (all committed + pushed on
> `council-redesign`): (a) PnL DECOMPOSITION (`harvest` / `alpha_vs_hold` /
> `inventory_hold_delta` — never cite equity `total` alone) + the STAND_ASIDE
> WORK-OFF LADDER (`scheduler.maintain_workoff_ladder`, sells-only maker rungs above
> market, floor-capped, operator-gated arming) as `dab17a1`; (b) the 06-28 Open list
> ALL CLOSED as `429b6aa` — ntfy emoji-title crash fixed (wake notifications deliver
> for the first time), `tape_verdict` RESTORED (GCS snapshot + gap-free Bitstamp
> refill + hourly `tape-tail.timer`; W2's verdict arm is LIVE again — real W2 wake
> fired 07-03 11:00 UTC), grader predicates UNIFIED (one band-break definition backs
> both stance and seat graders; matured STAND_ASIDE rows in a rally now honestly
> score 0/6), `Ranking` ballots sanitized at `aggregate()` (first live catch of a
> duplicate ballot 07-02); (c) episode-aware startup gate (`4da7b53`) — restarts
> during an already-answered breach stay QUIET; (d) the proactive bug-catching
> architecture layers 1–3 LIVE (CI ruff+Hypothesis gate on GitHub; on-box
> `magi/invariants.py` riding the observer tick; MAGI-02 off-box falsifier deployed
> on the operator's desktop, 6/6 predicates verified unattended). LADDER VERIFIED
> LIVE 07-03/07-04: armed at the 07-03 00:00 UTC daily cycle, seeded 5 rungs, fills
> topped up correctly, headroom respected. MARKET: XRP rallied ~$1.03 → ~$1.15 while
> the council held STAND_ASIDE since 06-26 — paper `alpha_vs_hold` = −$0.47 (equity
> +$3.26 is inventory beta; harvest $0, zero round trips); the 07-04 daily cycle
> split 3 ways (Casper MAINTAIN / Melchior HALT / Balthasar STAND_ASIDE) — the
> stance-EXIT question and the rally-window accuracy review are the live watch
> items. LATER SAME DAY (2026-07-04 evening — DEPLOYED): the operator-driven audit
> of a Journal-redesign proposal exposed that **the LEARNING LOOP had been DEAD
> since the council redesign** — the per-seat Journal (`get_agent_recall`) lost its
> only caller at the 06-25/26 rebuild and the replacement shared ledger fed the
> seats raw equity `pnl_24h` (the banned metric) with none of the graded outcomes;
> four audits missed it (money-path scoped; doc claim trusted, never re-verified).
> REBUILT + DEPLOYED same day: (a) **SYNC RATIO** (`magi/sync_ratio.py`) — v2
> cost-based grading, each decision's dollar edge vs the rejected alternative via
> the shared forward sim (acceptance table over the paper run: 7 matured
> stand-asides netted +$0.03 vs deploying; the 07-01 rally decision alone SAVED
> $0.28 — the caution was free insurance, where hit-rate grading scored the same
> rows 0/7); (b) the ledger outcome line now carries those FACTS (no pass/fail
> verdicts — seats weigh the asymmetry; binary grades remain operator-facing
> observability only); (c) the cumulative condition-bucketed **COUNCIL TRACK
> RECORD** block; (d) **ENTRY PLUG** (`magi/entry_plug.py`) — reliability-weighted
> NOTEWORTHY PRECEDENTS from outside the recency window, weights recomputed
> deterministically from history (no mutable state; 5/5 synthetic mechanics
> checks; frozen superiority test pre-registered for ≥30 matured episodes);
> (e) `memory_injections` flight-recorder table — every cycle records what the
> seats were shown; (f) MAGI-02 **P7_learning_loop_alive** (proposed — operator
> promotion pending) + **P1 refined** (its 07-03 FAIL was a formalization
> false-positive: sweep-consumption by scheduled/W2 cycles counted as "wakes";
> now only genuine `gate_wake:W1` cycles count). Config fingerprint gained
> `memory_schema=sync_ratio_v1` → one-time version bump at deploy; council
> memory restarts at the new boundary and fills as cycles mature (~72h). Open:
> promote P7 + set its effective_from (operator), watch the first post-deploy
> cycles, MAGI-02 first miner run (Ollama not on the desktop yet), chaos-drill
> layer 4, dead-arbiter-module deletion + engine hygiene items (see `02`).**
> **As of 2026-06-28 (supersedes the 2026-06-26 block below): `magi.service`
> is RUNNING on paper. A real audit (4 parallel finders + own verification) found &
> fixed 3 bugs (committed): (1) HIGH — the observer grid-replenishment (`scheduler.py`)
> re-armed a BUY on every sell fill ignoring `pause_longs`/stance/exposure-cap — a
> council-BYPASS that silently undid STAND_ASIDE between cycles (verified firing
> 06-26/06-27; cancelled by luck) — now gated on the council's protective flags
> (`pause_longs` OR exposure-cap for buys, `pause_shorts` for sells; reads existing
> state, makes no market call). (2) MED — `roc_6h` was nulled hourly by `gate_monitor`'s
> empty-6h-list recompute (a 2nd writer to the `indicators` table); fixed to resample
> 6h from the 1h bars, verified live. (3) MED — added alert-only
> `world_state_schema.alert_on_stale_inputs` (catches shape-valid-but-null/stale council
> inputs that drift-validation can't see). Aggregation/anonymizer/engine-guards/
> paper-fills re-VERIFIED SOUND. Tape `history.db` is gone from the box → `tape_verdict`
> dead. §8 lesson: the first audit pass was a status-check that MISSED the live
> council-bypass — again. Detail: `01` (latest) + `05` (2026-06-28 TL;DR). Open:
> grader-predicate mismatch, `Ranking` permutation guard, tape restore/demote.**
> **As of 2026-06-26 (supersedes the 2026-06-25 block below): the trading
> engine `magi.service` is RUNNING ON PAPER again (restarted 2026-06-26, `enabled` +
> `active`); the dashboard is up at `https://api.ethobs.uk`. The blind-review persona
> rewrite is VALIDATED — a pre-restart audit found the rewritten personas read
> world_state paths that DON'T EXIST (`indicators.bearish_trend`,
> `indicators.drawdown_from_high_7d`) and `validate_schema` was FAILing; fixed (paths
> repointed/dropped + schema consumer lists synced → `validate_schema` PASS), and
> `run_council` on the stored downtrend world_state then flipped MAINTAIN →
> STAND_ASIDE (2× STAND_ASIDE + 1× HALT, clear Condorcet). On the live restart the
> startup council voted STAND_ASIDE on the real downtrend → buys cancelled, the book
> is sells-only (the correct protective posture). FIXED this session: the stale-counter
> bug (`get_trajectory_context` floors at `paper_run_started_utc`;
> `melchior_blocked_cycles` no longer miscounts `THESIS_HOLDS`; `reset_paper_book.py`
> clears the down_walk anchors); and a SEPARATE engine bug found by the live restart
> (NOT the audit — a thoroughness miss, see §8) — the post-action GRID INTEGRITY guard
> (`engine.py` ~1611) tried to emergency-rebuild the council-mandated one-sided book
> (re-adding buys into the downtrend) and errored on a missing `spacing_pct`; now the
> guard leaves a PAUSE_LONGS/STAND_ASIDE/non-DEPLOY one-sided book alone and only
> rebuilds a genuine DEPLOY-stance degeneracy (with the effective spacing). ADDED: an
> off-schedule wake alert (ntfy, existing MAGI topic) on any council wake that is not
> the daily 20:00 ET floor or a manual run (startup/backstop/W1/W2). Only the DOWNTREND
> regime is validated so far; benign/ranging + RECONFIGURE are exercised by the live
> paper run. The 2026-06-25 block below is now historical (its SHUT-DOWN /
> NOT-validated / reset-INCOMPLETE claims are superseded).**
> **As of 2026-06-25 (END of session): the trading engine `magi.service` is SHUT
> DOWN by operator order; `magi-dashboard.service` IS running — waitress on :5000,
> public at `https://api.ethobs.uk`. SEQUENCE this session: the engine was
> briefly RESTARTED on paper (clean book reset → fresh 2.5%/5-level grid around
> ~$1.03 → one blind-review council cycle, decision MAINTAIN), then a real audit
> found the council grids into a confirmed XRP downtrend, and the operator ordered
> it SHUT DOWN. The `magi.service` systemd unit now exists and is INSTALLED but
> `disabled`/`inactive`. `reset_paper_book.py` (repo root, untracked) is the clean
> reset; it is INCOMPLETE — it does not clear the trajectory/exposure counters
> derived from `magi_decisions` (stale-counter bug, see `05`/`02`). The Casper
> propose 400 is FIXED and the Langfuse instrumentation was rebuilt — see
> `05_COUNCIL_REDESIGN.md` §7. **All three council personas were REWRITTEN
> blind-review-native (§7d in `05`) to fix the root cause — they had been stale
> arbiter-era — but the rewrite is NOT yet validated.** A full pre-restart audit ran
> (5 dimensions) and a serious chain of Claude's own failures occurred — both
> documented in §8 above and `05` §7d.** The engine is
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
> is the deliberate fix. ~~The controlled, SQLite-sourced, prompt-injected Journal
> recall layer is BUILT and wired into council_v2 (committed cebccb5, 2026-06-09)~~
> **FALSE AS OF THE REDESIGN — CORRECTED 2026-07-04:** that per-seat Journal was
> silently ORPHANED when the blind-review redesign (06-25/26) rebuilt `run_council`
> — `get_agent_recall` has had NO live caller since; the redesign's replacement
> memory (a shared 6-item recency ledger) carried raw equity `pnl_24h` (the
> banned-as-verdict metric) and NONE of the graded outcomes. The learning loop was
> effectively DEAD for the whole blind-review paper run and four audits missed it
> (each scoped to the money path; each trusted this very doc line). REPAIRED and
> EXTENDED 2026-07-04 (deployed): the live memory is the council ledger with
> factual 72h cost-vs-alternative outcomes (`magi/sync_ratio.py` — the v2
> cost-based grading), the condition-bucketed COUNCIL TRACK RECORD, and the
> reliability-weighted NOTEWORTHY PRECEDENTS (`magi/entry_plug.py`); every cycle's
> injected memory is flight-recorded in `memory_injections` and watched by MAGI-02
> predicate P7 so this class of silent death is falsifiable off-box, nightly.
> Deterministic, config-version-filtered, recomputed-from-history (no mutable
> state), NOT vendor memory — the original principles, now actually running.
> (2) Cadence is **gate-driven**, not a 4h clock — IMPLEMENTED 2026-06-09 (BU-2),
> REDESIGNED 2026-06-11 (Fix 4, after the wake-yield audit found 0/16
> gate-woken cycles produced a council-originated change):
> `scheduler.py` fires ONE daily clock-floor council call (`MAGI_DAILY_HOUR_EST = 20`,
> 20:00 EST end-of-day assessment, grid or no grid) plus a 25h max-silence
> backstop (`MAGI_MAX_SILENCE_HOURS`); every call in between comes only from the
> **W-series wake QUESTIONS** — **W1** (price left the band and stayed out:
> corrective recentre or not? fed by T2 detection, one wake per breach episode)
> and **W2** (the evidence under the standing stance changed — warehouse verdict
> shift held one bar, or exposure-cap engaged/released: re-judge the stance).
> All T-series triggers (T1–T16) are now CONTEXT-ONLY detectors — recorded and
> shown to the council, never waking it. 60-min throttle, 15-min dwell and
> non-trading suppression still apply. The startup cycle is gated too: a restart
> wakes the council only if the config fingerprint changed, an unconsumed W event
> is pending, or price is outside the grid band — otherwise quiet (kills the
> restart-spend). Rule: adding a T is cheap instrumentation; adding a W requires
> naming the council-only question it asks. (The daily hour is duplicated in
> `dashboard.py:_next_magi_eta` — change both.)
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
  no Letta blocks to provision and no vendor-side agent state. `agent_registry` no
  longer maps to Letta UUIDs and, as of 2026-06-25 (CS2), no longer backs the dashboard
  AGENT HEALTH chip's model labels either — those come from `magi/agents/seats.py:MODELS`
  (the table's `model` column had drifted stale: Balthasar `claude-sonnet-4-6` vs the
  live `claude-haiku-4-5`).
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
- Grid spacing clamps: `MIN_GRID_SPACING_PCT = 0.015`,
  `MAX_GRID_SPACING_PCT = 0.025` (1.5% to 2.5%). Set in `config.py`.
  The floor was raised from 0.3% on 2026-06-11: floor = 6×`MAKER_FEE`,
  so the round-trip fee (2 maker fills) never exceeds 1/3 of the gross
  spacing. Basis: a 9.5-year hourly backtest (2016-12 → 2026-06,
  tape/history.db, fresh $61.50 book per year, 0.25% maker both sides)
  — 0.75% spacing lost in 9 of 10 years because fees ate ~2/3 of gross;
  1.5–2.5% is the viable band. Mirrored in
  `orchestrator.py:HARD_RULES["min_grid_spacing_pct"]` — change both.
- **Exposure cap (down-walk streak, Fix 2 2026-06-11).** The grid's worst
  failure mode is recentering INTO a downtrend (each rebuild steps lower and
  buys the fall). `grid/engine.py:initialise_grid` tracks consecutive
  downward rebuilds in `system_state` (`down_walk_streak`, linked within
  `DOWN_WALK_LINK_HOURS = 48`); at `DOWN_WALK_CAP_STREAK = 3` the rebuild
  goes SELLS-ONLY (anchor forced to sell, buy arms suppressed) until a
  rebuild lands at a HIGHER centre, which resets the streak — self-releasing,
  no tunable release threshold. A drawdown "brake" was tested against the
  9.5y history and REJECTED (drawdown ≥6%-from-7d-high is 60% of all hours;
  post-streak returns are mean-reverting) — do not re-propose it. Dashboard
  chip: EXPOSURE CAP.
- **Council stance mandate (Fix 3, 2026-06-11).** The arbiter's synthesis
  RiskVote carries `stance ∈ {DEPLOY, HOLD, STAND_ASIDE}` — the council's
  capital mandate, translated deterministically in `enforce_hard_rules`
  (step 0-pre): DEPLOY → verdict pipeline unchanged; HOLD → rebuild blocked
  (no new capital); STAND_ASIDE → MAINTAIN + risk floored at PAUSE_LONGS
  (buys cancelled, sells keep working inventory off). The GRID_DEGENERATE
  rule is stance-gated (fires only under DEPLOY/none) — under STAND_ASIDE a
  one-sided book is the mandate, not damage, and the first DEPLOY vote is
  the exit (rule 6 restores the full grid). Standing stance + time-in-stance
  persist in `system_state` (`council_stance`, `council_stance_since`;
  NOT updated on council_error cycles — a crashed council is not a stance
  decision). world_state carries three stance inputs: `tape_verdict`
  (signals_1h green/yellow/red with age/stale — STALE while the tape
  collector is stood down; stale = missing evidence, never negative
  evidence), `exposure_cap`, `council_stance`. Stance is recorded per cycle
  (`debate_records.stance`) and graded at 72h maturity
  (`observer.backfill_stance_grades`, thresholds anchored to grid band
  width) → Langfuse scores `stance` / `stance_correct`.
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
  to `TAKER_FEE` here (taker wrongly priced the recurring round-trip at
  0.80%). **CORRECTED 2026-06-11:** the old claim here — "maker unlocks the
  fee-positive 0.75% grid (validated: ~3–6× more fills on live candles, all
  net-positive)" — was FALSE at the equity level. That "validation" counted
  per-fill fee-positivity, not equity outcomes; the 9.5-year hourly backtest
  showed the 0.75% grid loses in 9 of 10 years because fees consume ~2/3 of
  gross and trend cycling eats the remainder. Acceptability is now the
  fee-share floor ONLY: spacing ≥ 6×`MAKER_FEE` = 1.5% (fees ≤ 1/3 of gross
  per round trip), and the scorer's fill FORECAST no longer gates
  acceptability — it is informational evidence for Melchior's judgment
  (GoodCrypto-frame redesign; see `magi/spacing_evaluator.py` docstring).
  The one-time anchor IS a taker market order, but it is an amortized setup
  cost handled in `engine._execute_anchor`, deliberately outside the
  per-level fee-positivity model.
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
  **Scope is decided in ONE place (2026-06-11):** `grid/pnl.py:current_scope_cutoff()`
  + `fill_in_current_scope()` — shared by the outcome backfill, the orchestrator's
  world_state fill recency, the gate's fill-gap trigger, and readiness L3. Never
  inline the txid check in a new fill reader: an inlined live-only copy in
  `observer.py` wrote fake zeros into every paper cycle's outcome record (poisoning
  Journal recall + seat grading) until fixed 2026-06-11. **Live-flip rule:** blank
  `system_state['paper_run_started_utc']` when arming live — engine live gate 4
  REFUSES live mode while it is set (fails closed), because every scope-aware
  reader would otherwise stay in paper scope and live fills would be invisible.
  Langfuse outcome scores are delivered convergently (per-window
  `outcome_{w}_scores_pushed` receipts; the observer sweep retries until every
  score POST confirms 2xx) — a 429/outage delays the mirror, never loses it.
- **Dashboard auth** is app-side: a Flask signed-cookie session in
  `dashboard.py` (`/login`, `/logout`, `before_request` gate; password in
  `.env:DASHBOARD_PASSWORD`, `SECRET_KEY` in `.env`; 365-day cookie).
  Served by **waitress** (a real WSGI server, not Flask's dev `app.run()`) via
  `magi-dashboard.service` (ExecStart uses `.venv`). The cloudflared tunnel hits
  Flask:5000 directly (nginx is NOT in the public path). **Tunnel rebuilt 2026-06-25
  (see `05` §7):** now a locally-managed `eth-observer` tunnel
  (`e4d95b41…`) driven by on-disk `/etc/cloudflared/config.yml`, public at
  `https://api.ethobs.uk` (the old embedded-token tunnel `0a3c34dc…` had been
  deleted and its connector was dead). Token-authenticated API calls
  (`X-Magi-Token`) bypass the login gate for automation.
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
  `debate_records`. **Degraded-detection is ERA-AWARE as of 2026-06-25 (CS2).**
  The blind-review council does NOT write the arbiter-era SAFE_DEFAULTS sentinel
  (`conviction=0.0 AND crux LIKE '(no response)%'`) — a non-responding seat is
  simply absent and its `{seat}_r0_action` column reads NULL. So `_fetch_agent_health`
  classifies each row: a blind-review row (any seat has a non-NULL `{seat}_r0_action`)
  degrades a seat iff its OWN action is NULL while a peer responded; an arbiter-era
  row keeps the legacy SAFE_DEFAULTS fingerprint. Model labels shown on the chips
  come from `magi/agents/seats.py:MODELS` (the single source of truth), NOT the
  `agent_registry` table — that table held arbiter-era models (Balthasar
  `claude-sonnet-4-6`) and drifted stale when the redesign dropped Balthasar to
  `claude-haiku-4-5`. API: `/api/agent_health`.
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

### Session 2026-06-25 — a compounding failure cascade (Claude's, documented at operator order)

A pre-restart "audit" and the bring-up that followed went badly. The chain, owned
plainly so it is not repeated:

- **A status check was delivered as a "comprehensive audit."** Before arming the
  paper restart, the audit validated that the code *ran* and handled the blind-review
  era structurally, then declared **"no blockers, correctly built."** It never
  validated the actual VALUES flowing into the council, nor the QUALITY of a real
  decision against the design goal. This is the exact substitution §7 warns against —
  shipped anyway. The real defects (below) were all missed by it. *Lesson:* an audit
  that does not recompute the council's inputs against ground truth and measure a
  real decision against `>50% accuracy / fee-positive / survive` is a status check.
  Do not call it an audit.

- **A non-existent "critical bug" was fabricated, and the operator shut the system
  down over it.** Reviewing the first live cycle, `EMA200=1.56` (with price `1.03`)
  was declared "impossible / corrupt indicators" — by assuming it was a **200-hour**
  EMA and comparing it to the hourly range. It is a **200-DAY** EMA (`observer.py:159`
  computes EMA/ADX/BB from daily candles); ~1.5 is correct for XRP's real multi-month
  decline from ~$2.07. Independent recompute later matched it to the digit (EMA50
  exact, EMA200 within 0.7%). A working paper system was halted over an alarm that was
  itself the error. *Lesson:* verify a value's UNITS/timeframe and recompute it against
  ground truth BEFORE declaring it impossible. A false alarm is worse than a missed one.

- **The proposed "fix" was to bolt a hard rule the council could not override.** When
  the audit (finally, under operator pressure) found the council grids into a
  downtrend, the first instinct was a deterministic `PAUSE_LONGS` rule in
  `enforce_hard_rules` — exactly the **static rule creep / council bypass** forbidden
  by P1–P2 and the operator's standing mandate ("improve the council, never bypass
  it"). *Lesson:* a bad council decision is fixed INSIDE the council (seats /
  aggregation), never by a rule that strips its judgment.

- **Anchoring + tunneling.** Each step fixated on one thing (one indicator's plumbing;
  then "the personas are THE issue") to the exclusion of a systematic sweep, and only
  swept the whole layer when the operator demanded it. *Lesson:* on a "is this the
  only issue" question, enumerate and check every component before answering; do not
  present the first root cause found as the whole truth.

- **THE ACTUAL ROOT CAUSE (found on the real audit):** the council redesign rebuilt
  the machinery (schemas, propose→review→aggregate, anonymizer — all verified sound)
  but **left all three seat personas in their stale arbiter-era form** — wrong output
  schema (`position`/`verdict`/`stance`/`geometry_veto`), dead R1-synthesis sections,
  and protection logic that depends on **"Casper's regime read"** as an input a
  blind-review seat never sees. So the seats improvised onto the real action space and
  the protective seats (Casper by regime, Balthasar by capital-erosion) could not
  reliably fire — Casper stood alone and was outvoted into a confirmed downtrend (the
  #1 documented grid-bleed failure mode). Fixed by rewriting all three personas
  blind-review-native (single `action` over the shared space; no peer context; each
  reads the downtrend from world_state directly; only Melchior carries
  RECONFIGURE/geometry). **The rewrite is NOT yet validated** — the validation run was
  not completed. *Lesson:* a layer redesign is incomplete until the PERSONAS that
  drive it are rewritten for it; matching them is foundational, not "prompt tuning."

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
