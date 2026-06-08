"""Standalone Balthasar (Risk/Survival guardian) seat backed by Claude Sonnet.

NOT wired into the live council. council.py is unchanged; this module is a
self-contained, testable wrapper that takes a world_state dict and returns a
validated `RiskVote`. Sibling to melchior_deepseek.py — same raw-Anthropic
forced-tool structure, adapted to Balthasar.

Two corrections vs. the phase-1 probe (phase1_balthasar/balthasar_runner.py),
honoring the live invariants:
  (a) validate against the LIVE `RiskVote` from schemas.py (risk_action /
      geometry_veto / conviction / key_evidence / crux), NOT the phase-1-local
      BalthasarR0.
  (b) build the tool input_schema via `schema_for_tool(RiskVote)`, NOT raw
      model_json_schema() — keep the "always schema_for_tool" rule even though
      Claude tolerates additionalProperties (schema_for_tool strips it centrally).

Mechanism: raw anthropic SDK messages.create with a single forced tool
(tool_choice={"type":"tool","name":...}), temperature=0. Persona -> system;
world_state -> user via the shared render_world_state. No `thinking` param — that
is a DeepSeek-v4-pro concern, not Claude. Validation retries ONCE with the error
fed back, mirroring Melchior (Claude does not hard-enforce the schema either).

Key: ANTHROPIC_API_KEY is read from the environment directly by the SDK (mirrors
melchior_deepseek.py — the caller/orchestrator is responsible for loading .env).
No tracing here (the orchestrator wraps this in a later stage).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from magi.agents.personas import load_persona
from magi.agents.schema_tools import schema_for_tool
from magi.agents.schemas import RiskVote
from magi.agents.world_state_render import render_world_state

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"
_TOOL_NAME = "risk_vote"

_PROMPT_PREFIX = (
    "MAGI cycle — Round 0. Evaluate the current world_state below as Balthasar, "
    "the survival guardian, by walking your decision tree in order. Return your "
    f"vote via the {_TOOL_NAME} tool.\n\nworld_state:\n"
)


def _extract_tool_input(response: Any) -> dict[str, Any]:
    """Pull the risk_vote tool_use block's `.input` out of an Anthropic response."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
            return block.input
    block_types = [getattr(b, "type", None) for b in response.content]
    raise ValueError(
        f"Claude response contained no '{_TOOL_NAME}' tool_use block; blocks={block_types}"
    )


def _build_messages(world_state: dict, extra_context: str | None) -> list[dict[str, Any]]:
    """Build Balthasar's user turn with an Anthropic prompt-cache breakpoint.

    The STABLE PREFIX is system(persona) + the world_state block; both repeat
    verbatim across Balthasar's two calls in a cycle (opening, then synthesis).
    We put a single ephemeral cache_control breakpoint on the LAST block of that
    prefix — the world_state user block — so tools + system + world_state are
    cached, and extra_context (the volatile openings/rebuttals transcript) is a
    SEPARATE block placed AFTER the breakpoint, never busting the cached prefix.

    TTL: the default 5-minute ephemeral TTL (no `ttl` field). Balthasar's opening
    and synthesis are seconds apart, well inside 5 min; the 1-hour TTL's 2x write
    premium would never pay at our ~1-convene/day cadence — see the handoff docs.

    Even with extra_context=None the content is a one-element BLOCK LIST carrying
    cache_control, so the request JSON differs from the pre-caching plain-string
    form. The model INPUT TEXT (_PROMPT_PREFIX + rendered world_state) is identical,
    so the vote is unchanged; only the caching metadata/shape is added.
    """
    stable_block = {
        "type": "text",
        "text": _PROMPT_PREFIX + render_world_state(world_state),
        "cache_control": {"type": "ephemeral"},
    }
    if extra_context:
        content: Any = [stable_block, {"type": "text", "text": extra_context}]
    else:
        content = [stable_block]
    return [{"role": "user", "content": content}]


def run_balthasar_with_meta(
    world_state: dict,
    persona: str | None = None,
    model: str = _DEFAULT_MODEL,
    extra_context: str | None = None,
) -> tuple[RiskVote, Any]:
    """Run the Balthasar seat and return (validated RiskVote, raw Anthropic response).

    Same logic as `run_balthasar`, but also surfaces the raw response so callers
    can inspect `response.model` / `response.usage` later. Mirrors
    run_melchior_with_meta's (vote, raw) shape.

    extra_context (council_v2 only): an extra instruction block (openings /
    rebuttals transcript + synthesis instruction) placed AFTER the cached stable
    prefix. The forced-tool risk_vote call, temperature, max_tokens and
    schema_for_tool(RiskVote) are unchanged.
    """
    api_key = os.environ["ANTHROPIC_API_KEY"]  # KeyError if unset; never logged
    client = anthropic.Anthropic(api_key=api_key)

    tool = {
        "name": _TOOL_NAME,
        "description": "Emit Balthasar's Round-0 survival/risk vote.",
        "input_schema": schema_for_tool(RiskVote),
    }
    system = persona if persona is not None else load_persona("balthasar")
    messages: list[dict[str, Any]] = _build_messages(world_state, extra_context)

    def _call(msgs: list[dict[str, Any]]) -> Any:
        return client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=msgs,
        )

    response = _call(messages)
    raw = _extract_tool_input(response)

    try:
        return RiskVote.model_validate(raw), response
    except Exception as first_err:
        # Claude does not hard-enforce the schema server-side either. Retry ONCE
        # with the bad output echoed back and the exact validation error stated.
        logger.warning("RiskVote validation failed; retrying once. error=%s", first_err)
        retry_messages = messages + [
            {"role": "assistant", "content": json.dumps(raw)},
            {
                "role": "user",
                "content": (
                    f"That risk_vote failed schema validation with error:\n{first_err}\n"
                    "Re-emit a valid risk_vote conforming to the schema: risk_action "
                    "in {CLEAR, PAUSE_LONGS, PAUSE_SHORTS, HALT}, geometry_veto in "
                    "{PROCEED, HOLD_GEOMETRY, RISK_BLOCK}, conviction a float 0.0-1.0, "
                    "key_evidence a list of short strings, crux one sentence."
                ),
            },
        ]
        response2 = _call(retry_messages)
        raw2 = _extract_tool_input(response2)
        try:
            return RiskVote.model_validate(raw2), response2
        except Exception as second_err:
            raise ValueError(
                "Balthasar Claude output failed RiskVote validation twice: "
                f"first={first_err!s}; retry={second_err!s}"
            ) from second_err


def run_balthasar(
    world_state: dict,
    persona: str | None = None,
    model: str = _DEFAULT_MODEL,
    extra_context: str | None = None,
) -> RiskVote:
    """Run the Balthasar (Risk/Survival) seat on Claude Sonnet; return a validated
    RiskVote.

    Standalone — not wired into council.py. Reads ANTHROPIC_API_KEY from the
    environment (never logged), builds the SAFE tool schema via
    schema_for_tool(RiskVote), forces the risk_vote tool, and validates the result
    with one feedback retry on failure. Defaults to the live balthasar.md persona.

    extra_context defaults to None. Note: even at None the request carries the
    ephemeral cache breakpoint (block-list form); the model input text is
    unchanged from the pre-caching path, so the vote is identical.
    """
    vote, _response = run_balthasar_with_meta(
        world_state, persona=persona, model=model, extra_context=extra_context
    )
    return vote
