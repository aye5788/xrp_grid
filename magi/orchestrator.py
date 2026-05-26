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
    get_trajectory_context,
    insert_debate_record,
    insert_magi_decision,
)
from magi.spacing_evaluator import DEFAULT_VARIANTS, score_variants
from guardrails import check_all_guardrails
from magi.council import (
    emit_human_alert,
    resolve_consensus,
    run_round_0_parallel,
    run_round_1,
    update_world_state,
)

load_dotenv()
log = logging.getLogger('magi.orchestrator')


HARD_RULES = {
    "max_allocation_skew": 0.85,
    "min_usd_buffer": 10.0,
    "min_xrp_buffer_usd": 10.0,
    "daily_loss_limit_pct": 0.15,
    "halt_file": "/root/xrp_grid/HALT",
    "max_grid_spacing_pct": 0.025,
    "min_grid_spacing_pct": 0.003,
}


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


def _hours_since_last_fill() -> float | None:
    """Hours since the most recent grid_orders fill, or None if no fills exist."""
    conn = get_conn()
    row = conn.execute(
        "SELECT filled_at FROM grid_orders "
        "WHERE status='filled' AND filled_at IS NOT NULL "
        "ORDER BY filled_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or not row['filled_at']:
        return None
    try:
        last = datetime.fromisoformat(row['filled_at'])
    except ValueError:
        return None
    return round((datetime.utcnow() - last).total_seconds() / 3600, 2)


def _last_fill_summary() -> dict | None:
    """Summary of the most recent filled order. Used by agents to reason
    about whether a recent fill represents an open position they should
    let the grid close, vs. a stale state that warrants RECENTRE."""
    conn = get_conn()
    row = conn.execute(
        "SELECT order_id, side, price, size, fill_price, fee, filled_at "
        "FROM grid_orders WHERE status='filled' AND filled_at IS NOT NULL "
        "ORDER BY filled_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
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
        "hard_rules":               HARD_RULES,
        # Derived portfolio metrics (xrp_value_usd, total_universe_usd,
        # xrp_pct_of_universe, allocation_skew). Single source of truth —
        # both the rule layer and Balthasar's persona read from here.
        "portfolio":                portfolio_block,
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
    # Rule 0d — council veto
    "[REGIME_DEFER]",
    "[REGIME_STANDDOWN]",
    "[BALTHASAR_HOLD_GEOMETRY]",
    "[BALTHASAR_RISK_BLOCK]",
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
}
# AGENT_DEGRADED is emitted templated as "[AGENT_DEGRADED:<agent_id>]". The three
# valid agent_ids (casper/melchior/balthasar) are enumerated above rather than
# prefix-matched, so this stays a closed membership test and a malformed or
# unexpected agent_id correctly trips Invariant 2. ([GUARDRAILS_BLOCKED] is emitted
# by _early_halt_return, NOT by enforce_hard_rules, so it is deliberately absent.)

_RULE_0D_REGIME_VETOS    = {"DEFER_STRUCTURAL", "STAND_DOWN"}
_RULE_0D_GEOMETRY_VETOS  = {"HOLD_GEOMETRY", "RISK_BLOCK"}
_RULE_0D_TRIGGER_ACTIONS = {"RECENTRE", "TIGHTEN", "WIDEN"}
_RULE_0D_COVERAGE_TAGS   = {
    "[REGIME_DEFER]", "[REGIME_STANDDOWN]",
    "[BALTHASAR_HOLD_GEOMETRY]", "[BALTHASAR_RISK_BLOCK]",
}
# Rules that run AFTER rule 0d and may legitimately overwrite its MAINTAIN
# coercion (see the precedence ladder in enforce_hard_rules' docstring). When
# one of these tags is present, a non-MAINTAIN grid_action does NOT violate
# Invariant 1 — survival/integrity rules outrank the council veto.
_RULE_0D_SUPERSEDING_TAGS = {
    "[KILL_SWITCH]", "[DAILY_LOSS_LIMIT]", "[ALLOC_SKEW_CEILING]",
    "[GRID_DEGENERATE]", "[NO_ACCEPTABLE_VARIANT]",
}


# The contract predicates below delegate their set logic to these helpers.
# Keeping set()/any()/`or []` OUT of the @ensure lambda bodies matters: icontract
# re-walks the lambda AST to build the violation message, and it cannot recompute
# `set(x or []) & y` (raises 'bool' object is not iterable). Opaque helper calls
# sidestep that, and icontract still reports each helper's return value plus the
# `result.get("hard_rule_overrides")` argument in the failure message. The helpers
# are also independently unit-testable.
def _has_rule0d_coverage_tag(overrides):
    """True if the override list carries >=1 rule-0d council-veto tag."""
    return any(tag in (overrides or []) for tag in _RULE_0D_COVERAGE_TAGS)


def _has_rule0d_superseding_tag(overrides):
    """True if a higher-precedence rule (HALT / RECENTRE / GRID_PAUSE) ran after
    rule 0d and legitimately overwrote its MAINTAIN coercion."""
    return any(tag in (overrides or []) for tag in _RULE_0D_SUPERSEDING_TAGS)


def _unknown_override_tags(overrides):
    """Set of override tags that are NOT canonical (empty set when all valid)."""
    return set(overrides or []) - _CANONICAL_OVERRIDE_TAGS


@icontract.snapshot(lambda consensus: consensus.get("regime_action"), name="in_regime_action")
@icontract.snapshot(lambda consensus: consensus.get("geometry_veto"), name="in_geometry_veto")
@icontract.snapshot(lambda consensus: consensus.get("grid_action"),   name="in_grid_action")
@icontract.ensure(
    lambda OLD, result: (
        not (
            (OLD.in_regime_action in _RULE_0D_REGIME_VETOS
             or OLD.in_geometry_veto in _RULE_0D_GEOMETRY_VETOS)
            and OLD.in_grid_action in _RULE_0D_TRIGGER_ACTIONS
        )
        or (
            _has_rule0d_coverage_tag(result.get("hard_rule_overrides"))
            and (
                result.get("grid_action") == "MAINTAIN"
                or _has_rule0d_superseding_tag(result.get("hard_rule_overrides"))
            )
        )
    ),
    description=(
        "Invariant 1 (rule 0d coverage): when input regime_action is "
        "DEFER_STRUCTURAL/STAND_DOWN or geometry_veto is HOLD_GEOMETRY/"
        "RISK_BLOCK AND input grid_action is RECENTRE/TIGHTEN/WIDEN, rule 0d "
        "must record the veto with at least one of [REGIME_DEFER]/"
        "[REGIME_STANDDOWN]/[BALTHASAR_HOLD_GEOMETRY]/[BALTHASAR_RISK_BLOCK], "
        "and returned grid_action must be MAINTAIN UNLESS a later "
        "higher-precedence rule legitimately superseded it (at least one of "
        "[KILL_SWITCH]/[DAILY_LOSS_LIMIT]/[ALLOC_SKEW_CEILING]/[GRID_DEGENERATE]/"
        "[NO_ACCEPTABLE_VARIANT] present — see the precedence ladder in the "
        "docstring). Regression guard for cyc_1779480012 (2026-05-22T20:00:12)."
    ),
)
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
        -1.  Council-degradation freeze → MAINTAIN (1 agent) / HALT (council collapsed)
        0a/0b/0c. RECENTRE block        → MAINTAIN (GRID_HEALTHY_NO_RECENTRE /
                                          RECENTRE_COOLDOWN / RECENT_POSITION_HOLD)
        0d.  Council veto               → MAINTAIN (REGIME_DEFER / REGIME_STANDDOWN /
                                          BALTHASAR_HOLD_GEOMETRY / BALTHASAR_RISK_BLOCK)
        1.   Kill switch                → HALT
        2.   Daily loss limit           → HALT
        3.   Allocation skew ceiling    → HALT
        4/5. USD / XRP buffer floors    → risk_action CLEAR (grid_action untouched)
        6.   Grid degenerate            → RECENTRE
        7.   PAUSE_INVALID              → risk_action CLEAR
        8.   Geometry injection / no acceptable variant → GRID_PAUSE
    Rule 0d coerces grid_action to MAINTAIN, but rules 1-3 (→HALT), 6 (→RECENTRE)
    and 8 (→GRID_PAUSE) run afterward and can legitimately supersede it. This is
    why Invariant 1 requires a rule-0d coverage tag yet accepts a non-MAINTAIN
    grid_action when one of those superseding rules (_RULE_0D_SUPERSEDING_TAGS)
    also left its tag in hard_rule_overrides.
    """
    cons = dict(consensus)
    overrides = list(cons.get("hard_rule_overrides") or [])
    notes = [cons.get("reasoning", "")]

    # Capture Melchior's ORIGINAL intent BEFORE any rule mutates
    # cons["grid_action"]. The council-veto step (added below 0c) uses
    # this so its tag fires even when the rule layer's 0a/0b/0c also
    # downgrade — defense-in-depth tag visibility.
    _original_grid_action = cons.get("grid_action")

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

    # 0d. COUNCIL VETO — Casper's regime_action and Balthasar's
    # geometry_veto. Operates on Melchior's ORIGINAL intent (captured
    # at function entry), NOT on the current grid_action. This means
    # the council-veto tag fires even when 0a/0b/0c already downgraded
    # for their own reasons — both tags end up in hard_rule_overrides,
    # so the operator can see both council judgment AND rule-layer
    # judgment caught the same case. Defense-in-depth visibility.
    #
    # Permissive defaults: missing/unparseable fields default to
    # EXECUTE / PROCEED in resolve_consensus, so this rule only fires
    # when the agents EXPLICITLY voted to veto.
    #
    # Survival rules (1 KILL_SWITCH, 2 DAILY_LOSS_LIMIT, 3
    # ALLOC_SKEW_CEILING) run after this and can still force HALT
    # — council can't override survival floors.
    if _original_grid_action in ("RECENTRE", "TIGHTEN", "WIDEN"):
        regime_action_v = cons.get("regime_action") or "EXECUTE"
        geometry_veto_v = cons.get("geometry_veto") or "PROCEED"
        council_vetoed = False
        if regime_action_v == "DEFER_STRUCTURAL":
            overrides.append("[REGIME_DEFER]")
            notes.append(
                f"[REGIME_DEFER] Casper says regime defers structural "
                f"change (was {_original_grid_action})"
            )
            log.info(
                "Hard rule: REGIME_DEFER — Casper voted DEFER_STRUCTURAL "
                "against Melchior's %s", _original_grid_action,
            )
            council_vetoed = True
        elif regime_action_v == "STAND_DOWN":
            overrides.append("[REGIME_STANDDOWN]")
            notes.append(
                f"[REGIME_STANDDOWN] Casper says regime stand-down "
                f"(was {_original_grid_action})"
            )
            log.warning(
                "Hard rule: REGIME_STANDDOWN — Casper voted STAND_DOWN "
                "against Melchior's %s", _original_grid_action,
            )
            council_vetoed = True
        if geometry_veto_v == "HOLD_GEOMETRY":
            overrides.append("[BALTHASAR_HOLD_GEOMETRY]")
            notes.append(
                f"[BALTHASAR_HOLD_GEOMETRY] Balthasar says hold geometry "
                f"(was {_original_grid_action})"
            )
            log.info(
                "Hard rule: BALTHASAR_HOLD_GEOMETRY — Balthasar voted "
                "HOLD_GEOMETRY against Melchior's %s", _original_grid_action,
            )
            council_vetoed = True
        elif geometry_veto_v == "RISK_BLOCK":
            overrides.append("[BALTHASAR_RISK_BLOCK]")
            notes.append(
                f"[BALTHASAR_RISK_BLOCK] Balthasar blocks geometry change "
                f"(was {_original_grid_action})"
            )
            log.warning(
                "Hard rule: BALTHASAR_RISK_BLOCK — Balthasar voted "
                "RISK_BLOCK against Melchior's %s", _original_grid_action,
            )
            council_vetoed = True
        if council_vetoed:
            # Coerce to MAINTAIN (idempotent if 0a/0b/0c already did so).
            # risk_action is left alone — council veto is geometry-only.
            cons["grid_action"] = "MAINTAIN"

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
    _degraded_freeze_active = any(
        t == "[COUNCIL_COLLAPSED]" or t.startswith("[AGENT_DEGRADED:")
        for t in overrides
    )
    if (cons.get("grid_action") != "HALT"
            and cons.get("risk_action") != "HALT"
            and not _degraded_freeze_active):
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
    # already set the PAUSE (those are legitimate), or when [GRID_DEGENERATE]
    # / [RECENTRE_COOLDOWN] already cleared the risk action.
    if (cons.get("grid_action") != "HALT"
            and cons.get("risk_action") in ("PAUSE_LONGS", "PAUSE_SHORTS")
            and "[USD_BUFFER_FLOOR]" not in overrides
            and "[XRP_BUFFER_FLOOR]" not in overrides):
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
        record[f"{agent}_r0_position"]   = r0.get("position")
        try:
            record[f"{agent}_r0_conviction"] = float(r0.get("conviction") or 0.0)
        except (TypeError, ValueError):
            record[f"{agent}_r0_conviction"] = 0.0
        record[f"{agent}_r0_crux"]       = r0.get("crux")
        # list/dict values for *_evidence are JSON-encoded by insert_debate_record
        record[f"{agent}_r0_evidence"]   = r0.get("key_evidence") or []

    # debate_triggered semantics under always-R1 synthesis: True iff
    # any agent's R1 position differs from their R0 position
    record["debate_triggered"] = 1 if cons.get("debate_triggered") else 0

    if round_1:
        for agent in ("casper", "melchior", "balthasar"):
            r1 = round_1.get(agent) or {}
            if r1.get("_r1_parse_error"):
                # synthesis call failed parse — fell back to R0
                record[f"{agent}_r1_held"] = None
                record[f"{agent}_revision_valid"] = None
                record[f"{agent}_r1_text"] = f"PARSE_ERROR: {r1.get('_r1_parse_error')!s}"[:500]
                continue
            r0_pos = (round_0.get(agent) or {}).get("position")
            r1_pos = r1.get("position")
            held = (r0_pos == r1_pos) if (r0_pos and r1_pos) else True
            record[f"{agent}_r1_held"] = 1 if held else 0
            # revision_valid retained for schema continuity; under
            # synthesis architecture we accept R1's revision as valid by
            # construction (the agent saw peer R0s and produced a final
            # answer). True when revised, None when held.
            record[f"{agent}_revision_valid"] = None if held else 1
            # Stash the R1 crux as the human-readable summary
            record[f"{agent}_r1_text"] = (r1.get("crux") or "")[:500]

    record["final_grid_action"] = cons.get("grid_action")
    record["final_risk_action"] = cons.get("risk_action")
    record["deadlock"]          = 0  # synthesis architecture has no deadlock
    # New columns for the structural-vote architecture
    record["regime_action"] = cons.get("regime_action")
    record["geometry_veto"] = cons.get("geometry_veto")
    # JSON-encoded list of bracketed hard-rule tags applied this cycle
    # (e.g. ["[RECENTRE_COOLDOWN]", "[PAUSE_INVALID]"]). The dashboard reads
    # this column directly instead of parsing magi_decisions.notes.
    record["hard_rule_overrides"] = cons.get("hard_rule_overrides") or []
    # 'agent' | 'scorer_fallback' | 'unchanged' — set by enforce_hard_rules
    # rule #8. Lets the dashboard show how often Melchior actually contributes
    # geometry vs how often the hard-rule fallback carries the load.
    record["geometry_source"] = cons.get("geometry_source") or "unchanged"
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
        position = r0.get("position") or ""
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

def run_cycle(trigger: str = "manual", force: bool = False) -> dict:
    log.info("MAGI cycle starting — trigger=%s force=%s", trigger, force)
    cycle_id = f"cyc_{int(time.time())}"

    # 2. Pre-cycle guardrails
    ok, failures = check_all_guardrails()
    if not ok and not force:
        log.error("Pre-cycle guardrails blocked: %s", failures)
        return _early_halt_return(trigger, cycle_id, failures)
    if not ok and force:
        log.warning("Pre-cycle guardrails failing but force=True: %s", failures)

    # 3. Build world state
    world_state = build_world_state()

    # 4. Push to shared Letta block — non-fatal if it fails
    try:
        update_world_state(world_state)
    except Exception as e:
        log.error("Failed to push world_state to Letta: %s — agents will see stale state", e)

    # 5. Round 0 in parallel — pass world_state so council.py's freshness
    # validator can cross-check each agent's R0 evidence against the
    # current world_state values and force a one-shot correction re-prompt
    # on stale agents (see council.py:_validate_r0_freshness).
    round_0 = run_round_0_parallel(cycle_id, world_state)
    log.info(
        "Round 0: casper=%s/%.2f melchior=%s/%.2f balthasar=%s/%.2f",
        round_0['casper'].get('position'),
        float(round_0['casper'].get('conviction') or 0.0),
        round_0['melchior'].get('position'),
        float(round_0['melchior'].get('conviction') or 0.0),
        round_0['balthasar'].get('position'),
        float(round_0['balthasar'].get('conviction') or 0.0),
    )

    # 6. Round 1 — ALWAYS fires as synthesis. Each agent's R1 prompt
    # explicitly pastes peers' R0 outputs and asks for revision/hold
    # in light of the integrated view. Replaces the prior
    # conflict-triggered debate; CONFLICT_MATRIX retired.
    log.info("Round 1: firing synthesis for all three agents")
    round_1 = run_round_1(round_0, cycle_id)
    for agent in ("casper", "melchior", "balthasar"):
        r1 = round_1.get(agent) or {}
        if r1.get("_r1_parse_error"):
            log.warning(
                "Round 1 [%s] parse error: %s — falling back to R0",
                agent, r1.get("_r1_parse_error"),
            )
            continue
        r0_pos = (round_0.get(agent) or {}).get("position")
        r1_pos = r1.get("position")
        if r1_pos and r0_pos and r1_pos != r0_pos:
            log.info("Round 1 [%s] shifted %s -> %s", agent, r0_pos, r1_pos)
    conflict = None  # retained for downstream signatures expecting it

    # 9. Resolve consensus from the synthesised votes
    cons = resolve_consensus(round_0, round_1, conflict)
    log.info(
        "Consensus: grid=%s risk=%s regime=%s regime_action=%s "
        "geometry_veto=%s debate_triggered=%s — %s",
        cons.get('grid_action'), cons.get('risk_action'),
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
