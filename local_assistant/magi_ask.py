#!/usr/bin/env python3
"""
magi_ask.py — local "ask-it" assistant for the MAGI grid bot.

Runs on the home desktop. It reads a LOCAL COPY of the bot's database
(pulled from the droplet by pull_db.ps1), assembles a compact snapshot of
the current state, and asks a local Ollama model to explain it in plain
English using GLOSSARY.md as its system knowledge.

Dependency-free: only the Python standard library + a running Ollama server.
No `pip install` needed. sqlite3, json, urllib are all stdlib.

Usage:
    python magi_ask.py                      # interactive REPL
    python magi_ask.py "what happened at the noon wake?"   # one-shot

Config: edit the constants below (model name, DB path, Ollama URL).
"""

import os
import sys
import json
import sqlite3
import urllib.request
from datetime import datetime, timezone, timedelta

# ---- config -----------------------------------------------------------------
# The model to narrate with. Any local Ollama instruct model works; swap freely.
MODEL = os.environ.get("MAGI_MODEL", "llama3.1:8b")

# Local copy of observer.db (pull_db.ps1 keeps this fresh). Defaults to a file
# named observer.db sitting next to this script; override with MAGI_DB.
_HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("MAGI_DB", os.path.join(_HERE, "observer.db"))
GLOSSARY_PATH = os.path.join(_HERE, "GLOSSARY.md")

OLLAMA_URL = os.environ.get("MAGI_OLLAMA_URL", "http://localhost:11434/api/chat")
# ----------------------------------------------------------------------------


def _conn():
    # read-only; the DB is a copy, but be polite about it
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _section(title, fn):
    """Run a bundle-builder fn, returning its text under a heading. Any failure
    degrades to a one-line note rather than crashing the whole bundle."""
    try:
        body = fn()
        return f"## {title}\n{body}\n" if body else ""
    except Exception as e:
        return f"## {title}\n(unavailable: {e})\n"


def _standing_state(c):
    keys = ("council_stance", "council_stance_since",
            "down_walk_streak", "paper_run_started_utc")
    rows = c.execute(
        "SELECT key, value FROM system_state WHERE key IN (%s)"
        % ",".join("?" * len(keys)), keys).fetchall()
    d = {r["key"]: r["value"] for r in rows}
    return "\n".join(f"- {k}: {d.get(k, 'n/a')}" for k in keys)


def _latest_price(c):
    r = c.execute(
        "SELECT timestamp, close FROM candles ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    return f"- last price {r['close']} at {r['timestamp']}" if r else ""


def _indicators(c):
    r = c.execute(
        "SELECT timestamp, timeframe, ema_50, ema_200, adx, adx_pos, adx_neg, "
        "roc_6h, vwap_dev_pct, vol_regime FROM indicators "
        "ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return ""
    return ("- as of {ts} (timeframe {tf}): vol_regime={vr}, adx={adx}, "
            "adx_pos={ap}, adx_neg={an}, roc_6h={roc}, vwap_dev_pct={vd}, "
            "ema_50={e50}, ema_200={e200} (ema_200 is a 200-DAY average)"
            ).format(ts=r["timestamp"], tf=r["timeframe"], vr=r["vol_regime"],
                     adx=r["adx"], ap=r["adx_pos"], an=r["adx_neg"],
                     roc=r["roc_6h"], vd=r["vwap_dev_pct"],
                     e50=r["ema_50"], e200=r["ema_200"])


def _recent_cycles(c):
    rows = c.execute(
        "SELECT timestamp, trigger, casper_r0_action, melchior_r0_action, "
        "balthasar_r0_action, final_grid_action, final_risk_action, stance, "
        "casper_r0_crux, melchior_r0_crux, balthasar_r0_crux "
        "FROM debate_records ORDER BY id DESC LIMIT 6").fetchall()
    if not rows:
        return ""
    out = []
    for i, r in enumerate(rows):
        out.append(
            f"- {r['timestamp']} trigger={r['trigger']} | "
            f"votes: casper={r['casper_r0_action']}, "
            f"melchior={r['melchior_r0_action']}, "
            f"balthasar={r['balthasar_r0_action']} | "
            f"final_grid={r['final_grid_action']}, "
            f"final_risk={r['final_risk_action']}, stance={r['stance']}")
        if i == 0:  # full reasoning only for the most recent cycle
            for seat in ("casper", "melchior", "balthasar"):
                crux = r[f"{seat}_r0_crux"]
                if crux:
                    out.append(f"    {seat} reasoning: {crux}")
    return "\n".join(out)


def _recent_wakes(c):
    rows = c.execute(
        "SELECT datetime(timestamp,'unixepoch') AS ts, trigger_id, details "
        "FROM magi_gate_events WHERE trigger_id IN ('W1','W2') AND fired=1 "
        "ORDER BY id DESC LIMIT 8").fetchall()
    if not rows:
        return "(no W1/W2 wake events on record)"
    return "\n".join(
        f"- {r['ts']} {r['trigger_id']} fired: {r['details']}" for r in rows)


def _vital_signs(c):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    tot = c.execute(
        "SELECT side, COUNT(*) n, MAX(filled_at) last FROM grid_orders "
        "WHERE status='filled' GROUP BY side").fetchall()
    last24 = c.execute(
        "SELECT side, COUNT(*) n FROM grid_orders WHERE status='filled' "
        "AND filled_at >= ? GROUP BY side", (cutoff,)).fetchall()
    if not tot:
        return "(no fills recorded)"
    d24 = {r["side"]: r["n"] for r in last24}
    lines = []
    for r in tot:
        lines.append(f"- {r['side']}: {r['n']} fills total, "
                     f"{d24.get(r['side'], 0)} in last 24h, "
                     f"last fill {r['last']}")
    return "\n".join(lines)


def _pnl(c):
    rows = c.execute(
        "SELECT date, net_pnl, fees_paid, trades_count FROM pnl_daily "
        "ORDER BY date DESC LIMIT 7").fetchall()
    if not rows:
        return ""
    note = ("(pnl_daily = rough daily aggregates; the authoritative paper "
            "equity P&L lives on the dashboard)")
    body = "\n".join(
        f"- {r['date']}: net {r['net_pnl']}, fees {r['fees_paid']}, "
        f"trades {r['trades_count']}" for r in rows)
    return note + "\n" + body


def build_context():
    c = _conn()
    try:
        parts = [
            f"DATA BUNDLE (pulled {datetime.now(timezone.utc).isoformat()} UTC; "
            f"DB copy may be a few minutes stale)\n",
            _section("Standing state", lambda: _standing_state(c)),
            _section("Latest price", lambda: _latest_price(c)),
            _section("Market indicators", lambda: _indicators(c)),
            _section("Recent council cycles (newest first)",
                     lambda: _recent_cycles(c)),
            _section("Recent off-schedule wakes (W1/W2)",
                     lambda: _recent_wakes(c)),
            _section("Vital signs (fills)", lambda: _vital_signs(c)),
            _section("Daily P&L (rough)", lambda: _pnl(c)),
        ]
        return "\n".join(p for p in parts if p)
    finally:
        c.close()


def load_glossary():
    with open(GLOSSARY_PATH, "r", encoding="utf-8") as f:
        return f.read()


def ask_ollama(system, user):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))["message"]["content"]


def answer(question):
    if not os.path.exists(DB_PATH):
        return (f"No database found at {DB_PATH}. Run pull_db.ps1 first to copy "
                f"observer.db down from the droplet.")
    system = load_glossary() + "\n\n---\n\n" + build_context()
    return ask_ollama(system, question)


def main():
    if len(sys.argv) > 1:                       # one-shot
        print(answer(" ".join(sys.argv[1:])))
        return
    print(f"MAGI ask-it  (model={MODEL}, db={DB_PATH})")
    print("Ask about the bot. Empty line or Ctrl-C to quit.\n")
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            break
        try:
            print("\n" + answer(q) + "\n")
        except Exception as e:
            print(f"\n[error talking to Ollama: {e}]\n"
                  f"Is the Ollama server running? Try: ollama run {MODEL}\n")


if __name__ == "__main__":
    main()
