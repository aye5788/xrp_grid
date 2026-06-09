"""
magi/validate_schema.py — standalone CLI verification tool.

Usage:
  python -m magi.validate_schema

Returns exit 0 on PASS (no errors and no warnings), exit 1 on any ERROR,
exit 2 on internal failure (e.g. cannot import build_world_state).

Two checks performed:
  1. Schema vs build_world_state() runtime output — does every declared
     field actually appear in the snapshot, and vice versa.
  2. Schema vs each persona's hand-authored body — does every persona
     reference resolve to a declared path where the agent is a consumer.

This module owns no validation logic of its own. All logic lives in
magi/world_state_schema.py. This module is a CLI wrapper.

Callable from:
  - command line (operator + CI)
  - magi/provision_agents.py (programmatically, refuses to push on ERROR)
  - pre-commit hooks
"""

import sys

from magi.world_state_schema import (
    AGENTS,
    load_persona,
    validate_persona_references,
    validate_runtime_output,
    FIELDS,
)


def _print_separator():
    print("-" * 72)


def check_runtime_output() -> dict:
    """Build a snapshot of world_state and validate it against the schema.

    Returns the validate_runtime_output() result dict. If the import or
    call fails, prints the exception and returns a synthetic error result.
    """
    try:
        from magi.orchestrator import build_world_state
    except Exception as e:
        print(f"[FATAL] cannot import build_world_state: {e!r}")
        return {
            "missing_from_runtime": [],
            "undeclared_in_schema": [],
            "ok": False,
            "_internal_error": str(e),
        }

    try:
        ws = build_world_state()
    except Exception as e:
        print(f"[FATAL] build_world_state() raised: {e!r}")
        return {
            "missing_from_runtime": [],
            "undeclared_in_schema": [],
            "ok": False,
            "_internal_error": str(e),
        }

    return validate_runtime_output(ws)


def check_personas() -> dict:
    """Validate every persona body against the schema.

    Returns dict {agent_id: per-agent result}.
    """
    out = {}
    for agent_id in AGENTS:
        try:
            text = load_persona(agent_id)
        except Exception as e:
            out[agent_id] = {
                "errors": [{"token": "<persona-load>", "reason": str(e)}],
                "warnings": [],
                "ok": False,
            }
            continue
        out[agent_id] = validate_persona_references(text, agent_id)
    return out


def main() -> int:
    print(f"magi/validate_schema.py — checking {len(FIELDS)} declared fields\n")

    # ----- Runtime output check -----
    print("== Runtime output vs schema ==")
    runtime = check_runtime_output()
    if runtime.get("_internal_error"):
        return 2
    if runtime["ok"]:
        print("[OK]  build_world_state output matches schema "
              f"({len(FIELDS)} declared paths)")
    else:
        for p in runtime["missing_from_runtime"]:
            print(f"[ERR] declared in schema but missing from runtime output: {p}")
        for p in runtime["undeclared_in_schema"]:
            print(f"[ERR] present in runtime output but undeclared in schema: {p}")
    _print_separator()

    # ----- Persona checks -----
    print("== Personas vs schema ==")
    persona_results = check_personas()
    persona_ok = True
    persona_warn_count = 0
    for agent_id, result in persona_results.items():
        if result["ok"] and not result["warnings"]:
            print(f"[OK]  {agent_id} persona — all references resolve, "
                  f"no orphan consumer declarations")
        else:
            for err in result["errors"]:
                print(f"[ERR] {agent_id} persona — {err['reason']}")
            for warn in result["warnings"]:
                print(f"[WARN] {agent_id} persona — {warn['reason']}")
                persona_warn_count += 1
            if not result["ok"]:
                persona_ok = False
        # Low-severity prose-token notes — bare snake_case tokens that resolve to
        # no schema leaf (treated as prose, e.g. melchior.md's `current_price`).
        # Surfaced for visibility; NEVER counted toward errors/warns or the exit.
        for note in result.get("notes", []):
            print(f"[NOTE] {agent_id} persona — {note['reason']}")
    _print_separator()

    # ----- Summary -----
    runtime_errors = (
        len(runtime["missing_from_runtime"])
        + len(runtime["undeclared_in_schema"])
    )
    persona_errors = sum(len(r["errors"]) for r in persona_results.values())
    total_errors = runtime_errors + persona_errors

    if total_errors == 0 and persona_warn_count == 0:
        print("SUMMARY: 0 ERROR, 0 WARN — PASS")
        return 0
    if total_errors == 0:
        print(f"SUMMARY: 0 ERROR, {persona_warn_count} WARN — PASS (warnings only)")
        return 0
    print(f"SUMMARY: {total_errors} ERROR, {persona_warn_count} WARN — FAIL")
    return 1


if __name__ == "__main__":
    from magi import adam
    adam.init_oneshot("validate_schema")
    sys.exit(main())
