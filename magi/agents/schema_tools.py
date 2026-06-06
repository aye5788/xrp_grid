from __future__ import annotations
from copy import deepcopy
from typing import Any
from pydantic import BaseModel

# Vendored verbatim from CrewAI's pydantic_schema_utils.py — the SAFE half only.
OPENAI_SUPPORTED_FORMATS = {"date-time", "date", "time", "duration"}

def resolve_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline all local $refs using $defs. Pure; cycle-guarded."""
    defs = schema.get("$defs", {})
    schema_copy = deepcopy(schema)
    expanding: set[str] = set()

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                def_name = ref.replace("#/$defs/", "")
                if def_name not in defs:
                    raise KeyError(f"Definition '{def_name}' not found in $defs.")
                if def_name in expanding:
                    def_schema = defs[def_name]
                    stub: dict[str, Any] = {"type": def_schema.get("type", "object")}
                    if "description" in def_schema:
                        stub["description"] = def_schema["description"]
                    return stub
                expanding.add(def_name)
                try:
                    return _resolve(deepcopy(defs[def_name]))
                finally:
                    expanding.discard(def_name)
            return {k: _resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_resolve(i) for i in node]
        return node

    return _resolve(schema_copy)

def strip_unsupported_formats(d: Any, _seen: set[int] | None = None) -> Any:
    """Remove format hints outside the supported set. Defensive; no-op for plain types."""
    if _seen is None:
        _seen = set()
    if isinstance(d, dict):
        if id(d) in _seen:
            return d
        _seen.add(id(d))
        fmt = d.get("format")
        if isinstance(fmt, str) and fmt not in OPENAI_SUPPORTED_FORMATS:
            del d["format"]
        for v in d.values():
            strip_unsupported_formats(v, _seen)
    elif isinstance(d, list):
        if id(d) in _seen:
            return d
        _seen.add(id(d))
        for i in d:
            strip_unsupported_formats(i, _seen)
    return d

def strip_additional_properties(d: Any, _seen: set[int] | None = None) -> Any:
    """Recursively remove EVERY "additionalProperties" key at every nesting level
    (top object, nested objects, anyOf/oneOf branches, array items, $defs-inlined
    sub-objects). Returns the same structure mutated in place.

    Gemini's response_schema proto has no `additional_properties` field, so ANY
    schema carrying "additionalProperties" 400s (INVALID_ARGUMENT) regardless of its
    value (false from extra="forbid", or a dict for `dict[str, X]` models). Stripping
    it centrally here makes that 400 structurally impossible to reach a vendor: a
    model's `extra=` setting can no longer re-arm it. Touches ONLY this one key —
    required / optional / nullable / enum / items / anyOf structure is left intact,
    so conditional contracts (e.g. GridVote's geometry-iff-RECONFIGURE) survive."""
    if _seen is None:
        _seen = set()
    if isinstance(d, dict):
        if id(d) in _seen:
            return d
        _seen.add(id(d))
        d.pop("additionalProperties", None)
        for v in d.values():
            strip_additional_properties(v, _seen)
    elif isinstance(d, list):
        if id(d) in _seen:
            return d
        _seen.add(id(d))
        for i in d:
            strip_additional_properties(i, _seen)
    return d

def schema_for_tool(model: type[BaseModel]) -> dict[str, Any]:
    """Native Pydantic schema -> tool input_schema, SAFE transforms only.
    Preserves optional/nullable fields (e.g. conditional GridVote.geometry) so the
    vendor sees the real contract, not a strict-mode rewrite.

    The final strip_additional_properties pass is a structural guard against the
    Gemini `additionalProperties` 400 — it removes the key no matter which model's
    `extra=` setting emitted it, so no individual schema has to remember to use
    extra="ignore" to stay clear."""
    s = model.model_json_schema()
    s = resolve_refs(s)
    s.pop("$defs", None)
    s = strip_unsupported_formats(s)
    s = strip_additional_properties(s)
    return s
