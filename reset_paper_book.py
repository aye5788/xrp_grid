"""Clean paper-book reset — STAGED for the MAGI restart. Run this ONCE, deliberately,
immediately before `systemctl enable --now magi.service`.

What it does (paper-mode only; never touches real Kraken orders):
  1. Cancel all open paper orders (the stale 11-day book) -> grid_orders marked
     'cancelled', engine.paper_orders cleared.
  2. Rebase paper inventory to the REAL Kraken balances (read-only get_balances)
     and persist the inventory snapshot that startup's load_state restores.
  3. Refresh system_state['paper_run_started_utc'] to NOW -> PnL scope starts fresh
     (all pre-reset fills fall outside the new scope, exactly like the 2026-06-09 reset).
  4. Reset down_walk_streak; assert pause flags are clear.

It deliberately does NOT build the grid: the engine builds a fresh, fee-compliant
grid at startup AFTER run_observer_cycle() backfills the stale candles. On startup the
config fingerprint will differ from the last (arbiter-era) debate_records row, so the
startup gate fires ONE blind-review council cycle on the fresh book — the intended
first real cycle.

Safety: constructs GridEngine(paper=True); cancel/inventory writes are paper-only;
get_balances/get_current_price are read-only. Prints a before/after summary.
"""
import os
from datetime import datetime, timezone

# load .env (symlink -> /root/magi.env)
for line in open("/root/xrp_grid/.env"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from grid.engine import GridEngine
from database import (get_conn, get_system_state, set_system_state,
                      get_current_grid_state)

eng = GridEngine(paper=True)
assert eng.paper, "REFUSING: engine is not in paper mode"

eng.load_state()

# --- BEFORE ---
conn = get_conn()
open_before = conn.execute("SELECT COUNT(*) FROM grid_orders WHERE status='open'").fetchone()[0]
conn.close()
gs = get_current_grid_state() or {}
print("=== BEFORE ===")
print(f"  open paper orders : {open_before}")
print(f"  paper_inventory   : {eng.paper_inventory}")
print(f"  paper_run_started : {get_system_state('paper_run_started_utc')}")
print(f"  pause_longs/shorts: {gs.get('pause_longs')}/{gs.get('pause_shorts')}")
print(f"  down_walk_streak  : {get_system_state('down_walk_streak')}")

# --- 1. cancel the stale book ---
cancelled = eng.cancel_all_orders()
print(f"\n[1] cancelled {cancelled} open paper orders")

# --- 2. rebase inventory to real Kraken balances ---
xrp_real, usd_real = eng.exchange.get_balances()
price = eng.get_current_price()
eng.paper_inventory = {"xrp": float(xrp_real), "usd": float(usd_real)}
eng.update_inventory(price)   # persists the snapshot load_state() will restore
print(f"[2] inventory rebased to Kraken: xrp={xrp_real:.4f} usd=${usd_real:.2f} "
      f"(price=${price})")

# --- 3. refresh PnL scope cutoff ---
now_iso = datetime.now(timezone.utc).isoformat()
set_system_state("paper_run_started_utc", now_iso)
print(f"[3] paper_run_started_utc -> {now_iso}")

# --- 4. reset streak + assert pause flags clear ---
set_system_state("down_walk_streak", "0")
# also clear the down-walk LINK anchors (engine reads '' as "no anchor" -> streak 0),
# so the first post-restart rebuild cannot link to the pre-reset centre/timestamp.
set_system_state("down_walk_last_centre", "")
set_system_state("down_walk_last_ts", "")
gs2 = get_current_grid_state() or {}
if gs2.get("pause_longs") or gs2.get("pause_shorts"):
    print(f"[4] WARNING: pause flags set (longs={gs2.get('pause_longs')} "
          f"shorts={gs2.get('pause_shorts')}) — startup will SKIP the grid rebuild. "
          f"Clear them before starting the service.")
else:
    print("[4] down_walk_streak=0; pause flags clear")

# --- AFTER ---
conn = get_conn()
open_after = conn.execute("SELECT COUNT(*) FROM grid_orders WHERE status='open'").fetchone()[0]
conn.close()
print("\n=== AFTER ===")
print(f"  open paper orders : {open_after}  (engine.paper_orders={len(eng.paper_orders)})")
print(f"  paper_inventory   : {eng.paper_inventory}")
print(f"  paper_run_started : {get_system_state('paper_run_started_utc')}")
print("\nBook reset complete. Next: systemctl enable --now magi.service")
print("(startup will: fund-check -> backfill candles -> build a fresh compliant grid")
print(" around current price -> fire ONE blind-review council cycle on the new book.)")
