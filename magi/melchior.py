import json
import logging
from openai import OpenAI
from config import OPENAI_API_KEY

log = logging.getLogger('magi.melchior')

client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT_PATH = "/root/xrp_grid/magi/prompts/melchior_prompt.txt"

def load_prompt():
    with open(SYSTEM_PROMPT_PATH, 'r') as f:
        return f.read()

def build_context(indicators: dict, grid_state: dict, inventory: dict,
                  casper_regime: dict = None) -> str:
    """Build the context package Melchior receives."""
    from database import get_open_orders_summary, get_trajectory_context
    orders = get_open_orders_summary()
    traj = get_trajectory_context()
    inv_skew = inventory.get('inventory_skew')

    if inv_skew is None:
        skew_line = "- inventory_skew: NULL\n"
    else:
        skew_line = (
            f"- inventory_skew: {inv_skew:+.3f}  (range ±1; 0 = balanced 50/50, "
            f"+1 = all XRP, -1 = all USD; Balthasar manages risk thresholds)\n"
        )

    if casper_regime is not None:
        regime_block = (
            f"\nRegime Context (from Casper):\n"
            f"- regime: {casper_regime.get('regime')}\n"
            f"- conviction: {casper_regime.get('conviction')}\n"
            f"- trend_direction: {casper_regime.get('trend_direction')}\n"
            f"- reasoning: {casper_regime.get('reasoning')}\n"
            f"Note: If regime is TRENDING, your TIGHTEN or RECENTRE recommendation "
            f"will be blocked by consensus. Consider whether WIDEN or MAINTAIN is "
            f"more appropriate given this regime."
        )
    else:
        regime_block = ""

    return f"""CURRENT MARKET MICROSTRUCTURE — XRP/USD

Grid Parameters:
- grid_centre: {grid_state.get('centre_price', 'NULL')}
- grid_spacing_pct: {grid_state.get('spacing_pct', 'NULL')}
- pause_longs: {grid_state.get('pause_longs', 0)}
- pause_shorts: {grid_state.get('pause_shorts', 0)}

Microstructure Signals:
- vwap_dev_pct: {indicators.get('vwap_dev_pct', 'NULL')}%
- vol_regime: {indicators.get('vol_regime', 'NULL')}
- autocorr_1h: {indicators.get('autocorr_1h', 'NULL')}
- autocorr_4h: {indicators.get('autocorr_4h', 'NULL')}
- atr: {indicators.get('atr', 'NULL')}
- atr_percentile: {indicators.get('atr_percentile', 'NULL')}
{skew_line}
Order Ladder (live open orders):
- open_buys: {orders['buy_count']} orders, highest bid: {orders['highest_buy']}
- open_sells: {orders['sell_count']} orders, lowest ask: {orders['lowest_sell']}
- fills last 24h: {len(orders['recent_fills'])} fills

Trajectory Context:
- casper_regime_consecutive_cycles: {traj['regime_consecutive']} (how long current regime has held)
- melchior_blocked_cycles: {traj['melchior_blocked_cycles']} (consecutive cycles your recommendation was overridden)
- cycles_since_structural_change: {traj['cycles_since_structural_change']} (grid unchanged for this many cycles)
- fills_since_last_magi: {traj['fills_since_last_magi_buys']} buys / {traj['fills_since_last_magi_sells']} sells
- pause_longs_active: {traj['pause_longs_active']} | pause_shorts_active: {traj['pause_shorts_active']}
{regime_block}
Respond with a JSON object only. No preamble."""

def get_decision(indicators: dict, grid_state: dict, inventory: dict,
                 casper_regime: dict = None) -> dict:
    """Call Melchior and return structured decision."""
    try:
        system_prompt = load_prompt()
        context = build_context(indicators, grid_state, inventory, casper_regime)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ],
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"}
        )
        raw = response.choices[0].message.content
        result = json.loads(raw)
        try:
            from database import insert_token_usage
            from magi.costs import estimate_cost
            pt = response.usage.prompt_tokens
            ct = response.usage.completion_tokens
            tt = response.usage.total_tokens
            cost = estimate_cost("gpt-4o", pt, ct)
            insert_token_usage(
                agent="melchior",
                model="gpt-4o",
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                source="direct"
            )
        except Exception as e:
            log.warning(f"Melchior token logging failed: {e}")
        log.info(f"Melchior: action={result.get('action')} conviction={result.get('conviction')}")
        return result
    except Exception as e:
        log.error(f"Melchior error: {e}")
        return {
            "agent": "melchior",
            "action": "MAINTAIN",
            "conviction": "low",
            "recentre_target": None,
            "spacing_adjustment_pct": None,
            "reasoning": f"Error: {str(e)}",
            "concerns": "Agent call failed — defaulting to MAINTAIN"
        }
