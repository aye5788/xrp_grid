# Next Build Tasks

> **TOP OF QUEUE 2026-07-02 — PnL DECOMPOSITION + STAND_ASIDE WORK-OFF LADDER
> SHIPPED (uncommitted; operator-approved; engine restart required to load, ladder
> arms at the 2026-07-03T00:00 UTC daily wake via
> `system_state['workoff_armed_after_utc']`). See `01_CURRENT_STATE.md` 2026-07-02
> block for full detail. Remaining from this session:**
> - **Restart `magi.service` + `magi-dashboard.service`** to load the new code
>   BEFORE the 07-03 00:00 UTC daily wake (engine restart may fire a gated startup
>   council cycle — price is outside the old band — ~6 seat calls).
> - **Watch the first armed ladder cycle** (first observer tick after the 00:00 UTC
>   council cycle, if the stance is still STAND_ASIDE): expect [WORKOFF] log lines
>   seeding 5 sells ~2.5% apart above market; verify rung count, floor headroom,
>   and that a stance exit stops the top-up.
> - **72h stance grading now has teeth both ways:** with `alpha_vs_hold` live, a
>   wrong persistent STAND_ASIDE shows up as realized distribution into a rally
>   (negative alpha), not silent paper regret — fold into the next accuracy review.
> - **FIXED during the restart (same session): engine `NameError` crash.** The
>   2026-06-26 GRID INTEGRITY guard fix (`grid/engine.py` ~1637) calls
>   `get_system_state` but the module never imported it — latent because the guard
>   path only runs when the post-action book looks degenerate, and 2026-07-02 was
>   the first cycle ever with a fully EMPTY book. The 16:57 UTC startup council
>   cycle crashed `apply_magi_decision` on it (decision itself stood: STAND_ASIDE,
>   2x STAND_ASIDE + 1x MAINTAIN, clear Condorcet; grid/pause actions had already
>   no-opped). One-line fix: added `get_system_state` to the module-level
>   `from database import (...)`.
> - **NEW BUG (found live, NOT fixed — needs operator go): wake-notification ntfy
>   send crashes on emoji.** The off-schedule wake alert title starts with `ℹ️`,
>   which fails latin-1 header encoding in the HTTP POST
>   (`magi/notify.py`: `UnicodeEncodeError` at 16:57 UTC startup wake) — the
>   2026-06-27 notification feature has never actually delivered. Fix is to strip/
>   encode non-latin-1 characters for the ntfy title header (body is fine as UTF-8
>   data).
>
> **OPEN FOLLOW-UPS carried from 2026-06-28 (unchanged, below): tape_verdict
> restore-or-demote, grader-predicate mismatch, `Ranking` permutation guard.**

> **TOP OF QUEUE 2026-06-28 — AUDIT + 3 FIXES SHIPPED (committed this session).**
> A real audit (4 parallel finders + own verification) found & fixed:
> 1. **HIGH — replenishment council-bypass FIXED.** `scheduler.py`'s post-fill grid
>    replenishment re-armed a BUY on every sell fill checking only price-drift — never
>    `pause_longs`/stance/exposure-cap — so the council's STAND_ASIDE was silently
>    undone between cycles (re-arming longs into the downtrend; verified firing
>    06-26/06-27, cancelled by luck). Now gated on `pause_longs` OR
>    `down_walk_streak>=DOWN_WALK_CAP_STREAK` (buys) and `pause_shorts` (sells).
> 2. **MED — `roc_6h` nulled by gate_monitor FIXED.** A 2nd writer to `indicators`
>    passed an empty 6h list → overwrote poll_cycle's value with NULL hourly. Now
>    resamples 6h from the 1h bars (`_resample_6h_from_1h`); verified live.
> 3. **MED — freshness monitor ADDED.** `world_state_schema.alert_on_stale_inputs`,
>    edge-triggered, ALERT-ONLY (`warn`, magi_alerts `stale_council_input`); catches
>    silently null/stale council inputs that shape-only drift validation misses.
>
> **OPEN FOLLOW-UPS (not done):**
> - **tape_verdict dead** — `tape/history.db` absent on the box; restore from GCS
>   (`gs://xrp-grid-tape-backups-ayn88/history/history.db.gz`) or demote it from
>   Balthasar's "primary" stance input in schema/persona.
> - **Grader predicate mismatch** — seat grader (`database._grade_action_row`:
>   STAND_ASIDE correct iff endpoint drift<0) ≠ stance grader (5%-band down-break);
>   the "Matches the stance grader" comment is false. Observability only.
> - **`Ranking` schema** doesn't enforce a permutation (`order` can dup/omit a label
>   → silently distorts the Condorcet/Borda tally). Low prob, unbounded effect.

> **TOP OF QUEUE 2026-06-26 — PERSONA REWRITE VALIDATED; ENGINE RESTARTED ON PAPER.**
> The blind-review persona rewrite (item 1 of the 2026-06-25 block below) is now
> **VALIDATED** and `magi.service` is **RUNNING on paper again** (restarted
> 2026-06-26). Done this session, in order:
> 1. **Persona rewrite VALIDATED.** A pre-restart audit found the rewritten personas
>    referenced world_state paths that DON'T EXIST (`indicators.bearish_trend`,
>    `indicators.drawdown_from_high_7d` — the latter is TOP-LEVEL, not under
>    `indicators`) and `validate_schema` was FAILing (10 ERROR). Fixed (paths
>    repointed/dropped, schema consumer lists synced) → `validate_schema` PASS. Then
>    `run_council` on `cyc_1782417183`'s stored downtrend `world_state` flipped
>    MAINTAIN → **STAND_ASIDE** (2× STAND_ASIDE + 1× HALT, clear Condorcet). Only the
>    DOWNTREND regime is validated; benign/ranging + RECONFIGURE are exercised by the
>    live paper run.
> 2. **Stale-counter data bug FIXED.** `get_trajectory_context` floors the lookback at
>    `paper_run_started_utc`; `melchior_blocked_cycles` no longer miscounts
>    `THESIS_HOLDS`; `reset_paper_book.py` clears `down_walk_last_centre`/`_ts`.
> 3. **Engine GRID INTEGRITY guard FIXED** (found by the live restart, NOT the audit —
>    a thoroughness miss, see `CLAUDE.md` §8). On a STAND_ASIDE the engine cancelled
>    buys (correct) but the post-action guard (`engine.py` ~1611) tried to
>    "emergency-rebuild" the one-sided book — which would re-add buys into the downtrend
>    AND errored on a missing `spacing_pct`. Now the guard leaves a council-mandated
>    one-sided book (PAUSE_LONGS / STAND_ASIDE / non-DEPLOY stance) alone; a genuine
>    DEPLOY-stance degeneracy rebuilds with the effective spacing.
> 4. **Off-schedule wake alert ADDED.** `run_magi_cycle` pages the operator via ntfy
>    (existing MAGI topic) on any wake that is NOT the daily 20:00 ET floor or a manual
>    run (startup, 25h backstop, W1/W2). Priority 3 (buzz; bump to 5 for DND bypass).
> 5. **Paper restart done.** Clean reset → fresh 2.5%/5-level grid ~$1.016 → startup
>    council STAND_ASIDE → sells-only protective book. The stance is re-judged at the
>    next daily floor (and W1/backstop); it stays sells-only until the council votes
>    DEPLOY on a recovered regime.
>
> **STILL OPEN (hygiene / follow-ups):** delete dead arbiter modules
> (`balthasar_claude.py`/`melchior_deepseek.py`/`casper_gemini.py` + `RegimeVote`/
> `GridVote`/`RiskVote`); the `engine.py:1328` "No grid state → initialise fresh"
> fallback also calls `initialise_grid()` with no spacing (fails safe — logs + builds
> nothing — but should route through geometry); ONE_GRID invariant (`engine.py:750`)
> detects-but-does-not-enforce; W2's off-schedule re-judge is DARK while the tape
> collector is stood down (its verdict arm), so the STAND_ASIDE EXIT is daily-floor-
> driven until the tape returns or a price/indicator W2 arm is added.
>
> The 2026-06-25 block below is the prior state — its items 1–2 are now DONE.

> **TOP OF QUEUE 2026-06-25 (end of session) — council-layer open items, in order.
> Engine SHUT DOWN by operator order; do NOT restart until at least (1) and (2) are
> done and the operator directs it. Full context: `05_COUNCIL_REDESIGN.md` §7d,
> `01_CURRENT_STATE.md` Session 2026-06-25, failure log in `CLAUDE.md` §8.**
> 1. **VALIDATE the persona rewrite.** All three personas were rewritten
>    blind-review-native to stop the council gridding into a downtrend, but the
>    validation run was STOPPED. Re-run `run_council` on `cyc_1782417183`'s stored
>    `world_state` (or fresh) and confirm the decision flips from MAINTAIN to
>    protection (STAND_ASIDE/PAUSE_LONGS). Persona edits can fail to move haiku/
>    deepseek/gemini behavior — until this passes, the fix is unproven. THIS GATES
>    any restart.
> 2. **Fix the stale-counter data bug.** `database.get_trajectory_context()` derives
>    `regime_consecutive`/`cycles_since_structural_change`/`melchior_blocked_cycles`
>    from the last 5 `magi_decisions` rows; `reset_paper_book.py` clears none of them
>    (nor `down_walk_last_centre`/`down_walk_last_ts`). Floor the lookback at
>    `paper_run_started_utc` (and clear the down_walk anchors in the reset). Also fix
>    `melchior_blocked_cycles` (it string-compares `THESIS_HOLDS` to `'MAINTAIN'`).
> 3. **Delete dead arbiter-era code:** `magi/agents/balthasar_claude.py`,
>    `melchior_deepseek.py`, `casper_gemini.py`, and the `RegimeVote`/`GridVote`/
>    `RiskVote` classes in `schemas.py` — imported by nothing live.
> 4. **ONE_GRID invariant** (`engine.py:750`) detects-but-does-not-enforce — make it
>    prevent a second grid, not just log.
> 5. Strategy reality: the audit established the grid is net-NEGATIVE in trends (live
>    −$10.27 / −15% equity; ~0 alpha vs hold at 2.5% in this downtrend). The persona
>    rewrite is meant to make the COUNCIL stand aside in such regimes — confirm via (1).

> **CURRENCY NOTE 2026-06-25 — the trading engine is currently SHUT DOWN (paper
> hold), so the "watching" direction below is paused. This session reconnected the
> dashboard, rebuilt the Langfuse instrumentation, and fixed the Casper propose 400 —
> none of which ran the engine. See `05_COUNCIL_REDESIGN.md` §7 and "Session
> 2026-06-25" in `01_CURRENT_STATE.md`. The blind-review council (`05`) supersedes the
> arbiter-era council items below. The next real build decision is whether/when to
> bring `magi.service` back up (out of scope until the operator directs it).**

> **DIRECTION 2026-06-12 — RESTARTED ON PAPER; THE QUEUE IS NOW WATCHING, NOT
> BUILDING.** F5 ran (PASS on its pre-committed criteria, then DEMOTED by the
> operator to skeleton-floor evidence — the replay cannot model the judgment
> layer; the paper run IS the acceptance test). The reactivation audit's 4
> blockers + 3 degraded items are FIXED and the restart checklist below was
> executed in full (all three services up; the startup council cycle fired on
> the config-fingerprint change as disclosed; first cycle: stance DEPLOY,
> THESIS_HOLDS→MAINTAIN, no overrides). Full record: `01_CURRENT_STATE.md`
> 2026-06-12 banner. **Live queue now = the PAPER-RUN WATCH ITEMS below + the
> day-14 `matches_backtest` check.** Remaining build items are hygiene-only
> (01 banner "Still open" list). The next *decision* point is the operator's,
> on evidence: stance grades (first matures ~2026-06-15 evening), seat accuracy
> vs the 50% line, fee_share_7d ≤ 0.33, net_harvest_7d, readiness gates.
>
> The 2026-06-11 direction below is retained as the record of what was
> specified; its F5 task and restart checklist are DONE.

> **DIRECTION 2026-06-11 — MAGI SHUT DOWN; FIVE-FIX REBUILD: Fixes 1–4 BUILT,
> Fix 5 IS THE QUEUE.** `magi.service` stopped + disabled by operator order after
> the audit found six accumulated failures (full record: `01_CURRENT_STATE.md`
> 2026-06-11 banner). The only live build item before the operator decides a
> restart:
>
> ### F5 — OFFLINE ACCEPTANCE TEST (the gate to any restart)
> Replay 2025→2026 from `tape/history.db` under `optimize/` with the REBUILT
> configuration (1.5–2.5% spacing band, floor-only acceptability, exposure cap
> streak-3/48h, stance semantics where simulable) against the OLD configuration.
> **Pre-committed pass criteria (set before running, never fitted after):** the
> rebuilt config must end with (a) more money and (b) a smaller worst drawdown
> than the old config over the same window. Log the run as a Langfuse dataset
> run so the comparison is auditable. Report PASS/FAIL to the operator —
> **the operator decides restart; a PASS is not a self-cleared go.**
>
> ### RESTART CHECKLIST (when the operator orders it)
> - Re-enable + start `magi.service` (startup gate is now smart: a restart wakes
>   the council ONLY if config changed / a W event is pending / price left the
>   band — disclose the cycle if one fires).
> - **Bring the tape collector back up** (`tape-collector.service`, stood down
>   2026-06-09) — without it `tape_verdict` stays stale: W2's verdict half is
>   silent and the council's stance evidence is degraded (the stale flag keeps
>   this honest, but the stance system is designed to run with a live verdict).
> - Restart `magi-dashboard.service` to pick up the EXPOSURE CAP chip + W-series
>   gate panel markers.
> - Expected cadence: ~1–3 council calls/day (daily 20:00 EST floor + W wakes)
>   vs the old ~11/day. Watch the new Langfuse scores: fee_share_7d (healthy
>   ≤0.33), net_harvest_7d, stance/stance_correct (graded from 72h),
>   wakes_per_day, cap_buy_fills (must be 0 during cap episodes).
> - Day 14: `matches_backtest` check — live paper economics vs the 9.5y backtest
>   expectation for the same config.
>
> The PAPER-RUN WATCH ITEMS below predate the shutdown — still the right
> questions for the NEXT run, now answerable with the new scores.

> **DIRECTION 2026-06-06 — decision layer = HAND-ROLLED orchestrator; seats proven
> standalone, not wired.** The three council seats (Casper `gemini-2.5-flash`, Balthasar
> `claude-sonnet-4-6`, Melchior `deepseek-v4-pro`) are each proven standalone via probes
> through `schema_for_tool`; the orchestrator that assembles them (direct vendor-SDK
> calls + owned SQLite state, NOT CrewAI / NOT an ADK framework) is the NEXT BUILD.
> **Historical (2026-05-31):** an ADK `council.py` agent layer was built (stateless,
> Melchior emits a verdict) — it is unchanged and superseded. See `01_CURRENT_STATE.md`
> STATE LEDGER + "Session 2026-05-31 (later) — ADK migration".
>
> **The live top priority is the "Post-migration work queue" immediately below.**
> The "Migration work queue (added 2026-05-29)" further down is now LARGELY DONE and
> PARTLY SUPERSEDED — it was written for a vendor-*stateful* Agent-Studio/Memory-Bank
> rebuild; the build went **stateless ADK** instead (self_model dropped; recall to be
> SQLite-sourced). Its M1–M3 per-agent Agent-Studio/Managed-Agents specs and M5's
> Letta-wrapper port are obsolete as written; the schema/persona/eval intent was
> executed. The older Phase-5 / M-/P-series items remain valid where they touch the
> engine/gate/dashboard and stay GATED on a live restart. Do not resume a historical
> item without operator direction.

## Post-migration work queue (the live top priority)

### ⚑ PAPER-RUN WATCH ITEMS (added 2026-06-10 — monitoring, not build tasks)

> Day-1 instrumentation is in place (see `01` Session 2026-06-10: Langfuse
> outcome/reiteration/seat-accuracy scores + trigger tags; T2 episode guard; T16
> drawdown-rung trigger; trimmed dashboard with Council Log + 24h call counter).
> What the paper run should now ANSWER, in rough priority order:
>
> - **Reiteration rate on gate wakes** (`council_changed` by `trigger:*` tag in the
>   Langfuse "MAGI Council" dashboard). 9 of the first 10 scored cycles reiterated,
>   including all 4 gate wakes. If gate-wake bars stay ~0 as data accumulates,
>   either the triggers are too loose (waking on questions the council was always
>   going to answer the same way) or the seats are anchoring despite statelessness.
>   Distinguish via `conviction_shift` (re-reading the data shows up as conviction
>   movement even when positions hold) and by whether reiterated MAINTAINs precede
>   bad `pnl_6h`.
> - **Melchior THESIS_HOLDS while `fillable=False`** — seen on day 1 (the 06:02
>   cycle needed `[GRID_DEGENERATE]` to force the RECENTRE the economics already
>   implied). If the hard-rule layer keeps doing Melchior's job, that's a persona
>   gap to fix, not a rule to celebrate.
> - **Per-seat 72h accuracy** (`{seat}_correct` scores, first land ~2026-06-12) —
>   the >50% directional-accuracy goal, now finally measured per seat on the live
>   paper tape rather than offline.
> - **Daily call count** (dashboard header counter / `trigger_class` widget) —
>   steady state should be 1 scheduled + episodic gate wakes. Day 1 was 8 (1
>   organic scheduled, 3 T2-bug now fixed, 3 dev-restart startups, 1 organic T16).
> - **Shadow sim** still runs with known-broken state under the removed panel; an
>   operator call on reset-vs-stand-down is pending.

### ⚑ PAPER BRING-UP READINESS — ✅ ALL DONE 2026-06-09 (later still); `magi.service` IS RUNNING ON PAPER.

> **EXECUTED + VERIFIED 2026-06-09 (later still).** BU-1 (config_validator removed from
> `run_magi_cycle` + archived), BU-2 (cadence rewired gate-primary: one daily clock-floor
> call `MAGI_DAILY_HOUR_EST=20` + `MAGI_MAX_SILENCE_HOURS=25` backstop; `MAGI_HOURS_EST`
> deleted; the gate-wake path unchanged), BU-3 (every live-path Letta call site deleted;
> `memory_lifecycle`/`costs`/`config_validator` → `archive/letta_decoupling_2026-06-09/`;
> dashboard gutted of LETTA AGENTS / Costs / EVAL HISTORY; `agent_registry` Letta UUIDs
> blanked + stale models corrected to the rebuild lineup; `LETTA_API_KEY` commented out).
> The done-when below was met, a fresh paper book was reset (stale live-era orders
> cancelled; paper inventory rebased to real balances), a **paper-scoped P&L** was added
> (`get_pnl_snapshot(paper=True)` + "Paper P&L" tile) so the run is measurable, and
> `magi.service` was **started on paper** (21:04 UTC) — startup verified clean: zero
> alerts, zero Letta traffic, scorer-built grid, full council cycle completed. Full
> record: `01_CURRENT_STATE.md` Session 2026-06-09 (later still). Code changes are in
> the working tree, UNCOMMITTED. The block below is preserved as the audit record.

**Context.** The operator is preparing to bring MAGI up **ON PAPER** (no real Kraken
capital at risk). In prep, this session (2026-06-09, later) (1) **stood down the tape
collector** and **reverted `magi-dashboard.service` from the tape monitor back to the MAGI
dashboard**; (2) **disarmed the live toggle** — `.env` now has `MAGI_LIVE_CONFIRM=NO` and
the `CONFIRM_LIVE` gate file was renamed to `CONFIRM_LIVE.disarmed.20260609`, so
`scheduler.py` will construct the engine in PAPER mode (the selector is
`_LIVE = os.environ.get("MAGI_LIVE_CONFIRM")=="YES"; engine = GridEngine(paper=not _LIVE)`,
scheduler.py:79-80); and (3) ran a **read-only audit of the live paper-bring-up import
chain** to find Letta-era / pre-Stage-3 assumptions that break or MISbehave on a paper
start. `magi.service` is still **stopped + disabled** — the tasks below gate the start.
Full audit record: `01_CURRENT_STATE.md` Session 2026-06-09 (later).

**The live chain (anchor — what a paper start actually runs).** `magi.service` →
`python3 -m scheduler` → the scheduler loop calls `observer.poll_cycle()` (data collection)
+ `magi.orchestrator.run_cycle()` (decisions), and `run_cycle` calls
`magi/council_v2.py:run_council` (the Stage-3 arbiter, **NOT** the old ADK `council.py`).
Verified: **nothing in the live chain imports `magi.council` (council.py) at all**, even
transitively — so the historically-flagged council.py import-time Letta client (old item
M5a) is unreachable and harmless. The remaining Letta exposure comes from OTHER live-chain
modules, and it is **real** because the box still has `LETTA_API_KEY` set in `.env`,
`letta_client` 1.11.0 installed, AND `agent_registry` still holding the three dead Letta
agent UUIDs (casper/melchior/balthasar) — so every stale Letta call site actually reaches
Letta Cloud rather than no-op'ing on a missing key. Candle data on a paper start comes from
MAGI's own path (`observer.poll_cycle` pulls Kraken OHLC via REST and writes `observer.db`),
independent of the now-stopped tape collector; the newest candle is currently frozen at
`2026-05-28T18:00` and the first poll backfills the gap.

**None of these hard-crash the scheduler** (every Letta touch in the live path is wrapped
non-fatal), but several MISbehave on a paper start. Ordered by severity:

- **BU-1 [HIGHEST — it pages the operator]. Unwire/replace the Letta `config_validator`
  from `run_magi_cycle`.** `magi/config_validator.py:alert_on_config_drift()` is called
  **twice per MAGI cycle** (scheduler.py:322 pre-cycle, scheduler.py:385 post-cycle). It
  compares "live Letta agent `model_settings`" against `provision_agents.AGENT_CONFIG`.
  Because `agent_registry` still maps the three agents to real Letta UUIDs, it makes a
  **live `client.agents.retrieve()` call per agent** (config_validator.py:65); the agents
  are gone from Letta, so each returns a `letta_error` (or a stale-state diff) → "not clean"
  → `insert_alert(severity="critical", category="config_drift")` per agent
  (config_validator.py:177-182). A `critical` alert fires `magi/notify.py:send_ntfy` →
  **phone push** (deduped to once/hour/agent). Net on a paper start: the operator is paged
  with ~3 false "config drift" criticals on the first cycle and forever after (hourly), and
  the system makes live Letta retrieves 2×/cycle. This validator checks a Letta-shaped world
  that no longer exists. **FIX:** remove it from `run_magi_cycle`, or repoint it at the new
  stateless seat config (model handles are now constants in the seat-callers, not Letta
  `model_settings`).

- **BU-2 [cadence — economics, not a crash]. Rewire the council firing cadence from
  clock-primary to gate-primary.** As-built, `scheduler.py` fires the **paid** council on a
  fixed wall-clock schedule: `MAGI_HOURS_EST = [0,4,8,12,16,20]` (scheduler.py:37; the inline
  comment literally reads "~$13/mo to fit $20 Letta plan" — dead Letta pricing),
  `should_run_magi` returns True at those hours (scheduler.py:508), and the main loop fires
  `run_magi_cycle('scheduled')` **unconditionally** at each (scheduler.py:1046-1048), PLUS a
  forced cycle on every startup (scheduler.py:955-956; the 30-min debounce won't catch a
  12-day-old last cycle). The always-on gate (`magi/gate_monitor.py`, started in-process at
  scheduler.py:842) only ADDS off-schedule wakes in the `else` branch — it never gates the
  scheduled fires. This is the **inverse** of the rebuild design (CLAUDE.md STATUS + item 8
  below): the gate is supposed to decide WHETHER the paid council wakes at all (floor ≈1/day,
  ceiling = breach frequency), with the clock only a backstop. As-is, a paper run fires the
  six-vendor-call council **~6×/day on the clock** (plus startup, plus gate wakes) against
  `LLM_MONTHLY_BUDGET_USD=5.00` and thin remaining vendor credits — it over-fires. The gate's
  DETECTION code itself is sound and reusable: `magi/gate.py` triggers T1–T15 are current
  market/book conditions with **zero Letta references**, last touched pre-rebuild (2026-05-24)
  but Letta-clean. The fix is the WIRING **relationship** in `scheduler.py` — make the clock a
  backstop floor and let accumulated `magi_gate_events` drive whether/when the council fires —
  plus retune `WAKE_MIN_INTERVAL_MIN=60` (scheduler.py:49) off its dead-Letta-cost rationale.
  This subsumes/concretizes item 0 P1 ("council cadence a function of trading state") and
  item 8 ("cost is gate-calibration, not cadence constants").

- **BU-3 [live Letta traffic / Sentry noise — non-fatal cleanup]. Retire the remaining
  live-path Letta call sites.** All wrapped (won't break a cycle), but each reaches Letta
  Cloud on the paper path for no benefit, and several log at ERROR → Sentry issues (ADAM):
  - `sweep_letta_steps_for_failures()` (scheduler.py def 391, called every 30 min from the
    main loop, scheduler.py:1012) polls `https://api.letta.com/v1/runs` for each agent that
    still has a `letta_agent_id` — i.e. all three — **every 30 minutes**.
  - `observer._record_outcome_to_block()` (observer.py:429, reached via
    `poll_cycle → backfill_outcomes`, observer.py:551) writes the 6h outcome to a Letta
    `recent_outcomes` block that nothing in the stateless council reads — a live Letta write
    ~6h into a run. It returns gracefully if Letta is unreachable, but with the key present
    it actually writes.
  - `magi/memory_lifecycle.py` constructs a Letta client at **module import** (line 155) and
    is imported+called by the rotation hook (`maybe_rotate`, scheduler.py:285-286) on each
    successful cycle; real Letta block ops only fire at the 30-cycle rotation boundary,
    erroring on the gone agents.
  - The restored MAGI dashboard's "LETTA AGENTS" census
    (`dashboard.py:_fetch_letta_agent_census`, ~line 2986) builds a Letta client and counts
    agents on every render (60s cache) — it is making live Letta calls **right now** on the
    running `magi-dashboard.service`.
  - **Single lever for BU-1 + BU-3:** the common root is `LETTA_API_KEY` present +
    `letta_client` installed + `agent_registry.letta_agent_id` still populated. **Nulling the
    registry's `letta_agent_id` values and/or removing `LETTA_API_KEY` neutralizes
    config_validator, the sweep, the rotation client, and the dashboard census in one move**
    (the dead code paths can then be deleted at leisure). This is the concrete, now-confirmed
    execution of old **M5/M5a**.

**Already verified CLEAN by the same audit (do NOT re-derive — these are not blockers):**
- **Action vocabulary (`RECENTRE`) is current, not stale.** `RECENTRE` IS the live engine
  grid_action: the orchestrator maps Melchior's `RECONFIGURE` verdict → `RECENTRE`
  (orchestrator.py:897) and the engine consumes `grid_action in ('RECENTRE','TIGHTEN','WIDEN')`
  (engine.py:1191,1238). The only stale `melchior_action=='RECENTRE'` readers are in
  **non-live** one-off / analysis scripts (`extract_test_cases.py`, `analysis/*`); the
  restored dashboard does NOT read this vocabulary. This **retires the live-path worry in
  item 5** (the display/analysis readers there are out-of-path).
- **`emit_human_alert`** is defined only in council.py and is **no longer imported by the
  orchestrator** — the old "orchestrator pulls council.py's Letta client via
  emit_human_alert" path is closed.
- **council.py itself** was migrated to ADK (it imports `google.adk`, not Letta) and is
  imported by nothing in the live chain — stale-but-present, harmless.

**Done when:** `magi.service` can run a paper cycle with (a) no false `config_drift`
criticals / no operator pages, (b) the paid council firing on a gate-driven cadence (clock
as a backstop only), and (c) no live Letta Cloud calls on the paper path. The disarm is
already in place, so the toggle itself is not a blocker — these three are.

**STATUS 2026-06-06.**
- **DONE:** the Melchior swap (gpt-4o → DeepSeek V4-pro, proven standalone); **all three
  seats proven standalone** via read-only probes through `schema_for_tool` (Casper
  `gemini-2.5-flash`, Balthasar `claude-sonnet-4-6`, Melchior `deepseek-v4-pro`);
  `schema_for_tool` hardened (the native-Gemini `additionalProperties` 400 is
  structurally dead); Balthasar persona corrected to own downtrend/capital-erosion risk.
- **CLOSED LOOSE ENDS (2026-06-06 later):** (a) **`magi/agents/schema_tools.py` committed**
  this session in the end-of-session code commit (was UNTRACKED; xrp_grid commit `86aa107`).
  (b) **`drawdown_from_high_7d` WIRED into `build_world_state`** — computed from 168×1h bars
  on a running-peak basis (clamps ≤0, signed percent, `None` fallback) with a matching
  `FIELDS` entry (`consumers: ["balthasar"]`); `validate_schema` 0/0 PASS at 102 paths. This
  **CLOSES the HARD PREREQUISITE** persona/world_state inconsistency (item 0★ DONE). Drawdown
  stays a judgment input — no threshold/gate keys off it.
**STATUS — Stage 1, Stage 2 (2026-06-07) and Stage 3 (2026-06-08) are ALL DONE; next is Stage 4.**
Stage 3 (the arbiter orchestrator, `magi/council_v2.py`) is built, wired into `run_cycle`,
**integration-verified through one real `run_cycle`**, and **committed as `c47e36a`** (not
pushed — operator pushes code manually). Details below + `01_CURRENT_STATE.md` Session 2026-06-08.
This session split the remaining work into stages. **Stage 1 (prereqs + the accuracy fix)
is DONE:** (a) `debate_records` gained `trace_id` (col 51) and `unrealized_pnl_6h`/
`unrealized_pnl_24h` (cols 52/53) — idempotent migration + `CREATE TABLE`; (b)
`PERSONA_DIR`/`load_persona` in `world_state_schema.py` repointed off the dead
`magi/prompts/*_prompt.txt` onto the live `magi/agents/personas/*.md`, and the validator
now downgrades bare prose snake_case tokens to NOTEs (so `validate_schema` is a WARN-only
PASS again — the 12 Melchior persona-coverage WARNs are KNOWN AND DELIBERATELY LEFT;
they are prose-citation gaps, not data starvation, since all 12 paths are fed to Melchior);
(c) **item 3 below (per-role `get_agent_accuracy`) is DONE** — replaced the single
`fills>0 AND pnl>=0` predicate with per-role scoring (Casper regime-realized over 72h via
the new shared `grid/forward_sim.py`; Melchior verdict-conditional; Balthasar total-PnL
with reality- and counterfactual-graded calls kept SEPARATE, never summed), which also
closes the hard dependency for the future Journal. **Stage 2 (seat-callers + renderer) is
DONE:** two new standalone callers `magi/agents/casper_gemini.py` (native-Gemini ADK,
`output_schema=RegimeVote`, no `schema_for_tool`) and `magi/agents/balthasar_claude.py`
(raw Anthropic forced-tool via `schema_for_tool(RiskVote)`, validates the live `RiskVote`)
join the existing `melchior_deepseek.py`; a shared `magi/agents/world_state_render.py`
(pretty JSON) now feeds all three, and all three are proven standalone with real vendor
calls (Casper billed `gemini-2.5-flash`, Balthasar `claude-sonnet-4-6`, Melchior
`deepseek-v4-pro`). The three seats are intentionally **not** symmetric in transport
(Casper native-Gemini/ADK; Balthasar+Melchior raw Anthropic-shape forced-tool) — three-vendor
judgment diversity is the principle, transport symmetry is not a goal. Full detail:
`01_CURRENT_STATE.md` Session 2026-06-07.

- **Stage 3 — the arbiter orchestrator (implements the 2026-06-04 redesign). DONE 2026-06-08,
  committed `c47e36a`.** The spec it implemented is preserved below for reference; status note
  + the four settled deferrals are at the end of this block.
  Assemble the three proven seat-callers + owned SQLite state + per-cycle world_state
  assembly. Direct vendor-SDK calls, NOT CrewAI, NOT an ADK framework. The 2026-05-31 ADK
  `council.py` is unchanged and superseded by this. The redesign it implements (see
  `04_EXPERIMENTAL_IDEAS.md` Session 2026-06-04): **sequential convene
  Casper → Melchior → Balthasar** (regime gates everything; Melchior prompted
  *orthogonally* — "given regime X, what do the economics say"); **a rebuttal round that
  always runs** when the gate convenes the council (each agent sees the round-1 transcript
  and may PASS only with a stated reason, never silent assent); **Balthasar as the
  synthesizing arbiter** who makes the final call through the risk lens; and **`should_run_r1`
  collapsed INTO the gate** — if the gate judged the moment worth convening, convening
  *means* debating, so there is no separate re-gate on whether they argue.

  **Confirmed Stage-3 carry-in requirements (do not lose these):**
  (a) **The orchestrator must call `load_dotenv()` once at startup before invoking any
  seat.** The seat-callers are asymmetric on env loading — `casper_gemini.py` self-loads
  `.env` at import, but `balthasar_claude.py` and `melchior_deepseek.py` read `os.environ`
  directly and assume the caller already loaded it. A single startup `load_dotenv()`
  resolves this for all three.
  (b) **The orchestrator owns all tracing.** It wraps each cycle in
  `magi/agents/tracing.py:trace_cycle(cycle_id)` and each seat call in
  `trace_seat(seat, model, vendor, request_payload)` (manual per-seat `model`/`vendor`
  attribution — required so the DeepSeek-via-Anthropic-compat Melchior seat isn't
  mislabeled Claude), and stamps `current_trace_id()` into the new
  `debate_records.trace_id` column when it writes the cycle's row. The seat-callers
  themselves contain NO tracing by design.
  (c) **Open design degrees-of-freedom to settle at the START of Stage 3:** the exact
  rebuttal-round choreography, and how each predecessor's output (and prior context)
  is threaded into Melchior's and Balthasar's prompts. These are the orchestrator's main
  design decisions and were intentionally left open.

  **After Stage 3:** Stage 4 = the `enforce_hard_rules` determinism-shrink (hand survival-floor
  authority back to the council per the 2026-06-04 "determinism-vs-vision rebalance"), and the
  per-agent **Journal** (the controlled SQLite-sourced recall layer, item 4 below) — both
  remain to be built after the orchestrator exists.

  **Stage-3 DONE 2026-06-08 — `magi/council_v2.py` (`run_council`) wired into `run_cycle`,
  INTEGRATION-VERIFIED through one real `run_cycle`, committed `c47e36a`.** Sequential six-call
  choreography (Casper → Melchior → Balthasar openings, Casper+Melchior rebuttal vs a frozen
  snapshot, Balthasar synthesis), per-seat Langfuse tracing, fail-safe safe-hold, standalone
  `__main__` runner. Balthasar got a 5-min ephemeral prompt-cache breakpoint; the two
  `debate_records.*_r1_position` columns were added. **Also: persona-load failure is now a hard
  stand-down** — `run_council` loads Melchior's persona before any vendor call and stands the
  council down (safe-hold cons, `council_error="persona_load_failed:melchior:…"`) rather than
  letting his seat-caller silently fall back to a thin default persona; Casper/Balthasar resolve
  their full `.md` personas via their own seat fail-safe. The integration `run_cycle` exercised
  the **hard-rule override path for real** (council held THESIS_HOLDS, but `[GRID_DEGENERATE]`
  forced RECENTRE and `[GEOMETRY_INJECTED_FROM_SCORER]` supplied scorer rank-1 geometry →
  `geometry_source=scorer_fallback`), swept a **100-event gate backlog** from the offline period,
  and confirmed Balthasar cache write→read (12,960 tokens) + DeepSeek auto-cache — with **no
  Kraken order** (decision-only path). The `database.py` commit was **patch-level** (only the
  Stage-3 `trace_id` + `*_r1_position` schema); a **pre-existing, unwired accuracy-scoring layer**
  also in `database.py` (`_score_casper/_melchior/_balthasar`, `_decision_bar_index`,
  `unrealized_pnl_{6h,24h}` + `update_debate_outcomes`) was **left out** of the commit and awaits
  its own commit + wiring. See `01_CURRENT_STATE.md` Session 2026-06-08 for the full record (incl.
  the five resolved contract subtleties). **NEXT = Stage 4** (`enforce_hard_rules`
  determinism-shrink + the per-agent SQLite Journal, item 4 below). **Still-open pre-live
  carry-forwards:** per-seat `world_state` trimming (deferred below, needs Langfuse per-seat token
  data); the accuracy-scoring layer's own commit + wiring; dashboard-auth posture before the MAGI
  dashboard is re-served live (currently app-side Flask cookie auth — `magi-dashboard.service` now
  serves the tape monitor, and nginx `auth_basic` was removed); and the genuine engine-vs-council
  ("two-engine") divergence cross-check (items near the §609 H-series below). **The four
  notes/deferrals below were settled with that build:**

  - **DEFERRED — Gemini/Casper context caching stays OFF (revisit trigger, not a TODO).** Unlike
    Anthropic/DeepSeek (per-call breakpoints, charged only when reused), **Gemini context
    caching bills by storage-time** (per-hour the cache is held alive), so it is a **net loss at
    our ~1-convene/day cadence** — the cache would expire unused between cycles, or we'd pay
    rent on an idle cache. Casper therefore uses NO caching. **Revisit only if** convene
    frequency rises materially (e.g. breach-heavy regimes pushing many convenes/hour); decide
    from the real per-seat token counts Langfuse will show, not a priori.

  - **DEFERRED — per-seat `world_state` trimming, its own scoped task AFTER Langfuse data.** All
    three seats currently receive the FULL `world_state` (rendered identically via
    `world_state_render.render_world_state`). Trimming each seat to only the fields its persona
    consumes (the `world_state_schema.py` `consumers` tags exist for exactly this) would cut
    input tokens, but **must not be a mechanical tag-filter** — a seat that loses a field it was
    implicitly reading degrades silently. Do this **after** Langfuse shows real per-seat token
    counts (so the saving is measured, not assumed), and **with the personas open** (confirm each
    trimmed field truly isn't load-bearing for that seat's read). Until then, full world_state to
    all three is the safe default.

  - **TOOL (on-demand, not live) — cache diagnostics in the standalone runner.** `python -m
    magi.council_v2 --cache-debug` prints the per-seat cached-token breakdown
    (`cache_creation`/`cache_read`) returned by each call, so caching is **observed, not
    assumed**, without a service or live cycle. This is a debugging affordance only — it is NOT
    wired into the live path. Deeper byte-level cache inspection can be had by adding the
    Anthropic **prompt-caching beta header** to the Balthasar caller, but that stays **off by
    default** (the GA caching path needs no beta header; the header is a debugging escalation,
    not the normal path).

  - **GUARD — deterministic/sorted tool-schema serialization (so a phantom `tools_changed` can't
    silently cost the cache).** Anthropic/DeepSeek cache the prefix = tools + system + the
    world_state block. If `schema_for_tool(RiskVote)` (the Balthasar/Melchior tool `input_schema`)
    serialized non-deterministically between two calls in a cycle, the `tools` segment would
    differ, **invalidating the cached prefix and silently losing the saving** with no visible
    error. `--cache-debug` asserts two serializations of the tool schema are **byte-identical
    under `sort_keys=True`**. Keep `schema_for_tool` output order-stable; if a future change makes
    it non-deterministic, the cache silently degrades — this check is the canary.

- **Stage 4 — `enforce_hard_rules` DETERMINISM-SHRINK. SUBSTANTIALLY DONE 2026-06-09; only the
  skew-categorization question is open.** Three items shipped + committed (local only — operator
  pushes code manually); the per-agent **Journal** (item 4 below) is the remaining Stage-4 build,
  and it needs its own design pass.
  - **Item 1 — config-version fingerprinting. DONE, committed `d75db3b`.** Every `debate_records`
    row is stamped with `config_version` (short hash of the behaviorally-relevant config) +
    `config_snapshot` (readable JSON). Additive; no decision changed. This is what makes the open
    skew A/B (below) cleanly separable — a band-present vs band-absent arm fingerprints differently.
  - **Item 2a — council veto moved from a post-hoc hard rule INTO the arbiter's vote. DONE,
    committed `5e7f7aa`.** Rule 0d (plus its `_RULE_0D_*` constants, `_has_rule0d_*` helpers,
    Invariant 1, and the engine-level veto cross-check) removed; Balthasar's synthesis
    `geometry_veto` now carries the structural veto (HOLD_GEOMETRY / RISK_BLOCK over a RECONFIGURE
    → THESIS_HOLDS in-council); proceeding over a live Casper STAND_DOWN / DEFER_STRUCTURAL
    requires `override_justification` (new RiskVote carrier + `debate_records` column), else the
    objection stands (conservative fallback — safety never loosens). `veto_mode` fingerprint
    flipped `hard_rule_0d → in_debate`. The now-inert `[REGIME_STANDDOWN]` wake suppressor was
    retired from `scheduler.py` in the same commit.
  - **Item 2b — council constraint DISCLOSURE. DONE, committed `dd5b497`.** `world_state` now
    discloses the "work-within" constraints as existence + CURRENT HEADROOM (USD/XRP buffer
    distance-to-floor) plus a kill-switch existence fact, gated per-constraint by
    `CONSTRAINT_DISCLOSURE` (orchestrator module global; breakers default OFF; loud budget-effect
    warning). The two failure-case BREAKERS (`daily_loss_limit_pct`, `max_allocation_skew`) and
    the `halt_file` path are WITHHELD — redacted from the seat-facing `world_state`, dropped from
    `FIELDS`, and removed from Balthasar's SIGNALS hints **together** (the load-bearing
    three-surface redaction; drift validator clean). `constraint_disclosure` joins the config
    fingerprint. Structural pauses (no-valid-geometry / NO_PROFITABLE_GRID) were DROPPED from the
    disclosure set — they are council outcomes, not pre-vote standing state.
  - **Also committed this session (were done-in-working-tree but uncommitted):** the **Balthasar
    downtrend/capital-erosion persona correction** (`0623dd3`) and the **`validate_schema` repoint
    to the live `.md` personas + bare-token NOTE downgrade** (`c84cdbd`, closes item 9 above). The
    three-commit split (`0623dd3` → `c84cdbd` → `dd5b497`) kept each change under an honest
    message — the downtrend correction and the repoint were NOT bundled under the 2b message.

  **OPEN / DEFERRED (recorded so they are not re-derived):**
  1. **SKEW CATEGORIZATION (open — the one piece of the determinism-shrink not settled).**
     Allocation skew is currently treated as a WITHHELD breaker (`max_allocation_skew` redacted
     from `world_state` by item 2b), but it is arguably a **work-within risk condition** Balthasar
     should reason about concretely (existence + headroom), not a circuit breaker he could steer
     toward as a budget. **The 0.85 band was DELIBERATELY LEFT in Balthasar's persona** (his
     Step-2 skew bands operate on the disclosed `portfolio.allocation_skew`, not on the withheld
     threshold field) — **do NOT change it until this is decided.** Inform the call from his
     recorded `crux` / `override_justification` on high-skew cycles (SUGGESTIVE only); **SETTLE it
     with a band-present vs band-absent PAPER A/B** (definitive — item-1 fingerprinting now makes
     the two arms cleanly separable). Decide the bucket (work-within vs withheld) before relying on
     the categorization.
  2. **WAKE-TYPE / `world_state` TRIMMING (deferred, COUPLED — frames the per-seat trimming
     deferral above).** Frame the deferred trimming along a **routine-wake vs gate-convene axis**:
     a routine daily wake could run a leaner input; a gate convene gets the full payload. THREE
     coupled facts, to resolve together with data, not piecemeal: (a) it is the principled trigger
     for WHEN to trim; (b) it REQUIRES Langfuse per-cycle token data — do not cut on a hunch; (c)
     trimming the routine wake's input IS deciding that gate convenes are a distinct operating
     MODE, which couples it to the "tell the council its wake type" question — in that design,
     **disclosing wake type becomes mandatory** (a lean-input wake genuinely is a different gear).
     Caching already softens the one-gear status quo, so two-gear savings must clear a real bar.
  3. **Langfuse SCORES mirror (carry-forward, Stage-4-era).** Project the matured per-role grades
     onto the cycle traces (DB stays authoritative) for decision-vs-outcome analytics — do this
     once the per-role scorers (`c4c0bd8`) have a live consumer (currently only the archived
     dashboard reads them).

The remaining numbered items below were written for the ADK-`council.py` framing and are
partly superseded by the hand-rolled-orchestrator direction; treat them as the checklist
the orchestrator build must satisfy, not a separate ADK bring-up.

1. **Runtime bring-up.** Set the per-vendor API keys in `.env` (Google for Casper,
   DeepSeek for Melchior, Anthropic for Balthasar). Confirm each seat parses structured
   `output_schema` output — Casper proven via native Gemini; Melchior via the DeepSeek
   Anthropic-compat endpoint with `thinking` disabled; Balthasar via Claude. All schemas
   go through `schema_for_tool`, never CrewAI `generate_model_description`. A native
   forced-tool fallback is already proven for Claude in `phase1_balthasar/`.
2. **Eval the ADK agents against frozen datasets** (`evals/`,
   `phase1_balthasar/balthasar_runner.py` pattern) — ≥0.70 gate, per agent. This is
   the go/no-go, NOT a Letta-output comparison (Letta `debate_records` are
   contaminated; do not use as baseline).
3. **DONE 2026-06-07, COMMITTED 2026-06-08 (`c4c0bd8`) — `get_agent_accuracy` rewritten
   per-role** (`database.py`). *(Status note: this was written 2026-06-07 but lived
   UNCOMMITTED in the working tree until 2026-06-08, when it was committed as-is — the source
   tree had diverged from this doc. It is **built + committed, but NOT yet wired to a live
   consumer**: the only caller was the archived `archive/magi_dashboard_2026-06-02/dashboard.py`;
   live wiring is a **Stage-4 Journal** task. Its prerequisite `unrealized_pnl_6h` column +
   observer backfill shipped in the sibling commit `9c7d1df`.)* The
   old single `fills_6h>0 AND pnl_6h>=0` predicate (which mis-scored a correct Melchior
   `NO_PROFITABLE_GRID` stand-down as a failure) is replaced with per-role scoring:
   Casper regime-realized over a 72h forward window via the new shared
   `grid/forward_sim.py` (PnL-independent; `UNCERTAIN` matched-to-ambiguous); Melchior
   verdict-conditional (`THESIS_HOLDS` reality-graded on fills+realized+unrealized,
   `NO_PROFITABLE_GRID` via forward-sim, `RECONFIGURE` via a decision-time scorer-comparison
   PROXY flagged as not a true counterfactual); Balthasar total-PnL with applied-vs-overridden
   detection, where reality-graded (applied CLEAR/PROCEED) and counterfactual-graded (applied
   veto, via forward-sim) results are kept SEPARATE in the return shape and never summed.
   This CLOSES the recall PREREQUISITE (without correct per-role scoring, recall would teach
   Melchior to over-trade). See `01_CURRENT_STATE.md` Session 2026-06-07.
4. **Build the controlled recall layer** (scoped, approved-in-principle, not built).
   Deterministic `get_agent_recall(agent, n, days, since=restart_cutoff)` from
   `debate_records`; per-role correctness; bounded (recency window + max items);
   read-only to the agent; injected as per-call prompt input by `council.py`;
   exclude contaminated Letta-era rows. NOT `VertexAiMemoryBankService` (LLM-driven
   consolidation reintroduces the rejected hidden-state problem). Measure with eval
   A/B (recall on/off, regression check) + a live no-act shadow for real signal.
5. **Update downstream readers off the old action vocabulary** (display/analysis
   only, not control): `dashboard.py` panels; `observer._record_outcome_to_block`
   (currently a dead Letta no-op — re-point to a SQLite/native channel or remove);
   `analysis/*` replay/forecast scripts testing `== 'RECENTRE'` etc. that won't
   match `'RECONFIGURE'`.
6. **Decide the dual-write fate** (old M6): with the agent layer rebuilt, confirm
   whether `magi_decisions` dual-write stays (legacy dashboard/learning consumers)
   or those readers move to `debate_records`.
7. **Council model lineup (rebuild) — DECIDED, all three seats PROVEN STANDALONE.**
   Casper `gemini-2.5-flash`, Balthasar `anthropic/claude-sonnet-4-6` (Sonnet is the
   DECIDED tier — supersedes the old "haiku-4-5 vs sonnet, re-confirm" question; the
   live Letta agent ran haiku-4-5), Melchior `deepseek-v4-pro`. **The Melchior swap
   (gpt-4o → DeepSeek) is DONE as a proof** — the standalone caller
   `magi/agents/melchior_deepseek.py` is proven (2026-06-05 probe). What remains is
   calling all three seats from the hand-rolled orchestrator (NEXT BUILD above), not a
   per-model swap. Wiring musts carried from the probe: DeepSeek `thinking` explicitly
   DISABLED (v4-pro defaults thinking ON → 400s under a forced `tool_choice`); schemas
   via `schema_for_tool` (NOT a strict-mode rewrite), keeping GridVote's
   geometry-iff-RECONFIGURE contract intact; DeepSeek auths via the Anthropic-compat
   endpoint (`base_url="https://api.deepseek.com/anthropic"`), guard the silent `-flash`
   fallback by asserting `response.model == deepseek-v4-pro`.
8. **Cost is gate-calibration, not cadence constants.** The always-on free gate
   owns cost by deciding whether the council wakes. If cost needs tuning, tune gate
   breach sensitivity (`magi/gate.py` thresholds), not a fixed timer. The per-call
   lever to watch is the R1 fire-rate (`debate_triggered`) within gate-greenlit
   cycles.
9. **DONE 2026-06-07, COMMITTED 2026-06-09 (`c84cdbd`) — repointed `PERSONA_DIR` /
   `load_persona`** in `world_state_schema.py` from the dead Letta-era
   `magi/prompts/*_prompt.txt` to the live `magi/agents/personas/*.md`, so the validator now
   checks the real persona text. The validator also now downgrades bare prose snake_case
   tokens that resolve to no schema field (e.g. `current_price` in `melchior.md`) from ERROR
   to a NOTE; `validate_schema` is a WARN-only PASS again. The 12 Melchior persona-coverage
   WARNs are KNOWN AND DELIBERATELY LEFT (prose-citation gaps, not data starvation — all 12
   paths are fed to Melchior). *(Status note: written 2026-06-07 but lived UNCOMMITTED in the
   working tree until 2026-06-09, when it was committed as changeset Y of the three-commit
   split — this CLOSES the standing "repoint validate_schema to live persona paths" item.)*
   See `01_CURRENT_STATE.md` Session 2026-06-07.

## Migration work queue (added 2026-05-29 — LARGELY DONE / PARTLY SUPERSEDED)

> **2026-05-31:** The agent-call layer this queue planned is BUILT — but on
> **stateless Google ADK**, not the vendor-stateful Agent-Studio / Memory-Bank /
> Managed-Agents design below. KEY DEVIATIONS: agents are stateless (self_model
> dropped; recall → SQLite prompt-injection, see post-migration queue item 4); the
> Casper→Agent-Studio (M1), Melchior→Responses-API (M2), Balthasar→Managed-Agents
> (M3) platform specs were NOT used; M5's "port the Letta wrappers" is moot
> (council.py was rewritten, not wrapper-ported). What WAS executed: schemas,
> personas, model handles, and the boundary-preserving council.py rewrite. Retained
> below as the original plan of record; do not action M1–M5 as written.

Direction locked 2026-05-29 (see `01_CURRENT_STATE.md` Session 2026-05-29 +
`00_PROJECT_OVERVIEW.md` "Migration target architecture"). Items in priority order.

- **M1. Per-agent rebuild spec — Casper (Google / Gemini Agent Platform).** Memory
  Bank for persistent memory, Sessions for in-cycle state. Draft in chat, not
  Claude Code.
  - **PARTIAL — 2026-05-31 (see `01_CURRENT_STATE.md` Session 2026-05-31).** The
    Google **Agent Studio** "Details" panel inputs are authored in `casper_gcp/`:
    `casper_description.txt` (Description field, 746 chars) and
    `casper_instructions.txt` (Instructions field / operating brain, 13,827 chars),
    both curated from the **live Letta persona** (stale fees, PAPER framing, and
    Letta-runtime phrasing dropped). Casper's R0 **structured-output contract** is
    extracted for the Studio JSON output schema (full field table in the Session
    2026-05-31 entry) with two recommendations: enforce the `position` enum
    (RANGING/TRENDING/UNCERTAIN — today's `_validate_r0` doesn't) and make
    `regime_action` **required** (it is the only decision-driver; missing → silent
    `EXECUTE`/no-veto).
  - **Casper-specific decision:** `self_model` is **NOT** carried forward
    (contaminated, being left behind). This overrides the generic "seed incl.
    self_model" line in `00`/M-queue **for Casper only**. The interrupted
    self_model fact-deconstruction proposal was abandoned — do not resume.
  - **Remaining:** Sessions mapping for in-cycle inputs (`world_state`,
    `recent_outcomes`, `cycle_phase`); Memory Bank seeding decision; enter the JSON
    output schema + set model (`google_ai/gemini-3-flash-preview`) in Studio;
    per-cycle prompt assembly is M5, not the Studio agent definition.
- **M2. Per-agent rebuild spec — Melchior (OpenAI / Responses + Conversations API).**
  Use extended `prompt_cache_retention` (up to 24h). Draft in chat.
- **M3. Per-agent rebuild spec — Balthasar (Anthropic / Claude Managed Agents).**
  Beta header `managed-agents-2026-04-01`; native "dreaming" memory consolidation
  (research preview). Note the **$0.08/session-hour** cost lever — open the session
  per-cycle, not 24/7. Draft in chat.
- **M4. Resolve open audit design questions (themes B, C, D, E of
  `LETTA_SURFACE_AUDIT.md` §7).** Answer together with the operator before any
  per-agent spec is finalised. (Themes A/F/G already answered: statefulness
  vendor-side; provider mapping locked; retire the LETTA AGENTS dashboard panel.)
- **M5. Infrastructure port — Claude Code workflow against the audit's
  `wrapper_functions` table (`LETTA_SURFACE_AUDIT.md` §3).** Replace the bodies of
  `council.py`'s wrappers and retire the rest: `magi/memory_lifecycle.py` (vendors
  consolidate memory natively — Claude dreaming, Gemini Memory Bank, OpenAI
  Conversations chaining; 30-cycle rotation goes away), most of
  `magi/provision_agents.py` (agents built/tuned on vendor platforms), the shared
  `*_r0_output` blocks (redundant — R1 already pastes peer outputs into prompts),
  `sweep_letta_steps_for_failures` (→ per-vendor SDK exception handling), the
  dashboard LETTA AGENTS panel (→ generic "agents reachable" health check). Targets
  stubs first if not all 3 native agents are ready.
  - **M5a. PREREQUISITE — make Letta client construction lazy/deferred.**
    `council.py:238` (and any other module-import-time `Letta(...)` construction,
    e.g. `memory_lifecycle.py:155`) constructs the client at import, so every
    module that transitively imports `council` (scheduler, orchestrator, and every
    test/CLI in that chain) hard-fails once Letta is removed. Refactor to
    function-scoped/deferred construction first, or as the first step of M5.
    Surfaced 2026-05-29 during `wake_guard_sim` verification (see
    `01_CURRENT_STATE.md` Session 2026-05-29 §7-H).
- **M6. learning.py live-path verification.** Confirm whether it runs on any active
  path (service, cron, dashboard, manual import). Required before deciding whether
  the `magi_decisions` dual-write can be retired. (`extract_test_cases.py` already
  verified 2026-05-29 as a one-off, not a live consumer — see CLAUDE.md §4.)
- **M7. Replay verification — third Claude Code workflow.** Replay historical cycles
  through the new infra and diff against pre-shutdown `debate_records`.

## Maintenance contract (read before adding/removing world_state fields)

Adding or removing a field in `magi/orchestrator.py:build_world_state()`
requires a corresponding entry in `magi/world_state_schema.py:FIELDS`.
The runtime validator fires a `severity='critical'` `magi_alerts` row
(triggers ntfy push) on any drift between schema and runtime output.
Persona provisioning hard-fails before any Letta call if a persona
references a path not in the schema, or references a path where the
agent is not declared as a consumer.

To change what an agent sees, edit the schema's `consumers` list and
per-agent `<agent>_usage` hint, then run `python -m magi.provision_agents`.
The auto-generated SIGNALS block is regenerated from the schema at
every provision — hand-editing between `<!-- BEGIN_AUTOGENERATED_SIGNALS -->`
and `<!-- END_AUTOGENERATED_SIGNALS -->` in any persona file will be
silently overwritten on the next provision.

CLI check: `python -m magi.validate_schema` — exits 0 on PASS, 1 on
ERROR, 2 on internal failure. Hook into pre-commit or CI as needed.

### Per-agent projection — deferred with explicit criteria

The schema's `consumers` lists describe which agents consume which
fields, but `world_state` is still pushed as one shared block visible
to all three agents. Projection (per-agent blocks restricting visibility
to only that agent's consumer list) is the natural next step but is
NOT shipped with this schema work.

Ship projection when ANY of the following becomes true:
- **(a)** The freshness validator catches ≥ 5 instances within a 30-day
  rolling window of an agent citing a field outside its declared
  consumer list. Mechanical, queryable from `debate_records.freshness_retries`
  + persona consumer lists in `magi/world_state_schema.py:FIELDS`.
- **(b)** Operator review identifies a specific cycle where an agent's
  vote was incorrect AND a contributing cause was reasoning over a
  field outside that agent's declared domain.
- **(c)** The doctrine generator (if shipped) produces a per-agent
  proposal showing recurring evidence of cross-domain reasoning errors.

Revisit at **2026-07-21** (60 days post-schema-deployment). If none of
(a)/(b)/(c) has fired by then, the conservative read is "projection
isn't load-bearing at this scale" and the deferral persists. The
schema enables projection whenever needed — it's a routing change in
`update_world_state()`, not a re-architecture.



Priorities reordered 2026-05-22. The structural-vote restructure shipped:
Casper emits `regime_action`, Balthasar emits `geometry_veto`, hard rule 0d
reads both additively with existing rule overrides. (R1 was made "always-fires"
in that restructure but was reverted to CONDITIONAL on 2026-05-27 — a
novelty-aware gate, `council.should_run_r1`, for cost reasons; see item 0.)
This was the operational answer to the "council ornamental?" question raised by
the prior renewal-decision framing. The COUNCIL LEVERS dashboard panel
makes the answer observable.

Agents were wiped + recreated this session, so the question is now
empirical: across the next ~30 cycles, how often does rule 0d actually
fire? The first cycle where Melchior + peer vetoes overlapped is also the
session's open bug (Highest Priority #1 below).

The pre-live blockers (§7–§9 below) were all RESOLVED 2026-05-23 — the bot is now live.

## Highest priority

### 0★. [HIGHEST PRIORITY — CAPITAL PRESERVATION] Grid bleeds in sustained downtrends — Balthasar owns the brake (judgment input, not a fitted threshold)

Discovered 2026-05-28 auditing the 9 council cycles since the 2026-05-27 16:09
restart. In a sustained downtrend (XRP ~$1.32→$1.26 over the window) the grid
**recenters into the fall and buys a falling knife.** As price walks below the
book it goes one-sided (`buy_count=0`, `grid_position.side='below'`,
`fillable=False`); T14 wakes MAGI, which correctly fires
`GRID_DEGENERATE → RECENTRE` and re-centers lower — then price keeps dropping
and it recenters again. Each recentre accumulates 1.65 XRP at a progressively
lower price. **Live equity since go-live is ≈ −$2.3, essentially all unrealized
inventory drawdown** (see PnL audit, Session 2026-05-28 in `01_CURRENT_STATE.md`).
The per-cycle judgments are individually *correct* (RECENTRE when stranded is the
right structural call); the **strategy** loses in a one-way market.

**Why the council won't learn this on its own** (the important part):
- The only outcome signal agents passively see is the shared `recent_outcomes`
  block (last 6 cycles, read-only — the per-agent "update your self_model" notify
  was removed for causing corruption). It carries **realized 6h round-trip PnL**,
  which is individually *positive* (~+$0.005/trip) even while unrealized inventory
  bleeds. So even with the PnL-signal bug fixed (below), the signal the agents see
  is "small profit, grid_alive yes" — it structurally **cannot** surface a
  downtrend bleed that lives in unrealized inventory.
- Until 2026-05-28 that PnL field was additionally **broken** — every live cycle
  showed `$0.0000` (the paper-pollution FIFO bug). The `observer.py`
  `_compute_window_metrics` live-scoping fix (staged for the next restart) makes
  it real going forward, but per the point above that is necessary-not-sufficient.
- Regime detection under-calls it: Casper keys TRENDING on `adx >= 20`, but ADX
  sat 15–17 even with `adx_neg`(22–26) ≫ `adx_pos`(12) and roc_6h −2 to −3.7.
  Compounded by roc_6h being null ~40% of cycles (root cause: 6h is a separate
  fragile Kraken fetch; fix staged in `observer._resample_6h_from_1h`).

**The fix — ADOPTED 2026-06-06: Balthasar OWNS this risk as a judgment call, NOT a
fitted deterministic threshold.** The corrected Balthasar persona makes downtrend /
capital-erosion risk his explicit domain (distinct from Casper's regime
classification), and he receives `drawdown_from_high_7d` as **context he weighs and
must cite when it moves his vote** — not a mechanical trigger. **Do NOT build a
`dd7d ≤ −X ⇒ PAUSE_LONGS` rule** (or any hardcoded drawdown threshold): the
decision-test evidence is thin (3/20, below) and fitting a hardcoded X to 3 events is
the overfitting trap the operator forbids — grid params stay anchored to fees/spacing,
never fitted to history. The bleed itself is real (above); what changed from the
2026-05-28 framing is that the *drawdown-magnitude* brake lives in Balthasar's risk
judgment, not in a fitted dd7d band. The council's *passive* signal still can't see
unrealized drawdown (it reads realized 6h PnL) — which is exactly why the risk must be
Balthasar's owned, prompted judgment rather than left for the council to infer. If the
persona promotion is later treated as a live behavior change, it MUST go through
`evals/run_all.sh` before any provision.

**OPEN candidate (distinct from the ruled-out dd7d band) — a deterministic REGIME
stand-down.** Ruling out a fitted *drawdown-magnitude* threshold does NOT rule out a
deterministic survival floor anchored to **trend mechanics**. The structural condition
that makes recentering pathological is a confirmed sustained downtrend — e.g. `adx_neg`
materially > `adx_pos` for N consecutive cycles, OR `roc_6h` below a floor, AND buy-side
skew rising. A rule that detects that regime and **stands down (suppress new buys /
suppress RECENTRE until the trend abates)** keys on the *direction/strength of the move*,
not on how far price has already fallen — so it is anchored to trend mechanics, not
fitted to the bleed episodes. This remains an **open candidate for the survival floor —
NOT ruled out, NOT yet built**, and is a different mechanism from both the Balthasar
judgment input above and the ruled-out dd7d *magnitude* band. Caveat: depends on the
`roc_6h`-null fix (staged, above); any thresholds must be justified by trend mechanics,
not tuned to the historical bleed. If built, it MUST clear `evals/run_all.sh` before any
provision.

**Update 2026-06-06 — corrected Balthasar persona PROMOTED; `drawdown_from_high_7d`
wiring is now a HARD PREREQUISITE.** An offline decision-test (240 `claude-sonnet-4-6`
calls over 60 forward-labeled stressed XRP world_states; detail in
`04_EXPERIMENTAL_IDEAS.md` / `01_CURRENT_STATE.md` Session 2026-06-06) asked whether
giving Balthasar a `drawdown_from_high_7d` input **plus** persona authority to use it
moves his verdict cautiously on downtrends. Finding: the signal is **inert** under the
old persona (it stated "there is no drawdown field" / "never reason about price
direction"); a corrected persona granting narrow price-erosion authority moves 3/20
TRUE_BLEED scenarios more-cautious (all citing the factor, incl. one CLEAR→PAUSE_LONGS,
net +2, zero false positives) — real but **thin**. **DECISION:** adopt drawdown as a
Balthasar **judgment input** (the corrected persona), **not** a fitted
`dd7d ≤ −X ⇒ PAUSE_LONGS` threshold — 3 events is too few to fit a hardcoded X without
overfitting. The downtrend bleed remains a real risk Balthasar now owns.
- DONE: the validated corrected language was promoted to the live
  `magi/agents/personas/balthasar.md` (text only; backup
  `magi/agents/personas/balthasar.md.bak.20260605`). No `evals/run_all.sh` /
  `provision_agents` run — the seats are built off these persona files, services are
  stopped; if this is later treated as a live behavior change, the eval gate above applies.
- **DONE (2026-06-06 later) — field WIRED, HARD PREREQUISITE CLOSED.** `build_world_state()`
  now computes `drawdown_from_high_7d` from 168×1h bars on a running-peak basis (`peak =
  max(max(highs), price)`, clamps ≤0, signed percent, `None` when price/candles missing),
  and `magi/world_state_schema.py:FIELDS` has the matching entry with Balthasar as the sole
  consumer (Maintenance contract followed). `validate_schema` 0/0 PASS at 102 paths;
  one-shot emit verified (−21.24 on current data). Drawdown is a judgment input — **no
  deterministic dd7d threshold added**, by design. Detail: `01_CURRENT_STATE.md` Session
  2026-06-06 (later).

### 0. [HIGHEST PRIORITY — COST] Council spend ~$5–6/day on a $67 book

Discovered 2026-05-27 auditing spend since the 2026-05-26 re-enable. It is
**not** gate-wakes (zero since resume) and **not** evals (zero since resume) —
it is the plain scheduled council. Real spend since resume (~12h, 6 cycles):
**$4.97** (`token_usage`), corroborated by Letta's own billing screen.
Steady-state on the 4h scheduled cadence ≈ **$5–6/day** — i.e. the entire $67
book consumed in ~12 days on LLM fees before a single fill. Not tenable. The
`~$0.30/cycle` figure in CLAUDE.md §4 is wrong by ~3×; real per-cycle cost is
**~$0.80–1.00**.

Call accounting (since resume): **50 LLM calls / 6 cycles = ~8.3 calls/cycle.**
Per agent over 6 cycles — Casper 18, Melchior 17, Balthasar 15. Decomposes into:
- **18 R0 votes** — 1/agent/cycle. Baseline.
- **18 R1 synthesis calls** — 1/agent/cycle. R1 has been **unconditional**
  ("always-fires") since the 2026-05-22 restructure (`council.py:run_round_1`).
  Every cycle is **2 calls/agent by design** (R0 + R1), not 1. `debate_triggered`
  (true 3/6) only records whether synthesis *shifted* a position — it does NOT
  gate the R1 call.
- **14 R0 freshness retries** — the avoidable bucket. When R0 cites stale numbers
  the orchestrator re-issues a full ~80–90k-token R0 call. Casper retried 6/6
  cycles, Melchior 5/6, Balthasar 3/6 (18 baseline R0 + 14 retries = 32 observed).

Measured cost structure (since resume):
- **Melchior / gpt-4o = 65% of spend** ($3.24 of $4.97). Most expensive model,
  ~75–87k-token inputs, inconsistent prompt caching (some R0 calls show no `ci`
  cache marker and pay full freight). Also the model the docs say is least
  responsive to prompts.
- **R1 synthesis = $1.78 (36% of spend), and it fires every cycle by design.**
  Not a debate-gated cost — it is the unconditional second pass. Cutting it means
  making R1 conditional again (P5), an architectural change, not a knob.
- **Freshness retries ≈ doubling of R0.** `debate_records.freshness_retries`
  shows `casper:true` 6/6, `melchior:true` 5/6. Each `true` is a second full
  ~80–90k-token re-call. This is the cheapest waste to remove if the retry
  isn't actually changing the vote (P3).
- **Context bloat underneath all of it:** 74–114k input tokens/call (Casper
  avg 93k). Letta threads are never trimmed, so every cycle costs more over time.
- **Worst part: all of this is spent while the bot is parked in HALT / not
  trading.** Full-price council deliberation, zero trading benefit.

Levers, in priority order (P1 highest):

- **P1 — Make council cadence a function of trading state.** A parked
  (HALT / PAUSE_INVALID / non-trading) bot does not need a ~$1/cycle council
  every 4h. Drop to 12h or event-only while parked; resume 4h only when the grid
  is actually allowed to trade. Biggest, simplest win — could cut steady-state
  cost ~3×. Lives in the scheduler / observer cycle gating. (Related: the
  "Reconsider startup-cycle behavior in scheduler" engineering item below.)
- **P2 — Question gpt-4o for Melchior.** 65% of cost, weakest responder. Either
  swap to a cheaper model or aggressively trim his thread — largest single
  line-item win. NOTE this collides with the "do not engineer away GPT-4o
  anchoring" entry under *Explicitly NOT on the roadmap*: that entry is about
  not fighting his bias for behavioral reasons; this is a **cost** decision.
  Revisit that boundary with the operator before acting.
- **P3 — Fix freshness retries. [FIX 1 DEPLOYED 11:28 UTC; FIX 2 DEPLOYED 16:09 UTC, 2026-05-27].**
  Diagnosis confirmed via 7 days of `[FRESHNESS_FAIL]` logs: ~67/69 retries fired
  on cosmetic precision drift (e.g. 39.41 vs 39.39, a static reference stat cited
  at slightly wrong precision), NOT staleness — pure burn. **Fix 1 (materiality
  band):** added to `council.py:_validate_r0_freshness` (`_within_freshness_tolerance`,
  `max(5% relative, 0.02 absolute)`); only flags as stale when a cited value
  diverges from its closest world_state candidate beyond tolerance. Validated:
  16/18 sampled real mismatches now skip the retry; the genuine catch (autocorr
  cited ~10x off) still fires. **Fix 1 was INCOMPLETE — `casper:true` still fired
  EVERY cycle.** Root cause was upstream, in `_find_closest_fresh`: Casper's lead
  evidence derives the price-vs-EMA200 % distance ("-22.41% from EMA200"), which has
  no literal world_state field; the old absolute match window (`±max(|stale|,1.0)`)
  matched -22.41 to the unrelated `drawdown_median=-2.45` (~9x smaller) → retry every
  cycle. **Fix 2 (matching gate):** (a) relative-capped plausibility gate
  `max(0.5*max(|stale|,|cand|), 1.0)` rejects large-magnitude cross-quantity matches,
  small-magnitude matching byte-identical; (b) no-analog now treated as FRESH, not
  stale (both required). Verified offline on live ws `cyc_1779891312`: Casper's
  -22.41 line → stale=False. Confirm in prod after the 20:00 UTC cycle via the
  query below — `casper` should flip to `false`.
- **P4 — Trim Letta threads. [PARTIALLY DEPLOYED 2026-05-27, restarted 12:44 UTC].**
  Shipped the highest-value piece (= item 2's L1): the 6h outcome-backfill no
  longer posts a per-agent thread message (`messages.create`) — it writes a
  rolling 6-cycle log to a new shared read-only `recent_outcomes` block
  (`observer._record_outcome_to_block`, attached to all 3 agents, in
  `provision_agents` shared-block list). Removes a billable inference per agent
  per cycle, removes the dominant removable thread-growth source, AND removes the
  "update your self_model" prompt that fed the Casper/Balthasar self_model
  corruption (self_model now evolves only via the 30-cycle rotation). Agents
  still see outcomes as fresh in-context block content. Removed the council-
  degradation backfill-notify hook (see CLAUDE.md §4 item 2 — now caught on the R0
  path instead). STILL OPEN (lower value now): L2 per-cycle compaction, shrinking
  the ~3.4k-token world_state block, and shortening the 30-cycle rotation cadence.
- **P5 — Make R1 conditional. [DEPLOYED 2026-05-27, restarted 11:28 UTC; confirmed firing/skipping in logs].**
  R1 was unconditional (always-fires for all 3 agents). A pure conflict-gate was
  found insufficient: Casper's `regime_action` is `STAND_DOWN` in 45/46 live
  cycles, so a chronic RECENTRE-vs-STAND_DOWN standoff makes "fire on conflict"
  fire ~65% of cycles. Implemented the **novelty-aware** gate instead
  (`council.py:should_run_r1` + `_r0_conflict` + `r0_position_signature`, wired in
  `orchestrator.run_cycle` via `_prior_r0_signature`): R1 fires only when a genuine
  position/lever conflict exists AND the R0 position triple differs from the prior
  cycle. Frozen standoffs (identical to last cycle) and aligned cycles skip — the
  hard-rule layer resolves both regardless. Deliberately excludes grid-state
  conflicts (handled by hard rules). Simulated over 46 live cycles: R1 fires 11/46
  (23%), ~76% fewer R1 calls. Skip path verified safe (resolve_consensus falls back
  to R0 finals incl. R0 regime_action/geometry_veto; debate_record writes NULL R1
  columns). NOT eval-covered (pipeline change, not a persona edit) — validate by
  watching `[Round 1: firing/skipped]` log lines + `council_r1` call rate post-restart.

Done when: steady-state council spend is bounded to a level the operator accepts
for a $67 book (target to be set with operator), AND the bot is not paying
trading-cadence council cost while parked in a non-trading state.

Evidence queries (`observer.db`, reproducible anytime; `<resume>` =
`2026-05-26T19:49`):
- spend by agent/source: `SELECT agent, source, COUNT(*), SUM(estimated_cost_usd)
  FROM token_usage WHERE timestamp >= '<resume>' GROUP BY agent, source;`
- retries + debate per cycle: `SELECT timestamp, trigger, debate_triggered,
  freshness_retries FROM debate_records WHERE timestamp >= '<resume>';`

**Casper chronic STAND_DOWN — diagnosed + fixed 2026-05-27 (the grid-parking
root cause, separate from cost).** Decision-tree replay over 38 cycles: all 38
fired TRENDING only via condition 4(b) (`adx_neg > adx_pos`) with ADX 14.6-15.4
(never >=20) — weak-ADX directional bias mislabeled as a trend, parking a
grid-favourable low-vol range. Fixed: ADX>=20 floor on persona condition 4(b) +
tightened STAND_DOWN + curated Casper self_model (which had hardened the bug into
doctrine, incl. "grid death = successful survival"). Deployed live, no evals
(operator declined). Full writeup in "Session 2026-05-27" of `01_CURRENT_STATE.md`.
Watch next scheduled cycle: Casper should flip to RANGING/EXECUTE → grid un-parks
→ first real orders.

### 0a. [RESOLVED 2026-05-26] standdown loop fixed + bot re-enabled live

magi.service re-enabled ~19:49 UTC 2026-05-26. Standdown loop root cause
(stranded grid + corrupted judgment, not a hard-rule bug) diagnosed and fixed —
full writeup in "Session 2026-05-26" of `01_CURRENT_STATE.md`. Fix added
`world_state.grid_position` + reframed Balthasar/Casper to treat a RECENTRE on a
stranded grid as corrective; eval-gated (Casper 0.875, Balthasar 0.778);
Balthasar's runaway "STAND_DOWN→HALT" self_model corruption curated + thread reset.

Live follow-ups still open:
1. **Model discrepancy (⚠):** live Balthasar runs `claude-haiku-4-5` but
   CLAUDE.md §1 and the eval factory build him on `sonnet-4-6` — the eval
   validates a stronger model than production. Decide downgrade-vs-drift.
   **Now also interacts with P2 (item 0):** model choice is a cost lever, so
   resolve these together.
2. **Memory hygiene vs cycle bursts:** 30-cycle rotation cadence couldn't keep
   pace when the T2 burn generated ~30 extra cycles/day. Consider size-triggered
   rotation, not just cycle-count. Overlaps P4 (item 0).
3. **Melchior (GPT-4o) mild anchoring** (sticky verbatim self_model lead line) —
   not currently harmful; thread compact/reset is the hygiene lever. Covered by
   item 2 below. Casper (Gemini) does NOT anchor — leave alone.

To revert to paper: `rm /root/xrp_grid/CONFIRM_LIVE` + restart (restore with
`touch`). To stop: `systemctl stop magi.service && systemctl disable magi.service`.

### 1. Rule 0d tag-emission / engine downgrade — RESOLVED 2026-05-27

Original 2026-05-22 report: `cyc_1779480012` showed `hard_rule_overrides=[]`
AND `applied_grid_action=NULL` on a cycle where Melchior=RECENTRE + peers veto,
where rule 0d should have tagged `[REGIME_STANDDOWN]`/`[BALTHASAR_RISK_BLOCK]`.

Audited 2026-05-27 — this was actually **two separate issues, both now closed**:

1. **Rule-0d tag recording: NOT a bug (already fixed, stale report).** Verified
   over 48 live cycles: when Melchior genuinely HOLDS a structural vote into the
   hard rules (`melchior_r1_held=1`, grid_action structural at function entry) with
   a council veto, rule 0d records the tag every time — e.g. cycles 05-25
   13:00/11:02/10:02/09:01 all show `["[REGIME_STANDDOWN]"]`. The empty-overrides
   cycles in the original report were correct: R1 synthesis had already shifted
   Melchior to MAINTAIN, so `_original_grid_action` was MAINTAIN and rule 0d had
   nothing to veto. The Invariant-1 icontract work (2026-05-24) plus the
   `_original_grid_action`-at-entry capture closed the original race. No code change.

2. **`applied_grid_action`/`applied_spacing`/`engine_clamped`/`clamp_reason` never
   written: real gap, now FIXED.** The orchestrator comment said these were
   "filled in later by the engine," but no write path existed — NULL in 0/48 rows.
   Fix: `grid/engine.py:apply_magi_decision` now records what it actually applies
   into `self.last_applied` (pure additive side-effect, no control-flow change),
   capturing engine-level divergence — council-veto cross-check coercion,
   empty-book-guard skips, null-geometry refusals, and spacing clamps. The
   scheduler writes it back via new `database.update_debate_applied(cycle_id, …)`
   after `apply_magi_decision`. Verified: compiles, DB round-trip works; populated
   from the next live cycle onward. This is genuine engine-vs-council divergence
   visibility (it was redundant-with-final_grid_action ONLY when the engine doesn't
   diverge — which is exactly when there's nothing to see).

### 2. Melchior conversation-history anchoring — structural fix

Melchior has been reciting byte-identical R0 evidence across 6
consecutive cycles spanning 2026-05-20T20:18 → 2026-05-21T08:04
(~12h): same `vwap_dev_pct: 0.5291`, same `autocorr_1h: -0.0244`,
same `autocorr_4h: 0.0968`, while `world_state` values have moved.
Memory rotation fired successfully on 2026-05-20T16:00 (thread
reset), but the freeze re-emerged within ~4h — the existing 30-cycle
cadence is too sparse to bound the anchoring failure mode.

The `[GEOMETRY_INJECTED_FROM_SCORER]` fallback (2026-05-19) papers
over the geometry-emission half of this; the vote/evidence half is
still naked. With Melchior frozen, the "architectural diversity"
premise of the three-provider council degrades to "two diverse
voices and one tape loop."

**Short-term (this session or next):**
- Snapshot Melchior's thread to `/tmp/melchior_messages_2026-05-21.json`.
- Force a `client.agents.messages.compact()` on Melchior outside the
  rotation cadence to clear immediate freeze. Verify the next R0 cites
  current world_state numbers.

**Status update 2026-05-22**: agent wipe + recreation cleared the
immediate anchoring state. L3 (freshness validator) shipped this session
as Change A (inline-correct retry) + Change B (warn alert). L1, L2, L4
remain unshipped — re-evaluate after observing the post-recreation
agents over the next 10–20 cycles whether anchoring re-emerges in the
new threads.

If it does:
- **(L1) Route observer outcome-backfill out of the message thread —
  DONE 2026-05-27 (as P4 in item 0).** The 6h backfill now writes to the shared
  `recent_outcomes` block (`observer._record_outcome_to_block`) instead of
  `messages.create`. (Note: only the 6h window ever notified, not 1h/6h/24h.)
  Removed thread growth + per-agent inference + the self_model-write prompt.
- **(L2) Per-cycle compact for Melchior** —
  `messages.compact(mode='self_compact_sliding_window',
  sliding_window_percentage=0.5)` before each R0 call. Bounds anchoring
  surface continuously instead of every 5 days.
- **(L4) Adaptive rotation cadence** — track per-agent Jaccard similarity
  of evidence list vs prior 3 cycles. Force `rotate_agent_memory` when
  score > 0.9 for 2 consecutive cycles.

Done when: across 10 consecutive cycles, no R0 evidence list is
byte-identical to the prior cycle when world_state numerics have
changed. Currently passing trivially (post-wipe, only 7 cycles exist).

### 3. Validate council-veto rule 0d earns its keep (observational)

Council-veto rule 0d shipped this session reading `regime_action` +
`geometry_veto` additively with the existing rule overrides. CONFLICT_MATRIX
+ Round 1 challenge/debate is retired; R1 now always fires as a synthesis
pass and the engine reads the two new structural vote fields directly.

The empirical question is: across the next ~30 cycles, do the four new
council-veto tags (`[REGIME_DEFER]`, `[REGIME_STANDDOWN]`,
`[BALTHASAR_HOLD_GEOMETRY]`, `[BALTHASAR_RISK_BLOCK]`) ever fire on
cycles where Melchior actually wants to rebuild?

The COUNCIL LEVERS dashboard panel is the visibility surface. If after a
week `cycles_with_council_downgrade=0` consistently, the structural-vote
fields are inert and the architecture needs different changes (likely
on Melchior's side, since the council-veto chain only matters when he
votes RECENTRE/TIGHTEN/WIDEN). If it fires non-trivially, the council
is load-bearing at the engine layer and the renewal-decision case
strengthens.

No code work — observational only. Re-evaluate at the 30-cycle mark
(~5 days from agent recreation = 2026-05-27).

## Medium priority

### 4. Orphan Letta block cleanup

Six orphan persona blocks at project scope from prior provisioning runs
(IDs in session record). Plus orphan `human`, `decisions`, and
`self_model` blocks. Confirmed not attached to any current agent. Visible
in the Letta UI's Memory blocks page and misleading. Delete via
`c.blocks.delete(block_id)` after a final manual confirmation pass.

### 5. Backfill 17 NULL `hard_rule_overrides` rows

Older `debate_records` rows (pre-column-migration) have NULL
`hard_rule_overrides`. The dashboard's 30-day override-count panel
under-reports until they age out. Options:

- (a) Re-parse `magi_decisions.notes` for matching cycles and backfill
  (already attempted; 17 rows could not be matched within 90s timestamp
  proximity — would need a wider window with manual confirmation).
- (b) Accept under-reporting; rows age out of the 30-day window in
  ~30 days from their original timestamps.

(b) is cheapest and acceptable. Only escalate to (a) if the operator
specifically wants accurate historical analytics in the 30-day window.

### 6. Dashboard `magi_decisions` migration completion

Two analytic reads migrated this session (latest-cycle override tags,
30-day counts). `/api/status:1777` still returns a `magi_decisions`-shaped
object for back-compat (consumer surface area unclear). Full migration
requires retiring the dual-write, which requires migrating `learning.py`
and `extract_test_cases.py` too. Defer until those readers are touched
for other reasons.

## Pre-live — ALL RESOLVED 2026-05-23 (bot is live)

### 7. Dashboard auth — DONE
Moved to a Flask signed-cookie session (`/login`, `DASHBOARD_PASSWORD` +
`SECRET_KEY` in `.env`, 365-day cookie); nginx `auth_basic` removed. The
cloudflared tunnel hits Flask:5000 directly so nginx was never in the
public path — app-side auth is what actually gates it now. Token
(`X-Magi-Token`) bypass preserved for automation.

### 8. Verify Kraken write-side ops with the live keys — DONE
`AddOrder` / `QueryOrders` / `ClosedOrders` / `Balance` all exercised
against the live keys 2026-05-23 (a real 2 XRP market test order filled,
txid OIGJW7-4GZ7T-AACAYV). Two-factor gate satisfied; paper⇄live is now a
single env-var toggle (`MAGI_LIVE_CONFIRM` + `CONFIRM_LIVE`).

### 9. Live trading decision — DONE (flipped live)
Flipped to live 2026-05-23. Live order placement +
`reconcile_live_fills_from_kraken` fill path shipped and verified. Grid is
currently PAUSED/empty (scorer finds no acceptable variant); first live
anchor fires on a future cycle when geometry qualifies. Accumulating a
meaningful >50%-accuracy / fee-positive window is now the ongoing
operational goal, not a pre-live gate.

## On the horizon

### 9. Asset selection — finalise
DOGE has the best historical grid PnL; XRP is current because grid
dynamics are more forgiving. Decide: switch primary, or run both.
Per-asset spacing already determined (XRP 1.5%, DOGE 2.5%, SOL 2.0%).

### 10. Dual-operation concept (deferred)
Run a "Volume Engine" on a high-volatility asset alongside the MAGI grid,
purpose: accumulate 30d Kraken volume to unlock lower fee tiers. Only
worth exploring after live trading is stable.

### 11. CHANGELOG.md
Long deferred. Re-evaluate when there's a stable cadence of changes
worth logging separately from the handoff docs.

## Engineering / non-blocking (added 2026-05-24)

Low priority. Do not promote above Highest Priority. All deferred.

### Measure cold startup time
Restart of `magi.service` has crept to ~60s. Add timing markers around the
major startup sections (imports done, Kraken balance fetched, WS connected,
Letta config validator done, world_state built, scheduler ready) and log the
deltas at INFO. Establish a baseline; revisit only if it crosses ~90s.

### Rename `MAGI_HOURS_EST` → `MAGI_HOURS_UTC`
(and any related EST-labeled variables). Misleading since inception — the
droplet runs UTC and the hours are UTC hours. Cosmetic; defer until
`scheduler.py` is being edited for another reason.

### Expand icontract invariant coverage
First pass (`enforce_hard_rules`, 2 invariants) landed 2026-05-24. Watch for
≥30 days. If the invariants prove their worth with no false positives, expand
to `apply_magi_decision` and `_build_debate_record` — but only with invariants
grounded in observed or specifically anticipated bug patterns, never
architectural intuition.

### Lore-relevant module/concept naming pass
Operator wants a finishing pass applying Judeo-Christian / Kabbalah-derived
names (mostly Tree-of-Life Sefirot) to architectural layers — e.g. Gevurah for
the hard-rules layer, Yesod for the orchestrator concept, Metatron for the
observer/scribe layer, Tikkun for memory rotation. Selective, not a full theme
— only where the metaphor genuinely clarifies the layer's function. Naming
table to be approved before any rename touches code.

### Sefirot-themed module renames (deferred from 2026-05-24)

Operator wants Kabbalah-derived names applied at the .py file level
for layers where the metaphor genuinely clarifies the layer's function.
Deferred because file renames require careful grep-driven import-site
updates across the codebase, atomic commit, and a verified service
restart — not session-end work.

Strong candidates (metaphor clearly earns it):

- observer.py → metatron.py — Metatron is the scribe of heaven in
  Jewish mysticism; observer.py records every cycle's outcomes to
  debate_records, computes 1h/6h/24h backfills, sends outcomes to
  agent threads. Direct functional fit.
- magi/memory_lifecycle.py → magi/tikkun.py — Tikkun olam is the
  Lurianic concept of ongoing repair/restoration by gathering
  scattered divine sparks. memory_lifecycle distills 30 cycles of
  agent thread chaos into permanent self_model patterns, evicting
  weak ones, preserving wisdom. Genuinely matches.

Lower-priority companion (internal constants in orchestrator.py):
- HARD_RULES → GEVURAH_RULES (the rule dict)
- _CANONICAL_OVERRIDE_TAGS → _GEVURAH_TAGS (the icontract canonical set)
  Gevurah is the Sefirah of restriction/judgment — the "no, hold back"
  force. Matches what enforce_hard_rules does (restrict the council's
  expansive will when survival or precedence requires).

Docs-only references (no code rename, conceptual commentary in
CLAUDE.md only):
- Yesod for orchestrator (channel/foundation that gathers and transmits)
- Raziel for the freshness validator (guardian of true record)

Execution plan when picked up:
1. Single Claude Code session.
2. Per file rename: git grep for every reference to old name, present
   full list for operator approval, atomic edit + git mv (preserves
   history).
3. Restart magi.service + magi-dashboard.service, verify clean startup
   and ADAM init lines for the new module name.
4. Land each file rename as a separate commit so a single revert is
   targeted if anything goes wrong.
5. Constant renames (GEVURAH_RULES, _GEVURAH_TAGS) ride along with the
   icontract orchestrator.py work or land as their own small commit.
6. Update CLAUDE.md with a "Conceptual layer naming" subsection
   documenting the full Sefirot mapping including the docs-only ones.
7. Sync to magi-docs.

Prerequisite: at least 24h of clean live operation since the most
recent code change, to ensure the renames aren't bundling onto a
not-yet-stabilized state.

### Reconsider startup-cycle behavior in scheduler

scheduler.run_magi_cycle fires on every startup (per design:
"cycles run every 4h plus startup and manual triggers"). This made
sense early in development when restarts were rare deploys, but
during iteration-heavy sessions it can add $3-5 of unnecessary
Letta cost per day (each restart = ~$0.30 cycle). Open question:
should startup just resume the existing schedule (next cycle at
the next 4h slot) rather than firing immediately?

Arguments for current behavior: catches state drift after
downtime; provides immediate signal that scheduler is healthy
end-to-end; the first cycle is the one most likely to surface
restart-related bugs.

Arguments for change: cost discipline during dev iteration;
restarts on a stable system aren't carrying new information;
ADAM/icontract already surface startup health without needing a
cycle to fire.

Possible middle ground: env-flag opt-out for development
restarts (e.g. MAGI_SKIP_STARTUP_CYCLE=1 in .env when iterating,
removed for production). Defer until restart-cost has bitten in
a way that justifies the design change.

## Done 2026-05-22 (do not re-do)

- **Council restructured: R1 always-fires + two new structural vote fields.**
  `regime_action` (Casper) and `geometry_veto` (Balthasar) emitted alongside
  R0, refined in R1 synthesis, persisted to `debate_records`, read by hard
  rule 0d. Four new override tags: `[REGIME_DEFER]`, `[REGIME_STANDDOWN]`,
  `[BALTHASAR_HOLD_GEOMETRY]`, `[BALTHASAR_RISK_BLOCK]`. Engine has a
  defensive cross-check in `apply_magi_decision`. CONFLICT_MATRIX retired
  as dead code.
- **Persona cleanup** across all three personas for the new R1 contract;
  schema consumers updated; validator clean.
- **Agent wipe + recreation** on Letta Cloud. Self_models intentionally
  empty; pattern numbering will restart at `## Pattern 1` per agent on
  first rotation.
- **Gate layer** (`magi/gate.py`) with 9 calibrated triggers
  (T1–T4, T6–T7, T11–T13) feeding `world_state.triggers_since_last_cycle`.
  Calibrated against 8 years of XRP/USD historical data.
- **Kraken WebSocket v2 substrate** (`grid/exchanges/kraken_ws.py`)
  always-on ticker + ohlc; health in `ws_health` table.
- **Freshness validator + A+B retry path** in `_validate_r0_response`.
  Inline-correct retry on numeric mismatch + warn alert on retry exhaustion.
  Per-cycle log in `debate_records.freshness_retries`.
- **COUNCIL LEVERS dashboard panel** showing distribution of `regime_action`,
  `geometry_veto`, and council-veto tag fires across post-recreation cycles.
- **Disk cleanup** (SAFE bucket sweep, ~8 GB recovered).

## Done 2026-05-20 (do not re-do)

- **Memory rotation lifecycle** — `magi/memory_lifecycle.py` ships and
  fired its first live rotation at 16:00 UTC. 30-cycle cadence,
  per-rotation safety invariants (snapshot, strict validation, eviction
  under 5000-char cap, merge-then-reset). Counter persisted in new
  `system_state` table; per-rotation accounting in new `memory_rotations`
  table. All three agents rotated successfully (status=success on all).
  See `01_CURRENT_STATE.md` § Session 2026-05-20 for details. Latent
  watch item: pattern numbering grows monotonically (no curation step
  yet); eviction is FIFO on the lowest-numbered block.

- **Phase 1 compaction API verified** — `client.agents.messages.compact()`
  returns `CompactionResponse{num_messages_before, num_messages_after,
  summary}`. Self-compaction modes (`self_compact_sliding_window`,
  `self_compact_all`) run on the agent's own model. The `summary`
  field carries the distilled output and is NOT written back to memory
  blocks by the API — block writes must go through
  `client.blocks.update()`. Verified live against Balthasar (365 → 257
  messages, 1338-char summary, self_model block untouched).

- **Kraken-keys staleness corrected** — see task 7 above. Prior claim
  that the live keys were broken is no longer accurate for the
  `Balance` endpoint; `AddOrder` is the remaining unverified path.

## Done 2026-05-19 (do not re-do)

- **No-fills root cause + four new orchestrator rules.** Diagnosed
  91h fill drought as Melchior emitting null `geometry` every cycle
  (GPT-4o anchoring), compounded by `get_latest_candle_hl` returning
  the in-progress candle. Fixes:
  - `[GEOMETRY_INJECTED_FROM_SCORER]` — inject scorer rank-1 into
    Melchior's R0 geometry on `RECENTRE/TIGHTEN/WIDEN` with null
    agent geometry. `debate_records.geometry_source` column tracks
    `'agent' | 'scorer_fallback' | 'unchanged'`.
  - `[GRID_HEALTHY_NO_RECENTRE]` (rule 0a) — downgrade RECENTRE to
    MAINTAIN when book is bilateral AND
    `|price − centre| / centre < spacing_pct`. Time-independent
    complement to `[RECENTRE_COOLDOWN]`.
  - `[NO_ACCEPTABLE_VARIANT]` — force `grid_action = GRID_PAUSE`
    when scorer has no acceptable rank-1 on a rebuild action.
  - `[RECENT_POSITION_HOLD]` (rule 0c) — downgrade
    `RECENTRE/TIGHTEN/WIDEN` to MAINTAIN + neutralise PAUSE_LONGS/SHORTS
    when `hours_since_last_fill < 2.0` AND `|inventory_skew| > 0.15`
    AND book bilateral. Protects open round-trips from premature
    force-close.

- **All static spacing defaults removed.** `GRID_SPACING_PCT` deleted
  from `config.py`. `engine.initialise_grid()` requires
  `spacing_pct > 0`. `apply_magi_decision` refuses to rebuild on null
  geometry. New `scheduler._first_boot_geometry()` pulls rank-1 for
  the first grid; stands down if no acceptable variant exists.

- **Anchor-then-arms mechanic in engine** — two-stage grid init.
  Stage 1: market anchor at current spot, direction follows skew,
  sized as one rung. Stage 2: arm limit orders built around the
  actual anchor fill price. If anchor fails, no arms placed. Three
  production fills since shipping (13:04 BUY anchor → 15:04 arm BUY
  → 16:00 SELL anchor on forced RECENTRE). Live-mode market anchor
  implemented + exercised 2026-05-23.

- **Position-state context in `world_state`** — new orchestrator
  helpers `_last_fill_summary()`, `_position_state_summary()`,
  `_skew_delta_since_rebuild()` add `last_fill`, `position_state`,
  `skew_delta_since_rebuild` to the world_state block.

- **Observer cadence** dropped 60 → 10 min. `get_latest_candle_hl`
  fixed to return last COMPLETED candle (was returning in-progress).
  Shadow simulator persist-key fix; 18 stale `shadow_grid_state`
  rows deleted. `agent_registry.balthasar.model` row corrected to
  `anthropic/claude-haiku-4-5` (display-only fix from the 5/18 model
  switch).

- **ONE GRID invariant tripwire** — `_assert_one_grid_invariant`
  called at `_execute_anchor` entry and `initialise_grid` exit.
  Surfaces silent multi-generation order accumulation. Non-blocking;
  informational only.

- **Dashboard UX overhaul (cosmetic).** MAGI triangle hero,
  conviction-based agent coloring, NGE-accurate fonts, JS soft
  refresh replacing meta-refresh, collapsible analytics panels.

## Done 2026-05-18 (do not re-do)

- Scheduler midnight-reset bug fixed (`scheduler.py:501-502` removed).
- `database.get_latest_candle_hl` now returns last completed candle.
- Balthasar: claude-sonnet-4-6 → claude-haiku-4-5 (knobs preserved via
  `model_settings`). `provision_agents.py:52` mirrored for idempotency.
- Letta display names capitalised: Casper / Melchior / Balthasar
  (lowercase `agent_id` strings unchanged).
- `magi/spacing_evaluator.py` — analytical per-level variant scorer.
  Per-pair threshold `2*i*spacing`, capital-normalised grid total,
  `acceptable` flag (operator's per-level positivity hard requirement).
  36 variants: `levels ∈ {5..10} × spacings ∈ {0.005..0.025}`.
- `_pick_shadow_winner_spacing` removed; `melchior_geometry.target_levels`
  added; engine clamp `[4, 12]`.
- Shadow sim reduced 24 → 6 variants (level-count only at live spacing).
  `config.SPACING_VARIANTS = [0.025]` (placeholder; overridden each
  rebuild). 18 stale `shadow_grid_state` rows deleted; DB vacuumed.
- Melchior persona appended (`=== ANALYTICAL VARIANT-SCORE ===` + per-
  level-positivity rider). Block 7951 → 8974, limit 8000 → 9000.
  Snapshot at `/tmp/melchior_persona_2026-05-18_pre-variant-eval-append.json`.

## Done 2026-05-17 (do not re-do)

- `scheduler.py` replacement-pricing bug — fixed
- `[GRID_DEGENERATE]` hard rule — implemented
- `[RECENTRE_COOLDOWN]` hard rule — implemented
- `[PAUSE_INVALID]` hard rule — implemented
- `hours_since_last_fill` / `hours_since_last_rebuild` in `world_state` — done
- LLM config equalisation (temperature, max_output_tokens, reasoning) — done
- Persona prompt equalisation — done (Casper / Melchior / Balthasar all rewritten with shared SYSTEM CONTEXT, numbered decision trees, 2 worked examples each)
- self_model curation first pass — done (Casper + Melchior rewritten; Balthasar untouched)
- `debate_records.hard_rule_overrides` column + dashboard migration — done
- `provision_agents.py` LLM config sync — done
- Letta Evals Option A — per-agent persona regression, frozen synthetic
  scenarios, exact_match on R0 `position`. Suites live under
  `/root/xrp_grid/evals/{casper,melchior,balthasar}/`; runner is
  `evals/run_all.sh`; results land in `magi_eval_runs` and render in the
  dashboard EVAL HISTORY panel. Use as the post-persona-edit gate before
  re-running `magi/provision_agents.py`. Requires manual one-time setup
  of a `magi-evals` Letta Cloud project (web UI) and `LETTA_EVALS_PROJECT_ID`
  in `.env`. The eval venv is Python 3.11 under `evals/.venv/`
  (uv-managed); MAGI's main venv stays Python 3.10.

## Deferred eval expansions

- Option B (cross-model parity) — add `model_handles: [...]` to suite YAMLs.
- Option C (self_model drift) — vary `agent_args.self_model_text` per sample.
- Option E (memory-block integrity) — `memory_block` extractor + rubric
  grader on self_model writes.
- Option F (rubric on evidence quality) — second grader on `r0_evidence`
  using `model_judge`.
- `provision_agents.py` eval-gate hook — block pushes when the most
  recent eval run failed; would abort `provision_agents.py` if no recent
  pass exists for the agent being updated, with `--skip-eval-gate` for
  emergencies. **Deferred — revisit after 2-3 successful eval-gated
  persona edits.** First need real signal on (a) whether operators
  actually run the eval before pushing without enforcement, (b) whether
  false-positive eval failures (e.g. a scenario the persona is being
  changed to intentionally answer differently) become a workflow blocker.

## Deferred follow-ons from 2026-05-24 gate-wake work

The gate-wake wire, T14/T15 book triggers, RECENT_POSITION_HOLD refinement,
and GATE ACTIVITY observability shipped this session (see
`01_CURRENT_STATE.md`). These are the deliberately-deferred next steps —
parked with operator agreement, not urgent, revisit against real data.

- **Real-time fill detection via Kraken private WS executions channel.**
  Today's book triggers detect a depleting fill on the observer/reconcile
  cadence (10-min REST `ClosedOrders` poll), so a one-sided book is noticed
  within ~10 min. Market data is ALREADY real-time (`gate_monitor` +
  `kraken_ws.py`), but that WS client is **public channels only by design**
  (ticker/ohlc/status) — fills are not on it. The genuinely real-time path is
  Kraken WS v2's **private executions / ownTrades** channel: a fill would push
  instantly → `evaluate_book_state_triggers` → wake, with ZERO extra REST
  calls. Deferred because (a) it needs private WS auth + a new channel handler
  threaded into the reconcile path, (b) the live fill model isn't candle-bound
  so 10-min REST is adequate for now, (c) the 1/hr wake throttle (below) is the
  actual responsiveness ceiling, not detection speed. NOTE: do not chase this
  by lowering `OBSERVER_INTERVAL_MINUTES` — that just multiplies public REST
  load; the WS executions feed is the correct lever. Trigger to revisit: if the
  GATE ACTIVITY panel shows T14 firing on real depletion events and the
  best-case ~10-min detection is demonstrably costing fills/PnL.

- **Decide whether a genuine one-sided-book emergency should bypass the 1/hr
  wake throttle.** `WAKE_MIN_INTERVAL_MIN=60` caps off-schedule wakes to bound
  Letta spend, so in the unlucky case (a cycle ran minutes before the book
  emptied) the adaptive response waits out the hour — the fired T14 isn't lost,
  just delayed. For most skew this is fine; for a hard one-sided book in a
  trending market it may be too slow. Option: let T14 (one-sided only, not T15)
  bypass the throttle, or give it a shorter floor (e.g., 20min). This is the
  higher-leverage dial than detection speed. Trigger to revisit: any event
  where T14 fired, the throttle delayed the wake >30min, and the book stayed
  one-sided accumulating inventory across that gap.

- **Promote T15 to wake-class and/or annotate Melchior's persona with the new
  wake reasons (eval-gated).** T15 (skew drift early-warning) currently fires +
  annotates the next cycle but does not itself wake MAGI. If early-warning
  proves valuable, promote it into `WAKE_CLASS_TRIGGERS`. Separately, Melchior
  already has the decision logic for one-sided/skew (Steps 1/4) and acts on the
  world_state signals regardless, but its persona doesn't yet name T14/T15 as
  wake reasons — adding that is a persona edit, so it MUST go through
  `evals/run_all.sh` + `provision_agents.py`, not a hand-push. Trigger to
  revisit: when tuning wake-class membership against GATE ACTIVITY data.

## Deferred follow-ons from 2026-05-18 cooldown-visibility work

Added after Part 3 design proposal on hard-rule visibility was approved.
The core change (Melchior reads `cooldown_status` as a pre-evaluated
constraint, with relaxed STEP 1 carve-out) shipped in the same session.
These three are downstream consequences of that doctrine — not urgent
right now, but they become live questions if production behaviour
surfaces the patterns each is meant to address.

- **Extend [RECENTRE_COOLDOWN] to also gate TIGHTEN / WIDEN if production
  shows action-thrash after Part 2.** The current rule blocks only
  RECENTRE; the new STEP 0 allows TIGHTEN/WIDEN during cooldown. If
  Melchior starts voting TIGHTEN aggressively during cooldown and the
  engine tears down a fresh grid as a result, the "let the fresh grid
  breathe" intent is violated. Fix is to extend the cooldown rule to
  gate `grid_action in {RECENTRE, TIGHTEN, WIDEN}` rather than RECENTRE
  alone. Trigger to revisit: > 1 cycle in any 24h window where Melchior
  votes TIGHTEN or WIDEN with `cooldown_status.recentre_cooldown_active=true`
  AND the resulting rebuild produces zero fills within the next cycle.

- **Expose `buffer_status` and `alloc_skew_status` to Balthasar in the
  same shape as `cooldown_status`** (doctrine extension to risk
  domain). Per the selective-visibility doctrine, each agent should see
  pre-evaluated constraints in its own domain. Balthasar currently
  re-derives [USD_BUFFER_FLOOR] / [XRP_BUFFER_FLOOR] / [ALLOC_SKEW_CEILING]
  from raw inventory each cycle. Schema mirror: `{buffer_active: bool,
  margin_remaining_usd: float, ...}`. Trigger to revisit: any cycle where
  a buffer / skew hard rule fires and Balthasar's R0 vote did not
  anticipate it. (Lower urgency than cooldown because risk rules fire
  far less often than cooldown.)

- **Revisit cooldown duration — 60min is unmotivated, possibly scale
  with vol_regime.** The current `< 1.0 hours` threshold in
  `enforce_hard_rules` is hard-coded with no analytical basis. Plausible
  alternatives: 30min under HIGH vol (let fast markets re-centre), 90min
  under LOW vol (let slow markets actually fill). Trigger to revisit:
  after 2-3 weeks of production data, if the post-cooldown-rebuild
  cycle's fill-within-60min rate is < 20%, the duration is wrong.

## Deferred follow-ons from 2026-05-18 Melchior v2 redesign

The 24-variant shadow infrastructure (Phase A) and economic world_state plumbing
(Phase B) landed and verified. The Grid Economist persona (Phase C) and new
dataset (Phase D) failed Phase E at 0.700 (7/10) against the 0.80 gate. Live
Letta Melchior agent was NOT updated; production runs continue with the v1
persona feeding through the new richer world_state (v1 persona simply ignores
the new fields). Picking up tomorrow from this state.

- **Resolve Phase E failures.** Three samples failed:
  - scen 2 (THESIS_HOLDS, voted NO_PROFITABLE_GRID): Melchior conflated
    "fresh rebuild → no time to fill yet" with "sustained quiet → regime
    broken." Persona needs to make the rebuild-recency exception more
    explicit and stronger relative to the "all variants 0 fills" → NO
    inference.
  - scen 7 (INSUFFICIENT_DATA, voted THESIS_HOLDS): Melchior treated 1 fill
    as validation of the math. Persona needs an explicit floor on what
    counts as "enough data" (e.g., ≥ 5 fills total across the table).
  - scen 8 (THESIS_HOLDS, voted NO_PROFITABLE_GRID): Melchior saw "current
    has 0 fills" and ignored that an alt variant had 6 fills. **Note: this
    scenario's ground truth is itself contestable — the operator's "uncertain"
    framing in the original Phase 2 spec may not map cleanly to a single
    correct label.** Revisit scen 8 ground truth before iterating the persona.

- **Kraken TradeVolume API integration for live fee tier sourcing.** Currently
  `current_fee_tier_pct` in world_state is sourced from `MAKER_FEE`
  (now `0.0025`, corrected to live-verified tier-0 on 2026-05-23). The
  Kraken client at `grid/exchanges/kraken.py`
  has no `TradeVolume` endpoint wired up. Add it; persist 30-day volume to
  observer.db; expose `current_fee_tier_pct` and a new
  `next_fee_tier_requirements` field in world_state. Bounded scope, ~half a
  session. Independent of the persona work — could ship standalone.

- **Mapping Melchior's RECONFIGURE output to orchestrator-side deterministic
  grid rebuild logic.** Currently a RECONFIGURE vote names a target variant
  in `key_evidence` ("reconfigure_target: lc=X, sp=Y.YY%") but nothing
  consumes it. `engine.evaluate_and_maybe_switch_levels` still only considers
  level-count switches at the live spacing. To make Melchior v2's vote
  actionable, wire the orchestrator to parse RECONFIGURE evidence, validate
  the target against the shadow table, and call into the engine to switch
  both level_count and spacing. **Required before Melchior v2 can fully
  replace v1 in production.** Defer until persona passes the eval.

- **Volume Engine strategic concept (separate engine to accumulate volume
  for fee tier improvement).** Independent strategic concept: a second engine
  whose explicit goal is generating volume to climb Kraken's fee tier ladder,
  even when the grid engine itself is fee-eating at the current tier. Trades
  short-term P&L for long-term fee reduction. Standalone — orthogonal to the
  Melchior redesign but lives in the same "economic reasoning" mental space.
  Probably 2-3 sessions of design + implementation, contingent on
  TradeVolume API integration landing first.

- **Live-mode market anchor implementation** — DONE 2026-05-23.
  `engine._execute_anchor` places a Kraken market order
  (`add_market_order`, no `oflags=post`), polls `query_order` for fill,
  reads `vol_exec`/`price`/`fee`, and reconciles inventory from
  `get_balances()`. Live arm fills tracked via
  `reconcile_live_fills_from_kraken` (ClosedOrders). See Session
  2026-05-23 in `01_CURRENT_STATE.md`.

- **Intra-bar fill detection.** Fill model still uses the most recent
  COMPLETED 1h candle's H/L. Live tick-level price action that
  traverses a level mid-hour is invisible until the candle closes
  (up to ~70 min latency). Two paths: (a) poll Kraken OHLC every 1-2
  min and use the in-progress candle's H/L (cheap, no new dependencies);
  (b) subscribe to Kraken WebSocket v2 `ticker` channel and track live
  bid/ask traversal (correct but adds always-on socket). Either unlocks
  meaningfully more fills at current vol.

## Explicitly NOT on the roadmap

- Self-hosted Letta (decommissioned; do not revisit unless Cloud fails)
- Supervisor / override authority (rejected)
- Letta open-source thread-persistence experiments
- ETH futures (dead)
- Adding new exchanges
- Scaling up paper dollar amounts (goal is validation, not money)
- Third-party Kraken wrappers (banned)
- Mem0, Graphiti, persistent-thread-only memory layers
- Engineering away GPT-4o's anchoring or Sonnet's risk-conservatism. The
  provider mix is the architectural diversity. The correction mechanism
  for stuck-agent behaviour is `CONFLICT_MATRIX` → Round 1 (task 1
  above), not per-agent compliance fixes.
