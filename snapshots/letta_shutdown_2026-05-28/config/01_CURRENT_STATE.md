# MAGI — Current State

Last updated: 2026-05-28 (**PnL-tracking overhaul + dashboard fixes + audit of
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
