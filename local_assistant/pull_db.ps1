# pull_db.ps1 — runs on the DESKTOP. Refreshes the local copy of the bot's
# database from the droplet so magi_ask.py has fresh data.
#
# The live DB is WAL-mode, so we ask the droplet to make a consistent .backup
# snapshot (make_snapshot.sh) and copy THAT, not the raw file. Uses your existing
# SSH key — no password needed (already verified working to this host).
#
# Run manually:   powershell -ExecutionPolicy Bypass -File C:\magi\pull_db.ps1
# Or schedule it every few minutes via Task Scheduler (see README.md).

$ErrorActionPreference = "Stop"
$Droplet = "root@209.97.145.40"
$Dest    = "C:\magi\observer.db"

New-Item -ItemType Directory -Force -Path (Split-Path $Dest) | Out-Null

# 1. consistent snapshot on the droplet (no quoting headaches — it's a script)
ssh -o ConnectTimeout=10 $Droplet "sh /root/xrp_grid/local_assistant/make_snapshot.sh"
# 2. copy it down
scp -o ConnectTimeout=10 "${Droplet}:/tmp/magi_snapshot.db" $Dest
# 3. clean up the remote snapshot
ssh -o ConnectTimeout=10 $Droplet "rm -f /tmp/magi_snapshot.db"

Write-Host ("{0}  pulled snapshot -> {1}" -f (Get-Date -Format o), $Dest)
