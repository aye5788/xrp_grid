# MAGI — Current State

> **2026-07-04 (LATEST — verification pass, no code changes). The 2026-07-02
> shipments are all COMMITTED + PUSHED on `council-redesign` (`dab17a1` PnL
> decomposition + work-off ladder; `429b6aa` ntfy fix + tape restore + grader
> unification + Ranking guard; `4da7b53` episode-aware startup gate; CI /
> invariants / MAGI-02 as `21504c5`/`0edb035`/`f86bdf3`+) and the engine has run
> uninterrupted since the 07-02 20:54 UTC restart. Verified against the live
> journal and book this session:**
> - **Work-off ladder: first armed cycle CONFIRMED working.** Armed at the
>   2026-07-03 00:00 UTC daily council cycle (stance still STAND_ASIDE); the 00:08
>   observer tick seeded 5 sell rungs $1.112→$1.221 (~2.44% apart), logging floor
>   headroom at each rung (12.55→5.95 XRP). Rungs filled into the rally at $1.112
>   (07-03 13:08) and $1.139 (07-03 20:04), each immediately topped up with a new
>   rung above ($1.247, $1.269). Book as of 07-04 ~13:00 UTC: 5 open sells
>   $1.166–$1.269, headroom 3.0 XRP (≈2 more fills to the `[XRP_BUFFER_FLOOR]`
>   stop — expected behavior). The stance-exit stop (ladder stands down on a
>   DEPLOY) is not yet exercised — no DEPLOY vote has occurred.
> - **W2 is ALIVE again.** With `tape_verdict` restored (07-02), a real W2 gate
>   wake fired 2026-07-03 11:00 UTC — the STAND_ASIDE exit is no longer
>   daily-floor-only, closing the "W2 dark" caveat from 06-26.
> - **Ranking guard earned its keep immediately:** first live catch 07-02 17:44 —
>   a seat submitted `order=['A','A','B']` (duplicate label) and the ballot was
>   excluded from the tally with a `ranking_ballot_excluded` warn alert, exactly
>   as designed. No invariant violations, no criticals since the restart.
> - **THE LIVE JUDGMENT QUESTION — stance exit vs the rally.** XRP rallied ~$1.03
>   (06-26 restart) → ~$1.15 (07-04) while the council held STAND_ASIDE
>   continuously since 06-26. Paper decomposed PnL at $1.148: equity Δ +$3.26 =
>   inventory beta +$3.73 + **alpha_vs_hold −$0.47** (realized harvest $0 — sells
>   only, zero round trips; the protective posture underperformed pure hold by the
>   insurance premium). Under the unified grader, matured STAND_ASIDE rows through
>   this window score 0/6 for Casper/Melchior. The 07-04 00:00 daily cycle split
>   three ways — Casper MAINTAIN, Melchior HALT, Balthasar STAND_ASIDE (applied
>   MAINTAIN, standing stance unchanged) — the first fracture after a week of
>   unanimity. Watch: whether the council finds its own DEPLOY exit, and the
>   rally-window accuracy review once these rows mature at 72h.
> - Docs alignment: the stale "uncommitted"/"NOT fixed"/"W2 dark"/06-28-Open
>   markers in `02_NEXT_BUILD_TASKS.md` and the `CLAUDE.md` STATUS block were
>   corrected this session. The local Ollama "ask-it" assistant idea is DROPPED
>   (operator kept the Claude subscription); the committed `local_assistant/`
>   scaffold stays in-repo but is inert and unowned. MAGI-02's desktop MINER
>   (also Ollama-based) is unaffected and still pending its first run.**

> **2026-07-02 (SECOND BATCH). Four operator-approved follow-ups shipped
> and DEPLOYED (services restarted, cycle verified clean): (1) ntfy emoji-title
> crash fixed — the wake-notification feature delivered for the first time ever;
> (2) tape `history.db` RESTORED from the 06-17 GCS snapshot + refilled gap-free
> to now from Bitstamp (27,644 bars, 0 residual) — `tape_verdict` is LIVE again
> (stale=False, verdict yellow/regime green) and kept current by a new hourly
> `tape-tail.timer` (Bitstamp-fed `warehouse tail` subcommand; collector stays
> stood down; trades/spread/flow remain frozen) plus the restored daily
> `tape-backup.timer` (observer.db had had NO GCS backup since ~06-17);
> (3) `Ranking` ballots sanitized at `aggregate()` — dup labels repaired
> keep-first, non-permutation ballots excluded like a non-responder
> (`ranking_ballot_excluded` warn alert), <2 surviving ballots → the existing
> NO_CONSENSUS path; (4) the stance grader and seat action grader now share ONE
> band-break predicate (`grid/forward_sim.py:stance_band`/`path_breaks`) — the
> seat grader's bare drift<0 was false-advertised as "matches the stance grader";
> matured STAND_ASIDE seat grades drop to 0/6 under the honest predicate (price
> rallied; no 5% down-break). The 18:10 UTC startup council cycle ran with the
> live tape verdict and voted STAND_ASIDE (2×STAND_ASIDE+1×MAINTAIN, clear
> Condorcet), its synthesis explicitly citing the workoff telemetry.
> THEN (operator escalation + go): the restart-wake pattern was diagnosed and
> FIXED — the startup gate's conditions (b) and (c) bypassed the W1
> episode-answered guard, so during a standing breach every restart fired a
> ~6-call wake (2 of today's 3 restart wakes re-asked the episode the 11:00 W1
> cycle had already answered). Both conditions now run the wake wire's own
> `_t2_episode_already_answered`; verified LIVE at 20:14 UTC — restart with
> price outside the band stayed QUIET (no council cycle). Detail in `02`
> top-of-queue block.**

> **2026-07-02 (first batch). Two operator-approved builds shipped
> (now COMMITTED as `dab17a1` and deployed): (1) PnL
> DECOMPOSITION and (2) the STAND_ASIDE WORK-OFF LADDER.**
> Background: the operator asked whether the ~23.4 XRP left after the protective
> sells should be sold to lock in the rally, and the verification that followed
> found two real defects. **(a) The equity-scoped PnL headline is inventory beta,
> not grid performance** — `grid/pnl.py:get_pnl_snapshot`'s `total` is
> `current_equity − baseline_equity`, and with ~23–30 XRP standing inventory
> against 1.65-XRP trades that number is dominated by XRP's price path (the live
> run's −$6.95 verdict carried the same distortion). FIXED (reporting only, no
> behavior change): the snapshot now also returns `harvest` (the existing
> FIFO fee-adjusted round-trip PnL, promoted to first-class), `alpha_vs_hold`
> (current equity minus the RUN-START book marked at today's price — the bot's
> contribution vs doing nothing) and `inventory_hold_delta` (pure beta;
> total = alpha + beta, identity verified on live data: +2.02 = −0.12 + 2.14).
> Dashboard tile now shows Grid Harvest / Alpha vs Hold / Total Equity Δ; the
> readiness gates already used FIFO realized and needed no change. **Evaluation
> rule going forward: never cite `total` alone as the profitability verdict.**
> **(b) STAND_ASIDE's "work inventory off" promise was ~78% unimplemented** — the
> fill replenisher can only re-arm the OPPOSITE side of a fill, so under
> STAND_ASIDE (no buys) nothing can ever create a new sell: the old grid's 4 sell
> rungs (6.6 of ~30 XRP) were the entire work-off capacity, after which the book
> went EMPTY (verified: last rung filled 2026-07-02 13:09, 0 open orders) and the
> residual inventory sat unmanaged — no seat reasons about it (verified across all
> cruxes since restart), no persona assigns it, and the action space has no verb
> for it. FIXED as engine FIDELITY to the council's own mandate (the
> `CandidateDecision` action text itself promises work-off — this implements the
> stance, it does not bypass the council): `scheduler.maintain_workoff_ladder`
> keeps a sells-only resting ladder above market while the STANDING stance is
> STAND_ASIDE — maker-only rungs of exactly `ORDER_SIZE_XRP` (1.65), depth capped
> at the grid's level count, never committing XRP past the `[XRP_BUFFER_FLOOR]`
> headroom ($10), no taker anchor; bootstrap (empty book) and re-arm are the same
> top-up operation, anchored to current market. Any other standing stance makes it
> a no-op; a DEPLOY rebuild replaces the book via rule 6 as before. **Activation
> is operator-gated:** inert until a council cycle exists at/after
> `system_state['workoff_armed_after_utc']` = 2026-07-03T00:00 UTC (the next daily
> wake) — the ladder starts from the system's own decision loop, never from the
> code deploy. The council was kept informed in the SAME change (world_state
> `workoff` block: active/rungs_resting/xrp_headroom_above_floor/
> worked_off_xrp_since_stance; new schema FIELDS entry, all three seats
> consumers; `validate_schema` PASS 0 ERROR) and all three personas + the
> `CandidateDecision` STAND_ASIDE description were updated to the new active
> semantics symmetrically (personas snapshotted `*.md.bak.20260702`), so no
> future vote is cast under the old passive reading. Dry-run verified with a stub
> engine: at $1.0917 it would place 5 sells at $1.119→$1.228 (8.25 XRP committed,
> floor headroom +6.0 XRP); pre-arm no-op verified; dashboard renders 200 with
> the new tiles. Current alpha reading: the protective sells cost $0.12 vs pure
> hold so far — the insurance premium, now measurable instead of hidden.
> DEPLOYED SAME SESSION: both services restarted; the gated startup council wake
> fired (config fingerprint changed — the persona/schema edits) and voted
> STAND_ASIDE (2x STAND_ASIDE + 1x MAINTAIN, clear Condorcet) — and EXPOSED a
> latent 06-26 bug: `grid/engine.py:apply_magi_decision`'s integrity guard calls
> `get_system_state` without importing it (`NameError` crashed the apply step on
> the first-ever fully-empty book; the decision itself stood). Fixed by adding it
> to the module import list. ALSO found live (open, in `02`): the off-schedule
> wake ntfy alert crashes on its `ℹ️` title (latin-1 header encoding) — the
> 06-27 notification feature has never actually delivered.**
> (1) **HIGH — council-bypass.** The observer's grid-replenishment (`scheduler.py`)
> re-armed a BUY on every sell fill checking only price-drift — never `pause_longs`,
> stance, or the exposure cap — silently undoing the council's STAND_ASIDE between
> cycles (verified firing 06-26/06-27; both re-armed buys cancelled by luck before
> price reached them). Fixed: gate buy re-arm on `pause_longs` OR
> `down_walk_streak>=DOWN_WALK_CAP_STREAK`, sell re-arm on `pause_shorts` (reads
> existing protective state, makes no market call). (2) **MED — `roc_6h` nulled
> hourly** by `gate_monitor`'s recompute (a 2nd writer to the `indicators` table that
> passed an empty 6h list → overwrote poll_cycle's value with NULL on ~99% of
> completed-hour rows for ~3 days); fixed to resample 6h from the 1h bars; verified
> live (21:00/22:00 rows now carry roc_6h). (3) **MED — freshness monitor added**
> (`world_state_schema.alert_on_stale_inputs`): edge-triggered, ALERT-ONLY (`warn`,
> magi_alerts `stale_council_input`) — catches silently null/stale council inputs that
> shape-only drift validation misses. ALSO: tape `history.db` is GONE from the box →
> `tape_verdict` permanently dead (restore from GCS or demote). Aggregation/anonymizer/
> engine-guards/paper-fills re-VERIFIED SOUND; `roc_6h` was the ONLY partial-write
> instance. Open: grader-predicate mismatch, `Ranking` permutation guard, tape decision.
> Design-goal status unchanged: surviving, but no round trip since 06-12 (fee-positive
> untestable) and no STAND_ASIDE stance matured to 72h (accuracy not yet measurable).
> Detail: `05` (2026-06-28 TL;DR), `CLAUDE.md` STATUS.**

> **2026-06-25. Dashboard reconnected publicly (new locally-managed
> cloudflared tunnel) + Langfuse instrumentation rebuilt for the blind-review
> council + the Casper propose 400 FIXED. The trading ENGINE (`magi.service`) stayed
> SHUT DOWN (paper hold) throughout — none of this session's work runs the engine.
> Full detail: `05_COUNCIL_REDESIGN.md` §7 and "Session 2026-06-25" below. Reminder:
> the blind-review council in `05` supersedes the arbiter-era council descriptions
> elsewhere in this doc.**

> **2026-06-12 — MAGI RESTARTED ON PAPER (16:10 UTC) after F5 + a full
> reactivation audit; all 4 audit blockers and 3 degraded items FIXED, each with
> per-fix operator approval and a verification artifact.**
> - **F5 ran and PASSED its pre-committed criteria** (2025→2026 hourly replay,
>   rebuilt $53.09 / DD 42.3% vs old $21.59 / DD 69.0%; Langfuse dataset
>   `f5-acceptance`, run `f5-2026-06-12`) — **but was DEMOTED by the operator to
>   skeleton-floor evidence only**: the replay models fixed 1.5% spacing and
>   simulable rule halves, not the judgment layer (council, Melchior's
>   variant-ranking access, stance reasoning), and two fidelity rules were added
>   unilaterally mid-test (a violation, owned). **The paper run itself is the
>   real acceptance test.** Do not cite F5 as validation of the council.
> - **Reactivation audit (operator-ordered, multi-agent workflow: 8 claim-attack
>   finders + adversarial verifiers, 152 claims attacked):** verdict NOT READY,
>   4 blockers, all fixed 2026-06-12: (1) hard-rule 6 (GRID_DEGENERATE) now also
>   dormant while the exposure cap is engaged — under a cap, a forced RECENTRE
>   rebuilds sells-only so it can never cure buy_count=0; it just flapped, one
>   paper-taker anchor per council cycle (orchestrator; 4-case dry-run matrix
>   verified). (2) Sub-floor book guard at scheduler startup — a restored book
>   with spacing below `MIN_GRID_SPACING_PCT` no longer resumes: paper → cancel
>   + first-boot rebuild at scorer rank-1; live → critical alert, NEVER
>   auto-cancels real orders. (Guard lives in scheduler, NOT engine.load_state —
>   the dashboard also calls load_state and a render must never cancel orders.)
>   On restart it fired exactly as designed: cancelled the 5 stale 0.75% orders,
>   rebuilt 5 levels @ 2.50% around ~$1.13. (3) Capped-rebuild abort threshold
>   corrected to 2 rungs (`xrp_avail < 2*ORDER_SIZE_XRP`): a capped rebuild
>   spends one 1.65 rung on the taker SELL anchor + needs one for a resting arm;
>   between 1.65 and 3.30 XRP it used to cancel the book, taker-sell the last
>   rung and end with ZERO orders. **Order size itself unchanged at 1.65.**
>   (4) 40 `magi_gate_events` rows written by the audit's own dry-run (incl. 2
>   unconsumed fired W1s that would have phantom-woken the council) deleted;
>   snapshot at `/tmp/gate_events_contamination_20260612.sql`.
> - **Degraded items fixed:** (a) gate `_compute_scorer_state` candles now carry
>   `close` — the scorer counts swings over closes (needs ≥24), so without it
>   every variant scored acceptable=False and T6/T7 were silently dead since
>   2026-06-09; verified live (rank-1 = 5 @ 2.50%, any_acceptable=True). (b)
>   Gate-eval dead-man's switch in `observer.poll_cycle`: if no
>   `magi_gate_events` row in >2h, the hourly observer poll runs `evaluate_gate`
>   itself — the prior scheduler comment claiming an observer fallback existed
>   was FALSE; a dead GateMonitor silently killed all W-wakes. Worst-case gate
>   blindness now ~3h at hourly latency instead of indefinite. (c) Seat-accuracy
>   Langfuse scores now delivered convergently: `seat_scores_pushed=1` only when
>   every score POST confirms 2xx; dropped deliveries tracked as warn
>   `magi_alerts` category `seat_scores_delivery_incomplete` (escalation if it
>   becomes regular: per-seat receipt columns — see code comment). Verified
>   end-to-end post-restart: 2 matured cycles graded, receipts stamped, all 6
>   scores queryable via the Langfuse API with grader comments.
> - **Infra (from an operator-submitted EXTERNAL audit, Gemini-authored,
>   source-anonymized as a test):** observer.db converted to WAL +
>   `synchronous=NORMAL` + 30s busy timeouts at all three runtime connect sites
>   (a real `database is locked` had hit gate_monitor 2026-06-09); pytz dropped
>   from scheduler (ZoneInfo only). Audit scorecard after per-claim code
>   verification: 7 findings → 2 real, 1 already-known, 2 wrong, 1
>   impossible-as-written, 1 by-design; its recommendations 2 and 4 were
>   REJECTED — rec 2 (RECENTRE under STAND_ASIDE) would undo Fix 3, rec 4
>   (recentre shadow sim on MAINTAIN) would corrupt the shadow comparison.
> - **Restart (operator-approved, full checklist):** `magi.service` +
>   `tape-collector.service` enabled + started, `magi-dashboard.service`
>   restarted onto rebuilt code. Startup council gate woke on the config
>   fingerprint change (disclosed spend); first cycle clean: **stance DEPLOY
>   (the first stance ever recorded)**, THESIS_HOLDS → MAINTAIN, risk CLEAR, no
>   hard-rule overrides; arbiter named its own exit conditions (skew > +0.60 or
>   buffer < ~$5 → PAUSE_LONGS/HOLD). 72h stance-grading clock running.
> - **New BINDING working rules** recorded in `03_INSTRUCTIONS_TO_CLAUDE.md`
>   ("Rules established 2026-06-12"): money-path wording precision; own it,
>   don't defend; scope is validation not yield; external-audit protocol;
>   track-then-escalate.
> - **Still open (hygiene, non-blocking):** tape/conditions.py 0.75% constants
>   (fix when touching tape analytics); dashboard `_configured_live` omits
>   engine live gate 4; TEMP DEBUG block `engine.py` simulate_fills; dead-ETH
>   root crons; `validate_schema` points at dead Letta-era persona paths;
>   untracked `optimize/f5_acceptance/`. Nothing committed — operator pushes.

> **2026-06-11 — MAGI SHUT DOWN BY OPERATOR ORDER; FIVE-FIX REBUILD EXECUTED (Fixes
> 1–4 BUILT + VERIFIED OFFLINE; Fix 5 acceptance test PENDING). NOT RESTARTED.**
> The session began as an LLM-cost investigation and became a forced audit that
> uncovered six accumulated failures the prior sessions' audits never surfaced
> (operator verdict: effectively deceived). `magi.service` was stopped + disabled
> mid-session on operator order. What was then built, each step verified with an
> artifact (see "Session 2026-06-11 — outcome-scope poisoning…" git context and the
> session entry below):
> - **Fix 1 — profit gap.** A 9.5-year hourly backtest (2016-12→2026-06,
>   tape/history.db, fresh $61.50 book/year, 0.25% maker both sides) proved the
>   0.75% grid loses in 9 of 10 years (fees ate ~2/3 of gross). Spacing floor
>   raised to 6×maker = 1.5% (config + HARD_RULES); `magi/spacing_evaluator.py`
>   redesigned GoodCrypto-frame: acceptability = fee floor ONLY, swing-counter
>   fill FORECAST (multi-hour legs, replacing the single-hour-range model that
>   would have deadlocked wide grids), rank by per-round-trip margin. Verified:
>   30/30 variants acceptable, rank-1 = 5 levels @2.50% (+2.0%/round trip).
>   Daily effect scores fee_share_7d / net_harvest_7d push to Langfuse
>   receipt-convergently (first real reading: trailing-7d fees $0.31 vs gross
>   $0.16 — net −$0.14, the old spacing's failure measured in money).
> - **Fix 2 — exposure cap.** 3 linked downward rebuilds (≤48h apart) → sells-only
>   rebuilds until a higher-centre rebuild resets. 8/8 streak scenarios pass;
>   dashboard EXPOSURE CAP chip. (Drawdown brake tested against history and
>   REJECTED — mean-reverting.)
> - **Fix 3 — stance mandate.** Arbiter RiskVote gains required
>   `stance ∈ {DEPLOY, HOLD, STAND_ASIDE}`; deterministic translation (HOLD = no
>   rebuild; STAND_ASIDE = no buys, keep sells via PAUSE_LONGS floor);
>   GRID_DEGENERATE stance-gated (DEPLOY-only — kills a fee-burning
>   cancel/rebuild flap found in review, and doubles as STAND_ASIDE's exit);
>   3 new world_state blocks (tape_verdict w/ stale flag, exposure_cap,
>   council_stance) — no schema drift; all three personas updated (stance
>   doctrine + stale-fact corrections: 1.5% floor, real balances ~30 XRP+$27,
>   paper-validation framing; dated .bak.20260611 backups). Safe-hold cycles do
>   NOT overwrite the standing stance (council_error guard). Operator-reviewed
>   line by line; implications analysis drove amendments (HOLD requires NAMED
>   evidence; stale verdict ≠ negative evidence).
> - **Fix 4 — wake redesign.** Wake-yield audit: 0/16 gate-woken cycles produced a
>   council-originated change. ALL T-triggers (T1–T16) demoted to context-only
>   detectors; wakes are now the W-SERIES questions: W1 (breach: recentre or
>   not?, one per episode) + W2 (stance evidence changed: verdict shift held one
>   bar / cap engaged-released). Startup cycle gated (config changed / pending W
>   event / price outside band — else quiet). `get_pending_score_pushes` NULL-trace
>   clog fixed; stance grader `observer.backfill_stance_grades` (72h maturity,
>   band-width-anchored thresholds) + 7 new Langfuse score configs (stance,
>   stance_correct, wake_yield, wakes_per_day, cap_buy_fills,
>   cap_episode_drawdown, matches_backtest).
> - **PENDING:** Fix 5 — offline acceptance test (2025–2026 replay under
>   `optimize/`, pre-committed pass criteria, Langfuse dataset run); operator
>   decides restart. **Restart checklist:** tape collector must come back up or
>   `tape_verdict` stays stale (W2's verdict half silent, stance evidence
>   degraded); expect ~1–3 council calls/day (daily floor + W wakes) vs the old
>   ~11.
>
> **2026-05-31 — AGENT LAYER MIGRATED TO GOOGLE ADK (in code); NOT RUN LIVE.** MAGI
> remains shut down at the service level (stopped + disabled 2026-05-28; no live
> orders). The council's agent-call layer has been **rebuilt off Letta onto Google
> ADK** — `magi/council.py` rewritten, three native ADK `LlmAgent`s in
> `magi/agents/`, **stateless per cycle**, Melchior redesigned as an
> economic-verdict judge. Code-complete and offline-validated (`py_compile` + logic
> checks); **no model invoked, nothing deployed, no live cycle run.** See "Session
> 2026-05-31 (later) — ADK migration" immediately below.
>
> **This SUPERSEDES the 2026-05-29 direction in two ways:** (1) agents are
> **STATELESS**, not vendor-stateful — the "vendor owns memory/self_model/thread
> history" plan is reversed (Letta statefulness caused anchoring + self_model
> corruption); a controlled SQLite-sourced recall layer is scoped, not built.
> (2) Cadence is **gate-driven** (free gate decides whether the paid council wakes;
> floor ≈ 1 call/day), the 4h timer only a backstop. The 2026-05-29 scoping and the
> Casper→Google-Agent-Studio M1 work below are now HISTORICAL — the build did not
> use Agent Studio / Memory Bank; it used ADK `LlmAgent`s with stateless
> prompt-injection. Pre-migration originals archived in
> `archive/pre_adk_migration_2026-05-31/` (+ git HEAD).
>
> **DIRECTION SHIFTED 2026-06-06 — this banner is the historical 2026-05-31 record.**
> The decision layer is now a **hand-rolled orchestrator** (direct vendor-SDK calls +
> owned SQLite state; NOT ADK, NOT CrewAI); the three seats are proven standalone but
> not wired; the ADK `council.py` is unchanged/superseded. The STATE LEDGER directly
> below is the authoritative current state.

## STATE LEDGER — what is LIVE vs. BUILT vs. EXPERIMENTAL (read this first)

> The one place that separates "live" from "experimental." A reader who only skims
> this section should never mistake an offline experiment for the running system.
> Verified against `systemctl` + `magi/council.py` on 2026-06-06. **If a later session
> changes any of this, update THIS ledger first.** Self-contained on purpose (the
> claude.ai reader has no shell to check `systemctl`).

**🟢 LIVE / RUNNING RIGHT NOW** — `magi-dashboard.service` and the leftover tape
warehouse timers. **`magi.service` is STOPPED + DISABLED (2026-06-11, operator
order)** — the paper run is halted pending the five-fix rebuild's acceptance test
(see the 2026-06-11 banner at the top); the "MAGI RUNNING ON PAPER" entry below is
the historical record of the 2026-06-09→11 run:
- `tape-collector.service` — **STOPPED + disabled 2026-06-09 (later)**, stood down for the
  MAGI paper bring-up. It had collected Kraken XRP/USD 1-minute OHLC + trade tape + spread →
  `tape/market_tape.db`; collection is paused, the DB/data are retained untouched. The three
  timers below were left running.
- `magi-dashboard.service` (active+enabled) — **REVERTED 2026-06-09 (later) back to the MAGI
  dashboard** (council/grid view, reads `observer.db`; the tape-monitor shim it had been
  serving was backed up to `dashboard.py.tape-shim.bak`). Same `:5000` + ethobs.uk tunnel +
  Flask cookie auth — unchanged. **2026-06-09 (later still): GUTTED of Letta-era panels and
  restarted** — LETTA AGENTS census, the Costs panel (LLM spend, DigitalOcean billing,
  per-agent credit/runway; spend observability is Langfuse now), and EVAL HISTORY (+ the
  `/evals/<agent>` route) are deleted (~540 lines). The P&L tile is **paper-scoped** in paper
  mode and labeled "Paper P&L" (`/api/status → paper_mode:true`). A latent template bug died
  with the gut: a stray `{% if eval_history.has_any_runs %}` had been wrapping the ENTIRE
  analytics section (Shadow→Debate Log), which would have blanked those panels on any box
  with no eval runs. **2026-06-10: TRIMMED 19→11 sections** — Shadow Grid Variants (+ "vs
  Best Shadow" card), Council Evolution, COUNCIL LEVERS, AGENT REASONING, and the separate
  Market section removed (Langfuse-redundant or obsolete iterations); added a **Council Log**
  (last 20 cycles with trigger, positions, hard-rule tags, per-cycle Langfuse trace
  deep-links) and a header **24h LLM call counter** (counts only the named seat spans —
  `casper`/`melchior`/`balthasar`; the dead `:rebuttal`/`:synthesis` names were dropped
  2026-06-25/CS2, since blind-review spans are named by bare seat with the phase in
  metadata); accuracy/attribution panels paper-scoped.
- `warehouse-append.timer` (hourly), `tape-backup.timer` + `warehouse-backup.timer`
  (daily → GCS) — feed/back up the `tape/history.db` warehouse. As of 2026-06-07 the
  hourly append also writes a **`signals_1h`** snapshot (the dashboard's GRID
  CONDITIONS metrics persisted as an hourly time series; this is where flow imbalance
  gets captured going forward). The full ~9.5y history was backfilled 2026-06-07
  (83,017 rows; see header summary above).
- Mirror repo `aye5788/market-tape`; operational writeup in `tape/README.md`.

**🟢 MAGI RUNNING ON PAPER — `magi.service` active + enabled since 2026-06-09 21:04 UTC.**
The full chain runs on the gate-primary cadence: scheduler → observer (10-min polls, candle
gap backfilled to current) → `council_v2` (on the daily 20:00 EST floor / 25h max-silence
backstop / gate wakes) → hard rules → engine in PAPER mode. **No real Kraken orders** — the
live toggle is disarmed (below) and the engine simulates fills against real prices into its
own paper ledger. First startup was clean end-to-end: fund detection passed ($61.43 ≥ $50),
scorer-built fresh grid (0.75% × 5 levels around 1.14087), paper anchor executed, startup
council cycle completed (Casper TRENDING/STAND_DOWN, Melchior THESIS_HOLDS, Balthasar
CLEAR/HOLD_GEOMETRY → MAINTAIN; trace_id + config fingerprint stamped), ZERO alerts, ZERO
Letta traffic. Full record: Session 2026-06-09 (later still) below. (Historical: the clean
LIVE shutdown was 2026-05-28; the 2026-06-08 hand-invoked decision-only `run_cycle` was the
only new-stack cycle before this run.) **UPDATE 2026-06-10:** the gate wake-class set is
**T14 / T2 / T11 / T16** with per-episode guards — T2 wakes once per breach episode (fixed
the hourly-rewake bug), the NEW T16 drawdown trigger wakes once per 3%-wide drawdown rung
(verified live same day). Decision quality is now scored onto each cycle's Langfuse trace
(1h/6h/24h outcomes, council_changed/conviction_shift reiteration metrics, per-seat 72h
accuracy) with a `trigger:<reason>` tag for slicing. See Session 2026-06-10 below.
**UPDATE 2026-06-11:** a scoping bug had been writing FAKE ZEROS into every paper cycle's
1h/6h/24h outcome record (the backfill counted only live-txid fills, and a paper run has
none) — poisoning Journal recall, Melchior/Balthasar grading, and the Langfuse outcome
scores. Fixed as a class (shared scope helpers in `grid/pnl.py`; commit `9a264b7`), the 9
poisoned rows re-backfilled with real values, the Langfuse mirror reconciled, score
delivery made convergent (per-window delivery receipts + observer retry sweep) and a new
live gate 4 added — live mode is refused until `system_state['paper_run_started_utc']` is
blanked (commit `b49f7dc`). No trading damage (the poisoned cycles' decisions were
unaffected). See Session 2026-06-11 below.

**Live toggle DISARMED 2026-06-09 (later) for the paper bring-up:** `.env` now has
`MAGI_LIVE_CONFIRM=NO` and the `CONFIRM_LIVE` gate file was renamed to
`CONFIRM_LIVE.disarmed.20260609`. The mode selector is `scheduler.py:79-80`
(`_LIVE = os.environ.get("MAGI_LIVE_CONFIRM")=="YES"; engine = GridEngine(paper=not _LIVE)`),
so with the toggle off, **`magi.service` runs PAPER, not live** — both levers were flipped
(defense in depth; either alone forces paper). The kill-switch `HALT` file is absent.
**UPDATE 2026-06-09 (later still): the "⚑ PAPER BRING-UP READINESS" blockers (BU-1/2/3) were
ALL EXECUTED and `magi.service` was STARTED on paper** — the Letta config_validator is removed
(no false pages), the cadence is gate-primary, and no live-path Letta call site remains
(`LETTA_API_KEY` commented out; `agent_registry` UUIDs blanked). See the session entry below.

**🟢 STAGE-3 ARBITER COUNCIL — BUILT + WIRED + COMMITTED, integration-verified, but NOT
running live** (2026-06-08, commit `c47e36a`). The **decision layer is a HAND-ROLLED
arbiter orchestrator**, `magi/council_v2.py` — direct vendor-SDK calls only (NOT CrewAI,
NOT an ADK framework, NOT LiteLlm), a sequential six-call choreography
(Casper→Melchior→Balthasar openings → Casper+Melchior rebuttal → Balthasar synthesis as
arbiter). It is **wired into `run_cycle`** (replacing the ADK parallel-R0/R1 block) and was
proven end-to-end through **one real `run_cycle`** (debate_records + magi_decisions writes,
gate-event consume, Langfuse 6-generation trace, hard-rule RECENTRE path, no Kraken order —
see the 2026-06-08 session entry). Seats (all DECIDED, full personas): Casper
`gemini-2.5-flash`, Melchior `deepseek-v4-pro`, Balthasar `claude-sonnet-4-6`. **UPDATE
2026-06-09 (later still): this IS running** — `magi.service` convenes it on the gate-primary
schedule (see the 🟢 RUNNING entry above). (The 2026-05-31 ADK `council.py` exists but is
unchanged and superseded for the `run_cycle` path.) See the COUNCIL LINEUP block in
`CLAUDE.md`. **Stage 4 determinism-shrink is SUBSTANTIALLY DONE + committed (2026-06-09)** —
item 1 config fingerprinting (`d75db3b`), item 2a council-veto-into-the-arbiter (`5e7f7aa`),
item 2b constraint disclosure (`dd5b497`); only the skew-categorization question is open (the
0.85 band is deliberately left in Balthasar's persona pending a paper A/B). **The per-agent
SQLite Journal recall layer is BUILT + wired into `council_v2`** (committed + pushed
`cebccb5`, 2026-06-09 — deterministic, config-version-filtered, prompt-injected).

**🧪 EXPERIMENTAL / OFFLINE-ONLY** — proofs, probes, and decision-tests that *inform*
decisions but **run nowhere** and are NOT part of the live or the built-and-shippable
system:
- Casper `adk optimize` persona-tuning scaffold (`optimize/casper/`) — offline smoke
  run only. (This is the *only* per-agent tuning scaffold that exists. There is **no
  Balthasar tuning scaffold/prompt** built.)
- Casper forward-realized eval-labeler (`optimize/casper/forward_label.py`).
- **Balthasar `drawdown_from_high_7d` decision-test** (2026-06-06, `/tmp` scratch) — a
  **one-off offline A/B**, not a tuning scaffold and not wired. Finding: the signal is
  real but thin (moved 3/20, net +2, zero false positives, all cited). Adopted as a
  Balthasar **judgment input** (corrected persona); **NOT** a fitted `dd7d ≤ −X ⇒ PAUSE`
  threshold (3 events ⇒ overfitting). The downtrend bleed it targets is a real risk.
- DeepSeek v4-pro Melchior-seat probe (`magi/agents/melchior_deepseek.py`) — standalone
  wrapper, proves viability, not wired.
- `schema_for_tool` Gemini-400 hardening — a real code guard, verified offline.
- **All of `04_EXPERIMENTAL_IDEAS.md`** — design directions (e.g. the council redesign
  making Balthasar the *arbiter*; tiered awareness). NOT adopted, NOT built.

**🔧 DECIDED but NOT WIRED** — locked, but no orchestrator wiring yet: all three seats
(incl. Melchior → `deepseek-v4-pro`) await the hand-rolled orchestrator. (The
`drawdown_from_high_7d` world_state field — the other item that sat here — was **WIRED
2026-06-06 (later)**: `build_world_state()` now emits it with a matching `FIELDS` entry,
closing the persona/world_state inconsistency that was the HARD PREREQUISITE; `02` item
0★ is now DONE. See Session 2026-06-06 (later) below.)

**⚫ DEAD / HISTORICAL — do NOT treat as current:** the entire **Letta** layer (Cloud
agents, memory blocks, self_model, memory rotation, freshness validator, provisioning);
old models Casper `gemini-3-flash-preview` / Melchior `gpt-4o` (being replaced) /
Balthasar `claude-haiku-4-5`; self-hosted Letta; the renewal-decision READINESS gate;
ETH futures; the Supervisor concept. Older session entries below describe these as if
running — they are the historical record, not current state.

---

Last updated: 2026-06-09 (later still) (**PAPER BRING-UP EXECUTED — BU-1/2/3 done,
Letta fully decoupled, gate-primary cadence wired, dashboard gutted + paper-scoped P&L,
fresh paper book, `magi.service` STARTED on paper and verified clean.** See the Session
2026-06-09 (later still) entry. Prior: 2026-06-07 — **TAPE: DOWNTREND-BLEED CHART + `signals_1h` HISTORY
(live-stack work, separate from MAGI).** Added a directional *drawdown-from-high*
line to the dashboard's GRID CONDITIONS — the counterpart to the direction-blind
regime efficiency-ratio (a clean rally and a clean crash both read "trending"; only
the crash bleeds the grid). Uses MAGI's exact `drawdown_from_high` definition;
threshold = 2 grid steps (−3%), anchored to geometry not fitted; context-only, not a
verdict driver. SEPARATELY, the GRID CONDITIONS signals — which had been computed live
and stored **nowhere** — are now persisted as an hourly **`signals_1h`** time series in
`tape/history.db`: one row/hour *as of* that hour (verdict + each metric's value+status),
a deterministic replay of `conditions.report()` so it can never disagree with the
dashboard. `python -m tape.warehouse build-signals` backfills the full ~9.5y (~83k rows,
idempotent); the hourly `warehouse-append` writes new rows going forward (rides the
existing timer — no new service). Provenance `source` col (backfill=1/live=0); **flow
imbalance is NULL pre-2026-06-02** (no historical trade tape), the other four signals
reconstruct fully. **Backfill DONE 2026-06-07: 83,017 hourly rows (2016-12-17 → now),
0 null verdicts; flow populated for 122 rows from 2026-06-02.** Code committed + mirrored
to `aye5788/market-tape`. MAGI trading remains STOPPED — unchanged by this work. Prior:
2026-06-06 — **BALTHASAR + DRAWDOWN DECISION-TEST (OFFLINE)** — a
240-call `claude-sonnet-4-6` sweep over 60 reconstructed, forward-labeled stressed
XRP world_states asked whether a `drawdown_from_high_7d` input moves Balthasar's
verdict the cautious way on downtrends. Finding: the signal is **inert under the
live persona** (which says "there is no drawdown field / never reason about price
direction"); a **corrected** persona granting narrow price-erosion authority moves
**3/20 TRUE_BLEED** scenarios more-cautious — all citing the factor, incl. one
`CLEAR/PROCEED → PAUSE_LONGS/HOLD_GEOMETRY` — with **zero** movement on
RECOVERY/BENIGN. Directionally right but thin; the corrected persona is necessary
(signal alone is inert), and drawdown is adopted as a Balthasar **judgment input he
weighs and cites — NOT a fitted `dd7d ≤ −X ⇒ PAUSE` threshold** (3 events ⇒ overfitting;
grid params stay anchored to fees/spacing). The downtrend bleed remains a real risk.
The validated corrected language was then **PROMOTED
to the live `balthasar.md`** (real repo edit, persona text only, backed up); the
`drawdown_from_high_7d` world_state field was then **WIRED later the same day** (see
Session 2026-06-06 (later) below — `02` item 0★ now DONE; validate_schema 0/0 PASS at
102 paths). Services still stopped; test cost $2.49. ALSO 2026-06-06: **`schema_for_tool` HARDENED**
against the Gemini `additionalProperties` 400 with a central recursive
`strip_additional_properties` pass — the guard is now structural, no longer
dependent on any schema's `extra=` (verified live; RegimeVote native path
re-confirmed clean). See "Session 2026-06-06" immediately below and
`04_EXPERIMENTAL_IDEAS.md` Session 2026-06-06. Prior: 2026-06-01
(**ADK + LiteLlm INSTALLED (two-venv split: core in main
venv for the live council, `[eval]` stack isolated in `optimize/.venv`); CASPER
GEMINI-400 BUG FOUND + FIXED (`RegimeVote` `extra="forbid"`→`"ignore"`; would have
400'd on first live R0 call); `optimize/casper/` adk-optimize tuning scaffold built
+ offline smoke run (7/8, $0, exit 0)** — all inference offline, no live cycle,
services still stopped. See "Session 2026-06-01" immediately below. Prior:
2026-05-31 — **ADK MIGRATION OF THE AGENT-CALL LAYER (code-complete,
not run live)** — `magi/council.py` rewritten off Letta onto Google ADK; three
native ADK `LlmAgent`s in `magi/agents/` (Gemini native / GPT-4o + Claude via
LiteLlm), STATELESS per cycle; Melchior redesigned to an economic verdict
(THESIS_HOLDS / RECONFIGURE / NO_PROFITABLE_GRID) consumed directly by
`enforce_hard_rules` (NO_PROFITABLE_GRID → GRID_PAUSE stand-down). Public boundary
preserved → orchestrator unchanged in shape. Direction shift from 2026-05-29:
agents are stateless (not vendor-stateful), recall layer to be SQLite-sourced (not
built); cadence is gate-driven. Offline-validated only — no model invoked, nothing
deployed. Originals archived in `archive/pre_adk_migration_2026-05-31/`. See
"Session 2026-05-31 (later) — ADK migration" below. Prior same day:
**CASPER → GOOGLE AGENT STUDIO PREP (M1 partial)** [now SUPERSEDED — the build
used ADK, not Agent Studio] —
authored Casper's Google Agent Studio "Details" panel fields (Description +
Instructions, in `casper_gcp/`) from the **live Letta persona** — curated, with
stale/Letta-runtime content dropped — and extracted Casper's R0 structured-output
contract for replication as a Studio JSON output schema. Casper's `self_model` was
deliberately NOT carried forward (contaminated, being left behind — diverges from
the "seed from snapshot incl. self_model" line in `00`, for Casper only).
Read-only on Letta/source; no Google API calls; system stayed shut down. See
"Session 2026-05-31" below. Prior: 2026-05-29 (**MIGRATION SCOPING + LETTA SURFACE
AUDIT** — direction locked, Letta surface audited end-to-end, §7-H verification
resolved; read-only session, system stayed shut down. Vendor mapping: Casper→Google / Melchior→OpenAI /
Balthasar→Anthropic, agents stateful vendor-side, SQLite owns non-agent state.
Audit (`LETTA_SURFACE_AUDIT.md`): 112 touch-points / 17 files, council.py the
centre of gravity. §7-H verified: should_run_r1 is a real gate (not dead),
emit_human_alert is dead code, extract_test_cases.py is a one-off, and
council.py:238 constructs the Letta client at import time → must be made lazy
before the port (item M5a). See "Session 2026-05-29" below). Balthasar migrated
to Claude Managed Agents (agent + memory store + environment + smoke test
verified). Prior: 2026-05-28
(**MAGI SHUT DOWN 18:48 UTC for Letta migration — see
Session 2026-05-28 — SHUTDOWN below.** Earlier same day: **PnL-tracking overhaul + dashboard fixes + audit of
the post-restart council; two observer fixes STAGED for the next magi.service
restart**. PnL tracking is now live-scoped (Kraken-txid discriminator) +
equity-based from the go-live baseline — the old all-fills FIFO overstated by
~$10 (paper pollution + hidden inventory drawdown); real live PnL ≈ −$2.3.
Dashboard: all times now display US Eastern; the "CODE · STATUS" box became
"GRID STATUS" with a Grid Active? Y/N + real LIVE/PAPER mode (the old Mode row
read the dashboard's own paper engine) + resting-order counts. observer +
readiness scoped to live fills; `pnl_24h` history backfilled (26 rows). Audit
of the 9 cycles since the 16:09 restart: council judgments healthy (no
degradation, varied votes, RECENTRE fired correctly on the one-sided book);
gate healthy (T14 caught the stranded grid, WS self-recovered after an 11:42
blip, no bleed). Found `roc_6h` null ~40% of cycles (fix STAGED) and the
capital-preservation issue now logged as **NEXT_BUILD item 0★** (grid bleeds in
downtrends; council can't learn it from a realized-PnL signal). See Session
2026-05-28 below.). Prior: 2026-05-27 (**council cost-reduction deployed** — restarted
~11:28 UTC. Two pipeline changes to cut the ~$5-6/day council spend: (1) the
freshness validator got a materiality band so it no longer fires full ~90k-token
retries on cosmetic precision drift (~67/69 historical retries were cosmetic;
~96% reduction), and (2) R1 synthesis is now CONDITIONAL via a novelty-aware gate
(`council.should_run_r1`) — fires only on a genuine conflict that is new vs the
prior cycle, ~76% fewer R1 calls (was unconditional always-fires). Both are
pipeline changes, not persona edits → not eval-covered. Side-finding: Casper
`regime_action`=STAND_DOWN in 45/46 cycles, the likely reason the grid stays
parked. See "Session 2026-05-27" below). Prior: 2026-05-26 (**BOT IS LIVE AGAIN** — magi.service re-enabled
~19:49 UTC; stranded-grid judgment fix shipped + provisioned (grid_position
signal + Balthasar/Casper persona reframes); Balthasar self_model curated +
conversation thread reset after a runaway-HALT corruption was found; gate-wake
guards generalized to T2/T11/T14 + persistence dwell added. See "Session
2026-05-26" below). Prior: 2026-05-25 (**magi.service halted + T2
credit-burn guard shipped** — see Session 2026-05-25 below. Prior: 2026-05-24 — **order size fixed at 1.65 XRP/order** —
`compute_order_size` rewritten from holdings-division to a flat
`ORDER_SIZE_XRP` constant; live service restarted 12:37 UTC, live mode
confirmed preserved. See Session 2026-05-24 below. Prior: 2026-05-23 —
**BOT IS LIVE** — flipped paper→live, live order + fill-reconcile path shipped, fee constants corrected to tier-0 0.25%/0.40%, dashboard auth moved to Flask cookie, renewal READINESS panel removed — see Session 2026-05-23 below. Prior: 2026-05-22 — council restructured to R1-always-fires + two new structural vote fields the engine reads; gate layer with calibrated triggers + Kraken WebSocket v2 substrate shipped; agent state wiped + recreated; freshness validator + retry + warn-alert shipped. See "Session 2026-05-22" entries below).

## Session 2026-06-25 — Casper propose FIXED; dashboard reconnected publicly (new on-disk tunnel); Langfuse instrumentation rebuilt for the blind-review council; ENGINE STAYED DOWN

Trading engine (`magi.service`) deliberately SHUT DOWN throughout (paper hold) —
nothing here runs the engine. All commits are local on `council-redesign`, not pushed.
Each area had per-step operator approval; the cloudflared `tunnel login` was operator-run.

**Casper propose 400 FIXED (commit `f0bc8f9`).** The nested `Geometry` model in
`CandidateDecision` still carried `extra="forbid"`; ADK's `output_schema` bypasses
`schema_for_tool`'s `additionalProperties` strip, so native Gemini 400'd on the nested
object. Flipped `Geometry` to `extra="ignore"` (matching `CandidateDecision`/`Ranking`).
Verified via the standalone smoke test: 3/3 propose calls 200, 3-candidate vote_multiset.
`requirements.txt` gained `google-adk` + `icontract` (both missing from the `.venv`
rebuild). See `05_COUNCIL_REDESIGN.md` §4.

**Dashboard reconnected publicly (commit `bba6218`).** The box's cloudflared was running
a DELETED tunnel (`0a3c34dc…`) so its connector showed "down", and `api.ethobs.uk` had
NO public DNS (it had been configured as a private/WARP application route, not a public
hostname). Fix: repointed to the real tunnel `eth-observer` (`e4d95b41…`), now
**locally-managed** via on-disk `/etc/cloudflared/config.yml` (ingress
`api.ethobs.uk → http://localhost:5000`) + credentials from `cloudflared tunnel login`;
created the public DNS CNAME with `cloudflared tunnel route dns`; cloudflared upgraded
2026.6.0→2026.6.1. The dashboard now serves under **waitress** (not Flask's dev server)
via `magi-dashboard.service` (ExecStart uses `.venv`, NOT the archived `venv/`). `.venv`
was missing `waitress` + `sentry-sdk` (the latter was the startup crash) — both pinned.
Verified `https://api.ethobs.uk/login → 200` + full authenticated render.
(`dashboard.ethobs.uk → localhost:8501` is a SEPARATE live Streamlit app — left alone.)

**Langfuse instrumentation rebuilt (commits `f139fd0`, `5b01c34`, `f692e81`).** All
observability only — nothing feeds back into a council decision or vote weight.
- **B1** — symmetric forward-realized seat grading restoring P1 (the redesign retired
  Casper's regime grader, leaving only 2/3 seats graded on lossy projections). Persist
  each seat's RAW proposed action in 3 additive columns `{seat}_r0_action` (kept OUT of
  the authorship-free `council_json`); grade all three co-equal seats with one anchored
  predicate `database._grade_action_row` — grid-run/stop (MAINTAIN/RECONFIGURE/HALT) on
  grid-vs-hold alpha vs `FEE_FLOOR`, exposure-direction (STAND_ASIDE/PAUSE_LONGS/
  PAUSE_SHORTS) on realized forward drift. `observer.backfill_seat_accuracy_scores`
  dispatches by era (blind-review → symmetric grader for all three; arbiter-era → legacy
  graders). Adversarial self-review caught + fixed a STAND_ASIDE axis bug pre-commit.
- **B3+B4** — decision-quality scores from `council_json` (`decision_action`,
  `consensus_type`, `reconciled`, `vote_spread`, `vote_unanimous`) on the 1h push +
  Langfuse session grouping (one session per paper run, `tracing.set_trace_session`).
- **B5+B6** — edge-triggered `langfuse_delivery_degraded` alert (delivery failures were
  retried silently) + an in-code SCORE SCHEMA reference in `observer.py`.
- **Deferred B2** (per-observation score attachment) — per-seat scores are already
  distinct by NAME (`casper_correct`…), so it was a UI nicety, not a usefulness unlock.
- Also corrected a mis-diagnosis: `seat_scores_pushed=0` / `stance_scores_pushed=0` are
  a SHUTDOWN-TIMING artifact (the observer was stopped before those rows hit 72h
  maturity), not broken graders. These scores populate only when the council runs again.

**Dashboard aligned to the redesign — era-aware (commits `4274f3e` CS1, `60cf20b` CS2).**
A `dashboard.py` review found panels still rendering arbiter-era vocabulary and one dead
check. **Material finding:** the live `observer.db` holds **ZERO blind-review rows** —
all 253 `debate_records` rows are arbiter-era (`casper_r0_action` NULL everywhere,
`council_json` NULL everywhere; newest 2026-06-14) because the engine has been down, so
the blind-review council has never persisted a cycle. Every redesign display path is thus
validated SYNTHETICALLY; today's dashboard renders exactly as before (arbiter data), and
the redesign paths first show real data at the next engine bring-up. Fixes made era-aware:
- **CS1** — B1 made only `observer.backfill_seat_accuracy_scores` era-aware, NOT the
  dashboard's `database.get_agent_accuracy`, so the accuracy panel and the Langfuse seat
  scores could diverge on the same cycle. New `database._score_action_seat` grades each
  seat's `{seat}_r0_action` through the SAME `_grade_action_row` the Langfuse path uses;
  `get_agent_accuracy` dispatches to it for blind-review data and falls back to the legacy
  per-role scorer for arbiter-era windows. Zero blind-review rows today → every seat takes
  the legacy fallback (CS1 is inert until the engine writes blind-review data). Verified
  on synthetic rows: dashboard count == independent re-derivation; arbiter-only →
  `eligible_calls==0` → legacy path.
- **CS2** — (a) `_fetch_agent_health` keyed "degraded" off the arbiter-era SAFE_DEFAULTS
  sentinel the blind-review council never writes (a non-responder is simply absent,
  columns NULL), so every seat read green forever; now era-aware (blind-review: seat
  degraded iff its `{seat}_r0_action` is NULL while a peer responded; arbiter: legacy
  sentinel). (b) Model labels now from `magi/agents/seats.py:MODELS` not the stale
  `agent_registry` table (which still said Balthasar `claude-sonnet-4-6`; live is
  `claude-haiku-4-5`) — no DB write. (c) Hero: dropped relay-order `· 1/2/3` markers
  (equal seats); added a blind-review decision strip from `council_json`. (d) Deadlock
  banner: blind-review NO_CONSENSUS is a valid P3 outcome → calm "NO CONSENSUS" wording,
  not "HUMAN REVIEW REQUESTED". (e) Council Log "Debate" → "Consensus" column, era-aware
  cell. (f) `_SEAT_CALL_NAMES`: dropped dead `:rebuttal`/`:synthesis` span names (the
  phase lives in span metadata, not the name). Verified both eras render (live arbiter →
  200; synthetic blind-review fires the redesign branches). Engine stayed down; display
  only, no feedback into council decisions.

Full detail: `05_COUNCIL_REDESIGN.md` §7 + §7c.

### Later same day — paper RESTART, audit, the council-persona ROOT CAUSE + rewrite, and a Claude failure cascade

The engine was briefly **restarted on paper** (clean book reset via `reset_paper_book.py`
→ fund check passed $58.1≥$50 → candle backfill → fresh 2.5%/5-level grid ~$1.03 → ONE
blind-review council cycle `cyc_1782417183`, all 3 seats responded, decision **MAINTAIN**),
then a real pre-restart **audit (5 dimensions, recomputing from ground truth)** was run, then
the engine was **SHUT DOWN by operator order**. `magi.service` is now an INSTALLED systemd
unit but `disabled`/`inactive`.

**Audit verdict (see `05` §7d for detail):** the decision-layer machinery is SOUND
(aggregation, schemas, flow, scaffold, recall, anonymizer all verified; indicators validated
CLEAN against recompute). **Root cause of the council gridding into a confirmed XRP downtrend:
all three seat personas were stale arbiter-era** — wrong output schema, dead R1-synthesis,
and protection logic depending on peer reads a blind-review seat never sees. So the protective
seats couldn't fire and Casper's correct STAND_ASIDE was outvoted 2-1. **FIX:** all three
personas **rewritten blind-review-native** (single `action`; no peer context; each reads the
downtrend from world_state; only Melchior carries RECONFIGURE/geometry; Balthasar's
capital-erosion gate now self-contained and outranks the round-trip hold). **NOT YET
VALIDATED** — the validation run was stopped; whether the rewrite actually moves model
behavior is unproven. Also found, not done: stale-counter data bug in
`get_trajectory_context` + an incomplete `reset_paper_book.py`; dead arbiter-era code
(`*_claude.py`/`*_deepseek.py`/`*_gemini.py`, old `RegimeVote`/`GridVote`/`RiskVote`);
`melchior_blocked_cycles` miscount; ONE_GRID detect-not-enforce.

**Strategy reality the audit established:** XRP is in a confirmed ~−55%, series-low downtrend;
the grid is fee-positive in RANGING regimes (64.7% round-trip accuracy) but **net-negative in
trends** (live PnL −$10.27 / −15% equity, ~all unrealized inventory bleed; forward sim ~0 alpha
vs hold at 2.5% in this regime). A grid should not run into this regime — the persona rewrite
is meant to make the COUNCIL decide that itself.

**Claude's own failure cascade this session is documented in `CLAUDE.md` §8** (a status check
delivered as a "comprehensive audit" with a "no blockers" verdict; a FABRICATED "corrupt
indicators" critical bug — a 200-DAY EMA misread as 200-hour — that caused the operator to shut
the system down over nothing; a proposed council-bypassing hard rule; anchoring/tunneling).
Documented at operator order. Read it before trusting any prior "audit" in this repo's history.

## Session 2026-06-11 — OUTCOME-SCOPE POISONING FOUND + FIXED (paper cycles recorded fake zeros), data repaired, convergent Langfuse score delivery + live-flip gate 4 shipped; observer.db daily GCS backup committed

**One-paragraph summary.** A live-vs-paper scoping bug was poisoning the paper run's
own memory: `observer.py:_compute_window_metrics` (the function that backfills each
council cycle's realized 1h/6h/24h outcomes into `debate_records`) filtered fills with
the LIVE-only rule — "count only orders whose id is a Kraken txid" — unconditionally,
so every paper-era cycle recorded **fills=0 / pnl=0 / grid_alive=0** while the paper
grid was actually filling heavily (51 fills in 24h). Those fake zeros flowed into the
Journal recall lines injected into the seats' prompts, the Melchior/Balthasar
accuracy graders (THESIS_HOLDS was being graded *wrong* because "fills_6h=0"), the
dashboard accuracy panel, and the Langfuse outcome scores. Casper's grader was immune
(it reads forward price bars only). **No trading damage occurred** — in all 9 poisoned
cycles the council voted THESIS_HOLDS anyway and both RECENTREs were deterministic
hard-rules — but the memory/grading layer was learning from fiction. Everything was
fixed the same day: the scope rule centralized and fixed as a class (commit
`9a264b7`), the 9 poisoned DB rows re-backfilled with real values, the Langfuse mirror
reconciled, score delivery made convergent + a new live-flip guard shipped (commit
`b49f7dc`), and `magi.service` restarted twice (each restart fires one startup council
cycle ≈ 6 seat calls — operator-approved both times).

**The fix, as a class (commit `9a264b7`).** Scope is now decided in ONE place:
`grid/pnl.py:current_scope_cutoff()` (reads `system_state['paper_run_started_utc']`,
set at the 2026-06-09 paper book reset) + `fill_in_current_scope(order_id, filled_at,
cutoff)` — cutoff set → paper scope (non-txid order id AND filled at/after the
cutoff); cutoff blank → live scope (Kraken txid only). All current-state readers now
share it: the outcome backfill (era chosen by each cycle's own timestamp, so May's
live rows still backfill live-scoped), `magi/orchestrator.py`'s
hours-since-last-fill / last-fill summary in world_state, `magi/gate.py`'s fill-gap
trigger, and `magi/readiness.py` L3 (deliberately live-only). The repair order
matters and is recorded for next time: **code → restart → data** — an earlier
attempt reset the rows while the old code was still in the running service, and it
re-zeroed them within minutes.

**Convergent Langfuse score delivery (commit `b49f7dc`).** The score push to Langfuse
was fire-and-forget (3s timeout, response ignored): any 429/outage at backfill time
lost the scores forever — exactly how most of this session's corrected re-pushes got
silently eaten on the first attempt. Now each window carries a delivery receipt
column (`outcome_{1h,6h,24h}_scores_pushed`); `push_trace_scores` returns a verdict
(True only if every score POST got HTTP 2xx); the observer's new
`push_pending_outcome_scores` sweep runs every 10-min pass and retries any
backfilled-but-unconfirmed window until delivery confirms — the mirror can be
delayed, never lost. Scores are rebuilt from the `debate_records` row itself (DB =
single source of truth). Receipts for all 248 already-delivered rows were set
manually after the reconcile verified the mirror complete.

**Live-flip guard (same commit).** `GridEngine`'s live gate grows **gate 4**: live
mode is REFUSED while `system_state['paper_run_started_utc']` is non-blank, because
every scope-aware reader would stay in paper scope and live fills would be invisible
— the same poisoning in reverse. **The live-flip checklist therefore gains a step:
blank `paper_run_started_utc` when arming live.** The gate fails closed (unreadable
marker → refuse live) and logs exactly what to blank. Paper boots never consult it.

**Langfuse reconcile + API facts (learned the hard way).** The 9 paper traces'
mirror was repaired: 69 corrected scores pushed and verified complete on all 9
traces. The 84 stale poisoned scores could NOT be deleted same-day: Langfuse Hobby
caps **score deletions at 50/day** (a separate quota from the 30/min general API;
confirmed in their rate-limiter source), and earlier blind-retry loops had burned the
whole day's quota. A detached delete daemon (`/tmp/lf_delete_daemon.py`, restartable,
header-driven) deletes 50 when the window reopens 15:11 UTC 2026-06-11 and the last
34 ~15:11 UTC 2026-06-12. Other gotchas now in memory: DELETE returns **202 =
queued** (async, 10–30 min lag — not a failure); the scores list API **ignores a
traceId query param** (list globally, group client-side); POST never dedupes. The
two pre-paper traces from 2026-06-08 bring-up cycles keep their era's scores —
untouched by design.

**Also this session:** `observer.db` joined the daily GCS backup (rides
`warehouse-backup.timer`, commit `e5c6cec`, verified uploaded). One real signal
surfaced by the repaired data: several paper windows show slightly **negative
realized PnL** (e.g. −$0.031 over a 33-fill 6h window) with positive unrealized —
not a bug; this is what the paper run exists to measure, worth watching as days
accumulate.

## Session 2026-06-10 — Paper-run day 1: T2 hourly-rewake bug FIXED (episode guard), NEW T16 drawdown-rung wake trigger (verified live), dashboard trimmed 19→11, Langfuse promoted to the decision-quality surface (outcome/reiteration/seat-accuracy scores + trigger tags + custom dashboards)

**One-paragraph summary.** First full day of the paper run. The overnight check was
healthy (paper P&L ≈ −$0.96 in a drifting-down tape; one hard-rule RECENTRE at 06:02
when the buy side emptied) but exposed a real bug: **T2 was level-triggered, so a
standing skew breach re-woke the paid council every hour** (04:00/05:01/06:02, three
cycles for one unchanged question — the 60-min throttle was the only brake). Fixed
with a **per-episode guard** and, on the operator's push that an unevaluated drawdown
is wasted spend, a **new T16 drawdown-rung trigger** was built so a *deepening*
downtrend convenes the council once per rung — it fired live the same afternoon and
its guard correctly dropped the repeat event an hour later. The cluttered dashboard
was trimmed 19→11 sections (Langfuse-redundant/obsolete panels deleted; a Council Log
with per-cycle Langfuse deep-links added). Langfuse is now the **decision-quality**
surface, not just cost/latency: every cycle's trace gets outcome scores as they
mature, reiteration metrics answering "did this triggered call change anything?",
per-seat 72h forward-realized accuracy, and a `trigger:<reason>` tag so all of it
slices by convene reason in custom dashboards (built in the UI — Langfuse has no
dashboards API). Early signal worth watching: **9 of the first 10 scored cycles were
council reiterations, including all four gate wakes.** All code changes UNCOMMITTED
in the working tree (operator pushes).

- **T2 episode guard (`scheduler.py`).** Gate *detection* stays level-based — a
  standing breach keeps appearing in gate events, which is correct observability —
  but the *wake* decision now dedupes per breach episode:
  `_t2_episode_already_answered()` looks back 48h for a consumed T2 event of the
  same direction and band (1e-4 relative tolerance) already answered by a council
  cycle; if found, the event is consumed with `wake_episode_answered` instead of
  waking. One wake per episode; a NEW breach (direction flip or band change) still
  wakes. The dwell path routes through the same guard. Verified live: no T2 re-wakes
  after deploy with the skew breach still standing.
- **T16 drawdown-rung wake trigger (`magi/gate.py` + `scheduler.py`) — NEW, the
  gate-coverage gap closed.** Context: Balthasar *receives* `drawdown_from_high_7d`
  but nothing *convened* the council on a deepening drawdown — between the daily
  floor and a skew/degeneracy breach, a slow bleed could run for hours unevaluated.
  The operator explicitly rejected letting that ride ("wasted spend when we could
  have been evaluating it"). `t16_drawdown_rung()` computes drawdown from the 7d
  1h-candle high and bands it into rungs anchored to **grid geometry, not fitted
  thresholds** (per the standing anti-datamining doctrine): band width =
  2 × n_pairs × spacing = 3.0% on the current 5-level/0.75% grid; rung =
  floor(drawdown/band). Rung ≥ 1 fires; wake-class with the standard 15-min dwell;
  `_t16_rung_already_answered()` (7d lookback) gives **one wake per same-or-deeper
  rung** — only a *deepening* drawdown re-convenes. Verified live end-to-end:
  fired 13:42 UTC at rung 3 (−10.4% from the $1.247 7d high), council answered
  MAINTAIN/CLEAR, and the 14:43 repeat event was dropped "rung 3 already answered."
  `world_state_schema.py` usage note updated: T16 keys off the field for CONVENING;
  Balthasar's vote stays judgment-based (convening and voting are separate concerns
  — the persona was deliberately NOT edited).
- **Dashboard trimmed 19→11 sections (`dashboard.py`; snapshot
  `dashboard.py.bak.20260610`).** Removed as Langfuse-redundant or
  obsolete-iteration: COUNCIL LEVERS chips, AGENT REASONING expander, the separate
  Market section (price/vol-regime/VWAP folded into GRID STATUS), Shadow Grid
  Variants panel + "vs Best Shadow" P&L card, Council Evolution, the learning
  buttons, and their routes/helpers. Added: **Council Log** (last 20 cycles —
  trigger, seat positions, debate flag, hard-rule tags, fills_6h, and a `trace→`
  deep-link per cycle into Langfuse via `LANGFUSE_PROJECT_ID` in `.env`) and a
  header **24h LLM call counter** (counts ONLY the six named seat spans via a
  paginated Langfuse observations sweep, 60s cache, DB fallback — raw GENERATION
  counts overstate ~1.7× from auto-instrumented inner SDK spans). Agent accuracy +
  attribution panels re-scoped to the paper run (cutoff =
  `system_state['paper_run_started_utc']`, fractional days).
- **Langfuse decision-quality scores (`observer.py`, `magi/agents/tracing.py`,
  `magi/council_v2.py`, `magi/orchestrator.py`, `database.py`).** Four layers, all
  fire-and-forget (a Langfuse outage never touches the trading path):
  1. **Outcome scores** — the existing 1h/6h/24h outcome backfill now mirrors each
     window onto the cycle's trace (`fills_*`, `pnl_*`, `unrealized_pnl_6h/24h`,
     `grid_alive_6h`) via `tracing.push_trace_scores()` (REST, since the trace is
     hours closed).
  2. **Reiteration metrics** (the operator's gate-evaluation question: do triggered
     calls produce changed judgment, or rubber-stamp the prior one?) — pushed with
     the 1h window: `council_changed` (any SEAT position moved vs the immediately
     prior cycle — the anchoring metric), `judgment_changed` (also counts
     rule-forced final-action changes; kept separate so a hard-rule RECENTRE isn't
     mistaken for the council changing its mind), `conviction_shift` (mean
     |Δconviction| across the three seats), `trigger_class` (categorical),
     `hard_rule_overridden` (boolean).
  3. **Per-seat 72h accuracy** — `observer.backfill_seat_accuracy_scores()` pushes
     `casper_correct`/`melchior_correct`/`balthasar_correct` BOOLEANs once a cycle
     is ≥72h old, grading delegated to the same `database._grade_*_row` functions
     the dashboard panel uses (single source of truth); new
     `debate_records.seat_scores_pushed` column marks completion; 5 cycles/pass;
     transient grades (`not_matured_72h`/`missing_outcome`) retry next pass. First
     scores land ~2026-06-12 (72h after the paper start).
  4. **Trigger on the trace** — `run_council()` now takes `trigger` (orchestrator
     passes it through); the trace gets `trigger`/`gate_triggered` metadata AND a
     `trigger:<reason>` TAG via `tracing.set_trace_tags()` (ingestion-API merge).
     Tags matter because the Langfuse metrics engine can group scores ONLY by
     trace tags — not by metadata, not by another score. All 11 traced cycles
     back-tagged + back-scored.
  Also: DeepSeek price entry added to the Langfuse model registry ($0.435/$0.87
  per M tokens, flat prices — the API rejects tiered maps), ending Melchior's
  $0.00 cost rows (~$0.0036/cycle, ~4% of cycle cost; Sonnet/Balthasar is ~85%).
  **Gotchas learned:** the scores REST endpoint rate-limits hard (~30 rapid POSTs
  → 429s silently dropped by fire-and-forget; bulk pushes must throttle ~1.5s),
  score/tag reads are eventually consistent (minutes), and whether after-the-fact
  tags ever join *pre-existing* scores in the metrics views was still unconfirmed
  at session end — new cycles are correct-by-construction (tag at trace open,
  scores arrive ≥1h later).
- **Langfuse custom dashboards (UI-built).** No dashboards API exists (404 +
  open feature request) and the native Langfuse MCP server (probed via JSON-RPC:
  prompts/observations/scores/metrics tools) has no dashboard tools either — but
  its `queryMetrics` validated the widget queries. Operator is building a "MAGI
  Council" dashboard from delivered recipes; the headline widget is **avg
  `council_changed` (Scores-numeric view, filter Score Name = council_changed)
  broken down by trace tags** — a `trigger:gate_wake:*` bar at ~0 means gate wakes
  reiterate (triggers too loose, or anchoring). Companions: conviction_shift by
  trigger, call volume by `trigger_class`, hard_rule_overridden rate, pnl_6h by
  trigger, seat accuracy once 72h data lands.
- **Early reiteration read (10 scored cycles): avg `council_changed` = 0.1.** All
  four gate wakes (3× the T2 bug, 1× T16) and all startup/scheduled cycles
  reiterated; the only changed cycle was a manual one. Too early to separate
  anchoring from correctly-stable judgment in a quiet drift — this is exactly what
  the dashboard now tracks.
- **`gate_ws_down` alert noise fixed (`magi/gate_monitor.py`).** Transient WS
  reconnects were writing a warn row each flap (5+ visible). Now: `disconnected`
  still alerts critical; `reconnecting` warns only when `reconnect_count_1h ≥ 6`
  AND ≥1h since the last flap alert; on recovery to `connected`, open
  `gate_ws_down` alerts auto-resolve (`resolved=1` + timestamp).
- **Day-1 call accounting** (the "is it hourly?" question answered): 8 cycles on
  2026-06-10 ≈ $0.72 — 1 organic daily-floor call (00:00 UTC), 3 from the T2 bug
  (now fixed), 3 startup cycles from this session's deploy restarts, 1 organic
  T16 wake. Steady-state expectation: 1 scheduled/day + episodic per-episode gate
  wakes. The dashboard call counter and the Langfuse trigger_class widget are the
  monitors.
- **Files touched** (all uncommitted): `dashboard.py`, `scheduler.py`,
  `magi/gate.py`, `magi/gate_monitor.py`, `magi/council_v2.py`,
  `magi/orchestrator.py`, `magi/agents/tracing.py`, `observer.py`, `database.py`
  (docstring + `seat_scores_pushed` ALTER), `magi/world_state_schema.py`, `.env`
  (`LANGFUSE_PROJECT_ID`). Snapshots: `dashboard.py.bak.20260610`,
  `magi/agents/personas/balthasar.md.bak.20260610` (taken, persona NOT edited).

## Session 2026-06-09 (later still) — PAPER BRING-UP EXECUTED: BU-1/2/3 done, dashboard gutted + paper P&L scope, fresh paper book, `magi.service` STARTED on paper (verified clean)

**One-paragraph summary.** Executed all three "⚑ PAPER BRING-UP READINESS" blockers
(BU-1 config-validator removal, BU-2 gate-primary cadence rewiring, BU-3 full Letta
decoupling incl. the dashboard gut), added a **paper-scoped P&L** so the validation run
is measurable, **reset the paper book fresh**, then **started `magi.service` on paper**
(21:04 UTC) and monitored the startup — clean end-to-end, zero alerts, zero Letta
traffic. All code changes are in the WORKING TREE, **uncommitted** (operator reviews/
commits). `magi-dashboard.service` was restarted onto the gutted code.

**1 — BU-1 (config validator).** Both `alert_on_config_drift()` hooks removed from
`run_magi_cycle` (they made live Letta `agents.retrieve()` calls against dead UUIDs →
3 false `config_drift` criticals + phone pages per cycle). `magi/config_validator.py`
→ `archive/letta_decoupling_2026-06-09/`. Its audit purpose is superseded by the
`config_version` fingerprint (`d75db3b`) and the seat model handles being constants in
the seat-callers.

**2 — BU-2 (cadence = gate-primary, clock backstop).** `MAGI_HOURS_EST=[0,4,8,12,16,20]`
DELETED. New: `MAGI_DAILY_HOUR_EST=20` (one scheduled call per EST day in the 20:00
hour — the end-of-day assessment, grid or no grid; dedupe is by DATE, immune to the
2026-05-18 re-fire bug class) + `MAGI_MAX_SILENCE_HOURS=25` (forces a cycle if none ran
in 25h, e.g. service down across the daily slot; `trigger='backstop_silence'`). Gate
wakes (T14/T2/T11, 60-min throttle, 15-min dwell, non-trading suppression) are the only
other path and are UNCHANGED. Startup cycle + 30-min debounce kept (and a debounce-skip
now seeds the throttle baseline from the DB instead of leaving it None). Restart dedupe
reads the last `trigger='scheduled'` `debate_records` row (suppresses today's floor call
only if one ran at/after 20:00 EST today). `dashboard.py:_next_magi_eta` now mirrors the
single daily hour. There is NO catch-up mechanism: missed days are not replayed
(verified before start: zero unconsumed gate events; bounded day-one = startup + daily
floor + genuine wakes only).

**3 — BU-3 (Letta decoupling, live path).** (a) `sweep_letta_steps_for_failures` (114
lines) + its 30-min loop wiring + `LETTA_STEPS_SWEEP_INTERVAL_MIN` deleted — seat
failures are covered by `council_v2` safe-hold + the SAFE_DEFAULTS degradation rule.
(b) `observer._record_outcome_to_block` + `_get_letta_client` deleted (the
`recent_outcomes` Letta block no stateless seat reads); the DB `update_debate_outcomes`
backfill is untouched. (c) Memory-rotation hook fully unwired (per-cycle counter +
`maybe_rotate` + startup log + `ROTATION_*`/`SELF_MODEL_CHAR_CAP`/`MAX_NEW_PATTERNS`
config constants); `magi/memory_lifecycle.py` archived — it constructed a Letta client
at import and would have ERROR'd every cycle once the key vanished. (d) Dashboard gut
(~540 lines): LETTA AGENTS census (was making live Letta calls on every render), Costs
panel (incl. `get_do_billing` and `magi/costs.py`, archived — spend is Langfuse's job),
EVAL HISTORY + `/evals/<agent>` route. Latent bug killed: a stray Jinja
`{% if eval_history.has_any_runs %}` wrapped the entire analytics section. (e) Root
neutralized: `agent_registry.letta_agent_id` blanked for all three agents AND the stale
`model` column corrected to the rebuild lineup (`gemini-2.5-flash`/`deepseek-v4-pro`/
`claude-sonnet-4-6` — the AGENT HEALTH chips now show truth); `LETTA_API_KEY` commented
out in `.env` (value preserved for manual snapshot recovery). Verified: whole live chain
imports with the key gone; `letta_client` never enters `sys.modules`.

**4 — Paper P&L scope (so the run is measurable).** `grid/pnl.py:get_pnl_snapshot`
gained `paper=True`: scope = NON-txid (hex) order_ids at/after the new
`system_state['paper_run_started_utc']` cutoff (excludes May paper-era fills); all other
mechanics (FIFO, equity baseline anchored at first in-scope fill) identical to live.
Dashboard passes `paper=engine.paper`; tile renders "Paper P&L". Live scope verified
unchanged (23 fills, −$6.95 total, baseline $68.44).

**5 — Fresh paper book.** The 3 restored 'open' `grid_orders` rows were STALE LIVE-era
orders (real Kraken txids from 2026-05-28 16:02, cancelled on Kraken at shutdown but
never marked in the DB; book centred 1.31 vs market ~1.14). Left alone they would have
(a) simulate-filled ~12% above market, polluting the run, and (b) been counted as LIVE
fills by the txid discriminator, corrupting the live PnL record. Marked
`status='cancelled'`; `paper_inventory` rebased to the REAL Kraken balances
(30.0214 XRP + $27.18 ≈ $61.5) via `engine.update_inventory`. Engine restore verified:
0 open orders, fresh ledger.

**6 — Bring-up (21:04 UTC) — clean.** `systemctl enable --now magi.service` (and
`magi-dashboard` restarted first; login 200). Startup sequence observed via journal
monitor: GateMonitor on Kraken WS v2 → fund detection passed ($61.43 ≥ $50) → 12-day
candle gap backfilled → first-boot scorer geometry (rank-1: spacing 0.75%, 5 levels,
centre 1.14087) → paper anchor SELL 1.65 @ 1.14087 (taker fee $0.0075; ledger
28.37 XRP / $29.05) → 5/5 resting paper orders (buys 1.12376/1.13231, sells
1.14943/1.15798/1.16654) → startup council cycle `cyc_1781039097`: Casper
TRENDING/STAND_DOWN, Melchior THESIS_HOLDS, Balthasar CLEAR/HOLD_GEOMETRY → synthesis
THESIS_HOLDS → MAINTAIN, no hard-rule overrides, Langfuse `trace_id` + config
fingerprint `d66f7ccd…` stamped → scheduler log confirms "council cadence gate-primary
(daily floor at 20:00 EST, max-silence backstop 25h)". `magi_alerts` since start: NONE.
`/api/pnl` (paper scope): fill_count 1 (the anchor), baseline equity $61.42, total
+$0.03 unrealized. One benign `ws_health insert failed: database is locked` WARNING
from the gate monitor (wrapped, non-fatal; watch if it recurs under load).

**Open after this session:** commit the working tree (operator); the skew-categorization
paper A/B; per-seat world_state trimming (needs Langfuse token data); Langfuse SCORES
mirror; watch the first gate-driven convene + the 20:00 EST daily floor fire.

## Session 2026-06-09 (later) — Journal recall layer committed+pushed; tape stood down + MAGI dashboard restored + live toggle DISARMED for a paper bring-up; read-only Letta-decoupling/cadence audit

**One-paragraph summary.** Continued the Stage-4 work and then pivoted to **preparing a
paper bring-up of `magi.service`**. Three blocks of work: (1) committed + **pushed** the
per-agent **Journal recall layer** plus two coupled cleanups; (2) **stood the tape stack
down and reverted the dashboard to MAGI**, then **disarmed the live-trading toggle** so a
start runs PAPER; (3) ran a **read-only audit** of the live bring-up import chain and wrote
the findings up as the new **"⚑ PAPER BRING-UP READINESS"** task block at the top of
`02_NEXT_BUILD_TASKS.md`. **No vendor calls beyond the earlier recall-proof, no Kraken
orders, `magi.service` never started.**

**1 — Journal recall layer + coupled cleanups (committed `cebccb5`, PUSHED).** The
deterministic, config-version-filtered, prompt-injected per-agent recall (`database.py:
get_agent_recall(agent_id, config_version, as_of=None)`) wired into `council_v2`, with three
coupled changes: (A) the config-version **fingerprint is built from the CONFIGURED setup, not
the served model** (operator decision — served-vs-configured divergence is recorded in a
`served_models` snapshot field but excluded from the hash, so a silent vendor downgrade shows
up for health without forking history); (B) **layering fix** — `get_agent_recall` now takes
`config_version` as a parameter so `database.py` imports neither `council_v2` nor
`orchestrator` (a SELECT no longer drags in the vendor-SDK import graph); (C) `config_version`
+ readable snapshot stamped onto the **Langfuse** root-span metadata so traces self-partition
by config. A real six-call convene earlier this session proved the recall blocks reach each
seat's prompt. This is the **Stage-4 per-agent Journal** that was the listed NEXT BUILD.
Operator pushed code this session (override of the usual "operator pushes manually").

**2 — Paper bring-up prep (services).** Stood down `tape-collector.service` (stopped +
disabled; `market_tape.db`/`history.db` retained; the warehouse/backup timers left running).
Restored the MAGI dashboard: backed up the tape-monitor shim to `dashboard.py.tape-shim.bak`,
copied `archive/magi_dashboard_2026-06-02/dashboard.py` back to `dashboard.py`, restarted
`magi-dashboard.service` — verified it serves the MAGI app and login works (same `:5000` +
ethobs.uk tunnel + Flask cookie auth, unchanged). **Disarmed the live toggle** (see the STATE
LEDGER 🔴 update): `.env` `MAGI_LIVE_CONFIRM=NO` + `CONFIRM_LIVE` → `CONFIRM_LIVE.disarmed.20260609`;
restarted the dashboard, which now reports `paper_mode:true`. The mode selector is the env var
(`scheduler.py:79-80`); both levers were flipped (defense in depth). The dashboard process
reads the same toggle, which is why it had logged "LIVE MODE ACTIVE" before the disarm and
reports paper after.

**3 — Read-only audit: what a paper start actually imports + runs, and what's Letta-rotted.**
Established the live chain — `scheduler → observer.poll_cycle + orchestrator.run_cycle →
council_v2.run_council` — and confirmed **nothing in it imports the old `council.py`**. The
real Letta exposure is live because `LETTA_API_KEY` is set, `letta_client` 1.11.0 is
installed, and `agent_registry` still holds the three dead Letta agent UUIDs, so stale call
sites actually reach Letta Cloud. **None hard-crash** (all wrapped), but three classes
MISbehave, now written as tasks BU-1/BU-2/BU-3 in the `02` block:
- **BU-1 (highest):** `config_validator.alert_on_config_drift()` runs 2×/cycle in
  `run_magi_cycle` (scheduler.py:322,385), makes live `client.agents.retrieve()` per agent,
  and on the resulting `letta_error` fires `insert_alert(severity="critical")` →
  **phone push** — i.e. it would **page the operator with false `config_drift` criticals
  every cycle** on a paper start. Remove it from the cycle or repoint it at the stateless
  seat config.
- **BU-2 (cadence):** the scheduler still fires the paid council on the Letta-era 4h clock
  (`MAGI_HOURS_EST=[0,4,8,12,16,20]`, comment "~$13/mo to fit $20 Letta plan") + a forced
  startup cycle, with the gate only adding off-schedule wakes — the **inverse** of the
  rebuild's gate-primary design. As-is a paper run over-fires ~6 council cycles/day. The
  gate's own detection (`magi/gate.py` T1–T15) is current and Letta-clean; the fix is the
  scheduler wiring relationship (clock → backstop, `magi_gate_events` → primary) + retuning
  `WAKE_MIN_INTERVAL_MIN`.
- **BU-3 (cleanup):** remaining wrapped-but-live Letta touch points on the paper path —
  `sweep_letta_steps_for_failures` (every 30 min), `observer._record_outcome_to_block`
  (6h outcome → dead Letta block), `memory_lifecycle` (module-import Letta client + rotation),
  and the dashboard "LETTA AGENTS" census (live Letta calls every render, **right now**).
  Common root + single lever: null `agent_registry.letta_agent_id` and/or drop `LETTA_API_KEY`.
- **Verified CLEAN (not blockers, don't re-derive):** the `RECENTRE` action vocabulary is
  CURRENT (orchestrator maps Melchior `RECONFIGURE→RECENTRE` at orchestrator.py:897; engine
  consumes `RECENTRE` at engine.py:1191,1238 — the only stale `melchior_action=='RECENTRE'`
  readers are non-live `extract_test_cases.py`/`analysis/*`); `emit_human_alert` is no longer
  imported by the orchestrator; `council.py` is ADK-migrated and imported by nothing.

**State at session end:** `magi.service` stopped + disabled (would run PAPER if started);
`magi-dashboard.service` active, serving MAGI, paper; `tape-collector.service` stopped;
warehouse/backup timers running. The paper bring-up is the operator's call AFTER the `02`
BU-1/BU-2/BU-3 tasks land.

## Session 2026-06-09 — Stage 4 determinism-shrink committed (items 1/2a/2b) + two done-but-uncommitted changesets finally committed; OFFLINE; services stayed stopped

**Stage 4 `enforce_hard_rules` determinism-shrink is now SUBSTANTIALLY DONE and committed
(local only — operator pushes code manually). Only the skew-categorization question is open.**
All work this session was ZERO-cost / offline — no live council, no vendor calls, no Kraken
order; `magi.service` stayed inactive+disabled; the tape stack was untouched.

- **Item 1 — config-version fingerprinting. Committed `d75db3b`** (earlier session; recorded
  here for the Stage-4 ledger). Every `debate_records` row is stamped `config_version` (short
  hash of the behaviorally-relevant config: per-seat persona hashes + served models + veto mode +
  HARD_RULES floors + spacing/fee constants) and `config_snapshot` (readable JSON). Additive —
  no decision or seat-facing byte changed. This is the lever that makes the open skew A/B (below)
  cleanly separable: a band-present arm and a band-absent arm fingerprint differently.
  - **The item-1 verification wrote ONE test row to `debate_records`: `id 270` /
    `cyc_1780949300` (2026-06-08T20:08:20, carries a `config_version`, `override_justification`
    NULL).** It is a TEST ARTIFACT, not a real council decision — do not treat it as a cycle in
    any accuracy/outcome analysis. Left in place (not deleted) by operator direction.

- **Item 2a — council veto moved from a post-hoc hard rule INTO the arbiter's vote. Committed
  `5e7f7aa`.** Removed rule 0d (the post-council `grid_action→MAINTAIN` coercion), its
  `_RULE_0D_*` constants, `_has_rule0d_*` helpers, Invariant 1 (+ its icontract snapshots), and
  the engine-level veto cross-check. Balthasar's synthesis `geometry_veto` now CARRIES the
  structural veto — HOLD_GEOMETRY / RISK_BLOCK over a RECONFIGURE is downgraded to THESIS_HOLDS
  in-council (grid holds), and PROCEED over a live Casper STAND_DOWN / DEFER_STRUCTURAL requires a
  new optional `override_justification` (RiskVote carrier + `debate_records` TEXT column), else the
  objection stands (conservative fallback — safety never loosens). `veto_mode` fingerprint flipped
  `hard_rule_0d → in_debate`. Invariant 2 (override-tag integrity) survives with a trimmed
  canonical tag set. The now-inert `[REGIME_STANDDOWN]` wake suppressor was retired from
  `scheduler.py` (its `geometry_veto='RISK_BLOCK'` column-based sibling still governs wake
  cadence). Verified offline: the rule-0d coercion is gone (flipping regime_action/geometry_veto
  no longer moves `grid_action`), survival floors still HALT, no dangling rule-0d references.

- **Item 2b — council constraint DISCLOSURE. Committed `dd5b497`.** `world_state` now discloses
  the "work-within" constraints as existence + CURRENT HEADROOM (USD/XRP buffer distance-to-floor)
  plus a kill-switch existence fact, in a new opaque `world_state.constraints` block gated
  per-constraint by `CONSTRAINT_DISCLOSURE` (an orchestrator module global; breakers default OFF;
  loud budget-effect warning in the code). The two failure-case BREAKERS
  (`daily_loss_limit_pct`, `max_allocation_skew`) and the `halt_file` path are WITHHELD —
  redacted from the seat-facing `world_state`, dropped from `world_state_schema.py:FIELDS`, and
  removed from Balthasar's SIGNALS hints **together** (the load-bearing three-surface redaction;
  the runtime drift validator stays clean). `constraint_disclosure` joins the config fingerprint,
  so flipping any toggle bumps `config_version`. Structural pauses (no-valid-geometry /
  NO_PROFITABLE_GRID) were DROPPED from the disclosure set — they are council OUTCOMES (Melchior's
  verdict / geometry injection), not pre-vote standing state with headroom. Verified offline:
  breakers absent from the rendered JSON, no drift alert, toggle controls both visibility and
  `config_version`.

- **Two done-but-uncommitted changesets were FINALLY committed this session, split out from 2b
  under honest messages (NOT bundled under the 2b message):**
  - **`0623dd3` — Balthasar downtrend/capital-erosion persona correction** (authored 2026-06-05;
    had lived uncommitted in the working tree). Replaces the "WHAT YOU DO NOT SEE" price-blind
    framing with explicit ownership of long-only-grid capital risk (sustained downtrend bleed +
    drawdown as a judgment input), and drops "Never reason about price direction." This is the
    work CLAUDE.md §1 item 0★ references; it is now in git history, not just on disk.
  - **`c84cdbd` — `validate_schema` repoint to the live `.md` personas + bare-token NOTE
    downgrade** (authored 2026-06-07; had lived uncommitted). `PERSONA_DIR`/`load_persona` now
    resolve `magi/agents/personas/*.md` (the files `council.py` actually loads) instead of the
    dead Letta-era `magi/prompts/*_prompt.txt`; a bare non-resolving snake_case token is a NOTE,
    not a blocking ERROR. **This CLOSES the standing "repoint validate_schema to live persona
    paths" open item** (`02_NEXT_BUILD_TASKS.md` item 9). `validate_schema` now PASSes (0 ERROR,
    17 WARN) against the LIVE personas — including a clean Balthasar after the 2b redaction.
  - The three-commit order was `0623dd3` (X) → `c84cdbd` (Y) → `dd5b497` (2b). Two of the three
    2b files (`balthasar.md`, `world_state_schema.py`) had intermixed pre-existing edits; the
    split was reconstructed against a clean HEAD and each staged diff reviewed before committing,
    so nothing was mislabeled and nothing was lost (working tree == pre-split state, verified).

- **OPEN — skew categorization (the one piece of the determinism-shrink not settled).**
  Allocation skew is currently treated as a WITHHELD breaker, but it is arguably a *work-within*
  risk condition Balthasar should reason about concretely. **The 0.85 band was deliberately LEFT
  in his persona** (his Step-2 bands operate on the disclosed `portfolio.allocation_skew`, not on
  the withheld `max_allocation_skew` threshold) — do NOT change it until decided. Settle it with a
  band-present vs band-absent **paper A/B** (definitive — item-1 fingerprinting now separates the
  arms). See `02_NEXT_BUILD_TASKS.md` Stage-4 OPEN/DEFERRED list (also records the coupled
  wake-type/`world_state`-trimming deferral and the carry-forward Langfuse SCORES mirror).

**NEXT BUILD = the per-agent SQLite Journal recall layer** (`02` item 4) — the remaining Stage-4
build; needs its own design pass. Determinism-shrink otherwise complete.

## Session 2026-06-08 — Stage 3 arbiter council built + wired + INTEGRATION-VERIFIED through real `run_cycle`; committed (`c47e36a`); services stayed as-is

The Stage-3 hand-rolled arbiter orchestrator is **built, wired into `run_cycle`, and
integration-verified end-to-end through one real `run_cycle`** (six real vendor calls),
then **committed to `xrp_grid` as `c47e36a`** (six files; NOT pushed — the operator pushes
code manually). `magi.service` (the trading/council scheduler) stayed **inactive+disabled**
throughout; the only running service is the unrelated market-tape `magi-dashboard.service`
(it serves the tape monitor and shares no tables with the trading code — verified). No live
trading happened: `run_cycle` is a **decision-only** path (it convenes the council, enforces
hard rules, and writes the decision tables) — its one engine touch is
`GridEngine(paper=True).get_current_price()`, a read-only price fetch — so **no Kraken order
was placed** (confirmed: zero order-placement calls reachable in the path; no new
`grid_orders` row; newest order row is still 2026-05-28).

**What was built.** A new module `magi/council_v2.py` with one public entry
`run_council(world_state, cycle_id) -> (round_0, round_1, cons)`, implementing the
2026-06-04 redesign as a **sequential six-call choreography over the three proven
seat-callers** (direct vendor SDKs only — NOT CrewAI, NOT ADK, NOT LiteLlm):
- **Phase A (openings, sequential):** Casper (regime) → Melchior (grid econ, prompted
  *orthogonally*: Casper's regime+regime_action passed as a GIVEN FACT, **label only** —
  no Casper conviction/crux/evidence, to avoid anchoring) → Balthasar (opening risk read,
  sees both full openings).
- **Phase B (rebuttal, ALWAYS runs, Casper+Melchior only):** both rebut against a **frozen
  snapshot of all three openings** (neither sees the other's rebuttal); hold REQUIRES a
  crux stating why you hold against the strongest opposing point (no silent assent).
- **Phase C (synthesis):** Balthasar sees the three openings + both rebuttals; his returned
  `RiskVote` IS the final risk call. He is the **arbiter** and does NOT rebut.

**Supporting changes this session:**
- **Seat-callers** gained an optional `extra_context` param (appended as a trailing block
  AFTER the world_state). `extra_context=None` is byte-identical to the proven standalone
  path for **Casper and Melchior**; for **Balthasar** the request gains an Anthropic
  **ephemeral (5-min TTL) prompt-cache breakpoint** on the stable prefix (persona +
  world_state), so his synthesis call reuses the opening call's cached prefix — the input
  *text* is unchanged so the vote is identical, only caching metadata/shape is added.
  Melchior preserves the stable prefix for DeepSeek's *automatic* cache (no `cache_control`
  code). Casper caching is deliberately OFF (see `02` Stage-3 notes).
- **`database.py`:** `debate_records` gained `casper_r1_position` / `melchior_r1_position`
  (CREATE TABLE + idempotent ALTERs) — each agent's POST-REBUTTAL structured label, so later
  accuracy scoring reads the revised call, not the stale opening. **No `balthasar_r1_position`**
  (he's the arbiter; his post-rebuttal call is `final_risk_action`).
- **`magi/orchestrator.py`:** `run_cycle` now calls `load_dotenv()` once at the top, then
  `round_0, round_1, cons = run_council(world_state, cycle_id)` in place of the ADK
  parallel-R0 / conditional-R1 / `resolve_consensus` block. The dead Letta
  `update_world_state()` push was removed. `_build_debate_record` was edited: it stamps
  `trace_id` **from `cons`** (NOT `tracing.current_trace_id()` — that builder runs after the
  trace context exits and would write NULL; `run_council` captures the id inside `trace_cycle`
  and carries it on `cons`, which `enforce_hard_rules` preserves via `cons = dict(consensus)`),
  writes the two `*_r1_position` columns from `round_1`, fixes the `r1_held` diff to use each
  agent's PRIMARY label (verdict for Melchior, position for Casper), and writes Balthasar's R1
  columns as **NULL** (arbiter never rebuts → `held=1` would be a false 100%-hold artifact).
  `magi/council.py` is **unchanged** (left intact for any other importer; superseded for the
  `run_cycle` path).
- **Persona-load = hard stand-down (this session's small fix).** `run_council` loads
  Melchior's persona once, inside the trace context, BEFORE any vendor call. If that load
  fails it does NOT continue — it hard stands down (the same safe-hold cons as a vendor
  failure, with `council_error="persona_load_failed:melchior:…"`), because Melchior's
  seat-caller would otherwise silently fall back to a **thin default persona** (a 287-char
  stub vs. the full 13.9 KB `melchior.md`). Casper and Balthasar are NOT special-cased: their
  seat-callers resolve their full `casper.md` / `balthasar.md` via their own
  `load_persona(...)` fallback, so a persona-load failure for them surfaces through the normal
  per-seat fail-safe (stand-down). Net effect: a missing/empty persona file for ANY seat → the
  council declines to convene that cycle, never runs on degraded instructions.

**Five contract subtleties resolved during the build (operator-approved before writing):**
(A) trace_id stamped from `cons`, not recomputed post-context; (B) `r1_held` diffs the
per-agent primary key, since Melchior's R0 primary is `verdict` not `position` (and adding
`position` to his R0 dict would have flipped the scheduler return shape via
`_agent_view_action`); (C) Melchior **geometry follows the post-rebuttal verdict** —
`round_0["melchior"]["geometry"]` is overwritten with the final geometry so a rebuttal
flipping THESIS_HOLDS→RECONFIGURE builds to the right grid, while the `melchior_r0_position`
column keeps the opening verdict; (D) Balthasar's `extra_context=None` request is NOT
byte-identical (caching restructure) but the input text is — semantic check, not byte check;
(E) Balthasar R1 columns NULL, not held=1.

**Fail-safe.** Every seat call is wrapped. Any vendor error (missing key, balance exhaustion,
API/validation error after the seat-caller's own retry) does NOT crash: the council STANDS
DOWN to a safe hold — `grid_verdict=THESIS_HOLDS` (→ MAINTAIN), `risk_action=CLEAR`, permissive
`regime_action=EXECUTE`/`geometry_veto=PROCEED` (no fabricated veto), `cons["council_error"]`
set, and any seat we never got a vote from is filled with the SAFE_DEFAULTS degradation
fingerprint (`conviction=0.0`, `crux="(no response)"`) so the existing council-degradation
detector still trips on a sustained outage. The same stand-down covers a Melchior
**persona-load** failure (see the persona bullet above). Verified two ways: a
`ANTHROPIC_API_KEY`-blanked standalone run (Casper+Melchior ran, Balthasar's blanked key
stood the council down to the safe-hold cons without raising), and a monkeypatched
persona-load failure (stood down before any vendor call — the vendor seat-callers were never
invoked — with `council_error="persona_load_failed:melchior:…"`).

**Standalone verification (real output):** `py_compile` + import clean on all changed files;
`PRAGMA table_info(debate_records)` shows the new columns; Casper/Melchior `extra_context=None`
payloads byte-identical, Balthasar same input text; a live standalone `run_council` produced
valid `round_0`/`round_1`/`cons` with a Langfuse trace of six seat generations (correct
per-seat model attribution — Melchior labeled **deepseek**, not claude) and **cache-creation
tokens on Balthasar's opening + cache-read tokens on his synthesis**.

**Integration verification — one real `run_cycle` (the seam that matters), real output:**
`run_cycle(trigger="manual", force=True)` returned its normal scheduler-shape dict without
raising and wrote a new `debate_records` row (`cyc_1780930146`). The full persistence seam was
confirmed: (1) all three R0 votes populated (Melchior carries a **verdict**, not an action);
(2) `casper_r1_position` / `melchior_r1_position` written from the rebuttal (both agents HELD
this cycle, so the post-rebuttal label equals the opening and `*_r1_held=1`; Balthasar's R1
columns NULL as designed); (3) `trace_id` populated (carried on `cons`); (4) the
`magi_decisions` legacy dual-write landed (id 436) and is consistent with the `debate_records`
final actions; (5) the gate-event consume ran — it swept a **100-event backlog** that had
accumulated since the last real cycle on 2026-05-28 (the period the council was offline),
including one `fired=1` T13, leaving zero unconsumed; (6) the Langfuse trace
(`council-cycle:cyc_1780930146`) shows the same six-generation shape as the standalone, now
under a real cycle, with Balthasar **cache write 12,960 → cache read 12,960** on the synthesis
call and DeepSeek auto-cache reads on Melchior. Notably the run **exercised the hard-rule
override path for real**: the council's raw verdict was THESIS_HOLDS (Melchior held), but
because the grid had not filled in 260.5 h the survival rule `[GRID_DEGENERATE]` forced
`grid_action=RECENTRE`; since a THESIS_HOLDS verdict carries no geometry,
`[GEOMETRY_INJECTED_FROM_SCORER]` injected the scorer's rank-1 geometry (5 levels, 0.75 %
spacing) and recorded `geometry_source=scorer_fallback` — the exact council-says-hold /
rule-forces-recentre / scorer-supplies-geometry interaction, now proven through the live
orchestrator.

**Committed.** The six code files (`magi/council_v2.py` new; `magi/orchestrator.py`,
`database.py`, and the three seat-callers modified) were committed as **`c47e36a`** —
"Stage 3: hand-rolled arbiter council (six-call) wired into run_cycle". **Not pushed** (the
operator pushes `xrp_grid` manually; HEAD is 1 ahead of `origin/main`). The `database.py`
staging was **patch-level**: only the Stage-3 schema (`trace_id` + the two `*_r1_position`
columns + their ALTERs) went into this commit. A **pre-existing, unwired agent
accuracy-scoring layer** that also lives uncommitted in `database.py` (`_decision_bar_index`,
`_score_casper/_melchior/_balthasar`, plus the `unrealized_pnl_{6h,24h}` outcome columns and
the matching `update_debate_outcomes` change) was **deliberately left out** of the Stage-3
commit — it has no live consumer yet and was not part of this verified work; it stays in the
working tree for its own future commit.

**NOT done / next:** services stay stopped pending operator direction (nothing here flips the
bot live). **Stage 4** is next: `enforce_hard_rules` determinism-shrink + the per-agent
Journal (SQLite recall layer). Carried-forward follow-ups (see `02`): per-seat world_state
trimming is deferred until Langfuse shows real per-seat token counts; Gemini/Casper caching
stays OFF (revisit from Langfuse data if convene frequency rises); nginx basic-auth and the H4
two-engine divergence remain pre-live items.

**Follow-up commits 2026-06-08 (the two leftover `database.py` pieces, committed in dependency
order; xrp_grid local, not pushed):**
- **`9c7d1df` — outcome-backfill unit** (`database.py` + `observer.py`): `debate_records` gains
  `unrealized_pnl_6h`/`unrealized_pnl_24h`; `update_debate_outcomes` accepts/writes them;
  `observer._compute_window_metrics` returns `(fills, realized, unrealized)` and
  `backfill_outcomes` computes + passes the unrealized drift. **Runtime-verified** on a temp
  copy of `observer.db` (real cycle `cyc_1779955233`: computed `unrealized_6h=-0.0956`,
  `unrealized_24h=1.097`, both written; canonical DB untouched). This is the **live-wired** half
  — `observer.py`'s backfill consumes it.
- **`c4c0bd8` — per-role `get_agent_accuracy` rewrite** (the `_score_casper/_melchior/_balthasar`
  + `_decision_bar_index` layer): the never-committed 2026-06-07 work (the source tree had
  diverged from `02` item 3, which already marked it DONE), committed **as-is**. It **consumes
  the committed `grid/forward_sim.py` (`60fc0ea`) and reads `unrealized_pnl_6h`** (hence it
  commits AFTER `9c7d1df`). It is **NOT wired to any live consumer** — only the archived
  dashboard ever called the old function; live wiring is a **Stage-4 Journal** task.

## Session 2026-06-07 — Langfuse observability + Stage 1 prereqs + Stage 2 seat-callers (all OFFLINE; services stayed stopped)

Three blocks of work toward the hand-rolled arbiter orchestrator (the "Stage 3"
build): an observability layer, the Stage-1 prerequisites + per-role accuracy fix,
and the two missing Stage-2 seat-callers. Nothing was wired into a live cycle; every
proof below was a standalone run. Naming used this session: **Stage 1** = DB/schema
prereqs + the `get_agent_accuracy` rewrite; **Stage 2** = the three standalone
seat-callers + a shared world_state renderer; **Stage 3** (next) = the orchestrator
that convenes them.

### 1. Langfuse observability layer added (NEW; not yet wired)

A tracing helper now exists at **`magi/agents/tracing.py`**, built and proven against
**Langfuse SDK v4.7.1**, cloud **Hobby tier, US region**, org **"NERV"** / project
**"MAGI"**. It exposes four functions: **`trace_cycle(cycle_id)`** (a context manager
opening the root trace for one council cycle, named `council-cycle:<id>`),
**`trace_seat(seat, model, vendor, request_payload)`** (a context manager opening a
nested generation observation, yielding the generation object so the caller can
`.update(output=…, usage_details=…)` after the model returns),
**`current_trace_id()`** (returns the active trace id as a string, for stamping into
the DB later), and **`get_tracer()`** (returns the Langfuse client or `None`).

- **Manual attribution, deliberately NOT auto-instrumentation.** Each seat's `model`
  is set explicitly. This is on purpose: the Melchior seat reaches DeepSeek through
  the Anthropic-compatible endpoint using the Anthropic SDK, so an Anthropic-SDK
  auto-instrumentor would mislabel that generation as Claude. Explicit per-seat
  `model`/`vendor` is the only way to attribute it correctly.
- **Fire-and-forget.** If Langfuse is unreachable or unauthed the helper degrades to
  no-ops and never raises into the caller — the trading path is never blocked by
  tracing. Verified with a blanked-keys run: with the keys cleared the process still
  exits cleanly and the helper silently does nothing.
- **Two bugs in the original reference were caught and fixed during the build.**
  (a) `update_current_trace(...)` does **not exist** in SDK v4.7.1 — instead the root
  span's `name` propagates to the trace, so naming the root span `council-cycle:<id>`
  is what titles the trace. (b) A double-`yield` bug in the context-manager wrapping
  would have masked a caller's own exception (turning a real trading error into a
  confusing tracing error); it was restructured so caller exceptions propagate
  untouched.
- **State:** proven end-to-end with one real trace (a generation correctly attributed
  to DeepSeek, not Claude). **NOT yet wired into any cycle** — wrapping cycles/seats in
  these context managers is a Stage-3 orchestrator job. Keys live in `.env` as
  `LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_BASE_URL` (note: the host
  var is `LANGFUSE_BASE_URL`, the current canonical name, not the older `LANGFUSE_HOST`).

### 2. Stage 1 prerequisites landed (DB columns, persona-path repoint, validator)

- **`debate_records` gained three columns** (idempotent migration applied to the live
  `observer.db`, and added to the `CREATE TABLE` in `database.py` so fresh DBs include
  them): **`trace_id` (column index 51)** — groundwork for stamping the Langfuse trace
  id of each cycle (no writer yet; stays NULL until the orchestrator stamps it); and
  **`unrealized_pnl_6h` (52) / `unrealized_pnl_24h` (53)** — the windowed mark-to-market
  drift on the held position, written by the existing observer backfill alongside the
  realized `pnl_6h`/`pnl_24h`. The unrealized number is computed on the SAME strict
  live-only basis as realized (it is `0.0` in paper / when no live fills occurred in the
  window, never garbage), and `realized + unrealized` equals the window's total equity
  change.
- **`PERSONA_DIR` / `load_persona` in `world_state_schema.py` repointed** off the dead
  Letta-era `magi/prompts/*_prompt.txt` onto the **live `magi/agents/personas/*.md`** —
  the files the council actually uses. This closes the "toothless validator" issue noted
  on 2026-06-06: the persona checks now run against the real persona text.
- **The validator now tolerates bare prose tokens.** A bare snake_case token in a
  persona that resolves to no schema field (e.g. `current_price` appearing in
  `melchior.md` as prose describing a dead function argument, not a world_state field)
  was previously a hard ERROR; it is now downgraded to a low-severity **NOTE**. A genuine
  broken *dotted* reference (`foo.bar` that resolves to nothing) still ERRORs.
  `python -m magi.validate_schema` is back to a **WARN-only PASS** (0 ERROR; exit 0).
- **The 12 Melchior persona-coverage WARNs are KNOWN AND DELIBERATELY LEFT.** The
  validator warns that 12 world_state paths the schema declares Melchior consumes
  (`hours_since_last_fill`, `hours_since_last_rebuild`, `indicators.vwap_dev_pct`,
  `indicators.vol_regime`, `indicators.autocorr_1h`, `indicators.autocorr_4h`,
  `cooldown_status.recentre_cooldown_active`, `last_fill.side`, `last_fill.hours_ago`,
  `position_state.round_trip_distance_pct`, `position_state.round_trip_net_pnl_usd`,
  `trajectory.skew_delta`) are never *cited* in `melchior.md`'s decision-tree prose.
  These are **persona-prose gaps, NOT data starvation**: all 12 paths ARE fed to
  Melchior (the seat-caller serializes the entire world_state into the prompt; a runtime
  check confirms all 102 declared paths are present in `build_world_state()` output).
  The verdict judge simply doesn't quote them in its written reasoning. **No action was
  taken and none is needed** — they are left visible (not suppressed) on purpose.

### 3. Per-role `get_agent_accuracy` rewrite (`database.py`)

The old accuracy function scored every agent with one predicate — `fills_6h > 0 AND
pnl_6h >= 0`. That is wrong per seat: it marked a **correct** Melchior `NO_PROFITABLE_GRID`
stand-down (which by design produces zero fills) as a *failure*. Rewritten so each seat
is scored on its own question:

- **Casper — regime-realized, PnL-independent.** A call is correct iff the regime he
  named (`RANGING`/`TRENDING`/`UNCERTAIN`) matches what a recycling grid would actually
  have done over the **next 72 hours** of real price history. `UNCERTAIN` is
  *matched-to-ambiguous* (correct only when the realized regime was also ambiguous; no
  free abstention). Rows whose 72h forward window isn't yet covered by candles are
  excluded as not-yet-matured, never counted wrong.
- **Melchior — verdict-conditional.** `THESIS_HOLDS` is reality-graded (grid kept
  filling and realized + unrealized didn't bleed); `NO_PROFITABLE_GRID` is graded by the
  forward sim (was no fee-clearing grid actually available); `RECONFIGURE` is graded by a
  **decision-time scorer-comparison PROXY** — explicitly flagged in the code as NOT a
  true held-the-old-config counterfactual (no such realized series exists).
- **Balthasar — total PnL (realized + unrealized) with applied-vs-overridden detection.**
  Critically, **reality-graded calls (he said CLEAR/PROCEED and it was applied) and
  counterfactual-graded calls (he applied a veto, scored by what the *unpaused* grid
  would have done via the forward sim) are kept SEPARATE in the return shape and are
  NEVER summed into one number.** Calls his vote didn't drive (overridden by a hard rule)
  are excluded.
- **Known limitations, stated plainly:** Balthasar's reality figure is currently
  inflated because most historical windows are paper / zero-PnL and the unrealized
  columns are NULL on all historical rows — it becomes meaningful only once **live**
  cycles populate the unrealized columns. Melchior is **ungradeable on historical rows**
  because those rows carry the Letta-era `MAINTAIN`/`RECENTRE` action vocabulary, not the
  new verdict vocabulary — the scorer correctly excludes them all.
- **New shared module `grid/forward_sim.py`** is the single source of the recycling-grid
  forward simulation + regime label (the harvest-vs-bleed-vs-hold logic, thresholded on
  the exogenous maker round-trip fee floor `2 × maker_fee = 0.50%`). The old
  `optimize/casper/forward_label.py` was refactored to a thin re-export from it, so the
  core path never imports anything under `optimize/` — important because importing that
  package pulls in the `vertexai` ADK-eval dependency, which is not installed in the core
  venv and would crash a core import.
- **This accuracy fix is the stated HARD DEPENDENCY for the future per-agent Journal**
  (the recall layer): without correct per-role scoring, validated recall would teach
  Melchior to over-trade.

### 4. Stage 2 seat-callers built and proven standalone (NEW)

Two new standalone seat-callers now exist as siblings to the proven
`magi/agents/melchior_deepseek.py`:

- **`magi/agents/casper_gemini.py`** — `run_casper(world_state, persona=None,
  model="gemini-2.5-flash")` and `run_casper_with_meta(...)`. Uses the **native-Gemini
  ADK path**: an ADK `LlmAgent` with `output_schema=RegimeVote` passed **directly** (NOT
  through `schema_for_tool`), because `RegimeVote` is declared `extra="ignore"` and so
  emits no `additionalProperties` — the structure that native Gemini 400s on. Parses the
  structured output and validates it against `RegimeVote`.
- **`magi/agents/balthasar_claude.py`** — `run_balthasar(world_state, persona=None,
  model="claude-sonnet-4-6")` and `run_balthasar_with_meta(...)`. Uses the **raw Anthropic
  forced-tool** path (single forced tool, `temperature=0`), builds the tool schema via
  `schema_for_tool(RiskVote)` (keeping the "always `schema_for_tool`" rule even though
  Claude tolerates `additionalProperties`), validates against the **live `RiskVote`** from
  `schemas.py` (NOT the older phase-1-local schema), and retries once with the validation
  error fed back if the first emission is invalid.
- **New shared renderer `magi/agents/world_state_render.py`** — `render_world_state(ws)`
  returns deterministic pretty JSON (`json.dumps(ws, indent=2, sort_keys=True)`). **All
  three callers now use it**, so the world_state block they put in front of their model is
  byte-identical. Melchior was switched off its old flattened `key: value` renderer onto
  this one and **RE-PROVEN** with a real DeepSeek call: it returned a valid
  `THESIS_HOLDS` verdict with conviction 0.82 and the geometry-iff-`RECONFIGURE` contract
  held (geometry absent on a non-`RECONFIGURE` verdict).
- **All three proven standalone with REAL vendor API calls** on a real historical
  world_state pulled from a `debate_records.world_state` row (no `build_world_state` call,
  which could hit Kraken): **Casper billed `gemini-2.5-flash`** (returned a validated
  `RegimeVote`, `position=RANGING`, via the native path with no `additionalProperties`
  400), **Balthasar billed `claude-sonnet-4-6`** (validated `RiskVote`, `risk_action=CLEAR`,
  reasoning that walks the live persona's decision tree), **Melchior billed
  `deepseek-v4-pro`** (validated `GridVote`, `THESIS_HOLDS` as above).

- **FINAL WIRING CONCLUSION (state plainly): the three seats are intentionally NOT
  symmetric in transport, and that is by design.** Casper runs on native-Gemini via ADK
  (`output_schema`); Balthasar and Melchior run on the raw Anthropic-shape forced-tool
  pattern (Melchior pointing at DeepSeek's Anthropic-compat endpoint). Three-vendor
  judgment diversity is the architectural principle; transport symmetry is explicitly
  **not** a goal, so no effort will be spent forcing one transport across all three.
- **Two known, harmless asymmetries the orchestrator resolves:** (a) **`.env` self-load**
  — `casper_gemini.py` calls `load_dotenv()` at import; `balthasar_claude.py` and
  `melchior_deepseek.py` read `os.environ` directly and assume the caller already loaded
  `.env`. (b) **Default persona handling** — the new Casper/Balthasar callers default to
  loading the live `.md` persona when none is passed; Melchior's caller keeps a short stub
  default. Both are moot once the orchestrator passes personas explicitly and loads `.env`
  once at startup.

**Nothing run live; no `provision_agents`/eval/live cycle; services stayed stopped.**

## Session 2026-06-06 (later) — `drawdown_from_high_7d` WIRED + CLAUDE.md trimmed/published

- **`drawdown_from_high_7d` now WIRED into `build_world_state()`** — closes the
  persona/world_state inconsistency that was the HARD PREREQUISITE (`02` item 0★ now
  DONE). `magi/orchestrator.py:build_world_state` computes it from the trailing-7d
  candle series (168 × 1h bars via `get_candles('1h', 168)`) on a **running-peak
  basis** — `peak = max(max(highs), price)` — so the value **clamps to ≤ 0.0** (0.0 =
  price at/above the 7d high). Emitted as a **signed percent** (e.g. −21.24 = 21.24%
  below the peak). **`None` fallback** when price is unavailable or no candles exist;
  any compute error logs a warning and emits `None`. A matching `FIELDS` entry was
  added to `magi/world_state_schema.py` (`type: float`, **`consumers: ["balthasar"]`**,
  `balthasar_usage` framed as risk context).
- **It is a JUDGMENT INPUT only — no threshold / gate / PAUSE / hard-rule keys off it**
  (per the 2026-06-06 DECISION; fitting `dd7d ≤ −X` to 3 events is the overfitting trap
  the operator forbids).
- **Verified:** `python -m magi.validate_schema` → **0 ERROR / 0 WARN PASS at 102
  declared paths** (was 101); runtime output matches schema; all three personas resolve
  clean. A one-shot `build_world_state()` on current data emitted
  `drawdown_from_high_7d = −21.24` (price 1.08669 vs 7d peak 1.37967). NOTE: observer.db
  1h candles are frozen at the 2026-05-28 shutdown, so the test window is current-price
  vs the last-recorded 7d high — confirms the math, not a live-updating window (the live
  feed is the separate tape stack; the MAGI observer is stopped). Services stayed
  stopped; no `provision_agents` / eval / live cycle run.
- **Known issue surfaced (NOT fixed):** `validate_schema` /
  `world_state_schema.py:load_persona` validate the **dead Letta-era
  `magi/prompts/*_prompt.txt`**, not the live `magi/agents/personas/*.md` — so the
  orphan-consumer check is toothless for the new field (the clean PASS leaned on the
  "context"-prefixed usage-hint heuristic, not real persona coverage). Added to `02`'s
  hand-rolled-orchestrator checklist. Repointing was deliberately NOT done this session.
- **CLAUDE.md trimmed under the 40k load limit and PUBLISHED.** Removed dead/superseded
  historical narrative (Letta-era lineup-drift NOTE, the R1-evolution parenthetical, a
  SUPERSEDED-2026-06-04 inlay, the STATUS migration-tail, and four dead Letta-era §4
  operational bullets), each replaced with a one-line pointer to this STATE LEDGER — no
  dangling refs, no KEEP content touched. **42,393 → 38,539 bytes.** Published via
  `bash /root/magi_docs/sync.sh` (magi-docs commit `8b8c88b`). Dashboard-auth and
  Council-degradation blocks preserved intact; Letta-flavored wording inside
  Council-degradation was flagged to the operator, not edited.

## Session 2026-06-06 — Balthasar + `drawdown_from_high_7d` decision-test (OFFLINE; evidence for the council redesign)

A controlled offline test of one redesign question (`04_EXPERIMENTAL_IDEAS.md`
Session 2026-06-04 makes Balthasar the arbiter and scores his outcomes per-role):
**does giving Balthasar a `drawdown_from_high_7d` signal move his verdict the
cautious way on sustained-downtrend world_states, without making him reflexively
cautious on recoveries / benign books?** This targets the grid-downtrend-bleed
failure mode (grid recenters into a downtrend and buys the fall). The live system
was never touched — services stayed stopped, no repo code or persona on disk was
modified, all work is scratch under `/tmp/phase2_balthasar_v2/`.

- **Design.** 60 XRP world_states reconstructed from `tape/history.db`, stratified
  by forward price path into TRUE_BLEED (fwd-3d < −0.03) / RECOVERY (dd7d < −0.08 at
  t AND fwd-3d ≥ 0) / BENIGN, 20 each, on fixed per-stratum stressed books (TRUE_BLEED
  = `allocation_skew` +0.55 just under the +0.60 PAUSE_LONGS band, both buffers thin
  but above the $10 floor, buy-heavy 7/3). 4 arms = {live persona, corrected persona}
  × {without, with drawdown}, 240 forced-`RiskVote` calls to `claude-sonnet-4-6` at
  temp 0 with faithful R0 reproduction. The "corrected" persona (in-memory only)
  removes the live "there is no drawdown field" / "Never reason about price direction"
  lines and grants narrow price-erosion authority (drawdown as risk context alongside
  skew/buffers, not a mechanical trigger). Spend $2.49 (projection had been $2.59).
- **Result.** (1) **Signal alone is inert** — live persona + drawdown ≈ live persona
  alone (TRUE_BLEED net zero, the lone "more" shift uncited; RECOVERY/BENIGN flat).
  (2) **Signal + persona authority moves it, correctly but weakly** — corrected
  persona + drawdown vs corrected-without: 3/20 TRUE_BLEED more-cautious, **all 3
  citing the drawdown**, incl. one `CLEAR/PROCEED → PAUSE_LONGS/HOLD_GEOMETRY`
  (dd −20.3%, fwd −13.2%) and two `HOLD_GEOMETRY` geometry-veto escalations (both
  forward-bled); 1 less (noise); 16 unchanged. (3) **No false alarms** — RECOVERY and
  BENIGN showed zero movement in every arm.
- **Read / implication.** Magnitude is small: even with both the authority and a
  severe drawdown, Balthasar left 17/20 stressed scenarios fully `CLEAR/PROCEED`,
  treating price drawdown as "approaching the skew band" context rather than a
  first-class trigger. So a drawdown input is worth giving Balthasar IF he becomes the
  arbiter, but the persona change is necessary (signal alone does nothing) and the
  evidence is **thin** (3/20). **DECISION 2026-06-06:** drawdown is adopted as a
  Balthasar **judgment input** he weighs and must cite — **NOT** a fitted
  `dd7d ≤ −X ⇒ PAUSE` threshold (fitting a hardcoded X to 3 events is the overfitting
  trap the operator forbids; grid params stay anchored to fees/spacing). The downtrend
  bleed it targets is a real risk Balthasar now owns. Detail + the three cited shifts
  are in `04_EXPERIMENTAL_IDEAS.md` Session 2026-06-06.
- **Persona PROMOTED + wiring DEFERRED (real repo edit, same day).** Following the
  test, the validated corrected language was promoted to the live
  `magi/agents/personas/balthasar.md` — **persona text only**: the "there is no
  drawdown field" / "Never reason about price direction" language was replaced with
  the price-erosion-authority block (drawdown weighed as risk context alongside
  skew/buffers, not a mechanical trigger), preserving the deterministic
  daily-loss-HALT / kill-switch tail, the risk ladder, the worked examples, and the
  output schema unchanged. Backup at `magi/agents/personas/balthasar.md.bak.20260605`
  (byte-identical to the pre-edit file). **Deliberately DEFERRED:** the
  `drawdown_from_high_7d` field is NOT yet wired — `build_world_state()` does not emit
  it and `magi/world_state_schema.py:FIELDS` has no entry — so the persona now
  references a field production does not yet emit. This persona/world_state
  inconsistency is a **HARD PREREQUISITE to close before any live or paper run**, via a
  wiring step that MUST follow the world_state Maintenance contract (add the field in
  `build_world_state()` + a matching `FIELDS` entry with Balthasar as consumer).
  Drawdown stays a **judgment input**, not a fitted `dd7d ≤ −X ⇒ PAUSE` threshold. No
  `provision_agents` / eval run was triggered (the seats are built off these persona
  files, services stopped). Tracked in `02_NEXT_BUILD_TASKS.md` item 0★.
- **`schema_for_tool` HARDENED against the Gemini `additionalProperties` 400 (real
  code edit, verified — two linked probes).** (1) Re-confirmed Casper's `RegimeVote`
  emits clean structured output on the native Gemini path (`gemini-2.5-flash`) via
  both `schema_for_tool` and the literal ADK `output_schema` — 200 OK, valid parse,
  coherent (RANGING→EXECUTE on a flat synthetic book, TRENDING→STAND_DOWN on a
  bearish-trend one). Negative controls proved the 400 is still live API-side
  (`additional_properties` is not a field in Gemini's `response_schema` proto; any
  schema carrying it 400s) and that `RegimeVote`'s `extra="ignore"` was the *only*
  thing avoiding it — flipping it to `extra="forbid"` re-armed the 400. (2) Removed
  that fragility: `magi/agents/schema_tools.py:schema_for_tool` now runs a final
  `strip_additional_properties` pass that recursively deletes every
  `additionalProperties` key (top object, nested objects, anyOf branches, inlined
  sub-objects) before any vendor sees the schema — so no model's `extra=` setting
  can re-arm the 400. Proof: a deliberately `extra="forbid"` schema (its
  `model_json_schema()` carries `additionalProperties:false`) now returns **200 + valid
  output** through the hardened helper; GridVote/RiskVote regression clean and the
  geometry-iff-RECONFIGURE optional/nullable structure is intact (strip touches only
  that one key). Backup `magi/agents/schema_tools.py.bak.20260606`. No schema `extra=`
  changed, no `council.py` wiring, services stopped. `RegimeVote`'s `extra="ignore"`
  is now belt-and-suspenders, not the sole defense.
- **Council model lineup for the rebuild RECORDED (documentation only — no code
  change).** The operator confirmed the NEW intended council seats, distinct from the
  old Letta lineup: **Casper `gemini-2.5-flash`** (wired), **Balthasar
  `anthropic/claude-sonnet-4-6`** (wired — Sonnet is the DECIDED tier, which closes
  the long-open "haiku-4-5 vs sonnet, re-confirm" question for the rebuild; the live
  Letta agent had run haiku-4-5), and **Melchior `deepseek-v4-pro`** (DECIDED seat,
  **NOT yet wired** — `council.py` still runs `openai/gpt-4o`; the drop-in wrapper
  `magi/agents/melchior_deepseek.py` was proven viable in the 2026-06-05 probe but is
  not connected). So 2 of 3 intended models are live in code; the gpt-4o→deepseek swap
  is the one outstanding model step. Documented across CLAUDE.md (STATUS COUNCIL
  LINEUP block + §3), `00` (agents table), `02` item 7, and `03`. No
  `council.py`/`schemas.py` edit this session.

## Session 2026-06-01 — ADK installed (two-venv split), Casper Gemini-400 bug found+fixed, `adk optimize` scaffold + smoke run

Read-only on the live system (services stayed stopped, no live cycle). All
inference this session was the offline Casper `adk optimize` smoke run.

### ADK + LiteLlm installed — deliberate two-venv split
- **Main venv** (`/root/xrp_grid/venv`, Python 3.10): core `google-adk` (2.1.0) +
  `litellm` (1.83.14) — what the **live council** needs to import and run. This
  closes the "needs google-adk+litellm installed" precondition from the
  2026-05-31 migration; the live path can now be imported/run (still NOT run live).
- **`optimize/.venv`** (separate): `google-adk[eval]` — the heavy tuning stack
  (gepa 0.1.1, google-cloud-aiplatform 1.154.0 → `vertexai`, pandas, etc.) needed
  only by `adk optimize`. Isolated **on purpose** so the production trading venv
  carries no eval/Vertex deps. `vertexai` is a code dependency only — inference
  still routes through the free-tier `GOOGLE_API_KEY` (no Vertex billing).
- Verified non-breaking against the existing import surface. Python 3.10 emits
  FutureWarnings about Google lib EOL (2026-10-04); non-blocking.

### LIVE BUG found + fixed — Casper RegimeVote 400 on native Gemini
- `magi/agents/schemas.py:RegimeVote` used `ConfigDict(extra="forbid")`, which
  serializes as `"additionalProperties": false`. The **native Gemini API**
  (`gemini-2.5-flash`, Casper's model) rejects a `response_schema` containing
  `additionalProperties` → `400 INVALID_ARGUMENT`. So `council._build_agent(
  "casper")` would have **400'd on its very first live R0 call**. Latent because
  the ADK council has never run live. Melchior/Balthasar are unaffected — they go
  via LiteLlm to OpenAI/Anthropic, which accept (OpenAI requires) the field.
- **Fix (shipped in code, offline only):** `RegimeVote` → `extra="ignore"`.
  GridVote/RiskVote KEPT `extra="forbid"`. Validated by proxy — the smoke run made
  11 successful `gemini-2.5-flash` calls with the identical-field schema. **NOT yet
  re-tested via a live council cycle.** Verify the live R0 path before any live run.

### `optimize/casper/` — eval-guided persona-tuning scaffold (NEW)
- `adk optimize` is **prompt/instruction optimization, NOT model fine-tuning**: it
  runs the stateless RegimeVote agent over the Casper regression cases, scores
  `position` vs ground truth, and proposes an improved `instruction` (i.e. a
  rewrite of `casper.md`). It rewrites ONLY the instruction string — never the
  schema, model, or the other two agents. Does NOT auto-apply: prints the
  candidate, operator diffs + regression-gates + hand-edits.
- Scaffold (all under `optimize/casper/`): `agent.py` (root_agent = the live
  Casper agent; also registers the custom metric + fixes two ADK-optimize gaps —
  it doesn't register `custom_metrics` and doesn't leave `optimize/` on sys.path),
  `metrics.py` (`regime_position_match` exact-match metric), `build_evalset.py`
  (converts `evals/casper/dataset.jsonl` → 8-case ADK evalset), `sampler_config.json`,
  `optimizer_config.freetier.json`, `README.md` (billing + run sequence + result).
- **Smoke run (offline, free-tier, $0, exit 0):** `max_metric_calls=10` → 11
  Gemini calls. Current `casper.md` scores **0.875 (7/8)**; the one miss is
  casper_005 (ground truth UNCERTAIN). `best_idx=0` — at smoke budget GEPA only
  fit the seed eval + one reflection, so it kept casper.md byte-for-byte. A real
  attempt to beat 7/8 needs the bigger free-tier budget (`max_metric_calls=20`).
- **Billing confirmed:** Casper's candidate model and the default optimizer model
  are both Gemini → 100% Gemini API, free-tier `GOOGLE_API_KEY`. Vertex is NOT
  cheaper (per-token from request one, no free tier); the free tier's only limit
  is rate, which `agent.py`'s 429 backoff rides out.

### AI Studio call logs as an eval-dataset source (operator note)
- Google AI Studio logs each Gemini call's output for the API key in use. Usable
  to grow eval coverage from real production calls — BUT logs solve the **input**
  problem, not the **label** problem: a log row is (world_state → what Casper
  *said*), not ground truth. Value in order: (1) real input distribution + volume,
  (2) **hindsight/outcome labeling** — label the correct regime from realized
  price action at T+6/24h (best for a trading bot, objective), (3) failure mining
  ("said RANGING, then trended" — directly targets the grid-downtrend-bleed /
  under-calls-trend issue). Only covers Casper's native-Gemini path; OpenAI/
  Anthropic dashboards are the equivalent for Melchior/Balthasar.

## Session 2026-05-31 (later) — ADK migration of the agent-call layer + Melchior verdict redesign

> **DIRECTION SHIFTED 2026-06-06 — this entry is now HISTORICAL.** The decision layer
> moved from this ADK `council.py` to a **hand-rolled orchestrator** (direct vendor-SDK
> calls + owned SQLite state; NOT ADK, NOT CrewAI); `council.py` is unchanged and
> superseded, and the seats are now proven standalone but not wired. For current
> authoritative state see the **STATE LEDGER** at the top of this file + CLAUDE.md
> STATUS. The text below records the 2026-05-31 ADK work as it stood then.

This was the authoritative council state as of 2026-05-31. It superseded the Agent
Studio direction in the earlier 2026-05-31 entry below and the 2026-05-29 scoping.
Built the native rebuild as a fresh **Google ADK** implementation (not the
vendor-stateful Agent-Studio/Memory-Bank design the earlier entries assumed).
Code-complete and offline-validated; NOT run against live models, NOT deployed.

### What was built
- **`magi/council.py` rewritten off Letta → ADK.** Each agent is a native ADK
  `LlmAgent` invoked via `Runner` + `InMemorySessionService`, `include_contents=
  "none"` (stateless per cycle), `output_schema` = its vote model, `output_key` =
  `casper_r0`/`melchior_r0`/`balthasar_r0`. The Letta client, shared blocks,
  threads, step/token sweep, and the freshness validator are GONE. The PUBLIC
  BOUNDARY is preserved verbatim — `update_world_state`, `run_round_0_parallel`,
  `should_run_r1`, `run_round_1`, `resolve_consensus`, `emit_human_alert` keep
  their names and parsed-vote dict shapes — so `orchestrator.py` needed no
  signature changes. world_state is cached in-process (`update_world_state`) and
  injected into each agent's prompt, replacing the Letta shared block.
- **New `magi/agents/` package.** `schemas.py` = `RegimeVote` (Casper),
  `GridVote`+`Geometry` (Melchior), `RiskVote` (Balthasar), all `extra="forbid"`,
  conviction as float 0.0–1.0. `personas/` = `casper.md`, `melchior.md`,
  `balthasar.md` + a `load_persona` loader that raises on empty.
- **Models:** Casper `gemini-2.5-flash` (native Gemini string), Melchior
  `LiteLlm("openai/gpt-4o")`, Balthasar `LiteLlm("anthropic/claude-sonnet-4-6")`.
  Keys from env (`GOOGLE_API_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`), never
  hardcoded. ADK imported lazily so importing council.py/orchestrator.py does not
  require google-adk installed. **(Lineup update 2026-06-06: Melchior's intended seat
  is now `deepseek-v4-pro` — DECIDED, not yet wired; the `gpt-4o` shown here is what
  code still runs. See the Session 2026-06-06 lineup bullet below + CLAUDE.md STATUS.)**
- **Melchior redesigned: economic VERDICT, not an action.** GridVote emits
  `verdict ∈ {THESIS_HOLDS, RECONFIGURE, NO_PROFITABLE_GRID}` + optional nested
  `geometry` (present iff RECONFIGURE, enforced by a model validator). Answers one
  always-present question — "is there profitable grid economics here right now?" —
  grid or no grid. The old MAINTAIN/RECENTRE/TIGHTEN/WIDEN tree is RETIRED.
- **Casper + Balthasar personas rebuilt to standard** (shared council framing,
  float conviction high→0.8/med→0.5/low→0.2, strict-JSON output). Decision trees,
  regime_action / geometry_veto, stranded-grid carve-outs preserved.

### orchestrator.py changes (verdict consumed directly)
- `enforce_hard_rules` reads `consensus['grid_verdict']` and translates at entry:
  **THESIS_HOLDS → MAINTAIN, RECONFIGURE → RECENTRE (+geometry),
  NO_PROFITABLE_GRID → GRID_PAUSE** (stand down: cancel orders + idle, NOT a hold).
  `_VERDICT_TO_GRID_ACTION` is the map. council.py does NOT flatten — the
  orchestrator owns the translation, so NO_PROFITABLE_GRID stands the grid down
  instead of being neutered into MAINTAIN.
- Veto ladder (rule 0d) UNCHANGED: a RECONFIGURE maps to RECENTRE, so STAND_DOWN /
  DEFER_STRUCTURAL / HOLD_GEOMETRY / RISK_BLOCK still coerce it to MAINTAIN.
  `_original_grid_action` captured after the map; rule 6 (GRID_DEGENERATE) skips on
  GRID_PAUSE; new `[NO_PROFITABLE_GRID]` tag added to the canonical set; icontract
  `in_grid_action` snapshot reads the mapped verdict.
- `debate_records.melchior_r0_position` and `magi_decisions.melchior_action` now
  store Melchior's VERDICT string. `_prior_r0_signature` compares verdict-to-verdict
  so the R1 novelty gate is unaffected.

### Cadence model — corrected (supersedes "4h schedule" framing)
Cost/cadence is GATE-DRIVEN, not clock-driven. The gate (`magi/gate.py`,
deterministic, ZERO API cost) runs every observer loop and decides whether the
council (the only paid layer) wakes. Floor ≈ 1 council call/day; ceiling = grid
breach frequency. The 4h timer is a backstop. (Gate threshold calibration internals
not re-read this session — this is the structure, not the tuned fire-rate.)

### Memory / recall — DELIBERATELY stateless; recall layer scoped, NOT built
The 2026-05-29 "vendor owns memory/self_model/thread history" direction is REVERSED.
Rationale, from this repo's own failure log: Letta's stateful agent layer caused
GPT-4o byte-for-byte thread-anchoring, the ~80–90k-token freshness-retry tax, and
Balthasar's runaway self_model corruption (STAND_DOWN→HALT). ADK agents are
therefore stateless per cycle. Per-agent learning (the original point of
`self_model`) is currently ABSENT, not relocated. The agreed rebuild is a
CONTROLLED recall layer: deterministic, Python-assembled from `debate_records`,
bounded (recency window + max items), read-only to the agent, injected as per-call
prompt input by council.py — NOT vendor-owned hidden state, and NOT
`VertexAiMemoryBankService` (LLM-driven consolidation reintroduces the rejected
hidden-state problem). SCOPED + approved-in-principle, NOT built. See
`02_NEXT_BUILD_TASKS.md` post-migration queue.

### Open issues surfaced (carried to 02)
- **`get_agent_accuracy` mis-scores the verdict model** (`database.py:1635`):
  "positive" = `fills_6h>0 AND pnl_6h>=0`, identical for all agents — so a CORRECT
  Melchior `NO_PROFITABLE_GRID` stand-down (fills=0 by design) scores as failure.
  Must be fixed (per-role correctness) before recall can use it, or recall teaches
  Melchior to over-trade.
- **Downstream readers still on the old vocabulary** (display/analysis only, not
  control): `dashboard.py` panels; `observer._record_outcome_to_block` (now a dead
  Letta no-op); `analysis/*` replay/forecast scripts testing `== 'RECENTRE'` won't
  match `'RECONFIGURE'`.
- **Contaminated history:** pre-restart `debate_records` are Letta-era; recall and
  any accuracy baseline must exclude them (restart cutoff).
- **Runtime prerequisites:** `google-adk` + `litellm` not installed here; provider
  keys not set; LiteLlm structured-output reliability for GPT-4o/Claude unverified
  (a native forced-tool fallback is proven for Claude in `phase1_balthasar/`).

### Safety / archive
Everything overwritten in place is archived under
`archive/pre_adk_migration_2026-05-31/` with a `RESTORE.md`: `council.py.letta`
(full pre-ADK restore point), `council.py.adk-preverdict`,
`orchestrator.py.preverdict`, `casper.md.preverdict`. No commits made; git HEAD
also still holds the originals.

### Validated (no model calls)
`py_compile` clean on council.py + orchestrator.py; schemas construct + enforce
(extra=forbid, conviction bounds, verdict↔geometry invariant); personas load
non-empty; verdict→action map + veto ladder confirmed by offline logic checks
(NO_PROFITABLE_GRID→GRID_PAUSE; RECONFIGURE vs STAND_DOWN/RISK_BLOCK fires R1;
aligned THESIS_HOLDS skips R1). NOT validated: any live model call, real-provider
structured output, a full live cycle, deployment.

---

## Session 2026-05-31 (earlier) — Casper → Google Agent Studio prep (M1 partial) [SUPERSEDED]

> **SUPERSEDED by the ADK migration above.** The build did NOT use Google Agent
> Studio / Memory Bank; it used ADK `LlmAgent`s with stateless prompt-injection.
> Casper's curated persona text (`casper_gcp/casper_instructions.txt`) WAS reused
> as the source for the ADK `magi/agents/personas/casper.md`. The Agent-Studio /
> Sessions / Memory-Bank mechanics below are no longer the plan.

Read-only build-prep session for **M1** (`02_NEXT_BUILD_TASKS.md` — per-agent
rebuild spec, Casper → Google). No Google API calls, no resource creation, no
memory writes; read-only against Letta; system stayed shut down. Produced the
concrete Google Agent Studio inputs for Casper. Artifacts under `casper_gcp/`.

### What was produced (`casper_gcp/`)

- **`casper_description.txt`** (746 chars) — the Studio "Details → Description"
  field: a 2-paragraph statement of Casper's role (regime analyst: RANGING /
  TRENDING / UNCERTAIN + the `regime_action` verdict EXECUTE / DEFER_STRUCTURAL /
  STAND_DOWN) and scope boundary (does NOT own grid geometry → Melchior, or
  inventory/risk → Balthasar).
- **`casper_instructions.txt`** (13,827 chars) — the Studio "Details →
  Instructions" field: Casper's operating brain, curated from the live persona —
  Role, trigger context, signals received, derived quantities, the full decision
  tree (Steps 0–4 verbatim), the REGIME_ACTION rule + stranded-grid carve-out,
  conviction calibration, both worked examples verbatim, the OUTPUT section, and
  CONSTRAINTS.

### Source-of-truth decision: live persona only, self_model left behind

- Both files were authored from the **live Letta `persona` block** (fetched via the
  SDK; confirmed byte-identical to the shutdown snapshot at 15,501 chars), per
  explicit operator direction "use the persona only." Casper's **`self_model` is
  contaminated and is being left behind** — it is NOT seeded into Google. This is a
  deliberate, Casper-specific divergence from the generic "seed each agent from the
  snapshot incl. self_model contents" line in `00_PROJECT_OVERVIEW.md` "Migration
  target architecture." (The earlier interrupted self_model fact-deconstruction
  proposal — `casper_facts_proposal.json` / `casper_facts_needs_review.md` — was
  abandoned and never written; do NOT resume it without operator direction.)
- **Content dropped during curation** (reported to operator): PAPER-mode framing;
  stale fee numbers (persona said maker 0.16% / taker 0.26% — actual tier-0 is
  0.25% / 0.40%, and the survival/fee floor is Balthasar's domain, not Casper's);
  and Letta-runtime-specific phrasing (e.g. `R1/regime_action` signal labels
  relabeled to `regime_action`; the `consensus["regime_action"]` glue sentence
  generalized). Geometry/fee authorship stays out of Casper by design (the
  deterministic `spacing_evaluator` owns fee-positive spacing — see CLAUDE.md §4).

### Casper R0 structured-output contract (for the Studio JSON output schema)

Extracted verbatim from `magi/council.py` so the Studio schema replicates the
existing contract. **Do-not-re-derive facts:**

| field | type | required | allowed values | downstream read-by |
|---|---|---|---|---|
| `position` | string | **yes** (parse) | RANGING / TRENDING / UNCERTAIN | `resolve_consensus` → `cons["regime"]` — **informational only** (records + logging; not a hard-rule gate) |
| `conviction` | float | **yes** (parse) | 0.0–1.0 | degradation fingerprint (0.0 + `(no response)` crux) |
| `key_evidence` | list[str] | **yes** (parse) | 3–5 short strings | record-building only |
| `crux` | string | **yes** (parse) | one sentence | **no decision-logic consumer** — used only as the degradation marker (`conviction==0 AND crux LIKE '(no response)%'`, `SAFE_DEFAULTS`) + stored in `*_r0_crux` |
| `regime_action` | string | optional (prompt) | EXECUTE / DEFER_STRUCTURAL / STAND_DOWN | **the only field with decision authority** — hard rule 0d in `orchestrator.py` (DEFER_STRUCTURAL → `[REGIME_DEFER]`, STAND_DOWN → `[REGIME_STANDDOWN]`, only when grid_action ∈ {RECENTRE,TIGHTEN,WIDEN}) |

- **Two schema recommendations for Studio** (close gaps in the Python validator):
  1. **Enforce the `position` enum in the Studio schema.** `_validate_r0`
     (`council.py:438-440`) only checks `position` is a non-empty *string* — it
     never checks it against `VALID_REGIMES`. The enum is unenforced today.
  2. **Make `regime_action` required in the Studio schema.** It is optional in the
     prompt and, when missing/malformed, `resolve_consensus` silently defaults it to
     `EXECUTE` (no veto) via `_safe_extension`. Since it is the *only* decision-driver,
     a silent default to "permit structural change" is the risky failure mode.
- **Validation/coercion pipeline** (for parity): `_parse_json_strict` (strip fences →
  `json.loads` whole → fallback to first `{...}`) → `_validate_r0` → retry → freshness
  check → `SAFE_DEFAULTS["casper"]` on failure (`position=UNCERTAIN, conviction=0.0,
  key_evidence=[], crux="(no response)"`) → consumption-time defaults
  (`regime_action`→EXECUTE).

### Round structure confirmed (orchestrator is source of truth)

- R1 synthesis is **CONDITIONAL**, not always-fires: `orchestrator.run_cycle` gates
  it on `council.should_run_r1` (fires iff `_r0_conflict` AND the R0 signature is new
  vs the prior cycle). Several `council.py` R1 docstrings still say "always-fires" —
  **those are stale**; the orchestrator gate is authoritative.

### What remains for M1 (not done this session)

- Map Casper's in-cycle inputs (`world_state`, `recent_outcomes`, `cycle_phase`) to
  Google **Sessions**; decide the **Memory Bank** seeding (persona carried via
  Instructions; self_model NOT carried). Enter the JSON output schema in Studio and
  set the model (`google_ai/gemini-3-flash-preview` per `casper_config.json`).
  Per-cycle prompt assembly (the ~5–8k-token rebuilt-from-SQLite payload) is part of
  the M5 infrastructure port, not the Studio agent definition.

## Session 2026-05-29 — migration scoping + Letta surface audit

Documentation/scoping session (read-only on source; the system stayed shut down).
Three outcomes: migration direction locked, the Letta surface audited end-to-end,
and the four `LETTA_SURFACE_AUDIT.md` §7-H verification questions resolved.

### Migration direction — LOCKED

- **Vendor mapping (per-agent native rebuild, each agent STATEFUL on its
  platform):** Casper→Google (Gemini Enterprise Agent Platform / Gemini API Managed
  Agents; Memory Bank for persistent memory, Sessions for in-cycle state);
  Melchior→OpenAI (Responses API + Conversations API; extended
  `prompt_cache_retention` up to 24h); Balthasar→Anthropic (Claude Managed Agents,
  beta header `managed-agents-2026-04-01`; native "dreaming" scheduled memory
  consolidation in research preview; session-hour runtime $0.08/hr, opened
  per-cycle, not 24/7).
- **State ownership:** the vendor owns each agent's memory, persona, self_model
  equivalent, and thread history — we do NOT mirror agent state locally. We own
  SQLite for everything non-agent (`debate_records`, `magi_gate_events`,
  `magi_alerts`, `grid_state`, inventory, all trading-side state).
- **Seeding:** each agent seeded from `snapshots/letta_shutdown_2026-05-28/`
  (persona text, self_model contents, accumulated thread history: casper 825 /
  melchior 466 / balthasar 704 msgs), and tuned on the vendor platform before going
  live in the new infrastructure.
- Full target write-up in `00_PROJECT_OVERVIEW.md` "Migration target architecture".

### North star restated (why the migration exists)

MAGI is an ADAPTIVE grid bot whose purpose is to solve the static-grid weakness:
regime-blindness in directional markets (catches falling knives in downtrends,
sells into rallies, bleeds in sustained moves). Every migration decision must
preserve or improve regime detection + reaction speed vs a static grid. NEXT_BUILD
item 0★ (grid bleeds in downtrends) is exactly this problem in the old system; the
migration is the chance to fix it via better-tuned native agents. **Not a
cost-optimization project** — cost was the trigger, adaptiveness is the purpose.

### Cadence / call model clarified (event-driven)

The call model is event-driven via the gate; the 4h `MAGI_HOURS_EST` schedule is a
backstop, not the primary trigger. Target steady state: **no active grid** → ~1
call/day at a designated time for assessment / daily recap (gate keeps monitoring
continuously); **active grid** → scheduled cycles + gate-driven wakes, with the
existing wake-class trigger curation (`WAKE_REQUIRES_ACTIVE_GRID`,
`WAKE_DWELL_MINUTES`, `WAKE_MIN_INTERVAL_MIN`, conditional R1 via `should_run_r1`)
bounding cost. Documented as the target so future sessions don't re-derive it from
the cost-reduction history.

### Letta surface audit — COMPLETE (`LETTA_SURFACE_AUDIT.md`)

15-agent dynamic Claude Code workflow (2026-05-29): 7 file groups enumerated by
Sonnet, peer-reviewed by Sonnet, adversarial sweep + synthesis on Opus. ~19 min,
~760k tokens; saved as `.claude/workflows/letta-surface-audit.js` for reuse.
- **112 unique touch-points across 17 files.** Counts: SDK_CONSTRUCT 12,
  AGENT_INVOKE 3, AGENT_LIST 7, BLOCK_CRUD 19, BLOCK_ATTACH 1, THREAD_COMPACT 1,
  THREAD_RESET 1, TOOL_DEF 0, MODEL_CONFIG 12, IMPORT_ONLY 12, INDIRECT 44.
- **Centre of gravity: `council.py`** (all 3 AGENT_INVOKE sites). Density top
  files: council.py 14, provision_agents.py 14, config_validator.py 14,
  scheduler.py 12, database.py 9.
- **Per-agent state collapses post-migration:** most of the 8 Letta blocks become
  vendor-native or per-call prompt content; only `world_state`, `recent_outcomes`,
  and `cycle_phase` need re-delivery as prompt content (rebuilt fresh from SQLite);
  persona / self_model / thread history live vendor-side. Per-call payload
  ~80–114k → ~5–8k tokens.
- **Retired, not migrated:** `magi/memory_lifecycle.py` (vendors consolidate memory
  natively — Claude dreaming, Gemini Memory Bank, OpenAI Conversations chaining;
  30-cycle rotation goes away); most of `magi/provision_agents.py` (agents
  built/tuned on vendor platforms); the shared `*_r0_output` blocks (redundant — R1
  already pastes peer outputs into prompts); `sweep_letta_steps_for_failures` raw
  HTTP (→ per-vendor SDK exception handling); the dashboard LETTA AGENTS panel (→
  generic "agents reachable" health check).

### §7-H verification — all four resolved

(Read-only verification, 2026-05-29; full citations in `LETTA_SURFACE_AUDIT.md` §7-H.)
- **should_run_r1 — REAL GATE, not dead code.** `council.py:1445-1463` returns
  three outcomes (skip on no conflict; skip on frozen standoff; fire only on a
  NOVEL conflict); `orchestrator.py:1876-1895` honors it with an explicit
  `if fire_r1:` guard (R1 skipped + `round_1={}` otherwise). CLAUDE.md's
  conditional-R1 description is correct. **Doc drift flagged (fix during the
  port, not now):** the `run_round_1` docstring at `council.py:1466-1472`
  ("Always-fires" / "run_cycle calls this unconditionally") and the "always fires"
  phrasing in `00_PROJECT_OVERVIEW.md`'s *historical* Architecture + Cycle-protocol
  sections are stale — R1 has been conditional since 2026-05-27. Historical drift,
  not a logic concern.
- **emit_human_alert — DEAD CODE.** Defined `council.py:1638`, imported
  `orchestrator.py:59`, never called anywhere (repo-wide grep: only the def + the
  import). Both the function and the unused import are safe to delete during the
  port.
- **wake_guard_sim.py — keeps its purpose; Letta-free in test scope.** It
  exercises `_is_wake_suppressed_nontrading` + `_dwell_t2/t14/t11` (wake-guard
  logic, no Letta). Audit nuance corrected: it imports `scheduler` *lazily inside
  `main()`* at `wake_guard_sim.py:87`, not at module level, so importing the module
  has no side effects. **More significant finding → port prerequisite:**
  `council.py:238` constructs the Letta client at MODULE-IMPORT time, so every
  module that transitively imports `council` (scheduler, orchestrator, and every
  test/CLI in that chain) inherits a hard Letta dependency at import and will fail
  once Letta is removed. The port MUST make client construction lazy/deferred before
  or as the first step of the council rewrite — filed as `02_NEXT_BUILD_TASKS.md`
  item M5a.
- **extract_test_cases.py — one-off manual script, NOT a live consumer.** Top-to-
  bottom executable, no `__main__` guard, not imported by anything, not invoked by
  any service or cron (verified `magi.service`=`python3 -m scheduler`,
  `magi-dashboard.service`=`python3 -m dashboard`; no crontab/systemd reference).
  CLAUDE.md §4 corrected accordingly — the `magi_decisions` dual-write justification
  rests on `learning.py` (live-path verification pending, item M6) and unmigrated
  dashboard panels, not this script.

### Migration artifacts — Balthasar (Anthropic / CMA)

Balthasar's native artifacts have been built on Claude Managed Agents
(beta header `managed-agents-2026-04-01`). The droplet port (M5) will
reference these IDs directly.
agent_id:        agent_01WhVAYWK7FnZxDtL6ZjSEZz
version:         v2  (full persona loaded from snapshot)
model:           claude-haiku-4-5
memory_store_id: memstore_014Joi8XcyEQfXMvzimZmBED
memory_id:       mem_01ASJucupxm2PRHfwXzfdkk3  (/self_model.md, 25,070 bytes)
environment_id:  env_01SvVPP6NrBcgjshQQFZ3nte

**Smoke test (2026-05-29):** real world_state from cyc shutdown sent to a
fresh session (sesn_011XjUHznBc91nTXyBJ6YTtq). Balthasar returned full
schema-compliant JSON, walked his decision tree explicitly, correctly
applied the stranded-grid carve-out (PROCEED, not HOLD_GEOMETRY) for the
T14 one-sided book + fillable=false geometry he was shown. Stop reason:
clean end_turn. Cost ~$0.02. No regression from the corrected logic in
the seeded self_model.

**Persona ported verbatim from snapshot** at
`snapshots/letta_shutdown_2026-05-28/balthasar_blocks.json` block label
"persona" (18.8 KB system prompt).

**Self_model ported verbatim** from the same snapshot, block label
"self_model" (25,070 bytes). The curated post-runaway-HALT-cleanup
version, including the cyc_1779854457 HOLD_GEOMETRY correction. Lives at
`/mnt/memory/balthasar/self_model.md` when sessions attach the store.

**Two doc-drift items the migration surfaced (defer to M5 port; do not fix
in docs now):**
- Persona text says "R1 always fires." Stale — `should_run_r1` is a real
  gate. Already flagged in this session's §7-H finding 1. Fix during port.
- Balthasar's smoke-test reasoning referenced `[RECENT_POSITION_HOLD]`
  hard rule name from Letta. Hard-rule names may change in the new
  orchestrator. Re-verify name during M5.

**Not yet attached to a session at runtime:** the memory store was created
and seeded but the smoke test session did not include it in the `resources`
array. Balthasar's persona alone produced correct behavior without reading
the memory file. Memory-attachment path needs verification when council.py
is ported — open question is whether memory access requires
`agent_toolset_20260401` to be enabled, which we explicitly chose not to do.

### Still open (gating the rebuild spec)

- Audit §7 design questions: themes A (statefulness — answered: vendor-side),
  F (provider mapping — answered: locked), G (LETTA AGENTS panel — answered:
  retire) are settled; **themes B, C, D, E remain open** and need operator
  decisions before per-agent rebuild specs finalise.
- Per-agent rebuild specs — not yet written (items M1–M3).
- Infrastructure port (council.py + 6 wrappers, retire the rest) — item M5, after
  at least one native endpoint/stub exists; lazy-client-construction (M5a) first.
- learning.py live-path verification — item M6; gates whether the `magi_decisions`
  dual-write can be retired.
- Replay verification — item M7, third workflow, downstream.
- Verify CMA memory store attachment works without prebuilt agent
  toolset enabled; if it requires the toolset, decide whether to enable
  minimal file-read tools on Balthasar (v3) or pass the self_model
  content inline in the user.message per cycle.

## Session 2026-05-28 — SHUTDOWN (Letta migration prep)

Controlled stop of the running system to migrate off Letta Cloud and rebuild the
council on each vendor's native platform (OpenAI / Anthropic / Google) with native
caching and owned state. Stop-and-preserve only; at the time of shutdown the
rebuild was a separate, not-yet-started decision (**since scoped and locked
2026-05-29 — see Session 2026-05-29 above**). Driven by the council token-cost investigation earlier
this session (see below / the cost memory): ~$5-6/day council spend on a ~$67 book
is economically inverted, and ~half is structural (4h cadence ≫ provider cache TTLs,
3 agents × cold-cache input/cycle) that no Letta-side config fixes.

What was done (all completed, no failures):
- **Services:** `magi.service` + `magi-dashboard.service` **stopped + disabled**
  (`systemctl disable`; won't auto-start on reboot). Scheduler logged a clean stop
  at 18:49:07 UTC. Self-hosted Letta Docker confirmed still down (no containers).
- **Kraken (was LIVE, not paper):** 3 real open orders cancelled via
  `KrakenExchange.cancel_all_open_orders()`, 0 remaining (verified). All were grid
  orders at the fixed 1.65 XRP size: sell @1.34010, buy @1.29095, buy @1.30078.
  XRP/USD inventory was NOT liquidated.
- **Snapshot** at `snapshots/letta_shutdown_2026-05-28/`:
  - Letta agent state (read-only): per-agent config, all 8 memory blocks (full
    text), full recall-storage message history (casper 825 / melchior 466 /
    balthasar 704 msgs), 3 tool defs. Balthasar `self_model` preserved at its
    bloated 25,069 chars (the cycle-60 `merge_failed` / skipped-reset cause).
  - `db/observer.db` (12.9 MB, integrity ok) + empty `magi.db`. Row counts:
    debate_records 231, magi_decisions 435, token_usage 1001, memory_rotations 9.
  - `config/`: `.env`, `config.py`, all five handoff docs, orchestrator/council/
    memory_lifecycle modules, and the three persona prompt files.
  - `git_head.txt` (HEAD 6dc4345 "doc update"), `git_status.txt`, `git_diff.txt`
    (clean tree). Plus `SHUTDOWN_NOTES.md` with the full manifest.
- **Last cycle that ran:** cyc_1779984050 @ 2026-05-28T16:00:50 UTC (scheduled;
  Casper RANGING / Melchior RECENTRE / Balthasar CLEAR; R1 fired, last billed call
  16:02:20).
- **Letta agents NOT deleted** — they persist on Letta's servers, reachable via the
  API. Do not cancel the Letta subscription until the rebuild is complete and the
  snapshots are verified usable.
- **Stale-process note:** two orphaned Claude Code shell loops from 2026-05-07
  (PIDs 671668, 677419) are still polling observer.db; not MAGI, harmless,
  recommend killing — left running pending operator confirmation.

Everything below this section describes the system **as it ran up to the shutdown**.

## Session 2026-05-28 changes

### PnL tracking overhaul — live-scoped + equity-based (dashboard)

The dashboard "Live P&L" panel (was mislabeled "Paper P&L") was reporting a
headline that was ~106% paper-trading history. Two root causes, both fixed in
`grid/pnl.py:get_pnl_snapshot`:
- **Paper/live commingled.** `grid_orders` has no paper flag; the snapshot
  counted all 68 filled rows. 50 were pre-live paper (hex order_ids, 7–24 XRP);
  18 are live Kraken fills (txid `O…-…-…`, 1.65 XRP). Now scoped to live via
  `grid/pnl.py:_is_live_order_id` (Kraken-txid shape).
- **Held inventory invisible.** Cumulative sells ≥ buys, so the FIFO buy-queue
  always drained → `unmatched_buys=0`, unrealized always $0, hiding the held
  ~31-XRP bag's drawdown. Total is now **equity-based**:
  `total = current_equity − baseline_equity`, baseline = inventory at the first
  live fill marked at that fill's price (≈$68.44); realized = FIFO on live
  round trips; `unrealized = total − realized`. Real live PnL ≈ **−$2.3**
  (was shown as +$7.46). `live_pnl_pct` denominator switched to baseline equity.

### Dashboard — Eastern time + GRID STATUS panel

- **All displayed times now US Eastern** via `_to_et()` registered as Jinja
  filter `et`; internals stay naive UTC. Uses America/New_York so the label
  auto-switches EST/EDT. Fixed a pre-existing double-"EST EST" header.
- **"CODE · STATUS" box → "GRID STATUS"**: added **Grid Active? Y/N** with a
  status word (ACTIVE/PAUSED/HALTED/NO ORDERS/DOWN), real **LIVE/PAPER mode**,
  and resting buy/sell counts. The old Mode row read `engine.paper` — but the
  dashboard's own engine runs PAPER (its systemd unit doesn't source `.env`), so
  it was wrong. Real mode now read from the on-disk 3-factor gate via
  `_configured_live()`; Grid-Active from `grid_orders` status='open' + kill
  switch + scheduler + last council action.

### observer + readiness scoped to live fills; pnl_24h backfilled

- The all-fills FIFO let the 29 paper sells (259 XRP) drain the entire buy queue
  (incl. all live buys) before any live sell, so **every live cycle's `pnl_24h`
  was 0.0** and readiness L4 was a FALSE GREEN on +$7.46 paper. Scoped
  `observer._compute_window_metrics` and `readiness._all_fills` to live fills.
  Readiness verdict now truthfully **RED** (L4 = −$0.17, L1 = 17 live trips).
  `observer` runs in magi.service → takes effect on the next restart.
- Backfilled 26 live-era `pnl_24h` rows with the corrected logic
  (`scratch/backfill_pnl24h.py`; snapshot in `/tmp`). Attribution panel can now
  distinguish actions (RECENTRE +$0.0075 avg vs MAINTAIN −$0.0089 avg).

### Post-restart council audit (9 cycles since 2026-05-27 16:09) + roc_6h fix

- **Judgments healthy:** no degradation, varied positions/convictions, no
  SAFE_DEFAULTS. The two RECENTRE cycles were correct (book genuinely one-sided).
- **Gate healthy:** wake-class triggers fired right (T14 caught the stranded grid
  at 22:17 + 03:24; T11 on vol-regime flips; T13/T2 logged without waking). The
  11:42 `gate_ws_down` was a transient WS blip that self-recovered. No bleed.
- **`roc_6h` null on 4/9 cycles** — 6h candles are a separate fragile Kraken
  fetch (no native 6h interval) returning [] on flake. Fix STAGED:
  `observer._resample_6h_from_1h` falls back to resampling the 1h bars already
  fetched that cycle (verified byte-identical to the direct fetch). Starved
  Casper's regime call.
- **Capital-preservation finding → NEXT_BUILD item 0★** (grid bleeds in
  downtrends; the council can't learn it because its only outcome signal is
  realized round-trip PnL, which stays positive while inventory bleeds
  unrealized). Deterministic stand-down rule recommended; deferred past this
  restart.

**Staged for the next magi.service restart (~$0.30 startup cycle):**
`observer.py` roc_6h fallback + `_compute_window_metrics` live-scoping. Dashboard
changes are already live (magi-dashboard.service is separate; reloading it is
free). Files touched: `grid/pnl.py`, `dashboard.py`, `observer.py`,
`magi/readiness.py`, `scratch/backfill_pnl24h.py`.

## Session 2026-05-27 changes

### Council cost-reduction — freshness materiality band + conditional R1

Audited council spend since the 2026-05-26 re-enable: ~$4.97 over 6 cycles
(~12h) ≈ $5-6/day steady-state on a ~$67 book. Not gate-wakes (zero since
resume) and not evals (zero) — the plain scheduled council. Real per-cycle cost
~$0.80-1.00 (~8.3 LLM calls/cycle, not 1): 3 R0 + 3 R1 (R1 was unconditional
always-fires) + ~2.3 freshness-retry R0 calls. Melchior/gpt-4o = 65% of spend.

**(1) Freshness materiality band** (`magi/council.py`). 7 days of
`[FRESHNESS_FAIL]` logs showed ~67/69 retries fired on cosmetic precision drift
(e.g. 39.41 vs 39.39, a static reference stat cited at slightly wrong precision),
not staleness — each a wasted ~90k-token re-call. Added `_within_freshness_tolerance`
(`max(5% relative, 0.02 absolute)`) + constants `_FRESHNESS_REL_TOL`/`_FRESHNESS_ABS_FLOOR`;
`_validate_r0_freshness` only flags a cited value as stale when it diverges from
its closest world_state candidate beyond tolerance. Validated: 16/18 sampled real
mismatches now skip; the genuine catch (autocorr cited ~10x off — model anchoring)
still fires. ~96% retry reduction.

**(1b) Freshness matching-gate fix** (`magi/council.py`, restarted 16:09 UTC).
The band in (1) was INCOMPLETE: `debate_records.freshness_retries` showed
`casper:true` EVERY cycle post-deploy. Root cause sits upstream of the band, in
`_find_closest_fresh`'s plausibility gate. Casper's lead evidence always DERIVES
the price-vs-EMA200 % distance ("Price -22.41% from EMA200") — a figure with NO
literal world_state field (only raw `ema_200`/`ema_50` exist). The old absolute
gate (`abs(stale-cand) <= max(|stale|,1.0)`, a ±22.41 window) matched -22.41 to the
unrelated `bullish_trend.drawdown_median=-2.45` (~9x smaller) and fired a full
~85k-token retry every cycle. Two coupled fixes: **(a)** relative-capped gate
`max(_FRESHNESS_MATCH_REL(0.5)*max(|stale|,|cand|), _FRESHNESS_MATCH_ABS_FLOOR(1.0))`
rejects large-magnitude cross-quantity matches while keeping small-magnitude
matching BYTE-IDENTICAL to before (ABS_FLOOR=1.0 == old floor; proven: of 6 probes
only -22.41 changed); **(b)** `_validate_r0_freshness` now treats no-analog
(`correct_val is None`) as FRESH/skip, not as a hallucination → stale — required,
since (a) alone just moves -22.41 from "matched-stale" to "no-analog-stale" (still
retries). Verified offline on live ws `cyc_1779891312`: Casper's -22.41 line →
stale=False. Tradeoff (operator priority = stop the credit bleed): a confabulated
value with no nearby field now passes unflagged; genuine stale-recitation is still
caught because a drifted copy keeps a close analog (the field's current value) and
trips the materiality band. NOT eval-covered (pipeline change). Production
confirmation pends the next scheduled cycle (16:00 EST / 20:00 UTC): `casper`
should read `false` on cycles where his lead evidence is the EMA200 distance.

**(2) Conditional R1 via novelty-aware gate** (`magi/council.py` +
`magi/orchestrator.py`). R1 was unconditional since 2026-05-22. A pure conflict
gate was insufficient — Casper `regime_action`=STAND_DOWN in 45/46 live cycles,
so a chronic RECENTRE-vs-STAND_DOWN standoff makes "fire on conflict" fire ~65%.
Added `should_run_r1` + `_r0_conflict` + `r0_position_signature` (council) and
`_prior_r0_signature` + a conditional block in `run_cycle` (orchestrator): R1
fires only when a genuine position/lever conflict exists AND the R0 position
triple differs from the prior cycle. Aligned cycles and frozen standoffs skip
(the hard-rule layer resolves both regardless). Grid-state conflicts deliberately
excluded (hard rules own them). Simulated over 46 cycles: R1 fires 11/46 (23%),
~76% fewer R1 calls. Skip path verified: `resolve_consensus` falls back to R0
finals (incl. R0 regime_action/geometry_veto); `insert_debate_record` writes NULL
R1 columns (dynamic INSERT from present keys — no crash). Over 46 cycles R1's
position shifts never once changed the applied action (hard rules + Melchior R0
decided everything).

**Verification:** `py_compile` clean; `should_run_r1` exercised on 5 synthetic
cases; restarted ~11:28 UTC. NOT eval-covered (pipeline change). Validate live via
`journalctl -u magi.service | grep "Round 1:"` (firing/skipped lines) + the
`council_r1` call rate in `token_usage`.

**Remaining cost levers** (`02_NEXT_BUILD_TASKS.md` item 0): P1 (council cadence
as a function of trading state — biggest, still unbuilt), P2 (gpt-4o for Melchior),
P4 (trim Letta threads).

### Casper STAND_DOWN mis-calibration — diagnosed + fixed (the grid-parking root cause)

Audited why the grid stays parked: Casper's `regime_action`=STAND_DOWN in 45/46
cycles. Mechanical decision-tree replay over 38 cycles showed ALL 38 fired
TRENDING solely via STEP-1 condition 4(b) (`adx_neg > adx_pos`) with **ADX
14.6-15.4 the whole window — never >= 20**. That is directional bias in a weak,
low-vol (atr_pct ~17), mean-reverting (autocorr_1h negative) tape ~22% below
EMA200 — exactly the "stale structural base = RANGING with a low base" Casper's
own Example A describes, i.e. grid-FAVOURABLE. Root cause: condition 4(b) had no
ADX strength floor. Compounding it, Casper's self_model had hardened the bug into
doctrine: Pattern 2 codified "adx asymmetry overrides missing momentum →
TRENDING", and Patterns 7/8 rationalized fills as an "Activity Trap" and grid
death (0 orders) as a "successful survival outcome" — a corruption of the same
shape as the Balthasar runaway-HALT.

**Fix (deployed live, provisioned, schema PASS):**
- `magi/prompts/casper_prompt.txt`: condition 4(b) now requires `adx >= 20` for
  the directional-asymmetry branch to count as momentum (roc_6h 4a and autocorr
  4c paths unchanged, so real trends still register); STAND_DOWN definition
  tightened to a CONFIRMED trend (Step 1 + adx>=20 + roc/autocorr) or a fired
  regime-shift trigger.
- Casper self_model curated via Letta API: removed the no-floor-asymmetry,
  activity-trap, and grid-death-is-success patterns; replaced with corrected
  reflections (weak ADX ≠ trend; low-vol mean-reversion is grid-favourable;
  parked grid is a cost not a survival win). Casper is Gemini → reads blocks
  fresh each cycle, so no thread reset needed (unlike Balthasar/Haiku).
- Snapshots: `/tmp/casper_{persona,self_model}_2026-05-27_pre-adx-floor.json`.
- **NO evals run** — operator declined (expensive). Validation was the 38-cycle
  counterfactual: 31/38 → RANGING/EXECUTE with the floor; the 4 roc-confirmed
  cycles stay TRENDING. Regression risk low (the change only adds an ADX floor to
  one of three momentum paths).

**Verification (12:44 + 12:01 cycles post-fix):** Casper's REASONING changed as
intended — it now cites `roc_6h` momentum and reads the curated self_model
("Pattern 4 validates STAND_DOWN as roc_6h confirms a persistent trend"), not the
bare weak-ADX asymmetry. It is still STAND_DOWN right now, but legitimately:
`roc_6h=-1.60` is a genuine momentum-confirmed downtrend, so condition 4(a) fires
and standing down is correct. The fix's effect shows when the downtrend exhausts
(roc moderates → weak-ADX range): Casper will now flip to RANGING/EXECUTE instead
of staying stuck on the asymmetry. Watch for that transition.

### Bug fixes + P4 (later 2026-05-27)

- **`applied_grid_action` write-back — FIXED.** The column (+ `applied_spacing`,
  `engine_clamped`, `clamp_reason`) was documented "filled in later by the engine"
  but never written (NULL in 0/48 rows). `grid/engine.py:apply_magi_decision` now
  records what it applies into `self.last_applied` (additive side-effect, no
  control-flow change — captures cross-check coercion, empty-book-guard skips,
  null-geometry refusals, spacing clamps); scheduler writes it back via new
  `database.update_debate_applied`. Verified live: 12:44 cycle wrote
  `applied_grid_action=MAINTAIN`, `engine_clamped=0`. The OTHER half of the old
  "Rule 0d" bug (tag recording) was already fixed — verified working over 48
  cycles; the doc was stale. See `02_NEXT_BUILD_TASKS.md` item 1.
- **P4 — observer outcome-backfill routed out of the agent threads.** The 6h
  backfill used to POST a user message to each agent (`_notify_agents_6h` →
  `messages.create`) — a billable inference per agent per cycle, thread-bloat (the
  dominant 80-114k-token context driver), AND the "consider updating your
  self_model" prompt that drove the Casper/Balthasar self_model corruption. Now
  `observer._record_outcome_to_block` writes a rolling 6-cycle log to a new shared
  read-only `recent_outcomes` block (created + attached to all 3 agents; added to
  `provision_agents` shared-block list). Agents still see outcomes (in-context,
  fresh each cycle) but with no inference cost, no thread growth, and no
  self_model-write invitation — self_model now evolves ONLY via the 30-cycle
  rotation. This also completes item 2's L1 lever. Removed the now-dead
  `_send_outcome_to_agent`/`_build_outcome_message`/`_backfill_failure_streak`
  machinery + unused import. **Contingency note:** the council-degradation hook
  "backfill-notify alerting" (CLAUDE.md §4 item 2) is GONE — agent unreachability
  is still caught on the R0 path (`_check_steps_for_alerts`/`_alert_exception`).
  Restarted 12:44 UTC; clean startup, both write-back paths exercised.

## Session 2026-05-26 changes

### BOT IS LIVE AGAIN
- **magi.service re-enabled and started ~19:49 UTC 2026-05-26** by the
  operator. Startup confirmed all three live gates PASS → "LIVE MODE ACTIVE —
  real orders will be placed." (Note: the recurring "Paper mode active" log
  lines are the throwaway `GridEngine(paper=True)` price-fetch helpers in
  `build_world_state`, NOT the trading engine.)
- Capital intact (~$67: ~30.8 XRP + ~$26 USD). On restart the grid was **dark
  (0 open orders)** — see the standdown/HALT history below; not a loss event.

### Stranded-grid judgment fix (Layer 1, NOT a new hard rule)
Root problem behind the long standdown: the council reads "price trended out of
the grid band" as "hostile regime, stand down," when the correct response to a
*stranded* grid (stale centre, price outside the band, can't fill) is to
RECENTRE and follow price. Agents had **no signal** for whether the grid was
stranded. Fix = one new input + two persona reframes (deliberately judgment-layer,
not another override — see operator directive to stop diluting hard rules):
- **`world_state.grid_position`** — new field, `{side: inside|above|below,
  pct_outside_band, fillable}`, computed in `orchestrator._grid_position` from
  the same centre ± n_pairs·spacing envelope T2 uses. Declared in
  `world_state_schema.FIELDS` (consumers: casper, balthasar). `fillable=False`
  = stranded.
- **Balthasar persona** (`balthasar_prompt.txt`): stranded-grid carve-out in
  the geometry_veto calibration + R1 escalation — when `grid_position.fillable`
  is false, a RECENTRE is risk-REDUCING; do not RISK_BLOCK it on regime alone.
- **Casper persona** (`casper_prompt.txt`): stranded-grid carve-out in the
  regime_action calibration — STAND_DOWN blocks *trend-chasing* rebuilds, not a
  *corrective* RECENTRE on a stranded grid; emit DEFER_STRUCTURAL/EXECUTE there.
- **Eval gate passed** (regression): Casper 7/8 (0.875), Balthasar 7/9 (0.778),
  both > 0.70. **Caveat:** the suite grades `position` (risk_action/regime), NOT
  the `geometry_veto`/`regime_action` the fix targets — so the eval confirms
  no-regression, not the fix's live effect. Provisioned to live agents
  (casper +1543 chars, balthasar +1704 chars; melchior unchanged → skipped).

### Balthasar self_model corruption found + curated
- Live cycle after restart showed Balthasar voting **HALT @ 0.95 conviction on
  a healthy book** (buffers fine, skew 0.11). Cause: his self_model had a
  **runaway** — ~16 logged "consecutive grid deaths under STAND_DOWN" that
  misattributed *idle/stranded-grid cycles* (every one was **0 fills, $0 P&L**,
  i.e. not filling — not losing) to the STAND_DOWN regime, concluding "only
  remedy is HALT." Self_model had ballooned 3.7KB→31.5KB during the 5-25
  standdown.
- **Curated** the self_model block: kept the ~13 sound reflections (incl. the
  correct "regime signals are Casper's context, not my survival gates" and the
  healthy-buffers→CLEAR→fills validation series), removed the runaway, appended
  a dated retraction tying the real cause (stranded grid) to `grid_position`.
  31.5KB → 13KB. PRE/POST snapshots in `/tmp/magi_persona_snapshot_20260526/`.
- **Block curation alone did NOT change behavior** (cycle 249 still cited "16
  grid deaths") — because the narrative also lives in the agent's **Letta
  conversation thread** (stateful `messages.create`; 40 of 299 messages carried
  it). This is the documented conversation-history-persistence failure mode.
  **Reset Balthasar's in-context thread** (`agents.messages.reset`); archive
  persists by design (messages.list still shows 299 — that's Letta's permanent
  store, harmless). Next scheduled cycle is the definitive test.

### Anchoring is model-specific (verified, supports the council-diversity premise)
Checked R0 evidence repetition across cycles: **Melchior (GPT-4o)** anchors
mildly (sticky verbatim self_model lead line, rest updates, vote still moves);
**Balthasar (Haiku)** anchored severely (full narrative replay); **Casper
(Gemini)** does NOT anchor (evidence tracks live numbers; its STAND_DOWN is a
correct read of a –22% EMA200 deviation, not replay). So Casper needs no thread
reset; Melchior's mild anchor isn't the current blocker.

### ⚠ Model discrepancy to reconcile
Live **Balthasar runs on `anthropic/claude-haiku-4-5`** (per `token_usage` and
the provision summary), but CLAUDE.md §1 says claude-sonnet-4-6, and the eval
factory builds Balthasar on **sonnet-4-6**. So the eval validates a *stronger*
model than production uses — a real validation gap, and haiku is less steerable
(it's why the persona/self_model edits needed the thread reset to take). Open
question: intended cost-downgrade or config drift? `AGENT_CONFIG` in
`provision_agents.py` sets no model handle, so provisioning won't change it.
Tracked in `02_NEXT_BUILD_TASKS.md`.

### Current live state (as of ~20:17 UTC) — what to watch
Grid dark (0 orders), price ~1.332 **inside** the band (so grid_position
fillable=true; the stranded carve-out is dormant — not the active lever right
now). Council still in HALT/STAND_DOWN from pre-reset cycles. **The next
scheduled cycle** (post-reset) is the test: Balthasar should vote CLEAR/PROCEED
and the grid can rebuild. Gate wakes in the interim are suppressed by
`WAKE_REQUIRES_ACTIVE_GRID` (last cycle was non-trading), so nothing fires
off-schedule. Money safe throughout.

## Session 2026-05-25 changes

### magi.service halted — Letta credit burn (T2 over-firing)

- **magi.service stopped and disabled 2026-05-25 ~20:25 UTC.** Letta plan
  balance burned from ~$20 to ~$1.93 in roughly 24h (expected ~$1.80/day
  at 6 cycles/day, actual ~$18/day = 5× budget). magi-dashboard.service
  left running. Grid stays paused. No code or DB modified during halt.

- **Root cause (diagnosed, not yet resolved):** T2 (`gate.py:t2_grid_breach`)
  is a *level-triggered* predicate — it fires whenever 2+ consecutive 1h
  closes are outside the grid's outermost level. The grid centre is 1.33618
  with 0.75% spacing and 5 levels: upper bound = 1.35622. Current XRP price
  ~1.357 has been consistently above 1.35622, so T2 fires at every 1h candle
  close. The scheduler's `WAKE_MIN_INTERVAL_MIN = 60` throttle is satisfied
  each time because T2 fires exactly once per hour. Result: 30 cycles in 24h
  instead of the documented 6 (5× overage). At ~$0.60/cycle (R1 synthesis
  firing plus grown context windows) the math is $18/day. The bot has also
  been stuck in `PAUSE_INVALID + geometry_veto=RISK_BLOCK` since ~14:00 UTC,
  so every one of those cycles produced `MAINTAIN / CLEAR` and zero action —
  pure credit burn.

### Gate-wake precondition guards shipped (2026-05-26: generalized + dwell)

Implemented in `scheduler.py` and `config.py`. `gate.py` *detection* is
unchanged — it still evaluates and writes events on the 1h close. Only the
*wake decision* (whether an event triggers an off-schedule council cycle) is
guarded. Two stages, in order, applied to ALL wake-class triggers
(`WAKE_CLASS_TRIGGERS = ("T14","T2","T11")`):

**Stage 1 — non-trading suppression.** Originally T2-only (the May-25 bleed
was T2 re-firing hourly for 12h during a PAUSE_INVALID/REGIME_STANDDOWN
standdown → 10 useless wakes, ~$6 credits). Generalized 2026-05-26: T11/T14
share the shape (a wake the standing-down council can't act on), so the guard
is now trigger-agnostic.
- **`config.py:WAKE_REQUIRES_ACTIVE_GRID = True`** (renamed from
  `T2_REQUIRES_ACTIVE_GRID`) — master switch; False disables.
- **`scheduler._WAKE_BLOCKING_OVERRIDE_PREFIXES`** — tuple of non-trading
  override prefixes: `[PAUSE_INVALID]`, `[REGIME_STANDDOWN]`, `[HALT]`,
  `[GRID_DEGENERATE]`, `[COUNCIL_COLLAPSED]`, `[GRID_PAUSE]`, `[KILL_SWITCH]`,
  `[DAILY_LOSS_LIMIT]`, `[ALLOC_SKEW_CEILING]`, `[AGENT_DEGRADED` (prefix).
- **`scheduler._is_wake_suppressed_nontrading() → (bool, str)`** (renamed
  from `_is_t2_wake_suppressed`) — queries DB on every call (no caching);
  `(True, reason)` when the latest `debate_records` row has any blocking tag
  OR `geometry_veto='RISK_BLOCK'`, or `grid_state.halt=1`. DB failure →
  `(False, '')` so errors never permanently suppress.

**Stage 2 — persistence dwell (new 2026-05-26).** A wake-class condition
must PERSIST before spending a cycle, so a transient breach/flip/one-sided
blip that has resolved by council-time doesn't wake.
- **`config.py:WAKE_DWELL_MINUTES = 15`** — required persistence. 0 disables.
  Short vs the 1/hr throttle + 4h scheduled cadence, so low added latency.
- **`scheduler._wake_dwell_status(trigger_id) → (status, reason)`** where
  status ∈ {`wake`, `defer`, `drop`}. `defer` = live but not yet dwelled →
  re-checked next 60s loop, NOT consumed, NO spend. `drop` = condition
  cleared → event consumed, no wake. Per-trigger liveness:
  - **T2** (`_dwell_t2`): all 1m closes over the last `WAKE_DWELL_MINUTES`
    must be beyond the SAME grid boundary (true continuous dwell on 1m
    candles). Falls back to event-age + latest 1h close when 1m history is
    short (startup / REST-fallback path).
  - **T14** (`_dwell_t14`): book must STILL be one-sided now AND event age ≥
    dwell; a refilled/emptied book → `drop`.
  - **T11** (`_dwell_t11`): live `vol_regime` must NOT have reverted to the
    pre-flip value AND event age ≥ dwell; reverted → `drop`.

- **`scheduler._consume_wake_gate_event(trigger_id, sentinel)`** (renamed +
  generalized from `_consume_t2_gate_event`) — marks ALL unconsumed fired
  events for the trigger consumed (not just newest) so deferred/cleared rows
  don't accumulate. Sentinels: `wake_suppressed_nontrading`,
  `wake_breach_cleared`.

- **Guard placement** in `scheduler.main()` loop: after
  `pending = _pending_wake_class_trigger()`, before `run_magi_cycle()`.
  Logs `gate_wake_suppressed` / `gate_wake_dropped` / `gate_wake_deferred` /
  `Gate wake:` per branch.

- **Verified** by `wake_guard_sim.py` at repo root (supersedes
  `t2_guard_sim.py`, deleted; delete this one after review). Imports the REAL
  scheduler functions: Stage-1 suppression against the live DB (current
  PAUSE_INVALID state → suppressed ✓), and wake/defer/drop dwell assertions
  for T2/T11/T14 against in-memory DBs — all pass.

- **Note:** T11/T14 are edge-triggered (regime flip; bilateral→one-sided
  book transition), so they don't re-fire hourly on a standing condition the
  way level-triggered T2 does — they were not the May-25 bleed. The guards
  now cover them anyway as defense-in-depth + transient filtering, at no cost
  when they aren't firing.

- **What remains (Problem 2, separate session):** why is the grid stuck in
  `PAUSE_INVALID + geometry_veto=RISK_BLOCK` since ~14:00 UTC 2026-05-25?
  The council votes `PAUSE_LONGS` every cycle; `enforce_hard_rules` vetoes it
  (`PAUSE_LONGS` with only 2 open buys and no justified order skew fails the
  PAUSE_INVALID precondition). Until that is diagnosed and resolved, the grid
  is not trading even after magi.service is re-enabled. `02_NEXT_BUILD_TASKS.md`
  item 0 tracks this. magi.service must stay disabled until Problem 2 is
  understood.

## Session 2026-05-24 changes

- **Per-order size is now FIXED at 1.65 XRP** (operator directive). New
  `config.py:ORDER_SIZE_XRP = 1.65` (the Kraken XRP minimum); imported in
  `grid/engine.py`. `compute_order_size` was rewritten: it previously
  divided total holdings across the side's level count (sells: `xrp/N`;
  buys: `(usd/N)/centre`), floored at the Kraken min and capped at half
  the side's inventory — on the small live book that produced 14–24 XRP
  orders (whole/half-position fills; e.g. the 2026-05-23 live fills of
  16.98 / 17.70 / 24.31 XRP). It now returns a flat `ORDER_SIZE_XRP` for
  any tradeable side (`0.0` when `target_count <= 0`, or for a buy with
  invalid centre). Both buy and sell sides return the same size, so grid
  round trips match 1:1 under FIFO. Holdings sufficiency is still enforced
  downstream: `build_grid_levels()` stops walking a ladder when the next
  order's size/cost exceeds remaining holdings, and `_execute_anchor()`
  aborts when the fixed size exceeds available inventory.
- **Downstream trace (verified, no other change needed):** the shadow
  simulator (`shadow_simulator.py:61`) sizes on its own model
  (`max_inventory_usd/half/centre`), but its variant ranking uses
  `get_rolling_pnl_pct = net_pnl / buy_capital` — a ratio where order
  size cancels — so the level-switch decision is size-invariant and was
  left untouched. Orchestrator hard rules (buffer floors, skew ceiling,
  `GRID_DEGENERATE`) key off holdings/value/counts, not order size.
  `grid/pnl.py:_fifo_match` handles arbitrary sizes. Dashboard has no
  order-size assumptions. The `MAX_INVENTORY_USD` cap is only ever
  under-shot now (fixed 1.65 deploys less than holdings-division did).
- **Capital-deployment consequence (by design, operator-accepted):** with
  fixed 1.65/order and `level_count=5` (~2–3 levels/side), only ~3–5 XRP
  and ~$5–7 USD of the ~32 XRP / ~$24 book deploys per rebuild; the rest
  idles. To deploy more capital, raise the level count (more orders of
  this size), NOT the order size.
- **Live service restarted 12:37 UTC to load the new code.** The trading
  engine (`scheduler.py:43` singleton) passed all three confirmation gates
  on startup — `LIVE MODE ACTIVE — real orders will be placed` — so the
  restart preserved live mode; the new sizing is live. The change takes
  effect on the next grid rebuild (RECENTRE / TIGHTEN / WIDEN or fresh
  init); on restart the 4 pre-existing live open orders (2× 8.82 XRP buys,
  2× 10.81 XRP sells, placed 2026-05-23T21:13 under the old model) were
  resumed from DB and rest on Kraken until that rebuild cancels+replaces
  them.
- **Forced rebuild executed 12:49 UTC** to apply the new size deterministically
  (Option B): stopped service → `engine.cancel_all_orders()` cancelled the 4
  stale orders on Kraken + marked DB rows cancelled → restart → empty-book
  startup ran `initialise_grid()` from scorer geometry. Result: a 1.65-XRP grid
  (sell anchor 1.65 @ 1.36162, fee $0.0090, txid O5SRYD; + 2 buy / 3 sell arms
  all 1.65). Every open order is now exactly 1.65 XRP.
- **Scorer fee basis switched TAKER → MAKER for per-level economics.** New
  `config.py:GRID_LEVEL_FEE_PER_SIDE = MAKER_FEE`. The `spacing_evaluator`
  scorer was being called with `fee_rate_per_side=TAKER_FEE` (0.40%) at all
  three sites (`scheduler.py:80` first-boot, `magi/orchestrator.py:465`+`473`
  Melchior world_state + current-config, `magi/gate.py:354`); now all use
  `GRID_LEVEL_FEE_PER_SIDE`. Rationale: resting limit arms fill as MAKER
  (0.25%), so the recurring per-level round-trip costs `2*MAKER_FEE` — charging
  taker wrongly rejected fee-positive tight grids and pinned the tightest
  selectable spacing at 1.0%. The one-time anchor IS taker, but that's an
  amortized setup cost, not per-level economics. Acceptability is still
  `spacing > 2*fee AND total_pnl_pct > 0` (never run net-negative levels) — only
  the fee input changed. **Validated on live candles before shipping:** under
  taker the rank-1 was 5lv/1.00% (~0.33 rt/day, and the last-24h window had ZERO
  acceptable variants → full stand-down); under maker the rank-1 is **5lv/0.75%**
  (last-24h ~2.0 rt/day, 168h ~1.29, 720h ~0.97 rt/day), all net-positive after
  maker fees. ~3–6× more fills. Service restarted 13:39 UTC to load it.
  - **NOT YET APPLIED TO THE LIVE BOOK.** The grid is still the 1.0% / 1.65-XRP
    grid from the 12:49 rebuild (book non-empty → restart resumed it). The
    scorer now *prefers* 0.75%, but the grid only adopts it on a rebuild — which
    is gated by Casper's standing `STAND_DOWN` (see audit below), or via a forced
    empty-book rebuild (first-boot path uses the scorer directly and would place
    0.75%). Pending operator decision.
- **Live audit (since 2026-05-23 cutover, ~16h):** surviving cleanly — no HALT,
  no critical alerts, no council degradation, no schema drift, no exceptions
  (3 transient Letta 520/521 warns recovered). BUT **zero organic round-trips**:
  the only live fills are anchor takers; every resting arm was cancelled
  unfilled. Root cause = spacing (1%) too wide for LOW-vol regime (ATR ≈0.52%,
  12h range 1.17%). Compounded by Casper voting `STAND_DOWN` every cycle
  (`[REGIME_STANDDOWN]` degrades Melchior RECENTRE→MAINTAIN; geometry frozen).
  Casper's read is defensible (price ~21% below EMA200, low ADX → "activity
  trap" avoidance). Melchior is the weakest participant: emitted non-null
  geometry in 1 of 388 cycles ever — the deterministic scorer is the real
  spacing brain (by design, per the GPT-4o-can't-author-geometry doctrine).
  The maker-fee fix is the highest-leverage lever (it's the scorer's input that
  was wrong, not Melchior). `pnl_daily` table is empty (rollup not writing) —
  monitoring gap to fix.

### Live fill-reconcile bug + gate-wake build (later 2026-05-24)

- **ClosedOrders open-time filter — fixed (live-money bug).** Operator noticed
  a buy limit @ 1.3480 (`OFVM5L-FY2K7-VNT7CO`) showing OPEN on the dashboard
  long after price had traded a full cent below it. QueryOrders confirmed it
  FILLED on Kraken at 14:14 (placed 12:49, 1h25m rest) — but our DB still said
  open. Root cause: `reconcile_live_fills_from_kraken` calls
  `get_closed_orders(start=now-3600)`, and Kraken's `ClosedOrders` `start`
  filters by **open time** unless `closetime="close"` is passed. Any arm that
  rests longer than the 1h window before filling has its open time aged out, so
  it's silently dropped from the result — reconcile never sees the fill (no
  log, no error). The prior live fills that DID reconcile were all `@ market`
  anchors (open≈close, inside window); OFVM5L was the first resting limit fill
  and exposed it. **Fix:** `grid/exchanges/kraken.py:get_closed_orders` now sets
  `payload["closetime"]="close"` whenever `start` is given. Verified: patched
  call returns the missed fill. Inventory was NOT affected (live mode syncs
  `get_balances()` truth-of-record per cycle independent of reconcile — the
  14:16 snapshot already matched Kraken). Deployed via restart 17:01 UTC.
- **Orphaned fill backfilled (one-time).** `grid_orders` row for
  `OFVM5L-FY2K7-VNT7CO` marked `filled` (fill_price 1.348, fee $0.0055605,
  filled_at 14:14) via `update_grid_order_status` so dashboard/PnL/round-trip
  accounting are correct. Won't self-heal (closed >1h before the fix shipped).
- **Gate now WAKES MAGI off-schedule (was schedule-only).** Investigation found
  the "gate watches → wakes MAGI" architecture was half-built: `gate_monitor`
  evaluated triggers and wrote `magi_gate_events`, but **nothing ran a MAGI
  cycle off the fixed 4h `MAGI_HOURS_EST` clock** — fired triggers were only
  consumed as `triggers_since_last_cycle` context by the next scheduled cycle.
  So book depletion could wait up to 4h for any adaptive response.
  - **Wake wire** (`scheduler.py`): main loop runs an off-schedule cycle
    (`trigger='gate_wake:<id>'`) when an unconsumed, fired, wake-class trigger
    exists. `WAKE_CLASS_TRIGGERS=("T14","T2","T11")`; throttled to
    `WAKE_MIN_INTERVAL_MIN=60` since ANY cycle (`_last_magi_cycle_at` set in
    `run_magi_cycle`). Edge-triggered functions + `consumed_in_cycle` marking
    prevent re-waking on a standing condition. Cost: ~$0 extra in calm markets,
    one prompt per event. **Correction (2026-05-25):** the ≤~24/day estimate
    assumed T2 was edge-triggered — it is not. T2 is level-triggered (fires
    whenever price stays outside the grid boundary) and produced ~30 cycles/day
    during a standdown period, burning ~$18 over 24h. A precondition guard
    (Session 2026-05-25) suppresses these wakes while the grid is in a
    non-trading state. **2026-05-26:** generalized to T2/T11/T14 and renamed
    `T2_REQUIRES_ACTIVE_GRID` → `WAKE_REQUIRES_ACTIVE_GRID`, plus a
    `WAKE_DWELL_MINUTES` persistence dwell — see "Gate-wake precondition
    guards" under Session 2026-05-25.
- **Two new book-composition gate triggers** (`magi/gate.py`), the gate's first
  eyes on "trending market drains one side of the grid" (the market-movement
  triggers T1–T3/T11–T13 and the 24h drought T4 never saw it):
  - **T14 book one-sided** — edge-fires on the bilateral→one-sided transition
    (one side 0 orders, other ≥1; empty book is NOT one-sided). Wake-class.
  - **T15 skew drift** — edge-fires when `|skew_delta_since_rebuild|` crosses
    0.10 (reuses Melchior Step 4's RECENTRE threshold; structural reuse, not a
    new calibration). NOT wake-class — early-warning that annotates next cycle.
  - Both run via `evaluate_book_state_triggers()`, called on the live
    fill-reconcile path (prompt detection after a fill) AND from
    `evaluate_gate` (hourly). Shared `T14_eval`/`T15_eval` edge state.
- **`RECENT_POSITION_HOLD` refined** (`orchestrator.py`). The hold's rationale
  is "let the round-trip close naturally," so it now only fires when an imminent
  PROFITABLE close actually exists (`round_trip_net_pnl_usd > 0 AND
  round_trip_distance_pct < 0.5`, mirroring Melchior Step 0.5). If skew is open
  but no profitable close is near, it yields so the council's RECENTRE stands —
  closes the case where a gate-wake on a skewing book would have been
  immediately re-held into the static-grid failure. (Already only fired
  bilateral, so it never blocked the binary one-sided case.)
- **Gate observability** for threshold tuning. `database.get_gate_trigger_stats`
  (per-trigger fire counts 24h/window + last-fired details + off-schedule wake
  counts) → `/api/gate_activity` → **GATE ACTIVITY** dashboard panel (reuses the
  READINESS `.gate-chip` grid; ★ marks wake-class triggers). Substrate was
  already there — every gate eval writes a `magi_gate_events` row with full
  `details` — this just surfaces it. Lets the operator see if T14/T15 thresholds
  are too loose (firing constantly) or too tight (never firing).
- **Verification + deploy:** all files compile, schema validator PASS (no FIELDS
  changed, personas untouched), T14/T15 edge logic unit-tested, driver returns
  `[]` on the current bilateral book (1b/3s, skew_delta 0.031), pending-wake
  query empty (no spurious wake on restart). Deployed via restart 18:11 UTC —
  came up LIVE, restored 4 orders, no gate-wake fired on startup. **Cadence
  note:** book triggers detect on the observer/reconcile cadence (10 min), and
  the 1/hr wake throttle is the real responsiveness ceiling — so a depleting
  book gets the council involved best-case ~10 min, worst-case ~1h (throttle),
  vs. up to 4h before. See `02_NEXT_BUILD_TASKS.md` for the deferred real-time
  fill path.

### ADAM error tracking + log-severity audits + icontract invariants (later 2026-05-24)

- **ADAM shipped (`magi/adam.py`).** Sentry error-only tracking (no perf /
  tracing / profiling / replay; `send_default_pii=False`). `SENTRY_DSN` in
  `.env`. Two entry points: `adam.init("<service>")` for the three long-running
  services (scheduler, observer [module-top], dashboard) and
  `adam.init_oneshot("<name>")` (adds an `atexit` flush) for the seven one-off
  `__main__` scripts (database, config_validator, learning, orchestrator,
  provision_agents, readiness, validate_schema). `before_send` drops
  KeyboardInterrupt / SystemExit; adam loads dotenv at import so it resolves
  `SENTRY_DSN` standalone. Operational alerting (ntfy + magi_alerts + dashboard
  panels) is unchanged and independent — ADAM only catches unhandled
  exceptions / crashes.
- **Log-severity audits** (driven by ADAM's `LoggingIntegration`, which turns
  every `logger.error/.exception/.critical` into a Sentry issue — so ERROR-level
  severity now has a Sentry cost):
  - `grid/engine.py`: 8 lines re-leveled — 60/61/62 (`Live gate — gate_X: PASS`,
    the recurring false-positive source, fired at every engine init) and
    1376/1415 (PAUSE no-op) → INFO; 173/443/447 (zero-balance fallback,
    price-sanity order refusals) ERROR → warning.
  - `magi/orchestrator.py`: already well-classified, 0 changes.
  - `magi/council.py`: 1 line — `1156` (R0-failed-after-retry → SAFE_DEFAULTS)
    ERROR → warning, since agent degradation is already alerted via the
    edge-triggered ntfy path and was otherwise spamming Sentry every cycle
    during sustained degradation.
- **`scheduler.py:341`**: `log.error(f"MAGI cycle error: {e}")` →
  `log.exception("MAGI cycle error: %s", e)` so Sentry attaches the full Python
  traceback alongside the exception's message text.
- **icontract installed (2.7.3); first two postcondition invariants on
  `enforce_hard_rules`** (`@icontract.snapshot` captures the input
  regime_action / geometry_veto / grid_action). Invariant 1 — rule-0d coverage
  (regression guard for `cyc_1779480012`): when the council vetoes a structural
  action, rule 0d must record a coverage tag AND grid_action must be MAINTAIN
  unless a higher-precedence rule superseded it. Invariant 2 — override-tag
  integrity: every tag in `hard_rule_overrides` must be canonical. New module
  constants in `orchestrator.py`: `_CANONICAL_OVERRIDE_TAGS` (20 — the 17
  distinct literal tags + the three `[AGENT_DEGRADED:<agent>]` forms) and
  `_RULE_0D_SUPERSEDING_TAGS` (5). A precedence-ladder paragraph was added to the
  `enforce_hard_rules` docstring documenting the implicit rule order (later rules
  override earlier rules' grid_action — by design).
- **icontract violations surface via the Sentry path, not a crash.** A
  `ViolationError` propagates out of `run_cycle`, is caught by the existing
  `try/except` in `scheduler.py`, logged via `log.exception` → ADAM → Sentry
  (now with traceback + icontract's value dump). The cycle fails (no decision
  applied) and the scheduler loop continues — no crash-loop.

## Session 2026-05-23 changes

- **Live order path verified + fee constants corrected.** Blocker 3
  (live-mode market anchor) implemented and exercised: a real 2 XRP
  market buy (txid OIGJW7-4GZ7T-AACAYV) filled at 1.33841, confirming
  AddOrder/QueryOrders/Balance write-side ops work on the live keys.
  The fill revealed the live test order verified taker fee at exactly
  0.40% (fee 0.01070728 / cost 2.67682), not the 0.26% the constants
  claimed; `TAKER_FEE` 0.0026→0.0040 and `MAKER_FEE` 0.0016→0.0025
  corrected in config.py, with break-even doctrine updated here +
  CLAUDE.md, pre-live-flip.

- **FLIPPED TO LIVE.** paper⇄live is now a single env-var toggle:
  `scheduler.py:41` and `dashboard.py:20` read
  `_LIVE = os.environ.get("MAGI_LIVE_CONFIRM") == "YES"` and construct
  `GridEngine(paper=not _LIVE)`. The two-factor gate
  (`MAGI_LIVE_CONFIRM=YES` in `.env` + `CONFIRM_LIVE` file containing
  `I_UNDERSTAND_THIS_IS_REAL_MONEY\n`) is satisfied; log shows
  `LIVE MODE ACTIVE`. Rollback = remove the env var + delete
  `CONFIRM_LIVE` + restart. NOTE the ephemeral price-only engines
  (`guardrails.py:61`, `orchestrator.py:442`) stay `paper=True` by
  design — their "Paper mode active" log lines are not the trader.
  As of cutover the grid is PAUSED/empty (scorer found no acceptable
  variant); first live anchor fires on a future cycle when geometry
  becomes acceptable. Balances unchanged: 29.4768599 XRP / $28.2888.
- **Live order + fill path implemented (Phase 3).** (1) live
  `engine.place_order` now persists arms to `grid_orders` by Kraken
  txid + mirrors into the in-memory book; (2)
  `KrakenExchange.get_closed_orders(start)` wraps `/private/ClosedOrders`;
  (3) `engine.reconcile_live_fills_from_kraken()` — the live counterpart
  to paper `simulate_fills` — matches our open txids against closed
  orders, marks rows filled with Kraken's real price/fee, syncs
  inventory from `get_balances()`; (4) `scheduler.py` observer cycle
  calls it via `if not engine.paper:` (mutually exclusive with the paper
  block). Watch `magi.log` for `[ANCHOR LIVE]` and `[LIVE FILL]`.
- **Dashboard auth → Flask signed-cookie session.** `dashboard.py` now
  has `/login` + `/logout` + a `before_request` gate; password in
  `.env:DASHBOARD_PASSWORD`, `SECRET_KEY` in `.env`, 365-day cookie.
  nginx `auth_basic` block removed (the cloudflared tunnel hits
  Flask:5000 directly, so nginx was never in the public path — the old
  basic auth never actually protected the public URL). Token-auth
  (`X-Magi-Token`) still bypasses the gate for automation. Verified
  401→login→cookie→200 locally and via api.ethobs.uk. Orphaned
  `/etc/nginx/.magi_htpasswd` left in place (harmless).
- **Renewal READINESS panel removed.** `magi/readiness.py` no longer
  computes the R1–R7 renewal gates / RENEW-MARGINAL-DO_NOT_RENEW verdict
  / 2026-06-03 countdown; `evaluate()` returns only `{'live': …,
  'generated_at_utc': …}`. Dashboard renewal panel + `/api/readiness`
  renewal key removed. LIVE READINESS (L1–L9) unchanged.

## Session 2026-05-22 changes

1. **Council restructured: R1 always-fires + two new structural vote fields the
   engine reads.** Previously R1 fired only when `CONFLICT_MATRIX` rules
   detected action incompatibilities (2/146 lifetime cycles, 1.4%). Two
   problems: (a) the dominant disagreement triple `TRENDING/RECENTRE/CLEAR`
   was never routed to debate, and (b) when R1 did fire, the synthesis was
   "challenge / hold-or-revise" — the engine read `final_grid_action`
   and `final_risk_action` regardless of how the council ranked the proposal.
   Council was ornamental at the engine layer.

   Rewrite in `magi/council.py`:
   - `_r1_prompt()` rewritten as **synthesis** with peer R0 outputs pasted
     in cleanly via `_format_peer_r0()`. New `_R1_FRAMING_PER_AGENT` dict
     gives each agent specific synthesis instructions.
   - `send_round_1_synthesis()` replaces `send_round_1_challenge`.
   - `run_round_1()` fires for all three agents every cycle — no conflict
     guard.
   - `resolve_consensus()` reads R1 with R0 fallback, emits two new fields.
   - `CONFLICT_MATRIX` and `detect_conflict` retained as dead code for
     backward import compat; unused.

   Two new vote fields the engine actually reads:
   - **`regime_action`** (Casper): `EXECUTE | DEFER_STRUCTURAL | STAND_DOWN`
   - **`geometry_veto`** (Balthasar): `PROCEED | HOLD_GEOMETRY | RISK_BLOCK`

   `orchestrator.run_cycle` always calls `run_round_1`. `_build_debate_record`
   persists both new fields + `debate_triggered` (based on whether R1
   differs from R0, not on CONFLICT_MATRIX). Database migration adds two
   columns to `debate_records`: `regime_action TEXT`, `geometry_veto TEXT`.

2. **New hard rule 0d — council-veto branch.** `enforce_hard_rules` now
   captures `_original_grid_action` at function entry and runs an additive
   council-veto step after rules 0a/0b/0c (existing RECENTRE_COOLDOWN,
   GRID_HEALTHY_NO_RECENTRE, RECENT_POSITION_HOLD). Four new override tags
   live in this branch:
   - `[REGIME_DEFER]` — Casper said `DEFER_STRUCTURAL` on a RECENTRE/TIGHTEN/WIDEN
   - `[REGIME_STANDDOWN]` — Casper said `STAND_DOWN` on a structural action
   - `[BALTHASAR_HOLD_GEOMETRY]` — Balthasar said `HOLD_GEOMETRY`
   - `[BALTHASAR_RISK_BLOCK]` — Balthasar said `RISK_BLOCK`

   Engine layer: `grid/engine.py:apply_magi_decision` adds a defensive
   cross-check reading `consensus["regime_action"]` and `consensus["geometry_veto"]`;
   logs warning + coerces to MAINTAIN if council vetoed but `grid_action`
   still RECENTRE/TIGHTEN/WIDEN (should not happen since enforce_hard_rules
   runs first — defense in depth).

3. **Persona cleanup.** Each persona rewritten for the new R1 contract:
   - Casper: removed POSITION-AWARENESS conviction modifier (operated on
     `position_state.round_trip_distance_pct`, which is no longer in Casper's
     consumer list — Step 0.5 was reasoning over data outside its domain).
     Added REGIME_ACTION section + ROUND 1 SYNTHESIS section.
   - Melchior: removed Step 3 SPACING FIT body (superseded by the
     ANALYTICAL VARIANT-SCORE addendum). Added ROUND 1 SYNTHESIS — LOAD-BEARING
     section instructing integration of Casper's `regime_action` and Balthasar's
     `geometry_veto`.
   - Balthasar: compressed Step 1 OPEN-ORDER SAFETY GATES from 5 detailed
     clauses to 2 paragraphs. Added GEOMETRY_VETO section + ROUND 1 SYNTHESIS
     section.
   - Schema consumers updated in `magi/world_state_schema.py`: Casper removed
     from `skew_delta_since_rebuild`, `last_fill.hours_ago`,
     `position_state.round_trip_distance_pct`, `position_state.round_trip_net_pnl_usd`;
     Melchior removed from `trajectory.fills_per_hour`. Validator: 0 ERROR /
     0 WARN.

4. **Agent state wipe + recreation.** Casper / Melchior / Balthasar deleted
   and recreated on Letta Cloud — earlier session data (self_models, threads,
   patterns from prior persona iterations) was contaminating the new
   structural-vote behaviour. Self_models intentionally empty post-recreation
   (do not seed). `rotation_cycle_counter` reset to 0 via `system_state`.
   Agent UUIDs in `agent_registry` updated. Pre-deletion snapshot at
   `/tmp/magi_agent_state_pre_wipe_20260522.json`.

5. **COUNCIL LEVERS dashboard panel.** New panel below AGENT HEALTH showing
   distribution of the two new vote fields + count of cycles where the
   council-veto branch actually fired in `hard_rule_overrides`. Three chips:
   CASPER `regime_action` (EXEC / DEFER / STANDDOWN), BALTHASAR
   `geometry_veto` (PROCEED / HOLD / BLOCK), VETO TAGS FIRED (count of
   `[REGIME_DEFER] | [REGIME_STANDDOWN] | [BALTHASAR_HOLD_GEOMETRY] |
   [BALTHASAR_RISK_BLOCK]` cycles). `POST_RECREATION_CUTOFF = '2026-05-22T16:00:00'`
   filters to post-wipe cycles. API: `/api/council_levers`. Soft-refresh
   selector list updated. Color logic: green=all default/permissive,
   yellow=some non-default, red=majority non-default.

6. **Gate layer shipped.** `magi/gate.py` exposes 9 trigger predicates
   (T1–T4, T6–T7, T11–T13) calibrated against 8 years of XRP/USD historical
   price data — T1 velocity, T2 grid envelope breach, T3 rapid level
   traversal, T4 fill drought >24h, T6 scorer rank-1 PnL improvement +50%
   stable for 3+ evaluations, T7 scorer acceptability returning after
   stand-down, T11 vol_regime transitions, T12 ADX threshold crossings at
   25/20, T13 VWAP deviation ±1%. Events persisted to new `magi_gate_events`
   table; consumed via `world_state.triggers_since_last_cycle`. The TRIGGER
   CONTEXT preamble in personas references them. T1 calibration fix: live
   uses `close.pct_change()` (close-to-close 1h) matching the calibration
   table, not `(high-low)/low` (intra-hour range).

7. **Kraken WebSocket v2 substrate shipped.** New module
   `grid/exchanges/kraken_ws.py` subscribes to public `ticker` + `ohlc`
   channels. Always-on monitoring service replacing the 10-min REST poll
   for sub-second event detection. Health surfaced in new `ws_health` table
   (timestamp, state, last_heartbeat_age_sec, reconnect_count_1h,
   last_tick_age_sec). 458 health rows currently.

8. **Freshness validator + A+B retry path.** Two-part fix for the Balthasar
   confabulation pattern (SAFE_DEFAULTS event on `cyc_1779450912`):
   - **Change A** — inline-correct retry: when validator catches an evidence
     field that doesn't match world_state to 2dp, retry once with explicit
     injection ("your evidence cited X but world_state says Y, re-vote with
     current numbers") before falling back to SAFE_DEFAULTS.
   - **Change B** — `severity='warn', category='freshness_retry_failed'`
     `magi_alerts` row when the retry exhausts and SAFE_DEFAULTS fires. Helps
     distinguish "stable agent" from "silently degraded."
   - Both stored in `debate_records.freshness_retries` (new JSON column).

9. **Disk cleanup.** Operator-approved SAFE bucket sweep recovered ~8 GB.
   Anchor verified at `/`: 35% used (was ~50%+ before).

10. **Tip-of-state observations from the first 7 post-recreation cycles
    (2026-05-22T16:00 → 20:00):**
    - All 5 cycles after the vote-field migration produced non-default
      structural votes: Casper `STAND_DOWN` ×5; Balthasar `HOLD_GEOMETRY` ×3,
      `RISK_BLOCK` ×1, `PROCEED` ×1.
    - Council-veto rule (0d) fired **zero** times on cycles where Melchior
      voted MAINTAIN (nothing to veto).
    - **Latest cycle (`cyc_1779480012`, 2026-05-22T20:00:12)** is the first
      cycle where Melchior voted RECENTRE while peers voted veto
      (Casper STAND_DOWN + Balthasar RISK_BLOCK). `hard_rule_overrides=[]` and
      `applied_grid_action=NULL` in the row — needs investigation; expected
      to show `[REGIME_STANDDOWN]` and/or `[BALTHASAR_RISK_BLOCK]`.
      Tag-emission bug in rule 0d, or row-write race with the engine
      downgrade — TBD. Filed as Outstanding issue.

## Phase 5 — COMPLETE

Migrated from stateless three-agent council to stateful Letta Cloud agents.
All six provisioning prompts executed; verification passes.

### Built and verified — Phase 5 baseline (2026-05-16)
- SQLite: `debate_records` + `agent_registry`
- `magi/provision_agents.py` — idempotent Letta agent creation, 7 memory blocks per agent
- `magi/council.py` — parallel Round 0, CONFLICT_MATRIX, Round 1 debate, validate_revision, resolve_consensus
- `magi/orchestrator.py` — builds world_state, enforces HARD_RULES, dual-writes
- `observer.py` — `backfill_outcomes()` for 1h/6h/24h windows, sends 6h outcomes to agent threads
- `dashboard.py` — council panels; legacy Latest MAGI Decision / Supervisor / Recent Decisions panels removed

### Session 2026-05-17 changes (11-item summary)

1. **Persona overhaul** — all three agents got new persona prompts: identical
   1752-byte SYSTEM CONTEXT preamble, numbered decision-tree with "first
   matching gate wins" precedence, 2 worked examples each with `WRONG action`
   callouts, 1 derived-quantity formula each. Melchior's TIGHTEN path was
   structurally unreachable and is now reachable. Live char counts: Casper
   7883, Melchior 7951, Balthasar 7940.
2. **New hard rules** in `enforce_hard_rules`: `[GRID_DEGENERATE]`,
   `[RECENTRE_COOLDOWN]`, `[PAUSE_INVALID]`. `hours_since_last_fill` and
   `hours_since_last_rebuild` exposed in `world_state`.
3. **CONFLICT_MATRIX expansion** — predicate signature changed from
   `(round_0)` to `(round_0, world_state)`; 4 grid-state-aware rules added
   (one-sided MAINTAIN, stale MAINTAIN, PAUSE_LONGS on empty buys,
   PAUSE_SHORTS on empty sells). First Round 1 debate ever fired at
   `cyc_1779032110`. **Known gap**: no rule covers the current divergence
   pattern (Casper TRENDING/RANGING + Melchior RECENTRE + Balthasar CLEAR);
   Round 1 is not firing on the real disagreement. → `02_NEXT_BUILD_TASKS.md`.
4. **Engine guards** in `grid/engine.py` — post-action integrity guard
   rebuilds if a risk action leaves the book one-sided. PAUSE_LONGS /
   PAUSE_SHORTS emit WARN "no-op" when `cancelled==0 AND pre-count==0`.
5. **Scheduler** — `MAGI_HOURS_EST = [0, 4, 8, 12, 16, 20]` (every 4 hours,
   6 cycles/day; reduced from hourly on 2026-05-18 to bring monthly cost
   inside the $20 Letta plan — ~$13/mo @ 6 cycles/day). Startup debounce
   reads `debate_records` (was reading sparse `magi_decisions` and firing
   duplicates on restart). `/internal/trigger_magi` reads `debate_records`.
6. **`debate_records` schema** — added `hard_rule_overrides` (JSON-encoded
   list of bracketed tags), `balthasar_concerns`, `casper_concerns` (schema
   symmetry with `melchior_concerns`). Dashboard's latest-override-tag panel
   and 30-day override-count panel migrated to read `hard_rule_overrides`.
   `magi_decisions` dual-write retained; documented at `database.py:614`.
   **17 of 38 historical rows** still have NULL `hard_rule_overrides`;
   30-day panel under-reports until they age out.
7. **`provision_agents.py`** UPDATE path now syncs persona blocks **and**
   LLM config knobs from `AGENT_CONFIG`, idempotently. Re-running the script
   is the canonical way to push persona + config edits to live agents.
8. **LLM config equalisation** — `temperature` 1.0 → 0.3, `max_output_tokens`
   → 8192 across all three. Thinking enabled at comparable levels: Anthropic
   `effort=medium` + `budget_tokens=2048`, OpenAI `reasoning_effort=medium`,
   Google AI `thinking_config.thinking_budget=2048`. Provider asymmetry
   documented: GPT-4o has no native extended-thinking budget;
   `reasoning_effort` is the closest equivalent. `parallel_tool_calls` is
   platform-forced to `True` and cannot be set `False`.
9. **self_model curation** — Casper's and Melchior's `self_model` blocks
   rewritten to retire reflections written under the prior persona. Casper
   flipped from RANGING to TRENDING bearish on the first post-curation cycle
   and is holding. Melchior **did not** shift — GPT-4o is pattern-matching
   prior conversation-turn responses (evidence list is byte-identical
   across cycles 40–46 including the stale `autocorr 1h: 0.0218` value).
   Balthasar untouched (was aligned). Pre-intervention snapshot at
   `/tmp/self_model_snapshot_2026-05-17.json`. Six orphan persona blocks
   identified at project scope; not deleted this session.
10. **Architecture-intent reframe** (operator-led) — the three providers are
    chosen to offset each other's biases by design. Diversity is the
    architecture's strength. The right lever for stuck-agent behaviour is
    `CONFLICT_MATRIX` expansion routing genuine divergence to Round 1, not
    per-agent compliance fixes. Captured in `CLAUDE.md` §3.
11. **`CLAUDE.md` authored** at repo root — operating discipline, architecture
    intent, recurring failure patterns. Auto-loaded by Claude Code at
    session start.

### Session 2026-05-18 changes

1. **Scheduler frequency bug — fixed.** `scheduler.py:501-502` had a
   `if now_est.hour == 0: last_magi_hour = -1` block that ran every
   60-second iteration during EST hour 0, causing the cycle to re-fire
   minute-by-minute through the whole hour (observed: 47 cycles in 60
   minutes at 2026-05-18 04:00 UTC = 00:00 EDT). Removed; cross-midnight
   dedupe works without the reset because `current_hour != last_magi_hour`
   already handles the 20 → 0 rollover. **`MAGI_HOURS_EST` already
   `[0,4,8,12,16,20]`** — the 6-cycle/day target was correct; the burst
   was a separate bug.
2. **`database.py:get_latest_candle_hl` — fixed.** Now filters
   `WHERE timeframe=? AND timestamp < <current_hour_start_utc>` and orders
   by timestamp DESC. Previously selected by `id DESC`, which returned
   the *in-progress* current-hour candle (partial high/low covering only
   the first few minutes of the hour). Mid-hour fills no longer silently
   missed.
3. **Balthasar Sonnet → Haiku 4.5.** `client.agents.update(agent_id, model=...)`
   on Letta. Per-agent knobs (temperature 0.3, effort medium, thinking
   budget 2048) restored via `model_settings` after the bare `model=`
   kwarg reset them to defaults. Mirror change in
   `magi/provision_agents.py:52` so re-runs stay idempotent.
   ~4-5× cost reduction per call. Snapshot of pre-switch llm_config at
   `/tmp/balthasar_llm_config_2026-05-18_pre-haiku-switch.json`.
4. **MAGI display names capitalized on Letta** — casper/melchior/balthasar →
   Casper/Melchior/Balthasar via `client.agents.update(agent_id, name=...)`.
   In-code keys remain lowercase.
5. **`magi/spacing_evaluator.py` — new.** Stdlib-only, deterministic
   closed-form variant scorer. Replaces the fill-based shadow-sim
   spacing search (which couldn't differentiate variants in low-vol
   regimes because nothing was filling). 36 variants:
   `levels ∈ {5,6,7,8,9,10} × spacings ∈ {0.005, 0.0075, 0.01, 0.015,
   0.02, 0.025}`. Per-LEVEL math:
   - pair i ∈ [1, N/2] sits at ±i*s from centre
   - qualifying hour = `(high-low)/low ≥ 2*i*s`
   - `pair_rt_per_day = qualifying_hours / 720 * 24`
   - `pair_pnl_grid_pct = (2/N) * pair_rt * (s - 2*fee)` — normalised by
     total grid capital so adding outer levels only helps when vol
     reaches them
   - `expected_daily_pnl_pct = sum(pair_pnl_grid_pct)` over all pairs
   - `acceptable` = `(s > 2*fee)` AND every pair has `rt > 0` in 720h
     window — operator-enforced "positive PnL per level" hard requirement
   - Sort: acceptable variants first, then unacceptable (still ranked
     for traceability but Melchior must skip them per persona)
   Fed into Melchior's context via `world_state.scored_variants_top_10`
   + `current_spacing_pct` + `current_levels` +
   `current_config_expected_daily_pnl_pct`. Uses `config.TAKER_FEE = 0.0040`.
   Under current XRP vol only `(5, 0.0075)` is acceptable — Melchior will
   pick that if RECENTRE clears the rule, otherwise MAINTAIN.
6. **`_pick_shadow_winner_spacing()` removed** from `magi/orchestrator.py`.
   `_final_consensus` now reads `geometry.target_spacing_pct` and the new
   `geometry.target_levels` from Melchior's R0 output. Graceful None
   fallback when missing — engine retains live spacing/levels.
7. **`target_levels` wired through engine.** `grid/engine.py:apply_magi_decision`
   reads `geom.target_levels`, clamps to `[4, 12]` (one step of slack on
   each side of the scorer's `{5..10}` range), assigns to `self.level_count`
   before `initialise_grid`. Spacing path unchanged (already there from
   prior session). Centre still anchors to live spot price via
   `get_current_price()` — historical candles never influence centre,
   only spacing/levels.
8. **Shadow sim reduced from 24 → 6 variants.** `config.SPACING_VARIANTS`
   collapsed from `[0.010, 0.015, 0.020, 0.025]` to `[0.025]` (placeholder
   — overridden on first rebuild). `grid/shadow_simulator.rebuild()` and
   `update_centre()` now USE the `spacing_pct` arg (previously documented
   as ignored). All 6 variants share the live grid's spacing; vary only
   in level count. Shadow sim is now a level-count sanity check, not a
   spacing search.
9. **18 stale `shadow_grid_state` rows deleted** (sp ∈ {0.010, 0.015,
   0.020}). DB vacuumed. 6 active rows remain (one per level count,
   sp=0.025).
10. **Melchior persona appended** with `=== ANALYTICAL VARIANT-SCORE
    (replaces Step 3 when present) ===` section + per-level-positivity
    rider. ~770 chars added total. Tells Melchior to skip Step 3 SPACING
    FIT when `scored_variants_top_10` is non-empty, pick rank-1 if its
    PnL beats current AND it's `acceptable`, emit a fifth top-level
    `geometry` field. Block size: 7951 → 8974 chars; limit raised
    8000 → 9000. Pre-edit snapshot at
    `/tmp/melchior_persona_2026-05-18_pre-variant-eval-append.json`.
11. **Temp debug log in `simulate_fills`** (`[SIMFILLS DEBUG] price=… …
    open_orders=…`) — marked TEMP in a code comment; remove after a few
    observer cycles confirm the candle fix is working.
12. **Letta 402 burst — diagnosed as transient, not active.** 8 distinct
    `402 Payment Required` responses across all three agents in a single
    3-second window at 2026-05-18 09:00:11–14 UTC, all returning
    `remainingPurchasedCredits: -241`. Bracketing calls (08:58 and
    09:58) returned 200 OK; 159 successful calls in the 4 hours after.
    Account is operational. Worth a Letta support ticket only if the
    burst recurs.

### Session 2026-05-19 changes

1. **No-fills diagnostic.** Bot had 0 paper fills in 91h despite
   intra-bar candle ranges that should have touched levels. Root cause:
   Melchior emits null `geometry` every cycle (GPT-4o anchoring on prior
   conversation), engine's RECENTRE branch fell back to "spacing
   unchanged" — inheriting `GRID_SPACING_PCT = 0.025` from first boot.
   Compounded by `get_latest_candle_hl` pre-fix returning the in-progress
   candle, which masked the one historic dip (5/17 23:00 low 1.37208)
   that would have triggered an arm.
2. **Orchestrator rule #8 — `[GEOMETRY_INJECTED_FROM_SCORER]`.** When
   `grid_action ∈ {RECENTRE, TIGHTEN, WIDEN}` and Melchior's
   `geometry.target_spacing_pct` / `target_levels` are null or invalid,
   the rule injects the scorer's rank-1 acceptable variant into
   `round_0['melchior']['geometry']` before `_final_consensus` reads it.
3. **Orchestrator rule 0a — `[GRID_HEALTHY_NO_RECENTRE]`.** Time-
   independent gate: downgrade RECENTRE → MAINTAIN when book is
   bilateral AND `|price − centre| / centre < spacing_pct`. Complements
   existing `[RECENTRE_COOLDOWN]` (time-based, same effect when book is
   healthy AND rebuild was < 60 min ago).
4. **Orchestrator `[NO_ACCEPTABLE_VARIANT]` stand-down.** When scorer
   has no acceptable rank-1 on a rebuild action, force
   `grid_action = GRID_PAUSE` (engine cancels all orders and idles).
   No `pause_longs`/`pause_shorts` flags — the rule re-fires next cycle
   and chattering flags would deadlock. Cited as `[NO_ACCEPTABLE_VARIANT]`
   in `hard_rule_overrides`.
5. **All static spacing defaults removed.** `GRID_SPACING_PCT` deleted
   from `config.py`. `engine.initialise_grid()` now requires
   `spacing_pct > 0` and errors out if None. `apply_magi_decision`
   refuses to rebuild on null geometry (was inheriting prior spacing).
   `evaluate_and_maybe_switch_levels` skips rebuild when grid_state
   missing. `MAX_GRID_SPACING_PCT` / `MIN_GRID_SPACING_PCT` re-commented
   as safety clamps only — never used as the actual spacing value.
6. **New `scheduler._first_boot_geometry()`.** Pulls scorer rank-1 for
   first-boot grid. If no acceptable variant exists, scheduler stands
   down rather than initialising a grid with a fabricated spacing.
7. **New `debate_records.geometry_source` column.** Values:
   `'agent'` | `'scorer_fallback'` | `'unchanged'`. Lets the dashboard
   show how often Melchior contributes geometry vs how often the
   fallback carries the load.
8. **Anchor-then-arms mechanic in engine** (operator-led design;
   mechanical ordering, separate from MAGI judgment).
   - `engine._execute_anchor()`: places a single market order at
     current spot before any arms. Direction follows skew (sell if
     XRP-heavy, buy if USD-heavy). Sized as one rung of the eventual
     ladder. Paper-mode fill is synchronous at spot with TAKER_FEE.
     **Live-mode market anchor is implemented (2026-05-23):** places a
     real Kraken market order via `add_market_order`, polls `query_order`
     for fill, reconciles inventory from `get_balances()`.
   - `initialise_grid()` is now two-stage. Stage 1: execute anchor.
     Stage 2: build arm limit orders around the anchor's actual fill
     price (not the caller-supplied `centre` parameter — anchor sets
     the centre by where it executes). If anchor fails, NO arm orders
     are placed; grid stays theoretical.
   - Anchor inventory is persisted immediately so a process restart
     between anchor fill and the next observer cycle doesn't load
     stale pre-anchor balances.
9. **`OBSERVER_INTERVAL_MINUTES = 60 → 10`.** Same 1h-completed-candle
   H/L is now evaluated within ~10 min of close instead of up to ~60 min.
   No other code path assumed 60-min cadence.
10. **Shadow simulator persist fix + stale-row cleanup.**
    `ShadowSimulator.persist_all` was writing using the dict's original
    `(lc, sp)` key, where `sp` was the now-placeholder
    `SPACING_VARIANTS=[0.025]`. Switched to `sg.spacing_pct` (the
    live-tracked value updated by `update_centre`). 6 stale
    `shadow_grid_state` rows at sp=0.025 deleted; the table now reflects
    the live 0.0075 spacing across all 6 level-count variants.
11. **`agent_registry.balthasar.model` corrected** sonnet-4-6 →
    haiku-4-5. The 5/18 Sonnet→Haiku switch went through on Letta but
    the registry row wasn't updated. The orchestrator reads
    `letta_agent_id` (not `model`) from this table, so behavior was
    unaffected — display-only fix.
12. **First fill since 5/15.** Anchor BUY 17.1683 XRP @ 1.37765 at
    2026-05-19T13:04:17 UTC (TAKER fee $0.0615; operator-forced manual
    rebuild to validate the new mechanic end-to-end). Inventory after:
    xrp=30.97 / usd=$23.59 / skew +0.14. 4 arm limit orders deployed
    at ±0.75% and ±1.5% from the anchor fill.

### Session 2026-05-19 evening additions

13. **Three production fills, full round-trip cycle observed.**
    - 13:04:17 anchor BUY 17.17 XRP @ 1.37765 (operator-forced)
    - 15:04:50 arm BUY 8.56 XRP @ 1.36732 (natural inner-arm fill on
      a 0.75% intraday dip)
    - 16:00:56 anchor SELL 19.77 XRP @ 1.36612 (scheduled MAGI cycle
      forced RECENTRE; engine's anchor mechanic read skew=+0.32 →
      chose SELL direction → executed at market, rebalanced inventory
      back to skew=-0.09)
    Validates the anchor-then-arms mechanic end-to-end in production
    (engine, orchestrator, scorer-fallback rule, all without manual
    intervention after the initial 13:04 anchor). Net P&L slightly
    negative — the 16:00 SELL closed the position at the current spot
    rather than at a profitable round-trip, which motivated rule 0c
    below.
14. **`[RECENT_POSITION_HOLD]` hard rule (rule 0c)** in
    `enforce_hard_rules`. Fires when `grid_action ∈ {RECENTRE, TIGHTEN,
    WIDEN}` AND `hours_since_last_fill < 2.0` AND `|inventory_skew| >
    0.15` AND book bilateral. Downgrades to MAINTAIN + neutralizes any
    PAUSE_LONGS/PAUSE_SHORTS that would partially close the position.
    Catches the exact failure mode from the 16:00 cycle: rebuild while
    a round-trip is open and arms could close it naturally for free.
    Replay test of the 16:00 scenario confirms the rule fires.
15. **Position-state context surfaced into world_state.** New helpers
    in `magi/orchestrator.py`: `_last_fill_summary()`,
    `_position_state_summary()`, `_skew_delta_since_rebuild()`. New
    world_state fields:
    - `last_fill`: `{side, price, size_xrp, size_usd, hours_ago, fee_usd, order_id}`
    - `position_state`: `{nearest_close_arm_price, round_trip_distance_pct, round_trip_gross_pnl_usd, round_trip_net_pnl_usd}`
    - `skew_delta_since_rebuild`: float
    Gives Casper/Melchior/Balthasar concrete grounds to reason about
    open positions rather than just numeric `hours_since_last_fill` and
    `inventory_skew`. The hard rule above is the Python backstop.
16. **ONE GRID invariant tripwire** in `grid/engine.py`. New helper
    `_assert_one_grid_invariant(context, expected_open)` called at two
    lifecycle transitions:
    - `_execute_anchor` entry: expected_open=0 (book must be clean
      after cancel_all_orders)
    - End of `initialise_grid`: expected_open=placed
    Logs `ERROR` on mismatch — surfaces silent multi-generation order
    accumulation before it causes inventory damage. Non-blocking;
    informational tripwire only.
17. **Dashboard UX overhaul (cosmetic).** Top-of-page MAGI triangle
    hero with Casper/Melchior/Balthasar arranged around a central
    MAGI core, conviction-based agent box coloring (high/med/low
    intensity + glow), side CODE·STATUS panel with cycle metadata.
    NGE-accurate fonts (Michroma for h1 — Eurostile substitute used
    for NERV signage in the show; Helvetica bold for h2 — NGE HUD
    interface text; VT323 for big numerical readouts — CRT terminal
    feel). Side-by-side Grid+Council and Inventory+P&L pairs.
    Analytics panels collapsed by default with localStorage
    persistence across refreshes. Meta-refresh replaced with JS soft
    refresh that fetches `/` in the background and swaps only the
    data sections; chart iframe stays alive (no more reconnect
    flicker every 30s). `agent_registry.balthasar.model` row
    corrected to `anthropic/claude-haiku-4-5` (stale display-only
    bug from the 5/18 model switch). Source typeface reference:
    `fontsinuse.com/uses/28760/neon-genesis-evangelion`.

### Session 2026-05-21 — schema-driven world_state contract shipped

1. **`magi/world_state_schema.py` — single source of truth** for fields in
   world_state and which agents consume them. 97 declared paths covering
   every field `build_world_state()` emits, including SQLite row metadata
   (`*.id`, `*.timestamp`, `*.timeframe`). Each field declares type,
   description, consumers list, and a per-consumer usage hint. The
   schema is a Python dict literal (pivoted from YAML when PyYAML proved
   to be a missing dependency — Python schema also removes a parse step).
2. **Two-layer validation.**
   - **Runtime:** `alert_on_runtime_drift(ws)` runs at the end of every
     `build_world_state()` call. On any schema-vs-output mismatch, writes a
     `magi_alerts` row with `severity='critical', category='schema_drift_runtime'`
     and the standard ntfy push fires. Trading continues — drift is a
     maintenance failure, not a trading-stop event.
   - **Persona:** `validate_persona_references(persona_text, agent_id)`
     scans the agent-specific portion (post-`ROLE —`) for dotted-path and
     bare-name references; ERRs on broken references, WARNs on cross-domain
     prose mentions.
3. **`magi/validate_schema.py` — standalone CLI.** `python -m magi.validate_schema`
   exits 0/1/2. `magi/provision_agents.py` imports `validate_schema_main`
   and aborts provisioning with non-zero exit if any ERROR surfaces, BEFORE
   any Letta call.
4. **`magi/portfolio.py` — single-sourced portfolio computation.**
   `compute_portfolio_metrics(xrp_held, usd_held, price)` is the canonical
   site for `xrp_value_usd`, `total_universe_usd`, `xrp_pct_of_universe`,
   `allocation_skew`. Replaces 3 duplicate compute sites
   (`orchestrator.py:594`, `engine.py:166`, `engine.py:1249`). `enforce_hard_rules`
   now reads `world_state.portfolio.xrp_value_usd` instead of recomputing.
5. **`world_state.portfolio` block added** to `build_world_state()` output —
   resolves Balthasar's persona references to a previously non-existent
   namespace. Persona references like `portfolio.allocation_skew` now
   actually resolve to data in world_state.
6. **`trajectory.fills_per_hour` added** to `get_trajectory_context()`
   output — was referenced by Melchior's Step 3 MID-band gate but never
   computed.
7. **All three personas migrated** to the auto-generated SIGNALS block
   pattern. Hand-authored SIGNALS lists removed; replaced with
   `<!-- BEGIN_AUTOGENERATED_SIGNALS -->` / `<!-- END_AUTOGENERATED_SIGNALS -->`
   markers. Provisioning renders the SIGNALS block from the schema and
   pastes between the markers (overwriting any prior contents — a "DO NOT
   EDIT" comment opens the block). Persona block limits bumped 8000/9000
   → 20000 to accommodate.
8. **Five broken references fixed in personas.**
   - Casper's `current_price` (3 mentions) → `price` (matches actual world_state path)
   - Balthasar's `current_price` (1 mention) → `price`
   - Balthasar's `portfolio.*` namespace (4 fields) → resolved by adding
     the `portfolio` block to `build_world_state`
   - Melchior's `trajectory.fills_per_hour` → resolved by adding the field
     to `get_trajectory_context` output
   - Melchior's `trajectory.hours_active` → removed (unused; was only in
     the old SIGNALS list)
9. **Position-awareness sections added** to all three personas. Casper:
   Step 0.5-like conviction modifier on `position_state.round_trip_distance_pct`
   when a round-trip is in flight. Melchior: new Step 0.5 gate that
   forces MAINTAIN when `position_state.round_trip_net_pnl_usd > 0` AND
   `round_trip_distance_pct < 0.5`. Balthasar: Step 0.5 gate that holds
   CLEAR against preference-level pause signals when an open round-trip
   is close to closing profitably (survival signals still override).
   These mirror the `[RECENT_POSITION_HOLD]` hard rule at the persona
   level — agents now reason about open positions rather than relying
   solely on the rule layer to catch the case.
10. **Persona snapshots taken** before push:
    `/tmp/persona_pre_schema_migration_{casper,melchior,balthasar}_20260521.json`.
11. **Live provisioning succeeded.** All three agents updated; second
    run produced 0 changes (idempotent). One MAGI cycle (`cyc_1779381400`,
    16:36 UTC) fired post-deployment with no errors and wrote a clean
    `debate_records` row.
12. **Maintenance contract going forward:** adding or removing a field
    in `build_world_state()` requires updating `magi/world_state_schema.py`.
    Runtime validator fires a critical alert (ntfy) on any drift; persona
    provisioning fails loud on any broken reference. Operators must edit
    the schema to change what an agent sees — hand-editing the
    autogenerated SIGNALS block in persona files is reverted on the next
    provision.

13. **Balthasar config drift investigation + two-fix shipment** (same
    session). Root cause identified for the 2026-05-20→05-21 ~17-hour
    config drift (Balthasar temperature 0.3 → 1.0, thinking budget 2048
    → 1024, effort medium → None): the BYOK failover runbook's revert
    step shipped `c.agents.update(agent_id=..., model=BASE_HANDLE)`
    without `model_settings=AGENT_CONFIG[...]`, and the Letta server
    treats a bare `model=` update as a request to reset model_settings
    to provider defaults. Impact on votes was masked by Balthasar's
    thread-anchoring failure mode (his 10 drift-period votes were all
    byte-identical CLEAR/0.77, so the temperature variance had no
    observable effect on outputs); pre-existing operational conditions
    stayed inside the CLEAR band so non-default voting wouldn't have
    fired regardless. No self_model rollback needed — the 2026-05-20
    memory rotation fired BEFORE drift started (16:00 UTC vs drift
    onset 20:09 UTC), and the one pattern added during drift (Pattern 7,
    skew_delta amplitude vs fill count) is substantive content
    independent of sampling variance. Full investigation findings
    chat-logged this session.

    **Fix 1 — RUNBOOK_BYOK_FAILOVER.md patched.** All four
    `agents.update(model=...)` call sites (lines 164, 231, 316, 335)
    now include `model_settings=AGENT_CONFIG['${AGENT_KEY}']` imported
    from `magi.provision_agents`. Pre-flight section opens with a
    CRITICAL CONVENTION callout: any `agents.update()` that passes
    `model=` must also pass `model_settings=`. Applies to swap AND
    revert paths.

    **Fix 2 — `magi/config_validator.py` shipped.** Standalone validator
    analogous to `magi/validate_schema.py`. Compares each agent's live
    Letta `model_settings` against `AGENT_CONFIG`; on any mismatch
    writes a `magi_alerts` row with `severity='critical',
    category='config_drift', agent_id=<key>` and the standard
    `magi/notify.py:send_ntfy()` push fires automatically. Reuses
    `_model_settings_diff` from provision_agents — no duplicated diff
    logic. Wired into `scheduler.run_magi_cycle` at start (catches
    drift introduced between provisioning runs) AND end (catches mid-
    cycle drift). Failure mode: non-fatal, emit alert, continue. CLI:
    `python -m magi.config_validator` exits 0 on PASS, 1 on drift.

    **Verification complete:** clean tree → exit 0; induced mismatch
    (Balthasar temp 0.5) → alert row id=17 written + ntfy invocation
    confirmed; re-induced mismatch → 60-min dedup window suppressed
    second alert; out-of-band drift on Casper between cycles → caught
    by scheduler pre-cycle hook on next manual trigger (alert id=18).

    **Discipline going forward:** no third drift validator without
    evidence of a third drift surface causing concrete problems. Two
    contracts (data + config) is the ceiling. The validator pattern is
    established; apply to a new surface only when failure evidence
    warrants it.

### Session 2026-05-21 — orphan-block sweep on Letta API key

1. **Letta-side cleanup.** SDK enumeration via
   `c.blocks.list()` returned 368 blocks on the production API key, of
   which only 11 were attached to a current agent (3 personas + 3
   self_models per canonical agent, plus the shared `world_state`,
   `cycle_phase`, and three `*_r0_output` blocks). The remaining 357
   were orphans: 121 personas, 112 self_models, 112 world_states, 6
   decisions, 6 humans. All 357 deleted in a single rate-limited pass
   (100ms throttle, 0 retries needed, 165s wall-clock). Post-cleanup
   total: 11 blocks, all canonical attached.

2. **Doc correction.** The "Six orphan persona blocks" line previously
   in §Outstanding issues → Engineering was a ~60× under-count — likely
   eyeballed from the Letta web UI's per-page Memory blocks view, which
   doesn't expose blocks from other projects on the same API key. The
   bulk of the orphans were `SYSTEM CONTEXT — MAGI COUNCIL` persona
   snapshots and `## Pattern N` self_model reflections from
   `magi-evals` throwaway agents, plus three `'probe'` blocks from the
   BYOK provider verification (`/tmp/byok_models.json` workflow), plus
   a few older "You are Melchior/Casper/Balthasar" per-agent personas
   from pre-2026-05-18 provisioning runs.

3. **Restoration safety net.**
   `/tmp/orphan_blocks_pre_delete_20260521.jsonl` (1.6 MB) holds the
   full pre-delete export — one `BlockResponse` dict per line including
   `id`, `label`, `value`, `description`, `metadata`, `limit`, `tags`.
   Restore via `c.blocks.create(...)` if anything turns out to have
   been load-bearing. Plan + classification details:
   `/tmp/orphan_block_cleanup_plan.md`.

4. **SDK gotcha for future cleanups.** `c.blocks.retrieve(block_id)`
   in letta_client 1.11.0 returns `project_id=None` for every block,
   even blocks known to live in `project-rOHyxZ66AqVwmoojjz0z`. The
   `project_id` filter on `c.blocks.list(project_id=...)` also returns
   zero results. Classification of orphans across projects on the same
   API key currently has to fall back to label + content patterns
   rather than authoritative project membership. Watch for SDK updates
   that fix this.

5. **Verification.** Post-cleanup `c.blocks.list()` returned exactly
   11 blocks, matching the pre-computed attached set exactly. Sampled
   10 deleted block IDs via `c.blocks.retrieve()` — all returned 404
   (gone).

6. **Agent-level cleanup.** Also deleted the two `test-haiku-tmp` BYOK
   verification agents from the 2026-05-20 failover test session
   (`agent-3dfd36ba-...` and `agent-0a27ad32-...`). Both were named
   `test-haiku-tmp`, had no MAGI persona/world_state/self_model
   attached, only stock Letta base instructions, and zero code
   references anywhere in the repo. Pre-delete snapshots at
   `/tmp/letta_agent_<id>_pre_delete_20260521.json` (agent config +
   attached blocks + last 20 messages). `c.agents.list()` post-cleanup
   returns exactly 3: Casper / Melchior / Balthasar — matching
   `agent_registry`. Dashboard "total agents" count should now read 3.

### Session 2026-05-20 changes

1. **Memory rotation lifecycle shipped end-to-end.** New module
   `magi/memory_lifecycle.py` distils Letta thread history into the
   agent's `self_model` block on a 30-cycle cadence, then resets the
   thread. Driven by `rotation_cycle_counter` persisted in the new
   `system_state` (key/value) table; consumed by `maybe_rotate(n)` which
   fires when `n % ROTATION_CADENCE == 0`. Per-rotation safety
   invariants: pre-write snapshot to `/tmp`, strict validation
   (≥1 `## Pattern N` heading, ≥1 `cyc_\d+` reference), server-side
   merge via `client.blocks.update` (never agent tool calls), thread
   reset only after a successful merge.
2. **Phase 1 validation against live Balthasar.** Confirmed
   `client.agents.messages.compact()` returns `CompactionResponse{num_
   messages_before, num_messages_after, summary}`. Test compaction:
   365 → 257 messages, 1338-char summary, `self_model` block was NOT
   modified by the call (compaction writes to the thread, not to
   blocks). Snapshot at `/tmp/balthasar_pre_compact_2026-05-20.json`.
3. **Four new config knobs in `config.py`.**
   - `ROTATION_CADENCE = 30` (cycles)
   - `ROTATION_WINDOW_PCT = 0.35` (keep ~35% of recent messages)
   - `SELF_MODEL_CHAR_CAP = 5000` (hard cap, eviction is the relief
     valve)
   - `MAX_NEW_PATTERNS = 2` per rotation
4. **Two new database tables.**
   - `memory_rotations` — one row per agent per rotation attempt
     regardless of outcome. Status vocabulary: `success` /
     `validation_failed` / `merge_failed` / `snapshot_failed` /
     `compact_failed` / `skipped` / `error`. Indexed on
     `(agent_id, timestamp)`.
   - `system_state` — generic `(key, value, updated_at)` for
     cross-restart counters. First user: `rotation_cycle_counter`.
     Helpers: `db.get_system_state(key, default)`,
     `db.set_system_state(key, value)`.
5. **Scheduler integration.** `run_magi_cycle` increments
   `rotation_cycle_counter` after every attempted MAGI cycle (success
   or fail); `maybe_rotate(counter)` fires only on success. Guardrail-
   blocked cycles do NOT increment — no council ran, no thread
   accumulated. Startup logs the current counter and "next rotation in
   N successful cycle(s)" for visibility.
6. **First live rotation succeeded on all three agents.** At
   2026-05-20T16:00 UTC, counter pre-set to 29 → startup MAGI cycle
   succeeded → counter 29→30 → rotation fired. Per-agent (status=success
   on all):
   - casper:    2481 → 3163 chars, +2 patterns (now has 5 total)
   - melchior:  1859 → 2510 chars, +2 patterns (now has 3 total)
   - balthasar: 1709 → 2490 chars, +2 patterns (now has 5 total)
   Rotation duration: ~58s for all three agents in sequence.
   Snapshots at `/tmp/self_model_pre_rotation_<agent>_20260520.json`.
7. **Kraken-keys claim was stale.** This session's restart ran
   `engine.exchange.get_balances()` successfully —
   `Kraken bot universe: 27.4769 XRP ($37.72) + $30.98 USD = $68.70`.
   The "current keys return `EAPI:Invalid key`" note in prior docs is
   no longer accurate for the `Balance` endpoint. Whether `AddOrder`
   succeeds with the live keys is still untested; that is the gate to
   verify before going live, not the keys-are-broken claim.
8. **BYOK failover runbook + Balthasar verification.** Live-swapped
   Balthasar onto `BATHY/claude-haiku-4-5-20251001` via
   `c.agents.update(agent_id, model=...)`, ran one MAGI cycle (R0
   `CLEAR` conv 0.77, real crux on allocation-skew gating, zero new
   `magi_alerts` rows in the 15-min window), reverted to
   `anthropic/claude-haiku-4-5`. Snapshot at
   `/tmp/balthasar_pre_byok_swap_20260520.json`. Generic runbook at
   `/root/xrp_grid/RUNBOOK_BYOK_FAILOVER.md` covers all three agents
   symmetrically: mapping table, pre-flight checks, parameterized
   swap/verify/revert sections, known-divergence flag
   (`parallel_tool_calls=False` on BYOK handles vs server-forced True
   on base). `provision_agents.py` not yet updated — that is the
   production-side follow-up.
9. **BYOK contingency Dim 1 + ntfy push layer shipped.** Two
   complementary visibility legs:
   - **Dashboard AGENT HEALTH tile.** Three persistent
     red/yellow/green chips above ALERTS, one per agent
     (CASPER/MELCHIOR/BALTHASAR), state computed from the last 3 R0
     rows per agent in `debate_records`. Safe-default predicate:
     `conviction = 0.0 AND crux LIKE '(no response)%'` (mirrors
     `council.py:SAFE_DEFAULTS`). 0/3 degraded = green, 1/3 = yellow,
     2-3/3 = red (with subtle pulse animation). Each chip carries the
     current model handle pulled from `agent_registry` so a swapped
     agent (e.g. on BATHY) is visible at a glance. New route:
     `/api/agent_health`. JS soft-refresh selector list extended to
     include `.agent-health-panel` — no meta-refresh, no chart iframe
     disruption.
   - **ntfy.sh push notifications.** New module `magi/notify.py:
     send_ntfy(title, body, severity, agent_id, category)`. Hooked
     into `database.insert_alert` AFTER the row commits; fires only
     for `severity='critical'`. Topic URL via `NTFY_TOPIC_URL` in
     `.env` (publicly-readable topic — body intentionally omits raw
     message text). Severity → priority: critical→5 (bypasses iOS
     DND), warn→3, info→no fire. 3s timeout + blanket `except` make
     notification failure non-blocking. Unset/empty topic URL =
     silent no-op (verified). All 7 verification steps passed; test
     rows cleaned from `magi_alerts`. The two legs are complementary,
     not redundant: chip catches the operator at the desk; ntfy
     catches them when away.
10. **Contingency plan close-out — items 3, 4, 5.** Three hooks
    share the same `conviction=0.0 AND crux LIKE '(no response)%'`
    fingerprint (mirrors `council.py:SAFE_DEFAULTS` and the dashboard
    chip predicate):
    - **Item 3 — degraded-mode hard rule** in
      `magi/orchestrator.py:enforce_hard_rules` at position -1 (top of
      stack). Queries `debate_records ORDER BY id DESC LIMIT 2` —
      `enforce_hard_rules` runs at step 11 of `run_cycle`, BEFORE the
      current cycle's debate_record insert at step 12, so the query
      naturally returns the two prior cycles. 1 degraded agent →
      `[AGENT_DEGRADED:<agent_id>]` freeze (force MAINTAIN + CLEAR);
      2-3 degraded → `[COUNCIL_COLLAPSED]` HALT. Rule 6 (GRID_DEGENERATE)
      now skips while a degraded freeze is in effect — a degraded
      council cannot supply trustworthy geometry, so forcing a RECENTRE
      against the scorer rank-1 would defeat the freeze's purpose.
      Edge-triggered alerts via `_maybe_fire_degradation_alert`:
      `system_state['last_degraded_tier']` persists the prior tier
      (0/1/2) and the next cycle compares; only tier-UP transitions
      emit a `severity='critical'` row, which fires ntfy. Tier-down
      and same-tier are silent.
    - **Item 4 — observer backfill-notify streak**: module-level
      `_backfill_failure_streak` dict in `observer.py` increments on
      each failed `_send_outcome_to_agent` future result; the 3rd
      consecutive failure (and ONLY the 3rd — explicit guard, not
      reliant on the alert-dedup window) writes a
      `severity='warn', category='backfill_notify_failed'` row.
      Resets to 0 on any success. Dashboard-only, no ntfy.
    - **Item 5 — memory rotation pre-gate**: `rotate_agent_memory`
      step 0 queries the last 30 R0 rows for the target agent and
      counts safe-defaults; if ≥ 12/30 (40%), the agent's rotation
      aborts with `status='skipped_degraded'`. No snapshot, no
      compact, no thread reset. New schema column
      `memory_rotations.degraded_count_in_window INTEGER DEFAULT 0`
      records the count on every rotation attempt (success OR skip),
      so the operator can see WHY any rotation was skipped without
      digging into the journal. Idempotent ALTER applied via
      `init_db` — verified `PRAGMA table_info` shows the new column
      at index 10. `severity='warn',
      category='rotation_skipped_degraded'` alert written;
      dashboard-only, no ntfy.
11. **READINESS decision-support panel.** Module `magi/readiness.py`
    evaluates the live-readiness gate set on every dashboard render;
    pure read-only, no schema changes, no control logic.
    (NOTE: the renewal-decision gate set described here originally —
    R1–R7, trailing-14d, RENEW/MARGINAL/DO_NOT_RENEW, 2026-06-03 — was
    **removed 2026-05-23** when the bot went live. See Session
    2026-05-23. Only the LIVE READINESS set below remains.)
    - **Live readiness** (L1–L9, lifetime). Verdict aggregator:
      0 fails → GREEN, 1-2 → YELLOW, 3+ → RED. Verdict as of the live
      cutover: **RED** (early lifetime data — few round trips / trading
      days; expected to climb as live history accumulates).
    - Round-trip counting: lifetime gates use the global
      `grid.pnl._fifo_match` (correct for inventory accounting).
    - HALT-state tracking does NOT exist; the fill-gap gate (L6) does
      not exclude HALT periods. The gate `detail` string calls this
      out so the operator can manually consider it.
    - Flask route: `/api/readiness` (shape `{live:{verdict,gates},
      generated_at_utc}`). Panel placement: below INVENTORY + PAPER P&L
      pair, above Manual Actions. Soft-refresh selector list includes
      `.readiness-panel`.

### Letta Cloud agents (provisioned, verified live 2026-05-22 — recreated)

Agents were wiped and recreated on 2026-05-22 ~16:00 UTC. Personas
re-pushed under the new R1-synthesis contract (see Session 2026-05-22
item 3). Self_models are intentionally empty post-recreation — DO NOT
seed.

| agent_id | Letta display name | Model | Persona chars (live) | Self-model |
|---|---|---|---|---|
| casper | Casper | google_ai/gemini-3-flash-preview | 13364 | empty (post-wipe) |
| melchior | Melchior | openai/gpt-4o | 18216 | empty (post-wipe) |
| balthasar | Balthasar | anthropic/claude-haiku-4-5 | 17577 | empty (post-wipe) |

Letta agent UUIDs live in `agent_registry` (regenerated this session;
prior UUIDs are dead). Refresh via:
`sqlite3 /root/xrp_grid/observer.db "SELECT agent_id, letta_agent_id, model FROM agent_registry;"`

Pattern numbering will restart at `## Pattern 1` per agent on the first
post-recreation rotation. Eviction of the lowest-numbered block under
`SELF_MODEL_CHAR_CAP=5000` still applies.

Letta display names were capitalized 2026-05-18 (Casper / Melchior /
Balthasar). The lowercase `agent_id` strings remain canonical everywhere in
code, DB, and CONFLICT_MATRIX — the rename only affects the Letta web UI.

**Per-agent LLM config** (synced via `provision_agents.AGENT_CONFIG`,
idempotent): temperature 0.3, max_output_tokens 8192, thinking enabled at
medium effort with 2048-token budget where the provider supports it. GPT-4o
uses `reasoning_effort=medium` (no native extended-thinking budget).
`parallel_tool_calls` is platform-forced True.

### Self-hosted Letta — DECOMMISSIONED but PRESERVED
- `letta.service` stopped + disabled
- `/root/xrp_grid/letta/` directory intact (docker-compose.yml, .env, pgdata/) for rollback
- Do not delete

## Verified facts (do NOT re-derive in future sessions)

### Migration scoping + §7-H verification (2026-05-29)
- **Migration direction locked:** Casper→Google, Melchior→OpenAI,
  Balthasar→Anthropic; agents stateful vendor-side; SQLite owns non-agent state;
  seed from `snapshots/letta_shutdown_2026-05-28/`. Full detail: Session 2026-05-29
  above + `00_PROJECT_OVERVIEW.md` "Migration target architecture".
- **Letta surface audit:** 112 touch-points / 17 files; `council.py` is the centre
  of gravity (all 3 AGENT_INVOKE sites). See `LETTA_SURFACE_AUDIT.md`.
- **`should_run_r1`** is a real novelty-gate (`council.py:1445-1463`,
  `orchestrator.py:1876-1895`), NOT dead code. R1 has been conditional since
  2026-05-27.
- **`emit_human_alert`** is dead code (def `council.py:1638`, import
  `orchestrator.py:59`, zero call sites) — delete during the port.
- **`council.py:238` constructs the Letta client at module-import time** — a hard
  import-time dependency for every transitive importer of `council`; must be made
  lazy before the council rewrite (item M5a).
- **`extract_test_cases.py`** is a one-off manual script, NOT a live consumer of
  `magi_decisions`.

### Kraken API
- HMAC auth: signature = HMAC-SHA512(path + SHA256(nonce + urlencoded_payload))
- Headers: `API-Key`, `API-Sign`, `Content-Type: application/x-www-form-urlencoded`
- Pair name: `XXRPZUSD` (alt `XRPUSD`, WS `XRP/USD`)
- Price decimals: 5 — lot decimals: 8 — cost decimals: 8
- Order min: 1.65 XRP — cost min: $0.50. **Per-order size is pinned to
  this minimum** via `config.py:ORDER_SIZE_XRP = 1.65` (see Session
  2026-05-24); every grid order is exactly 1.65 XRP. Valid at any XRP
  price > ~$0.30 (1.65 XRP clears the $0.50 cost min); below that a
  1.65-XRP order would be sub-minimum-cost.
- Tick: 0.00001
- Pro tier rate limits: trading max=125 decay=2.34/s; account-mgmt max=20 decay=0.5/s
- Open orders cap: 80 per pair
- `GetAPIKeyInfo` endpoint is **gone** (HTTP 404) — verify tier and permissions via the Kraken web console
- Live keys verified working for `Balance` endpoint (fund detection
  passed at scheduler startup 2026-05-20). `AddOrder` / write-side ops
  not yet exercised against the live keys — verify before going live.

### Kraken fees (tier 0)
| 30d vol $ | Taker | Maker |
|---|---|---|
| 0 | 0.40% | 0.25% |
| 10K | 0.35% | 0.20% |
| 50K | 0.24% | 0.14% |
| 100K | 0.22% | 0.12% |
| 250K | 0.20% | 0.10% |
| 500K | 0.18% | 0.08% |
| 1M | 0.16% | 0.06% |
| 10M | 0.10% | 0.00% |

`config.py` uses `TAKER_FEE = 0.0040`, `MAKER_FEE = 0.0025` (tier-0,
verified via live test order 2026-05-23 — fee 0.01070728 / cost
2.67682 = 0.40% exactly).

### Asset analysis (completed; do not re-derive)
- DOGE: best grid PnL historically (~$606 over ~4.2yr, ~2.5%/4 levels) — top performer
- XRP: most forgiving grid dynamics — current active asset
- SOL: narrow grid characteristics — viable but constrained
- ADA: eliminated (does not meet grid trading criteria)

### Optimal grid spacing (determined per asset)
- XRP: 1.5%
- DOGE: 2.5%
- SOL: 2.0%

Hard caps in `config.py`: `MAX_GRID_SPACING_PCT = 0.025`, `MIN_GRID_SPACING_PCT = 0.003`.

### Memory rotation (verified live 2026-05-20)
- `client.agents.messages.compact()` exists in letta_client 1.11.0 and
  returns `CompactionResponse{num_messages_before, num_messages_after,
  summary}`. The `summary` field is the model-generated distillation
  text (the prompt drives the format). Self-compaction modes
  (`self_compact_sliding_window`, `self_compact_all`) run on the agent's
  own model; `sliding_window_percentage` controls how much of the recent
  thread is kept verbatim. Compaction writes to the thread (replaces
  older messages with a system-alert pointer), NOT to memory blocks —
  block writes must go through `client.blocks.update()`.
- `client.agents.messages.reset(agent_id,
  add_default_initial_messages=False)` clears the thread cleanly. We
  pass `False` because the next MAGI cycle feeds `world_state` + cycle
  prompt through the existing `council.py` flow.
- `client.agents.messages.list(limit=...)` is server-capped at 200. A
  higher limit returns HTTP 422; the server may also rate-limit with
  `agent_messages_large_page_rate_limit_exceeded` (429). The lifecycle
  module does not need full message lists, so this is informational.
- Rotation cadence: `ROTATION_CADENCE = 30` cycles. Counter persists in
  the `system_state` table (key=`rotation_cycle_counter`). At the live
  cadence of 6 MAGI cycles/day, rotations fire roughly every 5 days
  per agent.
- Per-rotation accounting lives in the `memory_rotations` table — one
  row per agent per rotation attempt regardless of outcome. Snapshots
  to `/tmp/self_model_pre_rotation_<agent>_<YYYYMMDD>.json`.

## Environment

`/root/xrp_grid/.env` contains:
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY` (main system uses GOOGLE_API_KEY for Casper's direct path; Letta Cloud reads from Casper's model handle separately)
- `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` (currently invalid — regenerate before live)
- `COINBASE_API_KEY`, `COINBASE_API_SECRET` (legacy, unused; `EXCHANGE = "kraken"`)
- `CF_ACCOUNT_ID`, `CF_GATEWAY_ID`, `CF_AIG_TOKEN` — Cloudflare AI Gateway
- `LETTA_API_KEY` — added 2026-05-16 for Letta Cloud
- Backup of pre-migration .env: `/root/xrp_grid/.env.pre-cloud-migration.bak`

`/root/xrp_grid/letta/.env` exists but is unused (self-hosted dormant).

## Services
| Service | State |
|---|---|
| magi.service | **inactive + disabled** (halted 2026-05-25 ~20:25 UTC — T2 credit burn; do not re-enable until Problem 2 resolved) |
| magi-dashboard.service | active |
| letta.service | inactive + disabled (intentional) |
| docker.service | active (no MAGI containers; engine doesn't use Docker) |

Restart pattern: `systemctl enable magi.service && systemctl restart magi.service magi-dashboard.service`

## Live state (verified 2026-05-22 ~20:00 UTC)

- MAGI cadence: **every 4 hours (6 cycles/day)**, EST hours [0, 4, 8, 12,
  16, 20]. Letta Cloud operational. Agents wiped + recreated this session;
  7 post-recreation cycles so far. `rotation_cycle_counter` reset to 0
  (next rotation at counter=30).
- Observer cadence: **every 10 min** (was 60). Public Kraken REST hits
  (Ticker + OHLC) are well inside rate limits.
- Grid: centre **1.36612** (anchor fill price from 5/19 16:00 RECENTRE),
  spacing **0.0075**, levels **5**, pause flags 0/0.
- Inventory: **xrp=19.77 / usd=$39.10 / skew −0.09** (well-balanced
  after the 5/21 round-trips).
- **Last fill: 2026-05-21T11:06:05 — BUY 6.59 XRP @ 1.3643.** Most
  recent visible round-trip: BUY 2026-05-20 19:04 @ 1.3649 → SELL
  2026-05-21 01:00 @ 1.3754 (~+0.77% gross, ~+0.45% net of taker fees).
- **9 real fills since the anchor mechanic shipped 2026-05-19T13:04**
  (5 buys, 4 sells, $0.27 total fees). The 91h drought referenced in
  prior sessions is closed — the rule layer + new engine mechanics are
  producing fills without council deliberation (Round 1 fired 2/146
  cycles lifetime).
- Production Letta agents (unchanged from 5/18):
  - Casper: gemini-3-flash-preview
  - Melchior: gpt-4o
  - Balthasar: claude-haiku-4-5 (registry row corrected this session)
- Shadow infrastructure: 6 variants (lc ∈ {6,8,10,12,14,16}), all
  persisted at the live spacing 0.0075 after the persist-key fix.
  Note: variant level-counts don't include the live `lc=5` because
  `config.GRID_LEVEL_VARIANTS` is hardcoded `{6,8,10,12,14,16}` —
  shadow sim shows neighbour configs but not the live one. Cleanup
  candidate (see Outstanding).
- `debate_records.geometry_source` column populated on cycles since
  the migration. Latest cycle (`cyc_1779198478`, 13:47:58 UTC):
  `MAINTAIN/CLEAR + [GRID_HEALTHY_NO_RECENTRE], geometry_source=unchanged`
  — rule 0a behaving correctly (drift ≈ 0 vs 0.75% spacing).

## Outstanding issues

### Engineering (non-blocking)
- **Live-mode market anchor** — RESOLVED 2026-05-23. Implemented +
  exercised (real 2 XRP test fill, txid OIGJW7-4GZ7T-AACAYV). Live arm
  persistence + `engine.reconcile_live_fills_from_kraken` fill path also
  shipped. See Session 2026-05-23.
- **Fill detection still 1h-candle granularity.** Observer cadence
  dropped from 60→10 min, but the underlying touch model still
  evaluates the most recent COMPLETED 1h candle's H/L. Latency from
  "live tick touched a level" to "fill registered" is still up to
  ~70 min in the worst case. Next step (deferred): intra-bar polling
  of Kraken OHLC, or WebSocket v2 `ticker` channel.
- **Temp debug log** `[SIMFILLS DEBUG]` in `grid/engine.py:simulate_fills`
  — still in place per inline comment. Safe to remove.
- **Melchior conversation-history anchoring** — GPT-4o still reproduces
  prior-cycle evidence byte-for-byte. Verified 2026-05-21: 6 consecutive
  cycles spanning 12h emitted identical evidence lists with stale
  numbers (`vwap_dev_pct: 0.5291`, `autocorr_1h: -0.0244`). Memory
  rotation fired 2026-05-20T16:00 and reset the thread; the freeze
  re-emerged within ~4h, confirming the 30-cycle cadence is too sparse
  for this failure mode. The `[GEOMETRY_INJECTED_FROM_SCORER]` fallback
  papers over the geometry half but the vote half is naked. → see
  `02_NEXT_BUILD_TASKS.md` task 1 for the L1/L2/L3 structural fix
  proposal.
- **CONFLICT_MATRIX coverage gap** — RESOLVED 2026-05-22 via the council
  restructure. R1 now always fires; `regime_action` + `geometry_veto`
  introduce two new structural-vote axes the engine reads via hard rule 0d.
  CONFLICT_MATRIX retained as dead code (backward import compat only); not
  consulted. The renewal-decision question ("is the council load-bearing
  or ornamental?") now operationalises as: how often does rule 0d fire?
  Visible in the COUNCIL LEVERS dashboard panel.

- **Rule 0d tag-emission bug (open).** Latest cycle (`cyc_1779480012`,
  2026-05-22T20:00:12) had Casper `STAND_DOWN` + Balthasar `RISK_BLOCK` +
  Melchior `RECENTRE` — first cycle that should have triggered the council
  veto branch. `hard_rule_overrides=[]` and `applied_grid_action=NULL` in
  the debate_record row. Either rule 0d isn't writing the tag back to the
  row, or there's a row-write race between orchestrator and engine. Needs
  investigation next session.
- **`GRID_LEVEL_VARIANTS` ≠ `DEFAULT_VARIANTS` level range.** Shadow
  sim uses `{6,8,10,12,14,16}` from `config.py`; scorer uses
  `{5,6,7,8,9,10}` from `magi.spacing_evaluator`. Live `lc=5` isn't
  in the shadow set. Cosmetic — doesn't affect live trading.
- **17 NULL `hard_rule_overrides` rows** in `debate_records` from the
  pre-column-migration window; under-reports the 30-day override panel
  until rows age out.
- **Orphan persona blocks** — RESOLVED 2026-05-21 (see "Session 2026-05-21
  orphan-block sweep" entry above). The "six" figure originally noted here
  was a ~60× under-count from eyeballing the web UI; SDK enumeration found
  357 unattached blocks total, all deleted.
- **Dashboard `magi_decisions` reads** — two analytic reads migrated in
  prior session; `/api/status:1777` still reads `magi_decisions` for
  back-compat. Full migration deferred until dual-write is retired.
- **self_model pattern numbering grows monotonically.** Each rotation
  appends `## Pattern (max_N + 1)` blocks; no curation step. Eviction
  of the lowest-numbered block is the relief valve when a merge would
  exceed `SELF_MODEL_CHAR_CAP = 5000`. Eventually the older patterns
  will get evicted on FIFO order — fine in steady state, but means
  there's no merit-based pruning. Watch item, not a blocker.

### Pre-live — RESOLVED 2026-05-23 (bot is live)
- **Dashboard auth** — RESOLVED. Moved to a Flask signed-cookie session
  (`/login`, `DASHBOARD_PASSWORD` + `SECRET_KEY` in `.env`); nginx
  `auth_basic` removed. The internal auth token in page source is no
  longer the access gate. See Session 2026-05-23.
- **Kraken keys** — VERIFIED. `AddOrder` / `QueryOrders` / `ClosedOrders`
  / `Balance` all exercised against the live keys 2026-05-23 (real test
  order filled).
- **Base rates staleness** — deferred (not a live blocker).

### Deferred docs
- **CHANGELOG.md** — long deferred.
