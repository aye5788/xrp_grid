import time
import hmac
import hashlib
import base64
import urllib.parse
import json
import logging
import requests
from datetime import datetime, timezone
from typing import Optional
from config import MAX_INVENTORY_USD
from grid.exchanges.base import BaseExchange

log = logging.getLogger('grid.exchanges.kraken')

KRAKEN_REST = "https://api.kraken.com"
# Kraken's canonical pair name. The engine passes "XRP-USD" (Coinbase format);
# we store that as self.symbol but use this constant for all Kraken API calls.
KRAKEN_PAIR = "XXRPZUSD"
USER_AGENT = "magi-xrp-grid/0.1"


class KrakenExchange(BaseExchange):

    def __init__(self, symbol: str):
        # symbol will be "XRP-USD" from the engine — stored for interface compliance
        # but all Kraken API calls use KRAKEN_PAIR ("XXRPZUSD") directly.
        super().__init__(symbol)

        # Kraken API credentials — routed through config.py which calls load_dotenv() at import time,
        # ensuring vars are available under both shell sessions and systemd-launched processes.
        from config import KRAKEN_API_KEY, KRAKEN_API_SECRET
        self._api_key = KRAKEN_API_KEY
        self._private_key = KRAKEN_API_SECRET
        if not self._api_key or not self._private_key:
            log.warning("Kraken API credentials not set in environment — auth calls will fail")

        # Trading rate counter state (Pro tier: max=125, decay=2.34/s)
        self._trading_counter: float = 0.0
        self._counter_last_update: float = time.time()
        self._trading_max: float = 125.0
        self._trading_decay_per_sec: float = 2.34

        # Order-age tracking: maps txid → time.time() when order was placed.
        # Populated on successful AddOrder, removed on cancel/fill detection.
        self._open_order_placed_at: dict = {}

    # --- Auth ---

    def _sign(self, urlpath: str, data: dict) -> str:
        """HMAC-SHA512 signature per Kraken REST API docs."""
        if not self._api_key or not self._private_key:
            raise RuntimeError("Kraken API credentials not set in environment")
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data['nonce']) + postdata).encode('utf-8')
        message = urlpath.encode('utf-8') + hashlib.sha256(encoded).digest()
        private_key_bytes = base64.b64decode(self._private_key)
        signature = hmac.new(private_key_bytes, message, hashlib.sha512)
        return base64.b64encode(signature.digest()).decode('utf-8')

    # --- HTTP helpers ---

    def _private_post(self, endpoint: str, data: dict = None, as_json: bool = False) -> dict:
        """Authenticated POST to a private Kraken endpoint. Returns result dict.

        as_json=False (default): urlencoded body, compatible with all standard endpoints.
        as_json=True: JSON body with JSON-aware signing input, required for CancelOrderBatch.
        """
        data = data or {}
        data['nonce'] = str(time.time_ns())
        urlpath = f"/0/private/{endpoint}"

        if as_json:
            body_str = json.dumps(data)
            encoded = (str(data['nonce']) + body_str).encode('utf-8')
            message = urlpath.encode('utf-8') + hashlib.sha256(encoded).digest()
            private_key_bytes = base64.b64decode(self._private_key)
            signature = hmac.new(private_key_bytes, message, hashlib.sha512)
            sig = base64.b64encode(signature.digest()).decode('utf-8')
            headers = {
                'API-Key': self._api_key,
                'API-Sign': sig,
                'Content-Type': 'application/json',
                'User-Agent': USER_AGENT,
            }
            r = requests.post(
                KRAKEN_REST + urlpath,
                headers=headers,
                data=body_str,
                timeout=10
            )
        else:
            signature = self._sign(urlpath, data)
            headers = {
                'API-Key': self._api_key,
                'API-Sign': signature,
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': USER_AGENT,
            }
            r = requests.post(
                KRAKEN_REST + urlpath,
                headers=headers,
                data=urllib.parse.urlencode(data),
                timeout=10
            )

        r.raise_for_status()
        result = r.json()
        if result.get('error'):
            log.error(f"Kraken API error ({endpoint}): {result['error']}")
            raise RuntimeError(f"Kraken API error: {result['error']}")
        return result.get('result', {})

    def _public_get(self, endpoint: str, params: dict = None) -> dict:
        """Unauthenticated GET from a public Kraken endpoint. Returns result dict."""
        r = requests.get(
            KRAKEN_REST + f"/0/public/{endpoint}",
            params=params,
            headers={'User-Agent': USER_AGENT},
            timeout=10
        )
        r.raise_for_status()
        result = r.json()
        if result.get('error'):
            log.error(f"Kraken public API error ({endpoint}): {result['error']}")
            raise RuntimeError(f"Kraken API error: {result['error']}")
        return result.get('result', {})

    # --- Rate counter helpers ---

    def _decay_counter(self) -> None:
        now = time.time()
        elapsed = now - self._counter_last_update
        self._trading_counter = max(0.0, self._trading_counter - elapsed * self._trading_decay_per_sec)
        self._counter_last_update = now

    def _check_and_add(self, cost: float, op: str) -> None:
        self._decay_counter()
        projected = self._trading_counter + cost
        if projected > self._trading_max:
            sleep_needed = (projected - self._trading_max) / self._trading_decay_per_sec + 0.1
            log.warning(
                f"Kraken trading counter would exceed limit "
                f"({self._trading_counter:.1f}+{cost}={projected:.1f}>{self._trading_max}); "
                f"sleeping {sleep_needed:.1f}s for {op}"
            )
            time.sleep(sleep_needed)
            self._decay_counter()
        self._trading_counter += cost
        log.debug(f"Kraken counter +{cost} ({op}) → {self._trading_counter:.2f}")

    def _cancel_cost(self, txid: str) -> int:
        """Return the rate-counter cost of cancelling this order, based on its age."""
        placed_at = self._open_order_placed_at.get(txid)
        if placed_at is None:
            return 0  # Unknown/old order — treat as free
        age = time.time() - placed_at
        if age < 5:
            return 8
        elif age < 15:
            return 5
        elif age < 45:
            return 4
        elif age < 90:
            return 2
        elif age < 300:
            return 1
        else:
            return 0

    # --- BaseExchange interface ---

    def get_current_price(self) -> Optional[float]:
        """Return current XRP last-trade price, or None on error."""
        try:
            result = self._public_get("Ticker", {"pair": KRAKEN_PAIR})
            return float(result[KRAKEN_PAIR]["c"][0])
        except Exception as e:
            log.error(f"Price fetch error: {e}")
            return None

    def get_ticker(self) -> tuple:
        """Return (bid, ask, spread_pct), or (0, 0, 0) on error."""
        try:
            result = self._public_get("Ticker", {"pair": KRAKEN_PAIR})
            bid = float(result[KRAKEN_PAIR]["b"][0])
            ask = float(result[KRAKEN_PAIR]["a"][0])
            spread_pct = (ask - bid) / bid * 100 if bid > 0 else 0
            return bid, ask, spread_pct
        except Exception as e:
            log.error(f"Spread fetch error: {e}")
            return 0, 0, 0

    def place_order(self, side: str, price: float, size: float,
                    client_order_id: str) -> dict:
        """Place a post-only limit order on Kraken. Returns {'order_id', 'status'}."""
        self._check_and_add(1, "AddOrder")
        # Kraken userref is a positive int32 (1–2147483647); mask to 31 bits to stay in range
        userref = int(hashlib.md5(client_order_id.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
        payload = {
            "pair": KRAKEN_PAIR,
            "type": "buy" if side.lower() == "buy" else "sell",
            "ordertype": "limit",
            "price": f"{price:.5f}",
            "volume": f"{size:.8f}",
            "oflags": "post",
            "userref": userref,
        }
        try:
            result = self._private_post("AddOrder", payload)
            txid = result["txid"][0]
            self._open_order_placed_at[txid] = time.time()
            log.info(f"[KRAKEN] {side.upper()} {size} XRP @ {price} placed — txid={txid}")
            return {"order_id": txid, "status": "open"}
        except RuntimeError as e:
            if "EOrder:Post only order" in str(e):
                # Post-only rejection: placement cost (1) already added; implicit cancel costs 8 more
                self._check_and_add(8, "post-only rejection cancel cost")
                log.warning(f"Kraken post-only rejection for {client_order_id}")
                return {"order_id": client_order_id, "status": "rejected"}
            log.error(f"Order placement failed: {e}")
            return {"order_id": client_order_id, "status": "failed"}
        except Exception as e:
            log.error(f"Order placement failed: {e}")
            return {"order_id": client_order_id, "status": "failed"}

    def cancel_all_open_orders(self) -> int:
        """Cancel all open XRP/USD orders. Returns count cancelled."""
        try:
            result = self._private_post("OpenOrders")
            open_orders = result.get("open", {})
            # Kraken returns alt pair name "XRPUSD" in OpenOrders descr (not canonical XXRPZUSD)
            txids = [
                txid for txid, order in open_orders.items()
                if order.get("descr", {}).get("pair") == "XRPUSD"
            ]
            if not txids:
                return 0
            if len(txids) == 1:
                txid = txids[0]
                cost = self._cancel_cost(txid)
                self._check_and_add(cost, "CancelOrder x1")
                result = self._private_post("CancelOrder", {"txid": txid})
                n = result.get("count", 0)
                if n > 0:
                    self._open_order_placed_at.pop(txid, None)
                log.info(f"[KRAKEN] Cancelled {n} order (single)")
                return n
            total_cost = sum(self._cancel_cost(txid) for txid in txids)
            self._check_and_add(total_cost, f"CancelOrderBatch x{len(txids)}")
            result = self._private_post("CancelOrderBatch", {"orders": txids}, as_json=True)
            n = result.get("count", 0)
            for txid in txids:
                self._open_order_placed_at.pop(txid, None)
            log.info(f"[KRAKEN] Cancelled {n} orders")
            return n
        except Exception as e:
            log.error(f"Cancel all failed: {e}")
            # Do NOT remove from _open_order_placed_at — orders may still be live
            return 0

    def get_balances(self) -> tuple:
        """Return (xrp_balance, usd_balance) for XXRP and ZUSD only.

        Ring-fenced by design: ignores all other assets on the account.
        """
        try:
            result = self._private_post("Balance")
            xrp = float(result.get("XXRP", 0))
            usd = float(result.get("ZUSD", 0))
            return xrp, usd
        except Exception as e:
            log.error(f"Balance fetch failed: {e}")
            return 0.0, 0.0

    def get_candles(self, granularity: str, limit: int = 300) -> list:
        """Return candles as list of dicts: timestamp, open, high, low, close, volume.

        SIX_HOUR is resampled from 1H bars because Kraken does not natively support
        a 6H interval (360 is not a valid Kraken OHLC interval).
        """
        if granularity == "SIX_HOUR":
            # Fetch 1H bars and resample into 6H buckets in Python.
            try:
                result = self._public_get("OHLC", {"pair": KRAKEN_PAIR, "interval": 60})
                raw = result.get(KRAKEN_PAIR, [])
                # Bucket each hourly bar by its 6H window (Unix ts // 21600)
                buckets = {}
                for c in raw:
                    key = int(c[0]) // (6 * 3600)
                    if key not in buckets:
                        buckets[key] = []
                    buckets[key].append(c)
                candles = []
                for key in sorted(buckets):
                    bars = buckets[key]
                    bucket_start = key * 6 * 3600
                    candles.append({
                        "timestamp": datetime.fromtimestamp(bucket_start, tz=timezone.utc).isoformat(),
                        "open":      float(bars[0][1]),
                        "high":      max(float(b[2]) for b in bars),
                        "low":       min(float(b[3]) for b in bars),
                        "close":     float(bars[-1][4]),
                        "volume":    sum(float(b[6]) for b in bars),
                    })
                # Drop trailing bucket if it contains fewer than 6 hourly bars (incomplete window)
                if candles and len(buckets[sorted(buckets)[-1]]) < 6:
                    candles = candles[:-1]
                return candles[-limit:]
            except Exception as e:
                log.error(f"Candles fetch error: {e}")
                return []

        granularity_map = {
            "ONE_HOUR": 60,
            "ONE_DAY": 1440,
        }
        interval = granularity_map.get(granularity)
        if interval is None:
            log.warning(f"Unknown Kraken granularity '{granularity}' — defaulting to 60m")
            interval = 60
        try:
            result = self._public_get("OHLC", {"pair": KRAKEN_PAIR, "interval": interval})
            raw = result.get(KRAKEN_PAIR, [])
            candles = []
            for c in raw:
                candles.append({
                    "timestamp": datetime.fromtimestamp(int(c[0]), tz=timezone.utc).isoformat(),
                    "open":      float(c[1]),
                    "high":      float(c[2]),
                    "low":       float(c[3]),
                    "close":     float(c[4]),
                    "volume":    float(c[6]),
                })
            candles.sort(key=lambda x: x["timestamp"])
            return candles[-limit:]
        except Exception as e:
            log.error(f"Candles fetch error: {e}")
            return []
