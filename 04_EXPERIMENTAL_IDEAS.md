# EXPERIMENTAL IDEAS

> **STATUS: EXPLORATORY — NOT ADOPTED.**
> Nothing in this file is a decision, a spec, or current system state. It is a
> holding pen for design directions under consideration during the rebuild/downtime.
> The canonical state of the system lives in `01_CURRENT_STATE.md`; the committed
> build plan lives in `02_NEXT_BUILD_TASKS.md`. **If anything here conflicts with
> those files, those files win.** Do not implement from this document. Do not treat
> any item here as settled until it has been explicitly promoted into a canonical
> doc.

_Last updated: 2026-06-06 · Origin: design-exploration session (Aaron + Claude)_

---

## Purpose

A record of design directions discussed but not yet committed, so the reasoning
isn't lost and future review starts from where the thinking actually ended —
including which parts rest on principle and which are still waiting on data.

---

## 1. Organizing principle under consideration: decouple awareness from cognition

The idea the rest of this document hangs on. Continuous **sensing** runs at
deterministic-Python cost; expensive **reasoning** is summoned only when sensing
says it's warranted. These are two different functions that the naive design fuses,
and fusing them forces a bad binary: poll the LLMs often (aware but bleeding cost)
or poll them rarely (solvent but blind between wakes). Separating the two dissolves
the trade — continuously aware *and* rarely cognitive at once, because awareness
was never the thing that cost money; the LLM calls were.

This is not a new architecture. It is the discipline the shutdown-era gate already
embodied (screen continuously, defer cognition upward, periodic backstop as the
safety floor), pushed one layer deeper into the council itself.

---

## 2. Tiered awareness/cognition structure (draft)

**Layer 0 — The Gate (always on, deterministic).** Continuous lightweight watcher
over the world_state, two complementary jobs:
- *Trip-wires (T1–T15):* catch fast, sharp changes (price breaches, volatility
  flips). These fire the escalation path.
- *Periodic backstop:* a full-council look on a fixed interval regardless of
  trip-wire activity. This is the gate's own false-negative insurance — trip-wires
  catch sharp moves, the backstop catches slow below-threshold drift the trip-wires
  are blind to by design. Together they cover both failure shapes. (At shutdown the
  backstop was ~daily, which was a sound call for XRP grid geometry.)

**Tier 1 — Cheap first seat (escalation-biased screen).** When the gate fires, the
cheapest reasoner evaluates first (Casper alone, optionally fed by a deterministic
"ghost" pre-classifier — e.g. ADX for trend strength, a Hurst estimate for
mean-reversion — computed every observer tick). Critical design property: this
tier's job is **screen-and-defer, not resolve-and-dispose.** It must fail *toward*
escalation — any genuine doubt wakes the next tier rather than ruling the call easy.
That escalation bias is what makes concentrating the triage decision in the
least-diverse component tolerable: the failure mode becomes "woke the council
unnecessarily" (cheap, visible, you eat some LLM cost) rather than "silently
suppressed a call that mattered" (expensive, invisible — the dangerous one).

**Tier 2 — Full council (convenes only on pre-filtered hard cases).** Because Tier 1
disposes of the unambiguous calls, the council only sees inputs *enriched for genuine
ambiguity*. This routes weak-seat noise away from the layer where divergence would
turn into noise, and makes disagreement at this layer *informative*: three reasoners
splitting on a pre-filtered hard case is close to the cleanest "stand down, genuinely
uncertain" signal available, because the easy calls that manufacture spurious
disagreement never reached here.

**Hard rules layer — survival floor (unchanged).** Deterministic, non-negotiable,
overrides council. Wiring note carried from discussion: the risk/survival seat
(Balthasar) must remain **independently summonable on a pure risk trigger** even when
Tier 1 reports "regime unchanged" — a risk event and a regime event are not the same
thing, and a calm-regime read must not be allowed to suppress a survival check.

---

## 3. The LLM-diversity question — SUSPENDED PENDING EVIDENCE

> The three-distinct-vendor rule remains the formal hard constraint until data says
> otherwise. This section records *why it is being re-examined*, not a decision to
> change it.

The vendor rule was always instrumental, not a goal in itself. Its original purpose
was to enable genuine dialectical *interaction* between independent reasoners, which
required reasoners independent enough not to collapse into the same priors. That
interactive modality currently appears out of reach (economically and technically),
which removes the feature the rule was protecting.

Two things follow, and the tension between them is the point:
- **Don't over-correct to "diversity is worthless."** Even without interaction,
  independent perception calls give *uncorrelated error* — three model families
  failing differently on an ambiguous read is still information (decorrelation, not
  synthesis).
- **But vendor identity is a weak proxy for decorrelation.** Frontier US models have
  largely converged; their remaining differences read as temperament (verbosity,
  hedging, phrasing), not judgment. Three of them on the same world_state may be
  paying three times for largely one opinion.

**The sharper axis (R&D, not a rebuild pivot):** genuinely divergent *lineages* —
e.g. a US model, a Chinese-trained model, and an open-weight model — differ in
training corpora and RLHF philosophy, which is the kind of divergence that actually
decorrelates error. Caveat: divergence cuts both ways (a weaker seat is just *wrong*
more often on easy calls), and open-weight means self-hosting, which reintroduces
the operational weight the rebuild is trying to shed. File as a controlled offline
study to run once the system is live and stable — not a migration-time change.

**How tiering interacts with this:** if the divergent council only convenes on
Tier-1-filtered hard cases, the "weaker seat noise on easy calls" objection is
largely removed by construction — the easy calls never reach the divergent layer.
This makes a divergent council more defensible *inside* a tiered structure than as
an always-on council. Still to be tested, not assumed.

---

## 4. Cost-reduction ideas reviewed

Condensed from a reviewed external suggestion set. Kept / reshaped / parked:

- **Sequential / tiered activation — KEEP.** Highest-leverage, touches no survival
  logic. This is Tier 1 above. Protect Balthasar's independent risk summon.
- **Deterministic "ghost" pre-classifier (ADX / Hurst) — KEEP, as a pre-filter that
  feeds Casper, NOT a replacement for him.** Replacing Casper outright drops the
  council to two LLM seats and breaks the diversity property by accident rather than
  by tested decision. Ghost as trip-wire, Casper as the perception layer that
  confirms or overrides.
- **Cheaper Melchior seat — KEEP as a model-seat question, not a hosting question.**
  As Melchior's role becomes closed-form expected-PnL scoring over ranked variants
  (structured computation, not open-ended judgment), a smaller/cheaper seat becomes
  plausible. This is the salvageable kernel of the "local SLM" suggestion; running
  *judgment over live capital* on a 7B/8B local model is a real quality risk and is
  not recommended.
- **Knowledge distillation — PARKED.** Sound instinct (capture expert decisions,
  train a cheaper mimic) but the suggested data source (`debate_records`) is the
  contaminated/stale table and must not be used. Revisit only with a *clean* labeled
  set generated by the rebuilt system going forward, after a few hundred trustworthy
  cycles. Not now.
- **Wasm / edge-compute agents — DROP for cost purposes.** Doesn't lower per-token
  LLM cost; spend is API inference, not where orchestration code runs. The real cost
  knobs are model choice and call frequency, both already addressed above.

Note on any "% savings" estimates from the source set: they assumed a GPT-4o /
Claude-3.5-Sonnet stack. The real per-seat baseline has already moved (Balthasar is
on a Haiku-class seat), so recompute savings against actual current costs before
letting any headline number drive a decision.

---

## 5. Settled vs. open dials

**Settled (rests on principle, held under pushback):**
- Awareness/cognition split.
- Gate trip-wires + periodic backstop as dual coverage of sharp and slow failures.
- Escalation-biased screen as the thing that makes the Tier-1 concentration point
  safe (it fails toward waking the council, never toward silent suppression).
- Divergence-on-pre-filtered-cases as an uncertainty signal rather than noise.

**Open dials (need data, not argument):**
- **Tier 1 escalation threshold** — the real decision. Too conservative → cost win
  shrinks; too confident → occasional suppression of a real call. A position on a
  dial, not a binary.
- **Backstop interval** — sets the max duration a gate blind spot can persist before
  it's caught; asset-dependent.
- **Diversity / vendor count** — suspended pending evidence (Section 3).
- **Model seats** — including how cheap Melchior's seat can get.

---

## 6. The measurement that resolves the dials

Replay historical world_states (reconstructed from the current downtime data
collector) through the tiered structure and measure the **Tier 1 false-negative
rate**: how often the cheap screen routed a call as "easy/unchanged" that the full
council would have acted on.
- Near-zero → design is sound, lean on it.
- Nonzero → tune the escalation threshold before it gatekeeps anything near live
  capital.

The same replay harness answers the diversity question empirically: run identical
states through a converged council vs. a divergent one and look at *where* the
disagreements land — concentrated on genuinely ambiguous high-stakes regimes
(diversity earning its seat) or scattered across easy calls (diversity paying for
redundancy / a weak seat). Decide from the data, not the principle.

**Dependency — now RESOLVED (2026-06-04).** Schema CONFIRMED (`aye5788/market-tape`,
`tape/schema.py`): the live collector captures 1m OHLC + the full trade tape + spread
(`ohlc_1m` / `trades` / `spread` / `rollup_bars`), enough for *faithful* world_state
reconstruction on the live span — not just coarse OHLC regime replay. (`book_l2` is
schema-defined but the `book` channel is currently OFF, so there is no L2 depth yet.)
The historical→live **stitch is VERIFIED**: the separate `history.db` warehouse is one
gap-free 1m series — Bitstamp 2016-12 → 2026-06-02, then live Kraken — joined in a
single table with a per-row `source` flag; the seam (2026-06-02 12:22→12:23) is
contiguous with no gap/overlap/dup, and the hourly append is now gap-aware so
out-of-order backfills self-heal. As of 2026-06-04 the warehouse also carries trades +
spread forward (one-time catch-up: ~100.9k trades + ~64.6k spread back to 2026-06-02;
gap-aware INSERT OR IGNORE append, warehouse does not prune), so order-flow/spread
rollup columns populate over the live span.
**Caveat for the replay:** OHLCV is continuous across the full ~9.5-year series, but
vwap / trade-count / order-flow / spread (and any L2 depth) exist only on the live span
(2026-06-02→); the deep Bitstamp history is OHLC-only. Filter by `source` and scope
microstructure-dependent measurements to the live span.

---

## 7. Explicitly NOT being proposed

- No change to the hard-rules survival floor.
- No use of `debate_records` for distillation, training, or evaluation.
- No collapse to a single cloud vendor (the "Casper deterministic + Melchior local +
  only-pay-for-Balthasar" endpoint is the one configuration that quietly dissolves
  the diversity property; rejected as written).
- No re-enabling of services or any action that assumes live cycles are running.

---

## Session 2026-06-04 — Council redesign: arbiter + genuine debate (DESIGN DECISION, not yet built)

A design session that reworks the council's decision flow and rebalances how much
authority is deterministic vs. delegated to the LLMs. Everything here is **agreed
direction, not code**: the live ADK build still has Melchior emitting the verdict, and
none of the below is implemented. Vendor diversity (three distinct providers) and the
small hard survival floor are unchanged. Which model occupies which seat is NOT decided
here — seat occupants stay deferred to the stressed-world_state tuning loop (in
particular, do **not** assume the arbiter seat is a Claude model).

**1. Balthasar = final arbiter / synthesizer.** SUPERSEDES the prior intent that
Melchior synthesizes regime + economics into the geometry decision. New flow: Casper
supplies regime, Melchior supplies grid economics, and **Balthasar synthesizes both
through a risk lens and makes the final call.** Synthesis-as-final-call is inherently a
risk judgment, so it belongs to the survival-biased agent, not the opportunity-biased
economist — Melchior's action-bias is exactly why action-selection in that seat proved
unreliable. The veto ladder (CLEAR / PAUSE_LONGS / PAUSE_SHORTS / HALT) becomes the
*form* Balthasar's synthesis takes, not a post-hoc gate bolted on after someone else's
decision. Chain of command: Casper + Melchior advisory, Balthasar decides. Model-in-seat
still deferred to the tuning loop (not assumed to be the Claude seat).

**2. Determinism-vs-vision rebalance.** Diagnosis: deterministic/static governance
ratcheted up one locally-justified step at a time until the council's authority had
narrowed to a slot in a Python scaffold — drifting from the original dialectical vision.
Decision: the survival floor stays **hard but small** — (a) no fabricated spacing
(GRID_PAUSE if no valid geometry), (b) position/inventory limits, (c) NO_PROFITABLE_GRID
stand-down, (d) the veto-ladder *mechanism*. Everything else now deterministic — the R1
second-filter, verdict→action mapper richness, vote/rule weighting in rule 0d, veto
*triggers* — is a candidate to hand back to a council that genuinely meets. Burden of
proof is on KEEPING anything hard: if no concrete capital-loss path is named, it isn't
floor. Economic justification: the LLM council only earns its cost if it governs; if
determinism hollows it into a rubber stamp, the rational move is to delete it and run a
pure mechanical grid — so genuine council authority is what justifies the architecture
existing at all.

**3. Debate protocol — the dialectic returns.** The production council never actually
convened: R0 voted in isolation and R1 ("debate") fired ~1 of 38 cycles. Decision: when
the gate fires, the council **genuinely meets**. Sequence: gate fires on a SUSTAINED /
dwell-confirmed breach (persistence distinguishes a real trend from a wick — reuses the
WAKE_DWELL_MINUTES logic, not an instantaneous trip) → Casper reads regime first (regime
gates everything) → Melchior brings economics, prompted **orthogonally** ("given regime
X, what do the economics say") rather than evaluatively, to avoid GPT-4o anchoring on /
deferring to Casper → **one rebuttal round that always runs** (each agent sees the
round-1 transcript and may PASS with a stated reason, never silent assent — this catches
coincidental agreement) → Balthasar synthesizes the full record and calls it → the small
hard floor bounds the executed output. The old R1 second-filter collapses **into the
gate**: if the gate judged the moment worth convening, convening *means* debating — do
not re-gate whether they argue. Statelessness is preserved: agents reading each other
*within* a cycle is not cross-cycle vendor state (the thing the migration removed); no
vendor memory persists between cycles.

**4. Per-agent Journal — the interpretive layer of the scoped recall layer.** Each agent
(not one shared journal — keeps the personas distinct) writes a short, outcome-validated
continuity note. It is **generated but not trusted as state**: the agent drafts it, it is
scored against that role's realized outcomes, and it is injected next cycle only if it
survives — which defuses the self-rewriting-hidden-state failure that corrupted the Letta
self_model. Cold start is **empty-but-structured** (no seeded lessons; it accrues only
validated outcomes). Drift control: each entry is rebuilt from OUTCOMES, never from the
prior journal (cap the lookback window, not just per-entry tokens). HARD DEPENDENCY: needs
the per-role correctness fix (`get_agent_accuracy`, `database.py:1635`) first — without
it, validation would teach Melchior to over-trade. This is the interpretive layer of the
already-scoped recall layer (post-migration queue item 4), not a competing mechanism.

**5. Outcomes are per-role — what "validation" means.** The single `fills>0 AND pnl>=0`
metric is wrong for all three seats (the `database.py:1635` bug). Correct per-role:
- **Casper** — was the regime call correct (directional, derived from price; fully
  backtestable on `history.db`).
- **Melchior** — did realized round-trips clear his after-fee forecast (calibration;
  OHLC-derivable across history, depth-faithful only on the live span).
- **Balthasar** — partly counterfactual: a correct HALT prevents a loss that then never
  appears, so the false-positive cost (bailing before a recovery) must ALSO be scored —
  otherwise an always-HALT agent looks perfect.

Status: design agreed 2026-06-04, NOT yet built. Supersedes the Melchior-synthesizer
framing in `00_PROJECT_OVERVIEW.md` and `CLAUDE.md` (see superseded notes there). Build
sequencing: per-role accuracy fix → debate orchestration → arbiter restructure → collapse
R1 filter into gate → journal.

## Session 2026-06-06 — Balthasar + `drawdown_from_high_7d`: decision-test RESULT (OFFLINE)

Empirical follow-up to the 2026-06-04 redesign. That design makes **Balthasar the
arbiter** and (item 5) scores his outcomes per-role, explicitly pricing the
counterfactual cost of a HALT that bails before a recovery. Open question feeding
both: if Balthasar is handed a `drawdown_from_high_7d` signal, does he actually act
on sustained price decline — i.e. does the signal (plus the persona authority to use
it) move his verdict in the **cautious** direction on downtrend world_states, WITHOUT
making him reflexively cautious on recoveries / benign books? This is the
grid-downtrend-bleed failure mode (the grid recenters into a sustained downtrend and
keeps buying the fall, accumulating inventory it can't sell back). Tested entirely
**offline** — the live system was never touched.

**Method (offline, scratch-only under `/tmp/phase2_balthasar_v2/`).** 60 reconstructed
XRP world_states from `tape/history.db`, stratified by forward price path into 3 strata
of 20: **TRUE_BLEED** (fwd-3d return < −0.03), **RECOVERY** (dd7d < −0.08 at t AND
fwd-3d ≥ 0), **BENIGN** (else). Each stratum sits on a fixed per-stratum stressed book
— TRUE_BLEED: `allocation_skew` +0.55 (deliberately just under the +0.60 PAUSE_LONGS
band), both buffers thin but above their $10 floor, buy-heavy 7/3; RECOVERY: mild long
tilt; BENIGN: balanced. 4 arms = {live persona, corrected persona} × {without, with
drawdown}, 240 calls to `claude-sonnet-4-6` via the Anthropic API at temp 0, faithful
R0 reproduction (persona as system prompt, the real `_r0_message`, forced `RiskVote`
tool). The **corrected** persona is in-memory only: it strips the live persona's "there
is no drawdown field" and "Never reason about price direction" lines and grants narrow
price-erosion authority (drawdown weighed as risk context alongside skew/buffers, *not*
a mechanical trigger). The on-disk `magi/agents/personas/balthasar.md` was NOT modified.
Cost: $2.49.

**Result.**
- **The drawdown signal alone is inert.** Live persona + drawdown (arm2 v arm1):
  TRUE_BLEED net zero (1 more-cautious / 1 less, the lone "more" not even citing the
  factor); RECOVERY/BENIGN flat. The live persona's explicit "no drawdown field / never
  reason about price direction" lines make it ignore the signal even when it is injected.
- **Signal + persona authority moves it, in the right direction, but weakly.** Corrected
  persona + drawdown (arm4 v arm3): of 20 TRUE_BLEED, **3 became more cautious and all 3
  cited the drawdown** — one full escalation `CLEAR/PROCEED → PAUSE_LONGS/HOLD_GEOMETRY`
  (dd −20.3%, fwd-3d −13.2%) and two geometry-veto escalations to `HOLD_GEOMETRY`
  (dd −14.7% / −14.4%, both of which forward-bled). 1 went less cautious (noise). The
  remaining 16 stayed `CLEAR/PROCEED`.
- **No false alarms.** RECOVERY and BENIGN strata showed **zero** movement in every arm.
  The corrected persona elevates caution only in the sustained-decline stratum it was
  meant to catch; it does not make Balthasar reflexively cautious everywhere.

**Read.** The combination is necessary and directionally right, but the magnitude is
small — even with both the persona authority and a severe drawdown, Balthasar left 17/20
stressed scenarios fully `CLEAR/PROCEED`, benchmarking price drawdown against his
skew/buffer ladder rather than treating it as a first-class trigger (he repeatedly framed
it as "approaching the +0.60 band" context, not its own threshold). Implication for the
redesign: a drawdown input is worth giving Balthasar **if** he becomes the arbiter, but
(a) the persona MUST change — the signal alone does nothing, confirming the CLAUDE.md
doctrine that persona prose is the weakest lever; and (b) the real lever is
weighting/threshold, not the prose. As a pure "context alongside skew/buffers" input it
barely fires as pure context. **SUPERSEDED 2026-06-06:** the deterministic `dd7d ≤ −X ⇒
PAUSE_LONGS` band floated here was **ruled out** — 3/20 is too thin to fit a hardcoded
threshold without overfitting; drawdown was instead adopted as a Balthasar **judgment
input** (corrected persona), not a deterministic band. The downtrend bleed it targets is
still a real risk Balthasar owns. See `02_NEXT_BUILD_TASKS.md` item 0★ /
`01_CURRENT_STATE.md` Session 2026-06-06.

Status: OFFLINE decision-test only. No live system change, no repo code touched (harness
and both personas live in `/tmp/`), services still stopped. This is **evidence for** the
council redesign (Balthasar-as-arbiter + per-role outcome scoring + where the downtrend
brake should live), not a shipped change.

**Follow-up (same day) — persona text PROMOTED, wiring DEFERRED.** On the strength of
the result, the corrected language was promoted to the live
`magi/agents/personas/balthasar.md` (persona **text only**; backup
`balthasar.md.bak.20260605`) — so this part is now shipped to the repo, superseding the
"no repo code touched" line above for the persona file specifically. The
`drawdown_from_high_7d` **world_state wiring was deliberately deferred** to a separate
step: `build_world_state()` does not emit the field and `world_state_schema.py:FIELDS`
has no entry, so until that step the persona references a field production does not yet
receive. The promotion is therefore inert in production for now. Tracked as the open gap
in `02_NEXT_BUILD_TASKS.md` item 0★ (which adopts drawdown as a Balthasar judgment
input, NOT a deterministic `dd7d ≤ −X ⇒ PAUSE_LONGS` band — that band was ruled out
2026-06-06 as overfitting). See `01_CURRENT_STATE.md` Session 2026-06-06.
