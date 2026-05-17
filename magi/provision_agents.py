"""
provision_agents.py — one-time provisioning of MAGI council agents in Letta.

Idempotent:
  - Looks up shared blocks by label; reuses if present, creates otherwise.
  - Per agent: if agent_registry already has a row for the logical agent,
    the script logs "already provisioned" and does NOT recreate the agent.
  - Safe to run repeatedly.

Run:
    cd /root/xrp_grid && python3 -m magi.provision_agents
"""

import os
import sys
from pathlib import Path

# Allow `python -m magi.provision_agents` from any cwd
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / '.env')

from letta_client import Letta

import database as db


PROMPT_DIR = _REPO_ROOT / 'magi' / 'prompts'

# Existing system models -> Letta handles. The Letta handle for Balthasar's
# claude-sonnet-4-6 was discovered via client.models.list(); _validate_model()
# below re-checks at runtime and exits loudly if any handle is missing.
AGENT_SPECS = [
    {
        'agent_id':    'casper',
        # Letta Cloud catalog (user-key path) only has gemini-3 family —
        # 2.5-flash isn't there. 3-flash-preview is the closest analogue.
        'model':       'google_ai/gemini-3-flash-preview',
        'prompt_file': PROMPT_DIR / 'casper_prompt.txt',
    },
    {
        'agent_id':    'melchior',
        'model':       'openai/gpt-4o',
        'prompt_file': PROMPT_DIR / 'melchior_prompt.txt',
    },
    {
        'agent_id':    'balthasar',
        # existing system uses 'claude-sonnet-4-6' (see magi/balthasar.py:127)
        'model':       'anthropic/claude-sonnet-4-6',
        'prompt_file': PROMPT_DIR / 'balthasar_prompt.txt',
    },
]

EMBEDDING = 'openai/text-embedding-3-small'

# Per-agent LLM config knobs synced on every re-provision. Provider-shaped
# (the model_settings.provider_type discriminates which fields apply).
# Equalised across the three agents wherever the providers expose an
# equivalent knob; provider-side asymmetries (e.g. GPT-4o has no native
# extended-thinking budget — only reasoning_effort) are documented inline.
# parallel_tool_calls is omitted: Letta server-side enforces True regardless
# of what we send.
AGENT_CONFIG = {
    'casper': {
        'provider_type': 'google_ai',
        'temperature': 0.3,
        'max_output_tokens': 8192,
        'thinking_config': {'include_thoughts': True, 'thinking_budget': 2048},
    },
    'melchior': {
        # GPT-4o has no native extended-thinking budget — reasoning_effort
        # is the closest equivalent (Letta-managed inner-thoughts surface).
        # Provider asymmetry, not bias.
        'provider_type': 'openai',
        'temperature': 0.3,
        'max_output_tokens': 8192,
        'reasoning': {'reasoning_effort': 'medium'},
        'strict': False,
    },
    'balthasar': {
        'provider_type': 'anthropic',
        'temperature': 0.3,
        'max_output_tokens': 8192,
        'thinking': {'type': 'enabled', 'budget_tokens': 2048},
        'effort': 'medium',
        'strict': False,
    },
}


def _model_settings_diff(live, desired):
    """Return (changed, missing_or_diff_keys). live and desired are dicts;
    we only flag keys that appear in `desired` and differ from `live`."""
    diffs = []
    for k, v in desired.items():
        if live.get(k) != v:
            diffs.append((k, live.get(k), v))
    return diffs

WORLD_STATE_DESC = (
    "Current market state for the cycle: indicators, inventory, open orders, "
    "recent fills, market knowledge, hard rules. Updated by the orchestrator "
    "at the start of every cycle. Read-only for agents."
)
CYCLE_PHASE_DESC = (
    "The current debate phase: 'round_0' (independent assessment), 'round_1' "
    "(debate on conflict). Agents must respond appropriately to the current "
    "phase."
)
SELF_MODEL_DESC = (
    "My evolving self-model: patterns I notice in my own decisions, "
    "calibration adjustments, biases I have identified in myself, situations "
    "where I tend to be wrong. I should update this block whenever I notice "
    "a recurring pattern in my outcomes."
)
SELF_MODEL_SEED = (
    "(no self-reflections yet — this is where I will record patterns I "
    "observe in my own performance over time)"
)
SHARED_AWAITING_VALUE = "(awaiting first cycle)"


def _validate_model(client, handle, role):
    """
    Verify that `handle` is present in client.models.list(); on failure print
    every available handle and exit non-zero. No silent substitution.
    """
    available = []
    for m in client.models.list():
        h = getattr(m, 'handle', None) or getattr(m, 'name', None) \
            or getattr(m, 'id', None)
        if h:
            available.append(h)
    if handle not in available:
        print(
            f"ERROR: model handle {handle!r} (requested for agent "
            f"{role!r}) is not available in this Letta server."
        )
        print("Available LLM handles:")
        for h in sorted(set(available)):
            print(f"  {h}")
        sys.exit(2)


def _get_or_create_block(client, label, value, description, limit, read_only):
    """
    Look up a shared block by exact label; create if not present.
    Returns the block id. Idempotent across re-runs of this script.
    """
    existing = list(client.blocks.list(label=label, limit=1))
    if existing:
        b = existing[0]
        print(f"  reuse block label={label!r} id={b.id}")
        return b.id
    b = client.blocks.create(
        label=label,
        value=value,
        description=description,
        limit=limit,
        read_only=read_only,
    )
    print(f"  create block label={label!r} id={b.id}")
    return b.id


def main():
    api_key = os.environ.get('LETTA_API_KEY')
    if not api_key:
        sys.exit(
            "ERROR: LETTA_API_KEY must be set in /root/xrp_grid/.env "
            "(Letta Cloud API key from app.letta.com → Settings → API Keys)"
        )

    # Letta Cloud is the SDK default when only api_key is passed —
    # base_url resolves to https://api.letta.com automatically.
    client = Letta(api_key=api_key)
    print(f"Connecting to Letta at {client.base_url} ...")

    # 1) Validate all model handles up front so we fail fast.
    print("Validating model handles ...")
    for spec in AGENT_SPECS:
        _validate_model(client, spec['model'], spec['agent_id'])
    print("  all model handles OK")

    # 2) Get-or-create the five shared blocks (idempotent by label).
    print("Provisioning shared blocks ...")
    world_id = _get_or_create_block(
        client,
        label='world_state',
        value=SHARED_AWAITING_VALUE,
        description=WORLD_STATE_DESC,
        limit=15000,
        read_only=True,
    )

    peer_block_ids = []
    for spec in AGENT_SPECS:
        agent_id = spec['agent_id']
        label = f"{agent_id}_r0_output"
        desc = (
            f"The most recent Round 0 output from {agent_id}: position, "
            f"key_evidence, crux. Updated by the orchestrator after each "
            f"Round 0. Used by peers during Round 1 debate. Read-only for "
            f"agents."
        )
        bid = _get_or_create_block(
            client,
            label=label,
            value=SHARED_AWAITING_VALUE,
            description=desc,
            limit=2000,
            read_only=True,
        )
        peer_block_ids.append(bid)

    cycle_phase_id = _get_or_create_block(
        client,
        label='cycle_phase',
        value='round_0',
        description=CYCLE_PHASE_DESC,
        limit=200,
        read_only=True,
    )

    all_shared_block_ids = [world_id, *peer_block_ids, cycle_phase_id]
    print(f"  shared block ids: {all_shared_block_ids}")

    # 3) Provision each agent (skip if already in agent_registry).
    print()
    created_count = 0
    updated_count = 0
    skipped_count = 0
    summary = []

    for spec in AGENT_SPECS:
        agent_id = spec['agent_id']
        existing_letta_id = db.get_letta_agent_id(agent_id)
        if existing_letta_id:
            # UPDATE path: sync persona block from prompt file → Letta.
            # Re-running this script is the canonical way to push prompt edits
            # to deployed agents.
            prompt_path = spec['prompt_file']
            if not prompt_path.exists():
                print(f"  WARN: prompt file missing for {agent_id} "
                      f"({prompt_path}) -- skipping (won't wipe live persona)")
                skipped_count += 1
                row = db.get_agent_registry_row(agent_id) or {}
                summary.append({
                    'agent_id': agent_id,
                    'letta_id': existing_letta_id,
                    'model': row.get('model', '?'),
                    'attached_block_count': 'unchanged',
                })
                continue

            new_persona = prompt_path.read_text()
            try:
                existing_blocks = list(
                    client.agents.blocks.list(existing_letta_id)
                )
                persona_block = next(
                    (b for b in existing_blocks if b.label == 'persona'),
                    None,
                )

                if persona_block is None:
                    # Defensive: shouldn't happen, but don't crash.
                    new_block = client.blocks.create(
                        label='persona', value=new_persona, limit=8000,
                    )
                    client.agents.blocks.attach(
                        new_block.id, agent_id=existing_letta_id,
                    )
                    print(f"attached persona block: agent_id={agent_id} "
                          f"letta_id={existing_letta_id} "
                          f"block_id={new_block.id}")
                    updated_count += 1
                elif persona_block.value == new_persona:
                    print(f"up to date: agent_id={agent_id} "
                          f"letta_id={existing_letta_id} -- persona unchanged")
                    skipped_count += 1
                else:
                    old_len = len(persona_block.value)
                    new_len = len(new_persona)
                    client.agents.blocks.update(
                        block_label='persona',
                        agent_id=existing_letta_id,
                        value=new_persona,
                    )
                    print(f"updated persona: agent_id={agent_id} "
                          f"letta_id={existing_letta_id} "
                          f"(chars {old_len}→{new_len}, "
                          f"Δ={new_len - old_len:+d})")
                    updated_count += 1
            except Exception as e:
                print(f"  ERROR updating persona for {agent_id!r}: {e} "
                      f"-- continuing with other agents")
                skipped_count += 1

            # Sync LLM config knobs (temperature / max_output_tokens /
            # reasoning) — provider-shaped via model_settings. Only push
            # when at least one key in AGENT_CONFIG[agent_id] differs from
            # the live model_settings, so re-runs are idempotent.
            desired = AGENT_CONFIG.get(agent_id)
            if desired:
                try:
                    live = client.agents.retrieve(existing_letta_id).model_dump()
                    live_ms = live.get('model_settings') or {}
                    diffs = _model_settings_diff(live_ms, desired)
                    if not diffs:
                        print(f"config already in sync: agent_id={agent_id}")
                    else:
                        client.agents.update(
                            existing_letta_id, model_settings=desired,
                        )
                        diff_str = "; ".join(
                            f"{k}: {old!r}→{new!r}" for k, old, new in diffs
                        )
                        print(f"updated config: agent_id={agent_id} ({diff_str})")
                        updated_count += 1
                except Exception as e:
                    print(f"  ERROR syncing config for {agent_id!r}: {e} "
                          f"-- continuing")

            row = db.get_agent_registry_row(agent_id) or {}
            summary.append({
                'agent_id': agent_id,
                'letta_id': existing_letta_id,
                'model': row.get('model', '?'),
                'attached_block_count': 'unchanged',
            })
            continue

        prompt_path = spec['prompt_file']
        if not prompt_path.exists():
            sys.exit(f"ERROR: missing prompt file {prompt_path}")
        persona_value = prompt_path.read_text()

        memory_blocks = [
            {
                'label': 'persona',
                'value': persona_value,
                'limit': 8000,
            },
            {
                'label': 'self_model',
                'value': SELF_MODEL_SEED,
                'description': SELF_MODEL_DESC,
                'limit': 5000,
            },
        ]

        print(f"creating agent {agent_id!r} model={spec['model']} ...")
        agent_state = client.agents.create(
            name=agent_id,
            model=spec['model'],
            embedding=EMBEDDING,
            memory_blocks=memory_blocks,
            block_ids=all_shared_block_ids,
            tools=[],
            include_base_tools=True,
        )

        db.register_agent(
            agent_id=agent_id,
            letta_agent_id=agent_state.id,
            model=spec['model'],
            shared_world_block_id=world_id,
            # register_agent JSON-serialises lists transparently
            shared_peer_block_ids=peer_block_ids,
        )

        attached_count = len(memory_blocks) + len(all_shared_block_ids)
        print(f"  -> created letta_id={agent_state.id} "
              f"({len(memory_blocks)} owned + {len(all_shared_block_ids)} "
              f"shared = {attached_count} blocks)")
        summary.append({
            'agent_id': agent_id,
            'letta_id': agent_state.id,
            'model': spec['model'],
            'attached_block_count': attached_count,
        })
        created_count += 1

    # 4) Final summary
    print()
    print("=== summary ===")
    for s in summary:
        print(
            f"  {s['agent_id']:10s} -> {s['letta_id']}  "
            f"model={s['model']:36s}  blocks={s['attached_block_count']}"
        )
    print()
    print(f"created: {created_count}")
    print(f"updated: {updated_count}   (persona block diff detected and pushed)")
    print(f"skipped: {skipped_count}   (already up to date)")


if __name__ == "__main__":
    main()
