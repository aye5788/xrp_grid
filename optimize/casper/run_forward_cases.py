"""Run the CURRENT Casper persona against the forward-realized draft cases.

Validation, not tuning: does the live stateless RegimeVote agent (agent.root_agent,
native gemini-2.5-flash, the live persona) classify these reality-anchored windows
correctly? The hypothesis is that Casper UNDER-CALLS the item-0* danger band
(weak-ADX bearish base that bled) — i.e. says RANGING/UNCERTAIN where reality was
TRENDING — which is exactly the coverage the current 8-case suite lacks.

Offline, free-tier Gemini ($0). Run from repo root:
    optimize/.venv/bin/python optimize/casper/run_forward_cases.py
"""

import asyncio
import json
import re
from pathlib import Path

from agent import root_agent  # sets sys.path, loads .env + persona
from recall import load_corpus, recall, recall_text

from google.adk.runners import InMemoryRunner
from google.genai import types

_HERE = Path(__file__).resolve().parent
DRAFT = _HERE / "forward_cases.draft.jsonl"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
PROMPT = ("Classify the market regime for this cycle using your decision tree, "
          "then respond with your RegimeVote.\n\nworld_state:\n{ws}")
_CORPUS = load_corpus()


def _recall_for(case):
    m = case["metadata"]
    rec = recall(_CORPUS, m["ema_distance_pct"], m["recon_adx"], m["recon_adx_pos"],
                 m["recon_adx_neg"], m["recon_roc_6h"],
                 query_ts=case["agent_args"]["world_state"]["timestamp"])
    return recall_text(rec)


def load_cases():
    out = []
    for line in DRAFT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def parse_vote(text):
    s = _FENCE.sub("", text or "").strip()
    try:
        obj = json.loads(s)
    except Exception:
        return {}, {}
    return obj, obj


async def run_case(runner, case, inject_recall=False):
    ws = case["agent_args"]["world_state"]
    text = PROMPT.format(ws=json.dumps(ws))
    if inject_recall:
        text += "\n\n" + _recall_for(case)
    msg = types.Content(role="user", parts=[types.Part(text=text)])
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id="val")
    final = ""
    async for event in runner.run_async(
            user_id="val", session_id=session.id, new_message=msg):
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)
    return final


async def _vote(runner, case, inject):
    obj, _ = parse_vote(await run_case(runner, case, inject_recall=inject))
    return (str(obj.get("position", "")).strip().upper(),
            str(obj.get("regime_action", "")).strip().upper())


async def main():
    cases = load_cases()
    runner = InMemoryRunner(agent=root_agent, app_name="casper")

    rows = []
    for c in cases:
        gt = str(c["ground_truth"]).strip().upper()
        base_pos, base_ract = await _vote(runner, c, inject=False)
        mem_pos, mem_ract = await _vote(runner, c, inject=True)
        rows.append({
            "id": c["id"], "ts": c["agent_args"]["world_state"]["timestamp"][:10],
            "gt": gt, "base": base_pos, "mem": mem_pos,
            "mem_ract": mem_ract,
            "base_ok": base_pos == gt, "mem_ok": mem_pos == gt,
        })

    hdr = (f"{'#':>2} {'date':11} {'truth':9} {'base':9} {'b':1} {'+memory':9} {'m':1} "
           f"{'regime_action(mem)':18}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        chg = "→FIXED" if (r["mem_ok"] and not r["base_ok"]) else \
              "→BROKE" if (r["base_ok"] and not r["mem_ok"]) else ""
        print(f"{r['id']:>2} {r['ts']:11} {r['gt']:9} {(r['base'] or '—'):9} "
              f"{'✓' if r['base_ok'] else '✗'} {(r['mem'] or '—'):9} "
              f"{'✓' if r['mem_ok'] else '✗'} {(r['mem_ract'] or '—'):18} {chg}")

    n = len(rows)
    b = sum(r["base_ok"] for r in rows)
    m = sum(r["mem_ok"] for r in rows)
    print(f"\nBASELINE (no memory): {b}/{n} = {b/n*100:.0f}%")
    print(f"WITH real-outcome memory: {m}/{n} = {m/n*100:.0f}%")
    fixed = [r["id"] for r in rows if r["mem_ok"] and not r["base_ok"]]
    broke = [r["id"] for r in rows if r["base_ok"] and not r["mem_ok"]]
    print(f"  fixed by memory: {fixed or 'none'}")
    print(f"  broken by memory: {broke or 'none'}")
    for band in ("RANGING", "TRENDING"):
        grp = [r for r in rows if r["gt"] == band]
        if grp:
            print(f"  {band:9}-truth: base {sum(r['base_ok'] for r in grp)}/{len(grp)}"
                  f" -> memory {sum(r['mem_ok'] for r in grp)}/{len(grp)}")


if __name__ == "__main__":
    asyncio.run(main())
