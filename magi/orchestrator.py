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

from database import (
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
from guardrails import check_all_guardrails
from magi.council import (
    detect_conflict,
    emit_human_alert,
    resolve_consensus,
    run_round_0_parallel,
    run_round_1,
    update_world_state,
    validate_revision,
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


def build_world_state() -> dict:
    """Snapshot of all market/portfolio context for the cycle."""
    from grid.engine import GridEngine
    from config import MAKER_FEE
    price = None
    try:
        price = GridEngine(paper=True).get_current_price()
    except Exception as e:
        log.warning("Could not fetch current price for world_state: %s", e)

    open_orders = get_open_orders_summary()
    grid_state = get_current_grid_state() or {}
    return {
        "timestamp":                datetime.utcnow().isoformat(),
        "price":                    price,
        "indicators":               get_latest_indicators('1h') or {},
        "grid_state":               grid_state,
        "inventory":                get_latest_inventory() or {},
        "open_orders":              open_orders,
        "hours_since_last_fill":    _hours_since_last_fill(),
        "hours_since_last_rebuild": _hours_since_last_rebuild(),
        "cooldown_status":          _cooldown_status(open_orders),
        "shadow_variants":          _shadow_variants_for_world_state(),
        "current_variant_position": _current_variant_position(grid_state),
        # Hardcoded tier-0 today; future work: source from Kraken TradeVolume
        # (see 02_NEXT_BUILD_TASKS.md).
        "current_fee_tier_pct":     MAKER_FEE,
        "trajectory":               get_trajectory_context(),
        "market_knowledge":         _get_latest_market_knowledge(),
        "hard_rules":               HARD_RULES,
    }


# --- Hard-rule enforcement ---

def enforce_hard_rules(consensus: dict, world_state: dict) -> dict:
    """
    Apply non-negotiable safety overrides on top of LLM consensus.
    Returns the (mutated copy of) consensus dict with a 'hard_rule_overrides'
    list of tags appended for transparency.
    """
    cons = dict(consensus)
    overrides = list(cons.get("hard_rule_overrides") or [])
    notes = [cons.get("reasoning", "")]

    inventory = world_state.get("inventory") or {}
    xrp_held = float(inventory.get("xrp_held") or 0.0)
    usd_held = float(inventory.get("usd_held") or 0.0)
    skew = float(inventory.get("inventory_skew") or 0.0)
    price = world_state.get("price")
    xrp_value_usd = xrp_held * float(price) if price else 0.0

    # 0. RECENTRE cooldown — downgrade council-proposed RECENTRE to MAINTAIN
    # if the grid was just rebuilt and is healthy. Prevents hourly churn when
    # Melchior's STEP 0 GRID HEALTH GATE keeps firing on stale fills (which
    # don't reset until an actual fill happens).
    # The grid-degenerate hard rule below can still force RECENTRE if the book
    # is actually one-sided — this only catches "healthy book, hours-since-fill
    # high, no need to churn yet".
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
            # Also neutralize PAUSE actions: a fresh balanced grid should not
            # be partially cancelled on stale risk reasoning. Without this,
            # PAUSE_LONGS would kill the buys we just placed, the engine
            # integrity guard would emergency-rebuild, and the cycle would
            # churn its full ladder every hour.
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
    # Not applied if HALT is already set (kill-switch / loss limit takes priority).
    if cons.get("grid_action") != "HALT" and cons.get("risk_action") != "HALT":
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
    melchior_geometry (dict of centre_price/target_spacing_pct/biases).
    """
    return {
        "grid_action":         cons.get("grid_action"),
        "risk_action":         cons.get("risk_action"),
        "regime":              cons.get("regime"),
        "reason":              cons.get("reasoning"),
        "deadlock":            bool(cons.get("deadlock")),
        "hard_rule_overrides": cons.get("hard_rule_overrides") or [],
        "melchior_geometry":   {  # empty values → engine falls back to current price + existing spacing
            "centre_price":       None,
            "target_spacing_pct": None,
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

    record["debate_triggered"] = 1 if conflict else 0
    if conflict:
        a, b = conflict["agents"]
        record["conflict_pair"] = f"{a}_{b}"

    if round_1:
        for agent in ("casper", "melchior", "balthasar"):
            r1 = round_1.get(agent)
            if r1 is None:
                continue  # agent not in this conflict — columns stay NULL
            held = bool(r1.get("held"))
            record[f"{agent}_r1_held"] = 1 if held else 0
            if held:
                record[f"{agent}_revision_valid"] = None
            else:
                rv = r1.get("revision_valid")
                # revision_valid was annotated in run_cycle (True/False)
                record[f"{agent}_revision_valid"] = (
                    1 if rv is True else (0 if rv is False else None)
                )
            record[f"{agent}_r1_text"] = r1.get("text") or ""

    record["final_grid_action"] = cons.get("grid_action")
    record["final_risk_action"] = cons.get("risk_action")
    record["deadlock"]          = 1 if cons.get("deadlock") else 0
    # JSON-encoded list of bracketed hard-rule tags applied this cycle
    # (e.g. ["[RECENTRE_COOLDOWN]", "[PAUSE_INVALID]"]). The dashboard reads
    # this column directly instead of parsing magi_decisions.notes.
    record["hard_rule_overrides"] = cons.get("hard_rule_overrides") or []
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

    # 5. Round 0 in parallel
    round_0 = run_round_0_parallel(cycle_id)
    log.info(
        "Round 0: casper=%s/%.2f melchior=%s/%.2f balthasar=%s/%.2f",
        round_0['casper'].get('position'),
        float(round_0['casper'].get('conviction') or 0.0),
        round_0['melchior'].get('position'),
        float(round_0['melchior'].get('conviction') or 0.0),
        round_0['balthasar'].get('position'),
        float(round_0['balthasar'].get('conviction') or 0.0),
    )

    # 6. Conflict detection — world_state is required for the grid-state rules
    conflict = detect_conflict(round_0, world_state)
    round_1 = None

    # 8. Round 1 if conflict
    if conflict:
        log.info("Conflict detected: %s", conflict['reason'])
        round_1 = run_round_1(conflict, cycle_id)
        for agent in conflict['agents']:
            r1 = round_1.get(agent)
            if r1 is None:
                continue
            if r1.get('held'):
                r1['revision_valid'] = None
            else:
                r0_evidence = (round_0.get(agent) or {}).get('key_evidence') or []
                rev_ev = r1.get('revision_evidence') or ''
                is_valid, why = validate_revision(r0_evidence, rev_ev)
                r1['revision_valid'] = is_valid
                log.info(
                    "Round 1: %s revision %s — %s",
                    agent, 'VALID' if is_valid else 'INVALID (capitulation)', why
                )
    else:
        log.info("No conflict — proceeding with Round 0 consensus")

    # 9. Resolve consensus from the debate
    cons = resolve_consensus(round_0, round_1, conflict)
    log.info(
        "Consensus: grid=%s risk=%s deadlock=%s — %s",
        cons.get('grid_action'), cons.get('risk_action'),
        cons.get('deadlock'), cons.get('reasoning'),
    )

    # 10. Human alert on deadlock
    if cons.get('deadlock'):
        emit_human_alert(cycle_id, cons.get('reasoning', '(no reason)'))

    # 11. Apply hard rules on top of LLM consensus
    cons = enforce_hard_rules(cons, world_state)
    if cons.get('hard_rule_overrides'):
        log.info("Hard-rule overrides applied: %s", cons['hard_rule_overrides'])

    # 12-13. Write structured debate record (canonical source of truth)
    try:
        debate_record = _build_debate_record(
            cycle_id, trigger, world_state, round_0, conflict, round_1, cons
        )
        insert_debate_record(debate_record)
    except Exception as e:
        log.error("Failed to insert debate_record: %s", e)

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
