"""Log the F5 acceptance replay as a Langfuse dataset run (auditable record).

Creates/reuses dataset `f5-acceptance`, one item per config (deterministic
item ids — POST /dataset-items upserts by id, so re-runs are idempotent),
one trace per config via the ingestion API (deterministic uuid5 trace ids —
trace-create with the same id MERGES, the documented update path), then
links item -> trace under a dated run name via /dataset-run-items.

Known gotchas honoured (memory: langfuse-api-gotchas): list-before-create
where the API does not dedupe; every POST's HTTP status is checked and
reported — nothing fire-and-forget here, this record IS the deliverable.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import f5_replay

ENV_PATH = "/root/xrp_grid/.env"
DATASET_NAME = "f5-acceptance"
RUN_NAME = "f5-2026-06-12"
F5_NS = uuid.UUID("a3f5acce-0000-0000-0000-000000000000")  # uuid5 namespace


def load_env():
    vals = {}
    with open(ENV_PATH) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("LANGFUSE_") and "=" in line:
                k, v = line.split("=", 1)
                vals[k] = v.strip().strip('"')
    return vals


def main():
    env = load_env()
    base = env["LANGFUSE_BASE_URL"].rstrip("/")
    auth = (env["LANGFUSE_PUBLIC_KEY"], env["LANGFUSE_SECRET_KEY"])
    report = f5_replay.main()

    failures = []

    def post(path, body):
        r = requests.post(f"{base}{path}", json=body, auth=auth, timeout=10)
        if r.status_code not in (200, 201, 202, 207):
            failures.append(f"POST {path} -> {r.status_code}: {r.text[:200]}")
        return r

    # 1. Dataset: list first, create only if absent.
    r = requests.get(f"{base}/api/public/v2/datasets", auth=auth,
                     params={"limit": 50}, timeout=10)
    r.raise_for_status()
    names = {d["name"] for d in r.json().get("data", [])}
    if DATASET_NAME not in names:
        post("/api/public/v2/datasets", {
            "name": DATASET_NAME,
            "description": ("Offline acceptance test for the 2026-06-11 "
                            "five-fix rebuild: 2025->2026 hourly replay from "
                            "tape/history.db, rebuilt vs old config. "
                            "Pre-committed pass criteria: more money AND "
                            "smaller worst drawdown."),
        })

    # 2. Items (upsert by deterministic id) + traces + run links.
    for cfg_name, cfg in report["configs"].items():
        item_id = f"f5-cfg-{cfg_name}"
        post("/api/public/dataset-items", {
            "datasetName": DATASET_NAME,
            "id": item_id,
            "input": {"config": cfg_name, **cfg,
                      "window": report["window"]},
            "expectedOutput": {
                "criteria": "rebuilt must beat old on BOTH end equity and "
                            "worst drawdown (pre-committed, never fitted)",
            },
        })

        trace_id = str(uuid.uuid5(F5_NS, f"{RUN_NAME}:{cfg_name}"))
        now = datetime.now(timezone.utc).isoformat()
        post("/api/public/ingestion", {"batch": [{
            "id": str(uuid.uuid4()),
            "type": "trace-create",
            "timestamp": now,
            "body": {
                "id": trace_id,
                "name": f"f5-replay:{cfg_name}",
                "timestamp": now,
                "input": cfg,
                "output": report["results"][cfg_name],
                "tags": ["f5-acceptance", f"verdict:{report['verdict']}"],
                "metadata": {"window": report["window"],
                             "criteria": report["criteria"]},
            },
        }]})

        post("/api/public/dataset-run-items", {
            "runName": RUN_NAME,
            "datasetItemId": item_id,
            "traceId": trace_id,
            "metadata": {"verdict": report["verdict"],
                         "criteria": report["criteria"],
                         "results": report["results"][cfg_name]},
        })

    if failures:
        print("\nLANGFUSE LOGGING FAILURES:")
        for f in failures:
            print(" ", f)
        sys.exit(1)
    print(f"\nLangfuse dataset run logged: dataset={DATASET_NAME} "
          f"run={RUN_NAME} verdict={report['verdict']}")


if __name__ == "__main__":
    main()
