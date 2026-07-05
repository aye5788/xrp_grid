#!/usr/bin/env bash
# MAGI-02 nightly runner — the single entry point schedulers call (Windows
# Task Scheduler via `wsl.exe -e .../nightly.sh`, or cron on Linux). Keeping
# the whole command in-repo avoids the shell-quoting hazards of embedding it
# in a scheduler's argument string.
#
# The SSH target lives in ./droplet.env (gitignored — the repo is public, the
# droplet address is not). Create it once:
#   echo 'MAGI_DROPLET_SSH=root@<droplet-ip>' > droplet.env
# gcs mode needs no droplet.env: FETCH_MODE=gcs in droplet.env or env.
set -euo pipefail
cd "$(dirname "$0")"

[ -f droplet.env ] && . ./droplet.env
export MAGI_DROPLET_SSH="${MAGI_DROPLET_SSH:-}"
MODE="${FETCH_MODE:-rsync}"

{
  echo "=== nightly run $(date -u +%FT%TZ) (mode=$MODE)"
  ./fetch_snapshot.sh "$MODE"
  python3 falsifier.py --db observer_snapshot.db
} >> nightly.log 2>&1
