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

# --- Council cadence: GATE-PRIMARY, clock as backstop (BU-2, 2026-06-09) ---
# The always-on free gate (magi/gate.py via gate_monitor) decides whether the
# paid council wakes in between; the clock only guarantees a FLOOR of one
# council call per day — the end-of-day assessment/recap, grid or no grid —
# plus a max-silence backstop for the case where the service was down across
# the daily slot. This replaces the Letta-era MAGI_HOURS_EST=[0,4,8,12,16,20]
# fixed 4h schedule (6 unconditional cycles/day, priced for the dead $20/mo
# Letta plan). NOTE: dashboard.py:_next_magi_eta() mirrors the daily hour —
# update both places if it changes.
MAGI_DAILY_HOUR_EST = 20      # end-of-day council assessment fires in this EST hour
MAGI_MAX_SILENCE_HOURS = 25   # force a cycle if none has run in this many hours

# --- Gate wake wire ---------------------------------------------------
# Fix 4 (2026-06-11): wake-class is the W-SERIES — wake QUESTIONS, not
# detectors. Every T-series trigger is context-only (detected, recorded,
# shown to the council in triggers_since_last_cycle — never wakes anyone).
# A W names the council-only question it asks:
#   W1 — "price left the band and stayed out: corrective recentre or not?"
#        (fed by T2 detection; T2 dwell + one-wake-per-breach-episode guard)
#   W2 — "the evidence under your standing stance changed: re-judge it."
#        (warehouse verdict shift held one bar, or exposure-cap engaged/
#        released; edge-triggered in gate.py, one wake per shift)
# The old wake set (T2/T11/T14/T16) was demoted after the 2026-06-11 yield
# audit: 0/16 gate-woken cycles produced a council-originated change — the
# rule layer already handled what those detectors saw. Their dwell handlers
# remain below so stale pre-deploy events drain cleanly.
# Wakes stay throttled to >= WAKE_MIN_INTERVAL_MIN since any MAGI cycle.
# Adding a T is cheap instrumentation; adding a W requires naming the
# council-only question it asks.
WAKE_CLASS_TRIGGERS = ("W1", "W2")
WAKE_MIN_INTERVAL_MIN = 60
_last_magi_cycle_at = None   # set by run_magi_cycle; drives the wake throttle

# Hard-rule override tags that indicate the grid is NOT in an active trading
# state. A wake-class trigger (T2/T11/T14) is suppressed (see
# WAKE_REQUIRES_ACTIVE_GRID in config.py) when the most recent debate_records
# row contains any of these tags. Use startswith matching to handle
# parameterised tags like [AGENT_DEGRADED:...].
_WAKE_BLOCKING_OVERRIDE_PREFIXES = (
    "[PAUSE_INVALID]",
    # [REGIME_STANDDOWN] retired (Stage-4 item 2a): rule 0d was removed, so that tag
    # is no longer emitted by anything. The Casper regime stand-down objection is now
    # honored INSIDE the council — Balthasar's synthesis sees regime_action and
    # declines to reconfigure over a live STAND_DOWN (council_v2 downgrades the
    # RECONFIGURE to THESIS_HOLDS) — so the scheduler no longer needs to pre-empt the
    # wake on it. Wake cadence is still governed by the gate, the daily floor +
    # max-silence backstop, and the geometry_veto='RISK_BLOCK' column suppressor below.
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


def run_magi_cycle(trigger='scheduled'):
    """Run full MAGI supervision cycle and apply to grid.

    (The Letta-era per-cycle hooks were removed 2026-06-09, BU-1/BU-3:
    the config-drift validator — it compared live Letta `model_settings`
    against provision_agents.AGENT_CONFIG, a world that no longer exists;
    seat model handles are now constants in the seat-callers and per-cycle
    config is recorded via the debate_records.config_version fingerprint —
    and the memory-rotation counter hook, which compacted Letta threads /
    self_model, concepts the stateless seats don't have.)
    """
    log.info(f"--- MAGI CYCLE (trigger={trigger}) ---")

    # Record cycle time for the gate-wake throttle. Set for EVERY cycle
    # (scheduled, startup, manual, gate wake) so an off-schedule wake can't
    # fire within WAKE_MIN_INTERVAL_MIN of any prior cycle.
    global _last_magi_cycle_at
    _last_magi_cycle_at = datetime.now(timezone.utc)

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
            # Record what the engine actually applied back onto the debate_records
            # row (distinct from final_grid_action — captures engine-level
            # divergence: cross-check coercion, empty-book skips, spacing clamps).
            cyc_id = consensus.get('cycle_id')
            applied = getattr(engine, 'last_applied', None)
            if cyc_id and applied:
                try:
                    from database import update_debate_applied
                    update_debate_applied(cyc_id, **applied)
                except Exception as e:
                    log.warning(f"applied-action write-back failed for {cyc_id}: {e}")
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
        log.exception("MAGI cycle error: %s", e)



def should_run_magi(now_est: datetime, last_scheduled_date) -> bool:
    """Daily clock floor: fire the scheduled council call once per EST
    calendar day, in the MAGI_DAILY_HOUR_EST hour. `last_scheduled_date`
    is the EST date of the last scheduled fire (datetime.date or None) —
    the date-based dedupe that stops the call re-firing on every 60s loop
    tick within the hour. Gate wakes and the max-silence backstop are
    handled separately in the main loop; this function is ONLY the
    once-a-day floor."""
    return (now_est.hour == MAGI_DAILY_HOUR_EST
            and now_est.date() != last_scheduled_date)


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
        a blocking tag (PAUSE_INVALID, HALT, GRID_DEGENERATE, COUNCIL_COLLAPSED,
        GRID_PAUSE, KILL_SWITCH, DAILY_LOSS_LIMIT, ALLOC_SKEW_CEILING,
        AGENT_DEGRADED). [REGIME_STANDDOWN] was retired in Stage-4 item 2a — the
        regime objection is now honored in-council, not pre-empted here.
      - The most recent debate_records row has geometry_veto='RISK_BLOCK'
        (Balthasar vetoed geometry this cycle; it is recorded as a column, not as
        a hard_rule_override tag, so it is checked separately).
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
      'drop_answered' — (T2/T16) condition is live but a council cycle already
                answered this episode (T2: same direction + band; T16: same
                or deeper drawdown rung). Caller consumes with the
                'wake_episode_answered' sentinel — one wake per episode.

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

            if trigger_id == "W1":
                # W1 is the breach wake question fed by T2 detection — the
                # T2 dwell logic (band check, 1m continuous dwell, one wake
                # per breach episode) applies verbatim.
                return _dwell_t2(conn, age_min, float(WAKE_DWELL_MINUTES))
            if trigger_id == "W2":
                # W2 is already edge-triggered AND one-bar-held by
                # construction (gate.py:w2_stance_evidence_shift) — no age
                # dwell adds information. Wake.
                return "wake", (f"stance-evidence shift: "
                                f"{details.get('changes')}")
            # Legacy wake-class IDs (T2/T11/T14/T16 were demoted to
            # context-only in Fix 4) — keep their dwell handlers so any
            # unconsumed pre-deploy event drains correctly on the first
            # post-deploy pass instead of hitting the generic fallback.
            if trigger_id == "T2":
                return _dwell_t2(conn, age_min, float(WAKE_DWELL_MINUTES))
            if trigger_id == "T14":
                return _dwell_t14(conn, age_min, float(WAKE_DWELL_MINUTES))
            if trigger_id == "T11":
                return _dwell_t11(conn, details, age_min,
                                  float(WAKE_DWELL_MINUTES))
            if trigger_id == "T16":
                return _dwell_t16(conn, details, age_min,
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


def _t2_episode_already_answered(conn, direction: str,
                                 upper: float, lower: float) -> bool:
    """True when a prior T2 fire for this SAME breach episode — same
    direction, same grid band — was already consumed by a real council
    cycle (consumed_in_cycle = 'cyc…', never a suppression sentinel).

    The council has ruled on this exact standing condition; re-waking it
    every throttle window re-asks the same question for the same answer
    (the 2026-06-10 04:00/05:00/06:00 triple-wake: two redundant MAINTAIN
    cycles). One wake per episode. A NEW episode — band rebuilt by a
    recentre, or the breach flipping direction — has different details and
    wakes normally. A standing answered breach still gets fresh judgment
    from the daily floor call, and a dry ladder still trips
    [GRID_DEGENERATE].

    Fails open (False → wake) on any DB/parse error.
    """
    import json as _json
    import time as _time
    try:
        rows = conn.execute(
            # W1 is the breach wake question (Fix 4); T2 rows are the
            # pre-Fix-4 wake history for the same condition — both count
            # as "this episode was already answered".
            "SELECT details FROM magi_gate_events "
            "WHERE trigger_id IN ('W1', 'T2') AND fired=1 "
            "AND consumed_in_cycle LIKE 'cyc%' "
            "AND timestamp >= ?",
            (_time.time() - 48 * 3600,),
        ).fetchall()
    except Exception as e:
        log.warning("_t2_episode_already_answered: DB read failed (%s) — "
                    "not suppressing", e)
        return False
    for r in rows:
        try:
            det = _json.loads(r["details"]) if r["details"] else {}
            if det.get("direction") != direction:
                continue
            du, dl = float(det["upper"]), float(det["lower"])
        except (KeyError, TypeError, ValueError):
            continue
        # Stored band values are rounded to 5dp; one spacing step apart is
        # >= ~0.3%, so a 0.01% relative tolerance separates bands cleanly.
        if abs(du - upper) < 1e-4 * upper and abs(dl - lower) < 1e-4 * lower:
            return True
    return False


def _dwell_t2(conn, age_min: float, dwell_min: float) -> tuple:
    """T2 dwell: price must have stayed beyond the SAME grid boundary for
    >= dwell_min, measured on 1m candles (a true continuous dwell, matching
    the operator's 'remains above/below for X minutes'). Falls back to event
    age + latest 1h close when 1m history is too short (startup / REST
    fallback path, which only writes 1h candles).

    A sustained breach additionally passes the episode guard
    (_t2_episode_already_answered): if a council cycle already consumed a T2
    fire for this same direction + band, the event is dropped
    ('drop_answered') instead of re-waking — one wake per breach episode."""
    _, upper, lower = _grid_band(conn)
    if upper is None:
        return "defer", "no grid_state for band"

    def _side(c: float):
        if c > upper:
            return "above"
        if c < lower:
            return "below"
        return None

    def _wake_or_answered(side: str, wake_reason: str) -> tuple:
        if _t2_episode_already_answered(conn, side, upper, lower):
            return "drop_answered", (
                f"breach {side} already answered by a council cycle for "
                f"this band — one wake per episode")
        return "wake", wake_reason

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
            return _wake_or_answered(
                newest, f"price {newest} band for >= {need}min (1m)")
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
        return _wake_or_answered(
            side, (f"price {side} band, event age {age_min:.0f}min >= "
                   f"{dwell_min:.0f}min (1h fallback)"))
    return "defer", (f"breach {side}, age {age_min:.0f}min < "
                     f"{dwell_min:.0f}min (1h fallback)")


def _t16_rung_already_answered(conn, rung: int) -> bool:
    """One council wake per drawdown rung: True when a prior T16 fire at the
    SAME or DEEPER rung was consumed by a real council cycle within the last
    7 days (the signal's own lookback window). A deepening drawdown that
    crosses into a new rung wakes again; an unchanged answered rung does
    not. Sentinel consumptions don't count. Fails open (False -> wake)."""
    import json as _json
    import time as _time
    try:
        rows = conn.execute(
            "SELECT details FROM magi_gate_events "
            "WHERE trigger_id='T16' AND fired=1 "
            "AND consumed_in_cycle LIKE 'cyc%' "
            "AND timestamp >= ?",
            (_time.time() - 7 * 86400,),
        ).fetchall()
    except Exception as e:
        log.warning("_t16_rung_already_answered: DB read failed (%s) — "
                    "not suppressing", e)
        return False
    for r in rows:
        try:
            det = _json.loads(r["details"]) if r["details"] else {}
            if int(det.get("rung") or 0) >= rung:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _dwell_t16(conn, details: dict, age_min: float, dwell_min: float) -> tuple:
    """T16 dwell: drawdown-from-7d-high is a slow signal, so the age dwell
    is mostly a formality — the real spend control is the rung-episode
    guard (one council wake per integer rung of drawdown depth)."""
    rung = int(details.get("rung") or 0)
    if rung < 1:
        return "drop", "drawdown recovered above first rung"
    if _t16_rung_already_answered(conn, rung):
        return "drop_answered", (
            f"drawdown rung {rung} already answered by a council cycle — "
            f"one wake per rung")
    if age_min >= dwell_min:
        return "wake", (f"drawdown rung {rung} "
                        f"({details.get('drawdown_pct')}% from 7d high) "
                        f"persisted >= {dwell_min:.0f}min")
    return "defer", (f"drawdown rung {rung}, age {age_min:.0f}min < "
                     f"{dwell_min:.0f}min")


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
    global running, _last_magi_cycle_at

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

    # Startup council gate (Fix 4, 2026-06-11 — replaces the 30-min
    # debounce). Every restart used to fire a ~6-call council cycle unless
    # the prior one was <30 min old; with several restarts a day that was
    # pure spend with 0 audited yield. A restart now wakes the council ONLY
    # when something a council cycle could act on actually changed:
    #   (a) the config fingerprint differs from the last cycle's
    #       (personas/models/floors changed — the council should re-judge
    #       under the new configuration), or
    #   (b) unconsumed fired wake-class (W) events are pending (a real wake
    #       was in flight when the service stopped), or
    #   (c) price has left the current grid band (the breach question —
    #       same condition W1 asks; a restart shouldn't dodge it).
    # Otherwise: seed the throttle/backstop baseline and stay quiet — the
    # daily floor and the 25h backstop carry the cadence. Fails open
    # (wake) on any check error: a broken check must not silence the
    # council indefinitely.
    try:
        from database import get_conn
        startup_wake_reason = None
        conn = get_conn()
        try:
            last = conn.execute(
                "SELECT timestamp, config_version FROM debate_records "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last is None:
                startup_wake_reason = "no prior council cycle on record"
            else:
                # Seed throttle/backstop baseline from the DB row
                # regardless of whether we wake.
                last_ts = datetime.fromisoformat(last['timestamp'])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                _last_magi_cycle_at = last_ts

                # (a) config fingerprint changed
                try:
                    # Composes the SAME full version stamped on
                    # debate_records rows (council half from disk + floor
                    # half) — see council_v2.current_config_fingerprint.
                    from magi.council_v2 import current_config_fingerprint
                    cfg_now, _ = current_config_fingerprint()
                    if cfg_now and last['config_version'] and \
                            cfg_now != last['config_version']:
                        startup_wake_reason = (
                            f"config changed ({last['config_version']} -> "
                            f"{cfg_now})")
                except Exception as e:
                    log.warning("startup gate: fingerprint check failed "
                                "(%s) — skipping condition (a)", e)

                # (b) unconsumed fired wake-class events
                if startup_wake_reason is None:
                    ph = ",".join("?" for _ in WAKE_CLASS_TRIGGERS)
                    pend = conn.execute(
                        f"SELECT trigger_id FROM magi_gate_events "
                        f"WHERE consumed_in_cycle IS NULL AND fired=1 "
                        f"AND trigger_id IN ({ph}) LIMIT 1",
                        tuple(WAKE_CLASS_TRIGGERS),
                    ).fetchone()
                    if pend:
                        startup_wake_reason = (
                            f"unconsumed wake event {pend['trigger_id']} "
                            f"pending from before restart")

                # (c) price outside the current grid band
                if startup_wake_reason is None:
                    try:
                        _, b_upper, b_lower = _grid_band(conn)
                        px = engine.get_current_price()
                        if (b_upper is not None and px is not None
                                and (px > b_upper or px < b_lower)):
                            startup_wake_reason = (
                                f"price {px} outside grid band "
                                f"[{b_lower:.4f}, {b_upper:.4f}]")
                    except Exception as e:
                        log.warning("startup gate: band check failed (%s) "
                                    "— skipping condition (c)", e)
        finally:
            conn.close()

        if startup_wake_reason:
            log.info("Startup council gate: WAKING — %s", startup_wake_reason)
            run_magi_cycle(trigger='startup')
        else:
            log.info(
                "Startup council gate: quiet restart — config unchanged, "
                "no pending wake events, price in band. No startup cycle "
                "(daily floor / 25h backstop / W-wakes carry the cadence)."
            )
    except Exception as e:
        log.warning(f"Startup gate check failed, running cycle anyway: {e}")
        run_magi_cycle(trigger='startup')

    last_observer_time = datetime.now(timezone.utc)

    # Initialize the daily-floor dedupe from DB so a restart inside the
    # daily hour doesn't re-fire a scheduled call that already ran today.
    # Phase 5 writes to debate_records; legacy magi_decisions is sparse and
    # cannot be used as a debounce source.
    last_scheduled_date = None
    try:
        import pytz
        from database import get_conn
        conn = get_conn()
        row = conn.execute(
            "SELECT timestamp FROM debate_records "
            "WHERE trigger='scheduled' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row and row['timestamp']:
            est = pytz.timezone('US/Eastern')
            last_dt = datetime.fromisoformat(row['timestamp']).replace(
                tzinfo=timezone.utc).astimezone(est)
            now_est = datetime.now(timezone.utc).astimezone(est)
            if (last_dt.date() == now_est.date() and
                    last_dt.hour >= MAGI_DAILY_HOUR_EST):
                last_scheduled_date = last_dt.date()
                log.info(f"Scheduler restart: daily MAGI call already ran "
                         f"at {last_dt.strftime('%H:%M')} EST today — "
                         f"not re-firing")
    except Exception as e:
        log.warning(f"Could not read last scheduled MAGI time from "
                    f"debate_records: {e} — daily-floor dedupe starts empty")

    log.info(
        f"Scheduler running — observer every {OBSERVER_INTERVAL_MINUTES}min, "
        f"council cadence gate-primary (daily floor at "
        f"{MAGI_DAILY_HOUR_EST:02d}:00 EST, max-silence backstop "
        f"{MAGI_MAX_SILENCE_HOURS}h)"
    )

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

        # Council cadence (gate-primary, BU-2 2026-06-09). Priority order:
        #   1. Daily clock floor — one scheduled call per EST day in the
        #      MAGI_DAILY_HOUR_EST hour (end-of-day assessment, grid or no
        #      grid). Dedupe is the per-DATE check in should_run_magi(), so
        #      it cannot re-fire minute-by-minute within the hour (the
        #      2026-05-18 47-cycles-in-60-min bug class).
        #   2. Max-silence backstop — if NO cycle of any kind has run in
        #      MAGI_MAX_SILENCE_HOURS (e.g. the service was down across the
        #      daily slot), force one rather than skip a whole day.
        #   3. Gate wakes — every call in between is decided by the free
        #      always-on gate (wake-class triggers, throttle + dwell below).
        if should_run_magi(now_est, last_scheduled_date):
            run_magi_cycle(trigger='scheduled')
            last_scheduled_date = now_est.date()
        elif (_last_magi_cycle_at is not None
              and (now_utc - _last_magi_cycle_at).total_seconds() / 3600.0
              >= MAGI_MAX_SILENCE_HOURS):
            log.info(
                "Max-silence backstop: no council cycle in >= %dh — "
                "forcing one", MAGI_MAX_SILENCE_HOURS,
            )
            run_magi_cycle(trigger='backstop_silence')
        else:
            # Off-schedule gate wake: a wake-class trigger fired since the
            # last cycle. Throttled to >= WAKE_MIN_INTERVAL_MIN since ANY
            # MAGI cycle so a depleting book gets the council involved within
            # the hour instead of waiting up to a day for the floor call —
            # without open-ended spend.
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
                        if status in ("drop", "drop_answered"):
                            log.info(
                                "gate_wake_dropped: %s — %s; consuming "
                                "event, no wake", pending, reason,
                            )
                            _consume_wake_gate_event(
                                pending,
                                "wake_episode_answered"
                                if status == "drop_answered"
                                else "wake_breach_cleared")
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
