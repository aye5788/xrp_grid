# MAGI-02 — off-box promise falsifier + miner

Named for the Matsushiro backup MAGI that cross-examined the primary. This is
plan layer 3 of the proactive bug-catching architecture (see
`02_NEXT_BUILD_TASKS.md`, PLAN 2026-07-02): a nightly desktop job that tests
the system's **documented promises** against the **live data** — the exact
confrontation that has caught every recent real bug, made standing and
automatic.

## What runs nightly

1. `fetch_snapshot.sh` pulls the observer.db snapshot that the droplet's
   `tape-backup.timer` uploads to GCS daily at 04:10 UTC.
2. `falsifier.py` runs every **approved** predicate in `predicates.json`
   against it (read-only URI mode — a predicate physically cannot write).
   Each predicate is a falsifiable claim with its doc citation; its SQL
   returns *violation rows*, so a failure ships its own evidence. Non-zero
   exit + `nightly.log` + `last_report.json` on violations.

## The miner (manual, after doc changes)

`miner.py --docs <changed .md files>` drafts NEW predicates from the docs via
**local Ollama** (zero API spend). Everything it emits lands in
`proposals.json` as `status: proposed` — proposals **cannot alert or fail a
run** until you review one, verify its doc citation, and move it into
`predicates.json` as `approved`. That gate is load-bearing: spec-extraction
research shows LLMs fabricate promises that aren't in the text. Dry-run a
proposal first: `falsifier.py --db snapshot.db --include-proposed`
(proposed predicates report but never fail the run).

## Desktop install (one-time, minutes)

```
git pull
cd magi02
./install.sh gcs     # or: ./install.sh rsync
```

Pick the auth mode:

* **gcs** — install google-cloud-cli, then authenticate with a **dedicated
  read-only service-account key** scoped to the backup bucket only (in GCP:
  create a service account with `roles/storage.objectViewer` on
  `xrp-grid-tape-backups-ayn88`, download its key, then
  `gcloud auth activate-service-account --key-file=key.json`). Never copy the
  droplet's main key — a compromised desktop should be able to read backups
  and nothing else.
* **rsync** — needs SSH to the droplet (`export MAGI_DROPLET_SSH=root@<ip>`);
  takes a WAL-consistent `.backup` snapshot server-side, never the live file.
  Same channel the planned Ollama assistant uses.

Windows: no cron — put the same two commands (`fetch_snapshot.sh` needs WSL
or Git Bash) in Task Scheduler.

## Triage rules (keep the alarm trustworthy)

* A violated predicate means **buggy code OR rotted docs** — both are
  defects here (the docs have a code-blind reader). Fix whichever is wrong;
  if the promise legitimately changed, update the predicate's
  `effective_from` or retire it with a note.
* Any predicate that false-positives **twice** gets demoted to `proposed`
  (report-only) until rewritten — an ignored alarm is worse than none.

## Seed ledger (2026-07-02)

Six approved predicates, each from a promise verified the hard way: one wake
per breach episode (P1), no buys under protective posture (P2), the
STAND_ASIDE work-off ladder actually executes (P3), stance grading stays
alive (P4), the candle pipeline stays alive (P5), decision rows always carry
a stance (P6). All six HOLD on the live db as of seeding; P2/P3 verified to
FIRE on an injected-violation scratch copy.
