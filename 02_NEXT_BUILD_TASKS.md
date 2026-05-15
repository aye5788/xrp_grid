# Next Build Tasks

**Last updated:** 2026-05-14

See `01_CURRENT_STATE.md` for full system state. See `00_PROJECT_OVERVIEW.md` §Roadmap for the Supervisor layer design. See the project proposal PDF for the full architectural rationale.

---

## Current Priority — Path 1: Recalibration

The system has been in a trading deadlock since 2026-05-13. The grid is dead. Path 1 fixes must deploy before anything else. Do not add features until the core trading loop is working.

### Fix 1A — Remove RECENTRE from TRENDING veto (HIGH PRIORITY)

**File:** `magi/magi_orchestrator.py`

**Problem:** `apply_consensus()` currently blocks RECENTRE when Casper says TRENDING. RECENTRE is regime-neutral — it resets grid position without adding directional exposure. Only TIGHTEN should be blocked during TRENDING (increases fill rate into a directional move). This one-line change immediately unblocks the natural recovery path.

**Change:** In the TRENDING branch of `apply_consensus()`, allow RECENTRE to pass through alongside WIDEN. Only default to MAINTAIN for other actions.

**Verification:** Trigger a manual MAGI cycle after deploy. If Melchior recommends RECENTRE and Casper says TRENDING, the system should execute RECENTRE instead of MAINTAIN.

### Fix 1B — Dead-grid override rule (HIGH PRIORITY)

**File:** `magi/magi_orchestrator.py`

**Problem:** No mechanism detects or escapes the state where PAUSE_LONGS + zero open buys + extended no-fill period combine. PAUSE_LONGS on a grid with zero buy orders has zero protective value — there is nothing to pause.

**Change:** After computing `grid_action` and `risk_action`, add deterministic check:
```python
if (risk_action == 'PAUSE_LONGS'
        and open_buy_count == 0
        and hours_since_last_fill > 12):
    grid_action = 'RECENTRE'
    risk_action = 'CLEAR'
    notes += " [DEAD_GRID_OVERRIDE]"
```
`open_buy_count` and `hours_since_last_fill` must be derived from DB in `apply_consensus()` context. Read from `grid_orders` and `magi_decisions` tables.

**Verification:** After deploy, confirm next cycle exits deadlock. Check grid_state for `[DEAD_GRID_OVERRIDE]` note.

### Fix 1C — Inject grid health into all agent contexts (MEDIUM PRIORITY)

**Files:** `scheduler.py`, `magi/magi_melchior.py`, `magi/magi_balthasar.py`, `magi/magi_casper.py`

**Problem:** Agents receive `fills_since_last_magi` (resets each cycle) but not cumulative fill drought or open order count. Balthasar cannot reason about whether PAUSE_LONGS is meaningful without knowing there are zero buy orders.

**Change:** Add two fields to every agent's context block:
- `open_buy_count`: current count of open buy orders from `grid_orders WHERE status='open' AND side='buy'`
- `hours_since_last_fill`: hours since most recent filled order

Update each agent's prompt template to reference these fields explicitly.

**Verification:** Trigger a manual cycle. Check that agent reasoning references `open_buy_count` or `hours_since_last_fill` in its output.

### Fix 1D — Raise PAUSE_LONGS threshold in Balthasar (MEDIUM PRIORITY)

**File:** `magi/magi_balthasar.py`

**Problem:** Balthasar fires PAUSE_LONGS at allocation_skew of -0.204 (mild USD-heavy lean) even when zero buy orders exist. The current prompt does not instruct Balthasar to consider whether PAUSE_LONGS has any protective value given the current order book.

**Change:** Update Balthasar's prompt to include explicit instruction: "If open_buy_count == 0, PAUSE_LONGS has no protective value — there are no buy orders to pause. In this state, return CLEAR unless skew exceeds ±0.6 or a buffer floor is breached."

Do not change the ±0.6 PAUSE_LONGS threshold or the ±0.85 HALT threshold. Only change behavior when open_buy_count == 0.

**Verification:** With zero buy orders, Balthasar should return CLEAR at mild skew levels.

### Fix 1E — Wire pnl_daily writer (LOW PRIORITY)

**Files:** `observer.py`, `database.py`

**Problem:** `pnl_daily` table has existed since launch with zero rows. No writer exists. Daily PnL reporting is dark.

**Change:** Add `insert_pnl_daily()` to `database.py`. Call it from `observer.poll_cycle()` at UTC midnight (date change detection). Compute from `grid_orders` fills for that date using existing FIFO logic in `grid/pnl.py`.

**Verification:** After one UTC midnight passes, query `SELECT * FROM pnl_daily ORDER BY date DESC LIMIT 3` and confirm rows exist.

---

## Path 2: Supervisor Layer (After Path 1 Stabilizes)

Build Supervisor v1 in parallel with Path 1 fixes. Deploy in shadow mode immediately — it logs what it would have done without affecting grid behavior. No clean baseline required. Activate with override authority after 7 days of shadow decisions if directionally sound. The Supervisor has a hard constraint: it can only override toward productive actions. A bad override produces a misplaced RECENTRE — recoverable. Weeks of waiting produces nothing.

### Supervisor v1 — Stateless with observer.db context

**File:** `magi/magi_supervisor.py` (new)

**Design:** Single stateless LLM call (claude-sonnet-4-6) that receives:
- All three council outputs
- Grid health context from observer.db (open_buy_count, hours_since_last_fill, fills_last_7d, drift_pct)
- Recent decision history (last 5 decisions + outcomes)
- Explicit mandate: "Is the council's recommendation serving profitable trading?"

**Outputs:** APPROVE (council recommendation stands) or OVERRIDE [RECENTRE / WIDEN / CLEAR_PAUSE] with one-sentence reasoning.

**Override constraints:** May only override toward productive actions. Cannot override toward HALT, TIGHTEN, or greater conservatism.

**Pushback mechanism:** For each council output that is circular or disconnected from grid reality, send back prior output + specific challenge. Agent responds with its own output in context — statelessness is not a barrier. Maximum 2 rounds per agent per cycle.

### Supervisor v2 — Persistent memory via Mem0

**After Supervisor v1 has run for 14+ days with 20+ override decisions:**

Add Mem0 (bolt-on memory layer, Python SDK) to Supervisor only. Three things to remember:
1. Override outcome history (POSITIVE / NEGATIVE / NEUTRAL, written 6h after each override)
2. Approval outcome history (when council recommendation was approved, what happened)
3. Learned patterns ("PAUSE_LONGS with zero buys consistently produces continued deadlock")

Councils remain stateless. Only the Supervisor accumulates memory.

---

## Deferred (Do Not Build Yet)

### Weekly synthesis / learning loop
Do not build until 2-3 months of clean live trading data exists. Synthesizing from paper data or broken-period data risks encoding artifacts as market wisdom.

### Stop-loss on entire grid
Required before live trading. Not urgent during paper validation.

### Two-factor paper→live confirmation
Required before any live flip. Not urgent until thesis is validated.

### Email/SMS alerts on HALT
Important for live. Nice-to-have for paper.

### Exchange downtime detection
Required for live. Acceptable behavior for paper.

---

## Operator Preferences (Hard Rules)

- **No live trading until:** system making sensible decisions, Supervisor catching genuine problems, realized P&L positive after fees over available sample, failure modes understood well enough to trust with real money. This is a judgment call made when there is enough information — not after an arbitrary waiting period.
- **No Letta or agent runtimes.** Rejected. Mem0 is the correct memory layer choice.
- **No krakenex or third-party Kraken wrappers.** Direct REST + stdlib only.
- **No third-party historical data wrappers.** Kraken CSV archive is the source.
- **No features the operator didn't ask for.** Do not add scope mid-task.
- **Full files always.** Every code change delivered as complete file ready to copy-paste. No snippets.
- **Read before write.** Before changing any file touching live state, read the current version first.
- **Verify before declaring done.** Code changes require a log line or DB query confirming the new path executed.
- **Restart both services after every code change:** `systemctl restart magi.service magi-dashboard.service`
- **Flag discrepancies.** If docs conflict with code, flag it — don't silently propagate the error.
- **No commits without operator permission.** Operator runs `magi-sync` manually.
