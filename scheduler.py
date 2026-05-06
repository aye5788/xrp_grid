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
LEARNING_HOUR_EST = 17     # 5PM EST — manual trigger only for now

EST = ZoneInfo('America/New_York')

# Global engine instance
engine = GridEngine(paper=True)
running = True

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
                filled = engine.simulate_fills(price)
                if filled:
                    log.info(f"Observer: {len(filled)} paper fills at {price:.4f}")
                    engine.update_inventory(price)
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
        log.info("No paper orders restored — initialising fresh grid on startup")
        engine.initialise_grid()
    else:
        log.info(f"Resumed {len(engine.paper_orders)} paper orders from DB — skipping fresh grid init")

    # Run initial MAGI cycle
    run_magi_cycle(trigger='startup')

    last_observer_time = datetime.now(timezone.utc)
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


if __name__ == "__main__":
    main()
