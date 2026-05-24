import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
import requests
from config import (
    COINBASE_API_KEY, COINBASE_API_SECRET,
    SYMBOL, GRID_LEVELS_DEFAULT,
    TAKER_FEE, MAKER_FEE, MAX_INVENTORY_USD, ORDER_SIZE_XRP,
    DB_PATH, EXCHANGE,
    LIVE_CONFIRMATION_FILE, LIVE_CONFIRMATION_TOKEN,
    LIVE_CONFIRMATION_ENV_VAR, LIVE_CONFIRMATION_ENV_VALUE,
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

        if self.paper:
            log.info("Two-factor live gate: not consulted (paper mode active)")
            log.info("Paper mode active — no real orders will be placed")
        else:
            gate_1_env = os.environ.get(LIVE_CONFIRMATION_ENV_VAR) == LIVE_CONFIRMATION_ENV_VALUE
            gate_2_file = os.path.isfile(LIVE_CONFIRMATION_FILE)
            if gate_2_file:
                gate_3_token = open(LIVE_CONFIRMATION_FILE).read() == LIVE_CONFIRMATION_TOKEN
                gate_3_label = "PASS" if gate_3_token else "FAIL (content mismatch)"
            else:
                gate_3_token = False
                gate_3_label = "skipped — file does not exist"
            log.info(f"Live gate — gate_1_env ({LIVE_CONFIRMATION_ENV_VAR}={LIVE_CONFIRMATION_ENV_VALUE}): {'PASS' if gate_1_env else 'FAIL'}")
            log.info(f"Live gate — gate_2_file ({LIVE_CONFIRMATION_FILE}): {'PASS' if gate_2_file else 'FAIL'}")
            log.info(f"Live gate — gate_3_token: {gate_3_label}")
            if gate_1_env and gate_2_file and gate_3_token:
                log.warning("LIVE MODE ACTIVE — all three confirmation gates passed — real orders will be placed")
            else:
                failed = [name for name, passed in [
                    ("gate_1_env", gate_1_env),
                    ("gate_2_file", gate_2_file),
                    ("gate_3_token", gate_3_token),
                ] if not passed]
                log.error(f"Live mode refused — failed gates: {', '.join(failed)}")
                raise RuntimeError("Live mode refused — confirmation gates not satisfied. See log for details.")

    # --- State loading ---

    def load_state(self):
        """Load shadow simulator and level_count from DB. Call after init_db()."""
        from config import GRID_LEVEL_VARIANTS, SPACING_VARIANTS
        from grid.shadow_simulator import ShadowSimulator
        self.shadow_sim = ShadowSimulator(GRID_LEVEL_VARIANTS, SPACING_VARIANTS)
        self.shadow_sim.load_from_db()
        # Restore _last_shadow_level_count from DB so restarts don't
        # trigger unnecessary shadow full-rebuilds.
        try:
            from database import get_active_shadow_level_count
            persisted_lc = get_active_shadow_level_count()
            if persisted_lc is not None:
                self._last_shadow_level_count = persisted_lc
                log.info(
                    f"Shadow level count restored from DB: "
                    f"_last_shadow_level_count={persisted_lc}"
                )
        except Exception as e:
            log.warning(f"Could not restore shadow level count: {e}")
        grid_state = get_current_grid_state()
        if grid_state and grid_state.get('levels'):
            self.level_count = grid_state['levels']
        # If any variant has empty resting_orders after loading from DB (e.g. service
        # restart with restored paper_orders that skips initialise_grid), rebuild all
        # shadow variants now using the current grid_state so process_tick works immediately.
        if any(not sg.resting_orders for sg in self.shadow_sim.variants.values()):
            _gs_centre = grid_state.get('centre_price') if grid_state else None
            _gs_spacing = grid_state.get('spacing_pct') if grid_state else None
            if _gs_centre and _gs_spacing:
                self.shadow_sim.rebuild(_gs_centre, _gs_spacing)
                self._last_shadow_level_count = self.level_count
                log.info(
                    f"Shadow sim rebuilt in load_state — resting_orders were empty "
                    f"(centre={_gs_centre}, spacing={_gs_spacing})"
                )
        # Persist current variant state (with populated resting_orders) to DB.
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
                    from magi.portfolio import compute_portfolio_metrics
                    price = self.get_current_price() or 0.0
                    pm = compute_portfolio_metrics(xrp_real, usd_real, price)
                    upsert_inventory(
                        xrp_real, usd_real,
                        pm["xrp_value_usd"], pm["allocation_skew"],
                    )
                else:
                    log.warning("[PAPER RESET] Kraken returned zero balances; falling back to defaults")
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
        """Evaluate shadow variants; switch level_count and rebuild if warranted.

        With the 24-variant (lc, sp) shadow grid, this function preserves the
        original "switch level_count only" semantics by filtering variants to
        the current live spacing and picking the best level_count among them.
        Cross-spacing switching is deferred — see 02_NEXT_BUILD_TASKS.md for
        the Melchior RECONFIGURE → orchestrator-mapping follow-on.
        """
        if not self.shadow_sim:
            return None
        from config import (GRID_SWITCH_THRESHOLD_PCT, GRID_SWITCH_MIN_FILLS,
                            GRID_SWITCH_MIN_HOURS)
        eval_data = self.shadow_sim.get_evaluation()
        variants = eval_data['variants']
        if not variants:
            return None

        # Determine current live spacing — filter variants to that band.
        grid_state = get_current_grid_state() or {}
        live_spacing = grid_state.get('spacing_pct')
        if live_spacing is None:
            log.info("Shadow eval: no live spacing in grid_state — no switch")
            return None
        # Match live spacing to nearest variant spacing for safety.
        same_spacing = [v for v in variants
                        if abs(v['spacing_pct'] - live_spacing) < 1e-9]
        if not same_spacing:
            log.info(f"Shadow eval: no shadow variant matches live spacing "
                     f"{live_spacing} — no switch")
            return None

        current_stats = next(
            (v for v in same_spacing if v['level_count'] == self.level_count),
            {}
        )
        current_pnl = current_stats.get('rolling_pnl_pct', 0.0)
        current_fills = current_stats.get('fills', 0)

        # Gate 1: fills count on current variant
        if current_fills < GRID_SWITCH_MIN_FILLS:
            log.info(f"Shadow eval: insufficient fills "
                     f"(current lc={self.level_count} fills={current_fills} "
                     f"< min={GRID_SWITCH_MIN_FILLS}) — no switch")
            return None

        best = max(same_spacing, key=lambda v: v['rolling_pnl_pct'])
        best_lc = best['level_count']
        best_pnl = best['rolling_pnl_pct']
        best_fills = best['fills']

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
        current_sg = self.shadow_sim.variants.get((self.level_count, live_spacing))
        best_sg = self.shadow_sim.variants.get((best_lc, live_spacing))
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
        if not grid_state:
            log.info(
                "Shadow eval: no live grid_state — skipping rebuild "
                "(no static spacing default; first-boot path owns this)"
            )
            return None
        centre = grid_state['centre_price']
        spacing = grid_state['spacing_pct']
        if centre is None or spacing is None or spacing <= 0:
            log.warning(
                "Shadow eval: live grid_state missing centre/spacing "
                "(centre=%r spacing=%r) — skipping rebuild",
                centre, spacing,
            )
            return None

        # Audit trail row before rebuild
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
                           levels: int, xrp_held: float, usd_held: float,
                           sell_level_bias: float = 1.0,
                           buy_level_bias: float = 1.0) -> list:
        """Build grid price levels trimmed to what current holdings can cover."""
        half = levels // 2

        # Asymmetric level distribution based on inventory skew
        # skew < 0 = USD-heavy → need more buys to reaccumulate XRP
        # skew > 0 = XRP-heavy → need more sells to release XRP
        # Skew range: -1 to +1. Neutral = 0.
        # Thresholds: mild (±0.2), moderate (±0.4), heavy (±0.6+)

        skew = (xrp_held * centre - usd_held) / (xrp_held * centre + usd_held) \
               if (xrp_held * centre + usd_held) > 0 else 0.0

        if skew < -0.4:      # Heavily USD-heavy — need lots more buys
            buy_ratio = 0.65
        elif skew < -0.2:    # Mildly USD-heavy
            buy_ratio = 0.55
        elif skew > 0.4:     # Heavily XRP-heavy — need lots more sells
            buy_ratio = 0.35
        elif skew > 0.2:     # Mildly XRP-heavy
            buy_ratio = 0.45
        else:                # Balanced — symmetric
            buy_ratio = 0.50

        target_buy_count = max(1, round(levels * buy_ratio))
        target_sell_count = max(1, levels - target_buy_count)

        if abs(buy_ratio - 0.5) > 0.01:
            log.info(
                f"Asymmetric grid: skew={skew:.3f} → "
                f"{target_buy_count} buys / {target_sell_count} sells "
                f"(ratio {buy_ratio:.2f})"
            )

        # Apply Melchior's regime-aware level bias on top of inventory skew.
        # Per the geometry contract, bias scales target_buy_count and the
        # remainder fills target_sell_count so total stays at `levels`.
        # sell_level_bias is accepted in the signature for symmetry but does
        # not affect level counts under the buy-only-scaling rule.
        if abs(buy_level_bias - 1.0) > 0.01 or abs(sell_level_bias - 1.0) > 0.01:
            pre_bias_buy = target_buy_count
            pre_bias_sell = target_sell_count
            target_buy_count = max(1, round(target_buy_count * buy_level_bias))
            target_sell_count = max(1, levels - target_buy_count)
            log.info(
                f"Level bias applied: buy_bias={buy_level_bias:.2f} "
                f"sell_bias={sell_level_bias:.2f} → "
                f"buys {pre_bias_buy}→{target_buy_count}, "
                f"sells {pre_bias_sell}→{target_sell_count}"
            )

        sell_size = self.compute_order_size(centre, 'sell', target_sell_count)
        buy_size = self.compute_order_size(centre, 'buy', target_buy_count)

        # Sell ladder: walk nearest-to-centre outward, stop when XRP runs out
        sell_levels = []
        cumulative_xrp = 0.0
        for i in range(1, target_sell_count + 1):
            if cumulative_xrp + sell_size > xrp_held:
                break
            price = round(centre * (1 + i * spacing_pct), 5)
            sell_levels.append({'price': price, 'side': 'sell', 'level': i, 'size': sell_size})
            cumulative_xrp += sell_size

        # Buy ladder: walk nearest-to-centre outward, stop when USD runs out
        buy_levels = []
        cumulative_usd = 0.0
        for i in range(1, target_buy_count + 1):
            level_price = round(centre * (1 - i * spacing_pct), 5)
            cost = buy_size * level_price
            if cumulative_usd + cost > usd_held:
                break
            buy_levels.append({'price': level_price, 'side': 'buy', 'level': -i, 'size': buy_size})
            cumulative_usd += cost

        return buy_levels + sell_levels

    def compute_order_size(self, centre: float, side: str,
                            target_count: int) -> float:
        """
        Per-order size is FIXED at ORDER_SIZE_XRP (the Kraken XRP minimum),
        independent of holdings or level count. Every grid order is exactly
        this size and never exceeds it — operator directive 2026-05-24.

        Returns 0.0 when the side has no levels to place (target_count <= 0),
        or when centre is invalid for a buy. Holdings sufficiency is enforced
        downstream, not here: build_grid_levels() stops walking a ladder when
        the next order's size/cost would exceed remaining holdings, and
        _execute_anchor() aborts when the fixed size exceeds available
        inventory. Both buy and sell sides return the same size, so grid
        round trips match 1:1 under FIFO.

        Prior model (removed): divided total holdings across target_count
        levels, floored at the Kraken min and capped at half the side's
        inventory. On a small book that produced 14-24 XRP orders (whole- or
        half-position fills). To deploy more capital now, raise the level
        count (more orders of this size), not the order size.
        """
        if target_count <= 0:
            return 0.0
        if side == 'buy' and centre <= 0:
            return 0.0
        return ORDER_SIZE_XRP

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
                log.warning(f"Refusing buy order — price {price} above market {current_price}")
                order['status'] = 'rejected'
                return order
            if side == 'sell' and price < current_price * 0.999:
                log.warning(f"Refusing sell order — price {price} below market {current_price}")
                log.warning(
                    f"Sell order rejected — price {price:.5f} below sanity threshold "
                    f"{current_price * 0.999:.5f} (current={current_price:.5f})"
                )
                order['status'] = 'rejected'
                return order

        # Concentration cap — directional backstop above Balthasar's HALT (±0.85).
        # Reads allocation_skew (stored in the legacy-named inventory_skew column
        # per the 2026-05-06 risk-math reframe). Blocks buys when XRP-heavy
        # (skew > +0.90) and sells when USD-heavy (skew < -0.90). In normal
        # operation Balthasar HALTs at ±0.85 first; this is a deterministic
        # backstop only.
        from database import get_latest_inventory
        inv = get_latest_inventory() or {}
        skew = inv.get('inventory_skew', 0) or 0
        if side == 'buy' and skew > 0.90:
            log.error(f"Concentration cap reached: skew={skew:+.3f} > +0.90 — refusing buy")
            order['status'] = 'rejected_concentration'
            return order
        if side == 'sell' and skew < -0.90:
            log.error(f"Concentration cap reached: skew={skew:+.3f} < -0.90 — refusing sell")
            order['status'] = 'rejected_concentration'
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

        # Live order placement. Persist the arm to grid_orders keyed by the
        # Kraken txid AND mirror it into self.paper_orders (the in-memory book
        # used in both modes) so reconcile_live_fills_from_kraken() and the
        # ONE_GRID invariant can see it. Without this the live fill-reconciler
        # would have no row to mark when the order closes.
        result = self.exchange.place_order(side, price, size, order_id)
        order['order_id'] = result['order_id']
        order['status'] = result['status']
        if result['status'] in ('open', 'filled'):
            txid = result['order_id']
            try:
                insert_grid_order(
                    timestamp=order['timestamp'],
                    order_id=txid,
                    side=side,
                    price=price,
                    size=size,
                    status='open',
                    fee=0.0,
                )
            except Exception as e:
                log.warning(f"Live order persist failed for {txid}: {e}")
            self.paper_orders[txid] = {
                'order_id': txid,
                'side': side,
                'price': price,
                'size': size,
                'status': 'open',
                'timestamp': order['timestamp'],
            }
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

    def simulate_fills(self, current_price: float,
                       candle_high: float = None,
                       candle_low: float = None):
        """Paper mode: check which resting orders would be filled at current price."""
        # TEMP DEBUG (candle-completeness verification) — log inputs before
        # walking orders. Remove once the get_latest_candle_hl fix is confirmed
        # in production.
        open_count = sum(
            1 for o in self.paper_orders.values() if o.get('status') == 'open'
        )
        log.info(
            f"[SIMFILLS DEBUG] price={current_price} "
            f"candle_high={candle_high} candle_low={candle_low} "
            f"open_orders={open_count}"
        )
        filled = []
        for order_id, order in list(self.paper_orders.items()):
            if order['status'] != 'open':
                continue
            filled_flag = False
            check_low = candle_low if candle_low is not None else current_price
            check_high = candle_high if candle_high is not None else current_price
            if order['side'] == 'buy' and check_low <= order['price']:
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
            elif order['side'] == 'sell' and check_high >= order['price']:
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

        if filled and self.shadow_sim:
            try:
                self.shadow_sim.persist_all()
            except Exception as e:
                log.warning(f"Shadow persist after fill failed: {e}")

        return filled

    def reconcile_live_fills_from_kraken(self):
        """Live-mode fill detection (the live counterpart to simulate_fills).

        Queries Kraken ClosedOrders, finds any of OUR resting orders (tracked
        in self.paper_orders by txid) that have closed, marks the matching
        grid_orders row 'filled' with the ACTUAL fill price and fee reported
        by Kraken, then syncs inventory from exchange.get_balances() — which
        is truth-of-record. The per-order marking is for PnL/dashboard
        bookkeeping; the balance sync is the safety-critical part.

        Idempotent: an order already marked filled/cancelled in our book is
        no longer 'open', so it is never re-processed. Returns the list of
        newly-filled order dicts.
        """
        our_open = {
            oid: o for oid, o in self.paper_orders.items()
            if o.get('status') == 'open'
        }
        if not our_open:
            return []
        try:
            # Look back 1h of closes — far beyond the ~10-min reconcile
            # cadence. Overlap is harmless: we only act on orders still
            # 'open' in our book, so re-seeing a handled order is a no-op.
            closed = self.exchange.get_closed_orders(start=time.time() - 3600)
        except Exception as e:
            log.error(f"Live reconcile: ClosedOrders fetch failed: {e}")
            return []

        filled = []
        now_iso = datetime.utcnow().isoformat()
        for oid, order in our_open.items():
            info = closed.get(oid)
            if not info:
                continue  # still open on Kraken (or outside the window)
            status = info.get('status')
            if status != 'closed':
                if status in ('canceled', 'expired'):
                    # Terminal without (full) execution — drop from the book
                    # so it isn't reconciled forever. Any partial fill is
                    # captured by the get_balances() sync regardless.
                    order['status'] = 'cancelled'
                    try:
                        update_grid_order_status(oid, 'cancelled', filled_at=now_iso)
                    except Exception as e:
                        log.warning(f"Live reconcile: cancel-mark failed for {oid}: {e}")
                continue
            filled_vol = float(info.get('vol_exec') or 0.0)
            fill_price = float(info.get('price') or 0.0)
            fee_usd = float(info.get('fee') or 0.0)
            if filled_vol <= 0 or fill_price <= 0:
                order['status'] = 'cancelled'
                try:
                    update_grid_order_status(oid, 'cancelled', filled_at=now_iso)
                except Exception as e:
                    log.warning(f"Live reconcile: zero-fill mark failed for {oid}: {e}")
                continue
            order['status'] = 'filled'
            order['fill_price'] = fill_price
            order['fee'] = fee_usd
            try:
                update_grid_order_status(
                    oid, 'filled',
                    filled_at=now_iso,
                    fill_price=fill_price,
                    fee=fee_usd,
                )
            except Exception as e:
                log.warning(f"Live reconcile: fill-mark failed for {oid}: {e}")
            filled.append(order)
            log.info(
                "[LIVE FILL] %s %s XRP @ %s fee=%.4f (txid=%s)",
                order['side'].upper(), filled_vol, fill_price, fee_usd, oid,
            )

        if filled:
            # Inventory truth-of-record: update_inventory() in live mode pulls
            # fresh balances from Kraken, so this is correct regardless of the
            # per-order fee/vol math above.
            try:
                price = self.get_current_price()
                if price:
                    self.update_inventory(price)
            except Exception as e:
                log.error(f"Live reconcile: update_inventory failed: {e}")
            if self.shadow_sim:
                try:
                    self.shadow_sim.persist_all()
                except Exception as e:
                    log.warning(f"Live reconcile: shadow persist failed: {e}")
        return filled

    # --- Grid Lifecycle ---

    def _assert_one_grid_invariant(self, context: str,
                                     expected_open: int = 0) -> bool:
        """ONE GRID INVARIANT — the bot holds at most one active grid at
        a time. Verified at key lifecycle transitions:

          - After cancel_all_orders before placing an anchor: expected_open=0
          - After initialise_grid completes: expected_open == placed count

        Logs an ERROR if the open-order count doesn't match expected. This
        is a tripwire, not a fix; if it ever fires, something has either
        leaked orders between rebuilds, raced two MAGI cycles, or stale
        rows were restored from DB. The accumulated state is dangerous
        (multi-generation grids with overlapping levels, double inventory
        commitments, etc.) and should be investigated before continuing.
        """
        open_count = sum(
            1 for o in self.paper_orders.values()
            if o.get('status') == 'open'
        )
        if open_count != expected_open:
            log.error(
                "ONE_GRID INVARIANT VIOLATION at %s: expected %d open "
                "orders, found %d. Bot must hold at most one active grid; "
                "multi-grid state is unsafe (overlapping levels, double "
                "inventory commit, race conditions). Investigate.",
                context, expected_open, open_count,
            )
            return False
        return True

    def _execute_anchor(self, current_price: float) -> Optional[float]:
        """Execute a single market order at current spot — the 'anchor' that
        gates the rest of the grid. Direction follows inventory skew: SELL
        if XRP-heavy, BUY if USD-heavy. Sized as one rung of the eventual
        ladder so its capital weight matches the arms.

        Returns the fill price (current spot in paper mode), or None if
        the anchor couldn't be sized or executed. Arms are placed only
        after this returns a real price — the rest of the grid stays
        theoretical until the anchor confirms an actual trade can happen.

        In paper mode the fill is synchronous at current_price and TAKER_FEE
        is charged (market-order semantics). Live-mode market execution is
        not implemented yet; refuses with an error.
        """
        # ONE GRID INVARIANT — anchor must execute from a clean book.
        # initialise_grid() calls cancel_all_orders() before us; this is
        # the post-cancel checkpoint that anything stuck open would trip.
        self._assert_one_grid_invariant(
            context="_execute_anchor entry", expected_open=0,
        )
        xrp_held = float(self.paper_inventory.get('xrp') or 0.0)
        usd_held = float(self.paper_inventory.get('usd') or 0.0)
        xrp_value = xrp_held * current_price
        total = xrp_value + usd_held
        if total <= 0:
            log.error("Anchor: empty inventory — cannot execute")
            return None

        skew = (xrp_value - usd_held) / total
        side = 'sell' if skew > 0 else 'buy'

        # Size matches a single rung of the eventual ladder. compute_order_size
        # divides the chosen side's inventory by the per-side rung count, so the
        # anchor consumes ~1/N of that side's capital — same as any arm.
        target_count_per_side = max(1, self.level_count // 2)
        size = self.compute_order_size(current_price, side, target_count_per_side)
        if size <= 0:
            log.error("Anchor: compute_order_size returned %r — cannot execute", size)
            return None

        # Affordability check before charging the inventory.
        if side == 'buy':
            cost_usd = size * current_price
            if cost_usd > usd_held:
                log.error(
                    "Anchor BUY: cost $%.4f > usd_held $%.4f — cannot execute",
                    cost_usd, usd_held,
                )
                return None
        else:
            if size > xrp_held:
                log.error(
                    "Anchor SELL: size %.4f > xrp_held %.4f — cannot execute",
                    size, xrp_held,
                )
                return None

        order_id = str(uuid.uuid4())[:8]
        fee = size * current_price * TAKER_FEE
        now_iso = datetime.utcnow().isoformat()

        if self.paper:
            # Synchronous paper fill at current spot. Mirrors what a Kraken
            # market order would do in seconds, minus slippage.
            if side == 'buy':
                self.paper_inventory['xrp'] = xrp_held + size
                self.paper_inventory['usd'] = usd_held - (size * current_price + fee)
            else:
                self.paper_inventory['xrp'] = xrp_held - size
                self.paper_inventory['usd'] = usd_held + (size * current_price - fee)
            try:
                insert_grid_order(
                    timestamp=now_iso,
                    order_id=order_id,
                    side=side,
                    price=current_price,
                    size=size,
                    status='open',
                    fee=0.0,
                )
                update_grid_order_status(
                    order_id, 'filled',
                    filled_at=now_iso,
                    fill_price=current_price,
                    fee=fee,
                )
            except Exception as e:
                log.warning(f"Anchor: failed to persist grid_orders row: {e}")
            # Persist inventory immediately so a process restart between
            # anchor fill and the next observer cycle doesn't load stale
            # pre-anchor balances. update_inventory() reads from
            # self.paper_inventory, which we just mutated above.
            try:
                self.update_inventory(current_price)
            except Exception as e:
                log.warning(f"Anchor: update_inventory() failed: {e}")
            log.info(
                "[ANCHOR FILL] %s %s XRP @ %s fee=%.4f (TAKER) — id=%s "
                "(skew %.3f → %s; post-anchor: xrp=%.4f usd=$%.2f)",
                side.upper(), size, current_price, fee, order_id, skew, side,
                self.paper_inventory['xrp'], self.paper_inventory['usd'],
            )
            return float(current_price)

        # Live-mode market execution. The two-factor gate at __init__ time
        # already blocked construction unless all three confirmation gates
        # passed, so reaching this branch means the operator has authorised
        # real-money orders. The anchor places a market taker; arms are
        # built around the actual fill price by initialise_grid().
        try:
            placed = self.exchange.add_market_order(
                side, size, client_order_id=order_id,
            )
        except Exception as e:
            log.error(f"Anchor: AddOrder failed: {e}")
            return None
        txid = placed.get("order_id")
        if not txid:
            log.error(
                "Anchor: add_market_order returned no txid "
                "(validate mode?) — refusing to advance: %r", placed,
            )
            return None

        # Poll QueryOrders for fill confirmation. Market orders on Kraken
        # normally close within milliseconds; 30s is generous and aborts
        # cleanly if the book is too thin to clear (canceled/expired).
        deadline = time.time() + 30
        order_info: dict = {}
        while time.time() < deadline:
            try:
                order_info = self.exchange.query_order(txid)
            except Exception as e:
                log.warning(f"Anchor: QueryOrders error {e} — retrying")
                time.sleep(0.5)
                continue
            if order_info.get("status") in ("closed", "canceled", "expired"):
                break
            time.sleep(0.5)
        else:
            log.error(
                "Anchor: txid=%s did not reach terminal status within 30s "
                "— last status=%r", txid, order_info.get("status"),
            )
            return None

        if order_info.get("status") != "closed":
            log.error(
                "Anchor: txid=%s terminated %s, not filled — vol_exec=%s",
                txid, order_info.get("status"), order_info.get("vol_exec"),
            )
            return None

        filled_vol = float(order_info.get("vol_exec") or 0.0)
        fill_price = float(order_info.get("price") or 0.0)
        fee_usd = float(order_info.get("fee") or 0.0)
        if filled_vol <= 0 or fill_price <= 0:
            log.error(
                "Anchor: closed but vol_exec=%s price=%s — refusing to "
                "advance", filled_vol, fill_price,
            )
            return None

        # Persist the anchor as a grid_orders row, then reconcile inventory
        # from Kraken Balance (truth-of-record). update_inventory(price) in
        # live mode calls self.exchange.get_balances() under the hood —
        # avoids the fee-currency assumption that a manual
        # vol_exec/price/fee recomputation would carry.
        try:
            insert_grid_order(
                timestamp=now_iso, order_id=txid, side=side,
                price=fill_price, size=filled_vol,
                status='open', fee=0.0,
            )
            update_grid_order_status(
                txid, 'filled',
                filled_at=datetime.utcnow().isoformat(),
                fill_price=fill_price, fee=fee_usd,
            )
        except Exception as e:
            log.warning(f"Anchor: failed to persist grid_orders row: {e}")
        try:
            self.update_inventory(fill_price)
        except Exception as e:
            log.error(f"Anchor: live update_inventory failed: {e}")

        try:
            xrp_now, usd_now = self.exchange.get_balances()
        except Exception as e:
            log.warning(f"Anchor: post-fill balance fetch failed: {e}")
            xrp_now, usd_now = float('nan'), float('nan')
        log.info(
            "[ANCHOR LIVE] %s requested_size=%s filled_size=%.4f "
            "fill_price=%s fee_usd=%.4f (TAKER) — txid=%s "
            "(skew %.3f → %s; post-anchor: xrp=%.4f usd=$%.2f)",
            side.upper(), size, filled_vol, fill_price, fee_usd, txid,
            skew, side, xrp_now, usd_now,
        )
        return float(fill_price)

    def initialise_grid(self, centre: Optional[float] = None,
                         spacing_pct: Optional[float] = None,
                         sell_level_bias: float = 1.0,
                         buy_level_bias: float = 1.0):
        """Set up the full grid from scratch via the anchor-then-arms
        mechanic.

        Stage 1: execute a market-order ANCHOR at current spot. Direction
        follows inventory skew. Sized as one rung. In paper mode it fills
        synchronously; in live mode this is a real market order (taker fee).
        If the anchor doesn't fill, NO arm orders are placed — the grid
        stays theoretical until a real trade has happened.

        Stage 2: build the LIMIT arm orders around the anchor's actual
        fill price (which is current spot, not the `centre` parameter —
        the anchor sets the centre by where it executes). Spacing,
        level_count, biases come from the caller (MAGI geometry or
        first-boot scorer).

        spacing_pct is required. The engine no longer carries a static
        spacing default. The `centre` parameter is accepted for backward
        compat with callers, but is informational only under the anchor-
        first model — actual centre comes from the anchor fill.
        """
        if centre is None:
            centre = self.get_current_price()
            if not centre:
                log.error("Cannot initialise grid — no price available")
                return False

        if spacing_pct is None or spacing_pct <= 0:
            log.error(
                "Cannot initialise grid — spacing_pct is required and "
                "must be > 0; caller passed %r. The engine no longer "
                "carries a static default. Route through scorer or "
                "orchestrator geometry path instead.",
                spacing_pct,
            )
            return False

        log.info(
            f"Initialising grid (anchor-first) — target centre={centre} "
            f"spacing={spacing_pct*100:.2f}% levels={self.level_count}"
        )
        self.cancel_all_orders()

        # Stage 1: anchor. Use current spot, not the target centre, because
        # the anchor is an actual trade and must clear against the live book.
        current_spot = self.get_current_price() or centre
        anchor_fill_price = self._execute_anchor(current_spot)
        if anchor_fill_price is None:
            log.error(
                "Initialise_grid: anchor execution failed — NO arm orders "
                "placed. Grid stays theoretical until next attempt."
            )
            insert_grid_state(
                current_spot, spacing_pct, self.level_count,
                notes="Anchor failed — no arms placed (grid theoretical)",
            )
            return False

        # Stage 2: arms around the actual fill price, using post-anchor
        # inventory.
        arm_centre = anchor_fill_price
        log.info(
            f"Anchor filled at {arm_centre} — placing arms at spacing="
            f"{spacing_pct*100:.2f}% levels={self.level_count}"
        )

        # Re-export the loop-local `centre` so the rest of this method (the
        # grid_state insert, shadow sim update, record_grid_config) anchors
        # to the actual fill price, not the caller-supplied target.
        centre = arm_centre

        levels = self.build_grid_levels(
            arm_centre, spacing_pct, self.level_count,
            xrp_held=self.paper_inventory['xrp'],
            usd_held=self.paper_inventory['usd'],
            sell_level_bias=sell_level_bias,
            buy_level_bias=buy_level_bias,
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

        placed = 0
        for level in levels:
            order = self.place_order(level['side'], level['price'], level['size'])
            if order['status'] in ('open', 'filled'):
                placed += 1
            time.sleep(0.1)  # Rate limit buffer

        insert_grid_state(centre, spacing_pct, self.level_count,
                          notes=f"Grid initialised — {placed} orders placed")
        log.info(f"Grid initialised — {placed}/{len(levels)} orders placed")

        # ONE GRID INVARIANT — after init, the open-order count must equal
        # the number we just placed. If higher: leaked orders from a prior
        # grid (cancel_all_orders failed somewhere, or another thread placed
        # orders concurrently). If lower: place_order silently rejected
        # some (e.g. sanity-check or concentration-cap). Either is worth
        # logging loudly.
        self._assert_one_grid_invariant(
            context="initialise_grid post-rebuild", expected_open=placed,
        )

        # Record this config for performance feedback
        try:
            from database import record_grid_config
            indicators = get_latest_indicators('1h')
            regime = indicators.get('vol_regime', 'UNKNOWN') if indicators else 'UNKNOWN'
            xrp_val = self.paper_inventory['xrp'] * centre
            total_val = xrp_val + self.paper_inventory['usd']
            skew_start_val = (xrp_val / total_val) if total_val > 0 else 0.0
            record_grid_config(
                centre_price=centre,
                spacing_pct=spacing_pct,
                buy_level_bias=buy_level_bias,
                sell_level_bias=sell_level_bias,
                levels=self.level_count,
                regime_at_config=regime,
                skew_start=skew_start_val,
            )
        except Exception as e:
            log.warning(f"grid_config_outcomes record failed: {e}")

        if self.shadow_sim:
            # Only do a full shadow rebuild when level count changes.
            # On spacing/centre changes, update centre metadata only —
            # preserves resting orders and prevents fill-count resets.
            needs_rebuild = (getattr(self, '_last_shadow_level_count', None) != self.level_count)
            if not needs_rebuild and any(
                not sg.resting_orders
                for sg in self.shadow_sim.variants.values()
            ):
                needs_rebuild = True
                log.warning(
                    "Shadow sim rebuild forced — resting_orders empty on "
                    "one or more variants"
                )
            if needs_rebuild:
                self.shadow_sim.rebuild(centre, spacing_pct)
                self._last_shadow_level_count = self.level_count
                log.info(
                    f"Shadow sim full rebuild — level count changed to "
                    f"{self.level_count}"
                )
            else:
                self.shadow_sim.update_centre(centre, spacing_pct)
                log.info(
                    f"Shadow sim centre update only — level count "
                    f"{self.level_count} unchanged, orders preserved"
                )

        return True

    def apply_magi_decision(self, consensus: dict):
        """Apply the MAGI consensus to the grid.

        Council structural inputs (regime_action, geometry_veto) flow
        through enforce_hard_rules upstream — by the time this method
        sees the consensus dict, grid_action has already been coerced
        to MAINTAIN if either field was non-permissive. This block is
        a defensive cross-check that satisfies the literal "engine
        reads consensus[...]" requirement and surfaces a warning if
        enforce_hard_rules somehow let a vetoed structural change
        through. Under normal operation this branch is a no-op."""
        grid_action = consensus.get('grid_action', 'MAINTAIN')
        risk_action = consensus.get('risk_action', 'CLEAR')
        regime_action = consensus.get('regime_action') or 'EXECUTE'
        geometry_veto = consensus.get('geometry_veto') or 'PROCEED'

        if grid_action in ('RECENTRE', 'TIGHTEN', 'WIDEN'):
            engine_council_tags = []
            if regime_action == 'DEFER_STRUCTURAL':
                engine_council_tags.append('[REGIME_DEFER]')
            elif regime_action == 'STAND_DOWN':
                engine_council_tags.append('[REGIME_STANDDOWN]')
            if geometry_veto == 'HOLD_GEOMETRY':
                engine_council_tags.append('[BALTHASAR_HOLD_GEOMETRY]')
            elif geometry_veto == 'RISK_BLOCK':
                engine_council_tags.append('[BALTHASAR_RISK_BLOCK]')
            if engine_council_tags:
                log.warning(
                    "engine-level council veto cross-check fired %s — "
                    "enforce_hard_rules should have caught this upstream. "
                    "Coercing %s to MAINTAIN.",
                    engine_council_tags, grid_action,
                )
                grid_action = 'MAINTAIN'

        if grid_action == 'HALT' or risk_action == 'HALT':
            log.warning("MAGI HALT — cancelling all orders")
            self.cancel_all_orders()
            return

        if grid_action == 'GRID_PAUSE':
            # Regime gate fired — cancel all orders and idle the grid.
            # Different from HALT: kill switch is not tripped, gate re-evaluates
            # next cycle and releases automatically when conditions no longer meet.
            cancelled = self.cancel_all_orders()
            cur = get_current_grid_state() or {}
            centre = cur.get('centre_price')
            spacing = cur.get('spacing_pct')
            levels = cur.get('levels', self.level_count)
            insert_grid_state(
                centre, spacing, levels,
                pause_longs=0, pause_shorts=0,
                notes=f"GRID_PAUSE — {consensus.get('reason', 'regime gate')}"
            )
            log.warning(
                f"GRID_PAUSE applied — {cancelled} orders cancelled. "
                f"Grid idle until regime gate releases."
            )
            return

        current_state = get_current_grid_state()
        if not current_state:
            log.warning("No grid state — initialising fresh")
            self.initialise_grid()
            return

        centre = current_state['centre_price']
        spacing = current_state['spacing_pct']

        rebuild_requested = grid_action in ('RECENTRE', 'TIGHTEN', 'WIDEN')
        guard_tripped = False

        if rebuild_requested:
            price = self.get_current_price() or centre
            target_count = self.level_count // 2
            order_size_xrp_sell = self.compute_order_size(price, 'sell', target_count)
            order_size_xrp_buy = self.compute_order_size(price, 'buy', target_count)
            order_size_usd_buy = order_size_xrp_buy * price
            xrp_held = self.paper_inventory['xrp']
            usd_held = self.paper_inventory['usd']

            if risk_action == 'PAUSE_LONGS' and xrp_held < order_size_xrp_sell:
                log.warning(
                    f"Empty-book guard: skipping {grid_action} — "
                    f"PAUSE_LONGS + xrp_held={xrp_held:.4f} < "
                    f"order_size_xrp_sell={order_size_xrp_sell:.4f}. Rebuild "
                    f"would produce 0 sells; buys would be cancelled "
                    f"by PAUSE_LONGS. Risk action still applied."
                )
                insert_grid_state(
                    centre, spacing, self.level_count,
                    notes=f"{grid_action} skipped by empty-book guard; "
                          f"risk={risk_action} applied"
                )
                guard_tripped = True
            elif risk_action == 'PAUSE_SHORTS' and usd_held < order_size_usd_buy:
                log.warning(
                    f"Empty-book guard: skipping {grid_action} — "
                    f"PAUSE_SHORTS + usd_held={usd_held:.2f} < "
                    f"order_size_usd_buy={order_size_usd_buy:.2f}. Rebuild "
                    f"would produce 0 buys; sells would be cancelled "
                    f"by PAUSE_SHORTS. Risk action still applied."
                )
                insert_grid_state(
                    centre, spacing, self.level_count,
                    notes=f"{grid_action} skipped by empty-book guard; "
                          f"risk={risk_action} applied"
                )
                guard_tripped = True

        if not guard_tripped:
            if grid_action in ('RECENTRE', 'TIGHTEN', 'WIDEN'):
                from config import MIN_GRID_SPACING_PCT, MAX_GRID_SPACING_PCT
                geometry = consensus.get('melchior_geometry') or {}
                geom_centre = geometry.get('centre_price')
                geom_spacing = geometry.get('target_spacing_pct')
                geom_levels = geometry.get('target_levels')
                sell_bias = float(geometry.get('sell_level_bias') or 1.0)
                buy_bias = float(geometry.get('buy_level_bias') or 1.0)
                # Clamp biases to documented Melchior contract range
                sell_bias = max(0.5, min(2.0, sell_bias))
                buy_bias = max(0.5, min(2.0, buy_bias))

                # Levels selection: adopt Melchior's target_levels when present
                # and within [4, 12]. The analytical scorer's variant set is
                # levels ∈ {5..10}; the clamp adds one step of slack on each
                # side so hard-rule fallbacks have headroom but can't ship an
                # absurd grid. When null, keep the existing self.level_count.
                LEVELS_MIN, LEVELS_MAX = 4, 12
                if (geom_levels is not None
                        and isinstance(geom_levels, int)
                        and geom_levels > 0):
                    clamped_lc = max(LEVELS_MIN, min(LEVELS_MAX, int(geom_levels)))
                    if clamped_lc != geom_levels:
                        log.info(
                            f"MAGI {grid_action} — levels from Melchior {geom_levels} "
                            f"clamped to {clamped_lc} "
                            f"(bounds {LEVELS_MIN}–{LEVELS_MAX})"
                        )
                    else:
                        log.info(
                            f"MAGI {grid_action} — levels from Melchior geometry: "
                            f"{clamped_lc} (was {self.level_count})"
                        )
                    self.level_count = clamped_lc
                else:
                    log.info(
                        f"MAGI {grid_action} — levels unchanged at "
                        f"{self.level_count} (no geom_levels in consensus)"
                    )

                current_price = self.get_current_price() or centre

                # Centre selection: prefer geometry if within ±10% of current price,
                # else fall back to current price, else existing centre.
                if (geom_centre is not None
                        and isinstance(geom_centre, (int, float))
                        and geom_centre > 0
                        and current_price > 0
                        and abs(geom_centre - current_price) / current_price <= 0.10):
                    new_centre = float(geom_centre)
                    log.info(
                        f"MAGI {grid_action} — centre from Melchior geometry: "
                        f"{new_centre} (current={current_price})"
                    )
                elif current_price:
                    new_centre = float(current_price)
                    log.info(
                        f"MAGI {grid_action} — centre fallback to current price "
                        f"{new_centre} (geometry centre={geom_centre} rejected or null)"
                    )
                else:
                    new_centre = float(centre)
                    log.info(
                        f"MAGI {grid_action} — centre fallback to existing {new_centre} "
                        f"(no current price available)"
                    )

                # Spacing selection: prefer agent/scorer geometry, clamp to
                # bounds. When geometry is missing/invalid, do NOT guess —
                # orchestrator rule #8 (GEOMETRY_INJECTED_FROM_SCORER) should
                # have injected scorer rank-1 before reaching here, or rule
                # #8's no-acceptable-variant branch should have forced
                # GRID_PAUSE upstream. If we hit this branch with null
                # geometry, something upstream failed; refuse the rebuild
                # rather than fabricate a spacing.
                if (geom_spacing is not None
                        and isinstance(geom_spacing, (int, float))
                        and geom_spacing > 0):
                    clamped = max(MIN_GRID_SPACING_PCT,
                                   min(MAX_GRID_SPACING_PCT, float(geom_spacing)))
                    if abs(clamped - geom_spacing) > 1e-9:
                        log.info(
                            f"MAGI {grid_action} — spacing from Melchior {geom_spacing} "
                            f"clamped to {clamped} "
                            f"(bounds {MIN_GRID_SPACING_PCT}–{MAX_GRID_SPACING_PCT})"
                        )
                    else:
                        log.info(
                            f"MAGI {grid_action} — spacing from Melchior geometry: "
                            f"{clamped}"
                        )
                    new_spacing = clamped
                else:
                    log.error(
                        "MAGI %s reached engine with null geometry "
                        "(geom_spacing=%r). Orchestrator rule #8 should "
                        "have injected scorer rank-1 or forced GRID_PAUSE. "
                        "Refusing to fabricate a spacing — leaving live "
                        "grid intact this cycle.",
                        grid_action, geom_spacing,
                    )
                    return  # exit apply_magi_decision without touching the book

                # Ensure centre is not so far below market that all sells will be rejected.
                # The sell sanity check rejects sells below current_price * 0.999, so
                # the outermost sell at (new_centre + new_spacing * levels//2) must clear
                # that threshold. Uses new_spacing (the spacing actually being applied),
                # not a global ceiling, so the check tracks whatever variant is shipped.
                max_sell_reach = new_centre * (1 + new_spacing * (self.level_count // 2))
                if max_sell_reach < current_price * 0.995:
                    adjusted_centre = current_price * (1 - new_spacing * (self.level_count // 4))
                    log.warning(
                        f"RECENTRE centre {new_centre:.4f} too far below market {current_price:.4f} "
                        f"— sells would all be rejected. Adjusting centre to {adjusted_centre:.4f}"
                    )
                    new_centre = adjusted_centre

                log.info(
                    f"MAGI geometry applied — centre={new_centre} spacing={new_spacing} "
                    f"buy_bias={buy_bias:.2f} sell_bias={sell_bias:.2f}"
                )
                self.initialise_grid(
                    centre=new_centre, spacing_pct=new_spacing,
                    sell_level_bias=sell_bias, buy_level_bias=buy_bias,
                )

            else:  # MAINTAIN
                log.info("MAGI MAINTAIN — no grid changes")

        # Refresh so pause rows reflect the post-action grid configuration.
        # RECENTRE/TIGHTEN/WIDEN each call initialise_grid() which writes a new
        # grid_state row — re-reading here ensures the pause row inherits the
        # correct (post-action) centre and spacing, not the pre-action values.
        post_action = get_current_grid_state() or current_state
        eff_centre = post_action['centre_price']
        eff_spacing = post_action['spacing_pct']
        eff_levels = post_action.get('levels', self.level_count)

        # Apply risk constraints
        if risk_action == 'PAUSE_LONGS':
            now_iso = datetime.utcnow().isoformat()
            pre_buys = sum(
                1 for o in self.paper_orders.values()
                if o.get('status') == 'open' and o.get('side') == 'buy'
            )
            cancelled = 0
            for order_id, order in list(self.paper_orders.items()):
                if order.get('status') == 'open' and order.get('side') == 'buy':
                    try:
                        update_grid_order_status(order_id, 'cancelled',
                                                 filled_at=now_iso)
                    except Exception as e:
                        log.warning(f"Failed to persist cancel for {order_id}: {e}")
                    order['status'] = 'cancelled'
                    cancelled += 1
            if cancelled:
                keys_to_remove = [oid for oid, o in self.paper_orders.items()
                                  if o.get('status') == 'cancelled']
                for k in keys_to_remove:
                    del self.paper_orders[k]
            if cancelled == 0 and pre_buys == 0:
                log.info(
                    "MAGI PAUSE_LONGS no-op — book already had 0 buy orders"
                )
            else:
                log.info(f"MAGI PAUSE_LONGS — cancelled {cancelled} open buy orders")
            if not self.paper:
                try:
                    live_cancelled = self.exchange.cancel_orders_by_side('buy')
                    log.info(f"MAGI PAUSE_LONGS [LIVE] — cancelled {live_cancelled} buy orders on exchange")
                except Exception as e:
                    log.error(f"MAGI PAUSE_LONGS [LIVE] — exchange cancellation failed: {e}")
            insert_grid_state(
                eff_centre, eff_spacing, eff_levels,
                pause_longs=1, pause_shorts=0,
                notes=f"PAUSE_LONGS applied — {cancelled} buys cancelled"
            )

        elif risk_action == 'PAUSE_SHORTS':
            now_iso = datetime.utcnow().isoformat()
            pre_sells = sum(
                1 for o in self.paper_orders.values()
                if o.get('status') == 'open' and o.get('side') == 'sell'
            )
            cancelled = 0
            for order_id, order in list(self.paper_orders.items()):
                if order.get('status') == 'open' and order.get('side') == 'sell':
                    try:
                        update_grid_order_status(order_id, 'cancelled',
                                                 filled_at=now_iso)
                    except Exception as e:
                        log.warning(f"Failed to persist cancel for {order_id}: {e}")
                    order['status'] = 'cancelled'
                    cancelled += 1
            if cancelled:
                keys_to_remove = [oid for oid, o in self.paper_orders.items()
                                  if o.get('status') == 'cancelled']
                for k in keys_to_remove:
                    del self.paper_orders[k]
            if cancelled == 0 and pre_sells == 0:
                log.info(
                    "MAGI PAUSE_SHORTS no-op — book already had 0 sell orders"
                )
            else:
                log.info(f"MAGI PAUSE_SHORTS — cancelled {cancelled} open sell orders")
            if not self.paper:
                try:
                    live_cancelled = self.exchange.cancel_orders_by_side('sell')
                    log.info(f"MAGI PAUSE_SHORTS [LIVE] — cancelled {live_cancelled} sell orders on exchange")
                except Exception as e:
                    log.error(f"MAGI PAUSE_SHORTS [LIVE] — exchange cancellation failed: {e}")
            insert_grid_state(
                eff_centre, eff_spacing, eff_levels,
                pause_longs=0, pause_shorts=1,
                notes=f"PAUSE_SHORTS applied — {cancelled} sells cancelled"
            )

        elif risk_action == 'CLEAR':
            insert_grid_state(
                eff_centre, eff_spacing, eff_levels,
                pause_longs=0, pause_shorts=0,
                notes="Risk CLEAR — pause flags reset"
            )

        # GRID INTEGRITY GUARD — defense-in-depth.
        # If the council + hard-rule layer somehow still produced a degenerate
        # book (zero buys or zero sells), emergency-rebuild at current price.
        # The orchestrator's [GRID_DEGENERATE] hard rule should handle this
        # earlier, but state can also become degenerate after PAUSE_LONGS /
        # PAUSE_SHORTS cancellations on a thin starting book.
        # Not invoked when HALT was applied (HALT cancels everything intentionally).
        if grid_action != 'HALT' and risk_action != 'HALT':
            post_buys = sum(
                1 for o in self.paper_orders.values()
                if o.get('status') == 'open' and o.get('side') == 'buy'
            )
            post_sells = sum(
                1 for o in self.paper_orders.values()
                if o.get('status') == 'open' and o.get('side') == 'sell'
            )
            if post_buys == 0 or post_sells == 0:
                rebuild_price = self.get_current_price()
                log.warning(
                    "GRID INTEGRITY: post-action book is degenerate "
                    "(buys=%d sells=%d) — emergency-rebuilding at %s",
                    post_buys, post_sells, rebuild_price,
                )
                if rebuild_price:
                    self.initialise_grid(centre=rebuild_price)
                else:
                    log.error(
                        "GRID INTEGRITY: cannot emergency-rebuild — "
                        "no current price available"
                    )

    def update_inventory(self, price: float):
        """Sync inventory state to database."""
        if self.paper:
            xrp = self.paper_inventory['xrp']
            usd = self.paper_inventory['usd']
        else:
            xrp, usd = self._get_live_balances()
            # self.paper_inventory is the engine's working in-memory book in
            # BOTH modes (the name is legacy). In live mode it MUST track real
            # balances, or downstream sizing runs against stale pre-trade
            # inventory. Concrete failure this fixes: _execute_anchor sells
            # ~half the XRP, then initialise_grid sizes the sell-arm ladder via
            # compute_order_size (which reads self.paper_inventory['xrp']) — if
            # that's still the pre-anchor balance the outer sell rungs get sized
            # too large and Kraken rejects them with EOrder:Insufficient funds,
            # leaving a lopsided grid. Sync here so every post-fill caller sees
            # truth-of-record.
            self.paper_inventory = {'xrp': float(xrp), 'usd': float(usd)}

        from magi.portfolio import compute_portfolio_metrics
        pm = compute_portfolio_metrics(xrp, usd, price)
        xrp_value_usd = pm["xrp_value_usd"]
        allocation_skew = pm["allocation_skew"]

        # net_usd column kept for backward compatibility; now stores xrp_value_usd
        # inventory_skew column kept for backward compatibility; now stores allocation_skew
        upsert_inventory(xrp, usd, xrp_value_usd, allocation_skew)
        return xrp, usd, xrp_value_usd, allocation_skew

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
