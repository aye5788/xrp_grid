"""
orchestrator.py — debate-driven MAGI cycle (Phase 5).

Replaces the stateless three-call deliberation with a Letta-backed debate:
  1. Build world_state from DB
  2. Push it to the shared Letta world_state block
  3. Round 0: all three agents respond in parallel
  4. Detect conflict via CONFLICT_MATRIX
  5. Round 1 (only if conflict) — challenge the two conflicting agents
  6. resolve_consensus + enforce_hard_rules
  7. Write to debate_records (canonical source of truth)
  8. Return scheduler-compatible dict

Return shape preserved for scheduler.run_magi_cycle:
  {
    'melchior':  {'action', 'conviction', 'reasoning', 'centre_price', ...},
    'balthasar': {'action', 'conviction', 'reasoning'},
    'casper':    {'regime', 'conviction', 'reasoning'},
    'consensus': {
        'grid_action', 'risk_action', 'regime', 'reason',
        'melchior_geometry', 'melchior_conviction', 'deadlock',
        'hard_rule_overrides', 'cycle_id',
    },
    'decision_id': None,   # legacy magi_decisions writes retired post-Phase 5
  }

Note: regime-gate and magi_supervisor layers from the previous orchestrator
are intentionally NOT carried over — this prompt specifies a clean replacement.
Regime gate was a no-op in current config (REGIME_GATE_ENABLED=False) and the
supervisor is an orthogonal feature that can be re-attached in a later prompt.
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
import icontract

from database import (
    get_candles,
    get_conn,
    get_current_grid_state,
    get_latest_indicators,
    get_latest_inventory,
    get_latest_magi_decision_id,
    get_open_orders_summary,
    get_system_state,
    get_trajectory_context,
    insert_debate_record,
    insert_magi_decision,
)
from magi.spacing_evaluator import DEFAULT_VARIANTS, score_variants
from guardrails import check_all_guardrails
# Stage-3 hand-rolled arbiter council (sequential six-call choreography). This
# supersedes the ADK parallel-R0 / conditional-R1 engine in magi/council.py for
# the run_cycle path; council.py is left intact for any other importer.
from magi.council_v2 import run_council

load_dotenv()
log = logging.getLogger('magi.orchestrator')


HARD_RULES = {
    "max_allocation_skew": 0.85,
    "min_usd_buffer": 10.0,
    "min_xrp_buffer_usd": 10.0,
    "daily_loss_limit_pct": 0.15,
    "halt_file": "/root/xrp_grid/HALT",
    "max_grid_spacing_pct": 0.025,
    "min_grid_spacing_pct": 0.015,   # raised from 0.003 (2026-06-11) — must
                                     # match config.MIN_GRID_SPACING_PCT; the
                                     # 9.5y backtest killed sub-1.5% spacing
                                     # (fees ate 2/3 of gross; lost 9/10 yrs).
}

# --- Stage-4 item 2b: per-constraint council DISCLOSURE toggles ---
#
# Which survival constraints the council is allowed to SEE in world_state, and at
# what fidelity. "Work-within" constraints are disclosed as existence + CURRENT
# HEADROOM so the council reasons inside them. The two failure-case BREAKERS
# (daily_loss_limit, allocation_skew) default WITHHELD — their thresholds never
# reach world_state.
#
#   *** LOUD WARNING — budget-effect guard ***
#   Flipping a breaker to True exposes its proximity (its threshold, and for skew a
#   live headroom) to the council. That RE-ENABLES the budget effect: a council that
#   can see "you have room until the HALT ceiling" will steer toward it as a budget
#   rather than treating it as a hard floor. That is the exact failure mode this
#   redaction exists to prevent. Flip a breaker ON only as a DELIBERATE, PAPER-ONLY
#   experiment, never casually and never on a live book. The toggle state feeds the
#   config fingerprint (config_version), so any flip is recorded on every cycle.
CONSTRAINT_DISCLOSURE = {
    "usd_buffer":        True,   # work-within floor — existence + headroom
    "xrp_buffer":        True,   # work-within floor — existence + headroom
    "kill_switch":       True,   # existence fact only (operator can halt; no headroom)
    "daily_loss_limit":  False,  # BREAKER — withheld (budget-effect guard)
    "allocation_skew":   False,  # BREAKER — withheld (budget-effect guard)
}

# HARD_RULES keys that may still be dumped verbatim into world_state.hard_rules.
# The two breakers (max_allocation_skew, daily_loss_limit_pct) are EXCLUDED so their
# thresholds never reach the council via the hard_rules block; the kill-switch path
# (halt_file) is EXCLUDED too — the kill switch is disclosed as a bare existence fact
# in world_state.constraints, never as a filesystem path. The buffer floors and the
# engine spacing clamps remain (Balthasar cites the buffer floors; Melchior cites the
# spacing clamps), so those persona references stay valid.
_DISCLOSED_HARD_RULE_KEYS = (
    "min_usd_buffer",
    "min_xrp_buffer_usd",
    "max_grid_spacing_pct",
    "min_grid_spacing_pct",
)


# --- World state assembly ---

def _get_latest_market_knowledge():
    """Pull the latest market_knowledge row and parse stats_json."""
    conn = get_conn()
    row = conn.execute(
        "SELECT computed_at, data_from, data_to, total_bars, stats_json "
        "FROM market_knowledge ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    try:
        stats = json.loads(row['stats_json'] or '{}')
    except (ValueError, TypeError):
        stats = {}
    return {
        'computed_at': row['computed_at'],
        'data_from': row['data_from'],
        'data_to': row['data_to'],
        'total_bars': row['total_bars'],
        'stats': stats,
    }


def _last_in_scope_fill_row(columns: str):
    """Most recent filled grid_orders row IN THE CURRENT RUN SCOPE (paper fills
    during the paper run, Kraken txid fills otherwise — grid/pnl.py:
    fill_in_current_scope). Scoping added 2026-06-10: the unscoped "most recent
    fill" read happened to be correct during the paper run only because paper
    fills were newest; this makes world_state's fill recency immune to
    cross-scope rows. Scans recent rows newest-first; 500 covers any realistic
    backlog of out-of-scope rows on top."""
    from grid.pnl import current_scope_cutoff, fill_in_current_scope
    cutoff = current_scope_cutoff()
    conn = get_conn()
    rows = conn.execute(
        f"SELECT {columns} FROM grid_orders "
        "WHERE status='filled' AND filled_at IS NOT NULL "
        "ORDER BY filled_at DESC LIMIT 500"
    ).fetchall()
    conn.close()
    for row in rows:
        if fill_in_current_scope(row['order_id'], row['filled_at'], cutoff):
            return row
    return None


def _hours_since_last_fill() -> float | None:
    """Hours since the most recent IN-SCOPE grid_orders fill (current run
    scope), or None if no in-scope fills exist."""
    row = _last_in_scope_fill_row("order_id, filled_at")
    if not row or not row['filled_at']:
        return None
    try:
        last = datetime.fromisoformat(row['filled_at'])
    except ValueError:
        return None
    return round((datetime.utcnow() - last).total_seconds() / 3600, 2)


def _last_fill_summary() -> dict | None:
    """Summary of the most recent IN-SCOPE filled order (current run scope).
    Used by agents to reason about whether a recent fill represents an open
    position they should let the grid close, vs. a stale state that warrants
    RECENTRE."""
    row = _last_in_scope_fill_row(
        "order_id, side, price, size, fill_price, fee, filled_at"
    )
    if not row or not row['filled_at']:
        return None
    try:
        last_dt = datetime.fromisoformat(row['filled_at'])
    except ValueError:
        return None
    hours_ago = (datetime.utcnow() - last_dt).total_seconds() / 3600
    fill_price = float(row['fill_price'] or row['price'] or 0)
    size = float(row['size'] or 0)
    return {
        'order_id': row['order_id'],
        'side': row['side'],
        'price': round(fill_price, 5),
        'size_xrp': round(size, 4),
        'size_usd': round(size * fill_price, 2),
        'hours_ago': round(hours_ago, 2),
        'fee_usd': round(float(row['fee'] or 0), 4),
    }


def _position_state_summary(last_fill: dict | None,
                             grid_state: dict | None,
                             price: float | None) -> dict | None:
    """Where the round-trip closes + projected P&L if it does.

    For a recent BUY fill, the matching close is a SELL at fill_price *
    (1 + spacing); for a SELL fill, a BUY at fill_price * (1 - spacing).
    Reports distance to that level and net P&L after maker fees.

    Returns None if the inputs aren't enough to compute meaningfully.
    """
    if not (last_fill and grid_state and price):
        return None
    spacing = grid_state.get('spacing_pct')
    if spacing is None or float(spacing) <= 0:
        return None
    try:
        from config import MAKER_FEE
    except Exception:
        return None
    sp = float(spacing)
    fill_price = float(last_fill.get('price') or 0)
    size_xrp = float(last_fill.get('size_xrp') or 0)
    if fill_price <= 0 or size_xrp <= 0:
        return None

    if last_fill['side'] == 'buy':
        close_price = round(fill_price * (1 + sp), 5)
        gross = (close_price - fill_price) * size_xrp
    else:  # sell
        close_price = round(fill_price * (1 - sp), 5)
        gross = (fill_price - close_price) * size_xrp

    rt_fees = 2.0 * MAKER_FEE * size_xrp * close_price
    net = gross - rt_fees
    distance_pct = abs(float(price) - close_price) / float(price) * 100
    return {
        'nearest_close_arm_price': close_price,
        'round_trip_distance_pct': round(distance_pct, 3),
        'round_trip_gross_pnl_usd': round(gross, 4),
        'round_trip_net_pnl_usd': round(net, 4),
    }


def _skew_delta_since_rebuild() -> float | None:
    """Change in inventory_skew between the moment of the last grid rebuild
    and now. Positive = bot has acquired XRP since the rebuild (recent buys);
    negative = bot has shed XRP (recent sells). Tells the agents whether
    skew represents an open position from a recent fill or a pre-existing
    inventory state."""
    conn = get_conn()
    rb = conn.execute(
        "SELECT timestamp FROM grid_state "
        "WHERE notes LIKE 'Grid initialised%' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not rb or not rb['timestamp']:
        conn.close()
        return None
    inv_then = conn.execute(
        "SELECT inventory_skew FROM inventory WHERE timestamp <= ? "
        "ORDER BY timestamp DESC LIMIT 1",
        (rb['timestamp'],)
    ).fetchone()
    inv_now = conn.execute(
        "SELECT inventory_skew FROM inventory ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not (inv_then and inv_now):
        return None
    try:
        return round(float(inv_now['inventory_skew']) - float(inv_then['inventory_skew']), 4)
    except (TypeError, ValueError):
        return None


def _hours_since_last_rebuild() -> float | None:
    """Hours since the most recent grid rebuild (grid_state row whose notes
    begin with 'Grid initialised'). Returns None if no rebuild row exists.
    Same source-of-truth as the RECENTRE_COOLDOWN hard rule, exposed to
    agents so they can avoid voting RECENTRE during the cooldown window."""
    conn = get_conn()
    row = conn.execute(
        "SELECT timestamp FROM grid_state "
        "WHERE notes LIKE 'Grid initialised%' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or not row['timestamp']:
        return None
    try:
        last = datetime.fromisoformat(row['timestamp'])
    except ValueError:
        return None
    return round((datetime.utcnow() - last).total_seconds() / 3600, 2)


def _cooldown_status(open_orders: dict | None) -> dict:
    """RECENTRE cooldown state for world_state. Mirrors the
    [RECENTRE_COOLDOWN] gate in enforce_hard_rules: active when
    (last 'Grid initialised' row < 60 min ago) AND book healthy
    (buys>=3 AND sells>=2). Exposed so agents can read the same
    gate the rule layer enforces."""
    try:
        buy_n = int((open_orders or {}).get("buy_count") or 0)
        sell_n = int((open_orders or {}).get("sell_count") or 0)
    except (TypeError, ValueError):
        buy_n = sell_n = 0
    book_healthy = buy_n >= 3 and sell_n >= 2

    conn = get_conn()
    row = conn.execute(
        "SELECT timestamp FROM grid_state "
        "WHERE notes LIKE 'Grid initialised%' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or not row['timestamp']:
        return {
            "recentre_cooldown_active": False,
            "recentre_cooldown_minutes_remaining": None,
            "last_recentre_at_utc": None,
        }
    try:
        last_build = datetime.fromisoformat(row['timestamp'])
    except ValueError:
        return {
            "recentre_cooldown_active": False,
            "recentre_cooldown_minutes_remaining": None,
            "last_recentre_at_utc": row['timestamp'],
        }
    minutes_since = (datetime.utcnow() - last_build).total_seconds() / 60.0
    cooldown_active = book_healthy and minutes_since < 60
    minutes_remaining = max(0, int(60 - minutes_since)) if cooldown_active else 0
    return {
        "recentre_cooldown_active": cooldown_active,
        "recentre_cooldown_minutes_remaining": minutes_remaining,
        "last_recentre_at_utc": last_build.isoformat(),
    }


def _shadow_variants_for_world_state() -> list:
    """Return the 24-variant shadow table for Melchior's economic comparison.
    Each entry: {level_count, spacing_pct, expected_pnl_pct_per_round_trip,
    fill_count_24h, rolling_pnl_pct, last_fill_at}. Sourced from
    shadow_grid_state (populated by GridEngine.shadow_sim.persist_all)."""
    conn = get_conn()
    rows = conn.execute(
        '''SELECT level_count, spacing_pct, fill_count, rolling_pnl_pct,
                  expected_pnl_pct, state_blob
           FROM shadow_grid_state
           ORDER BY level_count, spacing_pct'''
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        last_fill_at = None
        blob_str = r['state_blob']
        if blob_str:
            try:
                blob = json.loads(blob_str)
                fills = blob.get('fills') or []
                if fills:
                    last_fill_at = fills[-1].get('timestamp')
            except (ValueError, TypeError):
                pass
        out.append({
            'level_count': r['level_count'],
            'spacing_pct': r['spacing_pct'],
            'expected_pnl_pct_per_round_trip': r['expected_pnl_pct'] or 0.0,
            'fill_count_24h': r['fill_count'] or 0,
            'rolling_pnl_pct': r['rolling_pnl_pct'] or 0.0,
            'last_fill_at': last_fill_at,
        })
    return out


def _current_variant_position(grid_state: dict | None) -> dict:
    """Return {level_count, spacing_pct} for the live grid, drawn from the
    same row build_world_state already fetched. None values if grid_state is
    missing."""
    gs = grid_state or {}
    return {
        'level_count': gs.get('levels'),
        'spacing_pct': gs.get('spacing_pct'),
    }


def _grid_position(grid_state: dict | None, price) -> dict | None:
    """Where current price sits relative to the grid's outer band — the same
    centre ± (n_pairs · spacing) envelope T2 (gate.t2_grid_breach) uses.

    Makes 'the grid has drifted off price and can no longer fill without a
    recentre' a first-class, legible signal for the council instead of
    something each agent has to infer from a buried T2 trigger detail.

    Returns None when grid_state / price are unavailable (no grid yet) —
    personas treat None (or fillable=True) as 'no stranding signal'. When a
    dict is returned it ALWAYS carries all three keys, so the world_state
    schema contract holds (grid_position.* declared in world_state_schema).
    """
    if not grid_state or price is None:
        return None
    centre = grid_state.get('centre_price')
    spacing = grid_state.get('spacing_pct')
    if centre is None or spacing is None:
        return None
    try:
        centre = float(centre)
        spacing = float(spacing)
        price = float(price)
        levels = int(grid_state.get('levels') or 5)
    except (TypeError, ValueError):
        return None
    n_pairs = max(1, levels // 2)
    upper = centre * (1.0 + n_pairs * spacing)
    lower = centre * (1.0 - n_pairs * spacing)
    if price > upper:
        side = "above"
        pct = round((price - upper) / upper * 100.0, 3)
    elif price < lower:
        side = "below"
        pct = round((lower - price) / lower * 100.0, 3)
    else:
        side = "inside"
        pct = 0.0
    return {
        "side": side,
        "pct_outside_band": pct,
        "fillable": side == "inside",
    }


def _score_current_config(candles: list, current_levels, current_spacing,
                            fee_rate_per_side: float):
    """Score the live grid's (levels, spacing) under the same analytical model
    so Melchior can compare rank-1 vs incumbent on equal footing. Returns
    the variant dict (with rank=1 by definition of a single-variant input),
    or None when the live spacing is missing / out of scorer bounds / no
    24h history. Never raises — failures degrade to None."""
    try:
        if current_levels is None or current_spacing is None:
            return None
        scored = score_variants(
            current_price=0.0,  # not used by the scorer (price cancels)
            candles_1h=candles,
            fee_rate_per_side=fee_rate_per_side,
            candidate_variants=[(int(current_levels), float(current_spacing))],
        )
        return scored[0] if scored else None
    except Exception as e:
        log.warning("Could not score current grid config: %s", e)
        return None


def _unconsumed_gate_events() -> tuple[list, Optional[int]]:
    """Return (events, max_id) where events is the list of unconsumed
    magi_gate_events rows with fired=1 since the last MAGI cycle, and
    max_id is the highest id at this read-point (used to mark consumed
    after debate_records insert, so triggers fired BETWEEN build and
    insert are still surfaced on the NEXT cycle rather than lost)."""
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT id, timestamp, trigger_id, details "
            "FROM magi_gate_events "
            "WHERE consumed_in_cycle IS NULL AND fired=1 "
            "ORDER BY id ASC"
        ).fetchall()
        max_row = conn.execute(
            "SELECT MAX(id) AS mid FROM magi_gate_events "
            "WHERE consumed_in_cycle IS NULL"
        ).fetchone()
        conn.close()
    except Exception as e:
        log.warning("Could not read unconsumed gate events: %s", e)
        return [], None

    events: list = []
    for r in rows:
        try:
            details = json.loads(r["details"]) if r["details"] else {}
        except (ValueError, TypeError):
            details = {}
        events.append({
            "trigger_id": r["trigger_id"],
            "timestamp": r["timestamp"],
            "details": details,
        })
    max_id = max_row["mid"] if max_row and max_row["mid"] is not None else None
    return events, max_id


def _mark_gate_events_consumed(cycle_id: str, ws_timestamp: str) -> None:
    """Mark all unconsumed gate event rows with timestamp at or before
    ws_timestamp (world_state's iso timestamp) as consumed by this
    cycle_id. Events fired AFTER world_state was built remain
    unconsumed and surface in the next cycle's window.

    magi_gate_events.timestamp is unix epoch; ws.timestamp is iso UTC.
    Convert ws_timestamp to a unix epoch upper bound here."""
    try:
        from datetime import datetime, timezone
        ts = ws_timestamp
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        upper_epoch = dt.timestamp()
    except Exception as e:
        log.warning("Could not parse ws_timestamp=%s: %s", ws_timestamp, e)
        return
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE magi_gate_events SET consumed_in_cycle=? "
            "WHERE consumed_in_cycle IS NULL AND timestamp <= ?",
            (cycle_id, upper_epoch),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("Could not mark gate events consumed for %s: %s",
                    cycle_id, e)


def _build_constraint_disclosure(usd_held, xrp_value_usd, allocation_skew) -> dict:
    """Stage-4 item 2b: the curated 'work-within' constraint block for the council.

    Each constraint is gated by CONSTRAINT_DISCLOSURE. Disclosed buffer floors carry
    existence (the floor value) AND current headroom (how close we are right now), so
    the council reasons inside the floor instead of being surprised by it. The kill
    switch is disclosed as a bare existence fact (operator can halt at any time) — no
    path, no headroom.

    The two failure-case breakers (daily_loss_limit, allocation_skew) default WITHHELD
    in CONSTRAINT_DISCLOSURE, so they are OMITTED ENTIRELY here — their thresholds
    never enter world_state and the council cannot steer toward them as a budget. They
    render only if a toggle is deliberately flipped on (see the loud warning on
    CONSTRAINT_DISCLOSURE); that path is paper-only and is recorded in the fingerprint.
    """
    usd = float(usd_held or 0.0)
    xrpv = float(xrp_value_usd or 0.0)
    out: dict = {}

    if CONSTRAINT_DISCLOSURE.get("usd_buffer"):
        floor = HARD_RULES["min_usd_buffer"]
        out["usd_buffer"] = {
            "floor_usd":   floor,
            "headroom_usd": usd - floor,   # >0 = above floor; <0 = breached
        }
    if CONSTRAINT_DISCLOSURE.get("xrp_buffer"):
        floor = HARD_RULES["min_xrp_buffer_usd"]
        out["xrp_buffer"] = {
            "floor_usd":   floor,
            "headroom_usd": xrpv - floor,
        }
    if CONSTRAINT_DISCLOSURE.get("kill_switch"):
        # Existence fact only — the operator can halt at any time. No path, no state.
        out["kill_switch"] = {"operator_can_halt": True}

    # --- failure-case breakers: withheld by default; render only if toggled on ---
    if CONSTRAINT_DISCLOSURE.get("allocation_skew"):
        ceiling = HARD_RULES["max_allocation_skew"]
        out["allocation_skew"] = {
            "ceiling_abs_skew": ceiling,
            "headroom":         ceiling - abs(float(allocation_skew or 0.0)),
        }
    if CONSTRAINT_DISCLOSURE.get("daily_loss_limit"):
        # Threshold only — live daily-PnL headroom would need a guardrails DB read;
        # the threshold itself is the budget-relevant disclosure.
        out["daily_loss_limit"] = {"floor_pct": HARD_RULES["daily_loss_limit_pct"]}

    return out


def _tape_verdict_block() -> dict:
    """Latest market-conditions verdict from the tape warehouse's signals_1h
    series (tape/history.db) — the anchored green/yellow/red the 9.5y backtest
    validated as a regime gate (Fix 3, 2026-06-11). Carries age_hours and a
    stale flag: the warehouse only advances while the tape collector runs
    (STOOD DOWN since 2026-06-09), so the council must treat a stale verdict
    as MISSING evidence, never as current truth. Never raises."""
    out = {"verdict": None, "vol_status": None, "regime_status": None,
           "drawdown_pct": None, "age_hours": None, "stale": True}
    try:
        import sqlite3 as _sq
        conn = _sq.connect("file:/root/xrp_grid/tape/history.db?mode=ro",
                           uri=True, timeout=5)
        row = conn.execute(
            "SELECT ts_begin, verdict, vol_status, regime_status, drawdown_pct "
            "FROM signals_1h ORDER BY ts_begin DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            age_h = (datetime.utcnow().timestamp() - row[0] / 1000.0) / 3600.0
            out.update({
                "verdict": row[1],
                "vol_status": row[2],
                "regime_status": row[3],
                "drawdown_pct": row[4],
                "age_hours": round(age_h, 1),
                # one hourly snapshot of slack + margin: anything older than
                # 3h means the pipeline is not keeping up.
                "stale": age_h > 3.0,
            })
    except Exception as e:
        log.warning("tape verdict read failed (emitting stale/None): %s", e)
    return out


def _exposure_cap_block() -> dict:
    """Down-walk exposure-cap state (Fix 2) for the council's eyes — the same
    system_state keys grid/engine.py enforces from. Never raises."""
    from config import DOWN_WALK_CAP_STREAK, DOWN_WALK_LINK_HOURS
    out = {"streak": 0, "threshold": DOWN_WALK_CAP_STREAK,
           "link_hours": DOWN_WALK_LINK_HOURS, "engaged": False}
    try:
        streak = int(get_system_state('down_walk_streak', default='0') or 0)
        out["streak"] = streak
        out["engaged"] = streak >= DOWN_WALK_CAP_STREAK
    except Exception as e:
        log.warning("exposure cap read failed (emitting zeros): %s", e)
    return out


def _council_stance_block() -> dict:
    """The council's current standing stance and how long it has held
    (Fix 3). Source: system_state keys written by run_cycle after each
    council decision. hours_in_stance is the anti-anchoring mirror — a seat
    seeing 'STAND_ASIDE held 90h' can ask whether conditions still justify
    it. Never raises."""
    out = {"stance": None, "since_utc": None, "hours_in_stance": None}
    try:
        stance = get_system_state('council_stance', default=None)
        since = get_system_state('council_stance_since', default=None)
        out["stance"] = stance or None
        out["since_utc"] = since or None
        if since:
            out["hours_in_stance"] = round(
                (datetime.utcnow() - datetime.fromisoformat(since))
                .total_seconds() / 3600.0, 1)
    except Exception as e:
        log.warning("council stance read failed (emitting None): %s", e)
    return out


def build_world_state() -> dict:
    """Snapshot of all market/portfolio context for the cycle."""
    from grid.engine import GridEngine
    from config import MAKER_FEE, GRID_LEVEL_FEE_PER_SIDE
    price = None
    try:
        price = GridEngine(paper=True).get_current_price()
    except Exception as e:
        log.warning("Could not fetch current price for world_state: %s", e)

    open_orders = get_open_orders_summary()
    grid_state = get_current_grid_state() or {}

    # Analytical variant scoring for Melchior — 720h of 1h candles, scored
    # against DEFAULT_VARIANTS (36 entries: 6 level-counts × 6 spacings).
    # Replaces fill-based shadow-sim spacing selection. Casper / Balthasar
    # don't act on these fields; harmless for them to see them in the shared
    # block. Failures here must not break the cycle — fall back to empty list
    # so Melchior degrades to MAINTAIN with target_spacing_pct=None.
    scored_top_10: list = []
    current_config_score = None
    try:
        candles_1h = get_candles('1h', limit=720)
        # get_candles returns DESC; the scorer doesn't care about order but
        # passing chronological is friendlier to anyone debugging the input.
        candles_1h = list(reversed(candles_1h))
        all_scored = score_variants(
            current_price=float(price or 0.0),
            candles_1h=candles_1h,
            fee_rate_per_side=GRID_LEVEL_FEE_PER_SIDE,  # maker: resting arms fill maker
            candidate_variants=DEFAULT_VARIANTS,
        )
        scored_top_10 = all_scored[:10]
        current_config_score = _score_current_config(
            candles_1h,
            grid_state.get('levels'),
            grid_state.get('spacing_pct'),
            GRID_LEVEL_FEE_PER_SIDE,
        )
    except Exception as e:
        log.warning("Variant scoring failed — Melchior will see empty list: %s", e)

    # Position-state context (independent of scoring path).
    last_fill_block = _last_fill_summary()
    position_state_block = _position_state_summary(
        last_fill_block, grid_state, price
    )

    # Portfolio metrics — single-sourced via magi/portfolio.py so agents and
    # the rule layer see identical values. Replaces the prior inline compute
    # in enforce_hard_rules (and resolves Balthasar's persona references to
    # world_state.portfolio.*, which previously pointed at a non-existent
    # namespace).
    from magi.portfolio import compute_portfolio_metrics
    inv = get_latest_inventory() or {}
    portfolio_block = compute_portfolio_metrics(
        inv.get("xrp_held"), inv.get("usd_held"), price,
    )

    # Drawdown from the trailing-7d high — a JUDGMENT INPUT for Balthasar's
    # capital-erosion read, not a threshold (no rule/gate keys off it). Running
    # peak includes current price so the value clamps to <= 0.0 (0.0 = at/above
    # the 7d high). Sourced from the same 1h candle series the variant scorer
    # uses (168 x 1h bars = 7d). None when price is unavailable or no candles.
    drawdown_from_high_7d = None
    try:
        if price is not None:
            dd_candles = get_candles('1h', limit=168)
            highs = [float(c['high']) for c in dd_candles if c.get('high') is not None]
            if highs:
                peak = max(max(highs), float(price))
                if peak > 0:
                    drawdown_from_high_7d = (float(price) - peak) / peak * 100.0
    except Exception as e:
        log.warning("drawdown_from_high_7d compute failed (emitting None): %s", e)

    ws = {
        "timestamp":                datetime.utcnow().isoformat(),
        "price":                    price,
        "indicators":               get_latest_indicators('1h') or {},
        "grid_state":               grid_state,
        "inventory":                inv,
        "open_orders":              open_orders,
        "hours_since_last_fill":    _hours_since_last_fill(),
        "hours_since_last_rebuild": _hours_since_last_rebuild(),
        "cooldown_status":          _cooldown_status(open_orders),
        "shadow_variants":          _shadow_variants_for_world_state(),
        "current_variant_position": _current_variant_position(grid_state),
        # Where price sits vs the grid band (T2 envelope). fillable=False =>
        # the grid has drifted off price and cannot fill until re-centred —
        # the council's signal that a RECENTRE is corrective, not trend-chasing.
        "grid_position":            _grid_position(grid_state, price),
        # Analytical scoring surface for Melchior — replaces the prior
        # shadow-fill-based spacing search.
        "scored_variants_top_10":   scored_top_10,
        "current_spacing_pct":      grid_state.get('spacing_pct'),
        "current_levels":           grid_state.get('levels'),
        "current_config_expected_daily_pnl_pct": (
            current_config_score.get('expected_daily_pnl_pct')
            if current_config_score else None
        ),
        # Hardcoded tier-0 today; future work: source from Kraken TradeVolume
        # (see 02_NEXT_BUILD_TASKS.md).
        "current_fee_tier_pct":     MAKER_FEE,
        # Signed-percent drawdown from the trailing-7d running peak (<= 0.0).
        # Risk-context judgment input for Balthasar — no rule keys off it.
        "drawdown_from_high_7d":    drawdown_from_high_7d,
        # Position-state context — surfaced so agents can reason about
        # whether they have an open position from a recent fill (and
        # therefore should HOLD, letting the grid close the round-trip)
        # vs. a stale state warranting RECENTRE. The [RECENT_POSITION_HOLD]
        # hard rule in enforce_hard_rules uses the same source data as
        # a deterministic backstop.
        "last_fill":                last_fill_block,
        "position_state":           position_state_block,
        "skew_delta_since_rebuild": _skew_delta_since_rebuild(),
        "trajectory":               get_trajectory_context(),
        "market_knowledge":         _get_latest_market_knowledge(),
        # Stage-4 item 2b: CURATED hard_rules — only the disclosed, non-breaker
        # thresholds (buffer floors + engine spacing clamps). The two failure-case
        # breakers (max_allocation_skew, daily_loss_limit_pct) and the kill-switch
        # path (halt_file) are NOT dumped here — see CONSTRAINT_DISCLOSURE and
        # world_state.constraints. The wholesale HARD_RULES dump is gone so a
        # withheld breaker's value can never reach the council via this block.
        "hard_rules":               {k: HARD_RULES[k] for k in _DISCLOSED_HARD_RULE_KEYS},
        # Stage-4 item 2b: the curated 'work-within' constraint disclosure — buffer
        # existence + CURRENT HEADROOM and the kill-switch existence fact, each gated
        # by CONSTRAINT_DISCLOSURE. Declared as one opaque type:"dict" FIELDS entry,
        # so the runtime drift validator blesses it without enumerating leaves and a
        # disclosure toggle can add/remove inner keys without firing drift.
        "constraints":              _build_constraint_disclosure(
            inv.get("usd_held"),
            portfolio_block.get("xrp_value_usd"),
            portfolio_block.get("allocation_skew"),
        ),
        # Derived portfolio metrics (xrp_value_usd, total_universe_usd,
        # xrp_pct_of_universe, allocation_skew). Single source of truth —
        # both the rule layer and Balthasar's persona read from here.
        "portfolio":                portfolio_block,
        # Fix 3 (2026-06-11): the three stance-mandate inputs. Each is one
        # opaque type:"dict" FIELDS entry (inner shape free to evolve).
        "tape_verdict":             _tape_verdict_block(),
        "exposure_cap":             _exposure_cap_block(),
        "council_stance":           _council_stance_block(),
    }

    # Gate trip-wire events since the last cycle. List of dicts:
    # {trigger_id, timestamp (unix), details}. Empty when the window was
    # routine. After insert_debate_record succeeds in run_cycle, rows
    # with timestamp <= ws.timestamp are marked consumed_in_cycle =
    # cycle_id so they don't re-surface next cycle.
    try:
        gate_events, _ = _unconsumed_gate_events()
    except Exception as e:
        log.warning("gate event read failed (non-fatal): %s", e)
        gate_events = []
    ws["triggers_since_last_cycle"] = gate_events

    # Runtime schema validation — fires critical alert on drift but never
    # blocks the cycle. Trading continues; the operator is paged via the
    # existing ntfy hook on critical-severity magi_alerts rows.
    try:
        from magi.world_state_schema import alert_on_runtime_drift
        alert_on_runtime_drift(ws)
    except Exception as e:
        log.warning("schema runtime validator raised (non-fatal): %s", e)

    return ws


# --- Hard-rule enforcement ---

def _check_council_degradation() -> dict:
    """
    Inspect the last 2 historical debate_records rows (already-written cycles
    only — enforce_hard_rules runs BEFORE the current cycle's row is inserted)
    and return per-agent degradation state.

    Degradation fingerprint matches magi/council.py:SAFE_DEFAULTS and the
    dashboard AGENT HEALTH tile: an R0 vote with conviction == 0.0 AND
    crux LIKE '(no response)%' is a parse-failure / model-degradation marker.

    Returns:
        {
            'evaluable':         bool,         # False when <2 rows exist
            'degraded_agents':   list[str],    # subset of ('casper','melchior','balthasar')
            'degraded_count':    int,          # 0..3
            'cycle_ids_checked': list[str],
        }
    """
    out = {
        'evaluable':         False,
        'degraded_agents':   [],
        'degraded_count':    0,
        'cycle_ids_checked': [],
    }
    try:
        conn = get_conn()
        rows = conn.execute(
            "SELECT cycle_id, "
            "       casper_r0_conviction,   casper_r0_crux, "
            "       melchior_r0_conviction, melchior_r0_crux, "
            "       balthasar_r0_conviction, balthasar_r0_crux "
            "FROM debate_records ORDER BY id DESC LIMIT 2"
        ).fetchall()
        conn.close()
    except Exception as e:
        log.warning("Council-degradation check: DB read failed: %s", e)
        return out
    if len(rows) < 2:
        return out

    out['evaluable'] = True
    out['cycle_ids_checked'] = [r['cycle_id'] for r in rows]
    for agent in ('casper', 'melchior', 'balthasar'):
        both_degraded = True
        for r in rows:
            conv = r[f"{agent}_r0_conviction"]
            crux = r[f"{agent}_r0_crux"] or ''
            conv_zero = (conv is None) or (abs(float(conv)) < 1e-9)
            if not (conv_zero and crux.startswith('(no response)')):
                both_degraded = False
                break
        if both_degraded:
            out['degraded_agents'].append(agent)
    out['degraded_count'] = len(out['degraded_agents'])
    return out


def _degradation_tier(count: int) -> int:
    """0 = healthy, 1 = single-agent degraded, 2 = council collapsed (≥2)."""
    if count <= 0:
        return 0
    if count == 1:
        return 1
    return 2


def _maybe_fire_degradation_alert(curr_count: int,
                                   degraded_agents: list,
                                   cycle_ids_checked: list) -> None:
    """Edge-triggered alert: only fire when current tier strictly exceeds
    previous tier. Recovery (tier going down or staying flat) is silent.
    Uses system_state['last_degraded_tier'] for cross-restart persistence."""
    try:
        from database import get_system_state, set_system_state, insert_alert
        prev_tier = int(get_system_state('last_degraded_tier', '0'))
    except Exception as e:
        log.warning("Degradation tier read failed: %s — skipping alert", e)
        return
    curr_tier = _degradation_tier(curr_count)

    if curr_tier > prev_tier:
        try:
            cycles_ref = ','.join(cycle_ids_checked) if cycle_ids_checked else '(none)'
            if curr_tier == 1:
                agent = degraded_agents[0] if degraded_agents else None
                insert_alert(
                    severity='critical',
                    category='council_degraded',
                    agent_id=agent,
                    message=(
                        f"Council degraded: agent={agent} returned "
                        f"SAFE_DEFAULTS (conviction=0, crux=(no response)) "
                        f"on the last 2 cycles ({cycles_ref}). "
                        f"Freezing grid at MAINTAIN/CLEAR until recovery."
                    ),
                )
            else:  # curr_tier == 2
                insert_alert(
                    severity='critical',
                    category='council_collapsed',
                    agent_id=None,
                    message=(
                        f"Council collapsed: {curr_count} of 3 agents "
                        f"degraded ({','.join(degraded_agents)}) for last 2 "
                        f"cycles ({cycles_ref}). HALTing engine."
                    ),
                )
        except Exception as e:
            log.warning("Could not insert degradation alert: %s", e)

    # Always persist the current tier so the next cycle's edge check is correct
    try:
        set_system_state('last_degraded_tier', str(curr_tier))
    except Exception as e:
        log.warning("Could not persist last_degraded_tier=%s: %s",
                    curr_tier, e)


_CANONICAL_OVERRIDE_TAGS = {
    # Council degradation
    "[AGENT_DEGRADED:casper]",
    "[AGENT_DEGRADED:melchior]",
    "[AGENT_DEGRADED:balthasar]",
    "[COUNCIL_COLLAPSED]",
    # Rule 0 — RECENTRE block
    "[GRID_HEALTHY_NO_RECENTRE]",
    "[RECENTRE_COOLDOWN]",
    "[RECENT_POSITION_HOLD]",
    # (The rule-0d council-veto tags — [REGIME_DEFER] / [REGIME_STANDDOWN] /
    # [BALTHASAR_HOLD_GEOMETRY] / [BALTHASAR_RISK_BLOCK] — were removed in Stage-4
    # item 2a. The structural veto now lives in the arbiter's synthesis vote
    # (council_v2.run_council); it downgrades a vetoed RECONFIGURE to THESIS_HOLDS
    # in-council, so enforce_hard_rules never sees a council-veto override to tag.)
    # Survival floors
    "[KILL_SWITCH]",
    "[DAILY_LOSS_LIMIT]",
    "[ALLOC_SKEW_CEILING]",
    "[USD_BUFFER_FLOOR]",
    "[XRP_BUFFER_FLOOR]",
    # Grid integrity / pause / geometry
    "[GRID_DEGENERATE]",
    "[PAUSE_INVALID]",
    "[GEOMETRY_INJECTED_FROM_SCORER]",
    "[NO_ACCEPTABLE_VARIANT]",
    # Melchior verdict-driven stand-down (NO_PROFITABLE_GRID -> GRID_PAUSE)
    "[NO_PROFITABLE_GRID]",
    # Council stance mandate (Fix 3, 2026-06-11) — NOT overrides OF the
    # council; these tag the deterministic ENFORCEMENT of the council's own
    # stance vote, so a blocked rebuild / forced PAUSE_LONGS is auditable.
    "[STANCE_HOLD]",
    "[STANCE_STAND_ASIDE]",
}
# AGENT_DEGRADED is emitted templated as "[AGENT_DEGRADED:<agent_id>]". The three
# valid agent_ids (casper/melchior/balthasar) are enumerated above rather than
# prefix-matched, so this stays a closed membership test and a malformed or
# unexpected agent_id correctly trips Invariant 2. ([GUARDRAILS_BLOCKED] is emitted
# by _early_halt_return, NOT by enforce_hard_rules, so it is deliberately absent.)

# Melchior's economic verdict -> the engine's grid_action vocabulary. council_v2
# passes the verdict through unflattened (consensus['grid_verdict']); the
# orchestrator owns this deterministic translation. NO_PROFITABLE_GRID resolves to
# GRID_PAUSE (stand down: cancel orders and idle) — NOT MAINTAIN. RECONFIGURE maps
# to RECENTRE. The structural council veto is no longer applied here (rule 0d was
# removed in Stage-4 item 2a): the arbiter's synthesis already downgraded a vetoed
# RECONFIGURE to THESIS_HOLDS in-council, so by the time grid_verdict reaches this
# map it reflects the post-veto call.
_VERDICT_TO_GRID_ACTION = {
    "THESIS_HOLDS":       "MAINTAIN",
    "RECONFIGURE":        "RECENTRE",
    "NO_PROFITABLE_GRID": "GRID_PAUSE",
}


# The contract predicate below delegates its set logic to this helper. Keeping
# set()/`or []` OUT of the @ensure lambda body matters: icontract re-walks the
# lambda AST to build the violation message, and it cannot recompute `set(x or [])
# - y` (raises 'bool' object is not iterable). An opaque helper call sidesteps that,
# and icontract still reports the helper's return value plus the
# `result.get("hard_rule_overrides")` argument in the failure message. The helper
# is also independently unit-testable.
#
# (Invariant 1 — the rule-0d coverage contract — and its _RULE_0D_* constants /
# _has_rule0d_* helpers were removed with rule 0d in Stage-4 item 2a. The structural
# veto now lives in the arbiter's synthesis vote, which downgrades a vetoed
# RECONFIGURE to THESIS_HOLDS in-council; there is no post-hoc coercion left to
# police here. Invariant 2 — override-tag integrity — is unchanged.)
def _unknown_override_tags(overrides):
    """Set of override tags that are NOT canonical (empty set when all valid)."""
    return set(overrides or []) - _CANONICAL_OVERRIDE_TAGS


@icontract.ensure(
    lambda result: _unknown_override_tags(result.get("hard_rule_overrides")) == set(),
    description=(
        "Invariant 2 (override-tag integrity): every entry in "
        "result['hard_rule_overrides'] must be a member of "
        "_CANONICAL_OVERRIDE_TAGS; _unknown_override_tags(...) reported above "
        "is the offending unknown tag(s)."
    ),
)
def enforce_hard_rules(consensus: dict, world_state: dict,
                        round_0: dict | None = None) -> dict:
    """
    Apply non-negotiable safety overrides on top of LLM consensus.
    Returns the (mutated copy of) consensus dict with a 'hard_rule_overrides'
    list of tags appended for transparency.

    When `round_0` is supplied, rule #8 (GEOMETRY_INJECTED_FROM_SCORER) may
    mutate `round_0['melchior']['geometry']` in place so the downstream
    `_final_consensus` helper picks the injected values up unchanged.
    The caller is expected to pass the same round_0 dict to _final_consensus.

    Precedence ladder — rules run in this order, and a LATER rule may overwrite
    an EARLIER rule's grid_action assignment. That is the precedence design, not
    a bug: survival and integrity rules outrank council judgment.
        0-pre. STANCE translation (Fix 3) — the arbiter's own mandate, enforced
               deterministically before the ladder: HOLD blocks a RECENTRE back
               to MAINTAIN; STAND_ASIDE additionally floors risk_action at
               PAUSE_LONGS (no buys, keep sells). A later integrity rule may
               still force a rebuild (e.g. GRID_DEGENERATE), but the PAUSE_LONGS
               floor survives on risk_action, so the buy-freeze holds.
        -1.  Council-degradation freeze → MAINTAIN (1 agent) / HALT (council collapsed)
        0a/0b/0c. RECENTRE block        → MAINTAIN (GRID_HEALTHY_NO_RECENTRE /
                                          RECENTRE_COOLDOWN / RECENT_POSITION_HOLD)
        1.   Kill switch                → HALT
        2.   Daily loss limit           → HALT
        3.   Allocation skew ceiling    → HALT
        4/5. USD / XRP buffer floors    → risk_action CLEAR (grid_action untouched)
        6.   Grid degenerate            → RECENTRE (fires only under stance
             DEPLOY/none — under HOLD/STAND_ASIDE a one-sided or inactive book
             is the council's mandate, and this gate doubles as STAND_ASIDE's
             exit: the first DEPLOY vote restores the full grid. Also dormant
             while the exposure cap is engaged: a forced RECENTRE under the
             cap rebuilds sells-only, so it can never cure buy_count=0 —
             it would just flap, paying a taker anchor per council cycle)
        7.   PAUSE_INVALID              → risk_action CLEAR
        8.   Geometry injection / no acceptable variant → GRID_PAUSE
    The structural COUNCIL VETO is no longer a rule here (rule 0d was removed in
    Stage-4 item 2a). The arbiter (Balthasar) now carries it in his synthesis vote:
    a HOLD_GEOMETRY / RISK_BLOCK over a RECONFIGURE is downgraded to THESIS_HOLDS
    in-council by council_v2, so grid_verdict already reflects the veto and the
    THESIS_HOLDS→MAINTAIN translation below holds the grid — no post-hoc coercion.
    Only Invariant 2 (override-tag integrity) remains; Invariant 1 went with rule 0d.
    """
    cons = dict(consensus)
    overrides = list(cons.get("hard_rule_overrides") or [])
    notes = [cons.get("reasoning", "")]

    # Translate Melchior's economic verdict (passed through unflattened by
    # council_v2 as consensus['grid_verdict'], already post-veto) into the engine's
    # grid_action vocabulary:
    #   THESIS_HOLDS       -> MAINTAIN    (hold the current grid)
    #   RECONFIGURE        -> RECENTRE    (rebuild to Melchior's geometry)
    #   NO_PROFITABLE_GRID -> GRID_PAUSE  (stand down: cancel orders and idle)
    # NO_PROFITABLE_GRID is a stand-down, NOT a hold — the engine's GRID_PAUSE path
    # cancels all orders and idles. The rest of the precedence ladder operates on
    # grid_action as before.
    grid_verdict = cons.get("grid_verdict") or "THESIS_HOLDS"
    cons["grid_action"] = _VERDICT_TO_GRID_ACTION.get(grid_verdict, "MAINTAIN")
    if grid_verdict == "NO_PROFITABLE_GRID":
        overrides.append("[NO_PROFITABLE_GRID]")
        notes.append(
            "[NO_PROFITABLE_GRID] Melchior: no acceptable variant — standing down "
            "(GRID_PAUSE: cancel orders and idle)"
        )

    # ---- Council STANCE mandate (Fix 3, 2026-06-11) ----
    # The arbiter's stance (cons['stance'], from Balthasar's synthesis
    # RiskVote) is the council's capital-deployment mandate. This is the
    # council's OWN judgment being enforced deterministically — the opposite
    # of an override of it:
    #   DEPLOY      -> verdict pipeline unchanged (the mapping above stands).
    #   HOLD        -> no NEW deployment: a RECENTRE (rebuild) is blocked back
    #                  to MAINTAIN; GRID_PAUSE (stand-down) still proceeds —
    #                  cancelling orders deploys nothing.
    #   STAND_ASIDE -> no buys, keep sells: grid_action MAINTAIN (no rebuild)
    #                  and risk_action floored at PAUSE_LONGS (the engine's
    #                  existing cancel-buys-keep-sells path). An explicit
    #                  HALT from the arbiter outranks the floor.
    # A MISSING stance (None — pre-Fix-3 rows, legacy replays) is a
    # passthrough: no stance enforcement here and rule 6 stays open, i.e.
    # exactly the pre-Fix-3 behavior. The live path always sets a stance
    # (council_v2 and the safe-hold cons both default HOLD). A GARBAGE
    # stance string degrades to HOLD — the conservative reading.
    stance = cons.get("stance")
    if stance is not None and stance not in ("DEPLOY", "HOLD", "STAND_ASIDE"):
        log.warning("Unknown council stance %r — treating as HOLD", stance)
        stance = "HOLD"
        cons["stance"] = stance
    if stance == "HOLD" and cons["grid_action"] == "RECENTRE":
        cons["grid_action"] = "MAINTAIN"
        overrides.append("[STANCE_HOLD]")
        notes.append(
            "[STANCE_HOLD] council stance HOLD — rebuild blocked, no new "
            "capital deployed (resting orders stay)"
        )
    elif stance == "STAND_ASIDE":
        if cons["grid_action"] == "RECENTRE":
            cons["grid_action"] = "MAINTAIN"
        if cons.get("risk_action") not in ("HALT", "PAUSE_LONGS"):
            cons["risk_action"] = "PAUSE_LONGS"
        overrides.append("[STANCE_STAND_ASIDE]")
        notes.append(
            "[STANCE_STAND_ASIDE] council stance STAND_ASIDE — buys off "
            "(PAUSE_LONGS), resting sells stay to work inventory off"
        )

    inventory = world_state.get("inventory") or {}
    xrp_held = float(inventory.get("xrp_held") or 0.0)
    usd_held = float(inventory.get("usd_held") or 0.0)
    skew = float(inventory.get("inventory_skew") or 0.0)
    price = world_state.get("price")
    # Read portfolio metrics from the single-sourced world_state.portfolio block
    # rather than recomputing xrp_value_usd inline. Same numbers the agents see.
    portfolio = world_state.get("portfolio") or {}
    xrp_value_usd = float(portfolio.get("xrp_value_usd") or 0.0)

    # -1. COUNCIL DEGRADATION — runs first because freeze-on-degraded must
    # short-circuit the rest of the council-trusting rules (RECENTRE/cooldown
    # gates, geometry injection, etc.). Safety conditions (kill switch, daily
    # loss limit, allocation skew ceiling) further down can still upgrade to
    # HALT — those don't rely on council judgment. Rule 6 (GRID_DEGENERATE)
    # explicitly skips if degraded freeze is in effect so we don't bypass
    # the freeze with a forced RECENTRE that has no usable geometry.
    degradation_state = _check_council_degradation()
    if degradation_state['evaluable'] and degradation_state['degraded_count'] > 0:
        deg_count = degradation_state['degraded_count']
        deg_agents = degradation_state['degraded_agents']
        if deg_count == 1:
            agent = deg_agents[0]
            cons["grid_action"] = "MAINTAIN"
            cons["risk_action"] = "CLEAR"
            overrides.append(f"[AGENT_DEGRADED:{agent}]")
            notes.append(
                f"[AGENT_DEGRADED:{agent}] last 2 cycles returned "
                f"SAFE_DEFAULTS for {agent}; freezing grid at MAINTAIN/CLEAR "
                f"until council recovers (existing orders continue to fill)"
            )
            log.warning(
                "Hard rule: AGENT_DEGRADED:%s — forcing MAINTAIN + CLEAR "
                "(cycles checked: %s)",
                agent, ','.join(degradation_state['cycle_ids_checked']),
            )
        else:  # 2 or 3
            cons["grid_action"] = "HALT"
            cons["risk_action"] = "HALT"
            overrides.append("[COUNCIL_COLLAPSED]")
            notes.append(
                f"[COUNCIL_COLLAPSED] {deg_count} of 3 agents "
                f"({','.join(deg_agents)}) returned SAFE_DEFAULTS for the "
                f"last 2 cycles; HALTing engine until council recovers"
            )
            log.warning(
                "Hard rule: COUNCIL_COLLAPSED — %d/3 agents degraded (%s); "
                "forcing HALT",
                deg_count, ','.join(deg_agents),
            )
        # Edge-triggered alert (only fires when tier increases)
        _maybe_fire_degradation_alert(
            deg_count, deg_agents, degradation_state['cycle_ids_checked']
        )
    elif degradation_state['evaluable']:
        # Healthy this cycle — make sure the persisted tier comes back down so
        # the next degradation transition correctly edge-triggers an alert.
        _maybe_fire_degradation_alert(0, [], degradation_state['cycle_ids_checked'])

    # 0. RECENTRE block — two complementary gates that can each downgrade a
    # council-proposed RECENTRE to MAINTAIN:
    #
    #   0a. [GRID_HEALTHY_NO_RECENTRE] — time-independent. Block RECENTRE when
    #       the book is bilateral AND price drift from centre is less than one
    #       full spacing step. Stops the every-4h MAGI cycle from tearing down
    #       a perfectly valid grid that simply hasn't filled yet because the
    #       hourly range is below the spacing band.
    #
    #   0b. [RECENTRE_COOLDOWN] — time-based. Block RECENTRE when the grid was
    #       rebuilt < 60 min ago and the book is healthy. Catches Melchior's
    #       repeated RECENTRE votes on stale-fill evidence.
    #
    # The grid-degenerate hard rule (#6) can still FORCE RECENTRE if the book
    # is actually one-sided. These gates only catch "healthy book, no need to
    # churn yet". PAUSE risk actions are neutralized in both branches because
    # a healthy / fresh balanced book shouldn't be partially cancelled on
    # stale risk reasoning.
    if cons.get("grid_action") == "RECENTRE":
        open_orders = world_state.get("open_orders") or {}
        try:
            buy_n = int(open_orders.get("buy_count") or 0)
            sell_n = int(open_orders.get("sell_count") or 0)
        except (TypeError, ValueError):
            buy_n = sell_n = 0

        # 0a. Grid-healthy gate (time-independent)
        grid_state_w = world_state.get("grid_state") or {}
        grid_centre = grid_state_w.get("centre_price")
        grid_spacing = grid_state_w.get("spacing_pct")
        current_price = world_state.get("price")
        drift_pct = None
        try:
            if (grid_centre is not None and current_price is not None
                    and float(grid_centre) > 0):
                drift_pct = abs(float(current_price) - float(grid_centre)) \
                            / float(grid_centre)
        except (TypeError, ValueError):
            drift_pct = None

        grid_bilateral = buy_n >= 1 and sell_n >= 1
        if (grid_bilateral
                and grid_spacing is not None
                and drift_pct is not None
                and drift_pct < float(grid_spacing)):
            cons["grid_action"] = "MAINTAIN"
            overrides.append("[GRID_HEALTHY_NO_RECENTRE]")
            notes.append(
                f"[GRID_HEALTHY_NO_RECENTRE] grid bilateral "
                f"({buy_n}b/{sell_n}s) and price drift {drift_pct*100:.2f}% < "
                f"spacing {float(grid_spacing)*100:.2f}% — downgrading "
                f"RECENTRE → MAINTAIN to preserve the resting book"
            )
            if cons.get("risk_action") in ("PAUSE_LONGS", "PAUSE_SHORTS"):
                old_risk = cons["risk_action"]
                cons["risk_action"] = "CLEAR"
                notes.append(
                    f"[GRID_HEALTHY_NO_RECENTRE] risk_action {old_risk} → "
                    f"CLEAR to preserve the healthy book"
                )
            log.info(
                "Hard rule: GRID_HEALTHY_NO_RECENTRE — bilateral "
                "(%db/%ds), drift %.2f%% < spacing %.2f%%; "
                "downgrading to MAINTAIN + CLEAR",
                buy_n, sell_n, drift_pct*100, float(grid_spacing)*100,
            )

    # 0b. Cooldown timer — only fires if 0a didn't already downgrade.
    if cons.get("grid_action") == "RECENTRE":
        open_orders = world_state.get("open_orders") or {}
        try:
            buy_n = int(open_orders.get("buy_count") or 0)
            sell_n = int(open_orders.get("sell_count") or 0)
        except (TypeError, ValueError):
            buy_n = sell_n = 0
        book_healthy = buy_n >= 3 and sell_n >= 2
        recent_rebuild_hours = None
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT timestamp FROM grid_state "
                "WHERE notes LIKE 'Grid initialised%' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row and row['timestamp']:
                last_build = datetime.fromisoformat(row['timestamp'])
                recent_rebuild_hours = (
                    (datetime.utcnow() - last_build).total_seconds() / 3600
                )
        except Exception as e:
            log.warning("Cooldown check: could not read grid_state: %s", e)

        if (book_healthy and recent_rebuild_hours is not None
                and recent_rebuild_hours < 1.0):
            cons["grid_action"] = "MAINTAIN"
            overrides.append("[RECENTRE_COOLDOWN]")
            notes.append(
                f"[RECENTRE_COOLDOWN] grid rebuilt {recent_rebuild_hours*60:.0f}min "
                f"ago (book={buy_n}b/{sell_n}s) — downgrading RECENTRE→MAINTAIN "
                f"to give fresh grid time to attract fills"
            )
            if cons.get("risk_action") in ("PAUSE_LONGS", "PAUSE_SHORTS"):
                old_risk = cons["risk_action"]
                cons["risk_action"] = "CLEAR"
                notes.append(
                    f"[RECENTRE_COOLDOWN] risk_action {old_risk} → CLEAR to "
                    f"preserve the fresh balanced book"
                )
            log.info(
                "Hard rule: RECENTRE cooldown — grid is %.0f min old and healthy "
                "(%d buys / %d sells); downgrading to MAINTAIN + CLEAR",
                recent_rebuild_hours*60, buy_n, sell_n,
            )

    # 0c. RECENT_POSITION_HOLD — protect an open round-trip from being
    # force-closed by a premature RECENTRE/TIGHTEN/WIDEN. When the bot
    # has just filled (within 2h) AND the inventory skew reflects a
    # meaningful open position (|skew| > 0.15) AND the book is bilateral,
    # the right move is to let the grid close the round-trip naturally.
    # Rebuilding here would unwind the position at the current spot,
    # paying taker fees on the rebalance anchor and locking in whatever
    # mark-to-market is happening right now (often a small loss against
    # the entry). Defense against agents over-eager to rebalance — they
    # have last_fill / position_state context in world_state now, but
    # this hard rule is the Python-side backstop per CLAUDE.md doctrine.
    if cons.get("grid_action") in ("RECENTRE", "TIGHTEN", "WIDEN"):
        hours_since_fill = world_state.get("hours_since_last_fill")
        try:
            hours_since_fill_f = (
                float(hours_since_fill) if hours_since_fill is not None
                else None
            )
        except (TypeError, ValueError):
            hours_since_fill_f = None
        try:
            skew_now = float((world_state.get("inventory") or {})
                              .get("inventory_skew") or 0.0)
        except (TypeError, ValueError):
            skew_now = 0.0
        open_orders = world_state.get("open_orders") or {}
        try:
            buy_n_h = int(open_orders.get("buy_count") or 0)
            sell_n_h = int(open_orders.get("sell_count") or 0)
        except (TypeError, ValueError):
            buy_n_h = sell_n_h = 0
        bilateral = buy_n_h >= 1 and sell_n_h >= 1

        # The hold's rationale is "let the grid close the round-trip
        # naturally" — so it only applies when an imminent PROFITABLE close
        # actually exists (mirrors Melchior Step 0.5: round_trip_net_pnl_usd
        # > 0 AND round_trip_distance_pct < 0.5%). If skew is open but no
        # profitable close is near, the rationale is absent: yield and let
        # the council's RECENTRE stand — the gate likely woke MAGI precisely
        # because the book is skewing one-sided, and holding would re-create
        # the static-grid failure this whole layer exists to avoid.
        ps = world_state.get("position_state") or {}
        try:
            rt_net = (float(ps.get("round_trip_net_pnl_usd"))
                      if ps.get("round_trip_net_pnl_usd") is not None else None)
        except (TypeError, ValueError):
            rt_net = None
        try:
            rt_dist = (float(ps.get("round_trip_distance_pct"))
                       if ps.get("round_trip_distance_pct") is not None else None)
        except (TypeError, ValueError):
            rt_dist = None
        imminent_profitable_close = (
            rt_net is not None and rt_net > 0
            and rt_dist is not None and rt_dist < 0.5
        )
        base_hold = (hours_since_fill_f is not None
                     and hours_since_fill_f < 2.0
                     and abs(skew_now) > 0.15
                     and bilateral)

        if base_hold and imminent_profitable_close:
            cons["grid_action"] = "MAINTAIN"
            overrides.append("[RECENT_POSITION_HOLD]")
            notes.append(
                f"[RECENT_POSITION_HOLD] last fill {hours_since_fill_f:.2f}h "
                f"ago, skew={skew_now:+.3f} (|>{0.15}|), book bilateral "
                f"({buy_n_h}b/{sell_n_h}s) — open position; "
                f"downgrading RECENTRE→MAINTAIN to let the grid close "
                f"the round-trip naturally"
            )
            if cons.get("risk_action") in ("PAUSE_LONGS", "PAUSE_SHORTS"):
                old_risk_h = cons["risk_action"]
                cons["risk_action"] = "CLEAR"
                notes.append(
                    f"[RECENT_POSITION_HOLD] risk_action {old_risk_h} → "
                    f"CLEAR — don't partially cancel the open position"
                )
            log.info(
                "Hard rule: RECENT_POSITION_HOLD — fill %.2fh ago, "
                "skew %+.3f, book %db/%ds, imminent profitable round-trip "
                "(rt_net=%s, rt_dist=%s); downgrading to MAINTAIN",
                hours_since_fill_f, skew_now, buy_n_h, sell_n_h, rt_net, rt_dist,
            )
        elif base_hold and not imminent_profitable_close:
            log.info(
                "Hard rule: RECENT_POSITION_HOLD yielded — fill %.2fh ago, "
                "skew %+.3f, book %db/%ds, but no imminent profitable "
                "round-trip (rt_net=%s, rt_dist=%s); letting council %s stand",
                hours_since_fill_f, skew_now, buy_n_h, sell_n_h,
                rt_net, rt_dist, cons.get("grid_action"),
            )

    # (Rule 0d — the post-hoc COUNCIL VETO — was removed in Stage-4 item 2a. The
    # structural veto now lives in the arbiter's synthesis vote: council_v2 downgrades
    # a RECONFIGURE that Balthasar vetoes (geometry_veto HOLD_GEOMETRY / RISK_BLOCK,
    # or an un-justified PROCEED over a live Casper regime objection) to THESIS_HOLDS
    # before this function runs, so grid_action is already MAINTAIN by the verdict
    # translation above. regime_action / geometry_veto survive on cons as record-only
    # columns. Survival rules below — KILL_SWITCH / DAILY_LOSS_LIMIT /
    # ALLOC_SKEW_CEILING — still force HALT regardless of the council's call.)

    # 1. Kill switch
    if os.path.exists(HARD_RULES["halt_file"]):
        cons["grid_action"] = "HALT"
        cons["risk_action"] = "HALT"
        overrides.append("[KILL_SWITCH]")
        notes.append("[KILL_SWITCH] halt file present")
        log.warning("Hard rule: kill switch active — forcing HALT")

    # 2. Daily loss limit (re-checked here even though scheduler also checks)
    try:
        from guardrails import check_daily_loss
        loss_ok, delta_pct, loss_msg = check_daily_loss()
        if not loss_ok:
            cons["grid_action"] = "HALT"
            cons["risk_action"] = "HALT"
            overrides.append("[DAILY_LOSS_LIMIT]")
            notes.append(f"[DAILY_LOSS_LIMIT] {loss_msg}")
            log.warning("Hard rule: daily loss limit tripped — forcing HALT")
    except Exception as e:
        log.warning("Daily loss check raised — proceeding without it: %s", e)

    # 3. Allocation skew ceiling
    if abs(skew) > HARD_RULES["max_allocation_skew"]:
        cons["grid_action"] = "HALT"
        cons["risk_action"] = "HALT"
        overrides.append("[ALLOC_SKEW_CEILING]")
        notes.append(
            f"[ALLOC_SKEW_CEILING] |skew|={abs(skew):.3f} > "
            f"{HARD_RULES['max_allocation_skew']}"
        )
        log.warning("Hard rule: allocation skew exceeds ceiling — forcing HALT")

    # 4. USD buffer floor (only upgrades from CLEAR)
    if (usd_held < HARD_RULES["min_usd_buffer"]
            and cons.get("risk_action") == "CLEAR"):
        cons["risk_action"] = "PAUSE_LONGS"
        overrides.append("[USD_BUFFER_FLOOR]")
        notes.append(
            f"[USD_BUFFER_FLOOR] usd_held={usd_held:.2f} < "
            f"{HARD_RULES['min_usd_buffer']:.2f} → PAUSE_LONGS"
        )
        log.info("Hard rule: USD buffer below floor → PAUSE_LONGS")

    # 5. XRP buffer floor (only upgrades from CLEAR)
    if (xrp_value_usd < HARD_RULES["min_xrp_buffer_usd"]
            and cons.get("risk_action") == "CLEAR"):
        cons["risk_action"] = "PAUSE_SHORTS"
        overrides.append("[XRP_BUFFER_FLOOR]")
        notes.append(
            f"[XRP_BUFFER_FLOOR] xrp_value_usd={xrp_value_usd:.2f} < "
            f"{HARD_RULES['min_xrp_buffer_usd']:.2f} → PAUSE_SHORTS"
        )
        log.info("Hard rule: XRP buffer below floor → PAUSE_SHORTS")

    # 6. Grid degeneracy — prevent infinite deadlock from one-sided/inactive grid.
    # Forces RECENTRE and clears any PAUSE that would block the rebuild's
    # opposite-side ladder.
    #
    # Two conditions can fire:
    #   - One-sided book (buy_count == 0 or sell_count == 0): always overrides.
    #     A one-sided book cannot oscillate; rebuild immediately regardless of age.
    #   - Stale book (hours_since_last_fill > 24): overrides ONLY if the grid
    #     hasn't been rebuilt in the last 4 hours. Without this cooldown, the
    #     rule would re-RECENTRE every cycle as long as no fill occurs, churning
    #     the grid. The cooldown gives a fresh rebuild time to attract fills.
    #
    # Not applied if HALT is already set (kill-switch / loss limit / council
    # collapse takes priority). Also skipped while [AGENT_DEGRADED:*] is in
    # effect — a degraded council cannot supply trustworthy geometry, so a
    # forced RECENTRE here would either churn the grid blindly or fall back
    # to the scorer rank-1; neither is appropriate when we've explicitly
    # frozen on council degradation.
    #
    # STANCE GATE (Fix 3, 2026-06-11): this rule fires only under stance
    # DEPLOY (or when no stance is recorded — pre-Fix-3 behavior preserved).
    # It was written when nothing else owned "should capital be working", so
    # the engine had to self-heal a one-sided/inactive book unconditionally.
    # The council's stance now owns that question: under STAND_ASIDE a
    # buys-cancelled one-sided book IS the mandate (without this gate, rule 6
    # would see buy_count=0 the very next cycle, force a RECENTRE and reset
    # the PAUSE_LONGS to CLEAR — rebuilding the buys the stance just
    # cancelled, a fee-burning flap); under HOLD a rebuild would deploy new
    # capital against an explicit no-new-deployment mandate. Both states are
    # the council's accountable, graded choice. This gate is also the
    # STAND_ASIDE exit path: the cycle the council votes DEPLOY again, the
    # rule sees the one-sided book and immediately restores the full grid.
    _stance_gate = cons.get("stance") in (None, "DEPLOY")
    if not _stance_gate:
        notes.append(
            f"grid-degenerate rule dormant under stance={cons.get('stance')} "
            f"(one-sided/inactive book is the council's mandate, not damage)"
        )
    # EXPOSURE-CAP GATE (2026-06-12): the rule is also dormant while the
    # down-walk exposure cap is engaged. Under an engaged cap the rebuild is
    # forced sells-only (buy arms suppressed, taker SELL anchor), so a forced
    # RECENTRE here can never restore buy_count>0 — it would just flap: fire
    # on buy_count=0 every council cycle, pay a taker anchor each time, and
    # land back at buy_count=0. The sells-only book is the cap's mandate, not
    # damage. This gates only the FORCED recentre — a council-voted
    # RECONFIGURE still flows through, and W2 wakes the council on cap
    # engage/release, so the exit stays with the judgment layer (a
    # higher-centre rebuild releases the cap and re-arms this rule).
    _cap = world_state.get("exposure_cap") or {}
    _cap_gate = not _cap.get("engaged")
    if not _cap_gate:
        notes.append(
            f"grid-degenerate rule dormant under engaged exposure cap "
            f"(streak={_cap.get('streak')}) — the sells-only book is the "
            f"cap's mandate, not damage"
        )
    _degraded_freeze_active = any(
        t == "[COUNCIL_COLLAPSED]" or t.startswith("[AGENT_DEGRADED:")
        for t in overrides
    )
    if (cons.get("grid_action") != "HALT"
            and cons.get("risk_action") != "HALT"
            and cons.get("grid_action") != "GRID_PAUSE"
            and not _degraded_freeze_active
            and _stance_gate
            and _cap_gate):
        # GRID_PAUSE here means Melchior's NO_PROFITABLE_GRID stand-down — do NOT
        # let the grid-degenerate rule force a RECENTRE over a deliberate
        # stand-down (there is no profitable geometry to rebuild to).
        open_orders = world_state.get("open_orders") or {}
        try:
            buy_count = int(open_orders.get("buy_count") or 0)
            sell_count = int(open_orders.get("sell_count") or 0)
        except (TypeError, ValueError):
            buy_count = sell_count = 0
        hours_inactive_raw = world_state.get("hours_since_last_fill")
        try:
            hours_inactive = (
                float(hours_inactive_raw) if hours_inactive_raw is not None
                else None
            )
        except (TypeError, ValueError):
            hours_inactive = None

        # Compute hours since most recent grid_state row (any insert).
        # initialise_grid() writes "Grid initialised — N orders placed"; even
        # pause-flag changes write rows, so this is a conservative liveness
        # signal — we only treat it as "recently rebuilt" if the latest note
        # actually mentions a build/recentre.
        hours_since_rebuild = None
        try:
            conn = get_conn()
            row = conn.execute(
                "SELECT timestamp, notes FROM grid_state "
                "WHERE notes LIKE 'Grid initialised%' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row and row['timestamp']:
                last_build = datetime.fromisoformat(row['timestamp'])
                hours_since_rebuild = (
                    (datetime.utcnow() - last_build).total_seconds() / 3600
                )
        except Exception as e:
            log.warning("Could not compute hours_since_rebuild: %s", e)

        degenerate_reasons = []
        if buy_count == 0:
            degenerate_reasons.append(f"buy_count=0")
        if sell_count == 0:
            degenerate_reasons.append(f"sell_count=0")
        if hours_inactive is not None and hours_inactive > 24:
            # Stale-fill check is gated by the rebuild cooldown
            if hours_since_rebuild is None or hours_since_rebuild > 4:
                degenerate_reasons.append(
                    f"hours_since_last_fill={hours_inactive:.1f}>24 "
                    f"(rebuild_age={hours_since_rebuild})"
                )

        if degenerate_reasons:
            cons["grid_action"] = "RECENTRE"
            overrides.append("[GRID_DEGENERATE]")
            notes.append(
                f"[GRID_DEGENERATE] {', '.join(degenerate_reasons)} → "
                f"forcing RECENTRE at current price"
            )
            if cons.get("risk_action") in ("PAUSE_LONGS", "PAUSE_SHORTS"):
                old_risk = cons["risk_action"]
                cons["risk_action"] = "CLEAR"
                notes.append(
                    f"[GRID_DEGENERATE] risk_action {old_risk} → CLEAR so "
                    f"the rebuild can place both ladders"
                )
            log.warning(
                "Hard rule: grid degenerate (%s) — forcing RECENTRE + CLEAR",
                ", ".join(degenerate_reasons),
            )

    # 7. PAUSE-vote validation — mirrors Balthasar's STEP 0 logic in Python
    # because claude-sonnet-4-6 has demonstrated it will vote PAUSE_LONGS /
    # PAUSE_SHORTS even when its persona explicitly forbids that vote at the
    # current book state. The hard rule is what actually protects the grid.
    #
    # A PAUSE_LONGS is valid only when the book is genuinely long-heavy AND
    # the inventory is also long-heavy. Anything else is a no-op at best and
    # a thin-side cancellation at worst. Same for PAUSE_SHORTS, mirrored.
    #
    # This rule runs AFTER the buffer-floor rules (4, 5), so a CLEAR upgraded
    # to PAUSE_LONGS by USD_BUFFER_FLOOR is preserved (USD running out is a
    # legitimate reason to pause longs even with a balanced book).
    #
    # Skipped when HALT is in effect, when [USD_BUFFER_FLOOR] / [XRP_BUFFER_FLOOR]
    # already set the PAUSE (those are legitimate), when the council's
    # STAND_ASIDE stance set it (Fix 3 — a deliberate buy-freeze is legitimate
    # on ANY book shape; this rule's skew test exists to stop book-BALANCING
    # misuse, which a stance pause is not), or when [GRID_DEGENERATE]
    # / [RECENTRE_COOLDOWN] already cleared the risk action.
    if (cons.get("grid_action") != "HALT"
            and cons.get("risk_action") in ("PAUSE_LONGS", "PAUSE_SHORTS")
            and "[USD_BUFFER_FLOOR]" not in overrides
            and "[XRP_BUFFER_FLOOR]" not in overrides
            and "[STANCE_STAND_ASIDE]" not in overrides):
        open_orders_v = world_state.get("open_orders") or {}
        try:
            buy_n_v = int(open_orders_v.get("buy_count") or 0)
            sell_n_v = int(open_orders_v.get("sell_count") or 0)
        except (TypeError, ValueError):
            buy_n_v = sell_n_v = 0
        total_v = buy_n_v + sell_n_v
        order_skew = (
            (buy_n_v - sell_n_v) / total_v if total_v > 0 else 0.0
        )
        invalid_reason = None
        if cons["risk_action"] == "PAUSE_LONGS":
            if buy_n_v < 2:
                invalid_reason = (
                    f"PAUSE_LONGS with buy_count={buy_n_v}<2 "
                    f"would damage the thin side"
                )
            elif not (order_skew > 0.7 and skew > 0.3):
                invalid_reason = (
                    f"PAUSE_LONGS requires order_count_skew>+0.7 AND "
                    f"allocation_skew>+0.3; got order_skew={order_skew:.2f}, "
                    f"alloc_skew={skew:.2f}"
                )
        elif cons["risk_action"] == "PAUSE_SHORTS":
            if sell_n_v < 2:
                invalid_reason = (
                    f"PAUSE_SHORTS with sell_count={sell_n_v}<2 "
                    f"would damage the thin side"
                )
            elif not (order_skew < -0.7 and skew < -0.3):
                invalid_reason = (
                    f"PAUSE_SHORTS requires order_count_skew<-0.7 AND "
                    f"allocation_skew<-0.3; got order_skew={order_skew:.2f}, "
                    f"alloc_skew={skew:.2f}"
                )
        if invalid_reason:
            old_risk_v = cons["risk_action"]
            cons["risk_action"] = "CLEAR"
            overrides.append("[PAUSE_INVALID]")
            notes.append(f"[PAUSE_INVALID] {invalid_reason} → CLEAR")
            log.info(
                "Hard rule: PAUSE invalid (%s → CLEAR) — %s",
                old_risk_v, invalid_reason,
            )

    # 8. Geometry source classification + scorer fallback.
    # When the final consensus action will rebuild grid geometry
    # (RECENTRE/TIGHTEN/WIDEN), check whether Melchior's r0 output carries
    # a usable `geometry` block. If it doesn't, and the analytical scorer in
    # world_state has an acceptable rank-1 variant, inject that variant into
    # round_0['melchior']['geometry'] so the downstream _final_consensus
    # picks it up unchanged. Otherwise leave geometry alone and let the
    # engine fallback retain current spacing/levels.
    #
    # geometry_source is recorded on cons for debate_records observability:
    #   - 'agent'            : Melchior emitted complete geometry
    #   - 'scorer_fallback'  : this rule injected scorer rank-1
    #   - 'unchanged'        : no geometry change happens this cycle
    #                          (MAINTAIN/HALT, or no acceptable rank-1)
    geometry_source = "unchanged"
    if (round_0 is not None
            and cons.get("grid_action") in ("RECENTRE", "TIGHTEN", "WIDEN")):
        m_r0 = round_0.get("melchior")
        m_geom = m_r0.get("geometry") if isinstance(m_r0, dict) else None
        if not isinstance(m_geom, dict):
            m_geom = {}
        sp_val = m_geom.get("target_spacing_pct")
        lv_val = m_geom.get("target_levels")
        has_sp = isinstance(sp_val, (int, float)) and sp_val > 0
        has_lv = isinstance(lv_val, int) and lv_val > 0
        if has_sp and has_lv:
            geometry_source = "agent"
        else:
            scored = world_state.get("scored_variants_top_10") or []
            rank1 = scored[0] if scored else None
            if (rank1
                    and rank1.get("acceptable")
                    and rank1.get("spacing_pct") is not None
                    and rank1.get("levels") is not None):
                injected = {
                    "centre_price":       None,
                    "target_spacing_pct": float(rank1["spacing_pct"]),
                    "target_levels":      int(rank1["levels"]),
                    "buy_level_bias":     1.0,
                    "sell_level_bias":    1.0,
                }
                if isinstance(m_r0, dict):
                    m_r0["geometry"] = injected
                else:
                    round_0["melchior"] = {"geometry": injected}
                overrides.append("[GEOMETRY_INJECTED_FROM_SCORER]")
                notes.append(
                    f"[GEOMETRY_INJECTED_FROM_SCORER] Melchior emitted "
                    f"no/partial geometry "
                    f"(agent_sp={sp_val!r}, agent_lv={lv_val!r}); injected "
                    f"scorer rank-1 (levels={injected['target_levels']}, "
                    f"spacing_pct={injected['target_spacing_pct']:.4f}, "
                    f"expected_daily_pnl_pct="
                    f"{rank1.get('expected_daily_pnl_pct') or 0.0:.4f})"
                )
                log.warning(
                    "Hard rule: GEOMETRY_INJECTED_FROM_SCORER — agent_sp=%r "
                    "agent_lv=%r → rank-1 (lc=%d, sp=%.4f)",
                    sp_val, lv_val,
                    injected["target_levels"], injected["target_spacing_pct"],
                )
                geometry_source = "scorer_fallback"
            else:
                if not rank1:
                    skip_reason = "scorer empty"
                elif not rank1.get("acceptable"):
                    skip_reason = "rank-1 unacceptable"
                else:
                    skip_reason = "rank-1 fields missing"
                # No usable geometry path. Don't rebuild with a fabricated
                # spacing and don't leave the existing grid running blind.
                # Force GRID_PAUSE so the engine cancels all orders and
                # idles until the next cycle, when the scorer (or Melchior)
                # might produce usable geometry. GRID_PAUSE alone — no
                # pause_longs/pause_shorts flags — because this rule re-
                # fires on each cycle and flag-based state would chatter.
                cons["grid_action"] = "GRID_PAUSE"
                overrides.append("[NO_ACCEPTABLE_VARIANT]")
                notes.append(
                    f"[NO_ACCEPTABLE_VARIANT] {skip_reason} — forcing "
                    f"GRID_PAUSE; engine will cancel all orders and idle "
                    f"until next cycle"
                )
                log.warning(
                    "Hard rule: NO_ACCEPTABLE_VARIANT (%s) — forcing "
                    "GRID_PAUSE", skip_reason,
                )
                # geometry_source stays 'unchanged' — no geometry was applied
    cons["geometry_source"] = geometry_source

    cons["hard_rule_overrides"] = overrides
    cons["reasoning"] = " ".join(s for s in notes if s).strip()
    return cons


# --- Per-agent / consensus view shaping for return value ---

def _conviction_label(conviction) -> str:
    """Map a float in [0,1] to the legacy {'high','medium','low'} string."""
    try:
        c = float(conviction or 0.0)
    except (TypeError, ValueError):
        return 'low'
    if c >= 0.75:
        return 'high'
    if c >= 0.5:
        return 'medium'
    return 'low'


def _agent_reasoning_json(r0_entry: dict) -> str:
    """Pack key_evidence + crux as JSON — stored in legacy *_reasoning columns."""
    return json.dumps({
        "key_evidence": r0_entry.get("key_evidence") or [],
        "crux":         r0_entry.get("crux"),
    })


def _agent_view_action(r0_entry: dict) -> dict:
    """Shape for melchior / balthasar in the run_cycle return dict."""
    if not r0_entry:
        return None
    return {
        "action":             r0_entry.get("position"),
        "conviction":         r0_entry.get("conviction"),
        "reasoning":          _agent_reasoning_json(r0_entry),
        "centre_price":       None,  # new agent doesn't emit geometry
        "target_spacing_pct": None,
        "sell_level_bias":    None,
        "buy_level_bias":     None,
    }


def _agent_view_casper(r0_entry: dict) -> dict:
    if not r0_entry:
        return None
    return {
        "regime":     r0_entry.get("position"),
        "conviction": r0_entry.get("conviction"),
        "reasoning":  _agent_reasoning_json(r0_entry),
    }


def _final_consensus(cons: dict, cycle_id: str, melchior_r0: dict) -> dict:
    """
    Shape the consensus dict for scheduler / engine consumption.
    Engine reads: grid_action, risk_action, regime, reason (singular!),
    melchior_geometry (dict of centre_price/target_spacing_pct/target_levels/
    biases).

    target_spacing_pct and target_levels are pulled from Melchior's actual
    geometry block in her R0 response. Replaces the prior shadow-fill-winner
    fallback. When Melchior's output is missing/unparseable, both fall back
    to None and the engine retains the live grid's current spacing/levels.
    centre_price stays None — recentres anchor to live spot at rebuild time.
    """
    geom_raw = (melchior_r0 or {}).get("geometry") or {}
    if not isinstance(geom_raw, dict):
        # Defensive: a non-dict 'geometry' value means Melchior emitted
        # something off-schema. Treat as missing and log the raw payload so
        # operator can diagnose without rerunning the cycle.
        log.warning(
            "Melchior geometry was %s, not a dict — treating as None. "
            "Raw r0: %r", type(geom_raw).__name__, melchior_r0,
        )
        geom_raw = {}

    target_spacing_pct = geom_raw.get("target_spacing_pct")
    target_levels = geom_raw.get("target_levels")
    # Coerce to native types, defaulting to None on any failure.
    try:
        target_spacing_pct = (
            float(target_spacing_pct)
            if target_spacing_pct is not None else None
        )
    except (TypeError, ValueError):
        target_spacing_pct = None
    try:
        target_levels = (
            int(target_levels) if target_levels is not None else None
        )
    except (TypeError, ValueError):
        target_levels = None

    if target_spacing_pct is not None or target_levels is not None:
        log.info(
            "Melchior geometry → target_spacing_pct=%s target_levels=%s",
            target_spacing_pct, target_levels,
        )

    return {
        "grid_action":         cons.get("grid_action"),
        "risk_action":         cons.get("risk_action"),
        "regime":              cons.get("regime"),
        "reason":              cons.get("reasoning"),
        "deadlock":            bool(cons.get("deadlock")),
        "hard_rule_overrides": cons.get("hard_rule_overrides") or [],
        "melchior_geometry":   {
            # centre stays None → engine anchors to live spot price on rebuild
            "centre_price":       None,
            "target_spacing_pct": target_spacing_pct,
            "target_levels":      target_levels,
            "sell_level_bias":    1.0,
            "buy_level_bias":     1.0,
        },
        "melchior_conviction": _conviction_label(
            (melchior_r0 or {}).get("conviction")
        ),
        "cycle_id":            cycle_id,
    }


# --- Persistence: debate_records ---

def _compose_config_fingerprint(cons: dict) -> tuple[str, dict]:
    """Stage-4 item-1: compose the config-version fingerprint for this cycle.

    The fingerprint is built in two halves because council_v2 must not import from
    orchestrator (circular). council_v2.run_council computes the half it owns
    (persona hashes, per-seat CONFIGURED model handles, veto mode) and rides it out
    on cons under '_fingerprint_council_half'; this folds in the FLOOR half — the
    HARD_RULES survival floors plus the config.py spacing/fee constants — which are
    in scope here but not in council_v2.

    config_version describes the CONFIGURED setup (operator decision): the per-seat
    model handles hashed are the configured constants, NEVER the served model id. The
    served ids ARE captured — in served_models (+ casper_model_version_observed) for
    the silent-downgrade health signal — but are SNAPSHOT-ONLY and excluded from the
    hash, so a provider serving a different id is visible in forensics without
    churning config_version.

    Returns (config_version, config_snapshot):
      * config_snapshot — a readable dict of every component, for forensics.
      * config_version — a short hex hash over the snapshot's canonical JSON,
        EXCLUDING served_models and casper_model_version_observed (the served-id
        health metadata, which must never churn the hash).

    Additive record-keeping only; reads cons, changes no decision.
    """
    import hashlib
    from config import (
        MIN_GRID_SPACING_PCT,
        MAX_GRID_SPACING_PCT,
        GRID_LEVEL_FEE_PER_SIDE,
    )

    half = cons.get("_fingerprint_council_half") or {}
    snapshot = {
        "persona_hashes": half.get("persona_hashes") or {},
        # CONFIGURED model handles — config_version describes the configured setup.
        "models": half.get("models") or {},
        # Health/observability only: the ACTUAL served model ids this cycle (+ casper's
        # served-version alias). EXCLUDED from the hash (see _HASH_EXCLUDE below) so a
        # silent provider downgrade shows up in forensics without churning the version.
        "served_models": half.get("served_models") or {},
        "casper_model_version_observed": half.get("casper_model_version_observed"),
        "veto_mode": half.get("veto_mode"),
        # Floor half — folded in here (in scope; council_v2 can't see it).
        "hard_rules": dict(HARD_RULES),
        "spacing_fee": {
            "MIN_GRID_SPACING_PCT": MIN_GRID_SPACING_PCT,
            "MAX_GRID_SPACING_PCT": MAX_GRID_SPACING_PCT,
            "GRID_LEVEL_FEE_PER_SIDE": GRID_LEVEL_FEE_PER_SIDE,
        },
        # Stage-4 item 2b: which constraints are disclosed to the council and at what
        # fidelity. Part of the behavioral config — flipping any toggle changes what
        # the council sees, so it joins the hash and bumps config_version at the
        # disclosure boundary (same pattern as veto_mode in 2a).
        "constraint_disclosure": dict(CONSTRAINT_DISCLOSURE),
    }
    # Hash everything EXCEPT the served-id health metadata. Canonical JSON
    # (sort_keys + default=str) so key ordering can never churn the hash.
    _HASH_EXCLUDE = {"served_models", "casper_model_version_observed"}
    hashable = {k: v for k, v in snapshot.items()
                if k not in _HASH_EXCLUDE}
    canon = json.dumps(hashable, sort_keys=True, default=str)
    version = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    return version, snapshot


def _build_debate_record(cycle_id: str, trigger: str, world_state: dict,
                          round_0: dict, conflict, round_1, cons: dict) -> dict:
    record = {
        "cycle_id":  cycle_id,
        "timestamp": world_state.get("timestamp") or datetime.utcnow().isoformat(),
        "trigger":   trigger,
        # Flight recorder: persist the exact inputs the council was shown this
        # cycle so any decision is auditable after the fact. insert_debate_record
        # JSON-serializes this dict into the world_state TEXT column. Without it
        # the only copy lived in a Letta block overwritten every cycle.
        "world_state": world_state,
    }
    for agent in ("casper", "melchior", "balthasar"):
        r0 = round_0.get(agent) or {}
        # Melchior's primary R0 judgment is its verdict (THESIS_HOLDS /
        # RECONFIGURE / NO_PROFITABLE_GRID), recorded in the _r0_position column;
        # Casper/Balthasar keep position (regime / risk_action).
        record[f"{agent}_r0_position"]   = (
            r0.get("verdict") if agent == "melchior" else r0.get("position")
        )
        # Raw proposed action (lossless) for the symmetric blind-review seat grader.
        # NULL on a non-responding seat and on arbiter-era rows (no action authored).
        record[f"{agent}_r0_action"]     = r0.get("action")
        try:
            record[f"{agent}_r0_conviction"] = float(r0.get("conviction") or 0.0)
        except (TypeError, ValueError):
            record[f"{agent}_r0_conviction"] = 0.0
        record[f"{agent}_r0_crux"]       = r0.get("crux")
        # list/dict values for *_evidence are JSON-encoded by insert_debate_record
        record[f"{agent}_r0_evidence"]   = r0.get("key_evidence") or []

    # debate_triggered: True iff a rebuttal label differed from its opening.
    # Authoritative value is computed by council_v2 and carried on cons (it knows
    # each agent's primary — verdict for Melchior, position for Casper).
    record["debate_triggered"] = 1 if cons.get("debate_triggered") else 0

    # Balthasar is the ARBITER: he synthesizes, he does NOT rebut, so his R1
    # columns are n/a (NULL) — never held=1. A held=1 here would be a factual lie
    # (he never rebutted) and would pollute any "how often did Balthasar hold under
    # rebuttal" metric with a 100%-hold artifact that never happened. His
    # post-rebuttal call is the synthesis, recorded as final_risk_action.
    record["balthasar_r1_held"] = None
    record["balthasar_revision_valid"] = None
    record["balthasar_r1_text"] = None

    if round_1:
        for agent in ("casper", "melchior"):
            r1 = round_1.get(agent) or {}
            # Post-rebuttal structured label (Casper regime / Melchior verdict),
            # stored under round_1[agent]['position']. Persist it so later accuracy
            # scoring reads the REVISED call, not the stale opening. NULL when the
            # rebuttal failed to parse.
            record[f"{agent}_r1_position"] = r1.get("position")
            if r1.get("_r1_parse_error"):
                # rebuttal call failed parse — fell back to opening
                record[f"{agent}_r1_held"] = None
                record[f"{agent}_revision_valid"] = None
                record[f"{agent}_r1_text"] = f"PARSE_ERROR: {r1.get('_r1_parse_error')!s}"[:500]
                continue
            # Diff each agent's PRIMARY label opening -> rebuttal. Melchior's
            # primary is 'verdict' (his R0 dict has no 'position'); Casper's is
            # 'position'. The rebuttal label sits under 'position' for both. Reading
            # 'position' for both sides of Melchior would always read None on the
            # opening side and spuriously report held=1.
            r0_prim = (round_0.get(agent) or {}).get(
                "verdict" if agent == "melchior" else "position"
            )
            r1_prim = r1.get("position")
            held = (r0_prim == r1_prim) if (r0_prim and r1_prim) else True
            record[f"{agent}_r1_held"] = 1 if held else 0
            # revision_valid retained for schema continuity; under the synthesis
            # architecture we accept a rebuttal revision as valid by construction
            # (the agent saw the frozen openings and produced a final answer).
            # True when revised, None when held.
            record[f"{agent}_revision_valid"] = None if held else 1
            # Stash the rebuttal crux as the human-readable summary
            record[f"{agent}_r1_text"] = (r1.get("crux") or "")[:500]

    record["final_grid_action"] = cons.get("grid_action")
    record["final_risk_action"] = cons.get("risk_action")
    record["deadlock"]          = 0  # synthesis architecture has no deadlock
    # New columns for the structural-vote architecture
    record["regime_action"] = cons.get("regime_action")
    record["geometry_veto"] = cons.get("geometry_veto")
    # Fix 3: the arbiter's capital mandate (DEPLOY/HOLD/STAND_ASIDE). Plain
    # string -> TEXT column. The forward-outcome grader (stance_correct)
    # reads this per cycle.
    record["stance"] = cons.get("stance")
    # Stage-4 item 2a: the arbiter's justification for PROCEEDing over a live Casper
    # regime objection on a RECONFIGURE (None whenever there was no such override).
    # Plain string -> binds straight to the TEXT column; insert_debate_record does
    # not JSON-encode it (it is not in the encode allow-list, and a string needs no
    # encoding).
    record["override_justification"] = cons.get("override_justification")
    # JSON-encoded list of bracketed hard-rule tags applied this cycle
    # (e.g. ["[RECENTRE_COOLDOWN]", "[PAUSE_INVALID]"]). The dashboard reads
    # this column directly instead of parsing magi_decisions.notes.
    record["hard_rule_overrides"] = cons.get("hard_rule_overrides") or []
    # 'agent' | 'scorer_fallback' | 'unchanged' — set by enforce_hard_rules
    # rule #8. Lets the dashboard show how often Melchior actually contributes
    # geometry vs how often the hard-rule fallback carries the load.
    record["geometry_source"] = cons.get("geometry_source") or "unchanged"
    # Langfuse trace id for this cycle's council debate. Captured INSIDE
    # council_v2.run_council's trace_cycle context and carried out on cons (which
    # enforce_hard_rules preserves via cons = dict(consensus)). It must be read
    # from cons here, NOT recomputed with tracing.current_trace_id(): this builder
    # runs after run_council returns, i.e. the trace context has already exited, so
    # current_trace_id() would return None. NULL when tracing is unavailable.
    record["trace_id"] = cons.get("trace_id")
    # Stage-4 item-1 config fingerprint, composed in run_cycle and carried on cons
    # (same pattern as trace_id). config_version is a short hex string (binds as
    # TEXT). config_snapshot is a dict — insert_debate_record only JSON-encodes a
    # hardcoded key allow-list and would throw InterfaceError on a raw dict, so it
    # is serialized HERE and stored as a JSON string. NULL on a compose failure.
    record["config_version"] = cons.get("config_version")
    _cfg_snap = cons.get("config_snapshot")
    record["config_snapshot"] = (
        json.dumps(_cfg_snap, sort_keys=True, default=str)
        if isinstance(_cfg_snap, (dict, list)) else _cfg_snap
    )
    # council_json: the blind-review council's own memory for this cycle — already a
    # JSON STRING when carried from council_v2 ({decision, vote_multiset, consensus,
    # reconciled}); binds straight to the TEXT column. A dict is tolerated and encoded
    # here. NULL on the pre-redesign arbiter relay (which carried no council_json).
    _cj = cons.get("council_json")
    record["council_json"] = (
        json.dumps(_cj, default=str) if isinstance(_cj, (dict, list)) else _cj
    )
    # Per-agent freshness-retry flags from council.py's R0 validator. None of
    # the agents will carry the key if world_state wasn't passed through, so
    # default to False to preserve the JSON shape across cycles.
    record["freshness_retries"] = {
        agent: bool((round_0.get(agent) or {}).get("freshness_retry"))
        for agent in ("casper", "melchior", "balthasar")
    }
    # applied_*, engine_clamped, clamp_reason are filled in later by the engine;
    # outcome_* fields are backfilled by the observer.
    return record


def _dual_write_magi_decision(trigger: str, round_0: dict, cons: dict) -> Optional[int]:
    """
    Mirror the cycle's final state into the legacy magi_decisions table so
    existing consumers (dashboard.py panels, learning.py, extract_test_cases.py)
    keep working with current data. debate_records is the canonical source;
    this dual-write fills the magi_decisions row to match.

    Returns the inserted row id (suitable for mark_magi_decision_applied),
    or None if insertion failed.
    """
    def _agent_field_pack(agent_key: str) -> tuple:
        r0 = round_0.get(agent_key) or {}
        # Melchior's primary judgment is its verdict; Casper/Balthasar use position.
        position = (
            (r0.get("verdict") if agent_key == "melchior" else r0.get("position"))
            or ""
        )
        conviction_label = _conviction_label(r0.get("conviction"))
        # Pack key_evidence + crux into reasoning so dashboard / learning can
        # surface it without joining tables.
        reasoning_blob = _agent_reasoning_json(r0)
        return position, conviction_label, reasoning_blob

    m_pos, m_conv, m_reason = _agent_field_pack("melchior")
    b_pos, b_conv, b_reason = _agent_field_pack("balthasar")
    c_pos, c_conv, c_reason = _agent_field_pack("casper")

    # balthasar_concerns and casper_concerns columns were added in the
    # 2026-05-17 schema-symmetry migration; before that only
    # melchior_concerns existed. Per-cycle prompt does not emit concerns
    # fields per agent, so all three are None for now — the columns exist
    # for future use and to keep the row shape symmetric.
    payload = {
        "trigger":               trigger,
        "melchior_action":       m_pos,
        "melchior_conviction":   m_conv,
        "melchior_reasoning":    m_reason,
        "melchior_concerns":     None,
        "balthasar_action":      b_pos,
        "balthasar_conviction":  b_conv,
        "balthasar_reasoning":   b_reason,
        "balthasar_concerns":    None,
        "casper_action":         c_pos,
        "casper_conviction":     c_conv,
        "casper_reasoning":      c_reason,
        "casper_concerns":       None,
        "consensus_grid_action": cons.get("grid_action"),
        "consensus_risk_action": cons.get("risk_action"),
        "consensus_regime":      cons.get("regime"),
        "applied":               0,
        # notes carries cons.reasoning so the dashboard's hard-rule-tag
        # extractor (`re.findall(r"\[([A-Z_]+)\]", notes)`) still works.
        "notes":                 cons.get("reasoning") or "",
        # New agent path does not emit per-agent geometry; leave NULL so the
        # engine fallback is what dashboards visualise.
        "melchior_centre_price":       None,
        "melchior_target_spacing_pct": None,
        "melchior_buy_level_bias":     None,
        "melchior_sell_level_bias":    None,
    }
    insert_magi_decision(payload)
    return get_latest_magi_decision_id()


def _early_halt_return(trigger: str, cycle_id: str, failures: list) -> dict:
    """Shape returned when pre-cycle guardrails block the cycle."""
    reason = "GUARDRAILS_BLOCKED: " + "; ".join(failures)
    cons = {
        "grid_action":         "HALT",
        "risk_action":         "HALT",
        "regime":              "UNCERTAIN",
        "reason":              reason,
        "deadlock":            False,
        "hard_rule_overrides": ["[GUARDRAILS_BLOCKED]"],
        "melchior_geometry":   {},
        "melchior_conviction": "low",
        "cycle_id":            cycle_id,
    }
    return {
        "melchior":    None,
        "balthasar":   None,
        "casper":      None,
        "consensus":   cons,
        "decision_id": None,
    }


# --- Main cycle ---

def _prior_r0_signature():
    """Read the previous cycle's R0 position triple from debate_records for
    the R1 novelty gate (council.should_run_r1). Order matches
    council.r0_position_signature: (casper, melchior, balthasar). Returns
    None when no prior row exists or on any DB error — the caller treats
    None as 'novel', so R1 fires (fail-open, never silently suppressed)."""
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT casper_r0_position, melchior_r0_position, "
            "balthasar_r0_position "
            "FROM debate_records ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except Exception as e:
        log.warning("R1 gate: prior-signature DB read failed: %s", e)
        return None
    if not row:
        return None
    return (row["casper_r0_position"], row["melchior_r0_position"],
            row["balthasar_r0_position"])


def run_cycle(trigger: str = "manual", force: bool = False) -> dict:
    log.info("MAGI cycle starting — trigger=%s force=%s", trigger, force)
    cycle_id = f"cyc_{int(time.time())}"

    # 1. Ensure provider keys + Langfuse creds are in the environment. Melchior and
    # Balthasar read os.environ directly; Casper self-loads .env; this load is
    # idempotent (module import already ran it) and harmless.
    load_dotenv()

    # 2. Pre-cycle guardrails
    ok, failures = check_all_guardrails()
    if not ok and not force:
        log.error("Pre-cycle guardrails blocked: %s", failures)
        return _early_halt_return(trigger, cycle_id, failures)
    if not ok and force:
        log.warning("Pre-cycle guardrails failing but force=True: %s", failures)

    # 3. Build world state
    world_state = build_world_state()

    # 4-9. Convene the Stage-3 arbiter council (sequential six-call choreography:
    # Casper -> Melchior -> Balthasar openings, Casper+Melchior rebuttal against a
    # frozen snapshot, Balthasar synthesis). run_council owns its own Langfuse trace
    # and is fail-safe: any seat failure resolves to a safe-hold cons (THESIS_HOLDS
    # -> MAINTAIN, CLEAR risk) rather than raising. It returns round_0 / round_1 /
    # cons in the exact shapes _build_debate_record and enforce_hard_rules consume.
    # (The dead Letta update_world_state push and the ADK parallel-R0 / conditional-R1
    # block are gone — see magi/council_v2.py.)
    round_0, round_1, cons = run_council(world_state, cycle_id, trigger=trigger)
    conflict = None  # vestigial _build_debate_record arg (unused by the builder)
    if cons.get("council_error"):
        log.error("Council stood down on seat failure: %s", cons["council_error"])
    log.info(
        "Consensus: verdict=%s risk=%s regime=%s regime_action=%s "
        "geometry_veto=%s debate_triggered=%s — %s",
        cons.get('grid_verdict'), cons.get('risk_action'),
        cons.get('regime'), cons.get('regime_action'),
        cons.get('geometry_veto'), cons.get('debate_triggered'),
        cons.get('reasoning'),
    )

    # 11. Apply hard rules on top of LLM consensus.
    # round_0 is passed so rule #8 (GEOMETRY_INJECTED_FROM_SCORER) can mutate
    # round_0['melchior']['geometry'] before _final_consensus reads it below.
    cons = enforce_hard_rules(cons, world_state, round_0)
    if cons.get('hard_rule_overrides'):
        log.info("Hard-rule overrides applied: %s", cons['hard_rule_overrides'])
    log.info("Geometry source: %s", cons.get('geometry_source') or 'unchanged')

    # 11b. Compose the config-version fingerprint (Stage-4 item 1). The council half
    # (persona hashes, per-seat served models, veto mode) rode out on cons from
    # run_council and survived enforce_hard_rules' cons = dict(consensus); here we
    # fold in the floor half (HARD_RULES + config.py spacing/fee constants, in scope
    # here) and stamp config_version + config_snapshot onto cons for
    # _build_debate_record. The intermediate council-half key is popped so it never
    # lingers downstream. Additive record-keeping — never blocks a cycle.
    try:
        _cfg_version, _cfg_snapshot = _compose_config_fingerprint(cons)
        cons["config_version"] = _cfg_version
        cons["config_snapshot"] = _cfg_snapshot
        log.info("Config fingerprint: %s", _cfg_version)
    except Exception as e:
        log.warning("config fingerprint compose failed (non-fatal): %s", e)
        cons["config_version"] = None
        cons["config_snapshot"] = None
    finally:
        cons.pop("_fingerprint_council_half", None)

    # 12-13. Write structured debate record (canonical source of truth)
    debate_inserted = False
    try:
        debate_record = _build_debate_record(
            cycle_id, trigger, world_state, round_0, conflict, round_1, cons
        )
        insert_debate_record(debate_record)
        debate_inserted = True
    except Exception as e:
        log.error("Failed to insert debate_record: %s", e)

    # 13b. Mark gate events consumed by THIS cycle so the next cycle's
    # world_state window starts from a clean slate. Only run after the
    # debate_records insert succeeds — if the insert failed we keep the
    # events unconsumed so they re-surface on the next cycle.
    if debate_inserted:
        try:
            _mark_gate_events_consumed(cycle_id, world_state.get("timestamp"))
        except Exception as e:
            log.warning("gate consume failed (non-fatal): %s", e)

    # 13c. Fix 3: persist the standing stance. council_stance_since only
    # advances when the stance CHANGES, so hours_in_stance in the next
    # cycle's world_state measures how long the mandate has actually held.
    # SKIPPED on council_error cycles: a crashed council's safe-hold stance
    # (HOLD) is a fallback, not a decision — letting it overwrite a standing
    # DEPLOY/STAND_ASIDE and reset the clock would record an outage as a
    # stance change and poison the time-in-stance anti-anchoring signal
    # (same failure class as the 2026-06-10 outcome-scope poisoning).
    if not cons.get("council_error"):
        try:
            from database import set_system_state
            new_stance = cons.get("stance") or "HOLD"
            prev_stance = get_system_state('council_stance', default=None)
            if new_stance != prev_stance:
                set_system_state('council_stance', new_stance)
                set_system_state('council_stance_since',
                                 datetime.utcnow().isoformat())
                log.info("council stance changed: %s -> %s",
                         prev_stance or "(none)", new_stance)
        except Exception as e:
            log.warning("council stance persist failed (non-fatal): %s", e)

    # 14. Dual-write to legacy magi_decisions for backward-compat readers:
    #     dashboard.py panels parse hard-rule tags from .notes, learning.py
    #     and extract_test_cases.py read columns by name. Until those readers
    #     migrate to debate_records, this dual-write keeps them current.
    decision_id = None
    try:
        decision_id = _dual_write_magi_decision(trigger, round_0, cons)
    except Exception as e:
        log.warning("Legacy magi_decisions dual-write failed: %s", e)

    # 15. Return scheduler-compatible dict
    return {
        "melchior":    _agent_view_action(round_0.get('melchior')),
        "balthasar":   _agent_view_action(round_0.get('balthasar')),
        "casper":      _agent_view_casper(round_0.get('casper')),
        "consensus":   _final_consensus(cons, cycle_id, round_0.get('melchior') or {}),
        "decision_id": decision_id,
    }


if __name__ == "__main__":
    from magi import adam
    adam.init_oneshot("orchestrator")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s',
    )
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--trigger', default='manual')
    args = parser.parse_args()

    result = run_cycle(trigger=args.trigger, force=args.force)
    if result:
        out = {
            'melchior':  result['melchior'].get('action')   if result['melchior']  else None,
            'balthasar': result['balthasar'].get('action')  if result['balthasar'] else None,
            'casper':    result['casper'].get('regime')     if result['casper']    else None,
            'consensus': result['consensus'],
        }
        print(json.dumps(out, indent=2, default=str))
