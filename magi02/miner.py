"""MAGI-02 promise miner — drafts NEW falsifiable predicates from the project
docs via a LOCAL model (Ollama on the desktop; zero API spend, private).

Stdlib-only. Output is ALWAYS status="proposed" into proposals.json — a mined
predicate can never alert or fail a run until the operator moves it into
predicates.json with status="approved". This gate is load-bearing, not
ceremony: published spec-extraction research (SpecGen, KBSpec, ICPC-2025)
shows LLMs fabricate specs absent from the docs and oversimplify boundaries.

Usage:
    python3 miner.py --docs CLAUDE.md 0*.md --stub        # plumbing test, no model
    python3 miner.py --docs $(git diff --name-only HEAD~1 -- '*.md')
                     [--model qwen2.5:7b] [--ollama http://localhost:11434]

Schema notes for the model are embedded in PROMPT; drafted SQL is validated
for read-only shape (single SELECT) and syntax-checked against a snapshot via
`falsifier.py --include-proposed` before anything reaches the operator.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

PROMPT = """You are auditing a trading system's documentation for FALSIFIABLE \
behavioral promises. The system logs to a SQLite database with tables: \
debate_records (council decisions: cycle_id, timestamp, trigger, stance, \
stance_correct, final_grid_action, final_risk_action, hard_rule_overrides, \
{seat}_r0_action), grid_orders (timestamp, order_id, side, price, size, \
status, filled_at, fill_price, fee), grid_state (timestamp, centre_price, \
spacing_pct, levels, pause_longs, pause_shorts), inventory (timestamp, \
xrp_held, usd_held), system_state (key, value), magi_gate_events (timestamp \
unix, trigger_id, fired, consumed_in_cycle, details json), magi_alerts, \
candles (timeframe, timestamp, open, high, low, close).

From the documentation excerpt below, extract promises about SYSTEM BEHAVIOR \
that the database could contradict. For each, emit a JSON object:
{"id": "short_snake_case", "claim": "one-sentence promise, quoted or tightly \
paraphrased", "source": "doc file + section", "sql": "ONE read-only SELECT \
over the tables above that returns VIOLATION rows (empty result = promise \
holds)", "expect": "zero_rows"}

Rules: only promises the schema above can actually test; prefer precise \
small checks over grand ones; NEVER invent behavior not stated in the text; \
if the excerpt contains no testable promise, emit nothing. Output: a JSON \
array, no prose.

DOCUMENTATION EXCERPT:
"""

READ_ONLY_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
FORBIDDEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|PRAGMA|REPLACE)\b",
    re.IGNORECASE)


def _chunks(text, size=6000, overlap=400):
    i = 0
    while i < len(text):
        yield text[i:i + size]
        i += size - overlap


def _ollama(prompt, model, base):
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=json.dumps({"model": model, "prompt": prompt,
                         "stream": False,
                         "options": {"temperature": 0.2}}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r).get("response", "")


def _extract_json_array(text):
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


def mine(doc_paths, model, base, stub=False):
    proposals = []
    for path in doc_paths:
        if not os.path.exists(path):
            print(f"  skip (missing): {path}")
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        for chunk in _chunks(text):
            if stub:
                continue  # plumbing test: read + chunk, call nothing
            raw = _ollama(PROMPT + chunk, model, base)
            for cand in _extract_json_array(raw):
                sql = str(cand.get("sql", ""))
                if not READ_ONLY_RE.match(sql) or FORBIDDEN_RE.search(sql):
                    print(f"  rejected (not read-only): {cand.get('id')}")
                    continue
                proposals.append({
                    "id": str(cand.get("id", "unnamed"))[:60],
                    "claim": str(cand.get("claim", ""))[:500],
                    "source": f"{path} (mined; verify citation): "
                              f"{str(cand.get('source', ''))[:200]}",
                    "sql": sql,
                    "expect": "zero_rows",
                    "status": "proposed",
                    "mined_at_utc":
                        datetime.now(timezone.utc).isoformat(),
                    "model": model,
                })
        print(f"  mined: {path}")
    return proposals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", nargs="+", required=True)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--stub", action="store_true",
                    help="no model calls — plumbing test only")
    ap.add_argument("--out", default=os.path.join(HERE, "proposals.json"))
    args = ap.parse_args()

    proposals = mine(args.docs, args.model, args.ollama, stub=args.stub)

    existing = []
    if os.path.exists(args.out):
        existing = json.load(open(args.out)).get("proposals", [])
    seen = {p["id"] for p in existing}
    fresh = [p for p in proposals if p["id"] not in seen]
    with open(args.out, "w") as f:
        json.dump({"proposals": existing + fresh}, f, indent=2)
    tail = ("STUB MODE — plumbing only, nothing mined" if args.stub
            else "review, then promote worthy ones into predicates.json "
                 "as approved")
    print(f"{len(fresh)} new proposal(s) -> {args.out} ({tail})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
