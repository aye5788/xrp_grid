import os
import logging
from datetime import datetime, timedelta
from letta_client import Letta
from dotenv import load_dotenv
from config import (
    LETTA_CREDIT_FLOOR, DAILY_LOSS_LIMIT_USD,
    MEMORY_BLOCK_MAX_CHARS, KILL_SWITCH_FILE
)
from database import get_conn

load_dotenv()
log = logging.getLogger('guardrails')
client = Letta(api_key=os.getenv('LETTA_API_KEY'))


def kill_switch_active() -> bool:
    """Check if the manual kill switch file exists."""
    return os.path.exists(KILL_SWITCH_FILE)


def check_letta_credits() -> tuple:
    """Return (ok, remaining_credits, reason)."""
    try:
        conn = get_conn()
        conn.execute('''CREATE TABLE IF NOT EXISTS letta_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            remaining_credits INTEGER,
            remaining_purchased INTEGER
        )''')
        row = conn.execute('''SELECT remaining_credits FROM letta_status
            ORDER BY timestamp DESC LIMIT 1''').fetchone()
        conn.close()
        if not row or row['remaining_credits'] is None:
            return True, None, "No credit data yet"
        remaining = row['remaining_credits']
        if remaining < LETTA_CREDIT_FLOOR:
            return False, remaining, f"Below floor ({LETTA_CREDIT_FLOOR})"
        return True, remaining, "OK"
    except Exception as e:
        log.warning(f"Credit check failed: {e}")
        return True, None, f"Check failed: {e}"


def check_daily_loss() -> tuple:
    """Return (ok, daily_pnl, reason)."""
    conn = get_conn()
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    fills = conn.execute('''SELECT side, price, size, fill_price, fee
        FROM grid_orders
        WHERE filled_at > ? AND status='filled' ''', (today_start,)).fetchall()
    conn.close()
    fills = [dict(f) for f in fills]

    pnl = 0.0
    for f in fills:
        if f['side'] == 'buy':
            pnl -= (f['fill_price'] or f['price']) * f['size']
        else:
            pnl += (f['fill_price'] or f['price']) * f['size']
        pnl -= (f['fee'] or 0)

    if pnl < -DAILY_LOSS_LIMIT_USD:
        return False, pnl, f"Daily loss ${pnl:.2f} exceeds limit ${DAILY_LOSS_LIMIT_USD}"
    return True, pnl, "OK"


def trim_memory_block(agent_id: str, block_label: str = 'decisions'):
    """Trim a memory block if it exceeds the size limit."""
    try:
        blocks = client.agents.blocks.list(agent_id=agent_id)
        for block in blocks:
            if block.label == block_label and block.value and len(block.value) > MEMORY_BLOCK_MAX_CHARS:
                trimmed = block.value[-(MEMORY_BLOCK_MAX_CHARS // 2):]
                nl = trimmed.find('\n')
                if nl > 0:
                    trimmed = trimmed[nl+1:]
                client.agents.blocks.update(
                    agent_id=agent_id,
                    block_label=block_label,
                    value=f"[trimmed earlier entries]\n{trimmed}"
                )
                log.info(f"Trimmed {block_label} block on {agent_id}")
                return True
    except Exception as e:
        log.warning(f"Memory trim failed for {agent_id}: {e}")
    return False


def update_credit_cache(remaining_credits: int, remaining_purchased: int = 0):
    """Cache the most recent credit balance from a Letta response."""
    conn = get_conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS letta_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        remaining_credits INTEGER,
        remaining_purchased INTEGER
    )''')
    conn.execute('''INSERT INTO letta_status
        (timestamp, remaining_credits, remaining_purchased)
        VALUES (?,?,?)''',
        (datetime.utcnow().isoformat(), remaining_credits, remaining_purchased))
    conn.commit()
    conn.close()


def check_all_guardrails() -> tuple:
    """Run all guardrails. Return (all_ok, list_of_failures)."""
    failures = []

    if kill_switch_active():
        failures.append("KILL SWITCH ACTIVE — /root/xrp_grid/HALT exists")

    credits_ok, remaining, credit_reason = check_letta_credits()
    if not credits_ok:
        failures.append(f"Letta credits: {credit_reason} (remaining: {remaining})")

    pnl_ok, daily_pnl, pnl_reason = check_daily_loss()
    if not pnl_ok:
        failures.append(f"Daily loss limit: {pnl_reason}")

    return len(failures) == 0, failures
