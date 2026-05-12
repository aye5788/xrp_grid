import time
import logging
import signal
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from database import init_db
from observer import poll_cycle
from magi.orchestrator import run_cycle
from grid.engine import GridEngine
from guardrails import check_all_guardrails

# observer.py installs a StreamHandler on the root logger at import time, making
# basicConfig() a no-op. Override explicitly so magi.log actually gets written.
_fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s — %(message)s')
_root = logging.getLogger()
_root.setLevel(logging.INFO)
for _h in _root.handlers[:]:
    _root.removeHandler(_h)
_fh = logging.FileHandler('/root/xrp_grid/magi.log')
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
_root.addHandler(_fh)
_root.addHandler(_sh)

log = logging.getLogger('scheduler')

# Schedule config (EST)
OBSERVER_INTERVAL_MINUTES = 60
MAGI_HOURS_EST = [9, 14]   # 9AM and 2PM EST

EST = ZoneInfo('America/New_York')

# Global engine instance
engine = GridEngine(paper=True)
running = True

# Track last stats recompute date
_last_recompute_date = None

def signal_handler(sig, frame):
    global running
    log.info("Shutdown signal received — stopping scheduler")
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def run_observer_cycle():
    """Run data collection cycle, shadow tick, and paper fill simulation."""
    log.info("--- OBSERVER CYCLE ---")
    try:
        poll_cycle()
    except Exception as e:
        log.error(f"Observer cycle error: {e}")

    try:
        price = engine.get_current_price()
        if price:
            engine.process_shadow_tick(price)
            if engine.paper:
                from database import get_current_grid_state, get_latest_candle_hl
                candle_high, candle_low = get_latest_candle_hl('1h')
                if candle_high and candle_low:
                    log.info(
                        f"Observer: simulating fills — price={price:.4f} "
                        f"candle_high={candle_high:.4f} candle_low={candle_low:.4f}"
                    )
                filled = engine.simulate_fills(
                    price,
                    candle_high=candle_high,
                    candle_low=candle_low
                )
                if filled:
                    log.info(f"Observer: {len(filled)} paper fills at {price:.4f}")
                    engine.update_inventory(price)

                    # Place replacement orders at the opposite side.
                    # A sell fill → replacement buy one spacing below fill price.
                    # A buy fill → replacement sell one spacing above fill price.
                    grid_state = get_current_grid_state()
                    spacing_pct = grid_state['spacing_pct'] if grid_state else None

                    if spacing_pct:
                        replacements = 0
                        for order in filled:
                            try:
                                if order['side'] == 'sell':
                                    replacement_price = round(
                                        order['price'] * (1 - spacing_pct), 5
                                    )
                                    replacement_side = 'buy'
                                else:
                                    replacement_price = round(
                                        order['price'] * (1 + spacing_pct), 5
                                    )
                                    replacement_side = 'sell'

                                result = engine.place_order(
                                    replacement_side,
                                    replacement_price,
                                    order['size']
                                )
                                if result.get('status') in ('open', 'filled'):
                                    replacements += 1
                                    log.info(
                                        f"[GRID REPLENISH] {order['side'].upper()} fill "
                                        f"@ {order['fill_price']:.4f} → "
                                        f"replacement {replacement_side.upper()} "
                                        f"@ {replacement_price:.4f}"
                                    )
                                else:
                                    log.warning(
                                        f"[GRID REPLENISH] Replacement order rejected: "
                                        f"status={result.get('status')} "
                                        f"side={replacement_side} "
                                        f"price={replacement_price:.4f}"
                                    )
                            except Exception as e:
                                log.warning(f"[GRID REPLENISH] Failed to place replacement: {e}")

                        log.info(f"Observer: {replacements}/{len(filled)} replacements placed")
                    else:
                        log.warning(
                            "[GRID REPLENISH] No grid state found — skipping "
                            "replacement orders"
                        )
    except Exception as e:
        log.error(f"Shadow tick error: {e}")


def run_magi_cycle(trigger='scheduled'):
    """Run full MAGI supervision cycle and apply to grid."""
    log.info(f"--- MAGI CYCLE (trigger={trigger}) ---")
    ok, failures = check_all_guardrails()
    if not ok:
        log.error(f"Guardrails blocked cycle: {failures}")
        try:
            engine.cancel_all_orders()
            log.warning("Orders cancelled due to guardrail failure")
        except Exception as e:
            log.error(f"Cancel-all failed: {e}")
        return
    try:
        result = run_cycle(trigger=trigger)
        if result:
            consensus = result['consensus']

            if consensus.get('grid_action') != 'HALT':
                try:
                    engine.evaluate_and_maybe_switch_levels()
                except Exception as e:
                    log.error(f"Shadow eval error: {e}")

            engine.apply_magi_decision(consensus)
            from database import mark_magi_decision_applied
            did = result.get('decision_id') if result else None
            if did is not None:
                try:
                    mark_magi_decision_applied(did)
                except Exception as e:
                    log.warning(f"Failed to mark decision {did} applied: {e}")
            price = engine.get_current_price()
            if price:
                engine.update_inventory(price)
            log.info(f"MAGI cycle complete — grid={consensus['grid_action']} risk={consensus['risk_action']}")
        else:
            log.warning("MAGI cycle returned no result")
    except Exception as e:
        log.error(f"MAGI cycle error: {e}")


def should_run_magi(now_est: datetime, last_magi_hour: int) -> bool:
    """Check if it's time for a MAGI cycle."""
    current_hour = now_est.hour
    if current_hour in MAGI_HOURS_EST and current_hour != last_magi_hour:
        return True
    return False


def main():
    global running

    log.info("========================================")
    log.info("MAGI XRP Grid Bot — Scheduler Starting")
    log.info("========================================")

    # Initialise database
    init_db()

    # Load engine state (shadow sim) after DB is ready
    engine.load_state()

    # Start internal IPC server for dashboard communication
    _ipc_thread = _threading.Thread(
        target=_start_internal_server, daemon=True
    )
    _ipc_thread.start()
    log.info("Internal IPC server started on localhost:5001")

    # Fund detection — only enforced when configured exchange is the trading exchange
    from config import EXCHANGE, MAX_INVENTORY_USD
    if EXCHANGE == "kraken":
        log.info("Running Kraken fund-detection check (XXRP + ZUSD only)...")
        try:
            xrp, usd = engine.exchange.get_balances()
            price = engine.exchange.get_current_price()
            if price is None or price <= 0:
                log.error("Cannot run fund detection — Kraken price unavailable")
                sys.exit(1)
            xrp_value_usd = xrp * price
            total_in_universe = xrp_value_usd + usd
            log.info(f"Kraken bot universe: {xrp:.4f} XRP (${xrp_value_usd:.2f}) + ${usd:.2f} USD = ${total_in_universe:.2f}")
            if total_in_universe < MAX_INVENTORY_USD:
                log.error(f"INSUFFICIENT FUNDS — bot universe ${total_in_universe:.2f} < required ${MAX_INVENTORY_USD:.2f}")
                log.error("Refusing to operate. Top up XRP or USD on Kraken and restart.")
                sys.exit(1)
            log.info(f"Fund detection passed — universe ${total_in_universe:.2f} >= ${MAX_INVENTORY_USD:.2f}")
        except SystemExit:
            raise
        except Exception as e:
            log.error(f"Fund detection check failed with exception: {e}")
            log.error("Refusing to operate until Kraken connectivity is verified.")
            sys.exit(1)

    # Run initial observer poll
    run_observer_cycle()

    # Initialise grid only if no orders were restored from DB.
    # If load_state() restored an existing order book, resume that book instead
    # of placing duplicates.
    if not engine.paper_orders:
        # Check pause flags before rebuilding — don't undo an active pause.
        # PAUSE_LONGS cancels all buy orders; if we rebuilt here, the pause
        # would be silently undone on every restart.
        from database import get_current_grid_state
        gs = get_current_grid_state() or {}
        if gs.get('pause_longs') or gs.get('pause_shorts'):
            log.info(
                f"Startup: pause_longs={gs.get('pause_longs')} "
                f"pause_shorts={gs.get('pause_shorts')} active — "
                f"skipping grid rebuild to preserve pause state"
            )
        else:
            log.info("No paper orders restored — initialising fresh grid on startup")
            engine.initialise_grid()
    else:
        log.info(f"Resumed {len(engine.paper_orders)} paper orders from DB — skipping fresh grid init")

    # Run initial MAGI cycle (debounced — skip if a cycle ran within 30 min)
    try:
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT timestamp FROM magi_decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        skip_startup = False
        if row:
            last_ts = datetime.fromisoformat(row['timestamp'])
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            age_min = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
            if age_min < 30:
                log.info(
                    f"Skipping startup MAGI — last cycle was {age_min:.1f} "
                    f"minutes ago (< 30 min debounce)"
                )
                skip_startup = True
        if not skip_startup:
            run_magi_cycle(trigger='startup')
    except Exception as e:
        log.warning(f"Startup debounce check failed, running cycle anyway: {e}")
        run_magi_cycle(trigger='startup')

    last_observer_time = datetime.now(timezone.utc)

    # Initialize from DB to avoid duplicate cycle on restart.
    # If a cycle already ran in the current EST hour, don't re-fire.
    try:
        from database import get_recent_magi_decisions
        import pytz
        recent = get_recent_magi_decisions(limit=1)
        if recent:
            last_ts = recent[0].get('timestamp', '')
            est = pytz.timezone('US/Eastern')
            last_dt = datetime.fromisoformat(last_ts).replace(
                tzinfo=timezone.utc).astimezone(est)
            now_est = datetime.now(timezone.utc).astimezone(est)
            # If last decision was in the current calendar hour,
            # mark that hour as done
            if (last_dt.date() == now_est.date() and
                    last_dt.hour == now_est.hour):
                last_magi_hour = last_dt.hour
                log.info(f"Scheduler restart: MAGI already ran at "
                         f"{last_dt.strftime('%H:%M')} EST — skipping re-fire")
            else:
                last_magi_hour = -1
        else:
            last_magi_hour = -1
    except Exception as e:
        log.warning(f"Could not read last MAGI time from DB: {e} — defaulting to -1")
        last_magi_hour = -1

    log.info("Scheduler running — observer every 60min, MAGI at 9AM and 2PM EST")

    while running:
        now_utc = datetime.now(timezone.utc)
        now_est = now_utc.astimezone(EST)

        # Observer: run every 60 minutes
        minutes_since_observer = (now_utc - last_observer_time).total_seconds() / 60
        if minutes_since_observer >= OBSERVER_INTERVAL_MINUTES:
            run_observer_cycle()
            last_observer_time = now_utc

            # Daily market knowledge recompute at midnight UTC
            global _last_recompute_date
            today_utc = now_utc.date()
            if _last_recompute_date != today_utc:
                try:
                    from magi.market_knowledge import recompute_stats
                    log.info("Running daily market knowledge recompute...")
                    ok = recompute_stats()
                    if ok:
                        _last_recompute_date = today_utc
                        log.info("Market knowledge recompute complete")
                except Exception as e:
                    log.error(f"Market knowledge recompute failed: {e}")

        # MAGI: run at 9AM and 2PM EST
        if should_run_magi(now_est, last_magi_hour):
            run_magi_cycle(trigger='scheduled')
            last_magi_hour = now_est.hour

        # Reset last_magi_hour at midnight
        if now_est.hour == 0:
            last_magi_hour = -1

        # Sleep 60 seconds between checks
        time.sleep(60)

    log.info("Scheduler stopped cleanly.")


from flask import Flask as _Flask, jsonify as _jsonify, request as _request
import threading as _threading

_internal_app = _Flask('scheduler_internal')


@_internal_app.route('/internal/trigger_magi', methods=['POST'])
def _internal_trigger_magi():
    """Internal endpoint — localhost only. Called by dashboard to
    trigger a MAGI cycle on the scheduler's engine instance."""
    try:
        run_magi_cycle(trigger='manual')
        from database import get_recent_magi_decisions
        decisions = get_recent_magi_decisions(1)
        latest = decisions[0] if decisions else {}
        return _jsonify({
            'ok': True,
            'consensus': {
                'grid_action': latest.get('consensus_grid_action'),
                'risk_action': latest.get('consensus_risk_action'),
                'regime': latest.get('casper_action'),
                'spacing_adjustment_pct': None,
                'recentre_target': None,
                'melchior_conviction': latest.get('melchior_conviction'),
                'reason': latest.get('consensus_reason', ''),
            },
            'timestamp': latest.get('timestamp'),
        })
    except Exception as e:
        return _jsonify({'ok': False, 'error': str(e)}), 500


def _start_internal_server():
    """Start the internal IPC server on localhost:5001."""
    _internal_app.run(host='127.0.0.1', port=5001,
                      debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
