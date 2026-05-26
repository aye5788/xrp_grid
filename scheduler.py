import time
import logging
import os
import signal
import sys
from datetime import datetime, timezone, timedelta
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
# 2026-05-19 — observer dropped 60→10 min to tighten fill-detection cadence
# (Option 3 of the no-fills diagnostic). Public Kraken endpoints (Ticker +
# OHLC) are well inside rate limits at 6×/h; the rate-limited counter is
# trading-side only, untouched.
OBSERVER_INTERVAL_MINUTES = 10
MAGI_HOURS_EST = [0, 4, 8, 12, 16, 20]   # every 4 hours (6 cycles/day) — ~$13/mo to fit $20 Letta plan

# --- Gate wake wire ---------------------------------------------------
# A gate trigger in WAKE_CLASS_TRIGGERS that fires since the last cycle wakes
# MAGI OFF-schedule (vs. only annotating the next 4h slot). Throttled to >=
# WAKE_MIN_INTERVAL_MIN since any MAGI cycle so the Letta spend stays bounded:
# in calm markets ~$0 extra, in a real event one prompt. Edge-triggered gate
# functions + consumed_in_cycle marking prevent a standing condition from
# re-waking. Wake-class is deliberately the urgent subset (book one-sided,
# grid breach, regime flip); low-urgency triggers (T15 skew drift early-warn,
# T6/T7 scorer) still wait for the next scheduled or woken cycle.
WAKE_CLASS_TRIGGERS = ("T14", "T2", "T11")
WAKE_MIN_INTERVAL_MIN = 60
_last_magi_cycle_at = None   # set by run_magi_cycle; drives the wake throttle

# Hard-rule override tags that indicate the grid is NOT in an active trading
# state. A wake-class trigger (T2/T11/T14) is suppressed (see
# WAKE_REQUIRES_ACTIVE_GRID in config.py) when the most recent debate_records
# row contains any of these tags. Use startswith matching to handle
# parameterised tags like [AGENT_DEGRADED:...].
_WAKE_BLOCKING_OVERRIDE_PREFIXES = (
    "[PAUSE_INVALID]",
    "[REGIME_STANDDOWN]",
    "[HALT]",
    "[GRID_DEGENERATE]",
    "[COUNCIL_COLLAPSED]",
    "[GRID_PAUSE]",
    "[KILL_SWITCH]",
    "[DAILY_LOSS_LIMIT]",
    "[ALLOC_SKEW_CEILING]",
    "[AGENT_DEGRADED",     # prefix: actual tag is [AGENT_DEGRADED:<agent_id>]
)

EST = ZoneInfo('America/New_York')

# Global engine instance
_LIVE = os.environ.get("MAGI_LIVE_CONFIRM") == "YES"
engine = GridEngine(paper=not _LIVE)
running = True

# Track last stats recompute date
_last_recompute_date = None

def signal_handler(sig, frame):
    global running
    log.info("Shutdown signal received — stopping scheduler")
    running = False

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def _first_boot_geometry():
    """Pick (spacing_pct, levels) for the very first grid from the
    analytical scorer's rank-1 acceptable variant. Returns (None, None)
    if no acceptable variant exists in current candle history — caller
    is expected to stand down rather than guess.

    Run only when no paper orders are restored from DB at startup.
    Replaces the prior `engine.initialise_grid()` no-arg call that
    silently fell back to the now-deleted GRID_SPACING_PCT constant.
    """
    from database import get_candles
    from config import GRID_LEVEL_FEE_PER_SIDE
    from magi.spacing_evaluator import score_variants, DEFAULT_VARIANTS
    try:
        candles = list(reversed(get_candles('1h', limit=720)))
    except Exception as e:
        log.error(f"_first_boot_geometry: candle fetch failed: {e}")
        return None, None
    try:
        scored = score_variants(
            current_price=0.0,  # not used by the scorer
            candles_1h=candles,
            fee_rate_per_side=GRID_LEVEL_FEE_PER_SIDE,  # maker: resting arms fill maker
            candidate_variants=DEFAULT_VARIANTS,
        )
    except Exception as e:
        log.error(f"_first_boot_geometry: scoring failed: {e}")
        return None, None
    rank1 = next((v for v in scored if v.get('acceptable')), None)
    if not rank1:
        return None, None
    return float(rank1['spacing_pct']), int(rank1['levels'])


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
            if not engine.paper:
                # Live-mode fill detection — reconcile resting orders against
                # Kraken ClosedOrders. Mutually exclusive with the paper
                # simulate_fills block below.
                try:
                    live_filled = engine.reconcile_live_fills_from_kraken()
                    if live_filled:
                        log.info(
                            f"Observer: {len(live_filled)} live fills "
                            f"reconciled at {price:.4f}"
                        )
                except Exception as e:
                    log.error(f"Live fill reconcile error: {e}")
                # Book-state gate triggers — evaluate right after reconcile so
                # a fill that drains a side (T14 one-sided) or drifts skew past
                # threshold (T15) wakes MAGI promptly. The wake wire in the
                # main loop picks up the unconsumed fired event.
                try:
                    from magi.gate import evaluate_book_state_triggers
                    from config import DB_PATH
                    bfired = evaluate_book_state_triggers(DB_PATH)
                    if bfired:
                        log.info(f"Observer: book-state gate triggers fired {bfired}")
                except Exception as e:
                    log.error(f"Book-state gate trigger eval error: {e}")
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

                    # Place replacement orders at the opposite side, anchored to
                    # current market price (not the filled order's resting price).
                    # Anchoring to fill price drifts the grid when market moves
                    # between placement and fill.
                    grid_state = get_current_grid_state()
                    spacing_pct = grid_state['spacing_pct'] if grid_state else None
                    market_price = engine.get_current_price()

                    if spacing_pct and market_price is None:
                        log.warning(
                            "[GRID REPLENISH] No current market price — skipping "
                            "replacement orders"
                        )
                    elif spacing_pct:
                        replacements = 0
                        for order in filled:
                            try:
                                if order['side'] == 'sell':
                                    replacement_price = round(
                                        market_price * (1 - spacing_pct), 5
                                    )
                                    replacement_side = 'buy'
                                else:
                                    replacement_price = round(
                                        market_price * (1 + spacing_pct), 5
                                    )
                                    replacement_side = 'sell'

                                fill_ref = order.get('fill_price') or order['price']
                                drift_pct = abs(market_price - fill_ref) / market_price
                                if drift_pct > 2 * spacing_pct:
                                    log.warning(
                                        f"[GRID REPLENISH] Skipping replacement "
                                        f"{replacement_side.upper()} for {order['side'].upper()} "
                                        f"fill @ {fill_ref:.4f} — market {market_price:.4f} "
                                        f"drifted {drift_pct*100:.2f}% > 2×spacing "
                                        f"({2*spacing_pct*100:.2f}%). RECENTRE will handle."
                                    )
                                    continue

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

    # Update grid config performance outcomes
    try:
        from database import update_grid_config_outcome
        update_grid_config_outcome(min_hours_active=2.0)
    except Exception as e:
        log.warning(f"Grid config outcome update failed: {e}")


def _update_rotation_counter_and_maybe_rotate(cycle_success: bool) -> None:
    """Persist the rotation_cycle_counter increment and (on success only)
    invoke magi.memory_lifecycle.maybe_rotate. Called from run_magi_cycle
    after a cycle was actually attempted (i.e. guardrails passed).

    Increment fires on success AND on fail so the cadence runs on calendar
    cycles, not only successful ones. Rotation itself only fires on
    success — distilling a bad cycle's thread is worse than skipping the
    rotation, since the thread state may already be inconsistent.
    """
    try:
        from database import get_system_state, set_system_state
        counter = int(get_system_state('rotation_cycle_counter',
                                       default='0'))
        counter += 1
        set_system_state('rotation_cycle_counter', counter)
        log.info(
            "rotation_cycle_counter = %d (cycle %s)",
            counter, 'succeeded' if cycle_success else 'failed',
        )
        if cycle_success:
            try:
                from magi.memory_lifecycle import maybe_rotate
                maybe_rotate(counter)
            except Exception as e:
                log.error(f"maybe_rotate({counter}) raised: {e!r}")
    except Exception as e:
        log.error(f"rotation counter persistence failed: {e!r}")


def run_magi_cycle(trigger='scheduled'):
    """Run full MAGI supervision cycle and apply to grid.

    Counter / rotation lifecycle (after any attempted cycle, i.e. one
    that passed the guardrail check):
      - increment rotation_cycle_counter in system_state and persist
      - on a successful cycle ONLY, call maybe_rotate(counter)
      - on a failed cycle: increment but do not rotate (don't distil a
        bad cycle's thread)
    Guardrail-blocked cycles do not increment — no council ran, no
    thread accumulated.

    Config-drift validator runs at start AND end of every cycle:
      - start hook catches drift introduced between provisioning runs
        (e.g. the 2026-05-20 BYOK runbook bug that reset Balthasar's
        temperature to 1.0 for ~17h)
      - end hook catches drift introduced mid-cycle by any mechanism
      Failure mode: emit critical alert, continue. Never blocks trading.
    """
    log.info(f"--- MAGI CYCLE (trigger={trigger}) ---")

    # Record cycle time for the gate-wake throttle. Set for EVERY cycle
    # (scheduled, startup, manual, gate wake) so an off-schedule wake can't
    # fire within WAKE_MIN_INTERVAL_MIN of any prior cycle.
    global _last_magi_cycle_at
    _last_magi_cycle_at = datetime.now(timezone.utc)

    # Pre-cycle config drift check — non-fatal.
    try:
        from magi.config_validator import alert_on_config_drift
        alert_on_config_drift()
    except Exception as e:
        log.warning("config_validator pre-cycle check failed: %s", e)

    ok, failures = check_all_guardrails()
    if not ok:
        log.error(f"Guardrails blocked cycle: {failures}")
        try:
            engine.cancel_all_orders()
            log.warning("Orders cancelled due to guardrail failure")
        except Exception as e:
            log.error(f"Cancel-all failed: {e}")
        return

    cycle_success = False
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
            cycle_success = True
        else:
            log.warning("MAGI cycle returned no result")
    except Exception as e:
        log.exception("MAGI cycle error: %s", e)

    # Counter + rotation hook — runs whether the cycle succeeded or failed.
    # Wrapped internally so nothing in here can crash the scheduler loop.
    _update_rotation_counter_and_maybe_rotate(cycle_success)

    # Post-cycle config drift check — non-fatal. Catches mid-cycle drift
    # introduced by any mechanism (e.g. an out-of-band agents.update).
    try:
        from magi.config_validator import alert_on_config_drift
        alert_on_config_drift()
    except Exception as e:
        log.warning("config_validator post-cycle check failed: %s", e)


def sweep_letta_steps_for_failures():
    """Scan recent Letta steps for credit/auth/error stop_reasons that the
    live council.py hook might have missed (summarization, retries, anything
    not flowing through the message-create return path).

    Scope: MAGI agents only (casper/melchior/balthasar via agent_registry).
    Window: from MAX(magi_alerts.timestamp) - 1h, or last 6h on first call.
    Dedup: insert_alert skips on existing step_id.

    The Letta SDK's c.steps.list() is server-side broken on the `order`
    parameter (returns 400), so this sweep walks recent runs per agent and
    pulls their steps via /v1/runs/{run_id}/steps which works correctly.
    """
    import os
    import requests
    from datetime import timedelta
    from database import (
        get_agent_registry_row, get_latest_alert_timestamp, insert_alert,
    )

    api_key = os.environ.get('LETTA_API_KEY')
    if not api_key:
        return
    headers = {'Authorization': f'Bearer {api_key}'}

    last_ts = get_latest_alert_timestamp()
    if last_ts:
        try:
            lookback_start = datetime.fromisoformat(last_ts) - timedelta(hours=1)
        except ValueError:
            lookback_start = datetime.utcnow() - timedelta(hours=6)
    else:
        lookback_start = datetime.utcnow() - timedelta(hours=6)

    stop_reason_map = {
        'insufficient_credits':  ('credit_exhausted', 'critical'),
        'llm_api_error':         ('provider_error',   'warn'),
        'invalid_llm_response':  ('provider_error',   'warn'),
        'error':                 ('unknown_failure',  'warn'),
    }

    swept = 0
    alerted = 0
    for agent_id in ('casper', 'melchior', 'balthasar'):
        row = get_agent_registry_row(agent_id)
        if not row:
            continue
        letta_id = row.get('letta_agent_id')
        if not letta_id:
            continue
        try:
            r = requests.get(
                'https://api.letta.com/v1/runs',
                headers=headers,
                params={'agent_id': letta_id, 'limit': 50},
                timeout=15,
            )
            r.raise_for_status()
            runs = r.json()
        except Exception as e:
            log.warning("sweep: runs list failed for %s: %r", agent_id, e)
            continue
        for run in runs:
            try:
                created = datetime.fromisoformat(
                    (run.get('created_at') or '').replace('Z', '')
                )
            except ValueError:
                continue
            if created < lookback_start:
                continue
            try:
                rs = requests.get(
                    f"https://api.letta.com/v1/runs/{run['id']}/steps",
                    headers=headers, timeout=15,
                )
                rs.raise_for_status()
                steps = rs.json()
            except Exception as e:
                log.warning("sweep: steps list failed for run %s: %r",
                            run.get('id'), e)
                continue
            for step in steps:
                swept += 1
                stop = step.get('stop_reason')
                status = step.get('status')
                err_data_str = str(step.get('error_data') or '').lower()
                if '401' in err_data_str or 'unauthorized' in err_data_str \
                        or 'authentication_error' in err_data_str:
                    cat, sev = 'auth_failed', 'critical'
                elif '429' in err_data_str or 'rate limit' in err_data_str:
                    cat, sev = 'rate_limited', 'warn'
                elif stop in stop_reason_map:
                    cat, sev = stop_reason_map[stop]
                elif status == 'failed':
                    cat, sev = 'unknown_failure', 'warn'
                else:
                    continue
                inserted = insert_alert(
                    severity=sev, category=cat,
                    message=(f"[sweep] stop_reason={stop} status={status} "
                             f"provider={step.get('provider_name','?')}"
                             f"/{step.get('provider_category','?')} "
                             f"error_data={err_data_str[:200]}"),
                    agent_id=agent_id,
                    provider_category=step.get('provider_category'),
                    provider_name=step.get('provider_name'),
                    step_id=step.get('id'),
                )
                if inserted:
                    alerted += 1
    log.info("sweep: walked %d steps, %d new alerts", swept, alerted)


def should_run_magi(now_est: datetime, last_magi_hour: int) -> bool:
    """Check if it's time for a MAGI cycle."""
    current_hour = now_est.hour
    if current_hour in MAGI_HOURS_EST and current_hour != last_magi_hour:
        return True
    return False


def _is_wake_suppressed_nontrading() -> tuple:
    """Return (suppressed: bool, reason: str) for a candidate wake-class gate
    wake (T2/T11/T14).

    When the grid is in a non-trading state the council cannot act on ANY
    wake-class trigger — waking it produces zero new information and burns
    Letta credits. This was the May-25 bleed: T2 (level-triggered) re-fired
    every hour during a 12h PAUSE_INVALID/REGIME_STANDDOWN standdown, waking
    the council ~10 times for nothing. The same shape applies to T11/T14, so
    the guard is trigger-agnostic. This function detects the non-trading state
    and tells the caller to suppress the wake.

    Non-trading is defined as any of:
      - The most recent debate_records row has hard_rule_overrides containing
        a blocking tag (PAUSE_INVALID, REGIME_STANDDOWN, HALT, GRID_DEGENERATE,
        COUNCIL_COLLAPSED, GRID_PAUSE, KILL_SWITCH, DAILY_LOSS_LIMIT,
        ALLOC_SKEW_CEILING, AGENT_DEGRADED).
      - The most recent debate_records row has geometry_veto='RISK_BLOCK'
        (REGIME_STANDDOWN is blocking geometry changes even when not listed as
        a hard_rule_override tag on every cycle).
      - The most recent grid_state row has halt=1.

    Reads DB directly on every call — no caching — so state is always current.
    Any DB failure is non-fatal: returns (False, '') so the wake fires rather
    than being permanently suppressed by a read error.
    """
    import sqlite3 as _sq
    import json as _json
    try:
        from config import DB_PATH
        conn = _sq.connect(DB_PATH)
        conn.row_factory = _sq.Row
        try:
            # Check 1: most recent cycle's hard_rule_overrides and geometry_veto.
            row = conn.execute(
                "SELECT hard_rule_overrides, geometry_veto "
                "FROM debate_records ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                overrides_raw = row["hard_rule_overrides"]
                if overrides_raw:
                    try:
                        overrides = _json.loads(overrides_raw)
                    except (ValueError, TypeError):
                        overrides = []
                    for tag in overrides:
                        for prefix in _WAKE_BLOCKING_OVERRIDE_PREFIXES:
                            if str(tag).startswith(prefix):
                                return True, f"last cycle hard_rule_override={tag}"
                gv = row["geometry_veto"] or ""
                if gv == "RISK_BLOCK":
                    return True, "last cycle geometry_veto=RISK_BLOCK"

            # Check 2: grid halt flag.
            gs = conn.execute(
                "SELECT halt FROM grid_state ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if gs and gs["halt"]:
                return True, "grid_state.halt=1"

        finally:
            conn.close()
    except Exception as e:
        log.warning(
            "_is_wake_suppressed_nontrading: DB read failed (%s) — "
            "not suppressing", e
        )
    return False, ""


def _consume_wake_gate_event(trigger_id: str, sentinel: str) -> None:
    """Mark ALL unconsumed fired events for `trigger_id` consumed with the
    given sentinel value (e.g. 'wake_suppressed_nontrading',
    'wake_breach_cleared').

    Called when a wake is suppressed or dropped so the event(s) do not
    re-trigger the guard every 60 seconds until a real cycle finally consumes
    them. Consuming the whole unconsumed-fired set (not just the newest row)
    prevents stale fired rows from accumulating while a condition is being
    deferred or has cleared. The next event (fired on a later gate evaluation)
    starts a fresh evaluation.

    Failure is non-fatal: logs a warning and continues.
    """
    try:
        import sqlite3 as _sq
        from config import DB_PATH
        conn = _sq.connect(DB_PATH)
        try:
            conn.execute(
                "UPDATE magi_gate_events SET consumed_in_cycle=? "
                "WHERE trigger_id=? AND fired=1 AND consumed_in_cycle IS NULL",
                (sentinel, trigger_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.warning("_consume_wake_gate_event(%s): DB write failed: %s",
                    trigger_id, e)


def _wake_dwell_status(trigger_id: str) -> tuple:
    """Decide whether a pending wake-class trigger has PERSISTED long enough
    to justify spending a council cycle. Returns (status, reason):

      'wake'  — condition is live and has persisted >= WAKE_DWELL_MINUTES.
      'defer' — condition is live but hasn't dwelled long enough yet. Caller
                does NOT consume and does NOT wake; the next 60s loop re-checks
                once dwell accrues (or the condition clears). No credit spend.
      'drop'  — condition is no longer live (a transient breach/flip/one-sided
                blip that resolved). Caller consumes the event without waking.

    WAKE_DWELL_MINUTES <= 0 disables the dwell (always 'wake'). Any DB/parse
    failure returns 'wake' — fail toward the prior behaviour rather than
    silently swallowing a real signal by deferring forever.
    """
    from config import WAKE_DWELL_MINUTES
    if not WAKE_DWELL_MINUTES or WAKE_DWELL_MINUTES <= 0:
        return "wake", "dwell disabled"
    import sqlite3 as _sq
    import json as _json
    import time as _time
    try:
        from config import DB_PATH
        conn = _sq.connect(DB_PATH)
        conn.row_factory = _sq.Row
        try:
            ev = conn.execute(
                "SELECT timestamp, details FROM magi_gate_events "
                "WHERE trigger_id=? AND fired=1 AND consumed_in_cycle IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (trigger_id,),
            ).fetchone()
            if ev is None:
                return "wake", "no pending fired event to dwell on"
            age_min = max(0.0, (_time.time() - float(ev["timestamp"])) / 60.0)
            try:
                details = _json.loads(ev["details"]) if ev["details"] else {}
            except (ValueError, TypeError):
                details = {}

            if trigger_id == "T2":
                return _dwell_t2(conn, age_min, float(WAKE_DWELL_MINUTES))
            if trigger_id == "T14":
                return _dwell_t14(conn, age_min, float(WAKE_DWELL_MINUTES))
            if trigger_id == "T11":
                return _dwell_t11(conn, details, age_min,
                                  float(WAKE_DWELL_MINUTES))
            # Unknown wake trigger — generic age-only dwell.
            if age_min >= WAKE_DWELL_MINUTES:
                return "wake", (f"age {age_min:.1f}min >= dwell "
                                f"{WAKE_DWELL_MINUTES}min")
            return "defer", (f"age {age_min:.1f}min < dwell "
                             f"{WAKE_DWELL_MINUTES}min")
        finally:
            conn.close()
    except Exception as e:
        log.warning("_wake_dwell_status(%s): failed (%s) — waking",
                    trigger_id, e)
        return "wake", f"dwell check error: {e}"


def _grid_band(conn) -> tuple:
    """(centre, upper, lower) for the current grid, or (None, None, None)."""
    gs = conn.execute(
        "SELECT centre_price, spacing_pct, levels FROM grid_state "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not gs or gs["centre_price"] is None or gs["spacing_pct"] is None:
        return None, None, None
    try:
        centre = float(gs["centre_price"])
        spacing = float(gs["spacing_pct"])
        levels = int(gs["levels"] or 5)
    except (TypeError, ValueError):
        return None, None, None
    n_pairs = max(1, levels // 2)
    return centre, centre * (1.0 + n_pairs * spacing), \
        centre * (1.0 - n_pairs * spacing)


def _dwell_t2(conn, age_min: float, dwell_min: float) -> tuple:
    """T2 dwell: price must have stayed beyond the SAME grid boundary for
    >= dwell_min, measured on 1m candles (a true continuous dwell, matching
    the operator's 'remains above/below for X minutes'). Falls back to event
    age + latest 1h close when 1m history is too short (startup / REST
    fallback path, which only writes 1h candles)."""
    _, upper, lower = _grid_band(conn)
    if upper is None:
        return "defer", "no grid_state for band"

    def _side(c: float):
        if c > upper:
            return "above"
        if c < lower:
            return "below"
        return None

    need = max(1, int(round(dwell_min)))  # one 1m candle ~= one minute
    rows = conn.execute(
        "SELECT close FROM candles WHERE timeframe='1m' "
        "ORDER BY timestamp DESC LIMIT ?",
        (need,),
    ).fetchall()
    if len(rows) >= need:
        sides = [_side(float(r["close"])) for r in rows]
        newest = sides[0]
        if newest is None:
            return "drop", "breach cleared (latest 1m inside band)"
        if all(s == newest for s in sides):
            return "wake", f"price {newest} band for >= {need}min (1m)"
        return "defer", (f"breach {newest} not yet sustained {need}min (1m)")

    # Fallback: insufficient 1m history.
    last_1h = conn.execute(
        "SELECT close FROM candles WHERE timeframe='1h' "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    if not last_1h:
        return "defer", "no candle history for dwell"
    side = _side(float(last_1h["close"]))
    if side is None:
        return "drop", "breach cleared (latest 1h inside band)"
    if age_min >= dwell_min:
        return "wake", (f"price {side} band, event age {age_min:.0f}min >= "
                        f"{dwell_min:.0f}min (1h fallback)")
    return "defer", (f"breach {side}, age {age_min:.0f}min < "
                     f"{dwell_min:.0f}min (1h fallback)")


def _dwell_t14(conn, age_min: float, dwell_min: float) -> tuple:
    """T14 dwell: the book must STILL be one-sided now and have been so for
    >= dwell_min. A book that refilled (or fully emptied) within the window
    was a transient drain — drop it."""
    rows = conn.execute(
        "SELECT side, COUNT(*) n FROM grid_orders WHERE status='open' "
        "GROUP BY side"
    ).fetchall()
    buys = sells = 0
    for r in rows:
        if r["side"] == "buy":
            buys = int(r["n"])
        elif r["side"] == "sell":
            sells = int(r["n"])
    total = buys + sells
    one_sided = total > 0 and ((buys == 0) != (sells == 0))
    if not one_sided:
        return "drop", f"book no longer one-sided (buys={buys} sells={sells})"
    if age_min >= dwell_min:
        return "wake", (f"book one-sided {age_min:.0f}min >= "
                        f"{dwell_min:.0f}min")
    return "defer", f"book one-sided {age_min:.0f}min < {dwell_min:.0f}min"


def _dwell_t11(conn, details: dict, age_min: float, dwell_min: float) -> tuple:
    """T11 dwell: the vol-regime flip must NOT have reverted and must have
    held >= dwell_min. `details` carries {'prior':…, 'current':…} from the
    firing evaluation; if the live regime has returned to `prior`, the flip
    was transient — drop it."""
    prior = details.get("prior")
    flipped_to = details.get("current")
    cur = conn.execute(
        "SELECT vol_regime FROM indicators WHERE timeframe='1h' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    cur_regime = cur["vol_regime"] if cur else None
    if prior is not None and cur_regime == prior:
        return "drop", f"regime reverted to {prior} (flip transient)"
    if age_min >= dwell_min:
        return "wake", (f"regime held {flipped_to} {age_min:.0f}min >= "
                        f"{dwell_min:.0f}min")
    return "defer", f"regime flip {age_min:.0f}min < {dwell_min:.0f}min"


def _pending_wake_class_trigger():
    """Return the trigger_id of an unconsumed, fired, wake-class gate event
    (or None). This is the off-schedule wake signal: it is the same
    unconsumed-fired-event set the orchestrator consumes when a cycle runs,
    so once MAGI is woken the event is marked consumed and won't re-wake.
    Read-only; any failure returns None (never blocks the loop)."""
    try:
        from database import get_conn
        conn = get_conn()
        try:
            placeholders = ",".join("?" for _ in WAKE_CLASS_TRIGGERS)
            row = conn.execute(
                f"SELECT trigger_id FROM magi_gate_events "
                f"WHERE consumed_in_cycle IS NULL AND fired=1 "
                f"AND trigger_id IN ({placeholders}) "
                f"ORDER BY id DESC LIMIT 1",
                tuple(WAKE_CLASS_TRIGGERS),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception as e:
        log.warning("wake-class trigger check failed: %r", e)
        return None


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

    # Start the always-on gate monitoring service. Wires Kraken WS v2
    # to magi/gate.py predicate evaluation. Falls back to REST polling
    # if WS is unavailable. See magi/gate_monitor.py for design notes.
    # Failure to start is non-fatal — gate evaluation reverts to the
    # observer poll path (which was the pre-WS gate path, still wired
    # in observer.poll_cycle as a safety check).
    try:
        from magi.gate_monitor import start_in_background as _start_gate_monitor
        _gate_monitor = _start_gate_monitor()
        log.info("GateMonitor launched (Kraken WS v2 streaming)")
    except Exception as e:
        log.error("GateMonitor failed to start (non-fatal): %s — "
                  "gate will fall back to observer poll cadence", e)
        _gate_monitor = None

    # Memory rotation: surface the current counter so operators can see
    # where we are in the cadence. Persisted in system_state, incremented
    # once per attempted MAGI cycle in run_magi_cycle(). Read-only here.
    try:
        from database import get_system_state
        from config import ROTATION_CADENCE
        rc = int(get_system_state('rotation_cycle_counter', default='0'))
        if rc <= 0:
            cycles_to_next = ROTATION_CADENCE
        else:
            cycles_to_next = (
                (ROTATION_CADENCE - (rc % ROTATION_CADENCE))
                % ROTATION_CADENCE
            ) or ROTATION_CADENCE
        log.info(
            "Memory rotation: counter=%d, cadence=%d — next rotation in "
            "%d successful cycle(s)",
            rc, ROTATION_CADENCE, cycles_to_next,
        )
    except Exception as e:
        log.warning(f"Could not read rotation_cycle_counter at startup: {e}")

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
            sp, lv = _first_boot_geometry()
            if sp is None:
                log.warning(
                    "First boot: no acceptable scorer variant from "
                    "current candle history — standing down. Grid will "
                    "remain empty until the next MAGI cycle produces "
                    "usable geometry (or the scorer flips to an "
                    "acceptable rank-1 on its own)."
                )
            else:
                log.info(
                    "First boot: scorer rank-1 → spacing=%.4f levels=%d",
                    sp, lv,
                )
                engine.level_count = lv
                engine.initialise_grid(spacing_pct=sp)
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
    # Phase 5 writes to debate_records; legacy magi_decisions is sparse and
    # cannot be used as a debounce source.
    try:
        import pytz
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT timestamp FROM debate_records "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row['timestamp']:
            est = pytz.timezone('US/Eastern')
            last_dt = datetime.fromisoformat(row['timestamp']).replace(
                tzinfo=timezone.utc).astimezone(est)
            now_est = datetime.now(timezone.utc).astimezone(est)
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
        log.warning(f"Could not read last MAGI time from debate_records: {e} — defaulting to -1")
        last_magi_hour = -1

    from config import LETTA_STEPS_SWEEP_INTERVAL_MIN
    last_sweep_time = datetime.now(timezone.utc) - \
        timedelta(minutes=LETTA_STEPS_SWEEP_INTERVAL_MIN)  # fire on first tick

    log.info(
        f"Scheduler running — observer every {OBSERVER_INTERVAL_MINUTES}min, "
        f"MAGI hours (EST) = {MAGI_HOURS_EST}, "
        f"alert sweep every {LETTA_STEPS_SWEEP_INTERVAL_MIN}min"
    )

    while running:
        now_utc = datetime.now(timezone.utc)
        now_est = now_utc.astimezone(EST)

        # Background alert sweep — every LETTA_STEPS_SWEEP_INTERVAL_MIN minutes
        if LETTA_STEPS_SWEEP_INTERVAL_MIN > 0:
            mins_since_sweep = (now_utc - last_sweep_time).total_seconds() / 60
            if mins_since_sweep >= LETTA_STEPS_SWEEP_INTERVAL_MIN:
                try:
                    sweep_letta_steps_for_failures()
                except Exception as e:
                    log.error(f"sweep_letta_steps_for_failures raised: {e!r}")
                last_sweep_time = now_utc

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

        # MAGI: fire when the wall-clock hour matches a slot in MAGI_HOURS_EST
        # and we haven't already fired this hour. Dedupe is the `current_hour
        # != last_magi_hour` check inside should_run_magi(); rollover from one
        # entry to the next (including 20 → 0 across midnight) is handled by
        # that comparison alone. The previous midnight reset
        # (`if hour==0: last_magi_hour=-1`) ran on every 60-second loop
        # iteration while the wall-clock was in EST hour 0, causing the cycle
        # to re-fire minute-by-minute through that whole hour — bug observed
        # at 2026-05-18 04:00 UTC (=00:00 EDT) producing 47 cycles in 60 min.
        if should_run_magi(now_est, last_magi_hour):
            run_magi_cycle(trigger='scheduled')
            last_magi_hour = now_est.hour
        else:
            # Off-schedule gate wake: a wake-class trigger fired since the
            # last cycle. Throttled to >= WAKE_MIN_INTERVAL_MIN since ANY
            # MAGI cycle so a depleting book gets the council involved within
            # the hour instead of waiting up to 4h — without open-ended spend.
            throttle_ok = (
                _last_magi_cycle_at is None
                or (now_utc - _last_magi_cycle_at).total_seconds() / 60.0
                >= WAKE_MIN_INTERVAL_MIN
            )
            if throttle_ok:
                pending = _pending_wake_class_trigger()
                if pending:
                    # Two-stage wake gate. gate.py DETECTION is unchanged —
                    # only the decision to spend a council cycle is guarded:
                    #   1. Non-trading suppression. If the grid is standing
                    #      down (PAUSE_INVALID / REGIME_STANDDOWN / HALT /
                    #      GRID_DEGENERATE / halt etc.) the council cannot act
                    #      on ANY wake trigger, so consume + skip. This is the
                    #      direct fix for the May-25 bleed (T2 re-fired hourly
                    #      for 12h during a standdown → 10 useless wakes).
                    #      Applies to T2/T11/T14. WAKE_REQUIRES_ACTIVE_GRID
                    #      disables.
                    #   2. Dwell. The condition must PERSIST WAKE_DWELL_MINUTES
                    #      before waking, so a transient breach/flip/one-sided
                    #      blip that has resolved by council-time doesn't spend
                    #      a cycle. 'defer' re-checks next loop (no spend);
                    #      'drop' consumes a cleared event.
                    from config import WAKE_REQUIRES_ACTIVE_GRID
                    decided = False
                    if WAKE_REQUIRES_ACTIVE_GRID:
                        suppressed, reason = _is_wake_suppressed_nontrading()
                        if suppressed:
                            log.info(
                                "gate_wake_suppressed: %s fired but %s — "
                                "consuming event, no wake fired",
                                pending, reason,
                            )
                            _consume_wake_gate_event(
                                pending, "wake_suppressed_nontrading")
                            decided = True

                    if not decided:
                        status, reason = _wake_dwell_status(pending)
                        if status == "drop":
                            log.info(
                                "gate_wake_dropped: %s — %s; consuming "
                                "event, no wake", pending, reason,
                            )
                            _consume_wake_gate_event(
                                pending, "wake_breach_cleared")
                        elif status == "defer":
                            log.info(
                                "gate_wake_deferred: %s — %s; re-checking "
                                "next loop", pending, reason,
                            )
                            # NOT consumed, NOT woken: re-evaluate next tick
                            # once the dwell accrues or the condition clears.
                        else:  # 'wake'
                            log.info(
                                "Gate wake: %s fired and %s — running "
                                "off-schedule MAGI cycle (throttle %dmin "
                                "satisfied)",
                                pending, reason, WAKE_MIN_INTERVAL_MIN,
                            )
                            run_magi_cycle(trigger=f'gate_wake:{pending}')

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
        # Read from debate_records (canonical Phase 5 source). magi_decisions
        # dual-write happens after this insert too, but debate_records is the
        # source of truth for new code paths like this status reporter.
        from database import get_recent_debate_records
        records = get_recent_debate_records(limit=1)
        latest = records[0] if records else {}
        # Map float conviction back to the legacy string label the dashboard
        # historically saw, so the response shape is stable.
        m_conv = latest.get('melchior_r0_conviction')
        try:
            m_conv_f = float(m_conv or 0.0)
            if m_conv_f >= 0.75:
                m_conv_label = 'high'
            elif m_conv_f >= 0.5:
                m_conv_label = 'medium'
            else:
                m_conv_label = 'low'
        except (TypeError, ValueError):
            m_conv_label = 'low'
        return _jsonify({
            'ok': True,
            'consensus': {
                'grid_action': latest.get('final_grid_action'),
                'risk_action': latest.get('final_risk_action'),
                'regime': latest.get('casper_r0_position'),
                'spacing_adjustment_pct': None,
                'recentre_target': None,
                'melchior_conviction': m_conv_label,
                'reason': '',
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
    from magi import adam
    adam.init("scheduler")
    main()
