#!/usr/bin/env bash
# TEMPORARY: run the persona regression for ONLY casper + balthasar (the two
# agents changed in the stranded-grid fix). Skips melchior (pre-existing
# failure, untouched by this work). Mirrors run_all.sh's per-agent path
# (cleanup + letta-evals + log_results) so magi_eval_runs rows are written.
# Delete after the fix is validated.
set -uo pipefail
cd "$(dirname "$0")"
REPO_ROOT="$(cd .. && pwd)"
EVAL_VENV_PY="${REPO_ROOT}/evals/.venv/bin/python"
LETTA_EVALS="${REPO_ROOT}/evals/.venv/bin/letta-evals"
MAGI_VENV_PY="${REPO_ROOT}/venv/bin/python"
export MAGI_EVAL_RUN_ID="run$(date -u +%Y%m%dT%H%M%S)"
TS="$(date -u +%Y%m%dT%H%M%S)"

echo "[1/3] cleanup stale eval agents..."
"${MAGI_VENV_PY}" "${REPO_ROOT}/evals/common/cleanup_eval_agents.py" || true
echo

echo "[2/3] snapshot live self_models..."
"${MAGI_VENV_PY}" "${REPO_ROOT}/evals/common/snapshot_self_models.py" || true
echo

for AGENT in casper balthasar; do
    OUT_DIR="${REPO_ROOT}/evals/results/${TS}_${AGENT}"
    echo "[3/3] full suite: ${AGENT} -> ${OUT_DIR}"
    pushd "${REPO_ROOT}/evals/${AGENT}" >/dev/null
    PYTHONPATH="${REPO_ROOT}/evals" "${LETTA_EVALS}" run suite.yaml \
        --output "${OUT_DIR}" --max-concurrent 2 || true
    popd >/dev/null
    TH="$(grep -E '^\s+value:' "${REPO_ROOT}/evals/${AGENT}/suite.yaml" | head -1 | awk '{print $2}')"
    "${MAGI_VENV_PY}" "${REPO_ROOT}/evals/common/log_results.py" \
        "${AGENT}" "${OUT_DIR}" --threshold "${TH}" || echo "WARN: log_results failed ${AGENT}"
    echo
done
echo "done."
