#!/usr/bin/env bash
# MAGI-02 snapshot fetch — pulls the nightly observer.db GCS snapshot that
# tape-backup.timer uploads at 04:10 UTC (single rolling object).
#
# Two auth modes — pick ONE at install (see README.md):
#   gcs   : needs gcloud/gsutil + a READ-ONLY service-account key scoped to
#           the backup bucket only (never the droplet's main key).
#   rsync : needs SSH access to the droplet; copies a consistent .backup
#           snapshot, never the live file.
set -euo pipefail

MODE="${1:-gcs}"
DEST="${2:-$(dirname "$0")/observer_snapshot.db}"
BUCKET="gs://xrp-grid-tape-backups-ayn88/observer/observer.db.gz"
DROPLET="${MAGI_DROPLET_SSH:-root@YOUR_DROPLET_IP}"

case "$MODE" in
  gcs)
    gsutil -q cp "$BUCKET" "${DEST}.gz"
    gunzip -f "${DEST}.gz"
    ;;
  rsync)
    # Consistent snapshot on the droplet side first (WAL-safe), then pull.
    ssh "$DROPLET" "sqlite3 /root/xrp_grid/observer.db \".backup '/tmp/observer_snap.db'\""
    rsync -z "$DROPLET:/tmp/observer_snap.db" "$DEST"
    ssh "$DROPLET" "rm -f /tmp/observer_snap.db"
    ;;
  *)
    echo "usage: fetch_snapshot.sh [gcs|rsync] [dest.db]" >&2; exit 2 ;;
esac

echo "snapshot -> $DEST ($(du -h "$DEST" | cut -f1))"
