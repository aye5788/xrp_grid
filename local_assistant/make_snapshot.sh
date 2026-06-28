#!/bin/sh
# Stays on the DROPLET. Makes a consistent snapshot of the live (WAL-mode)
# observer.db for the desktop assistant to pull. `.backup` captures committed
# WAL data safely while the trading service is still writing — copying the raw
# file could miss un-checkpointed writes or, rarely, tear a read.
# Invoked over SSH by the desktop's pull_db.ps1.
sqlite3 /root/xrp_grid/observer.db ".backup '/tmp/magi_snapshot.db'"
