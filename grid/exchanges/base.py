from abc import ABC, abstractmethod
from typing import Optional


class BaseExchange(ABC):

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.name = self.__class__.__name__

    @abstractmethod
    def get_current_price(self) -> Optional[float]:
        """Return mid/last price for the configured symbol, or None on error."""

    @abstractmethod
    def get_ticker(self) -> tuple:
        """Return (bid, ask, spread_pct), or (0, 0, 0) on error."""

    @abstractmethod
    def place_order(self, side: str, price: float, size: float,
                    client_order_id: str) -> dict:
        """Place a post-only limit order.

        Returns {'order_id': str, 'status': str}.
        Status values: 'open', 'rejected', 'failed'.
        """

    @abstractmethod
    def cancel_all_open_orders(self) -> int:
        """Cancel all open orders for the symbol. Returns count cancelled."""

    @abstractmethod
    def cancel_orders_by_side(self, side: str) -> int:
        """Cancel all open orders on one side ('buy' or 'sell'). Returns count cancelled."""

    @abstractmethod
    def get_balances(self) -> tuple:
        """Return (xrp_balance, usd_balance). Returns (0.0, 0.0) on error."""

    @abstractmethod
    def get_candles(self, granularity: str, limit: int = 300) -> list:
        """Return list of dicts with keys: timestamp, open, high, low, close, volume.

        Granularity strings: 'ONE_HOUR', 'SIX_HOUR', 'ONE_DAY'.
        Implementations translate as needed.
        """
