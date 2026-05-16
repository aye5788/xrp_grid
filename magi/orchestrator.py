"""
orchestrator.py — debate-driven MAGI cycle (Phase 5).

Replaces the stateless three-call deliberation with a Letta-backed debate:
  1. Build world_state from DB
  2. Push it to the shared Letta world_state block
  3. Round 0: all three agents respond in parallel
  4. Detect conflict via CONFLICT_MATRIX
  5. Round 1 (only if conflict) — challenge the two conflicting agents
  6. resolve_consensus + enforce_hard_rules
  7. Write to debate_records (new) AND magi_decisions (legacy, dashboard compat)
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
    'decision_id': <legacy magi_decisions row id>,
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


def build_world_state() -> dict:
    """Snapshot of all market/portfolio context for the cycle."""
    from grid.engine import GridEngine
    price = None
    try:
        price = GridEngine(paper=True).get_current_price()
    except Exception as e:
        log.warning("Could not fetch current price for world_state: %s", e)

    return {
        "timestamp":        datetime.utcnow().isoformat(),
        "price":            price,
        "indicators":       get_latest_indicators('1h') or {},
        "grid_state":       get_current_grid_state() or {},
        "inventory":        get_latest_inventory() or {},
        "open_orders":      get_open_orders_summary(),
        "trajectory":       get_trajectory_context(),
        "market_knowledge": _get_latest_market_knowledge(),
        "hard_rules":       HARD_RULES,
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


# --- Persistence: debate_records (new) + magi_decisions (legacy) ---

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
    # applied_*, engine_clamped, clamp_reason are filled in later by the engine;
    # outcome_* fields are backfilled by the observer.
    return record


def _build_legacy_decision(trigger: str, round_0: dict, cons: dict) -> dict:
    """Backward-compat row for the magi_decisions table (dashboard reads this)."""
    casper_r0    = round_0.get("casper")    or {}
    melchior_r0  = round_0.get("melchior")  or {}
    balthasar_r0 = round_0.get("balthasar") or {}
    return {
        "trigger":              trigger,
        "melchior_action":      melchior_r0.get("position"),
        "melchior_conviction":  _conviction_label(melchior_r0.get("conviction")),
        "melchior_reasoning":   _agent_reasoning_json(melchior_r0),
        "melchior_concerns":    None,
        "balthasar_action":     balthasar_r0.get("position"),
        "balthasar_conviction": _conviction_label(balthasar_r0.get("conviction")),
        "balthasar_reasoning":  _agent_reasoning_json(balthasar_r0),
        # legacy schema column 'casper_action' actually stores the regime string
        "casper_action":        casper_r0.get("position"),
        "casper_conviction":    _conviction_label(casper_r0.get("conviction")),
        "casper_reasoning":     _agent_reasoning_json(casper_r0),
        "consensus_grid_action": cons.get("grid_action"),
        "consensus_risk_action": cons.get("risk_action"),
        "consensus_regime":      cons.get("regime"),
        "applied":               0,
        "notes":                 cons.get("reasoning", ""),
        # Geometry columns nullable — new Melchior doesn't emit numeric geometry
        "melchior_centre_price":       None,
        "melchior_target_spacing_pct": None,
        "melchior_buy_level_bias":     None,
        "melchior_sell_level_bias":    None,
    }


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
    legacy = {
        "trigger":              trigger,
        "melchior_action":      None,
        "melchior_conviction":  None,
        "melchior_reasoning":   None,
        "melchior_concerns":    None,
        "balthasar_action":     None,
        "balthasar_conviction": None,
        "balthasar_reasoning":  None,
        "casper_action":        None,
        "casper_conviction":    None,
        "casper_reasoning":     None,
        "consensus_grid_action": "HALT",
        "consensus_risk_action": "HALT",
        "consensus_regime":      "UNCERTAIN",
        "applied":               0,
        "notes":                 reason,
        "melchior_centre_price":       None,
        "melchior_target_spacing_pct": None,
        "melchior_buy_level_bias":     None,
        "melchior_sell_level_bias":    None,
    }
    insert_magi_decision(legacy)
    return {
        "melchior":    None,
        "balthasar":   None,
        "casper":      None,
        "consensus":   cons,
        "decision_id": get_latest_magi_decision_id(),
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

    # 6. Conflict detection
    conflict = detect_conflict(round_0)
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

    # 12-13. Write structured debate record
    try:
        debate_record = _build_debate_record(
            cycle_id, trigger, world_state, round_0, conflict, round_1, cons
        )
        insert_debate_record(debate_record)
    except Exception as e:
        log.error("Failed to insert debate_record: %s", e)

    # 14. Backward-compat: write to legacy magi_decisions for the dashboard
    try:
        legacy = _build_legacy_decision(trigger, round_0, cons)
        insert_magi_decision(legacy)
        decision_id = get_latest_magi_decision_id()
    except Exception as e:
        log.error("Failed to insert legacy magi_decisions row: %s", e)
        decision_id = None

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
