import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from letta_client import Letta
from database import get_latest_inventory, get_conn
from magi.letta_agents import ENV_KEYS

load_dotenv()
log = logging.getLogger('magi.learning')
client = Letta(api_key=os.getenv('LETTA_API_KEY'))
EST = ZoneInfo('America/New_York')


def has_open_position() -> bool:
    inv = get_latest_inventory()
    if not inv:
        return False
    xrp = inv.get('xrp_held', 0) or 0
    return abs(xrp) > 0.01


def is_weekend() -> bool:
    now = datetime.now(timezone.utc).astimezone(EST)
    return now.weekday() >= 5


def should_run_learning() -> tuple:
    if not is_weekend():
        return True, "Weekday"
    if has_open_position():
        return True, "Weekend with open position"
    return False, "Weekend with no position — skipping"


def get_today_decisions(hours_back: int = 24) -> list:
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).isoformat()
    rows = conn.execute('''SELECT * FROM magi_decisions
        WHERE timestamp > ?
        ORDER BY timestamp ASC''', (cutoff,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_today_pnl_summary() -> dict:
    conn = get_conn()
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    fills = conn.execute('''SELECT * FROM grid_orders
        WHERE filled_at > ? AND status='filled' ''', (cutoff,)).fetchall()
    conn.close()
    fills = [dict(f) for f in fills]
    total_fees = sum((f['fee'] or 0) for f in fills)
    return {
        'total_fills': len(fills),
        'buy_fills': sum(1 for f in fills if f['side'] == 'buy'),
        'sell_fills': sum(1 for f in fills if f['side'] == 'sell'),
        'total_fees': round(total_fees, 4)
    }


def build_summary(decisions, pnl, inventory):
    today = datetime.now(EST).strftime('%Y-%m-%d')
    melchior_actions, balthasar_actions, casper_regimes = {}, {}, {}
    for d in decisions:
        m = d.get('melchior_action') or 'NULL'
        b = d.get('balthasar_action') or 'NULL'
        c = d.get('casper_action') or 'NULL'
        melchior_actions[m] = melchior_actions.get(m, 0) + 1
        balthasar_actions[b] = balthasar_actions.get(b, 0) + 1
        casper_regimes[c] = casper_regimes.get(c, 0) + 1

    return f"""DAILY SUMMARY — {today}

Decisions made today: {len(decisions)}
Melchior actions: {melchior_actions}
Balthasar actions: {balthasar_actions}
Casper regimes: {casper_regimes}

Grid activity:
- Total fills: {pnl['total_fills']} ({pnl['buy_fills']} buys, {pnl['sell_fills']} sells)
- Total fees paid: ${pnl['total_fees']}

End-of-day inventory:
- XRP held: {inventory.get('xrp_held', 0)}
- USD held: {inventory.get('usd_held', 0)}
- Net position USD: {inventory.get('net_position_usd', 0)}
- Inventory skew: {inventory.get('inventory_skew', 0)}
"""


def build_agent_message(agent_name: str, summary: str) -> str:
    if agent_name == 'melchior':
        focus = "Reflect on your grid structural decisions today. Did your TIGHTEN/WIDEN/RECENTRE/MAINTAIN calls align with the fill rate and inventory outcomes? Update your decisions memory block with one or two sentences capturing what you learned."
    elif agent_name == 'balthasar':
        focus = "Reflect on your risk decisions today. Did inventory skew or volatility ever approach concerning levels? Did your CLEAR/PAUSE/HALT calls protect the system appropriately? Update your decisions memory block with one or two sentences capturing what you learned."
    else:
        focus = "Reflect on your regime calls today. Did RANGING calls hold up? Were any TRENDING calls premature or correct? Update your decisions memory block with one or two sentences capturing what you learned."

    return f"""{summary}

{focus}

Use the core_memory_replace tool to update your 'decisions' memory block. Keep the block concise — append today's takeaway and trim older entries if the block exceeds 2000 characters."""


def run_learning_cycle(force: bool = False):
    should_run, reason = should_run_learning()
    if not should_run and not force:
        log.info(f"Skipping learning cycle — {reason}")
        return {'skipped': True, 'reason': reason}

    log.info(f"Learning cycle starting — {reason}")
    decisions = get_today_decisions()
    if not decisions:
        log.warning("No decisions in last 24h — nothing to learn from")
        return {'skipped': True, 'reason': 'No decisions to learn from'}

    pnl = get_today_pnl_summary()
    inventory = get_latest_inventory() or {}
    summary = build_summary(decisions, pnl, inventory)

    log.info(f"Summary built — {len(decisions)} decisions, {pnl['total_fills']} fills")

    results = {}
    for agent_name in ['melchior', 'balthasar', 'casper']:
        agent_id = os.getenv(ENV_KEYS[agent_name])
        if not agent_id:
            results[agent_name] = "no agent ID"
            continue
        message = build_agent_message(agent_name, summary)
        try:
            client.agents.messages.create(agent_id=agent_id, input=message)
            results[agent_name] = "ok"
            log.info(f"{agent_name} learning cycle complete")
        except Exception as e:
            results[agent_name] = f"error: {e}"
            log.error(f"{agent_name} learning cycle failed: {e}")

    from guardrails import trim_memory_block
    for agent_name in ['melchior', 'balthasar', 'casper']:
        agent_id = os.getenv(ENV_KEYS[agent_name])
        if agent_id:
            trim_memory_block(agent_id, 'decisions')

    return {
        'reason': reason,
        'decisions_count': len(decisions),
        'pnl': pnl,
        'results': results
    }


if __name__ == "__main__":
    import argparse
    import json
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    result = run_learning_cycle(force=args.force)
    print(json.dumps(result, indent=2, default=str))
