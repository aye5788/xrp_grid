import json
import logging
import time
import uuid
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key
import jwt as pyjwt
from config import (
    COINBASE_API_KEY, COINBASE_API_SECRET,
    SYMBOL, COINBASE_RATE_LIMIT_BACKOFF
)
from grid.exchanges.base import BaseExchange

log = logging.getLogger('grid.exchanges.coinbase')

COINBASE_REST = "https://api.coinbase.com/api/v3/brokerage"


class CoinbaseExchange(BaseExchange):

    def __init__(self, symbol: str = SYMBOL):
        super().__init__(symbol)

    def _get_jwt_headers(self, method, path):
        """Generate JWT auth headers for Coinbase Advanced API."""
        key_name = COINBASE_API_KEY
        key_secret = COINBASE_API_SECRET
        request_host = "api.coinbase.com"
        uri = f"{method} {request_host}{path}"
        private_key_bytes = key_secret.encode('utf-8')
        payload = {
            "sub": key_name,
            "iss": "cdp",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": uri
        }
        try:
            private_key = load_pem_private_key(private_key_bytes, password=None)
            token = pyjwt.encode(payload, private_key, algorithm="ES256",
                                  headers={"kid": key_name, "nonce": str(uuid.uuid4())})
            return {"Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"}
        except Exception as e:
            log.error(f"JWT generation failed: {e}")
            raise

    def get_current_price(self):
        """Return current XRP-USD mid price, or None on error."""
        try:
            url = f"{COINBASE_REST}/market/products/{self.symbol}"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            return float(data.get('price', 0))
        except Exception as e:
            log.error(f"Price fetch error: {e}")
            return None

    def get_ticker(self):
        """Return (bid, ask, spread_pct), or (0, 0, 0) on error."""
        try:
            url = f"{COINBASE_REST}/market/products/{self.symbol}/ticker"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            bid = float(data.get('best_bid', 0))
            ask = float(data.get('best_ask', 0))
            spread_pct = (ask - bid) / bid * 100 if bid > 0 else 0
            return bid, ask, spread_pct
        except Exception as e:
            log.error(f"Spread fetch error: {e}")
            return 0, 0, 0

    def place_order(self, side: str, price: float, size: float,
                    client_order_id: str) -> dict:
        """Place a post-only limit order on Coinbase. Returns {'order_id', 'status'}."""
        try:
            path = "/api/v3/brokerage/orders"
            headers = self._get_jwt_headers("POST", path)
            body = {
                "client_order_id": client_order_id,
                "product_id": self.symbol,
                "side": side.upper(),
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": str(size),
                        "limit_price": str(price),
                        "post_only": True
                    }
                }
            }
            r = requests.post(f"{COINBASE_REST}/orders",
                              headers=headers,
                              json=body, timeout=10)
            if r.status_code == 429:
                log.warning(f"Coinbase rate limited — backing off {COINBASE_RATE_LIMIT_BACKOFF}s")
                time.sleep(COINBASE_RATE_LIMIT_BACKOFF)
                r = requests.post(f"{COINBASE_REST}/orders",
                                  headers=headers,
                                  json=body, timeout=10)
            r.raise_for_status()
            result = r.json()
            log.info(f"[LIVE] {side.upper()} {size} XRP @ {price} placed — id={result.get('order_id', client_order_id)}")
            return {'order_id': result.get('order_id', client_order_id), 'status': 'open'}
        except Exception as e:
            log.error(f"Order placement failed: {e}")
            return {'order_id': client_order_id, 'status': 'failed'}

    def cancel_all_open_orders(self) -> int:
        """Cancel all open orders for the symbol. Returns count cancelled."""
        try:
            path = "/api/v3/brokerage/orders/batch_cancel"
            headers = self._get_jwt_headers("POST", path)
            r = requests.get(f"{COINBASE_REST}/orders/historical/batch",
                             headers=self._get_jwt_headers("GET",
                             "/api/v3/brokerage/orders/historical/batch"),
                             params={"product_id": self.symbol, "order_status": "OPEN"},
                             timeout=10)
            r.raise_for_status()
            orders = r.json().get('orders', [])
            order_ids = [o['order_id'] for o in orders]
            if not order_ids:
                return 0
            r2 = requests.post(f"{COINBASE_REST}/orders/batch_cancel",
                               headers=headers,
                               json={"order_ids": order_ids}, timeout=10)
            r2.raise_for_status()
            log.info(f"[LIVE] Cancelled {len(order_ids)} orders")
            return len(order_ids)
        except Exception as e:
            log.error(f"Cancel all failed: {e}")
            return 0

    def get_balances(self) -> tuple:
        """Return (xrp_balance, usd_balance). Returns (0.0, 0.0) on error."""
        try:
            path = "/api/v3/brokerage/accounts"
            headers = self._get_jwt_headers("GET", path)
            r = requests.get(f"{COINBASE_REST}/accounts",
                             headers=headers, timeout=10)
            r.raise_for_status()
            accounts = r.json().get('accounts', [])
            xrp = usd = 0.0
            for acc in accounts:
                if acc.get('currency') == 'XRP':
                    xrp = float(acc.get('available_balance', {}).get('value', 0))
                elif acc.get('currency') == 'USD':
                    usd = float(acc.get('available_balance', {}).get('value', 0))
            return xrp, usd
        except Exception as e:
            log.error(f"Balance fetch failed: {e}")
            return 0.0, 0.0

    def get_candles(self, granularity: str, limit: int = 300) -> list:
        """Return candles as list of dicts with keys: timestamp, open, high, low, close, volume."""
        try:
            url = f"{COINBASE_REST}/market/products/{self.symbol}/candles"
            params = {"granularity": granularity, "limit": limit}
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            raw = r.json().get('candles', [])
            candles = []
            for c in raw:
                candles.append({
                    'timestamp': c.get('start'),
                    'open':      float(c.get('open', 0)),
                    'high':      float(c.get('high', 0)),
                    'low':       float(c.get('low', 0)),
                    'close':     float(c.get('close', 0)),
                    'volume':    float(c.get('volume', 0)),
                })
            return candles
        except Exception as e:
            log.error(f"Candles fetch error: {e}")
            return []
