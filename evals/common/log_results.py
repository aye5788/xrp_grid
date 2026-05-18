"""
Parse a letta-evals --output directory and INSERT a magi_eval_runs row.

Usage:
    python -m common.log_results <agent_id> <output_dir> [--threshold 0.70]

Reads <output_dir>/summary.json + results.jsonl, sums per-sample cost,
counts passing samples per the gate threshold, writes one row.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# Use the MAIN MAGI venv's database.py (it imports the live observer.db).
sys.path.insert(0, str(REPO_ROOT))

from database import insert_eval_run  # noqa: E402


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return out[:12] if out else None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_id")
    ap.add_argument("output_dir")
    ap.add_argument("--threshold", type=float, default=0.70)
    args = ap.parse_args()

    out = Path(args.output_dir)
    summary_path = out / "summary.json"
    results_path = out / "results.jsonl"
    if not summary_path.exists() or not results_path.exists():
        print(f"ERROR: expected summary.json and results.jsonl in {out}",
              file=sys.stderr)
        return 1

    summary = json.loads(summary_path.read_text())
    gates_passed = bool(summary.get("gates_passed"))

    total = 0
    passed = 0
    cost = 0.0
    header_suite_name = None
    with results_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("type") == "config":
                header_suite_name = (
                    (obj.get("config") or {}).get("name")
                    or header_suite_name
                )
                continue
            if obj.get("type") != "result":
                continue
            r = obj.get("result") or {}
            total += 1
            grade = (r.get("grade") or {}).get("score")
            if grade is None:
                grades = r.get("grades") or []
                if grades:
                    grade = grades[0].get("score")
            if grade is not None and float(grade) >= 1.0:
                passed += 1
            sample_cost = r.get("cost")
            if sample_cost is not None:
                try:
                    cost += float(sample_cost)
                except (TypeError, ValueError):
                    pass

    # Header file has the canonical suite name; fall back to file-derived
    if header_suite_name is None:
        try:
            header = json.loads((out / "header.json").read_text())
            header_suite_name = (header.get("config") or {}).get("name")
        except Exception:
            header_suite_name = out.name

    accuracy = (passed / total) if total else 0.0

    row_id = insert_eval_run(
        agent_id=args.agent_id,
        suite_name=header_suite_name or f"{args.agent_id}_persona_regression",
        total_samples=total,
        passed_samples=passed,
        accuracy=accuracy,
        gate_passed=gates_passed,
        gate_threshold=args.threshold,
        cost_usd_estimate=cost or None,
        raw_results_path=str(out),
        git_commit_sha=_git_sha(),
        notes=None,
    )
    print(
        f"logged magi_eval_runs row id={row_id} "
        f"agent={args.agent_id} acc={accuracy:.3f} gate={'PASS' if gates_passed else 'FAIL'} "
        f"cost=${cost:.4f} samples={passed}/{total}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
