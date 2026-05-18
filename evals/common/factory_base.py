"""
Shared agent-factory for MAGI Option A persona regression.

Per sample: spins up a throwaway Letta agent in the magi-evals project with
the current persona file, a snapshot of the live self_model, and the
sample's synthetic world_state JSON. Returns the agent ID.

No production state is read or written here at runtime — self_models are
loaded from snapshot files on disk (snapshotted by run_all.sh before each
suite kicks off). The production magi project is never touched.

Cleanup of stale eval agents runs in run_all.sh BEFORE any new agent is
created (per operator decision), not inside the factory.
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from letta_client import AsyncLetta
from letta_evals.decorators import agent_factory
from letta_evals.models import Sample

# NB: do NOT add `from __future__ import annotations` here. letta-evals 0.15.0
# validates `sig.return_annotation is not str` on decorated functions — under
# PEP 563 string annotations the check fails (annotation is the string 'str',
# not the type).


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_REPO_ROOT / ".env")

# Per-agent model handles — base prefix, billed against Letta credits.
# Mirrors the live mapping in magi/provision_agents.py:AGENT_SPECS.
MODEL_HANDLES = {
    "casper":    "google_ai/gemini-3-flash-preview",
    "melchior":  "openai/gpt-4o",
    "balthasar": "anthropic/claude-sonnet-4-6",
}

# Per-agent LLM model_settings — mirrors magi/provision_agents.py:AGENT_CONFIG
# so eval agents respond under the same knobs as production. Provider-side
# asymmetries (GPT-4o has no native extended-thinking budget) match production.
MODEL_SETTINGS = {
    "casper": {
        "provider_type": "google_ai",
        "temperature": 0.3,
        "max_output_tokens": 8192,
        "thinking_config": {"include_thoughts": True, "thinking_budget": 2048},
    },
    "melchior": {
        "provider_type": "openai",
        "temperature": 0.3,
        "max_output_tokens": 8192,
        "reasoning": {"reasoning_effort": "medium"},
        "strict": False,
    },
    "balthasar": {
        "provider_type": "anthropic",
        "temperature": 0.3,
        "max_output_tokens": 8192,
        "thinking": {"type": "enabled", "budget_tokens": 2048},
        "effort": "medium",
        "strict": False,
    },
}

EMBEDDING = "letta/letta-free"

PROMPT_DIR = _REPO_ROOT / "magi" / "prompts"
SNAPSHOT_DIR = _REPO_ROOT / "evals" / "self_model_snapshots"

# RUN_ID is set by run_all.sh so cleanup can group agents by cohort. When
# letta-evals is invoked directly (no wrapper), each invocation gets its own
# UUID-based run_id; cleanup still works, just with finer granularity.
RUN_ID = os.environ.get("MAGI_EVAL_RUN_ID") or f"adhoc-{uuid.uuid4().hex[:8]}"


def _load_persona(agent_name: str) -> str:
    path = PROMPT_DIR / f"{agent_name}_prompt.txt"
    if not path.exists():
        sys.exit(f"ERROR: persona file missing: {path}")
    return path.read_text()


def _load_self_model(agent_name: str) -> str:
    path = SNAPSHOT_DIR / f"{agent_name}.txt"
    if not path.exists():
        sys.exit(
            f"ERROR: self_model snapshot missing: {path} — "
            f"run evals/run_all.sh which snapshots before suite execution"
        )
    return path.read_text()


def _eval_project_id() -> str:
    pid = os.environ.get("LETTA_EVALS_PROJECT_ID", "").strip()
    if not pid:
        sys.exit(
            "ERROR: LETTA_EVALS_PROJECT_ID not set in /root/xrp_grid/.env. "
            "Create the 'magi-evals' project via Letta Cloud web UI "
            "(Settings → Projects → New), copy the project ID, and set it "
            "before running any eval."
        )
    return pid


async def create_eval_agent_for(
    client: AsyncLetta,
    sample: Sample,
    agent_name: str,
) -> str:
    """Per-sample agent creation for a specific MAGI agent persona."""
    if agent_name not in MODEL_HANDLES:
        sys.exit(f"ERROR: unknown agent_name {agent_name!r}")

    world_state = (sample.agent_args or {}).get("world_state")
    if world_state is None:
        sys.exit(
            f"ERROR: sample {sample.id} missing agent_args.world_state"
        )

    persona_value = _load_persona(agent_name)
    self_model_value = _load_self_model(agent_name)
    world_state_value = json.dumps(world_state, indent=2, default=str)

    memory_blocks = [
        {"label": "persona",     "value": persona_value,     "limit": 8000},
        {"label": "self_model",  "value": self_model_value,  "limit": 5000},
        {"label": "world_state", "value": world_state_value, "limit": 15000},
    ]

    agent_label = (
        f"eval-{agent_name}-{RUN_ID}-{sample.id}-{int(time.time())}"
    )

    # Pass the project SLUG via `project=` — Letta SDK 1.11.0 accepts
    # name/slug here and resolves to the real project-XXX UUID server-side.
    # `project_id=` requires the UUID itself and returns 500 on a slug.
    agent = await client.agents.create(
        name=agent_label,
        model=MODEL_HANDLES[agent_name],
        embedding=EMBEDDING,
        memory_blocks=memory_blocks,
        model_settings=MODEL_SETTINGS[agent_name],
        tools=[],
        include_base_tools=True,
        project=_eval_project_id(),
    )
    return agent.id


# Per-agent thin wrappers — the suite YAML references the matching wrapper.
# Each is its own @agent_factory so the framework can introspect signatures
# cleanly and import paths are unambiguous.

@agent_factory
async def create_casper(client: AsyncLetta, sample: Sample) -> str:
    return await create_eval_agent_for(client, sample, "casper")


@agent_factory
async def create_melchior(client: AsyncLetta, sample: Sample) -> str:
    return await create_eval_agent_for(client, sample, "melchior")


@agent_factory
async def create_balthasar(client: AsyncLetta, sample: Sample) -> str:
    return await create_eval_agent_for(client, sample, "balthasar")
