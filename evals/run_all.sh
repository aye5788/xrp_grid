#!/usr/bin/env bash
# Run Option A persona regression for all three council agents.
#
# Order of operations (per operator spec):
#   1. Cleanup stale eval agents in magi-evals project BEFORE any new
#      agents are created (keeps last 3 cohorts).
#   2. Snapshot current live self_models to evals/self_model_snapshots/*.txt.
#   3. For each agent: run the suite, save results to
#      evals/results/<ts>_<agent>/, parse results, INSERT magi_eval_runs row.
#   4. Print combined summary with delta-vs-previous run per agent.
#
# Exits 0 iff all three suite gates pass. Honours MAGI_EVAL_MAX_SAMPLES
# env var if set (e.g. =3 for smoke test).
set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT="$(cd .. && pwd)"
EVAL_VENV_PY="${REPO_ROOT}/evals/.venv/bin/python"
LETTA_EVALS="${REPO_ROOT}/evals/.venv/bin/letta-evals"
MAGI_VENV_PY="${REPO_ROOT}/venv/bin/python"

# Run ID groups all of this sweep's eval agents under a single cohort for
# cleanup purposes. Exported to the factory via env.
export MAGI_EVAL_RUN_ID="${MAGI_EVAL_RUN_ID:-run$(date -u +%Y%m%dT%H%M%S)}"
TS="$(date -u +%Y%m%dT%H%M%S)"

# Optional smoke-test sample cap (passed to letta-evals as a temp override
# of max_samples via env — see PYTHONPATH trick below).
MAX_SAMPLES="${MAGI_EVAL_MAX_SAMPLES:-}"

echo "=== MAGI evals — run_id=${MAGI_EVAL_RUN_ID} ts=${TS} ==="
if [[ -n "$MAX_SAMPLES" ]]; then
    echo "SMOKE MODE: limiting each suite to ${MAX_SAMPLES} samples"
fi
echo

# 0) Pre-flight ghost-agent census (FIX C, 2026-05-18). If more than 20
# non-production agents exist before the eval starts, refuse to proceed
# until the cleanup leak is investigated. Independent of step 1 — catches
# the case where letta-evals was invoked directly without cleanup running.
echo "[0/4] Pre-flight ghost-agent census..."
"${MAGI_VENV_PY}" - <<'PRECHECK'
import os, sqlite3, sys
from pathlib import Path
from dotenv import load_dotenv
REPO_ROOT = Path("/root/xrp_grid")
load_dotenv(REPO_ROOT / ".env")
from letta_client import Letta
c = Letta(api_key=os.environ['LETTA_API_KEY'])

conn = sqlite3.connect(str(REPO_ROOT / "observer.db"))
prod_ids = set(r[0] for r in conn.execute(
    "SELECT letta_agent_id FROM agent_registry WHERE letta_agent_id IS NOT NULL"))
conn.close()

all_agents = []
after = None
while True:
    page = list(c.agents.list(limit=100, **({"after": after} if after else {})))
    if not page:
        break
    all_agents.extend(page)
    if len(page) < 100:
        break
    after = page[-1].id

ghost = [a for a in all_agents if a.id not in prod_ids]
print(f"[pre-flight] total agents: {len(all_agents)}, "
      f"production: {len(all_agents) - len(ghost)}, ghost: {len(ghost)}")
THRESHOLD = 20
if len(ghost) > THRESHOLD:
    print(f"[pre-flight] ABORT: ghost agent count {len(ghost)} > {THRESHOLD}. "
          f"Investigate cleanup_eval_agents.py before proceeding.",
          file=sys.stderr)
    sys.exit(3)
PRECHECK
echo

# 1) Cleanup (uses production venv since it doesn't need letta-evals; but
# this script imports letta_client only — main venv has it). Use main venv.
echo "[1/4] Cleaning up stale eval agents (keep last 3 cohorts)..."
"${MAGI_VENV_PY}" "${REPO_ROOT}/evals/common/cleanup_eval_agents.py"
echo

# 2) Snapshot self_models
echo "[2/4] Snapshotting live self_models..."
"${MAGI_VENV_PY}" "${REPO_ROOT}/evals/common/snapshot_self_models.py"
echo

# 3) Run each suite
TOTAL_RC=0
SUMMARY_LINES=()
for AGENT in casper melchior balthasar; do
    OUT_DIR="${REPO_ROOT}/evals/results/${TS}_${AGENT}"
    SUITE_DIR="${REPO_ROOT}/evals/${AGENT}"
    echo "[3/4] Running suite: ${AGENT} → ${OUT_DIR}"
    pushd "${SUITE_DIR}" >/dev/null

    # max_samples cap is a YAML-only knob; if MAGI_EVAL_MAX_SAMPLES is set
    # we generate a temporary suite.yaml that injects it (kept beside the
    # real one as suite.smoke.yaml so it's debuggable).
    SUITE_FILE="suite.yaml"
    if [[ -n "$MAX_SAMPLES" ]]; then
        "${EVAL_VENV_PY}" - <<EOF
import yaml, pathlib
src = pathlib.Path("suite.yaml")
dst = pathlib.Path("suite.smoke.yaml")
data = yaml.safe_load(src.read_text())
data["max_samples"] = int("${MAX_SAMPLES}")
dst.write_text(yaml.safe_dump(data, sort_keys=False))
print(f"wrote {dst} with max_samples={data['max_samples']}")
EOF
        SUITE_FILE="suite.smoke.yaml"
    fi

    # PYTHONPATH so common/ is importable from each suite dir.
    # --max-concurrent 2: Letta Cloud's per-route RPS limit (~429 route_rps_
    # rate_limit_exceeded) kicks in at the framework's default of 15. Two
    # concurrent samples keeps us inside the budget while still avoiding
    # fully-serial latency. Override with MAGI_EVAL_MAX_CONCURRENT if needed.
    MAXC="${MAGI_EVAL_MAX_CONCURRENT:-2}"
    if PYTHONPATH="${REPO_ROOT}/evals" \
        "${LETTA_EVALS}" run "${SUITE_FILE}" --output "${OUT_DIR}" \
        --max-concurrent "${MAXC}"; then
        SUITE_RC=0
    else
        SUITE_RC=$?
    fi
    popd >/dev/null

    # Log to DB regardless of gate outcome
    THRESHOLD="$(grep -E '^\s+value:' "${SUITE_DIR}/suite.yaml" | head -1 | awk '{print $2}')"
    "${MAGI_VENV_PY}" "${REPO_ROOT}/evals/common/log_results.py" \
        "${AGENT}" "${OUT_DIR}" --threshold "${THRESHOLD}" \
        || echo "WARN: log_results failed for ${AGENT}"

    # Render summary line
    SUMMARY=$(
        "${MAGI_VENV_PY}" - <<EOF
import sys
sys.path.insert(0, "${REPO_ROOT}")
from database import get_recent_eval_runs
runs = get_recent_eval_runs("${AGENT}", limit=2)
if not runs:
    print("${AGENT}: (no rows logged)")
else:
    r = runs[0]
    delta = ""
    if len(runs) > 1:
        prev = runs[1]["accuracy"]
        d = r["accuracy"] - prev
        delta = f" (Δ {d:+.3f} vs prev)"
    gp = "PASS" if r["gate_passed"] else "FAIL"
    cost = r["cost_usd_estimate"]
    cost_s = f" \${cost:.4f}" if cost is not None else ""
    print(f"${AGENT}: acc={r['accuracy']:.3f} {gp} "
          f"({r['passed_samples']}/{r['total_samples']}){cost_s}{delta}")
EOF
    )
    SUMMARY_LINES+=("${SUMMARY}")

    if [[ "${SUITE_RC}" -ne 0 ]]; then
        TOTAL_RC=1
    fi
    echo
done

# 4) Combined summary
echo "[4/4] === Summary ==="
for line in "${SUMMARY_LINES[@]}"; do
    echo "  ${line}"
done
echo
if [[ "${TOTAL_RC}" -eq 0 ]]; then
    echo "ALL GATES PASSED"
else
    echo "ONE OR MORE GATES FAILED (exit ${TOTAL_RC})"
fi
exit "${TOTAL_RC}"
