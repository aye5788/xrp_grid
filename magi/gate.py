"""
magi/gate.py — gate layer for MAGI.

Trip-wire predicates evaluated at the end of every observer poll. Each
fire is recorded to the magi_gate_events table. The orchestrator reads
unconsumed events at build_world_state time, surfaces them via
world_state["triggers_since_last_cycle"], and marks them consumed once
the cycle's debate_records row commits.

Active triggers (calibrated against 8 years of XRP/USD hourly history;
see /tmp/xrp_gate_calibration.md):
  T1  Velocity spike      — intra-hour |H-L|/L > 0.030
  T2  Grid level breach   — 2+ consecutive completed 1h closes
                            outside outermost grid level
  T3  Rapid traversal     — 4+ level lines crossed in last 1h candle
  T4  Fill drought        — hours_since_last_fill crosses ABOVE 24
                            (edge-triggered)
  T6  Scorer rank-1 lift  — rank-1 PnL ≥ deployed PnL × 1.50 AND
                            rank-1 has been stable for 3 consecutive
                            evaluations
  T7  Scorer acceptability— had no acceptable variant prior, now has one
  T11 Vol regime change   — LOW/MEDIUM/HIGH classification flipped
  T12 ADX threshold cross — ADX crossed above 25 or below 20
  T13 VWAP dev crossing   — vwap_dev_pct crossed above +1.0 or below -1.0
  T14 Book one-sided      — resting book transitions into one-sided (one
                            side 0 orders, other >=1); edge-triggered
  T15 Skew drift          — |skew_delta_since_rebuild| crosses above 0.10
                            (matches Melchior Step 4 RECENTRE); edge-triggered
  T16 Drawdown rung       — latest 1h close >= 1 full grid-band-width below
                            the trailing 7d high (level-based detection; the
                            scheduler dedupes wakes to ONE per integer rung
                            of drawdown depth — added 2026-06-10 so the
                            council is convened on the downtrend-bleed
                            failure mode itself, not only on its geometry
                            side-effects like T2)

T14/T15 are BOOK-COMPOSITION triggers, evaluated by
evaluate_book_state_triggers() — called both on the live fill-reconcile
path (near-real-time after a fill drains a side) and from evaluate_gate
(hourly). They are the gate's eyes on the "trending market drains one
side of the grid" failure that the market-movement triggers (T1-T3, T11-
T13) and the 24h drought (T4) do not see.

Cut triggers (T5, T8, T9, T10) intentionally not implemented — see
xrp_gate_calibration.md for justification.

Each trigger function takes the data it needs as arguments and returns
(fired: bool, details: dict). evaluate_gate() is the top-level entry
point called by observer.poll_cycle.

Non-blocking by design: evaluate_gate wraps every trigger in try/except.
A failure in one trigger never blocks the others, and a failure in the
gate as a whole never blocks observer or trading.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)


# Thresholds — final values from xrp_gate_calibration.md. Do not tune
# here without re-running the calibration; live drift should be tracked
# via the gate's event history first.
T1_VELOCITY_THRESHOLD = 0.030
T2_BREACH_COUNT = 2
T3_TRAVERSAL_COUNT = 4
T4_DROUGHT_HOURS = 24.0
T6_PNL_IMPROVEMENT = 0.50
T6_STABILITY_BARS = 3
T12_ADX_HIGH = 25.0
T12_ADX_LOW = 20.0
T13_VWAP_DEV = 1.0  # +/- 1.0% absolute
T15_SKEW_DELTA_THRESHOLD = 0.10  # |skew drift since rebuild| — matches
#                                  Melchior Step 4 RECENTRE trigger (±0.1).
#                                  Structural reuse of an in-use threshold,
#                                  not a new market-statistics calibration.

# Scorer lookback (matches orchestrator + scorer convention)
SCORER_LOOKBACK_HOURS = 720


# --- Trigger functions (one per active trigger) ----------------------


def t1_velocity_spike(closes_1h: list,
                      k: float = T1_VELOCITY_THRESHOLD) -> tuple:
    """Velocity spike: |close_now - close_prior| / close_prior > k,
    where close_prior is the previous completed 1h bar's close.

    Matches the calibration predicate in /tmp/xrp_gate_calibration.py
    (`df["close"].pct_change().abs() > k`). 1h granularity — sub-hour
    candles are not available in observer.db.

    `closes_1h` is the same list passed to t2_grid_breach: rows of
    {timestamp, high, low, close} ordered most-recent-first, length
    >= 2 for the predicate to evaluate.
    """
    if not closes_1h or len(closes_1h) < 2:
        return False, {"reason": "insufficient candle history (need >= 2)"}
    try:
        close_now = float(closes_1h[0]["close"])
        close_prior = float(closes_1h[1]["close"])
        if close_prior <= 0:
            return False, {"reason": "non-positive prior close"}
    except (KeyError, TypeError, ValueError):
        return False, {"reason": "bad candle data"}
    move = abs(close_now - close_prior) / close_prior
    fired = move > k
    return fired, {
        "max_move_pct": round(move, 5),
        "threshold": k,
        "close_now": close_now,
        "close_prior": close_prior,
        "candle_ts": closes_1h[0].get("timestamp"),
    }


def t16_drawdown_rung(closes_1h: list, high_7d: Optional[float],
                      grid_state: Optional[dict]) -> tuple:
    """Sustained-drawdown trigger: the latest completed 1h close sits at
    least one full grid-band-width below the trailing 7d high. The rung is
    floor(drawdown_pct / band_width_pct) — drawdown depth measured in whole
    grids. Detection is LEVEL-based (fires every eval while the drawdown
    persists, same observability model as T2); the scheduler's rung-episode
    guard dedupes the actual council wakes to one per rung, so the council
    answers the downtrend question once per band-width of deepening, not
    hourly. Threshold is anchored to grid geometry (spacing x level pairs),
    not fitted to history: one band-width down means the grid has been
    pushed through a full ladder of levels since the weekly high — the
    recentre-into-the-fall regime the system's known bleed mode lives in."""
    if not closes_1h:
        return False, {"reason": "no candle history"}
    if not high_7d or high_7d <= 0:
        return False, {"reason": "no 7d high"}
    if not grid_state or grid_state.get("spacing_pct") is None:
        return False, {"reason": "no grid_state"}
    try:
        close = float(closes_1h[0]["close"])
        spacing = float(grid_state["spacing_pct"])
        levels = int(grid_state.get("levels") or 5)
    except (KeyError, TypeError, ValueError):
        return False, {"reason": "bad inputs"}
    n_pairs = max(1, levels // 2)
    band_width_pct = 2 * n_pairs * spacing * 100.0
    if band_width_pct <= 0:
        return False, {"reason": "degenerate band width"}
    dd_pct = max(0.0, (high_7d - close) / high_7d * 100.0)
    rung = int(dd_pct // band_width_pct)
    return rung >= 1, {
        "drawdown_pct": round(dd_pct, 3),
        "high_7d": round(high_7d, 5),
        "close": close,
        "band_width_pct": round(band_width_pct, 3),
        "rung": rung,
    }


def t2_grid_breach(closes_1h: list, grid_state: Optional[dict],
                   breach_count: int = T2_BREACH_COUNT) -> tuple:
    """2+ consecutive completed 1h closes outside the outermost grid
    level (closes_1h is most-recent-first, length >= breach_count)."""
    if not grid_state or grid_state.get("centre_price") is None \
            or grid_state.get("spacing_pct") is None:
        return False, {"reason": "no grid_state"}
    if len(closes_1h) < breach_count:
        return False, {"reason": "insufficient candle history"}
    try:
        centre = float(grid_state["centre_price"])
        spacing = float(grid_state["spacing_pct"])
        levels = int(grid_state.get("levels") or 5)
    except (TypeError, ValueError):
        return False, {"reason": "bad grid_state"}
    n_pairs = max(1, levels // 2)
    upper = centre * (1.0 + n_pairs * spacing)
    lower = centre * (1.0 - n_pairs * spacing)
    breached = []
    for c in closes_1h[:breach_count]:
        try:
            close = float(c["close"])
        except (KeyError, TypeError, ValueError):
            return False, {"reason": "bad close value"}
        if close > upper:
            breached.append(("above", close))
        elif close < lower:
            breached.append(("below", close))
        else:
            breached.append((None, close))
    # All `breach_count` closes must breach in the same direction
    directions = [b[0] for b in breached]
    fired = (all(d == "above" for d in directions)
             or all(d == "below" for d in directions))
    return fired, {
        "consecutive": breach_count,
        "upper": round(upper, 5),
        "lower": round(lower, 5),
        "direction": directions[0] if fired else None,
        "closes": [b[1] for b in breached],
    }


def t3_rapid_traversal(latest_candle_1h: Optional[dict],
                       grid_state: Optional[dict],
                       n_levels: int = T3_TRAVERSAL_COUNT) -> tuple:
    """Hourly H-L crosses n_levels+ grid-level lines.
    Approximation: spec is 15-min window; live data is 1h candles.
    Same proxy used by the calibration."""
    if not latest_candle_1h or not grid_state:
        return False, {"reason": "no candle or grid_state"}
    if grid_state.get("centre_price") is None \
            or grid_state.get("spacing_pct") is None:
        return False, {"reason": "no grid centre/spacing"}
    try:
        hi = float(latest_candle_1h["high"])
        lo = float(latest_candle_1h["low"])
        centre = float(grid_state["centre_price"])
        spacing = float(grid_state["spacing_pct"])
        levels = int(grid_state.get("levels") or 5)
    except (KeyError, TypeError, ValueError):
        return False, {"reason": "bad data"}
    n_pairs = max(1, levels // 2)
    level_lines = [centre]
    for i in range(1, n_pairs + 1):
        level_lines.append(centre * (1.0 + i * spacing))
        level_lines.append(centre * (1.0 - i * spacing))
    crossed = sum(1 for line in level_lines if lo <= line <= hi)
    fired = crossed >= n_levels
    return fired, {
        "crossed_count": crossed,
        "threshold": n_levels,
        "candle_ts": latest_candle_1h.get("timestamp"),
        "candle_high": hi,
        "candle_low": lo,
    }


def t4_fill_drought(hours_since_last_fill: Optional[float],
                     prior_hours_since_last_fill: Optional[float],
                     drought_hours: float = T4_DROUGHT_HOURS) -> tuple:
    """Edge-triggered: prior poll had hours_since_last_fill <= 24,
    current poll has > 24. First crossing only — won't re-fire while
    drought persists."""
    if hours_since_last_fill is None or prior_hours_since_last_fill is None:
        return False, {"reason": "missing hours_since_last_fill (current or prior)"}
    fired = (prior_hours_since_last_fill <= drought_hours
             and hours_since_last_fill > drought_hours)
    return fired, {
        "prior_hours": round(prior_hours_since_last_fill, 3),
        "current_hours": round(hours_since_last_fill, 3),
        "threshold": drought_hours,
    }


def t6_scorer_rank_improvement(rank1: Optional[tuple],
                                rank1_pnl: Optional[float],
                                deployed_pnl: Optional[float],
                                recent_rank1_history: list,
                                pnl_improvement: float = T6_PNL_IMPROVEMENT,
                                stability_bars: int = T6_STABILITY_BARS) -> tuple:
    """Fire when rank-1's expected_daily_pnl_pct >= deployed * (1+pnl_improvement)
    AND rank-1 (lc, sp) tuple has been identical for `stability_bars`
    consecutive scorer evaluations (counting the current evaluation).

    `recent_rank1_history` is a list of (lc, sp) tuples from the most
    recent N gate evaluations, ordered newest-first. The current
    evaluation's rank1 is NOT included in this list — caller supplies
    history of PRIOR evaluations.
    """
    if rank1 is None or rank1_pnl is None or deployed_pnl is None:
        return False, {"reason": "missing rank1 or pnl values"}
    if deployed_pnl == 0:
        # Compare absolute PnL when deployed is exactly zero (division
        # by zero guard). Improvement threshold becomes "any positive".
        meets_pnl = rank1_pnl > 0
    elif deployed_pnl > 0:
        meets_pnl = rank1_pnl >= deployed_pnl * (1.0 + pnl_improvement)
    else:
        # Deployed PnL is negative — any positive rank-1 PnL is a win.
        meets_pnl = rank1_pnl > 0
    if not meets_pnl:
        return False, {
            "reason": "pnl improvement insufficient",
            "rank1_pnl": rank1_pnl,
            "deployed_pnl": deployed_pnl,
            "improvement_needed": pnl_improvement,
        }
    # Stability: rank1 must match the last (stability_bars - 1) prior evals
    needed_prior = stability_bars - 1
    if needed_prior > 0:
        if len(recent_rank1_history) < needed_prior:
            return False, {
                "reason": "insufficient history for stability check",
                "history_len": len(recent_rank1_history),
                "stability_required": stability_bars,
            }
        prior_to_check = recent_rank1_history[:needed_prior]
        all_match = all(tuple(p) == tuple(rank1) for p in prior_to_check)
        if not all_match:
            return False, {
                "reason": "rank-1 not stable",
                "current": list(rank1),
                "recent": [list(p) for p in prior_to_check],
            }
    return True, {
        "rank1": list(rank1),
        "rank1_pnl_pct": rank1_pnl,
        "deployed_pnl_pct": deployed_pnl,
        "improvement_pct": (
            (rank1_pnl - deployed_pnl) / abs(deployed_pnl)
            if deployed_pnl != 0 else None
        ),
        "stability_required": stability_bars,
    }


def t7_scorer_acceptability_returned(current_has_acceptable: bool,
                                      prior_has_acceptable: Optional[bool]) -> tuple:
    """Fire when prior eval had no acceptable variant, current does.
    prior_has_acceptable=None (first ever evaluation) does not fire."""
    if prior_has_acceptable is None:
        return False, {"reason": "no prior acceptability state"}
    fired = (not prior_has_acceptable) and current_has_acceptable
    return fired, {
        "prior_has_acceptable": prior_has_acceptable,
        "current_has_acceptable": current_has_acceptable,
    }


def t11_vol_regime_transition(current_regime: Optional[str],
                               prior_regime: Optional[str]) -> tuple:
    """Fire on classification flip between LOW/MEDIUM/HIGH."""
    if current_regime is None or prior_regime is None:
        return False, {"reason": "missing regime label (current or prior)"}
    fired = current_regime != prior_regime
    return fired, {
        "prior": prior_regime,
        "current": current_regime,
    }


def t12_adx_threshold_cross(current_adx: Optional[float],
                             prior_adx: Optional[float],
                             high: float = T12_ADX_HIGH,
                             low: float = T12_ADX_LOW) -> tuple:
    """Fire when ADX crosses above `high` OR below `low` between
    consecutive polls."""
    if current_adx is None or prior_adx is None:
        return False, {"reason": "missing ADX (current or prior)"}
    crossed_above = prior_adx <= high < current_adx
    crossed_below = prior_adx >= low > current_adx
    fired = crossed_above or crossed_below
    direction = None
    if crossed_above:
        direction = "above_25"
    elif crossed_below:
        direction = "below_20"
    return fired, {
        "prior_adx": round(prior_adx, 4),
        "current_adx": round(current_adx, 4),
        "direction": direction,
        "thresholds": {"high": high, "low": low},
    }


def t13_vwap_dev_threshold(current_dev: Optional[float],
                            prior_dev: Optional[float],
                            threshold: float = T13_VWAP_DEV) -> tuple:
    """Fire when vwap_dev_pct crosses above +threshold OR below
    -threshold between consecutive polls. vwap_dev_pct is stored in
    PERCENT (observer multiplies by 100), so threshold is also percent."""
    if current_dev is None or prior_dev is None:
        return False, {"reason": "missing vwap_dev (current or prior)"}
    crossed_above = prior_dev <= threshold < current_dev
    crossed_below = prior_dev >= -threshold > current_dev
    fired = crossed_above or crossed_below
    direction = None
    if crossed_above:
        direction = "above_+1pct"
    elif crossed_below:
        direction = "below_-1pct"
    return fired, {
        "prior_vwap_dev_pct": round(prior_dev, 4),
        "current_vwap_dev_pct": round(current_dev, 4),
        "direction": direction,
        "threshold_abs_pct": threshold,
    }


def t14_book_one_sided(open_buys: int, open_sells: int,
                       prior_one_sided: Optional[bool]) -> tuple:
    """Edge-triggered: fires when the resting book transitions INTO a
    one-sided state — exactly one side has 0 orders while the other has
    >= 1. This is the classic 'trending market drained one side of the
    grid' failure: the round-trip leg can't be placed and inventory
    accumulates one-way. Fires once on the transition (prior eval was not
    one-sided); does NOT re-fire each poll while it stays one-sided.

    An empty book (0 buys AND 0 sells) is NOT one-sided — that is a
    no-grid state owned by GRID_DEGENERATE / the rebuild path, not this
    trigger."""
    total = open_buys + open_sells
    if total == 0:
        return False, {
            "reason": "empty book (no grid)",
            "open_buys": open_buys, "open_sells": open_sells,
            "one_sided": False,
        }
    # XOR: exactly one side empty
    one_sided = (open_buys == 0) != (open_sells == 0)
    depleted_side = None
    if one_sided:
        depleted_side = "buys" if open_buys == 0 else "sells"
    fired = one_sided and not bool(prior_one_sided)
    return fired, {
        "open_buys": open_buys,
        "open_sells": open_sells,
        "one_sided": one_sided,
        "depleted_side": depleted_side,
        "prior_one_sided": bool(prior_one_sided) if prior_one_sided is not None else None,
    }


def t15_skew_drift(skew_delta: Optional[float],
                   prior_breached: Optional[bool],
                   threshold: float = T15_SKEW_DELTA_THRESHOLD) -> tuple:
    """Edge-triggered: fires when |skew_delta_since_rebuild| crosses ABOVE
    `threshold` (0.10, matching Melchior Step 4's RECENTRE trigger).
    skew_delta is the change in inventory_skew since the last grid rebuild
    — positive = acquired XRP (buys filling), negative = shed XRP (sells
    filling). Crossing the threshold means the grid has drifted materially
    one-sided since it was last centred: an early warning BEFORE the book
    fully depletes (T14). Fires once on the crossing; prior_breached guards
    against re-firing while it stays above. Resets naturally at the next
    rebuild (skew_delta returns toward 0)."""
    if skew_delta is None:
        return False, {"reason": "no skew_delta (no rebuild marker or inventory)"}
    breached = abs(skew_delta) > threshold
    fired = breached and not bool(prior_breached)
    direction = None
    if breached:
        direction = "xrp_accumulating" if skew_delta > 0 else "xrp_shedding"
    return fired, {
        "skew_delta": round(skew_delta, 4),
        "threshold": threshold,
        "breached": breached,
        "direction": direction,
        "prior_breached": bool(prior_breached) if prior_breached is not None else None,
    }


# --- Top-level driver ------------------------------------------------


def _compute_scorer_state(conn) -> dict:
    """Run the scorer once at the current evaluation point and return
    {'rank1': (lc, sp) | None, 'rank1_pnl': float | None,
     'deployed_pnl': float | None, 'any_acceptable': bool}."""
    from magi.spacing_evaluator import DEFAULT_VARIANTS, score_variants
    from config import GRID_LEVEL_FEE_PER_SIDE

    candles = conn.execute(
        "SELECT timestamp, high, low FROM candles "
        "WHERE timeframe='1h' ORDER BY timestamp DESC LIMIT ?",
        (SCORER_LOOKBACK_HOURS,),
    ).fetchall()
    if not candles:
        return {"rank1": None, "rank1_pnl": None,
                "deployed_pnl": None, "any_acceptable": False}
    candles_list = [{"high": r["high"], "low": r["low"]} for r in candles]
    scored = score_variants(
        current_price=0.0,
        candles_1h=candles_list,
        fee_rate_per_side=GRID_LEVEL_FEE_PER_SIDE,  # maker: resting arms fill maker
        candidate_variants=DEFAULT_VARIANTS,
    )
    any_acceptable = any(v.get("acceptable") for v in scored)
    rank1 = next(
        (v for v in scored if v.get("acceptable") and v.get("rank") == 1),
        None,
    )
    rank1_tuple = None
    rank1_pnl = None
    if rank1:
        rank1_tuple = (rank1.get("levels"), rank1.get("spacing_pct"))
        rank1_pnl = rank1.get("expected_daily_pnl_pct")

    # Deployed PnL: find the live grid_state and pull the matching
    # variant's expected_daily_pnl_pct (if present in scored).
    gs = conn.execute(
        "SELECT levels, spacing_pct FROM grid_state "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    deployed_pnl = None
    if gs is not None and gs["levels"] is not None and gs["spacing_pct"] is not None:
        dep_key = (int(gs["levels"]), float(gs["spacing_pct"]))
        deployed = next(
            (v for v in scored
             if (v.get("levels"), v.get("spacing_pct")) == dep_key),
            None,
        )
        if deployed is not None:
            deployed_pnl = deployed.get("expected_daily_pnl_pct")
    return {
        "rank1": rank1_tuple,
        "rank1_pnl": rank1_pnl,
        "deployed_pnl": deployed_pnl,
        "any_acceptable": any_acceptable,
    }


def _recent_t6_rank1_history(conn, n: int = T6_STABILITY_BARS) -> list:
    """Return the rank-1 (lc, sp) tuple from the last `n` gate
    evaluations that recorded a T6 evaluation, newest-first. Used by
    t6_scorer_rank_improvement's stability check. Pulls from
    magi_gate_events.details JSON."""
    rows = conn.execute(
        "SELECT details FROM magi_gate_events "
        "WHERE trigger_id='T6_eval' "
        "ORDER BY id DESC LIMIT ?",
        (n,),
    ).fetchall()
    out: list = []
    for r in rows:
        if not r["details"]:
            continue
        try:
            d = json.loads(r["details"])
        except (ValueError, TypeError):
            continue
        rank1 = d.get("rank1")
        if rank1 and isinstance(rank1, (list, tuple)) and len(rank1) == 2:
            out.append(tuple(rank1))
    return out


def _prior_acceptability(conn) -> Optional[bool]:
    """Look at the most recent T7_eval row and return its
    `current_has_acceptable` flag. Returns None on first-ever evaluation."""
    row = conn.execute(
        "SELECT details FROM magi_gate_events "
        "WHERE trigger_id='T7_eval' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["details"]:
        return None
    try:
        d = json.loads(row["details"])
    except (ValueError, TypeError):
        return None
    return d.get("current_has_acceptable")


def _hours_since_last_fill(conn) -> Optional[float]:
    """Hours since the most recent grid_orders.filled_at. None if no fills."""
    row = conn.execute(
        "SELECT MAX(filled_at) AS lf FROM grid_orders "
        "WHERE status='filled' AND filled_at IS NOT NULL"
    ).fetchone()
    if not row or not row["lf"]:
        return None
    try:
        from datetime import datetime, timezone
        lf = row["lf"]
        if lf.endswith("Z"):
            lf = lf[:-1] + "+00:00"
        dt = datetime.fromisoformat(lf)
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.utcnow()
        delta = now - dt
        return delta.total_seconds() / 3600.0
    except Exception:
        return None


def _prior_hours_since_last_fill(conn) -> Optional[float]:
    """Read the most recent T4_eval gate event's recorded hours_since_last_fill.
    None if first evaluation."""
    row = conn.execute(
        "SELECT details FROM magi_gate_events "
        "WHERE trigger_id='T4_eval' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["details"]:
        return None
    try:
        d = json.loads(row["details"])
    except (ValueError, TypeError):
        return None
    return d.get("current_hours")


def _open_order_counts(conn) -> tuple:
    """(open_buys, open_sells) — count of resting orders per side."""
    rows = conn.execute(
        "SELECT side, COUNT(*) AS n FROM grid_orders "
        "WHERE status='open' GROUP BY side"
    ).fetchall()
    buys = sells = 0
    for r in rows:
        if r["side"] == "buy":
            buys = int(r["n"])
        elif r["side"] == "sell":
            sells = int(r["n"])
    return buys, sells


def _gate_skew_delta_since_rebuild(conn) -> Optional[float]:
    """Change in inventory_skew between the last grid rebuild and now.
    Mirrors orchestrator._skew_delta_since_rebuild so the gate computes
    the same quantity Melchior's Step 4 acts on, without importing the
    orchestrator (avoids a circular dependency)."""
    rb = conn.execute(
        "SELECT timestamp FROM grid_state "
        "WHERE notes LIKE 'Grid initialised%' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not rb or not rb["timestamp"]:
        return None
    inv_then = conn.execute(
        "SELECT inventory_skew FROM inventory WHERE timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (rb["timestamp"],),
    ).fetchone()
    inv_now = conn.execute(
        "SELECT inventory_skew FROM inventory ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not (inv_then and inv_now):
        return None
    try:
        return round(float(inv_now["inventory_skew"])
                     - float(inv_then["inventory_skew"]), 4)
    except (TypeError, ValueError):
        return None


def _prior_t14_one_sided(conn) -> Optional[bool]:
    """`one_sided` flag from the most recent T14_eval row. None if first
    evaluation."""
    row = conn.execute(
        "SELECT details FROM magi_gate_events WHERE trigger_id='T14_eval' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["details"]:
        return None
    try:
        return json.loads(row["details"]).get("one_sided")
    except (ValueError, TypeError):
        return None


def _prior_t15_breached(conn) -> Optional[bool]:
    """`breached` flag from the most recent T15_eval row. None if first
    evaluation."""
    row = conn.execute(
        "SELECT details FROM magi_gate_events WHERE trigger_id='T15_eval' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["details"]:
        return None
    try:
        return json.loads(row["details"]).get("breached")
    except (ValueError, TypeError):
        return None


def _write_event(conn, trigger_id: str, fired: bool, details: dict) -> None:
    """Insert a single magi_gate_events row. Timestamp is UTC unix epoch."""
    ts = datetime.utcnow().timestamp()
    conn.execute(
        "INSERT INTO magi_gate_events (timestamp, trigger_id, fired, details) "
        "VALUES (?, ?, ?, ?)",
        (ts, trigger_id, 1 if fired else 0, json.dumps(details, default=str)),
    )


def evaluate_book_state_triggers(db_path: str) -> list:
    """Evaluate the book-composition triggers (T14 one-sided, T15 skew
    drift) against current order-book + inventory state, write event rows
    (including the *_eval edge-state rows), and return fired trigger_ids.

    Kept separate from evaluate_gate so it can be called on the live
    fill-reconcile path — near-real-time after a fill drains a side —
    WITHOUT re-running the heavy hourly scorer triggers (T6/T7). evaluate_gate
    also calls this, so the book triggers are covered on the 1h-close cadence
    too. Edge state (T14_eval.one_sided, T15_eval.breached) is shared across
    both entry points via the magi_gate_events rows, so detection is
    consistent regardless of which path ran last.

    Non-blocking: own try/except per trigger; opens and commits its own
    connection (no overlap with evaluate_gate's connection)."""
    fired_ids: list = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        open_buys, open_sells = _open_order_counts(conn)
        skew_delta = _gate_skew_delta_since_rebuild(conn)

        # T14 — one-sided book (edge-triggered). Read prior edge state
        # BEFORE writing this eval's state row.
        try:
            prior_os = _prior_t14_one_sided(conn)
            t14_fired, t14_details = t14_book_one_sided(open_buys, open_sells, prior_os)
        except Exception as e:
            t14_fired, t14_details = False, {"error": repr(e)}
            log.warning("T14 raised: %s", e)
        _write_event(conn, "T14_eval", False, {
            "one_sided": bool(t14_details.get("one_sided", False)),
            "open_buys": open_buys, "open_sells": open_sells,
        })
        _write_event(conn, "T14", t14_fired, t14_details)
        if t14_fired:
            fired_ids.append("T14")

        # T15 — skew drift magnitude (edge-triggered).
        try:
            prior_br = _prior_t15_breached(conn)
            t15_fired, t15_details = t15_skew_drift(skew_delta, prior_br)
        except Exception as e:
            t15_fired, t15_details = False, {"error": repr(e)}
            log.warning("T15 raised: %s", e)
        _write_event(conn, "T15_eval", False, {
            "breached": bool(t15_details.get("breached", False)),
            "skew_delta": skew_delta,
        })
        _write_event(conn, "T15", t15_fired, t15_details)
        if t15_fired:
            fired_ids.append("T15")

        conn.commit()
    finally:
        conn.close()
    return fired_ids


def evaluate_gate(db_path: str) -> list:
    """Run all triggers at the current observer state. Writes one row
    per trigger to magi_gate_events (always — whether fired or not).
    Stateful triggers (T4, T6, T7) read prior state from prior _eval
    rows. Returns list of fired trigger_ids for logging.

    Non-blocking: any exception is logged and re-raised so the caller
    (observer.poll_cycle) can log + continue. The caller is responsible
    for the outer try/except that prevents gate failure from breaking
    the observer loop.
    """
    fired_ids: list = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # --- Gather observation state -------------------------------
        last2_candles = conn.execute(
            "SELECT timestamp, high, low, close FROM candles "
            "WHERE timeframe='1h' ORDER BY timestamp DESC LIMIT 2"
        ).fetchall()
        latest_candle = (dict(last2_candles[0]) if last2_candles else None)
        closes_1h = [dict(r) for r in last2_candles]

        last2_indicators = conn.execute(
            "SELECT timestamp, vol_regime, adx, vwap_dev_pct "
            "FROM indicators WHERE timeframe='1h' "
            "ORDER BY id DESC LIMIT 2"
        ).fetchall()
        current_ind = dict(last2_indicators[0]) if last2_indicators else {}
        prior_ind = dict(last2_indicators[1]) if len(last2_indicators) >= 2 else {}

        gs_row = conn.execute(
            "SELECT centre_price, spacing_pct, levels FROM grid_state "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        grid_state = dict(gs_row) if gs_row else None

        # --- Run triggers -------------------------------------------
        triggers: list = []

        # T1: velocity spike (close-to-close)
        try:
            fired, details = t1_velocity_spike(closes_1h)
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T1 raised: %s", e)
        triggers.append(("T1", fired, details))

        # T2: grid level breach
        try:
            fired, details = t2_grid_breach(closes_1h, grid_state)
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T2 raised: %s", e)
        triggers.append(("T2", fired, details))

        # T3: rapid traversal
        try:
            fired, details = t3_rapid_traversal(latest_candle, grid_state)
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T3 raised: %s", e)
        triggers.append(("T3", fired, details))

        # T4: fill drought (stateful — also writes T4_eval state row)
        try:
            current_hours = _hours_since_last_fill(conn)
            prior_hours = _prior_hours_since_last_fill(conn)
            fired, details = t4_fill_drought(current_hours, prior_hours)
            _write_event(
                conn, "T4_eval", False,
                {"current_hours": current_hours, "prior_hours": prior_hours},
            )
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T4 raised: %s", e)
        triggers.append(("T4", fired, details))

        # T6 / T7: scorer-driven (stateful — writes T6_eval and T7_eval)
        try:
            scorer_state = _compute_scorer_state(conn)
            history = _recent_t6_rank1_history(conn, n=T6_STABILITY_BARS - 1)
            t6_fired, t6_details = t6_scorer_rank_improvement(
                scorer_state["rank1"],
                scorer_state["rank1_pnl"],
                scorer_state["deployed_pnl"],
                history,
            )
            # Record the rank1 state for future stability checks regardless
            # of T6 firing — this is the substrate for the stability counter.
            _write_event(
                conn, "T6_eval", False,
                {
                    "rank1": list(scorer_state["rank1"]) if scorer_state["rank1"] else None,
                    "rank1_pnl": scorer_state["rank1_pnl"],
                    "deployed_pnl": scorer_state["deployed_pnl"],
                },
            )
            prior_accept = _prior_acceptability(conn)
            t7_fired, t7_details = t7_scorer_acceptability_returned(
                scorer_state["any_acceptable"],
                prior_accept,
            )
            _write_event(
                conn, "T7_eval", False,
                {"current_has_acceptable": scorer_state["any_acceptable"]},
            )
        except Exception as e:
            t6_fired, t6_details = False, {"error": repr(e)}
            t7_fired, t7_details = False, {"error": repr(e)}
            log.warning("T6/T7 raised: %s", e)
        triggers.append(("T6", t6_fired, t6_details))
        triggers.append(("T7", t7_fired, t7_details))

        # T11: vol_regime transition
        try:
            fired, details = t11_vol_regime_transition(
                current_ind.get("vol_regime"),
                prior_ind.get("vol_regime"),
            )
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T11 raised: %s", e)
        triggers.append(("T11", fired, details))

        # T12: ADX threshold cross
        try:
            fired, details = t12_adx_threshold_cross(
                current_ind.get("adx"),
                prior_ind.get("adx"),
            )
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T12 raised: %s", e)
        triggers.append(("T12", fired, details))

        # T13: VWAP deviation threshold
        try:
            fired, details = t13_vwap_dev_threshold(
                current_ind.get("vwap_dev_pct"),
                prior_ind.get("vwap_dev_pct"),
            )
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T13 raised: %s", e)
        triggers.append(("T13", fired, details))

        # T16: sustained drawdown from the trailing 7d high
        try:
            hrow = conn.execute(
                "SELECT MAX(high) AS h FROM candles WHERE timeframe='1h' "
                "AND timestamp >= datetime('now', '-7 days')"
            ).fetchone()
            high_7d = (float(hrow["h"])
                       if hrow and hrow["h"] is not None else None)
            fired, details = t16_drawdown_rung(closes_1h, high_7d, grid_state)
        except Exception as e:
            fired, details = False, {"error": repr(e)}
            log.warning("T16 raised: %s", e)
        triggers.append(("T16", fired, details))

        # --- Persist fire-event rows --------------------------------
        # Write a row for every trigger evaluated this poll. Fired rows
        # surface in world_state["triggers_since_last_cycle"]; quiet rows
        # are for audit and rate-limit observability.
        for trigger_id, fired, details in triggers:
            _write_event(conn, trigger_id, fired, details)
            if fired:
                fired_ids.append(trigger_id)

        conn.commit()
    finally:
        conn.close()

    # Book-composition triggers (T14/T15) — separate connection, shared
    # _eval edge state. Run after the main gate connection has closed so
    # there is no write-lock overlap. Also called independently on the
    # fill-reconcile path; here it ensures hourly-close coverage.
    try:
        fired_ids.extend(evaluate_book_state_triggers(db_path))
    except Exception as e:
        log.warning("evaluate_book_state_triggers raised: %s", e)

    return fired_ids
