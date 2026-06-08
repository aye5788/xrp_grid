"""Standalone Melchior (Grid Economist) seat backed by DeepSeek v4-pro.

NOT wired into the live council. council.py is unchanged; this module is a
self-contained, testable wrapper that takes a world_state dict and returns a
validated `GridVote`. It exists to prove DeepSeek can fill the Melchior seat
before any integration.

Two integration facts this wrapper bakes in (see the 2026-06-05 probe):

1. DeepSeek v4-pro defaults thinking ON server-side, and thinking mode is
   incompatible with a forced `tool_choice` (the API 400s:
   "Thinking mode does not support this tool_choice"). So we pass
   `thinking={"type": "disabled"}` on every call.

2. We build the tool input_schema with `schema_for_tool` (SAFE transforms only),
   NOT CrewAI's strict pipeline. CrewAI's `generate_model_description` forces every
   property into `required` and strips `null`, which would declare GridVote.geometry
   mandatory and break its conditional contract (geometry present iff
   verdict == RECONFIGURE). `schema_for_tool` preserves the real, optional/nullable
   contract.

DeepSeek does not hard-enforce schema `required`/conditional constraints server-side,
so `run_melchior` validates the returned vote with the real `GridVote` model and
retries ONCE with the validation error fed back if the first emission is invalid.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from magi.agents.schema_tools import schema_for_tool
from magi.agents.schemas import GridVote
from magi.agents.world_state_render import render_world_state

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"

_DEFAULT_PERSONA = (
    "You are Melchior, the grid economist on a three-agent crypto trading council. "
    "Given the market regime and grid economics in the world_state, judge whether a "
    "profitable grid configuration exists and emit your verdict via the grid_vote tool "
    "— only the RECONFIGURE verdict carries geometry."
)

_TOOL_NAME = "grid_vote"


def _warn_if_fallback(response: Any, model: str) -> None:
    """Guard against DeepSeek's silent unrecognized-model -> v4-flash fallback."""
    billed = str(getattr(response, "model", ""))
    if not billed.startswith("deepseek-v4-pro"):
        logger.warning(
            "DeepSeek billed model %r, expected deepseek-v4-pro (silent fallback?); "
            "requested=%r",
            billed,
            model,
        )


def _extract_tool_input(response: Any) -> dict[str, Any]:
    """Pull the grid_vote tool_use block's `.input` out of an Anthropic-format response."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
            return block.input
    block_types = [getattr(b, "type", None) for b in response.content]
    raise ValueError(
        f"DeepSeek response contained no '{_TOOL_NAME}' tool_use block; blocks={block_types}"
    )


def _build_user_content(world_state: dict, extra_context: str | None) -> Any:
    """Build Melchior's first user-turn content. With extra_context=None this is
    the proven plain-string ("world_state:\\n" + render) — byte-identical to today.
    With extra_context set, it becomes a two-block list whose FIRST block is the
    identical world_state text and whose SECOND block is extra_context — placed
    AFTER the world_state so DeepSeek's automatic prefix cache (system + the
    world_state block) is never busted by the volatile trailing context."""
    ws_block = "world_state:\n" + render_world_state(world_state)
    if extra_context:
        return [
            {"type": "text", "text": ws_block},
            {"type": "text", "text": extra_context},
        ]
    return ws_block


def run_melchior_with_meta(
    world_state: dict,
    persona: str | None = None,
    model: str = "deepseek-v4-pro",
    extra_context: str | None = None,
) -> tuple[GridVote, Any]:
    """Run the Melchior seat and return (validated GridVote, raw Anthropic response).

    Same logic as `run_melchior`, but also surfaces the raw response so callers can
    inspect `response.model` / `response.usage` (cost, token accounting). Public
    callers should prefer `run_melchior`; this variant exists for tests/observability.

    extra_context (council_v2 only): an extra instruction block (regime premise /
    rebuttal transcript) placed AFTER the world_state block. No cache_control here —
    DeepSeek caches the stable prefix automatically; we only keep that prefix
    stable. None -> byte-identical to the proven standalone request.
    """
    api_key = os.environ["DEEPSEEK_API_KEY"]  # KeyError if unset; never logged
    client = anthropic.Anthropic(api_key=api_key, base_url=DEEPSEEK_BASE_URL)

    tool = {
        "name": _TOOL_NAME,
        "description": "Emit the grid economic verdict.",
        "input_schema": schema_for_tool(GridVote),
    }
    system = persona if persona is not None else _DEFAULT_PERSONA
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _build_user_content(world_state, extra_context)},
    ]

    def _call(msgs: list[dict[str, Any]]) -> Any:
        return client.messages.create(
            model=model,
            max_tokens=1024,
            temperature=0,
            thinking={"type": "disabled"},  # REQUIRED: v4-pro defaults thinking ON
            system=system,
            tools=[tool],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=msgs,
        )

    response = _call(messages)
    _warn_if_fallback(response, model)
    raw = _extract_tool_input(response)

    try:
        return GridVote.model_validate(raw), response
    except Exception as first_err:
        # DeepSeek does not hard-enforce schema constraints server-side. Retry ONCE
        # with the bad output echoed back and the exact validation error stated.
        logger.warning("GridVote validation failed; retrying once. error=%s", first_err)
        retry_messages = messages + [
            {"role": "assistant", "content": json.dumps(raw)},
            {
                "role": "user",
                "content": (
                    f"That grid_vote failed schema validation with error:\n{first_err}\n"
                    "Re-emit a valid grid_vote. Remember: geometry must be present ONLY "
                    "when verdict is RECONFIGURE, and must be omitted otherwise."
                ),
            },
        ]
        response2 = _call(retry_messages)
        _warn_if_fallback(response2, model)
        raw2 = _extract_tool_input(response2)
        try:
            return GridVote.model_validate(raw2), response2
        except Exception as second_err:
            raise ValueError(
                "Melchior DeepSeek output failed GridVote validation twice: "
                f"first={first_err!s}; retry={second_err!s}"
            ) from second_err


def run_melchior(
    world_state: dict,
    persona: str | None = None,
    model: str = "deepseek-v4-pro",
    extra_context: str | None = None,
) -> GridVote:
    """Run the Melchior (Grid Economist) seat on DeepSeek; return a validated GridVote.

    Standalone — not wired into council.py. Reads DEEPSEEK_API_KEY from the
    environment (never logged), builds the SAFE tool schema (preserving the
    conditional GridVote.geometry contract), disables thinking (v4-pro defaults it
    ON, which 400s with a forced tool_choice), forces the grid_vote tool, and
    validates the result with one feedback retry on failure.

    extra_context defaults to None (byte-identical to the proven standalone request).
    """
    vote, _response = run_melchior_with_meta(
        world_state, persona=persona, model=model, extra_context=extra_context
    )
    return vote
