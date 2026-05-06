import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
import requests
from config import (
    COINBASE_API_KEY, COINBASE_API_SECRET,
    SYMBOL, GRID_LEVELS_DEFAULT, GRID_SPACING_PCT,
    TAKER_FEE, MAKER_FEE, MAX_INVENTORY_USD,
    DB_PATH, EXCHANGE
)
from database import (
    insert_grid_state, get_current_grid_state,
    get_latest_indicators, upsert_inventory,
    get_latest_inventory,
    insert_grid_order, update_grid_order_status
)
from grid.exchanges.coinbase import CoinbaseExchange

log = logging.getLogger('grid.engine')

PAPER_MODE = True  # Always start in paper mode


class GridEngine:

    def __init__(self, paper=True):
        self.paper = paper
        self.paper_orders = {}  # Simulated order book {order_id: order_dict}
        self.paper_inventory = {'xrp': 0.0, 'usd': 100.0}  # Start with $100 USD paper
        self.level_count = GRID_LEVELS_DEFAULT
        self.shadow_sim = None
        self._shadow_tick_count = 0

        if EXCHANGE == "coinbase":
            self.exchange = CoinbaseExchange(symbol=SYMBOL)
        elif EXCHANGE == "kraken":
            from grid.exchanges.kraken import KrakenExchange
            self.exchange = KrakenExchange(symbol=SYMBOL)
        else:
            raise ValueError(f"Unknown exchange: {EXCHANGE}")

        if not self.paper:
            log.warning("LIVE MODE ACTIVE — real orders will be placed")
        else:
            log.info("Paper mode active — no real orders will be placed")

    # --- State loading ---

    def load_state(self):
        """Load shadow simulator and level_count from DB. Call after init_db()."""
        from config import GRID_LEVEL_VARIANTS
        from grid.shadow_simulator import ShadowSimulator
        self.shadow_sim = ShadowSimulator(GRID_LEVEL_VARIANTS)
        self.shadow_sim.load_from_db()
        grid_state = get_current_grid_state()
        if grid_state and grid_state.get('levels'):
            self.level_count = grid_state['levels']
        # Ensure all variant rows exist in DB (fill_count=0 for brand-new variants)
        self.shadow_sim.persist_all()

        # Restore paper_orders from grid_orders table — any status='open' row
        # represents an order that was live in a previous process incarnation.
        # The migration that introduced this behavior wiped historical ghosts,
        # so any row we find here is genuinely live state.
        from database import get_conn
        conn = get_conn()
        rows = conn.execute(
            "SELECT order_id, side, price, size, timestamp FROM grid_orders WHERE status='open'"
        ).fetchall()
        conn.close()
        self.paper_orders = {}
        for r in rows:
            self.paper_orders[r['order_id']] = {
                'order_id': r['order_id'],
                'side': r['side'],
                'price': r['price'],
                'size': r['size'],
                'status': 'open',
                'timestamp': r['timestamp'],
            }
        log.info(f"Engine state loaded — restored {len(self.paper_orders)} paper orders from DB")

        # Restore paper_inventory from the most recent inventory snapshot.
        # If the table is empty (very fresh deployment), keep the __init__ defaults.
        latest = get_latest_inventory()
        if latest is not None:
            self.paper_inventory = {
                'xrp': float(latest.get('xrp_held') or 0.0),
                'usd': float(latest.get('usd_held') or 0.0),
            }
            log.info(f"Engine state loaded — paper_inventory restored: xrp={self.paper_inventory['xrp']:.4f} usd=${self.paper_inventory['usd']:.2f}")

        # Sanity check: paper_inventory must be physically possible (no negative spot holdings).
        # If the most recent inventory snapshot has impossible values (legacy bug from
        # pre-fix simulator), reset baseline from live Kraken account.
        if self.paper_inventory['xrp'] < 0 or self.paper_inventory['usd'] < 0:
            log.warning(
                f"[PAPER RESET] Impossible paper_inventory detected: "
                f"xrp={self.paper_inventory['xrp']}, usd={self.paper_inventory['usd']}. "
                f"Querying live Kraken balance for baseline reset."
            )
            try:
                xrp_real, usd_real = self.exchange.get_balances()
                if xrp_real > 0 or usd_real > 0:
                    self.paper_inventory = {'xrp': float(xrp_real), 'usd': float(usd_real)}
                    log.warning(
                        f"[PAPER RESET] paper_inventory rebased from Kraken: "
                        f"xrp={xrp_real:.4f}, usd=${usd_real:.2f}"
                    )
                    # Persist the corrected baseline immediately
                    from database import upsert_inventory
                    price = self.get_current_price() or 0.0
                    net_usd = xrp_real * price
                    skew = net_usd / MAX_INVENTORY_USD if MAX_INVENTORY_USD > 0 else 0
                    upsert_inventory(xrp_real, usd_real, net_usd, skew)
                else:
                    log.error("[PAPER RESET] Kraken returned zero balances; falling back to defaults")
                    self.paper_inventory = {'xrp': 0.0, 'usd': 100.0}
            except Exception as e:
                log.error(f"[PAPER RESET] Kraken balance fetch failed: {e}; falling back to defaults")
                self.paper_inventory = {'xrp': 0.0, 'usd': 100.0}

        log.info(f"Engine state loaded — level_count={self.level_count}")

    # --- Shadow simulation ---

    def process_shadow_tick(self, price: float):
        """Pass a price tick to shadow simulator; persist every 10 ticks."""
        if not self.shadow_sim or price <= 0:
            return
        self.shadow_sim.process_tick(price)
        self._shadow_tick_count += 1
        if self._shadow_tick_count % 10 == 0:
            try:
                self.shadow_sim.persist_all()
            except Exception as e:
                log.warning(f"Shadow persist failed: {e}")

    def evaluate_and_maybe_switch_levels(self) -> Optional[int]:
        """Evaluate shadow variants; switch level_count and rebuild if warranted."""
        if not self.shadow_sim:
            return None
        from config import (GRID_SWITCH_THRESHOLD_PCT, GRID_SWITCH_MIN_FILLS,
                            GRID_SWITCH_MIN_HOURS)
        eval_data = self.shadow_sim.get_evaluation()
        variants = eval_data['variants']
        if not variants:
            return None

        current_stats = variants.get(self.level_count, {})
        current_pnl = current_stats.get('rolling_pnl_pct', 0.0)
        current_fills = current_stats.get('fills', 0)

        # Gate 1: fills count on current variant
        if current_fills < GRID_SWITCH_MIN_FILLS:
            log.info(f"Shadow eval: insufficient fills "
                     f"(current lc={self.level_count} fills={current_fills} "
                     f"< min={GRID_SWITCH_MIN_FILLS}) — no switch")
            return None

        best_lc = max(variants.keys(), key=lambda k: variants[k]['rolling_pnl_pct'])
        best_pnl = variants[best_lc]['rolling_pnl_pct']
        best_fills = variants[best_lc]['fills']

        if best_lc == self.level_count:
            log.info(f"Shadow eval: current lc={self.level_count} is best "
                     f"(pnl={current_pnl:.4f}%)")
            return None

        # Gate 2: fills count on best candidate
        if best_fills < GRID_SWITCH_MIN_FILLS:
            log.info(f"Shadow eval: insufficient fills on best candidate "
                     f"(lc={best_lc} fills={best_fills} < min={GRID_SWITCH_MIN_FILLS}) "
                     f"— no switch")
            return None

        # Gate 3: time window — both variants need GRID_SWITCH_MIN_HOURS of history
        current_sg = self.shadow_sim.variants.get(self.level_count)
        best_sg = self.shadow_sim.variants.get(best_lc)
        current_age = current_sg.get_oldest_fill_age_hours() if current_sg else 0.0
        best_age = best_sg.get_oldest_fill_age_hours() if best_sg else 0.0

        if current_age < GRID_SWITCH_MIN_HOURS or best_age < GRID_SWITCH_MIN_HOURS:
            log.info(f"Shadow eval: insufficient time window "
                     f"(current={current_age:.1f}h, best={best_age:.1f}h, "
                     f"min={GRID_SWITCH_MIN_HOURS}h) — no switch")
            return None

        # Gate 4: P&L margin
        margin = best_pnl - current_pnl
        if margin < GRID_SWITCH_THRESHOLD_PCT:
            log.info(f"Shadow eval: margin={margin:.4f}% < "
                     f"threshold={GRID_SWITCH_THRESHOLD_PCT}% "
                     f"({self.level_count}→{best_lc}) — no switch")
            return None

        old_lc = self.level_count
        log.warning(f"Shadow eval: switching {old_lc}→{best_lc} levels "
                    f"margin={margin:.4f}% pnl_best={best_pnl:.4f}% "
                    f"pnl_curr={current_pnl:.4f}%")
        self.level_count = best_lc
        grid_state = get_current_grid_state()
        centre = grid_state['centre_price'] if grid_state else None
        spacing = grid_state['spacing_pct'] if grid_state else GRID_SPACING_PCT

        # Audit trail row before rebuild
        if centre:
            insert_grid_state(centre, spacing, best_lc,
                              notes=f"level_switch {old_lc}→{best_lc} margin={margin:.4f}%")

        self.initialise_grid(centre=centre, spacing_pct=spacing)
        return best_lc

    # --- Market Data ---

    def get_current_price(self) -> Optional[float]:
        """Get current XRP-USD mid price."""
        return self.exchange.get_current_price()

    def get_current_spread(self) -> tuple:
        """Get current bid/ask spread."""
        return self.exchange.get_ticker()

    # --- Grid Construction ---

    def build_grid_levels(self, centre: float, spacing_pct: float,
                           levels: int, xrp_held: float, usd_held: float) -> list:
        """Build grid price levels trimmed to what current holdings can cover."""
        half = levels // 2
        size_xrp = self.compute_order_size(centre)

        # Sell ladder: walk nearest-to-centre outward, stop when XRP runs out
        sell_levels = []
        cumulative_xrp = 0.0
        for i in range(1, half + 1):
            if cumulative_xrp + size_xrp > xrp_held:
                break
            price = round(centre * (1 + i * spacing_pct), 5)
            sell_levels.append({'price': price, 'side': 'sell', 'level': i})
            cumulative_xrp += size_xrp

        # Buy ladder: walk nearest-to-centre outward, stop when USD runs out
        buy_levels = []
        cumulative_usd = 0.0
        for i in range(1, half + 1):
            level_price = round(centre * (1 - i * spacing_pct), 5)
            cost = size_xrp * level_price
            if cumulative_usd + cost > usd_held:
                break
            buy_levels.append({'price': level_price, 'side': 'buy', 'level': -i})
            cumulative_usd += cost

        return buy_levels + sell_levels

    def compute_order_size(self, price: float) -> float:
        """Compute XRP order size per level based on max inventory."""
        size_usd = MAX_INVENTORY_USD / (self.level_count // 2)
        size_xrp = size_usd / price
        return round(size_xrp, 2)

    # --- Order Management ---

    def place_order(self, side: str, price: float, size: float) -> dict:
        """Place a limit order. In paper mode, simulate it."""
        order_id = str(uuid.uuid4())[:8]
        order = {
            'order_id': order_id,
            'side': side,
            'price': price,
            'size': size,
            'status': 'open',
            'timestamp': datetime.utcnow().isoformat()
        }

        # Sanity check — refuse buys above market or sells below market
        current_price = self.get_current_price()
        if current_price:
            if side == 'buy' and price > current_price * 1.001:
                log.error(f"Refusing buy order — price {price} above market {current_price}")
                order['status'] = 'rejected'
                return order
            if side == 'sell' and price < current_price * 0.999:
                log.error(f"Refusing sell order — price {price} below market {current_price}")
                order['status'] = 'rejected'
                return order

        # Position size hard cap
        from config import MAX_INVENTORY_USD
        from database import get_latest_inventory
        inv = get_latest_inventory() or {}
        net_pos = abs(inv.get('net_position_usd', 0) or 0)
        if net_pos >= MAX_INVENTORY_USD:
            log.error(f"Position size cap reached: ${net_pos:.2f} >= ${MAX_INVENTORY_USD}")
            order['status'] = 'rejected_size_cap'
            return order

        if self.paper:
            self.paper_orders[order_id] = order
            try:
                insert_grid_order(
                    timestamp=order['timestamp'],
                    order_id=order_id,
                    side=side,
                    price=price,
                    size=size,
                    status='open',
                    fee=0.0
                )
            except Exception as e:
                log.warning(f"Failed to persist paper order: {e}")
            log.info(f"[PAPER] {side.upper()} {size} XRP @ {price} — id={order_id}")
            return order

        # Live order placement
        result = self.exchange.place_order(side, price, size, order_id)
        order['order_id'] = result['order_id']
        order['status'] = result['status']
        return order

    def cancel_all_orders(self):
        """Cancel all open orders."""
        if self.paper:
            now_iso = datetime.utcnow().isoformat()
            cancelled = 0
            for order_id, order in list(self.paper_orders.items()):
                if order.get('status') == 'open':
                    try:
                        update_grid_order_status(order_id, 'cancelled', filled_at=now_iso)
                    except Exception as e:
                        log.warning(f"Failed to persist cancel for {order_id}: {e}")
                    cancelled += 1
            count = len(self.paper_orders)
            self.paper_orders.clear()
            log.info(f"[PAPER] Cancelled {cancelled} open orders ({count} total cleared)")
            return cancelled

        return self.exchange.cancel_all_open_orders()

    def simulate_fills(self, current_price: float):
        """Paper mode: check which resting orders would be filled at current price."""
        filled = []
        for order_id, order in list(self.paper_orders.items()):
            if order['status'] != 'open':
                continue
            filled_flag = False
            if order['side'] == 'buy' and current_price <= order['price']:
                cost_usd = order['size'] * order['price']
                if self.paper_inventory['usd'] < cost_usd:
                    log.warning(
                        f"[PAPER REJECT] Buy fill rejected — insufficient USD. "
                        f"Need ${cost_usd:.2f}, have ${self.paper_inventory['usd']:.2f}. "
                        f"Order id={order_id} stays open."
                    )
                    continue
                filled_flag = True
                self.paper_inventory['xrp'] += order['size']
                self.paper_inventory['usd'] -= cost_usd
            elif order['side'] == 'sell' and current_price >= order['price']:
                if self.paper_inventory['xrp'] < order['size']:
                    log.warning(
                        f"[PAPER REJECT] Sell fill rejected — insufficient XRP. "
                        f"Need {order['size']}, have {self.paper_inventory['xrp']:.4f}. "
                        f"Order id={order_id} stays open."
                    )
                    continue
                filled_flag = True
                self.paper_inventory['xrp'] -= order['size']
                self.paper_inventory['usd'] += order['size'] * order['price']

            if filled_flag:
                order['status'] = 'filled'
                order['fill_price'] = current_price
                fee = order['size'] * order['price'] * MAKER_FEE
                order['fee'] = fee
                now_iso = datetime.utcnow().isoformat()
                try:
                    update_grid_order_status(
                        order_id, 'filled',
                        filled_at=now_iso,
                        fill_price=current_price,
                        fee=fee
                    )
                except Exception as e:
                    log.warning(f"Failed to persist fill for {order_id}: {e}")
                filled.append(order)
                log.info(f"[PAPER FILL] {order['side'].upper()} {order['size']} XRP @ {current_price} fee={fee:.4f}")

        return filled

    # --- Grid Lifecycle ---

    def initialise_grid(self, centre: Optional[float] = None,
                         spacing_pct: Optional[float] = None):
        """Set up the full grid from scratch."""
        if centre is None:
            centre = self.get_current_price()
            if not centre:
                log.error("Cannot initialise grid — no price available")
                return False

        if spacing_pct is None:
            spacing_pct = GRID_SPACING_PCT

        log.info(f"Initialising grid — centre={centre} spacing={spacing_pct*100:.2f}% levels={self.level_count}")
        self.cancel_all_orders()

        levels = self.build_grid_levels(
            centre, spacing_pct, self.level_count,
            xrp_held=self.paper_inventory['xrp'],
            usd_held=self.paper_inventory['usd'],
        )

        if not levels:
            log.warning("Grid empty after inventory trim — no buys or sells coverable with current holdings")
            return False

        n_buys = sum(1 for l in levels if l['side'] == 'buy')
        n_sells = sum(1 for l in levels if l['side'] == 'sell')
        half = self.level_count // 2
        if n_buys != n_sells or n_buys < half:
            log.warning(
                f"Grid asymmetric — placing {n_buys} buys + {n_sells} sells "
                f"(XRP held insufficient for full sell ladder)"
            )

        size = self.compute_order_size(centre)

        placed = 0
        for level in levels:
            order = self.place_order(level['side'], level['price'], size)
            if order['status'] in ('open', 'filled'):
                placed += 1
            time.sleep(0.1)  # Rate limit buffer

        insert_grid_state(centre, spacing_pct, self.level_count,
                          notes=f"Grid initialised — {placed} orders placed")
        log.info(f"Grid initialised — {placed}/{len(levels)} orders placed")

        if self.shadow_sim:
            self.shadow_sim.rebuild(centre, spacing_pct)

        return True

    def apply_magi_decision(self, consensus: dict):
        """Apply the MAGI consensus to the grid."""
        grid_action = consensus.get('grid_action', 'MAINTAIN')
        risk_action = consensus.get('risk_action', 'CLEAR')

        if grid_action == 'HALT' or risk_action == 'HALT':
            log.warning("MAGI HALT — cancelling all orders")
            self.cancel_all_orders()
            return

        current_state = get_current_grid_state()
        if not current_state:
            log.warning("No grid state — initialising fresh")
            self.initialise_grid()
            return

        centre = current_state['centre_price']
        spacing = current_state['spacing_pct']

        if grid_action == 'RECENTRE':
            indicators = get_latest_indicators('1h')
            new_centre = indicators.get('vwap') if indicators else None
            if new_centre:
                log.info(f"MAGI RECENTRE — {centre} → {new_centre}")
                self.initialise_grid(centre=new_centre, spacing_pct=spacing)

        elif grid_action == 'TIGHTEN':
            new_spacing = spacing * 0.8
            log.info(f"MAGI TIGHTEN — spacing {spacing*100:.3f}% → {new_spacing*100:.3f}%")
            self.initialise_grid(centre=centre, spacing_pct=new_spacing)

        elif grid_action == 'WIDEN':
            new_spacing = spacing * 1.2
            log.info(f"MAGI WIDEN — spacing {spacing*100:.3f}% → {new_spacing*100:.3f}%")
            self.initialise_grid(centre=centre, spacing_pct=new_spacing)

        else:  # MAINTAIN
            log.info("MAGI MAINTAIN — no grid changes")

        # Apply risk constraints
        if risk_action == 'PAUSE_LONGS':
            log.info("MAGI PAUSE_LONGS — cancelling buy orders")
        elif risk_action == 'PAUSE_SHORTS':
            log.info("MAGI PAUSE_SHORTS — cancelling sell orders")

    def update_inventory(self, price: float):
        """Sync inventory state to database."""
        if self.paper:
            xrp = self.paper_inventory['xrp']
            usd = self.paper_inventory['usd']
        else:
            xrp, usd = self._get_live_balances()

        net_usd = xrp * price
        skew = net_usd / MAX_INVENTORY_USD if MAX_INVENTORY_USD > 0 else 0
        upsert_inventory(xrp, usd, net_usd, skew)
        return xrp, usd, net_usd, skew

    def _get_live_balances(self):
        """Fetch live XRP and USD balances from the exchange."""
        return self.exchange.get_balances()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s')

    engine = GridEngine(paper=True)
    print(f"Current price: {engine.get_current_price()}")
    engine.initialise_grid()

    price = engine.get_current_price()
    if price:
        test_price = price * 0.995
        fills = engine.simulate_fills(test_price)
        print(f"Simulated fills at {test_price:.4f}: {len(fills)}")
        engine.update_inventory(price)
        print(f"Paper inventory: {engine.paper_inventory}")
