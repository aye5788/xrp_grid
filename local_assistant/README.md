# MAGI local "ask-it" assistant

A small, local, free assistant that explains what the MAGI grid bot is doing in
plain English. It runs entirely on your desktop (Ollama + a local model), reads a
copy of the bot's database pulled from the droplet, and narrates it using
`GLOSSARY.md` as its system knowledge.

It is a **narrator, not an auditor** — it explains the data it's given. It does
not catch bugs, give trading advice, or replace a real review.

## What's in this folder

| file | runs on | what it does |
|------|---------|--------------|
| `GLOSSARY.md`     | desktop | system knowledge injected into every prompt |
| `magi_ask.py`     | desktop | builds the data snapshot + asks the local model |
| `pull_db.ps1`     | desktop | pulls a fresh DB snapshot from the droplet |
| `make_snapshot.sh`| droplet | makes the consistent DB snapshot (called by pull_db.ps1) |

## One-time setup (desktop)

1. **Ollama running with a model.** You already have `llama3.1:8b`. Make sure the
   Ollama app/server is running, then confirm: `ollama list`.
2. **Python 3** installed (`python --version`). Nothing to `pip install` — the
   script uses only the standard library.
3. **Put the files in `C:\magi\`.** Copy `GLOSSARY.md` and `magi_ask.py` (and
   `pull_db.ps1`) into `C:\magi\`. (`make_snapshot.sh` stays on the droplet — it's
   already there under `/root/xrp_grid/local_assistant/`.)

## Use it

```powershell
# 1. pull fresh data (snapshot from the droplet)
powershell -ExecutionPolicy Bypass -File C:\magi\pull_db.ps1

# 2. ask a question (one-shot)
python C:\magi\magi_ask.py "what happened at the noon wake?"

# ...or interactive
python C:\magi\magi_ask.py
```

First run downloads nothing; `llama3.1:8b` is already local. The model loads into
your GPU and answers in a few seconds.

## Keep the data fresh automatically (optional)

Schedule `pull_db.ps1` so the local DB stays current:

1. Open **Task Scheduler** → **Create Basic Task**.
2. Trigger: **Daily**, then on the next screen set **Repeat task every 5 minutes**
   for a duration of **1 day** (and tick "indefinitely" if offered).
3. Action: **Start a program**
   - Program: `powershell.exe`
   - Arguments: `-ExecutionPolicy Bypass -File C:\magi\pull_db.ps1`
4. Finish. The local `observer.db` now refreshes every 5 minutes whenever your
   desktop is on.

You don't strictly need this — you can just run `pull_db.ps1` by hand before
asking a question. The schedule only matters if you want it always-current.

## Config knobs

All optional — set as environment variables, or just edit the top of `magi_ask.py`:

- `MAGI_MODEL`   — Ollama model to use (default `llama3.1:8b`).
- `MAGI_DB`      — path to the local DB copy (default `observer.db` next to the script).
- `MAGI_OLLAMA_URL` — Ollama chat endpoint (default `http://localhost:11434/api/chat`).

## Notes / limits

- The DB copy is a snapshot; it can be a few minutes behind. The assistant says so.
- If `magi_ask.py` says "No database found," run `pull_db.ps1` first.
- If it errors talking to Ollama, the Ollama server isn't running — start it
  (open the Ollama app, or run `ollama run llama3.1:8b` once).
- Detailed paper P&L is the dashboard's job; the assistant only reports the rough
  `pnl_daily` aggregates and will point you to the dashboard for the real figure.
