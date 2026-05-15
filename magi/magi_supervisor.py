"""
MAGI Supervisor — sits above the three councils.
Job: ensure council recommendations serve profitable trading, not just avoid risk.

Model: gemini-2.5-flash, routed through Cloudflare AI Gateway (matches Casper).
Memory: Mem0 (persistent, agent_id='magi_supervisor')
Mode: shadow_mode=True initially — logs decisions but does not override grid

Outputs:
  APPROVE — council recommendation stands
  OVERRIDE [RECENTRE|WIDEN|CLEAR_PAUSE] — override with logged reasoning
"""

import os
import json
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

from google import genai
from google.genai import types as _types

load_dotenv('/root/xrp_grid/.env')
log = logging.getLogger('magi.supervisor')

# Shadow mode: True = log only, False = actually override
SHADOW_MODE = False

# Gemini client via Cloudflare AI Gateway (matches Casper's routing)
from config import GOOGLE_API_KEY, cf_gateway_url, CF_AIG_TOKEN
_gw = cf_gateway_url("google-ai-studio")
_client = genai.Client(
    api_key=GOOGLE_API_KEY,
    **({"http_options": _types.HttpOptions(
            base_url=_gw,
            headers={"cf-aig-authorization": f"Bearer {CF_AIG_TOKEN}"}
        )} if _gw else {})
)

# Mem0 client — wrapped so a missing key or network failure doesn't break import
MEM0_AGENT_ID = 'magi_supervisor'
_mem0 = None
try:
    from mem0 import MemoryClient
    _mem0_key = os.getenv('MEM0_API_KEY')
    if _mem0_key:
        _mem0 = MemoryClient(api_key=_mem0_key)
    else:
        log.warning("MEM0_API_KEY not set — Supervisor will run without memory")
except Exception as e:
    log.warning(f"Mem0 client init failed — Supervisor will run without memory: {e}")


def _get_grid_health() -> dict:
    """Pull current grid health from observer.db."""
    from database import get_conn
    conn = get_conn()

    open_buys = conn.execute(
        "SELECT COUNT(*) FROM grid_orders WHERE status='open' AND side='buy'"
    ).fetchone()[0]

    open_sells = conn.execute(
        "SELECT COUNT(*) FROM grid_orders WHERE status='open' AND side='sell'"
    ).fetchone()[0]

    last_fill = conn.execute(
        "SELECT filled_at FROM grid_orders WHERE status='filled' "
        "ORDER BY filled_at DESC LIMIT 1"
    ).fetchone()

    fills_7d = conn.execute(
        "SELECT COUNT(*) FROM grid_orders WHERE status='filled' "
        "AND filled_at > datetime('now', '-7 days')"
    ).fetchone()[0]

    fills_24h = conn.execute(
        "SELECT COUNT(*) FROM grid_orders WHERE status='filled' "
        "AND filled_at > datetime('now', '-1 day')"
    ).fetchone()[0]

    latest_inv = conn.execute(
        "SELECT xrp_held, usd_held, inventory_skew FROM inventory "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    latest_grid = conn.execute(
        "SELECT centre_price, spacing_pct FROM grid_state "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    latest_price = conn.execute(
        "SELECT close FROM candles WHERE timeframe='1h' "
        "ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()

    recent_decisions = conn.execute(
        "SELECT consensus_grid_action, consensus_risk_action, consensus_regime, timestamp "
        "FROM magi_decisions ORDER BY timestamp DESC LIMIT 5"
    ).fetchall()

    conn.close()

    hours_since_fill = 999
    if last_fill and last_fill[0]:
        try:
            lf = datetime.fromisoformat(last_fill[0].replace('Z', ''))
            if lf.tzinfo is None:
                lf = lf.replace(tzinfo=timezone.utc)
            hours_since_fill = (datetime.now(timezone.utc) - lf).total_seconds() / 3600
        except Exception:
            pass

    drift_pct = None
    if latest_grid and latest_price and latest_grid[0] and latest_price[0]:
        drift_pct = round((latest_price[0] - latest_grid[0]) / latest_grid[0] * 100, 2)

    return {
        'open_buys': open_buys,
        'open_sells': open_sells,
        'hours_since_last_fill': round(hours_since_fill, 1),
        'fills_last_24h': fills_24h,
        'fills_last_7d': fills_7d,
        'allocation_skew': round(latest_inv[2], 3) if latest_inv else None,
        'xrp_held': round(latest_inv[0], 4) if latest_inv else None,
        'usd_held': round(latest_inv[1], 2) if latest_inv else None,
        'grid_centre': latest_grid[0] if latest_grid else None,
        'current_price': latest_price[0] if latest_price else None,
        'drift_pct': drift_pct,
        'recent_decisions': [
            {
                'grid': r[0],
                'risk': r[1],
                'regime': r[2],
                'timestamp': r[3]
            } for r in recent_decisions
        ]
    }


def _get_mem0_context(query: str) -> str:
    """Retrieve relevant memories from Mem0."""
    if _mem0 is None:
        return "Mem0 client unavailable — proceeding without memory context."
    try:
        # Current Mem0 SDK: agent_id must be passed inside `filters`, and the
        # result limit parameter is `top_k`, not `limit`. Top-level `agent_id`
        # or `limit` are rejected with ValidationError.
        results = _mem0.search(
            query,
            filters={'agent_id': MEM0_AGENT_ID},
            top_k=5
        )
        if not results:
            return "No prior Supervisor memory available."
        items = results.get('results', []) if isinstance(results, dict) else results
        memories = [r.get('memory') for r in items if r.get('memory')]
        return "\n".join(f"- {m}" for m in memories) if memories else "No relevant memories found."
    except Exception as e:
        log.warning(f"Mem0 search failed: {e}")
        return "Mem0 unavailable — proceeding without memory context."


def _write_mem0_outcome(supervisor_action: str, override_target: str,
                         council_recommendation: str, grid_health: dict,
                         outcome: str, outcome_notes: str):
    """Write outcome back to Mem0 after the observation window."""
    if _mem0 is None:
        return
    try:
        content = (
            f"Supervisor {supervisor_action}"
            + (f" to {override_target}" if override_target else "")
            + f". Council had recommended {council_recommendation}. "
            f"Grid state: open_buys={grid_health.get('open_buys')}, "
            f"hours_idle={grid_health.get('hours_since_last_fill')}, "
            f"drift={grid_health.get('drift_pct')}%. "
            f"Outcome: {outcome}. {outcome_notes}"
        )
        _mem0.add([
            {"role": "user", "content": f"What happened after Supervisor {supervisor_action}?"},
            {"role": "assistant", "content": content}
        ], agent_id=MEM0_AGENT_ID)
        log.info(f"Supervisor outcome written to Mem0: {outcome}")
    except Exception as e:
        log.warning(f"Mem0 write failed: {e}")


def _call_supervisor(council: dict, grid_health: dict, mem0_context: str) -> dict:
    """Call Gemini 2.5 Flash with full Supervisor context."""

    prompt = f"""You are the MAGI Supervisor. Your job is NOT risk management — the three councils handle that.
Your job is to ensure the council's collective recommendation is actually serving profitable grid trading.

COUNCIL RECOMMENDATION:
- Grid action: {council.get('grid_action')}
- Risk action: {council.get('risk_action')}
- Regime: {council.get('regime')}
- Melchior reasoning: {(council.get('melchior_reasoning') or 'N/A')[:300]}
- Balthasar reasoning: {(council.get('balthasar_reasoning') or 'N/A')[:300]}
- Casper reasoning: {(council.get('casper_reasoning') or 'N/A')[:300]}

CURRENT GRID HEALTH:
- Open buy orders: {grid_health['open_buys']}
- Open sell orders: {grid_health['open_sells']}
- Hours since last fill: {grid_health['hours_since_last_fill']}
- Fills last 24h: {grid_health['fills_last_24h']}
- Fills last 7 days: {grid_health['fills_last_7d']}
- Allocation skew: {grid_health['allocation_skew']} (0=neutral, +1=all XRP, -1=all USD)
- Current price: {grid_health['current_price']}
- Grid centre: {grid_health['grid_centre']}
- Drift from centre: {grid_health['drift_pct']}%

RECENT DECISION HISTORY (last 5 cycles):
{json.dumps(grid_health['recent_decisions'], indent=2)}

YOUR PRIOR MEMORY (what you have learned from past decisions):
{mem0_context}

YOUR MANDATE:
Evaluate whether the council's recommendation serves profitable trading RIGHT NOW.
Ask: if we execute MAINTAIN for another cycle, what is the opportunity cost?
Ask: is the stated risk real given the actual order book state?
Ask: does Balthasar's reasoning apply to a grid with {grid_health['open_buys']} open buy orders?

YOU MAY ONLY:
1. APPROVE — council recommendation is sound, execute as-is
2. OVERRIDE RECENTRE — rebuild grid at current price
3. OVERRIDE WIDEN — expand grid spacing
4. OVERRIDE CLEAR_PAUSE — lift risk restriction when it has no protective value

YOU MAY NOT override toward greater conservatism. You cannot trigger HALT or TIGHTEN.

Respond in this exact JSON format:
{{
  "action": "APPROVE" or "OVERRIDE",
  "override_target": null or "RECENTRE" or "WIDEN" or "CLEAR_PAUSE",
  "reasoning": "one to three sentences explaining your decision",
  "concerns": "any risks with your decision, or null"
}}"""

    try:
        response = _client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=_types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                thinking_config=_types.ThinkingConfig(thinking_budget=0)
            )
        )
        if not response.text:
            raise ValueError("Empty response from Gemini")
        result = json.loads(response.text)
        return {
            'action': result.get('action', 'APPROVE'),
            'override_target': result.get('override_target'),
            'reasoning': result.get('reasoning', ''),
            'concerns': result.get('concerns')
        }
    except Exception as e:
        log.error(f"Supervisor LLM call failed: {e}")
        return {
            'action': 'APPROVE',
            'override_target': None,
            'reasoning': f'Supervisor LLM error — defaulting to APPROVE: {e}',
            'concerns': None
        }


def run_supervisor(council: dict, cycle_timestamp: str) -> dict:
    """
    Main entry point. Called from orchestrator after council vote.

    Args:
        council: dict with keys grid_action, risk_action, regime,
                 melchior_reasoning, balthasar_reasoning, casper_reasoning
        cycle_timestamp: ISO timestamp of current MAGI cycle

    Returns:
        dict with keys: action, override_target, reasoning, shadow_mode
        If shadow_mode=True, action is always APPROVE for grid purposes
        but the Supervisor's true decision is logged.
    """
    from database import insert_supervisor_decision

    grid_health = _get_grid_health()

    query = (
        f"{council.get('grid_action')} {council.get('risk_action')} "
        f"{council.get('regime')} open_buys={grid_health['open_buys']} "
        f"hours_idle={grid_health['hours_since_last_fill']}"
    )
    mem0_context = _get_mem0_context(query)

    decision = _call_supervisor(council, grid_health, mem0_context)

    log.info(
        f"Supervisor: {decision['action']}"
        + (f" → {decision['override_target']}" if decision['override_target'] else "")
        + f" | shadow={SHADOW_MODE} | {decision['reasoning'][:100]}"
    )

    insert_supervisor_decision(
        cycle_timestamp=cycle_timestamp,
        council_grid_action=council.get('grid_action'),
        council_risk_action=council.get('risk_action'),
        council_regime=council.get('regime'),
        supervisor_action=decision['action'],
        override_target=decision['override_target'],
        reasoning=decision['reasoning'],
        shadow_mode=1 if SHADOW_MODE else 0
    )

    if SHADOW_MODE:
        log.info("Supervisor in SHADOW MODE — council recommendation stands regardless of Supervisor decision")
        return {
            'action': 'APPROVE',
            'override_target': None,
            'reasoning': decision['reasoning'],
            'shadow_mode': True,
            'true_decision': decision['action'],
            'true_override_target': decision['override_target']
        }

    return {
        'action': decision['action'],
        'override_target': decision['override_target'],
        'reasoning': decision['reasoning'],
        'shadow_mode': False
    }


def record_outcomes():
    """
    Called from observer cycle. Checks for Supervisor decisions
    that are 6+ hours old and records outcomes from DB data.
    Only runs when shadow_mode=False (filter applied in SQL).
    """
    from database import (
        get_pending_outcome_decisions, record_supervisor_outcome, get_conn
    )

    pending = get_pending_outcome_decisions(hours_threshold=6)
    if not pending:
        return

    for decision in pending:
        decision_ts = decision['timestamp']
        decision_id = decision['id']

        conn = get_conn()
        fills_after = conn.execute(
            "SELECT COUNT(*) FROM grid_orders WHERE status='filled' AND filled_at > ?",
            (decision_ts,)
        ).fetchone()[0]
        conn.close()

        if fills_after >= 2:
            outcome = 'POSITIVE'
            notes = f"{fills_after} fills since decision. Grid active."
        elif fills_after == 1:
            outcome = 'NEUTRAL'
            notes = f"1 fill since decision. Minimal activity."
        else:
            outcome = 'NEGATIVE'
            notes = f"0 fills since decision. Grid still idle."

        record_supervisor_outcome(decision_id, outcome, notes)

        # Pull real grid health for the Mem0 outcome entry so the memory
        # has actual context rather than a placeholder dict.
        _write_mem0_outcome(
            supervisor_action=decision['supervisor_action'],
            override_target=decision['override_target'],
            council_recommendation=decision['council_grid_action'],
            grid_health=_get_grid_health(),
            outcome=outcome,
            outcome_notes=notes
        )

        log.info(f"Supervisor outcome recorded: decision {decision_id} → {outcome}")
