# MAGI XRP Grid Bot — Current State

**Last updated:** 2026-05-08

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
- **Allocation-based risk math (Balthasar).** As of 2026-05-06, Balthasar reasons about position concentration via `allocation_skew = (xrp_value - target) / total_universe_usd` where `target = total_universe_usd / 2` (50/50 USD/XRP neutral allocation). Range ±1; 0 = balanced, +1 = all XRP, -1 = all USD. Replaced the prior framing that used `MAX_INVENTORY_USD = $50` as denominator for `pct_deployed` and `inventory_skew`. The new framing measures actual position concentration relative to total capital under management, not deployment relative to a fixed budget. Balthasar's Part A thresholds: HALT at ±0.85, PAUSE_LONGS / PAUSE_SHORTS at ±0.6, CLEAR inside ±0.6.
- **Daily loss guardrail uses total-universe delta.** As of 2026-05-07, `check_daily_loss` in `guardrails.py` compares current total universe value (`xrp_value_usd + usd_held` from the latest inventory snapshot) against the value at start of UTC day (first inventory snapshot at or after midnight UTC). Trips when delta < -15% of midnight value. Replaces the prior cash-flow formula that summed buy/sell fills since midnight — that formula misread normal grid buy-into-dip behavior as catastrophic loss. New constant: `DAILY_LOSS_LIMIT_PCT = 0.15` in `config.py`. Old `DAILY_LOSS_LIMIT_USD = 10.0` retained but deprecated.
- **Balthasar buffer-floor rule.** Beyond skew thresholds, Balthasar now triggers PAUSE_LONGS when `usd_held < $10` (below one grid order size, can't place another buy) and PAUSE_SHORTS when `xrp_value_usd < $10` (can't place another sell). Captures operational exhaustion of one grid leg deterministically. Threshold rationale: order size at default `MAX_INVENTORY_USD = $50` / 5 buys is roughly $10 per level; below $10 buffer, the grid functionally cannot trade that side.
- **Melchior inventory_skew fully wired.** Fixed three-line bug across `magi/melchior.py` and `magi/orchestrator.py` where inventory dict was fetched but never passed to Melchior, and the prompt template looked it up from the wrong dict (indicators instead of inventory). Now Melchior sees the same allocation skew Balthasar sees. The field is conditionally included in Melchior's context only when `|skew| > 0.6` (concentrated state); within normal range it's omitted, since Melchior doesn't make decisions on inventory anyway. Range annotation added: `(range ±1; 0 = balanced 50/50, +1 = all XRP, -1 = all USD)`.
- **Directional concentration cap in place_order.** Replaces the prior size-based cap that compared `net_position_usd` against `MAX_INVENTORY_USD`. After the 2026-05-06 risk-math reframe, `net_position_usd` stores mark-to-market XRP value, not deployment — so the old cap tripped whenever `xrp_value > $50` regardless of direction, blocking sells that would reduce concentration. New behavior: read `allocation_skew` from the latest inventory row (stored in the legacy-named `inventory_skew` column), block buys when `skew > +0.90`, block sells when `skew < -0.90`. Otherwise allow. Threshold sits just above Balthasar's HALT at ±0.85 so the cap is a deterministic backstop, not a primary control. Rejected orders log `rejected_concentration`.
- **Casper regime detection (2026-05-07):** tightened to escalate biased chop (low ADX + bearish EMA stack + negative momentum) to TRENDING. Added current_price, autocorr_1h, autocorr_4h to Casper's context. EMA structure now co-primary with ADX.
- **Dashboard chart vertical scaling fixed (2026-05-08).** Added `rightPriceScale` config to Lightweight Charts v4.2.3 createChart() call in dashboard.py with `scaleMargins: {top: 0.2, bottom: 0.2}`. Chart no longer compresses vertically when price stays in a tight range.

- **Open order book context added to Melchior and Balthasar (2026-05-08).** New `get_open_orders_summary()` function in database.py queries open orders by side and price, plus fills from the last 24h. Melchior receives a compact "Order Ladder" block (buy count, highest bid, sell count, lowest ask, 24h fill count). Balthasar receives "Order Book Exposure" and "Recent Fills (last 24h)" blocks. Both agents now have visibility into the live order ladder when making decisions.

- **Historical base rates injected into all three agent prompts (2026-05-08).** Empirical forward-return statistics block appended to casper_prompt.txt, melchior_prompt.txt, and balthasar_prompt.txt. Derived from 46,300 hours of XRP/USD hourly data (Jan 2021 - May 2026). Block covers: bearish chop analog stats (647 bars, 67.4% 7d win rate), regime duration characteristics, BB-width and volume context, drawdown-before-recovery distribution, and broader bearish stack base rates. Static block — agents use as calibration, not prediction.

- **Paper inventory reset to balanced 50/50 state (2026-05-08).** Paper inventory was drifted to xrp=48.4, usd=$1.30 (nearly all XRP, no USD) due to accumulated buy fills with no USD to place new buys. Reset to xrp=24.83, usd=$35.00, skew=0.000 at price $1.4093. Grid rebuilt with 3 buys + 3 sells. System is now operating two-sided. Root cause: paper mode has no rebalancing mechanism — when USD is depleted, buy ladder cannot be funded. Resolution: one-time manual reset. Future fix: note in deferred tasks.

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
- DB columns `inventory.inventory_skew` and `inventory.net_position_usd` store different conceptual values post-2026-05-06 than they did pre-fix. The schema is unchanged for backward compatibility, but the values written are now `allocation_skew` and `xrp_value_usd` respectively. Old data (rows before 2026-05-06 ~17:58 UTC) used the prior formula and should be treated as not directly comparable to new rows.
- **The system has TWO services, not one.** `magi.service` runs `python3 -m scheduler` (handles startup cycles, scheduled cycles at 9 AM / 2 PM EST, observer polling). `magi-dashboard.service` runs `python3 -m dashboard` (handles all Flask API requests including `/api/trigger_magi` and dashboard page rendering). Both must be restarted after any code change in shared modules (`magi/*.py`, `database.py`, `config.py`, `grid/engine.py`, etc.). Restarting only `magi.service` after a code change leaves the dashboard process running stale code, which produces inconsistent behavior — scheduled cycles see new code, manual triggers see old code. Standard restart command: `systemctl restart magi.service magi-dashboard.service`.
- **Kraken account is bot-only.** Operator does not manually deposit, withdraw, or trade on Kraken outside the bot's activity. All inventory changes reflect bot fills. Daily loss guardrail's universe-delta metric is therefore a clean P&L signal — no manual capital movement noise to filter out.
- **Historical base-rate analysis completed (2026-05-08).** 46,300 hours of XRP/USD hourly OHLCV pulled from FMP API (Jan 2021 - May 2026, Starter plan). Dataset stored in operator's Google Colab/Drive as xrp_hourly.csv. Six analyses completed: regime transitions, current streak context, regime age vs forward return, BB-width clustering, volume profile, drawdown before recovery. Key findings:
  - Bearish chop regime (ADX≤20, EMA-50<EMA-200, price >5% below EMA-200, negative ROC-6h): 647 analog bars, 7d win rate 67.4%, mean +3.23%
  - Episodes short-lived: median 2h, max 13h. Resolves UP 52.9%.
  - Only early-stage bars exist (all <12h) — regime is inherently transient
  - Compressed BB-width (current): 7d win rate 64.1% vs 70.7% expanded
  - High volume bearish chop: 7d win rate 69.8% vs 65.0% low volume
  - 7d winners: median max drawdown before recovery -3.12%; 51.8% drew down >3% first; only 21.1% went up without >1% dip
  - Broader bearish stack (EMA-50<EMA-200 only): 7d win rate 50.8% — edge exists only in tight-filter regime
  - Refresh dataset quarterly or when regime changes materially
- **FMP API surveyed (2026-05-08).** Starter plan confirmed, 5-year historical limit. Subscription ended after session. Worth revisiting if re-subscribed:
  - `search-crypto-news` (XRPUSD): best candidate for Balthasar tail-risk context, 5 headlines per call
  - `treasury-rates`: daily 10y yield, useful as monthly macro signal
  - `economics-indicators` (CPI/GDP): monthly, low priority
  - Technical indicators endpoint: not worth it, we compute ourselves
  - EOD historical: not worth it, hourly is strictly better

- **CDN caching clarification (2026-05-08).** Cache-busting query strings (?bust=...) do NOT work on raw.githubusercontent.com — Fastly ignores query strings entirely. TTL is 5 minutes. After magi-sync, wait 5+ minutes before opening a new Claude session. Only reliable fixes: (a) wait for TTL or (b) paste content directly from droplet via cat. 03_INSTRUCTIONS_TO_CLAUDE.md updated to reflect this.

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

Late afternoon 2026-05-06: Balthasar's risk math was reframed. The prior calibration used `MAX_INVENTORY_USD = $50` as denominator for `pct_deployed`, `inventory_skew`, and `drawdown_pct`. With paper inventory mirroring the actual Kraken account (~$70 universe), this denominator was smaller than the typical XRP-leg value, causing `pct_deployed` to read 78–98% under normal grid conditions and triggering spurious PAUSE_LONGS decisions. The fix replaces the math with `allocation_skew` (deviation from 50/50 neutral, normalized to total universe) and removes drawdown from Balthasar's context entirely (the daily loss limit guardrail still handles absolute drawdown deterministically). Verified live: same inventory state that produced 0.978 skew under old formula now reads +0.200, and Balthasar correctly returns CLEAR.

## Most recent significant event — 2026-05-07

Three correctness fixes plus discovery of two-service architecture. The session started with the daily loss guardrail miscalibrated (formula summed cash flow since midnight UTC, treated grid buy-into-dip as catastrophic loss; replaced with total-universe delta against UTC midnight baseline, 15% threshold). Balthasar got a buffer-floor rule for the case where allocation skew is moderate but one leg is operationally exhausted (less than $10 in either USD or XRP value, can't fund another grid order on that side). Melchior had a three-line bug where inventory was never passed to its context-builder; fixed, plus added range annotation and a conditional-include rule for the skew field.

During Melchior diagnosis, discovered the system runs two systemd services — `magi.service` (scheduler) and `magi-dashboard.service` (Flask) — and that prior restart procedures only restarted the first. The dashboard process had been running stale code since May 5, which masked which fixes had actually propagated to the manual-trigger path. All five manual cycles confabulated ("inventory_skew data is missing") while startup cycles were clean — the split between scheduled and manual behavior was the diagnostic signal. Restarting both services (`systemctl restart magi.service magi-dashboard.service`) made every fix from this session actually live in both paths. Verified: 0/5 manual cycles confabulate after the dual restart, all return `medium` conviction with reasoning grounded in actual indicators.

Late afternoon 2026-05-07: discovered a latent bug from the May 6 reframe. The position-size cap in `place_order` was retained at `net_position_usd >= MAX_INVENTORY_USD` ($50), but the meaning of `net_position_usd` had silently changed from "USD deployed" to "mark-to-market XRP value." With 48 XRP × ~$1.40 = $67, the cap tripped on every order placement after the 18:00 UTC WIDEN cycle, including sells. The grid sat at zero open orders for several hours until the bug was identified and the cap was reframed to use `allocation_skew` directionally. No real money exposed — paper mode throughout. Fix: replaced size cap with concentration cap at ±0.90 threshold, reading `allocation_skew` from inventory.

- 18:45 UTC. Casper regime fix landed. Three new context fields (current_price, autocorr_1h, autocorr_4h) plus prompt rewrite establishing EMA structure as co-primary with ADX. New "biased chop detection" rule escalates to TRENDING when structure, directional pressure, and momentum all agree on a direction even at low ADX. Addresses today's failure mode where Casper called RANGING high conviction while EMA-50 sat 20% below EMA-200 and the grid bled into a downward drift.
