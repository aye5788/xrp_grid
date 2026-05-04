import json
import logging
import argparse
from dotenv import load_dotenv
from database import (
    get_latest_indicators, get_current_grid_state, get_latest_inventory,
    insert_magi_decision, insert_grid_state
)
from magi.melchior import get_decision as melchior_stateless
from magi.balthasar import get_decision as balthasar_stateless
from magi.casper import get_decision as casper_stateless

load_dotenv()
log = logging.getLogger('magi.orchestrator')


def build_empty_inventory():
    return {'xrp_held': 0, 'usd_held': 0, 'net_position_usd': 0, 'inventory_skew': 0}


def build_empty_grid():
    from config import GRID_SPACING_PCT, GRID_LEVELS
    return {
        'centre_price': None, 'spacing_pct': GRID_SPACING_PCT,
        'levels': GRID_LEVELS, 'pause_longs': 0,
        'pause_shorts': 0, 'halt': 0
    }


def apply_consensus(melchior, balthasar, casper, current_grid):
    b_action = balthasar.get('action', 'CLEAR')
    c_regime = casper.get('regime', 'UNCERTAIN')
    m_action = melchior.get('action', 'MAINTAIN')

    if b_action == 'HALT':
        return {
            'grid_action': 'HALT',
            'risk_action': 'HALT',
            'regime': c_regime,
            'reason': 'Balthasar HALT — all grid activity suspended'
        }

    risk_action = b_action

    if c_regime == 'TRENDING':
        grid_action = 'MAINTAIN'
        reason = f'Casper TRENDING — blocking Melchior {m_action}, holding grid structure'
    else:
        grid_action = m_action
        reason = f'Casper {c_regime} — applying Melchior {m_action}'

    return {
        'grid_action': grid_action,
        'risk_action': risk_action,
        'regime': c_regime,
        'reason': reason
    }


def run_cycle(trigger='scheduled', force=False):
    log.info(f"MAGI cycle starting — trigger={trigger}")

    indicators = get_latest_indicators('1h') or {}
    grid_state = get_current_grid_state() or build_empty_grid()
    inventory = get_latest_inventory() or build_empty_inventory()

    if not indicators and not force:
        log.warning("No indicator data available — skipping cycle")
        return None

    log.info("Calling MAGI agents (stateless)...")
    melchior = melchior_stateless(indicators, grid_state)
    balthasar = balthasar_stateless(indicators, inventory, grid_state)
    casper = casper_stateless(indicators)

    log.info(f"Melchior: {melchior.get('action')} / {melchior.get('conviction')}")
    log.info(f"Balthasar: {balthasar.get('action')} / {balthasar.get('conviction')}")
    log.info(f"Casper: {casper.get('regime')} / {casper.get('conviction')}")

    consensus = apply_consensus(melchior, balthasar, casper, grid_state)
    log.info(f"Consensus: grid={consensus['grid_action']} risk={consensus['risk_action']} — {consensus['reason']}")

    decision_data = {
        'trigger': trigger,
        'melchior_action': melchior.get('action'),
        'melchior_conviction': melchior.get('conviction'),
        'melchior_reasoning': melchior.get('reasoning'),
        'melchior_concerns': melchior.get('concerns'),
        'balthasar_action': balthasar.get('action'),
        'balthasar_conviction': balthasar.get('conviction'),
        'balthasar_reasoning': balthasar.get('reasoning'),
        'casper_action': casper.get('regime'),
        'casper_conviction': casper.get('conviction'),
        'casper_reasoning': casper.get('reasoning'),
        'consensus_grid_action': consensus['grid_action'],
        'consensus_risk_action': consensus['risk_action'],
        'consensus_regime': consensus['regime'],
        'applied': 0,
        'notes': consensus['reason']
    }
    insert_magi_decision(decision_data)

    return {
        'melchior': melchior,
        'balthasar': balthasar,
        'casper': casper,
        'consensus': consensus
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
