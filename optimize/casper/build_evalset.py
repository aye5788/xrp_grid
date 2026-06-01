"""Convert evals/casper/dataset.jsonl -> ADK train_eval_set.evalset.json.

Each Letta-era case has {id, ground_truth, agent_args.world_state}. For the
stateless ADK agent the decision tree lives in the persona instruction, so the
user turn only needs to carry the world_state. We deliberately DROP the old
Letta `input` wrapper: it tells the agent to read a self_model block and use
core_memory tools — neither exists on the stateless ADK agent, and feeding stale
self_model instructions would bias the optimizer.

Writes train_eval_set.evalset.json next to this script.
"""

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_SRC = _REPO_ROOT / "evals" / "casper" / "dataset.jsonl"
_OUT = _HERE / "train_eval_set.evalset.json"
_APP = "casper"

_PROMPT = (
    "Classify the market regime for this cycle using your decision tree, then "
    "respond with your RegimeVote.\n\nworld_state:\n{ws}"
)


def main() -> None:
    cases = []
    for line in _SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        ws = row["agent_args"]["world_state"]
        gt = str(row["ground_truth"]).strip().upper()
        cases.append(
            {
                "eval_id": f"casper_{row['id']:03d}",
                "conversation": [
                    {
                        "invocation_id": f"inv_{row['id']}",
                        "user_content": {
                            "parts": [{"text": _PROMPT.format(ws=json.dumps(ws))}],
                            "role": "user",
                        },
                        "final_response": {"parts": [{"text": gt}], "role": "model"},
                    }
                ],
                "session_input": {"app_name": _APP, "user_id": "user"},
            }
        )

    out = {
        "eval_set_id": "train_eval_set",
        "name": "train_eval_set",
        "eval_cases": cases,
    }
    _OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {len(cases)} cases -> {_OUT}")


if __name__ == "__main__":
    main()
