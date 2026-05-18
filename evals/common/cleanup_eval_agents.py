"""
Cleanup of eval-only agents in the magi-evals Letta project.

Per operator decision: runs in run_all.sh BEFORE any new agents are
created, NOT inside the factory. This keeps the per-sample path free of
network round-trips for listing/deleting agents.

Policy: keep the most recent N "cohorts" (run IDs encoded in agent name as
`eval-<agent>-<RUN_ID>-<sample_id>-<ts>`). Default N=3. Anything older or
not matching the eval-naming convention is left alone (only matches our
own naming pattern — never deletes random project agents).

Historical bug (2026-05-18 audit): originally this script filtered
`client.agents.list(project_id='magi-evals')` — but the SDK's project_id
parameter does NOT slug-resolve, so the query returned 0 real eval agents
even though 102 were live in the resolved-UUID project. The script ran
clean for 10+ sweeps while zombies accumulated, burning $28+ of credit.

The fix (FIX A + FIX B + FIX E):
  - List ALL agents (no project filter) and match on EVAL_NAME_RE.
    Production agents are named 'casper'/'melchior'/'balthasar' so they
    cannot match the regex.
  - Double safety check before deletion: candidate name must match
    EVAL_NAME_RE AND id must NOT be in agent_registry. ABORT on any
    candidate that fails either check.
  - Optional third check (FIX E): if LETTA_EVALS_PROJECT_UUID is set,
    candidate's project_id must equal that UUID. ABORT otherwise.
  - Post-cleanup invariant: re-list, count eval-pattern agents; > 50
    raises an exception that fails the eval run. Log count every run.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(REPO_ROOT / ".env")

from letta_client import Letta  # noqa: E402

EVAL_NAME_RE = re.compile(
    r"^eval-(?P<agent>casper|melchior|balthasar)-"
    r"(?P<run_id>[a-zA-Z0-9_-]+?)-"
    r"(?P<sample_id>\d+)-"
    r"(?P<ts>\d+)$"
)

KEEP_COHORTS = 3
POST_CLEANUP_HARD_ALARM = 50  # FIX B: refuse to proceed if still > N


def _list_all_agents(client: Letta) -> list:
    """Paginate ALL agents visible to this API key. No project filter."""
    out = []
    after = None
    while True:
        page = list(client.agents.list(
            limit=100,
            **({"after": after} if after else {}),
        ))
        if not page:
            break
        out.extend(page)
        if len(page) < 100:
            break
        after = page[-1].id
    return out


def _production_agent_ids() -> set:
    """Production agent UUIDs from observer.db agent_registry. Source of
    truth for 'do not delete'."""
    conn = sqlite3.connect(str(REPO_ROOT / "observer.db"))
    ids = set(row[0] for row in conn.execute(
        "SELECT letta_agent_id FROM agent_registry"
        " WHERE letta_agent_id IS NOT NULL"
    ))
    conn.close()
    return ids


def main() -> int:
    api_key = os.environ.get("LETTA_API_KEY")
    if not api_key:
        print("ERROR: LETTA_API_KEY not set", file=sys.stderr)
        return 1

    eval_project_uuid = os.environ.get("LETTA_EVALS_PROJECT_UUID", "").strip()
    if eval_project_uuid:
        print(f"[cleanup] project UUID safety check enabled: {eval_project_uuid}",
              file=sys.stderr)
    else:
        print("[cleanup] LETTA_EVALS_PROJECT_UUID not set — skipping the "
              "project-UUID safety check (FIX A and FIX B safety still apply)",
              file=sys.stderr)

    client = Letta(api_key=api_key)
    prod_ids = _production_agent_ids()
    print(f"[cleanup] production agent UUIDs to protect: {len(prod_ids)} "
          f"({sorted(prod_ids)})", file=sys.stderr)

    all_agents = _list_all_agents(client)
    print(f"[cleanup] total agents visible: {len(all_agents)}", file=sys.stderr)

    # FIX A: classify by name regex
    runs: dict[str, list] = defaultdict(list)
    skipped = []
    for agent in all_agents:
        m = EVAL_NAME_RE.match(agent.name or "")
        if not m:
            skipped.append(agent)
            continue
        runs[m.group("run_id")].append((int(m.group("ts")), agent))

    cohort_age = {run_id: max(ts for ts, _ in items)
                  for run_id, items in runs.items()}
    keep_run_ids = set(sorted(cohort_age, key=cohort_age.get,
                              reverse=True)[:KEEP_COHORTS])

    candidates = []
    for run_id, items in runs.items():
        if run_id in keep_run_ids:
            continue
        for _, agent in items:
            candidates.append(agent)

    print(f"[cleanup] {len(runs)} eval-cohorts found, "
          f"{len(keep_run_ids)} kept, {len(candidates)} marked for deletion, "
          f"{len(skipped)} non-eval agents skipped", file=sys.stderr)

    # FIX A safety: pre-deletion verification — every candidate must match
    # regex AND not be a production ID AND (if set) live in the expected UUID.
    # Any failure ABORTS the entire run.
    for agent in candidates:
        m = EVAL_NAME_RE.match(agent.name or "")
        if not m:
            print(f"CRITICAL: candidate {agent.id} ({agent.name!r}) does not "
                  f"match EVAL_NAME_RE — aborting cleanup", file=sys.stderr)
            return 2
        if agent.id in prod_ids:
            print(f"CRITICAL: candidate {agent.id} is a PRODUCTION agent "
                  f"({agent.name!r}) — aborting cleanup, no deletions performed",
                  file=sys.stderr)
            return 2
        if eval_project_uuid:
            agent_project = getattr(agent, "project_id", None)
            if agent_project != eval_project_uuid:
                print(f"CRITICAL: candidate {agent.id} ({agent.name!r}) has "
                      f"project_id={agent_project!r}, expected "
                      f"{eval_project_uuid!r} — aborting cleanup", file=sys.stderr)
                return 2

    # Execute deletions
    deleted = 0
    for agent in candidates:
        try:
            client.agents.delete(agent.id)
            deleted += 1
        except Exception as e:
            print(f"WARN: failed to delete {agent.id} ({agent.name}): {e}",
                  file=sys.stderr)
    print(f"[cleanup] deleted {deleted}/{len(candidates)} stale eval agents",
          file=sys.stderr)

    # FIX B: post-cleanup invariant — re-list and count, alarm if > threshold
    post = _list_all_agents(client)
    eval_post = sum(1 for a in post if EVAL_NAME_RE.match(a.name or ""))
    prod_post = sum(1 for a in post if a.id in prod_ids)
    print(f"[cleanup] post-cleanup eval-agent count: {eval_post} "
          f"(production: {prod_post})", file=sys.stderr)

    if eval_post > POST_CLEANUP_HARD_ALARM:
        raise SystemExit(
            f"[cleanup] HARD ALARM: post-cleanup eval-agent count {eval_post} "
            f"exceeds {POST_CLEANUP_HARD_ALARM}. The cleanup script may be "
            f"broken or evals are running in parallel. Refusing to proceed "
            f"with eval suite. Investigate before re-running."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
