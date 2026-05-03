from letta_client import Letta
import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger('magi.letta')

client = Letta(api_key=os.getenv('LETTA_API_KEY'))

ENV_KEYS = {
    'melchior': 'MELCHIOR_AGENT_ID',
    'balthasar': 'BALTHASAR_AGENT_ID',
    'casper': 'CASPER_AGENT_ID'
}

MODELS = {
    'melchior': 'GEEP/gpt-4o',
    'balthasar': 'BATHY/claude-sonnet-4-6',
    'casper': 'GEMMNY/gemini-2.5-flash'
}

MODEL_SETTINGS = {
    'melchior': {'provider_type': 'openai', 'temperature': 0.2},
    'balthasar': {'provider_type': 'anthropic', 'temperature': 0.2},
    'casper': {'provider_type': 'google_ai', 'temperature': 0.2}
}

DISPLAY_NAMES = {
    'melchior': 'Melchior',
    'balthasar': 'Balthasar',
    'casper': 'Casper'
}


def load_prompt(name: str) -> str:
    path = f'/root/xrp_grid/magi/prompts/{name}_prompt.txt'
    with open(path, 'r') as f:
        return f.read()


def write_agent_id_to_env(env_key: str, agent_id: str):
    env_path = '/root/xrp_grid/.env'
    with open(env_path, 'r') as f:
        lines = f.readlines()
    with open(env_path, 'w') as f:
        for line in lines:
            if line.startswith(f'{env_key}='):
                f.write(f'{env_key}={agent_id}\n')
            else:
                f.write(line)


def get_or_create_agent(name: str) -> str:
    env_key = ENV_KEYS[name]
    agent_id = os.getenv(env_key, '').strip()

    if agent_id:
        try:
            agent = client.agents.retrieve(agent_id=agent_id)
            log.info(f"{DISPLAY_NAMES[name]} resumed — id={agent_id}")
            return agent_id
        except Exception:
            log.warning(f"{DISPLAY_NAMES[name]} agent ID in .env not found on Letta — creating new")

    prompt = load_prompt(name)
    agent = client.agents.create(
        name=DISPLAY_NAMES[name],
        model=MODELS[name],
        memory_blocks=[
            {
                "label": "persona",
                "value": prompt
            },
            {
                "label": "human",
                "value": "I am the MAGI orchestrator for the XRP/USD spot grid bot. I will send you structured market data each cycle and expect a JSON decision in return."
            },
            {
                "label": "decisions",
                "value": "No decisions recorded yet. This block accumulates a running summary of past decisions and outcomes to inform future reasoning."
            }
        ],
        model_settings=MODEL_SETTINGS[name]
    )
    agent_id = agent.id
    log.info(f"{DISPLAY_NAMES[name]} created — id={agent_id}")
    write_agent_id_to_env(env_key, agent_id)
    load_dotenv(override=True)
    return agent_id


def send_message(agent_id: str, message: str, agent_name: str = None, model: str = None) -> str:
    """Send a message to a Letta agent and return the assistant response text."""
    from config import MAX_LETTA_STEPS
    from guardrails import update_credit_cache
    try:
        response = client.agents.messages.create(
            agent_id=agent_id,
            input=message,
            include_return_message_types=['assistant_message'],
            max_steps=MAX_LETTA_STEPS
        )
    except Exception as e:
        err_str = str(e)
        import re as _re
        match = _re.search(r"remainingMonthlyCredits['\": ]+(\d+)", err_str)
        if match:
            try:
                update_credit_cache(int(match.group(1)))
            except:
                pass
        raise

    if agent_name and hasattr(response, 'usage') and response.usage:
        try:
            from database import insert_token_usage
            from magi.costs import estimate_cost
            usage = response.usage
            prompt_t = getattr(usage, 'prompt_tokens', 0) or 0
            comp_t = getattr(usage, 'completion_tokens', 0) or 0
            total_t = getattr(usage, 'total_tokens', 0) or (prompt_t + comp_t)
            cost = estimate_cost(model or 'unknown', prompt_t, comp_t)
            insert_token_usage(agent_name, model or 'unknown', prompt_t, comp_t, total_t, cost, source='letta')
        except Exception as e:
            log.warning(f"Token usage logging failed: {e}")

    for msg in response.messages:
        if getattr(msg, 'message_type', None) == 'assistant_message':
            content = msg.content
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if hasattr(part, 'text'):
                        parts.append(part.text)
                    elif isinstance(part, str):
                        parts.append(part)
                return ''.join(parts)
    return ''


def initialise_all_agents() -> tuple:
    log.info("Initialising Letta agents...")
    melchior_id = get_or_create_agent('melchior')
    balthasar_id = get_or_create_agent('balthasar')
    casper_id = get_or_create_agent('casper')
    log.info("All agents ready")
    return melchior_id, balthasar_id, casper_id


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s — %(message)s')
    load_dotenv()

    m, b, c = initialise_all_agents()
    print(f"\nMelchior: {m}")
    print(f"Balthasar: {b}")
    print(f"Casper: {c}")

    test = "TEST CYCLE: vol_regime=LOW, vwap_dev_pct=0.54, autocorr_1h=-0.18, autocorr_4h=-0.12, inventory_skew=0.0. Respond with your JSON decision only."

    print("\n--- Melchior ---")
    print(send_message(m, test))

    print("\n--- Balthasar ---")
    print(send_message(b, test))

    print("\n--- Casper ---")
    print(send_message(c, test))
