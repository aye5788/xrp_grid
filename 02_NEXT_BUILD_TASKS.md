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
