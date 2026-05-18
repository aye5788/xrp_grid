# Next Build Tasks

Priorities reordered after 2026-05-17 session. The highest-leverage work
right now is making the council's existing disagreement productive — the
49h+ fill drought is the real signal, and the architecture's intended
correction mechanism (Round 1 debate) is not firing on the patterns that
are actually happening.

## Highest priority

### 1. CONFLICT_MATRIX expansion driven by empirical divergence

`CONFLICT_MATRIX` in `magi/council.py` currently has 4 grid-state-aware
rules plus the original action-incompatibility rules. None of them match
the divergence pattern that has dominated the last ~50 cycles:

- Casper: TRENDING or RANGING with conviction ≥ 0.6
- Melchior: RECENTRE on stale fills
- Balthasar: CLEAR on a balanced book

These three signals are not in mutual contradiction by the old "actions
clash" definition, but they encode different *diagnoses* of the same
situation. The architecture intent is that genuine divergence routes to
Round 1 so the agents can challenge each other's reads — currently it
doesn't, and the council collapses into the same vote-recite loop every
cycle.

**Work:**
- Pull the last ~50 rows from `debate_records`; cluster the
  `(casper_r0_position, melchior_r0_position, balthasar_r0_position)`
  triples by frequency.
- For each high-frequency triple that has NOT triggered Round 1, decide
  whether it represents a real disagreement and add the rule. Candidates:
  - `(*, RECENTRE, CLEAR)` with `hours_since_last_fill > 24` —
    Melchior wants to rebuild, Balthasar says no risk, but the bot hasn't
    earned — casper vs. melchior debate?
  - `(TRENDING, RECENTRE, *)` — trending market + grid rebuild is at
    minimum questionable; melchior should defend rebuilding into a trend.
- Each new rule needs a `world_state`-aware predicate so it only fires when
  the pattern actually warrants debate.

Done when: at least one of the next ~20 cycles fires Round 1 on the
current divergence pattern, and `debate_triggered` rate over a 50-cycle
window is no longer 1/38.

### 2. Melchior conversation-history anchoring — decision

Cycles 40 through 46 show Melchior reciting byte-identical evidence
lists including stale `autocorr_1h: 0.0218` while the live `world_state`
block contains 0.0222. self_model curation (this session) did not shift
it. Options:

- (a) Clear Letta message history for `agent-65ee1ab4` via the SDK
  (investigate `c.agents.messages` / `c.agents.archives`). Risk: loses
  outcome-backfill context the agent has accumulated.
- (b) Wait for the (#1) CONFLICT_MATRIX expansion to land and let
  Round 1 force a different response surface. Lowest-risk; relies on
  #1 being effective.
- (c) Accept gradual drift — the hard rule layer keeps the bot safe,
  and the new self_model will eventually outweigh older patterns over
  many cycles. Cheapest; slowest to validate.

Decide after #1 lands. If Round 1 starts firing and Melchior still
recites cached responses inside the R1 challenge prompt, escalate to (a).

## Medium priority

### 3. Orphan Letta block cleanup

Six orphan persona blocks at project scope from prior provisioning runs
(IDs in session record). Plus orphan `human`, `decisions`, and
`self_model` blocks. Confirmed not attached to any current agent. Visible
in the Letta UI's Memory blocks page and misleading. Delete via
`c.blocks.delete(block_id)` after a final manual confirmation pass.

### 4. Backfill 17 NULL `hard_rule_overrides` rows

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

### 5. Dashboard `magi_decisions` migration completion

Two analytic reads migrated this session (latest-cycle override tags,
30-day counts). `/api/status:1777` still returns a `magi_decisions`-shaped
object for back-compat (consumer surface area unclear). Full migration
requires retiring the dual-write, which requires migrating `learning.py`
and `extract_test_cases.py` too. Defer until those readers are touched
for other reasons.

## Pre-live (still blocking live trading)

### 6. nginx basic auth — REQUIRED before live
Dashboard token visible in page source at `api.ethobs.uk`. Only remaining
security blocker for live deployment.

### 7. Fresh Kraken API key — REQUIRED before live
Current keys return `EAPI:Invalid key`. Regenerate via Kraken web console.
Two-factor live gate is already implemented (env var + token file +
paper flag); no code changes needed.

### 8. Live trading decision
After paper validation shows >50% accuracy AND positive PnL after fees
over a meaningful window. The current 49h+ fill drought means there is
no meaningful PnL window yet. Tasks 1 and 2 are the prerequisites for
that window starting to accumulate.

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

## Done this session (do not re-do)

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
  `current_fee_tier_pct` in world_state is hardcoded `MAKER_FEE = 0.0016`
  (Kraken XRP/USD tier-0). The Kraken client at `grid/exchanges/kraken.py`
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
