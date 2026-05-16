# Next Build Tasks

## Immediate (this week)

### 1. Observe the council (no code)
The whole point of Phase 5 is persistent agent experience. Let cycles run
3–7 days. Watch for:
- Agents updating their `self_model` blocks (currently ~106 chars = empty placeholder for all three)
- Conviction calibration drift (Council Accuracy panel)
- Whether debates actually trigger (CONFLICT_MATRIX coverage)
- Capitulation rate per agent (Council Evolution, 30d)

If after 7 days `self_model` blocks are still empty across all three agents,
that's a real finding — they have no mechanism or motivation to update them
under current prompts. Decide then whether to add explicit self-reflection
prompting, run a separate daily reflection cycle, or accept self_model as a
static scratchpad.

### 2. Dashboard cleanup verification
Confirm post-cleanup state: no Latest MAGI Decision panel, no Supervisor
section, no Recent Decisions table, Inventory + Paper P&L in one row,
Recent Orders collapsed. Visible at api.ethobs.uk.

### 3. nginx basic auth — REQUIRED before live
Dashboard token still visible in page source. Only remaining security
blocker for live deployment.

## Medium-term

### 4. Asset selection — finalise
DOGE has the best historical grid PnL; XRP is current because grid dynamics
are more forgiving. Decide: switch primary, or run both. Per-asset spacing
already determined (XRP 1.5%, DOGE 2.5%, SOL 2.0%).

### 5. Live trading decision
After paper validation shows >50% accuracy AND positive P&L after fees over
a meaningful window. Requires:
- nginx basic auth (#3)
- Fresh Kraken API key (current keys return `EAPI:Invalid key`)
- Two-factor live gate verified (already implemented: env var + token file + paper flag)

### 6. Dual-operation concept (deferred)
Run a "Volume Engine" on a high-volatility asset (SOL candidate) alongside
the MAGI grid, sole purpose accumulating 30d Kraken volume to unlock lower
fee tiers. Not for profit. MAGI then benefits from reduced fees. Only worth
exploring after live trading is stable.

## On the horizon

### 7. self_model utilization
If self_model blocks remain unused after 7 days, options:
- Strengthen the "after responding, update your self_model if you notice a pattern" line in the cycle prompt
- Run a separate daily reflection cycle that prompts each agent to summarise the last 24h of outcomes

### 8. CONFLICT_MATRIX expansion
Current matrix catches obvious conflicts. Watch for cases where Round 0
consensus emerges but outcomes suggest a debate should have fired. Add
those patterns.

### 9. CHANGELOG.md
Long deferred. Re-evaluate post-Phase 5.

## Explicitly NOT on the roadmap
- Self-hosted Letta (decommissioned; do not revisit unless Cloud fails)
- Supervisor / override authority (rejected)
- Letta open-source thread-persistence experiments (May 4 deliberation prototype)
- ETH futures (dead)
- Adding new exchanges
- Scaling up paper dollar amounts (goal is validation, not money)
- Third-party Kraken wrappers (banned)
- Mem0, Graphiti, persistent-thread-only memory layers
