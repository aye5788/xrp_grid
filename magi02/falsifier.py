"""MAGI-02 falsifier — runs the predicate ledger against an observer.db
snapshot (plan layer 3; named for the Matsushiro backup MAGI that
cross-examined the primary).

Stdlib-only by design: the desktop install needs python3 and nothing else.

Usage:
    python3 falsifier.py --db /path/to/observer.db [--include-proposed]
                         [--predicates predicates.json] [--report out.json]

Exit codes: 0 = all approved predicates hold; 1 = violations; 2 = runner error.

Safety: the snapshot is opened with SQLite's read-only URI mode — a mined or
mistyped predicate physically cannot write. Each predicate's SQL returns
EVIDENCE ROWS: with expect=zero_rows, any returned row is a violation and is
included verbatim in the report (the row IS the artifact, per the project's
no-DONE-without-verification-artifact rule).
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def load_ledger(path):
    with open(path) as f:
        return json.load(f)["predicates"]


def run_predicate(conn, pred):
    """Returns (ok, evidence_rows | error_str)."""
    params = {}
    if ":effective_from" in pred["sql"]:
        params["effective_from"] = pred.get("effective_from", "1970-01-01")
    try:
        cur = conn.execute(pred["sql"], params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [dict(zip(cols, r)) for r in cur.fetchmany(20)]
    except sqlite3.Error as e:
        return None, f"sql_error: {e}"
    if pred.get("expect", "zero_rows") == "zero_rows":
        return (len(rows) == 0), rows
    return None, f"unknown_expect: {pred.get('expect')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="observer.db snapshot path")
    ap.add_argument("--predicates",
                    default=os.path.join(HERE, "predicates.json"))
    ap.add_argument("--include-proposed", action="store_true",
                    help="also run status=proposed predicates (report-only; "
                         "they can never fail the run)")
    ap.add_argument("--report", default=os.path.join(HERE,
                                                     "last_report.json"))
    args = ap.parse_args()

    try:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True,
                               timeout=10)
    except sqlite3.Error as e:
        print(f"cannot open snapshot read-only: {e}", file=sys.stderr)
        return 2

    ledger = load_ledger(args.predicates)
    results, violations, errors = [], [], []
    for pred in ledger:
        status = pred.get("status", "proposed")
        if status != "approved" and not args.include_proposed:
            continue
        ok, evidence = run_predicate(conn, pred)
        entry = {
            "id": pred["id"],
            "status": status,
            "claim": pred["claim"],
            "source": pred["source"],
        }
        if ok is None:
            entry["result"] = "ERROR"
            entry["error"] = evidence
            errors.append(pred["id"])
        elif ok:
            entry["result"] = "HOLDS"
        else:
            entry["result"] = "VIOLATED"
            entry["evidence_rows"] = evidence
            # only APPROVED predicates can fail the run
            if status == "approved":
                violations.append(pred["id"])
        results.append(entry)
    conn.close()

    report = {
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot": args.db,
        "checked": len(results),
        "violations": violations,
        "sql_errors": errors,
        "results": results,
    }
    with open(args.report, "w") as f:
        json.dump(report, f, indent=2, default=str)

    for r in results:
        marker = {"HOLDS": "ok  ", "VIOLATED": "FAIL",
                  "ERROR": "ERR "}[r["result"]]
        print(f"[{marker}] {r['id']} ({r['status']})")
        if r["result"] == "VIOLATED":
            for row in r["evidence_rows"][:3]:
                print(f"        evidence: {row}")
    print(f"\n{len(results)} predicates checked — "
          f"{len(violations)} violation(s), {len(errors)} sql error(s). "
          f"Report: {args.report}")
    return 1 if violations else (2 if errors else 0)


if __name__ == "__main__":
    sys.exit(main())
