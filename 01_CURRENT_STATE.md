# MAGI XRP Grid Bot — Current State

**Last updated:** 2026-05-06

---

## Working

- **Three stateless MAGI agents** calling OpenAI (Melchior/gpt-4o), Anthropic (Balthasar/claude-sonnet-4-6), and Google (Casper/gemini-2.5-flash) directly via their respective REST APIs.
- **Token usage logged per agent per call** to the `token_usage` table. Estimated cost in USD computed at log time and surfaced on the dashboard.
- **Hourly observer cycle** pulling Kraken XRP/USD candles (ONE_HOUR, SIX_HOUR via Python resampling, ONE_DAY) plus Coinbase BTC/USD daily candles for Casper's market context signal.
- **Scheduled MAGI cycles at 9 AM and 2 PM EST.** Manual trigger available via dashboard button (`POST /api/trigger_magi`) and via `magi/orchestrator.py --force`.
- **Adaptive shadow grid simulator** with 6 variants (level_count ∈ {6, 8, 10, 12, 14, 16}). Each variant tracks fills and rolling P&L% on every price tick. Automatic level-count switching when a better variant clears all four gates (margin, fills, time window, not already current).
- **Paper trade persistence to grid_orders DB.** Every paper order placed writes a row with `status='open'`. Fill simulation updates rows to `status='filled'` with fill price and fee.
- **Paper state (open orders + inventory) persists across service restarts.** `engine.load_state()` restores `paper_orders` from `grid_orders WHERE status='open'` and `paper_inventory` from the most recent inventory row. Scheduler skips `initialise_grid()` if orders were restored.
- **Indicator computation always emits every key, with NULL when computation fails.** All 18 indicator keys are initialized to None at the start of `compute_indicators()`. Each `except` block resets its own keys to None. The `upsert_indicators()` upsert writes every key, so stale values cannot persist across cycles.
- **Budget-aware Balthasar.** Balthasar receives explicit budget context: `capital_deployed_usd` (USD value of XRP held), `capital_free_usd`, `total_value_usd` (mark-to-market), `drawdown_pct`, and `pct_deployed` (% of ring-fenced budget in XRP positions), alongside the existing `inventory_skew`.
- **Exchange abstraction.** `BaseExchange` in `grid/exchanges/base.py` defines the interface. `CoinbaseExchange` and `KrakenExchange` are concrete implementations. `EXCHANGE` flag in `config.py` controls which is active. `GridEngine` and `observer.get_candles_xrp()` both read this flag at runtime.
- **Fund detection at Kraken startup.** On service start, the scheduler verifies that XXRP + ZUSD value on Kraken ≥ `MAX_INVENTORY_USD` ($50). Refuses to operate if funds are insufficient.
- **P&L engine** with FIFO-matched realized P&L, unrealized P&L (mark-to-market), total, win rate, and fees. Results feed the dashboard at `https://api.ethobs.uk`.
- **Dashboard** at `https://api.ethobs.uk` with Trigger MAGI Cycle button, fill/order tables, shadow variant comparison, cost tracking, and agent decision log.
- **Guardrails:** kill switch (`/root/xrp_grid/HALT` file), daily loss limit (`-$10 USD`), paper mode enforcement. All guardrails run before every MAGI cycle; failure cancels all orders and blocks the cycle.
- **Daily summary generation** appended to `learning_log.md` via `magi/learning.py` (manual trigger only — not yet automated at 5 PM EST).
- **Spot-realistic paper simulation.** Grid placement and fill simulation respect actual inventory. `build_grid_levels` trims the sell ladder to what current XRP holdings can cover and the buy ladder to what current USD can cover. `simulate_fills` rejects any fill that would push a balance negative — the rejected order stays open, inventory is unchanged, and a `[PAPER REJECT]` warning is logged. Impossible (negative) inventory states cannot occur.
- **Auto-rebase on impossible inventory state.** If `load_state` restores a negative XRP or USD balance from the DB (legacy state from the pre-fix simulator), it queries `exchange.get_balances()` live from Kraken, overwrites `paper_inventory` with those real balances, and immediately persists the corrected row to the `inventory` table. This is a defensive guard — it self-corrects on next startup if a future bug ever produces invalid state again.
- **Asymmetric grid placement.** When XRP holdings can't cover the full sell ladder, the grid places fewer sells than buys and continues. The asymmetry is logged: `Grid asymmetric — N buys + M sells (XRP held insufficient for full sell ladder)`. A fully empty grid (zero buys AND zero sells coverable) logs a warning and returns `False` from `initialise_grid`.

---

## Currently executing on — Kraken paper

The exchange is Kraken Spot REST API, paper mode (`engine.paper = True`). No real orders are placed.

- **Auth:** HMAC-SHA512, hand-rolled in `grid/exchanges/kraken.py`. No third-party Kraken SDK. Credentials loaded via `config.py` → `load_dotenv()` (not via `os.getenv()` directly in kraken.py — the systemd-compatible pattern).
- **Canonical pair name:** `XXRPZUSD`. Engine passes `"XRP-USD"` (Coinbase format), `KrakenExchange` maps internally.
- **Operator's bot universe:** XXRP and ZUSD on Kraken only. Other Kraken assets are ring-fenced out — the bot queries only `XXRP` and `ZUSD` balances.
- **Paper inventory baseline (as of 2026-05-06 rebase):** xrp=27.4769, usd=$30.98, total ~$70 mark-to-market at ~$1.43. This mirrors the actual Kraken account balances at the time of the simulator fix. Paper inventory moves from this baseline as fills accumulate. Update this line if the numbers shift materially in future doc revisions.
- **Fees:** Standard verification tier — 0.25% maker / 0.40% taker. All orders use `oflags=post` (post-only), guaranteeing maker rate.
- **Note on config fee constants:** `config.py` has `MAKER_FEE = 0.004` (0.4%) and `TAKER_FEE = 0.006` (0.6%). These are slightly higher than Kraken's actual rates. P&L calculations are therefore conservatively pessimistic by ~0.15% per trade on maker fees. Safe-side error; no code change required for paper validation but should be corrected before live.
- **Trading rate counter:** max=125, decay=2.34/sec. Each `AddOrder` costs +1. Cancel cost depends on order age (0–8 points). Counter state is in-memory only — resets on service restart.
- **BTC candles stay on Coinbase** regardless of `EXCHANGE` flag. Coinbase public candle endpoint is unauthenticated and always available for this purpose.

---

## Verified facts (do not re-research)

- Operator's Kraken tier: Standard verification ("Verified") — confirmed.
- Kraken fees for XXRPZUSD: 0.25% maker / 0.40% taker at tier 0 — confirmed against public AssetPairs.
- XRP/USD pair details: `pair_decimals=5`, `lot_decimals=8`, `ordermin=1.65 XRP`, `costmin=0.50 USD`, `tick_size=0.00001` — confirmed against public AssetPairs.
- `userref` must be positive int32 (1 to 2,147,483,647). Engine derives it from `MD5(client_order_id)[:8] & 0x7FFFFFFF` — confirmed against Kraken API documentation.
- `CancelOrderBatch` requires minimum 2 orders — confirmed empirically. Engine routes single-order cancels to `CancelOrder` instead.
- `CancelOrderBatch` accepts JSON body with `orders` field as array of bare txid strings, signed with JSON-aware signing (body as JSON string instead of urlencoded) — confirmed empirically.
- Kraken OHLC valid intervals: 1, 5, 15, 30, 60, 240, 1440, 10080, 21600 minutes. 360 is not valid — that is why `SIX_HOUR` is resampled from 1H bars in `KrakenExchange.get_candles`.
- `OpenOrders` returns `descr.pair == "XRPUSD"` (alt name, not canonical `XXRPZUSD`) — confirmed empirically. `cancel_all_open_orders()` filters on this alt name.
- `KRAKEN_API_KEY` and `KRAKEN_API_SECRET` are read by `config.py` via `load_dotenv()`. `KrakenExchange.__init__` imports from `config`. Direct `os.getenv()` in `kraken.py` was an earlier pattern that failed under systemd's clean environment — that pattern has been fixed.
- `GetAPIKeyInfo` endpoint is no longer on Kraken REST API (returns HTTP 404 / EGeneral:Unknown method). Account tier must be verified via Kraken web console.
- Spot trading on Kraken has no margin: it is physically impossible to sell more XRP than currently held. Confirmed by exchange spec; confirmed by `simulate_fills` reject logic in `grid/engine.py`. The pre-fix simulator allowed negative XRP by blindly applying fills — that was a bug, now fixed as of 2026-05-06.
- Paper inventory `xrp >= 0` and `usd >= 0` invariant is enforced at two points: (1) `simulate_fills` checks balance before applying any fill and rejects with `continue` if insufficient, and (2) `load_state` detects negative values in the DB snapshot and auto-rebases from live Kraken via `exchange.get_balances()`.

---

## Kraken API operational details

- Base URL: `https://api.kraken.com`
- Private endpoint path: `/0/private/{Endpoint}` — POST, `application/x-www-form-urlencoded` (or JSON for `CancelOrderBatch`)
- Public endpoint path: `/0/public/{Endpoint}` — GET
- Auth headers: `API-Key`, `API-Sign`, `Content-Type`
- Signature: HMAC-SHA512(urlpath + SHA256(nonce + postdata))
- `nonce` is nanoseconds since epoch (`time.time_ns()`)
- Rate limit (Standard verification): max=125, decay=2.34/sec
- Account management rate limit: max=20, decay=0.5/sec
- Open orders cap: 80 per pair
- Cancel cost by age: <5s=8, <15s=5, <45s=4, <90s=2, <300s=1, ≥300s=0
- Post-only rejection: 1 (placement) + 8 (implicit cancel) = 9 rate points
- Open orders response: pair name in `descr.pair` uses alt name (`XRPUSD`), not canonical (`XXRPZUSD`)

---

## Database tables

| Table | Purpose |
|-------|---------|
| `candles` | OHLCV candles (1h and 1d timeframes), indexed by `(timestamp, timeframe)` |
| `indicators` | Computed technical indicators per timestamp/timeframe. `upsert_indicators` writes all 18 keys per cycle. |
| `grid_state` | Audit trail of grid configuration changes (centre, spacing, levels, flags) |
| `grid_orders` | Every paper order with status lifecycle: `open` → `filled` or `cancelled`. Restored on restart. |
| `inventory` | Snapshot of paper XRP/USD holdings after each fill batch. Latest row restored on restart. |
| `magi_decisions` | Full record of every MAGI cycle: all three agent outputs + consensus |
| `pnl_daily` | Daily aggregated P&L summary |
| `token_usage` | Per-call token and cost logging for all three agents |
| `shadow_grid_state` | Persisted state for all 6 shadow simulator variants |

---

## Known fragility — text timestamp format

`ORDER BY timestamp DESC LIMIT 1` queries are sensitive to the text format stored in timestamp columns. Two formats have appeared in the same columns during this project:

- SQLite `datetime('now')` produces `YYYY-MM-DD HH:MM:SS` (space separator, ASCII 32)
- Python `datetime.utcnow().isoformat()` produces `YYYY-MM-DDTHH:MM:SS.ffffff` (T separator, ASCII 84)

Because ASCII 84 > ASCII 32, Python-generated timestamps lexicographically sort *later* than same-wall-clock SQLite-generated timestamps. When both formats appear in a column, `ORDER BY timestamp DESC` returns the Python-format row as "most recent" even if it was inserted earlier in wall time.

**Convention:** always use `datetime.utcnow().isoformat()` (Python) for all inserts, including one-off SQL migrations. Never use `datetime('now')` in raw SQLite statements to insert timestamps into columns that Python also writes to. If a one-off SQL insert is needed, use `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` to match the T-separator format, or run the insert via Python instead.

This is a documented gotcha, not a bug to fix. The existing data is consistent (Python-format throughout) following the cleanup done during Kraken cutover.

---

## Known fragility — shadow simulator allows impossible fills

The shadow simulator (`grid/shadow_simulator.py`) has the same impossible-fill bug pattern that was fixed in the live engine on 2026-05-06. The shadow sim's internal accounting accepts fills regardless of its own simulated inventory — it operates on an idealized model where XRP supply is unlimited.

This means shadow rolling P&L percentages reflect a world where every hypothetical sell always executes regardless of holdings. They tell you "this level count would have done relatively better or worse than others" (directional signal), but NOT "this is the actual P&L this variant would have produced on a real spot account."

The shadow sim does not affect live grid behavior or order placement — it only feeds the level-count switching gates. The switching gates already require 20+ fills and 24h of history, which partially limits the damage from idealized accounting. But the P&L percentages should be treated as relative comparison signals only.

Fix is deferred. The priority is validating the corrected live simulator first. Revisit after 1-2 weeks of real data accumulates.

---

## What's NOT done

- **Shadow simulator spot fix.** Same impossible-fill bug pattern as the live engine had. Lower priority because the shadow sim only drives level-count switching, not order placement. But shadow P&L numbers are currently idealized rather than spot-realistic. Fix when convenient after the live simulator has been validated.
- **Risk wake mechanism.** A threshold-triggered MAGI cycle when inventory skew breaches a danger level (e.g., |skew| > 0.8), independent of the scheduled 9 AM / 2 PM windows. Design deferred pending observation of the corrected simulator's behavior. The original motivation was a -1.025 skew event that turned out to be caused by the simulator bug rather than a real risk pattern. We don't yet have evidence that scheduled cycles are too slow for genuinely-possible spot risk events — wait for real data before designing this.
- **Stop-loss on entire grid:** cancel all orders and halt if XRP drops X% from grid centre. Not urgent in paper; required before going live.
- **Two-factor paper→live confirmation:** explicit second confirmation required when flipping `paper=False`. Not urgent until thesis is validated; required before any live flip.
- **Email or SMS alerts on HALT events:** operational observability when Balthasar or guardrails fire a halt. Nice-to-have for paper; important for live.
- **Exchange downtime detection:** currently the system will retry-loop on Kraken connectivity failures rather than pausing cleanly. Nice-to-have for paper (the retry behavior is safe if ugly); required for live.
- **Backtest framework:** validate strategy parameter changes against historical XRP data rather than waiting for live cycles. Most valuable if shadow simulation doesn't generate enough signal over the first 2-week observation period.

---

## Most recent significant event — 2026-05-06

The paper simulator had a fundamental bug from the start: `simulate_fills` applied fills regardless of inventory balance, allowing negative XRP positions that don't exist on a non-margin spot exchange. Overnight on May 5–6, XRP rallied and 5 successive sell fills triggered against a near-zero long position, driving the paper inventory to xrp=-35.35 with usd=$150.72 — physically impossible on Kraken spot. The agents saw this broken state and responded correctly: Balthasar returned HALT at the 9 AM EST scheduled cycle (13:00 UTC), which was the right call given the reported skew of -1.025.

The fix landed mid-day. `build_grid_levels` now trims the sell ladder to what current XRP holdings can cover (and the buy ladder to what USD can cover). `simulate_fills` checks inventory before applying any fill and rejects with a `[PAPER REJECT]` log line if the balance is insufficient — the order stays open, no state changes. `load_state` detects impossible (negative) inventory on startup and auto-rebases from the live Kraken account via `exchange.get_balances()`.

On restart the rebase fired immediately: detected xrp=-35.35, queried Kraken, reset to xrp=27.4769 / usd=$30.98. The system resumed at 13:20 UTC with a spot-realistic baseline. The HALT kill switch was cleared as part of the migration.

The fill and inventory data from May 5 evening through May 6 ~13:20 UTC reflects the broken simulator and should be treated as diagnostic data only, not signal. True spot-realistic paper trading begins from 2026-05-06 13:20 UTC.
