# MAGI XRP Grid Bot — Current State

**Last updated:** 2026-05-14

---

## System Status: DEGRADED — Grid idle 46+ hours

The live paper grid has been in a self-reinforcing deadlock since 2026-05-13T13:01. The system is running but not trading. Root cause and fix plan documented below.

---

## What Is Working

- **Three MAGI agents** calling APIs directly — Melchior (gpt-4o), Balthasar (claude-sonnet-4-6), Casper (gemini-2.5-flash). All 186 decisions marked applied=1. Reasoning is coherent.
- **Hourly observer cycles.** Candle fetch, indicator computation, market_knowledge recompute all running cleanly. 70,087+ 1h candles in DB.
- **Paper fill engine.** Correctly rejects fills that would push inventory negative. Fill simulation logic is sound.
- **Order replenishment after fill.** Replacement orders placed correctly after each fill. (They get cancelled by PAUSE_LONGS on next cycle — that's the bug, not the replenishment logic.)
- **Guardrails.** Kill switch and daily loss limit wired correctly. Neither has tripped unexpectedly.
- **Dashboard** at `https://api.ethobs.uk`. Both services running: `magi.service` + `magi-dashboard.service`.
- **Token cost tracking.** 343 rows in token_usage. Lifetime cost ~$1.97. Within budget.
- **Spacing cap enforcement.** WIDEN decisions now clamped to `MAX_GRID_SPACING_PCT = 2.5%` (deployed 2026-05-14).
- **Shadow sim resting_orders.** Fixed 2026-05-14 — all 6 variants now have populated resting_orders on startup. Will accumulate fills on next live fill event.

---

## What Is Broken / Has Never Worked

### CRITICAL — Grid deadlock (active since 2026-05-13)

**Mechanism:**
1. PAUSE_LONGS cancels all open buy orders
2. MAINTAIN means no grid rebuild
3. TRENDING veto blocks RECENTRE (the natural recovery)
4. No buys → no fills → skew never corrects → PAUSE_LONGS never clears

**Current state:** 1 sell order open at $1.4733. XRP price ~$1.47. Grid centre at $1.40 (5% below price). Zero buy orders. 46+ hours since last fill.

**Root cause (Claude Code assessment):** Orchestration layer lacks dead-grid detection. MAINTAIN when open_buys == 0 is not maintaining anything. The TRENDING veto scope is too broad — RECENTRE is regime-neutral and should not be blocked. Balthasar's PAUSE_LONGS threshold fires at mild skew (-0.20) that has no protective value when zero buy orders exist.

**Fix plan:** See 02_NEXT_BUILD_TASKS.md Path 1 fixes. Must deploy before system self-recovers.

### Shadow level switching — never fired

All 6 shadow variants show fill_count=0 since launch. Shadow buy levels (~$1.36 and below) have never been touched by price (XRP low since launch: $1.377). Level switching cannot fire without fills. Lower priority than core trading loop.

### pnl_daily table — never written

Table exists, schema correct, zero rows. No writer exists anywhere in the codebase. Daily PnL reporting is dark. Fix is a one-line addition to observer.

### Stale spacing in DB

`grid_state` shows `spacing_pct = 0.02675` — above the 0.025 hard ceiling. The clamp fix (2026-05-14) applies to future WIDENs only. This stale value will correct on next WIDEN + rebuild cycle.

---

## Current Inventory (as of 2026-05-14)

- XRP held: 13.87 (~$20.40 at current price)
- USD held: $48.48
- Total universe: ~$68.88
- Allocation skew: -0.204 (mild USD-heavy lean — within normal range)

---

## MAGI Decision Distribution (186 total, since 2026-05-03)

| Action | Count | % |
|--------|-------|---|
| MAINTAIN | 133 | 71% |
| WIDEN | 39 | 21% |
| TIGHTEN | 11 | 6% |
| RECENTRE | 1 | 0.5% |
| HALT | 2 | 1% |

| Risk Action | Count |
|-------------|-------|
| PAUSE_LONGS | 92 |
| CLEAR | 89 |
| PAUSE_SHORTS | 3 |
| HALT | 2 |

MAINTAIN at 71% with RECENTRE at 0.5% reflects the TRENDING veto being too aggressive. This is the architectural problem, not a calibration problem.

---

## Fill History

18 total fills since 2026-05-04. Last fill: 2026-05-12T19:05. Distribution:

| Date | Fills |
|------|-------|
| 2026-05-12 | 1 |
| 2026-05-11 | 4 |
| 2026-05-08 | 2 |
| 2026-05-07 | 5 |
| 2026-05-06 | 4 |
| 2026-05-04 | 2 |

9 matched round trips. Win rate: 77.8%. Avg P&L per trip: $0.019. Realized P&L (net of fees): $0.1683. Fees paid: $0.6052.

Note: Fee rates in config.py (`MAKER_FEE=0.004`, `TAKER_FEE=0.006`) are slightly higher than actual Kraken rates (0.25%/0.40%). P&L figures are conservatively pessimistic. Correct before live.

---

## Architectural Assessment (May 2026)

End-to-end function: ~60% by code path, ~25% by intended trading behavior.

The system optimizes for **not losing** rather than **trading profitably**. Each safety component is individually correct. No component asks whether the collective output serves the trading objective. This is the core architectural gap — addressed by the Supervisor layer proposal (see 00_PROJECT_OVERVIEW.md §Roadmap and the project proposal PDF).

---

## Verified Facts (do not re-research)

- Operator's Kraken tier: Standard verification ("Verified") — confirmed
- Kraken fees: 0.25% maker / 0.40% taker at tier 0 — confirmed
- XRP/USD pair: `pair_decimals=5`, `lot_decimals=8`, `ordermin=1.65 XRP`, `costmin=0.50 USD` — confirmed
- `CancelOrderBatch` requires minimum 2 orders — confirmed empirically
- Kraken OHLC valid intervals: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600. 360 not valid — SIX_HOUR resampled from 1H
- `OpenOrders` returns `descr.pair == "XRPUSD"` (alt name, not canonical `XXRPZUSD`)
- Two services: `magi.service` (scheduler) and `magi-dashboard.service` (Flask). Always restart both after code changes
- Kraken account is bot-only. No manual deposits, withdrawals, or trades outside bot activity
- `GetAPIKeyInfo` endpoint no longer exists on Kraken REST API (HTTP 404)
- Spot trading: physically impossible to sell more XRP than held
- DB timestamp convention: always use `datetime.utcnow().isoformat()` — never `datetime('now')` in raw SQL
- Coinbase One and Kraken+ subscriptions do not apply to API trading — confirmed
- Fee rates before 2026-05-10 were 2-2.5x too high in config. Pre-May-10 P&L magnitude is inaccurate (direction correct)

---

## Experimental Findings (May 2026)

### Deliberation experiment (2026-05-12/13)

Ran structured 3-round deliberation (Casper challenges → Melchior defends → Balthasar adjudicates) on 29 historical MAINTAIN vs TRENDING conflicts and 2 RECENTRE vs TRENDING conflicts.

**Result:** 0/29 valid decision changes on MAINTAIN cases (1 apparent change was tainted by Casper API error). 2/2 RECENTRE cases changed — but to "TRENDING" (an invalid grid action, prompt artifact).

**Conclusion:** Stateless non-interacting council design is correct. Deliberation produces circular reinforcement loops where capitulation is indistinguishable from genuine persuasion.

### Bias audit (2026-05-13)

Ran bias audit on deliberation transcripts using claude-sonnet-4-6 as impartial auditor.

**Findings:** Capitulation: MEDIUM-HIGH. Sycophantic language: HIGH (Case 177). Last-speaker bias: MEDIUM. Overall: MEDIUM-HIGH.

**Key finding:** "Both decisions landed on the right answer, but the process would produce the same structural output even if Casper's argument were wrong — which is the core systemic risk."

**Conclusion:** The stateless design avoids this failure mode entirely. Councils cannot capitulate to each other because they never see each other's reasoning before voting. Documented in `learning_log.md`.

---

## Kraken API Operational Details

- Base URL: `https://api.kraken.com`
- Private: POST `/0/private/{Endpoint}`, `application/x-www-form-urlencoded`
- Public: GET `/0/public/{Endpoint}`
- Auth: HMAC-SHA512(urlpath + SHA256(nonce + postdata))
- nonce: `time.time_ns()`
- Rate limit: max=125, decay=2.34/sec
- Open orders cap: 80 per pair

---

## Database Tables

| Table | Purpose |
|-------|---------|
| `candles` | OHLCV candles (1h and 1d timeframes) |
| `indicators` | 18 computed technical indicators per cycle |
| `grid_state` | Audit trail of grid configuration changes |
| `grid_orders` | Paper orders: open → filled or cancelled |
| `inventory` | Paper XRP/USD holdings snapshots |
| `magi_decisions` | Full record of every MAGI cycle |
| `pnl_daily` | Daily P&L (schema exists, no writer — known gap) |
| `token_usage` | Per-call token and cost logging |
| `shadow_grid_state` | State for all 6 shadow simulator variants |
