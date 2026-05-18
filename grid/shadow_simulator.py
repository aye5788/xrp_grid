import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

log = logging.getLogger('grid.shadow_simulator')


def compute_expected_pnl_pct(grid_high: float, grid_low: float,
                              n_levels: int, mid_price: float,
                              maker_fee: float) -> float:
    """Closed-form expected PnL % per round-trip after maker fees.

    Reflects the static economics of a grid configuration regardless of
    fill rate. Positive → grid has theoretical edge per round-trip.
    Negative → fees exceed per-step gross. Mirrors GoodCrypto's
    "profit per level after fee" calculation.

    Note on independence from price: with grid_high / grid_low computed
    as mid_price * (1 ± half*spacing), mid_price cancels out and the
    result depends only on (n_levels, spacing_pct, maker_fee). This
    function still accepts the four positional inputs so callers don't
    have to reverse-engineer the spacing.
    """
    if n_levels <= 1 or mid_price <= 0:
        return 0.0
    price_step = (grid_high - grid_low) / (n_levels - 1)
    gross_pct_per_step = price_step / mid_price
    return gross_pct_per_step - (2 * maker_fee)


class ShadowGrid:
    """In-memory shadow simulation of one grid level variant."""

    def __init__(self, level_count: int, maker_fee: float = 0.004,
                 max_inventory_usd: float = 50.0):
        self.level_count = level_count
        self.maker_fee = maker_fee
        self.max_inventory_usd = max_inventory_usd
        self.centre = 0.0
        self.spacing_pct = 0.005
        self.resting_orders: Dict[int, dict] = {}
        self.fills: List[dict] = []
        self.last_price: float = 0.0
        self.inventory_xrp: float = 0.0
        self.inventory_usd: float = max_inventory_usd

    def rebuild(self, centre: float, spacing_pct: float,
                levels: Optional[int] = None):
        """Rebuild resting orders around centre. Preserves fill history."""
        if levels is not None:
            self.level_count = levels
        self.centre = centre
        self.spacing_pct = spacing_pct
        self.last_price = centre

        self.resting_orders = {}
        half = self.level_count // 2
        if half == 0 or centre <= 0:
            return
        size_xrp = round((self.max_inventory_usd / half) / centre, 2)

        for i in range(1, half + 1):
            buy_price = round(centre * (1 - i * spacing_pct), 5)
            self.resting_orders[-i] = {'side': 'buy', 'price': buy_price, 'size': size_xrp}
            sell_price = round(centre * (1 + i * spacing_pct), 5)
            self.resting_orders[i] = {'side': 'sell', 'price': sell_price, 'size': size_xrp}

    def update_centre(self, centre: float, spacing_pct: float):
        """Update centre and spacing without resetting resting orders.
        Used when grid spacing changes but level count stays the same.
        Preserves all resting orders and fill history."""
        self.centre = centre
        self.spacing_pct = spacing_pct
        self.last_price = centre
        log.debug(
            f"ShadowGrid lc={self.level_count}: centre updated to "
            f"{centre} spacing={spacing_pct} (orders preserved)"
        )

    def process_tick(self, price: float):
        """Fill triggered resting orders and flip them to reverse side."""
        if not self.resting_orders or price <= 0:
            return
        self.last_price = price
        to_flip = []

        for level_idx, order in list(self.resting_orders.items()):
            triggered = (
                (order['side'] == 'buy' and price <= order['price']) or
                (order['side'] == 'sell' and price >= order['price'])
            )
            if not triggered:
                continue

            fee = order['size'] * order['price'] * self.maker_fee
            if order['side'] == 'buy':
                cost = order['size'] * order['price'] + fee
                if self.inventory_usd < cost:
                    # Insufficient USD — skip fill, order stays resting
                    continue
                self.inventory_xrp += order['size']
                self.inventory_usd -= cost
            else:
                if self.inventory_xrp < order['size']:
                    # Insufficient XRP — skip fill, order stays resting
                    continue
                proceeds = order['size'] * order['price'] - fee
                self.inventory_xrp -= order['size']
                self.inventory_usd += proceeds

            self.fills.append({
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'side': order['side'],
                'order_price': order['price'],
                'fill_price': price,
                'size': order['size'],
                'fee': fee,
                'level': level_idx
            })
            to_flip.append((level_idx, order))

        for level_idx, order in to_flip:
            if order['side'] == 'buy':
                new_side = 'sell'
                new_price = round(order['price'] * (1 + self.spacing_pct), 5)
            else:
                new_side = 'buy'
                new_price = round(order['price'] * (1 - self.spacing_pct), 5)
            self.resting_orders[level_idx] = {
                'side': new_side,
                'price': new_price,
                'size': order['size']
            }

        if len(self.fills) > 500:
            self.fills = self.fills[-500:]

    def get_rolling_pnl_pct(self, window_fills: int = 50,
                              window_hours: int = 24) -> float:
        if not self.fills:
            return 0.0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        recent = [
            f for f in self.fills
            if datetime.fromisoformat(f['timestamp']) > cutoff
        ][-window_fills:]

        if not recent:
            return 0.0

        buy_cost = sum(f['size'] * f['order_price'] for f in recent if f['side'] == 'buy')
        sell_rev = sum(f['size'] * f['order_price'] for f in recent if f['side'] == 'sell')
        fees = sum(f['fee'] for f in recent)
        net_xrp = (
            sum(f['size'] for f in recent if f['side'] == 'buy') -
            sum(f['size'] for f in recent if f['side'] == 'sell')
        )
        unrealized = net_xrp * (self.last_price or self.centre)
        net_pnl = sell_rev - buy_cost - fees + unrealized
        buy_capital = buy_cost if buy_cost > 0 else 1.0
        return round(net_pnl / buy_capital * 100, 4)

    def get_fill_count(self, window_fills: int = 50,
                        window_hours: int = 24) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        recent = [
            f for f in self.fills
            if datetime.fromisoformat(f['timestamp']) > cutoff
        ]
        return min(len(recent), window_fills)

    def get_oldest_fill_age_hours(self, window_fills: int = 50,
                                   window_hours: int = 24) -> float:
        """Hours between now and the oldest fill in the rolling window. 0 if no fills."""
        if not self.fills:
            return 0.0
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        recent = [
            f for f in self.fills
            if datetime.fromisoformat(f['timestamp']) > cutoff
        ][-window_fills:]
        if not recent:
            return 0.0
        oldest_ts = datetime.fromisoformat(recent[0]['timestamp'])
        return round((datetime.now(timezone.utc) - oldest_ts).total_seconds() / 3600, 2)

    def serialize(self) -> dict:
        return {
            'level_count': self.level_count,
            'maker_fee': self.maker_fee,
            'max_inventory_usd': self.max_inventory_usd,
            'centre': self.centre,
            'spacing_pct': self.spacing_pct,
            'resting_orders': {str(k): v for k, v in self.resting_orders.items()},
            'fills': self.fills[-200:],
            'last_price': self.last_price,
            'inventory_xrp': self.inventory_xrp,
            'inventory_usd': self.inventory_usd
        }

    @classmethod
    def deserialize(cls, data: dict) -> 'ShadowGrid':
        sg = cls(
            level_count=data['level_count'],
            maker_fee=data.get('maker_fee', 0.004),
            max_inventory_usd=data.get('max_inventory_usd', 50.0)
        )
        sg.centre = data.get('centre', 0.0)
        sg.spacing_pct = data.get('spacing_pct', 0.005)
        sg.resting_orders = {
            int(k): v for k, v in data.get('resting_orders', {}).items()
        }
        sg.fills = data.get('fills', [])
        sg.last_price = data.get('last_price', 0.0)
        sg.inventory_xrp = data.get('inventory_xrp', 0.0)
        sg.inventory_usd = data.get('inventory_usd', sg.max_inventory_usd)
        return sg


class ShadowSimulator:
    """Fan-out shadow simulation across (level_count, spacing_pct) variants.

    Each variant is keyed by a (lc, sp) tuple. Variants share the centre with
    the live grid (centre cancels in the expected-PnL math), but each maintains
    its own spacing and level count for both realized fill tracking and the
    closed-form expected-PnL-per-round-trip calculation.
    """

    def __init__(self, level_variants: list, spacing_variants: list):
        self.level_variants = level_variants
        self.spacing_variants = spacing_variants
        self.variants: Dict[Tuple[int, float], ShadowGrid] = {}
        self._init_variants()

    def _init_variants(self):
        from config import MAKER_FEE, MAX_INVENTORY_USD
        for lc in self.level_variants:
            for sp in self.spacing_variants:
                sg = ShadowGrid(
                    level_count=lc,
                    maker_fee=MAKER_FEE,
                    max_inventory_usd=MAX_INVENTORY_USD,
                )
                sg.spacing_pct = sp
                self.variants[(lc, sp)] = sg

    def rebuild(self, centre: float, spacing_pct: float = None):
        """Rebuild all variants around centre. Each variant uses its own
        configured spacing; the spacing_pct arg is accepted but ignored
        (kept for back-compat with callers that pass the live grid spacing)."""
        for (lc, sp), sg in self.variants.items():
            sg.rebuild(centre, sp)
        log.info(
            f"ShadowSimulator rebuilt {len(self.variants)} variants "
            f"@ centre={centre}"
        )

    def update_centre(self, centre: float, spacing_pct: float = None):
        """Fan centre update to all variants. Each variant preserves its own
        spacing. spacing_pct arg ignored for back-compat."""
        for (lc, sp), sg in self.variants.items():
            sg.update_centre(centre, sp)
        log.info(
            f"ShadowSimulator centre updated {len(self.variants)} variants "
            f"@ centre={centre} (orders preserved, per-variant spacings)"
        )

    def process_tick(self, price: float):
        """Fan price tick out to all variants."""
        for sg in self.variants.values():
            sg.process_tick(price)

    def get_evaluation(self) -> dict:
        """Return per-variant stats list, keyed by (level_count, spacing_pct).
        Each entry includes the closed-form expected_pnl_pct alongside
        realized rolling stats."""
        from config import GRID_SWITCH_THRESHOLD_PCT, GRID_SWITCH_MIN_FILLS, MAKER_FEE
        stats = []
        for (lc, sp), sg in self.variants.items():
            half = lc // 2
            centre = sg.centre or 0.0
            if centre > 0 and half > 0:
                grid_high = centre * (1 + half * sp)
                grid_low  = centre * (1 - half * sp)
                expected_pnl_pct = compute_expected_pnl_pct(
                    grid_high, grid_low, lc, centre, MAKER_FEE
                )
            else:
                # No centre yet (pre-rebuild) — fall back to centre-independent form.
                expected_pnl_pct = (2 * half * sp / (lc - 1) - 2 * MAKER_FEE) if lc > 1 else 0.0
            stats.append({
                'level_count': lc,
                'spacing_pct': sp,
                'fills': sg.get_fill_count(),
                'rolling_pnl_pct': sg.get_rolling_pnl_pct(),
                'expected_pnl_pct': expected_pnl_pct,
                'resting_orders': len(sg.resting_orders),
            })
        return {
            'variants': stats,
            'threshold_pct': GRID_SWITCH_THRESHOLD_PCT,
            'min_fills': GRID_SWITCH_MIN_FILLS,
        }

    def get_best_shadow_pnl_pct(self, min_hours: int = None) -> Tuple[Optional[int], Optional[float], Optional[float]]:
        """Return (level_count, spacing_pct, rolling_pnl_pct) for the variant
        with the highest realized rolling P&L% among those with >= min_hours
        of fill history. Returns (None, None, None) if no variant qualifies."""
        from config import GRID_SWITCH_MIN_HOURS
        if min_hours is None:
            min_hours = GRID_SWITCH_MIN_HOURS

        best_key: Optional[Tuple[int, float]] = None
        best_pnl: Optional[float] = None

        for key, sg in self.variants.items():
            if sg.get_oldest_fill_age_hours() < min_hours:
                continue
            pnl = sg.get_rolling_pnl_pct()
            if best_pnl is None or pnl > best_pnl:
                best_pnl = pnl
                best_key = key

        if best_key is None:
            return None, None, None
        return best_key[0], best_key[1], best_pnl

    def persist_all(self):
        """Persist all variant states to DB."""
        from database import upsert_shadow_grid_state
        from config import MAKER_FEE
        for (lc, sp), sg in self.variants.items():
            half = lc // 2
            centre = sg.centre or 0.0
            if centre > 0 and half > 0:
                grid_high = centre * (1 + half * sp)
                grid_low  = centre * (1 - half * sp)
                expected_pnl_pct = compute_expected_pnl_pct(
                    grid_high, grid_low, lc, centre, MAKER_FEE
                )
            else:
                expected_pnl_pct = (2 * half * sp / (lc - 1) - 2 * MAKER_FEE) if lc > 1 else 0.0
            upsert_shadow_grid_state(
                lc, sp, sg.serialize(),
                sg.get_fill_count(),
                sg.get_rolling_pnl_pct(),
                expected_pnl_pct,
            )

    def load_from_db(self):
        """Load all variant states from DB, skip if missing."""
        from database import get_shadow_grid_state
        for lc in self.level_variants:
            for sp in self.spacing_variants:
                data = get_shadow_grid_state(lc, sp)
                if data:
                    sg = ShadowGrid.deserialize(data)
                    sg.spacing_pct = sp  # ensure key/state consistency
                    self.variants[(lc, sp)] = sg
                    log.info(f"Loaded shadow state lc={lc} sp={sp} "
                             f"fills={len(sg.fills)}")
