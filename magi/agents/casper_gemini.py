"""Standalone Casper (Regime Classifier) seat backed by native gemini-2.5-flash.

NOT wired into the live council. council.py is unchanged; this module is a
self-contained, testable wrapper that takes a world_state dict and returns a
validated `RegimeVote`. Sibling to melchior_deepseek.py / balthasar_claude.py.

Mirrors the PROVEN native-Gemini path (optimize/casper/agent.py + the live
magi.council validated parse), NOT the loose forward-cases parse:

  * ADK LlmAgent with output_schema=RegimeVote passed DIRECTLY (native Gemini
    structured-output) — NOT schema_for_tool. RegimeVote is extra="ignore", so it
    emits no `additionalProperties`, which native Gemini 400s on. That is the
    proven Gemini path; do not route it through a forced tool.
  * include_contents="none" — stateless per cycle.
  * persona is the agent instruction; world_state goes in the user turn rendered
    via the shared render_world_state (pretty JSON).
  * 429 RESOURCE_EXHAUSTED is ridden out with ADK's client-side retry config.

Parse mirrors council._parse_native_vote: read the structured output from
session.state[output_key] (a dict) or json.loads the final response text (fences
stripped), then RegimeVote.model_validate(...).

Key: load_dotenv puts GOOGLE_API_KEY in the environment; the google-genai client
the ADK runtime uses reads it from there. No tracing here (the orchestrator wraps
this in a later stage).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Provider key (GOOGLE_API_KEY) for the native Gemini client. Mirrors
# optimize/casper/agent.py — load .env at import so a standalone run just works.
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")

from magi.agents.personas import load_persona
from magi.agents.schemas import RegimeVote
from magi.agents.world_state_render import render_world_state

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-2.5-flash"
_APP_NAME = "casper_standalone"
_USER_ID = "casper"
_OUTPUT_KEY = "casper_r0"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

_PROMPT_PREFIX = (
    "Classify the market regime for this cycle using your decision tree, then "
    "respond with your RegimeVote.\n\nworld_state:\n"
)


def _build_agent(persona: str, model: str):
    """Construct the stateless RegimeVote LlmAgent — identical wiring to the
    proven optimize/casper/agent.py root_agent (native Gemini, output_schema,
    include_contents='none', 429 retry)."""
    from google.adk.agents import LlmAgent
    from google.genai import types

    retry_cfg = types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=2, attempts=5),
        ),
    )
    return LlmAgent(
        name="casper",
        model=model,                          # native Gemini string handle
        instruction=persona,
        include_contents="none",
        output_schema=RegimeVote,             # passed DIRECTLY — no schema_for_tool
        output_key=_OUTPUT_KEY,
        generate_content_config=retry_cfg,
    )


async def _invoke_async(agent, message_text: str) -> tuple[Any, str | None, Any]:
    """Run the agent once over a fresh in-memory session. Returns
    (structured_state_value, final_response_text, final_event) — mirrors
    council._invoke_agent_async, plus the final event for usage/model capture."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    session_service = InMemorySessionService()
    session_id = "casper-standalone"
    await session_service.create_session(
        app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id,
    )
    runner = Runner(
        agent=agent, app_name=_APP_NAME, session_service=session_service,
    )
    content = types.Content(role="user", parts=[types.Part(text=message_text)])

    final_text = None
    final_event = None
    async for event in runner.run_async(
        user_id=_USER_ID, session_id=session_id, new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text
            final_event = event

    raw = None
    session = await session_service.get_session(
        app_name=_APP_NAME, user_id=_USER_ID, session_id=session_id,
    )
    if session is not None:
        raw = session.state.get(_OUTPUT_KEY)
    return raw, final_text, final_event


def _parse_validated(raw: Any, final_text: str | None) -> RegimeVote:
    """Validated parse — mirrors council._parse_native_vote. Prefer the structured
    output stored in session.state[output_key]; fall back to json.loads of the
    final text (code fences stripped). Validate against the real RegimeVote."""
    obj = None
    if isinstance(raw, dict):
        obj = raw
    elif isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except Exception:
            obj = None
    if obj is None and final_text:
        try:
            obj = json.loads(_FENCE.sub("", final_text).strip())
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        raise ValueError(
            f"Casper produced no parseable structured vote "
            f"(raw type={type(raw).__name__}, final_text={(final_text or '')[:120]!r})"
        )
    return RegimeVote.model_validate(obj)


def _build_message(world_state: dict, extra_context: str | None) -> str:
    """Compose Casper's single user turn: the proven prompt prefix + rendered
    world_state, then — only when supplied — extra_context as a SEPARATE trailing
    block AFTER the world_state. With extra_context=None the message is
    byte-identical to the proven standalone path (do not change that)."""
    msg = _PROMPT_PREFIX + render_world_state(world_state)
    if extra_context:
        msg = msg + "\n\n" + extra_context
    return msg


def run_casper_with_meta(
    world_state: dict,
    persona: str | None = None,
    model: str = _DEFAULT_MODEL,
    extra_context: str | None = None,
) -> tuple[RegimeVote, Any]:
    """Run the Casper seat and return (validated RegimeVote, raw final ADK event).

    Same logic as `run_casper`, but also surfaces the final ADK response event so
    callers can inspect usage_metadata / model later. Mirrors
    run_melchior_with_meta's (vote, raw) shape.

    extra_context (council_v2 only): an extra instruction block (predecessor
    context / rebuttal transcript) appended AFTER the world_state. None ->
    byte-identical to the proven standalone message.
    """
    instruction = persona if persona is not None else load_persona("casper")
    agent = _build_agent(instruction, model)
    raw, final_text, final_event = asyncio.run(
        _invoke_async(agent, _build_message(world_state, extra_context))
    )
    vote = _parse_validated(raw, final_text)
    return vote, final_event


def run_casper(
    world_state: dict,
    persona: str | None = None,
    model: str = _DEFAULT_MODEL,
    extra_context: str | None = None,
) -> RegimeVote:
    """Run the Casper (Regime Classifier) seat on native gemini-2.5-flash; return a
    validated RegimeVote.

    Standalone — not wired into council.py. Loads GOOGLE_API_KEY from the
    environment (via load_dotenv at import), builds the native-Gemini ADK agent
    with output_schema=RegimeVote (no schema_for_tool; extra="ignore" keeps the
    schema additionalProperties-free), runs it statelessly, and returns the
    validated regime vote. Defaults to the live casper.md persona.

    extra_context defaults to None (byte-identical to the proven standalone path).
    """
    vote, _event = run_casper_with_meta(
        world_state, persona=persona, model=model, extra_context=extra_context
    )
    return vote
