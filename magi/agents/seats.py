"""seats.py — SYMMETRIC seat callers for the blind-review council.

The three seats are EQUALS (governing principle P1): no arbiter, no privileged
seat, none that sees more or decides more than the others. This module enforces
that symmetry at the call boundary. Every seat, in both phases, gets the IDENTICAL
scaffold — its persona as a reasoning LENS only, the rendered world_state, and the
shared council-ledger block — and NO peer context in Phase 1. The only thing that
differs across seats is the persona text (the architectural-diversity lens) and the
vendor transport underneath; the task framing and the structured output are shared.

Two public verbs, used symmetrically for all three seats:
  * propose(seat, ...) -> CandidateDecision   (Phase 1: isolated proposal; Phase R:
                                               reconciliation re-proposal)
  * review(seat, ...)  -> Ranking             (Phase 2: anonymized cross-review)

Transports (kept where each seat's proven wiring lives):
  * casper    — native gemini-2.5-flash via ADK output_schema (extra="ignore" keeps
                the emitted response_schema free of additionalProperties, which
                native Gemini 400s on).
  * melchior  — deepseek-v4-pro via the Anthropic-compat endpoint, forced tool,
                thinking DISABLED (v4-pro defaults it ON, which 400s under a forced
                tool_choice), schema built with schema_for_tool.
  * balthasar — claude-haiku-4-5 (cost-matched: there is no synthesizer role, so the
                premium tier is not justified), forced tool, schema_for_tool.

Each call validates the structured output against the real pydantic model and
retries ONCE with the validation error fed back (the vendors do not hard-enforce
conditional constraints — e.g. CandidateDecision geometry-iff-RECONFIGURE — server
side). A second failure raises; run_council catches it and treats the seat as a
non-responder (no fabricated vote).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from magi.agents.personas import load_persona
from magi.agents.schema_tools import schema_for_tool
from magi.agents.schemas import CandidateDecision, Ranking
from magi.agents.world_state_render import render_world_state

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")

logger = logging.getLogger(__name__)

# Cost-matched lineup (operator-decided at the redesign): Casper native Gemini,
# Melchior DeepSeek, Balthasar dropped from claude-sonnet-4-6 to claude-haiku-4-5
# — with no arbiter/synthesizer seat the premium tier is not justified.
MODELS = {
    "casper": "gemini-2.5-flash",
    "melchior": "deepseek-v4-pro",
    "balthasar": "claude-haiku-4-5",
}
VENDORS = {"casper": "google", "melchior": "deepseek", "balthasar": "anthropic"}

_DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# --- shared task framing (identical across the three seats) ---

_ACTION_SPACE = (
    "MAINTAIN (keep the live grid as-is), RECONFIGURE (rebuild to a better geometry "
    "— REQUIRES geometry: target_spacing_pct and target_levels), PAUSE_LONGS / "
    "PAUSE_SHORTS (hold one side off), STAND_ASIDE (structural downtrend / "
    "capital-erosion risk — cancel buys, work inventory off), HALT (stand the grid "
    "down entirely)"
)

_PROPOSE_TASK = (
    "=== YOUR TASK — INDEPENDENT PROPOSAL ===\n"
    "You are one of THREE EQUAL members of this trading council. Reading the "
    "world_state above through your own judgment, decide the SINGLE best action over "
    "the shared action space: " + _ACTION_SPACE + ".\n"
    "The market regime is an INPUT you read from the world_state signals (trend "
    "stack, ADX, volatility regime, momentum, tape verdict) — it is NOT something "
    "you output; commit directly to an action. Emit ONE candidate_decision: the "
    "action, geometry ONLY if RECONFIGURE, 3-5 key_evidence citations of specific "
    "world_state values, a one-sentence rationale, and your conviction (0-1, "
    "RECORDED but never vote-weighted). You will NOT see the other seats' proposals."
)

_RECONCILE_PREFIX = (
    "=== RECONCILIATION ROUND ===\n"
    "The council did NOT converge on a winner. Below are the anonymized proposals "
    "from this cycle (one of them is your own — authorship is hidden). The split is "
    "real; reconsider on the merits and RE-EMIT your candidate_decision — revise it "
    "if the other positions genuinely move you, or hold your action with a sharper "
    "rationale. No seat's proposal is privileged.\n\n"
)

_REVIEW_TASK_HEAD = (
    "=== YOUR TASK — BLIND CROSS-REVIEW ===\n"
    "Below are the council's anonymized candidate decisions, labeled A/B/C (one may "
    "be your own — you cannot tell which, and none is privileged). Rank them BEST to "
    "WORST for the current world_state. Emit one ranking: `order` lists every label "
    "exactly once best->worst, and `why` gives a one-line justification per position "
    "(why[i] explains order[i]).\n\n"
)


def _scaffold_body(world_state: dict, ledger_block: str | None, task_block: str) -> str:
    """The IDENTICAL scaffold body handed to every seat: rendered world_state, the
    shared council-ledger block (same text for all three), then the task framing."""
    parts = ["world_state:\n" + render_world_state(world_state)]
    if ledger_block:
        parts.append("=== COUNCIL LEDGER (the council's own recent decisions + "
                     "outcomes — shared, authorship-free) ===\n" + ledger_block)
    parts.append(task_block)
    return "\n\n".join(parts)


# --- vendor transports: (persona, body, schema, tool_name) -> (validated, raw) ---

def _retry_note(model_name: str, err: Any) -> str:
    return (f"That {model_name} failed schema validation with error:\n{err}\n"
            "Re-emit a valid one conforming to the schema. For a candidate_decision, "
            "geometry must be present ONLY when action is RECONFIGURE and omitted "
            "otherwise; conviction is a float 0-1.")


def _call_gemini(persona: str, body: str, schema: type, output_key: str) -> tuple[Any, Any]:
    """Native-Gemini structured-output transport (Casper). output_schema passed
    DIRECTLY — schema is extra="ignore", so it emits no additionalProperties. One
    validation retry with the error appended to a fresh stateless turn."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    retry_cfg = types.GenerateContentConfig(
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(initial_delay=2, attempts=5)))
    agent = LlmAgent(
        name="seat", model=MODELS["casper"], instruction=persona,
        include_contents="none", output_schema=schema, output_key=output_key,
        generate_content_config=retry_cfg)

    async def _invoke(message_text: str) -> tuple[Any, str | None, Any]:
        svc = InMemorySessionService()
        sid = "seat-blind"
        await svc.create_session(app_name="seat_blind", user_id="seat", session_id=sid)
        runner = Runner(agent=agent, app_name="seat_blind", session_service=svc)
        content = types.Content(role="user", parts=[types.Part(text=message_text)])
        final_text, final_event = None, None
        async for event in runner.run_async(user_id="seat", session_id=sid, new_message=content):
            if event.is_final_response() and event.content and event.content.parts:
                final_text = event.content.parts[0].text
                final_event = event
        session = await svc.get_session(app_name="seat_blind", user_id="seat", session_id=sid)
        raw = session.state.get(output_key) if session is not None else None
        return raw, final_text, final_event

    def _parse(raw: Any, final_text: str | None) -> Any:
        obj = raw if isinstance(raw, dict) else None
        if obj is None and isinstance(raw, str):
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
            raise ValueError(f"Casper produced no parseable structured output "
                             f"(final_text={(final_text or '')[:120]!r})")
        return schema.model_validate(obj)

    raw, final_text, final_event = asyncio.run(_invoke(body))
    try:
        return _parse(raw, final_text), final_event
    except Exception as first_err:
        logger.warning("Casper %s validation failed; retrying once. error=%s",
                       schema.__name__, first_err)
        raw2, final_text2, final_event2 = asyncio.run(
            _invoke(body + "\n\n" + _retry_note(schema.__name__, first_err)))
        return _parse(raw2, final_text2), final_event2


def _call_anthropic(persona: str, body: str, schema: type, tool_name: str,
                    model: str, base_url: str | None, thinking_off: bool) -> tuple[Any, Any]:
    """Anthropic-format forced-tool transport (Melchior via DeepSeek, Balthasar via
    Claude). schema_for_tool strips additionalProperties centrally. One validation
    retry with the bad output echoed back and the error stated."""
    import anthropic  # lazy: the dev/test path can import seats without the SDK
    key_env = "DEEPSEEK_API_KEY" if base_url else "ANTHROPIC_API_KEY"
    client = anthropic.Anthropic(api_key=os.environ[key_env], base_url=base_url) \
        if base_url else anthropic.Anthropic(api_key=os.environ[key_env])
    tool = {"name": tool_name, "description": f"Emit the {tool_name}.",
            "input_schema": schema_for_tool(schema)}
    messages: list[dict[str, Any]] = [{"role": "user", "content": body}]

    def _call(msgs: list[dict[str, Any]]) -> Any:
        kwargs: dict[str, Any] = dict(
            model=model, max_tokens=1024, temperature=0, system=persona,
            tools=[tool], tool_choice={"type": "tool", "name": tool_name}, messages=msgs)
        if thinking_off:
            kwargs["thinking"] = {"type": "disabled"}  # REQUIRED for deepseek-v4-pro
        return client.messages.create(**kwargs)

    def _extract(response: Any) -> dict:
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                return block.input
        raise ValueError(f"response contained no '{tool_name}' tool_use block")

    response = _call(messages)
    raw = _extract(response)
    try:
        return schema.model_validate(raw), response
    except Exception as first_err:
        logger.warning("%s %s validation failed; retrying once. error=%s",
                       tool_name, schema.__name__, first_err)
        retry_messages = messages + [
            {"role": "assistant", "content": json.dumps(raw)},
            {"role": "user", "content": _retry_note(schema.__name__, first_err)}]
        response2 = _call(retry_messages)
        raw2 = _extract(response2)
        return schema.model_validate(raw2), response2


def _run_seat(seat: str, body: str, schema: type, tool_name: str) -> tuple[Any, Any]:
    """Dispatch one seat's structured call to its vendor transport. Returns
    (validated_object, raw_response/event)."""
    persona = load_persona(seat)
    if seat == "casper":
        return _call_gemini(persona, body, schema, output_key="seat_out")
    if seat == "melchior":
        return _call_anthropic(persona, body, schema, tool_name,
                               model=MODELS["melchior"], base_url=_DEEPSEEK_BASE_URL,
                               thinking_off=True)
    if seat == "balthasar":
        return _call_anthropic(persona, body, schema, tool_name,
                               model=MODELS["balthasar"], base_url=None,
                               thinking_off=False)
    raise ValueError(f"unknown seat {seat!r}")


# --- public verbs ---

def propose(seat: str, world_state: dict, ledger_block: str | None,
            reconcile_block: str | None = None) -> tuple[CandidateDecision, Any]:
    """Phase 1 (and reconciliation) isolated proposal. Returns (CandidateDecision, raw).

    `reconcile_block`: when set (reconciliation round), the anonymized split is shown
    after the task framing and the seat is asked to RE-EMIT (revise or hold). The
    block carries no authorship; the scaffold is otherwise identical to Phase 1."""
    task = _PROPOSE_TASK
    if reconcile_block:
        task = task + "\n\n" + _RECONCILE_PREFIX + reconcile_block
    body = _scaffold_body(world_state, ledger_block, task)
    return _run_seat(seat, body, CandidateDecision, "candidate_decision")


def review(seat: str, world_state: dict, anon_block: str,
           ledger_block: str | None) -> tuple[Ranking, Any]:
    """Phase 2 anonymized cross-review. `anon_block` is the labeled A/B/C candidate
    set, identical for every seat. Returns (Ranking, raw)."""
    task = _REVIEW_TASK_HEAD + anon_block
    body = _scaffold_body(world_state, ledger_block, task)
    return _run_seat(seat, body, Ranking, "ranking")
