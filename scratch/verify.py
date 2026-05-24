"""Independent verification of the claims I made. Reads raw Kraken API +
observer.db only. Recomputes everything; prints the raw numbers so you judge.
Run:  python scratch/verify.py
If any line contradicts what I told you, I was wrong."""
import sqlite3, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from grid.exchanges.kraken import KrakenExchange
from grid.pnl import _fifo_match

k = KrakenExchange('XRP/USD')
px = k.get_current_price()
xrp, usd = k.get_balances()
opens = k._private_post("OpenOrders").get("open") or {}
c = sqlite3.connect("observer.db"); c.row_factory = sqlite3.Row

print(f"price={px}")
# CLAIM: balances ~32.44 XRP + ~$24.06, total ~$68
print(f"[C1] Kraken balances: {xrp:.4f} XRP (${xrp*px:.2f}) + ${usd:.2f} = ${xrp*px+usd:.2f}")
inv = c.execute("SELECT xrp_held,usd_held FROM inventory ORDER BY id DESC LIMIT 1").fetchone()
print(f"     DB inventory:     {inv['xrp_held']:.4f} XRP + ${inv['usd_held']:.2f}  (should match Kraken)")

# CLAIM: exactly 4 open orders, 2 buys + 2 sells
sides = [o['descr']['type'] for o in opens.values()]
print(f"[C2] Kraken open orders: {len(opens)}  (buys={sides.count('buy')} sells={sides.count('sell')})")

# CLAIM: realized PnL ~+$7.28, win rate ~81%, 48 round-trips, 0 unmatched
fills = [dict(r) for r in c.execute(
    "SELECT order_id,side,fill_price,size,fee,filled_at FROM grid_orders "
    "WHERE status='filled' AND fill_price IS NOT NULL ORDER BY filled_at")]
trips, unmatched = _fifo_match(fills)
realized = sum(t['contribution'] for t in trips)
wins = sum(1 for t in trips if t['contribution'] > 0)
print(f"[C3] realized PnL=${realized:.4f}  round-trips={len(trips)}  "
      f"win_rate={100*wins/max(len(trips),1):.1f}%  unmatched_buys={len(unmatched)}")

# CLAIM: no self_model distillation today — last rotation 2026-05-20
mr = c.execute("SELECT timestamp,agent_id FROM memory_rotations ORDER BY id DESC LIMIT 1").fetchone()
print(f"[C4] last memory rotation (self_model distill): {mr['timestamp'] if mr else 'never'}  "
      f"(should be 2026-05-20, NOT today)")

# CLAIM (my own mistake, not hidden): the 20:48 manual cycle's record is MISSING
n_2048 = c.execute("SELECT COUNT(*) FROM debate_records WHERE timestamp LIKE '2026-05-23T20:4%'").fetchone()[0]
print(f"[C5] debate_records rows at 20:4x (the grid-build cycle): {n_2048}  "
      f"(I said this is 0 — my regression dropped it)")
c.close()
