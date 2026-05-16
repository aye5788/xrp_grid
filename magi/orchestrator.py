import json
import logging
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
from database import (
    get_latest_indicators, get_current_grid_state, get_latest_inventory,
    insert_magi_decision, insert_grid_state, get_conn
)
from magi.melchior import get_decision as melchior_stateless
from magi.balthasar import get_decision as balthasar_stateless
from magi.casper import get_decision as casper_stateless

load_dotenv()
log = logging.getLogger('magi.orchestrator')


def build_empty_inventory():
    return {'xrp_held': 0, 'usd_held': 0, 'net_position_usd': 0, 'inventory_skew': 0}


def build_empty_grid():
    from config import GRID_SPACING_PCT, GRID_LEVELS_DEFAULT
    return {
        'centre_price': None, 'spacing_pct': GRID_SPACING_PCT,
        'levels': GRID_LEVELS_DEFAULT, 'pause_longs': 0,
        'pause_shorts': 0, 'halt': 0
    }


def apply_consensus(melchior, balthasar, casper, current_grid):
    b_action = balthasar.get('action', 'CLEAR')
    c_regime = casper.get('regime', 'UNCERTAIN')
    m_action = melchior.get('action', 'MAINTAIN')

    # Conviction weighting: low-conviction calls carry less weight
    b_conviction = balthasar.get('conviction', 'medium')
    c_conviction = casper.get('conviction', 'medium')

    # Low-conviction HALT → downgrade to PAUSE_LONGS
    # A HALT Balthasar isn't sure about shouldn't fully suspend the grid
    if b_action == 'HALT' and b_conviction == 'low':
        b_action = 'PAUSE_LONGS'
        log.info("Balthasar low-conviction HALT downgraded to PAUSE_LONGS")

    if b_action == 'HALT':
        return {
            'grid_action': 'HALT',
            'risk_action': 'HALT',
            'regime': c_regime,
            'reason': 'Balthasar HALT — all grid activity suspended',
            'recentre_target': None,
            'spacing_adjustment_pct': None,
            'melchior_conviction': melchior.get('conviction'),
        }

    risk_action = b_action

    if c_regime == 'TRENDING' and c_conviction != 'low':
        if m_action in ('WIDEN', 'RECENTRE'):
            # WIDEN and RECENTRE are both appropriate in a trending market.
            # WIDEN widens the grid to ride momentum; RECENTRE is regime-neutral
            # and resets position without adding directional exposure.
            # Only TIGHTEN is dangerous in trends (increases fill rate into momentum).
            grid_action = m_action
            reason = (f'Casper TRENDING ({c_conviction} conviction) — '
                      f'allowing Melchior {m_action} (regime-neutral action)')
        else:
            grid_action = 'MAINTAIN'
            reason = (f'Casper TRENDING ({c_conviction} conviction) — '
                      f'blocking Melchior {m_action}, holding grid structure')
    elif c_regime == 'TRENDING' and c_conviction == 'low':
        grid_action = m_action
        reason = (f'Casper TRENDING (low conviction) — '
                  f'not blocking Melchior {m_action}, regime uncertain')
    else:
        grid_action = m_action
        reason = f'Casper {c_regime} — applying Melchior {m_action}'

    # Dead-grid override: a completely empty grid (zero orders on either
    # side) is always a dead grid — it doesn't matter what Balthasar said.
    # Override to RECENTRE + CLEAR to escape the deadlock.
    conn = get_conn()
    open_buy_count = conn.execute(
        "SELECT COUNT(*) FROM grid_orders WHERE status='open' AND side='buy'"
    ).fetchone()[0]
    open_sell_count = conn.execute(
        "SELECT COUNT(*) FROM grid_orders WHERE status='open' AND side='sell'"
    ).fetchone()[0]
    last_fill = conn.execute(
        "SELECT filled_at FROM grid_orders WHERE status='filled' "
        "ORDER BY filled_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    total_open = open_buy_count + open_sell_count

    if last_fill:
        last_fill_dt = datetime.fromisoformat(last_fill[0].replace('Z', ''))
        if last_fill_dt.tzinfo is None:
            last_fill_dt = last_fill_dt.replace(tzinfo=timezone.utc)
        hours_since_fill = (datetime.now(timezone.utc) - last_fill_dt).total_seconds() / 3600
    else:
        hours_since_fill = 999

    if (total_open == 0
            and hours_since_fill > 2
            and grid_action != 'HALT'
            and grid_action != 'GRID_PAUSE'):
        grid_action = 'RECENTRE'
        risk_action = 'CLEAR'
        override_note = (f'[DEAD_GRID_OVERRIDE: total_open=0, '
                         f'hours_since_fill={hours_since_fill:.1f}]')
        reason = reason + ' ' + override_note
        log.warning(f'Dead-grid override applied — {override_note}')
    else:
        override_note = ''

    # Geometry passes through to the engine regardless of grid_action veto.
    # If grid_action is MAINTAIN/HALT/blocked, the engine will not rebuild
    # and the geometry is simply ignored. If a rebuild fires, the engine
    # uses these exact values (with validation/clamping).
    melchior_geometry = {
        'centre_price': melchior.get('centre_price'),
        'target_spacing_pct': melchior.get('target_spacing_pct'),
        'sell_level_bias': melchior.get('sell_level_bias', 1.0) or 1.0,
        'buy_level_bias': melchior.get('buy_level_bias', 1.0) or 1.0,
    }

    return {
        'grid_action': grid_action,
        'risk_action': risk_action,
        'regime': c_regime,
        'reason': reason,
        'melchior_geometry': melchior_geometry,
        'melchior_conviction': melchior.get('conviction'),
    }


def check_regime_gate() -> dict | None:
    """
    Structural regime gate. Returns a gate-tripped result dict if market
    conditions make grid trading structurally unprofitable, else None.

    Gate fires when ALL of the following are true:
      1. price < ema_200 * 0.92  (price more than 8% below EMA200)
      2. ema_50 < ema_200        (bearish EMA stack)
      3. vol_regime == 'HIGH'    (trending vol, not ranging vol)
      4. vwap_dev_pct < -2.0     (price persistently below VWAP)

    All four conditions must be true simultaneously. This prevents the gate
    from firing on temporary dips or normal oscillation.

    When gate fires:
      - All open orders cancelled
      - Grid enters GRID_PAUSE state
      - MAGI cycle skips council votes
      - Gate re-evaluated every cycle; releases when conditions no longer met

    Gate release: fires when ANY of the following:
      - price > ema_200 * 0.95 (within 5% of EMA200)
      - ema_50 > ema_200 (EMA stack turns bullish)
      - vol_regime != 'HIGH'
    """
    from config import REGIME_GATE_ENABLED
    if not REGIME_GATE_ENABLED:
        return None

    indicators = get_latest_indicators('1h')
    if not indicators:
        return None

    price = indicators.get('close') or indicators.get('vwap')
    ema_50 = indicators.get('ema_50')
    ema_200 = indicators.get('ema_200')
    vol_regime = indicators.get('vol_regime')
    vwap_dev_pct = indicators.get('vwap_dev_pct')

    if not all([price, ema_50, ema_200, vol_regime, vwap_dev_pct is not None]):
        return None

    price_below_ema200 = price < ema_200 * 0.92
    bearish_ema_stack = ema_50 < ema_200
    high_vol = vol_regime == 'HIGH'
    below_vwap = vwap_dev_pct < -2.0

    gate_fires = all([price_below_ema200, bearish_ema_stack,
                      high_vol, below_vwap])

    if gate_fires:
        reason = (
            f"REGIME_GATE: structural downtrend detected — "
            f"price={price:.4f} is {((price/ema_200)-1)*100:.1f}% vs EMA200={ema_200:.4f}, "
            f"EMA50={ema_50:.4f} < EMA200 (bearish stack), "
            f"vol={vol_regime}, vwap_dev={vwap_dev_pct:.2f}%"
        )
        log.warning(reason)
        return {
            'grid_action': 'GRID_PAUSE',
            'risk_action': 'CLEAR',
            'regime': 'STRUCTURAL_BEAR',
            'reason': reason,
            'melchior_geometry': None,
            'melchior_conviction': None,
            'gate_fired': True,
        }

    return None


def run_cycle(trigger='scheduled', force=False):
    log.info(f"MAGI cycle starting — trigger={trigger}")

    indicators = get_latest_indicators('1h') or {}
    grid_state = get_current_grid_state() or build_empty_grid()
    inventory = get_latest_inventory() or build_empty_inventory()

    if not indicators and not force:
        log.warning("No indicator data available — skipping cycle")
        return None

    gate_result = check_regime_gate()
    if gate_result:
        log.warning("Regime gate fired — skipping council votes")
        decision_data = {
            'trigger': trigger,
            'melchior_action': None,
            'melchior_conviction': None,
            'melchior_reasoning': None,
            'melchior_concerns': None,
            'balthasar_action': None,
            'balthasar_conviction': None,
            'balthasar_reasoning': None,
            'casper_action': None,
            'casper_conviction': None,
            'casper_reasoning': None,
            'consensus_grid_action': gate_result['grid_action'],
            'consensus_risk_action': gate_result['risk_action'],
            'consensus_regime': gate_result['regime'],
            'applied': 0,
            'notes': gate_result['reason'],
        }
        insert_magi_decision(decision_data)
        from database import get_latest_magi_decision_id
        decision_id = get_latest_magi_decision_id()
        return {
            'melchior': None,
            'balthasar': None,
            'casper': None,
            'consensus': gate_result,
            'decision_id': decision_id,
        }

    from grid.engine import GridEngine
    _price_engine = GridEngine(paper=True)
    current_price = _price_engine.get_current_price()
    if current_price is None:
        log.warning("No current price for Balthasar budget context — proceeding with NULL")

    log.info("Calling MAGI agents (stateless)...")

    # Casper runs first — pure regime assessment, no other agent inputs
    casper = casper_stateless(indicators)
    log.info(f"Casper: {casper.get('regime')} / {casper.get('conviction')}")

    # Melchior runs second — receives Casper regime so it can self-suppress
    # actions that will be blocked by consensus anyway
    melchior = melchior_stateless(indicators, grid_state, inventory,
                                   casper_regime=casper)
    log.info(f"Melchior: {melchior.get('action')} / {melchior.get('conviction')}")

    # Balthasar runs last — regime-blind by design, pure capital preservation
    balthasar = balthasar_stateless(indicators, inventory, grid_state, current_price)
    log.info(f"Balthasar: {balthasar.get('action')} / {balthasar.get('conviction')}")

    consensus = apply_consensus(melchior, balthasar, casper, grid_state)
    log.info(f"Consensus: grid={consensus['grid_action']} risk={consensus['risk_action']} — {consensus['reason']}")

    # Run Supervisor — sits above the three councils.
    # In shadow mode (default), logs its decision but does not modify consensus.
    # In live mode, may override grid_action toward RECENTRE/WIDEN or risk_action toward CLEAR.
    try:
        from magi.magi_supervisor import run_supervisor
        supervisor_council = {
            'grid_action': consensus['grid_action'],
            'risk_action': consensus['risk_action'],
            'regime': consensus['regime'],
            'melchior_reasoning': melchior.get('reasoning', ''),
            'balthasar_reasoning': balthasar.get('reasoning', ''),
            'casper_reasoning': casper.get('reasoning', ''),
        }
        supervisor_result = run_supervisor(
            supervisor_council,
            cycle_timestamp=datetime.utcnow().isoformat()
        )

        if (supervisor_result['action'] == 'OVERRIDE'
                and not supervisor_result.get('shadow_mode')):
            override_target = supervisor_result['override_target']
            if override_target == 'RECENTRE':
                consensus['grid_action'] = 'RECENTRE'
            elif override_target == 'WIDEN':
                consensus['grid_action'] = 'WIDEN'
            elif override_target == 'CLEAR_PAUSE':
                consensus['risk_action'] = 'CLEAR'
            consensus['reason'] = (consensus['reason']
                                    + f" [SUPERVISOR_OVERRIDE: {override_target}]")
            log.info(f"Supervisor OVERRIDE applied: {override_target}")
    except Exception as e:
        log.warning(f"Supervisor failed — proceeding without: {e}")

    decision_data = {
        'trigger': trigger,
        'melchior_action': melchior.get('action'),
        'melchior_conviction': melchior.get('conviction'),
        'melchior_reasoning': melchior.get('reasoning'),
        'melchior_concerns': melchior.get('concerns'),
        'balthasar_action': balthasar.get('action'),
        'balthasar_conviction': balthasar.get('conviction'),
        'balthasar_reasoning': balthasar.get('reasoning'),
        'casper_action': casper.get('regime'),  # column named 'action' but stores regime string (RANGING/TRENDING/UNCERTAIN) — legacy naming
        'casper_conviction': casper.get('conviction'),
        'casper_reasoning': casper.get('reasoning'),
        'consensus_grid_action': consensus['grid_action'],
        'consensus_risk_action': consensus['risk_action'],
        'consensus_regime': consensus['regime'],
        'applied': 0,
        'notes': consensus['reason']
    }
    decision_data['melchior_centre_price'] = melchior.get('centre_price')
    decision_data['melchior_target_spacing_pct'] = melchior.get('target_spacing_pct')
    decision_data['melchior_buy_level_bias'] = melchior.get('buy_level_bias')
    decision_data['melchior_sell_level_bias'] = melchior.get('sell_level_bias')
    decision_id = insert_magi_decision(decision_data)
    if decision_id is None:
        from database import get_latest_magi_decision_id
        decision_id = get_latest_magi_decision_id()

    return {
        'melchior': melchior,
        'balthasar': balthasar,
        'casper': casper,
        'consensus': consensus,
        'decision_id': decision_id,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s')

    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    result = run_cycle(trigger='manual', force=args.force)
    if result:
        print(json.dumps({
            'melchior': result['melchior'].get('action'),
            'balthasar': result['balthasar'].get('action'),
            'casper': result['casper'].get('regime'),
            'consensus': result['consensus']
        }, indent=2))
