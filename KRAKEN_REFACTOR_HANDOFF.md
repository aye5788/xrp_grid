# Kraken Refactor Handoff
Generated: 2026-05-05T09:10:30Z

## Auth confirmed
- Golden test vector: PASS
- Live Balance call: FAIL — `EAPI:Invalid key` (key rejected by Kraken; see note below)
- Endpoint base: https://api.kraken.com
- Auth scheme: HMAC-SHA512(path + SHA256(nonce + urlencoded payload))
- Headers: API-Key, API-Sign, Content-Type: application/x-www-form-urlencoded

### Key status note
The HMAC implementation is correct — the golden test vector passes cleanly.
Public endpoints (Time, AssetPairs) respond correctly.
All private calls return `EAPI:Invalid key` from Kraken's servers, meaning the
key stored in `.env` (KRAKEN_API_KEY / KRAKEN_PRIVATE_KEY) has been revoked,
expired, or never activated on this account. Before the engine refactor,
generate a fresh key pair in Kraken's API management console and update `.env`.
`/0/private/GetAPIKeyInfo` also returns HTTP 404 — Kraken has removed that
endpoint from their REST API; permissions must be verified via the web console.

## Account
- Tier per GetAPIKeyInfo: <unavailable — key invalid; regenerate key>
- Permissions per GetAPIKeyInfo: <unavailable — key invalid>
- 30d volume per TradeVolume: <unavailable — key invalid>
- Maker/taker fees per TradeVolume for XXRPZUSD: <unavailable — key invalid>
- Fee schedule from public AssetPairs (authoritative tier-0 rates):
    Taker: 0.40% base, scales to 0.05% at $500M 30d volume
    Maker: 0.25% base, scales to 0.00% at $10M 30d volume
    (full schedule in XRP/USD pair specs section below)

## XRP/USD pair specs (from AssetPairs — public, confirmed live)
- Kraken pair name: XXRPZUSD
- Alt name: XRPUSD
- WS name: XRP/USD
- Base/quote: XXRP / ZUSD
- Price decimals (pair_decimals): 5
- Lot decimals: 8
- Cost decimals: 8
- Order minimum: 1.65 XRP
- Cost minimum: 0.50 USD
- Tick size: 0.00001
- Status: online
- Execution venue: international
- Fee volume currency: ZUSD
- Leverage available (buy): 2x–10x
- Leverage available (sell): 2x–10x
- Margin call: 80%  |  Margin stop: 40%
- Long position limit: 3,200,000 XRP
- Short position limit: 1,800,000 XRP

### Full taker fee schedule (XXRPZUSD, volume in USD)
| Volume tier ($)  | Taker % |
|-----------------|---------|
| 0               | 0.40    |
| 10,000          | 0.35    |
| 50,000          | 0.24    |
| 100,000         | 0.22    |
| 250,000         | 0.20    |
| 500,000         | 0.18    |
| 1,000,000       | 0.16    |
| 2,500,000       | 0.14    |
| 5,000,000       | 0.12    |
| 10,000,000      | 0.10    |
| 100,000,000     | 0.08    |
| 500,000,000     | 0.05    |

### Full maker fee schedule (XXRPZUSD, volume in USD)
| Volume tier ($)  | Maker % |
|-----------------|---------|
| 0               | 0.25    |
| 10,000          | 0.20    |
| 50,000          | 0.14    |
| 100,000         | 0.12    |
| 250,000         | 0.10    |
| 500,000         | 0.08    |
| 1,000,000       | 0.06    |
| 2,500,000       | 0.04    |
| 5,000,000       | 0.02    |
| 10,000,000      | 0.00    |
| 100,000,000     | 0.00    |
| 500,000,000     | 0.00    |

## Rate limits (already in memory, restated for refactor session)
- Trading: max=125, decay=-2.34/sec (Standard verification tier)
- Account-mgmt: max=20, decay=-0.5/sec
- Open orders cap: 80 per pair
- Cancel decay: <5s=+8, <15s=+5, <45s=+4, <90s=+2, <300s=+1, >=300s=0
- AddOrder fixed=+1
- Post-only rejection: 1 placing + 8 cancel = 9 points (avoid by pricing safely)

## Endpoints needed for engine refactor
- POST /0/private/AddOrder
- POST /0/private/CancelOrder
- POST /0/private/CancelOrderBatch
- POST /0/private/OpenOrders
- POST /0/private/QueryOrders
- POST /0/private/Balance
- GET  /0/public/Depth?pair=XXRPZUSD&count=N    (orderbook)
- GET  /0/public/Ticker?pair=XXRPZUSD           (last/bid/ask)
- GET  /0/public/OHLC?pair=XXRPZUSD&interval=N  (candles)

## Funds available (from Balance call)
- XRP: <unavailable — key invalid>
- USD: <unavailable — key invalid>

## Open questions for refactor session
- **ACTION REQUIRED**: Generate new Kraken API key pair before any engine work.
  Current keys in .env return `EAPI:Invalid key` on all private endpoints.
- Verify post-only flag name in AddOrder (probably 'oflags=post')
- Confirm CancelOrderBatch response shape (not yet tested — needs live key)
- Decide: REST polling vs WebSocket for fill detection (REST fine for 9AM/2PM cycles)
- GetAPIKeyInfo endpoint no longer exists on Kraken REST API (HTTP 404 / EGeneral:Unknown method).
  Check permissions/tier via Kraken web console after generating new key.
