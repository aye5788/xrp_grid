"""
Custom extractor for MAGI council R0 responses.

The production R0 prompt asks each agent for a single-line JSON object:
    {"position": "...", "conviction": 0.x, "key_evidence": [...], "crux": "..."}

`r0_position` walks the trajectory, finds the last assistant_message, strips
markdown fences, parses the JSON, and returns the `position` field. Parse
failure returns "" so exact_match grades the sample as 0.0 and the failure
is visible in per-sample output rather than aborting the suite.
"""
import json
import re
from typing import List

from letta_evals import LettaMessageUnion
from letta_evals.decorators import extractor

# NB: do NOT add `from __future__ import annotations` here. letta-evals 0.15.0
# decorator validates `sig.return_annotation is not str` — under PEP 563
# string annotations the annotation becomes 'str' and the identity check fails
# with the noisy "must return str, got str" error.

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _assistant_texts(trajectory: List[List["LettaMessageUnion"]]) -> list:
    """Return every assistant_message text in trajectory order (turn, then
    msg index). Used to scan for the FIRST one that parses as the R0 JSON
    with a `position` field — the agent may emit additional chat-style
    assistant messages after a core_memory tool_call, and we must not let
    those clobber the structured vote."""
    texts: list = []
    for turn in trajectory or []:
        for msg in turn or []:
            # Letta returns these as either pydantic objects or plain dicts
            # depending on serialisation path. Handle both.
            if isinstance(msg, dict):
                mtype = msg.get("message_type") or msg.get("role")
                content = msg.get("content")
            else:
                mtype = getattr(msg, "message_type", None) or getattr(msg, "role", None)
                content = getattr(msg, "content", None)
            if mtype not in ("assistant_message", "assistant"):
                continue
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list) and content:
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        t = part.get("text")
                    else:
                        t = getattr(part, "text", None)
                    if t:
                        parts.append(t)
                if parts:
                    texts.append("\n".join(parts))
    return texts


def _parse_r0_json(text: str) -> dict | None:
    if not text:
        return None
    stripped = _FENCE_RE.sub("", text).strip()
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(stripped[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except (ValueError, TypeError):
            return None
    return None


@extractor
def r0_position(trajectory: List[List["LettaMessageUnion"]], config: dict) -> str:
    """Return the FIRST assistant message that parses as the R0 JSON object
    with a `position` field. Tolerates trailing chat-style assistant
    messages that the agent may emit after core_memory tool calls (Sonnet
    in particular does this consistently in eval runs)."""
    for text in _assistant_texts(trajectory):
        parsed = _parse_r0_json(text)
        if not parsed:
            continue
        pos = parsed.get("position")
        if isinstance(pos, str) and pos:
            return pos
    return ""
