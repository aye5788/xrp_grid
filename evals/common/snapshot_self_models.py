"""
Snapshot live self_model blocks from the three production MAGI agents to
evals/self_model_snapshots/*.txt. Read-only against production: pulls
block values via the SDK, writes to local files.

Run before each eval sweep (handled by run_all.sh).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

from letta_client import Letta  # noqa: E402
import os  # noqa: E402

SNAPSHOT_DIR = REPO_ROOT / "evals" / "self_model_snapshots"


def main() -> int:
    api_key = os.environ.get("LETTA_API_KEY")
    if not api_key:
        print("ERROR: LETTA_API_KEY not set", file=sys.stderr)
        return 1

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(REPO_ROOT / "observer.db"))
    rows = conn.execute(
        "SELECT agent_id, letta_agent_id FROM agent_registry"
    ).fetchall()
    conn.close()
    if not rows:
        print("ERROR: agent_registry empty", file=sys.stderr)
        return 2

    client = Letta(api_key=api_key)
    written = []
    for agent_id, letta_id in rows:
        blocks = list(client.agents.blocks.list(letta_id))
        sm = next((b for b in blocks if b.label == "self_model"), None)
        if not sm:
            print(f"WARN: {agent_id} ({letta_id}) has no self_model block; "
                  f"writing empty placeholder")
            value = ""
        else:
            value = sm.value or ""
        path = SNAPSHOT_DIR / f"{agent_id}.txt"
        path.write_text(value)
        written.append((agent_id, len(value)))

    print("snapshotted self_models:")
    for agent_id, n in written:
        print(f"  {agent_id}: {n} chars → {SNAPSHOT_DIR / f'{agent_id}.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
