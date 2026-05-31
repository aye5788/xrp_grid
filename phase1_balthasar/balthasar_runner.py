"""Headless runner for the standalone Balthasar risk agent (Phase 1).

Decoupled from Letta. Loads the persona as the Anthropic system prompt, replays
the frozen scenarios through the live Anthropic Messages API with output forced
to the BalthasarR0 schema (via a single forced tool), grades each non-flagged
sample, and writes a timestamped results file.

Env vars:
  ANTHROPIC_API_KEY  - required; read by the anthropic SDK directly.
  BALTHASAR_MODEL    - model handle; defaults to claude-haiku-4-5 (production
                       ran Balthasar on Haiku). Not hardcoded in logic.

Grading:
  - position      : exact match vs ground truth (always graded).
  - geometry_veto : exact match, ONLY when the sample carries a ground-truth
                    geometry_veto (this dataset does not — so it is a no-op here
                    but the path is wired for future datasets).
  - conviction    : within +/-0.2 of ground truth, ONLY when present.
  - crux          : not auto-graded.
A sample passes iff every graded field passes. Aggregate accuracy is over
non-flagged samples only. Pass threshold: >= 0.70 (matches the suite gate).

Usage:
  python phase1_balthasar/balthasar_runner.py
"""

import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from phase1_balthasar.balthasar_schema import BalthasarR0

HERE = Path(__file__).resolve().parent
PERSONA_PATH = HERE / "balthasar_persona.md"
DATASET_PATH = HERE / "scenarios" / "dataset.jsonl"
RESULTS_DIR = HERE / "results"

DEFAULT_MODEL = "claude-haiku-4-5"
PASS_THRESHOLD = 0.70
CONVICTION_TOLERANCE = 0.2
TOOL_NAME = "emit_balthasar_vote"


def load_persona() -> str:
    return PERSONA_PATH.read_text()


def load_scenarios() -> list[dict]:
    scenarios = []
    with open(DATASET_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                scenarios.append(json.loads(line))
    return scenarios


def build_user_message(world_state: dict) -> str:
    """Construct the cycle prompt from a scenario's world_state.

    The output shape is enforced by the forced tool / BalthasarR0 schema, so the
    prompt only needs to present the data and ask for the risk decision.
    """
    ws_json = json.dumps(world_state, indent=2, sort_keys=True)
    return (
        "MAGI cycle — Round 0. Evaluate the current world_state below as "
        "Balthasar, the survival guardian, by walking your decision tree in "
        "order. Return your vote via the emit_balthasar_vote tool.\n\n"
        "world_state:\n"
        f"{ws_json}"
    )


def extract_ground_truth(sample: dict) -> dict:
    """Normalize ground_truth into {position, geometry_veto?, conviction?}.

    The dataset stores ground_truth as a bare position string; support a dict
    form too so future datasets can carry geometry_veto / conviction truth.
    """
    gt = sample.get("ground_truth")
    if isinstance(gt, str):
        return {"position": gt}
    if isinstance(gt, dict):
        return {
            "position": gt.get("position"),
            "geometry_veto": gt.get("geometry_veto"),
            "conviction": gt.get("conviction"),
        }
    return {"position": None}


def grade(out: BalthasarR0, gt: dict) -> dict:
    checks = {}
    checks["position"] = {
        "expected": gt.get("position"),
        "actual": out.position,
        "pass": out.position == gt.get("position"),
    }
    if gt.get("geometry_veto") is not None:
        checks["geometry_veto"] = {
            "expected": gt["geometry_veto"],
            "actual": out.geometry_veto,
            "pass": out.geometry_veto == gt["geometry_veto"],
        }
    if gt.get("conviction") is not None:
        within = abs(out.conviction - float(gt["conviction"])) <= CONVICTION_TOLERANCE
        checks["conviction"] = {
            "expected": gt["conviction"],
            "actual": out.conviction,
            "tolerance": CONVICTION_TOLERANCE,
            "pass": within,
        }
    sample_pass = all(c["pass"] for c in checks.values())
    return {"checks": checks, "pass": sample_pass}


def call_model(client, model: str, system: str, user_msg: str, tool_schema: dict):
    """Single forced-tool call. Returns (BalthasarR0, raw_message).

    Raises on API error or schema-validation failure; the caller logs and stops.
    """
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=0,
        system=system,
        tools=[
            {
                "name": TOOL_NAME,
                "description": "Emit Balthasar's Round-0 risk vote.",
                "input_schema": tool_schema,
            }
        ],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_msg}],
    )
    tool_block = next(
        (b for b in msg.content if getattr(b, "type", None) == "tool_use"), None
    )
    if tool_block is None:
        raise RuntimeError(
            "no tool_use block in response; content types="
            f"{[getattr(b, 'type', None) for b in msg.content]}"
        )
    out = BalthasarR0.model_validate(tool_block.input)
    return out, msg


def main() -> int:
    try:
        from anthropic import Anthropic
    except ImportError:
        print("ERROR: anthropic SDK not installed (pip install anthropic).")
        return 1

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        return 1

    model = os.environ.get("BALTHASAR_MODEL", DEFAULT_MODEL)
    persona = load_persona()
    scenarios = load_scenarios()
    tool_schema = BalthasarR0.model_json_schema()

    client = Anthropic()

    results = []
    graded = 0
    passed = 0

    for sample in scenarios:
        sid = sample.get("id")
        if sample.get("_drift_flag"):
            print(f"[skip] sample {sid}: drift-flagged — {sample['_drift_flag'][:80]}...")
            results.append({"id": sid, "skipped": True, "reason": "drift_flag"})
            continue

        world_state = sample.get("agent_args", {}).get("world_state", {})
        gt = extract_ground_truth(sample)
        user_msg = build_user_message(world_state)

        try:
            out, raw = call_model(client, model, persona, user_msg, tool_schema)
        except (ValidationError, Exception) as e:  # noqa: BLE001 - surface all
            print(f"ERROR on sample {sid}: {type(e).__name__}: {e}")
            raw_shape = None
            if "raw" in dir():
                try:
                    raw_shape = [getattr(b, "type", None) for b in raw.content]
                except Exception:
                    raw_shape = "unavailable"
            print(f"  raw response shape: {raw_shape}")
            traceback.print_exc()
            return 1

        g = grade(out, gt)
        graded += 1
        if g["pass"]:
            passed += 1
        verdict = "PASS" if g["pass"] else "FAIL"
        print(
            f"[{verdict}] sample {sid}: position={out.position} "
            f"(exp {gt['position']}), geometry_veto={out.geometry_veto}, "
            f"conviction={out.conviction:.2f}"
        )
        results.append(
            {
                "id": sid,
                "skipped": False,
                "tags": sample.get("tags"),
                "output": out.model_dump(),
                "ground_truth": gt,
                "grade": g,
            }
        )

    accuracy = (passed / graded) if graded else 0.0
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"run_{timestamp}.json"
    summary = {
        "timestamp_utc": timestamp,
        "model": model,
        "graded_samples": graded,
        "passed": passed,
        "accuracy": accuracy,
        "pass_threshold": PASS_THRESHOLD,
        "gate_pass": accuracy >= PASS_THRESHOLD,
        "results": results,
    }
    out_path.write_text(json.dumps(summary, indent=2))

    print()
    print(f"Model:    {model}")
    print(f"Graded:   {graded} non-flagged samples")
    print(f"Passed:   {passed}")
    print(f"Accuracy: {accuracy:.3f}  (threshold {PASS_THRESHOLD})  "
          f"-> {'GATE PASS' if accuracy >= PASS_THRESHOLD else 'GATE FAIL'}")
    print(f"Results:  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
