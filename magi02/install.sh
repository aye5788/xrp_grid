#!/usr/bin/env bash
# MAGI-02 desktop install — one-time, a few minutes. Stdlib-only (python3 is
# the only requirement; the miner additionally wants a local Ollama).
#
#   ./install.sh gcs      # or: ./install.sh rsync
#
# Registers a nightly cron job (05:00 local; the GCS snapshot lands 04:10
# UTC — adjust NIGHTLY_HOUR below if your timezone puts 05:00 before that):
# fetch snapshot -> run falsifier -> log report. The miner is NOT scheduled:
# run it manually after doc changes (it drafts proposals for YOUR review).
set -euo pipefail

MODE="${1:?usage: install.sh [gcs|rsync]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
NIGHTLY_HOUR="${NIGHTLY_HOUR:-5}"

command -v python3 >/dev/null || { echo "python3 required"; exit 1; }
if [ "$MODE" = gcs ]; then
  command -v gsutil >/dev/null || {
    echo "gsutil required for gcs mode (install google-cloud-cli, then"
    echo "  gcloud auth activate-service-account --key-file=READONLY_KEY.json)"
    exit 1; }
fi

# Bake rsync's SSH target into the cron line — cron runs with a bare
# environment, so an exported MAGI_DROPLET_SSH would work interactively and
# then silently fall back to the placeholder at 05:00.
ENV_PREFIX=""
if [ "$MODE" = rsync ]; then
  : "${MAGI_DROPLET_SSH:?rsync mode: export MAGI_DROPLET_SSH=root@<droplet-ip> before installing}"
  ENV_PREFIX="MAGI_DROPLET_SSH=$MAGI_DROPLET_SSH "
fi

CRON_LINE="0 $NIGHTLY_HOUR * * * cd $HERE && ${ENV_PREFIX}./fetch_snapshot.sh $MODE && python3 falsifier.py --db observer_snapshot.db >> nightly.log 2>&1"
( crontab -l 2>/dev/null | grep -v "magi02/falsifier\|fetch_snapshot" ; echo "$CRON_LINE" ) | crontab -
chmod +x "$HERE/fetch_snapshot.sh"

echo "Installed. Nightly at 0$NIGHTLY_HOUR:00 local: fetch + falsify -> $HERE/nightly.log"
echo "Smoke test now:  ./fetch_snapshot.sh $MODE && python3 falsifier.py --db observer_snapshot.db"
echo "Miner (manual, needs Ollama):  python3 miner.py --docs path/to/CLAUDE.md"
echo "(Windows: no cron — schedule the same two commands in Task Scheduler.)"
