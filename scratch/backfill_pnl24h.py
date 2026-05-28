"""One-off: recompute fills_24h / pnl_24h for live-era debate_records rows with
the corrected live-scoped FIFO logic. The old all-fills FIFO let paper sells
drain the buy queue, so every live cycle's pnl_24h was 0.0/None.

Dry-run by default; pass --apply to snapshot + write.
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, '/root/xrp_grid')
import observer
from database import get_conn, update_debate_outcomes

GO_LIVE = '2026-05-23T20:49'          # first live Kraken fill
WINDOW_H = 24


def mature(cycle_start):
    return cycle_start + timedelta(hours=WINDOW_H) <= datetime.utcnow()


def main(apply: bool):
    conn = get_conn()
    rows = conn.execute(
        "SELECT cycle_id, timestamp, fills_24h, pnl_24h, outcome_24h_backfilled "
        "FROM debate_records WHERE timestamp >= ? ORDER BY timestamp ASC",
        (GO_LIVE,),
    ).fetchall()
    conn.close()

    snapshot, changes = [], []
    for r in rows:
        cs = observer._parse_iso_safe(r['timestamp'])
        if cs is None or not mature(cs):
            continue
        ce = cs + timedelta(hours=WINDOW_H)
        new_fills, new_pnl = observer._compute_window_metrics(cs, ce)
        old_fills, old_pnl = r['fills_24h'], r['pnl_24h']
        snapshot.append((r['cycle_id'], old_fills, old_pnl))
        if (old_fills, old_pnl) != (new_fills, new_pnl):
            changes.append((r['cycle_id'], r['timestamp'][:19],
                            old_fills, old_pnl, new_fills, new_pnl))

    print(f"live-era mature rows: {len(snapshot)}  |  rows changing: {len(changes)}")
    print(f"{'cycle':<22} {'ts':<20} {'old_f':>5} {'old_pnl':>9} -> {'new_f':>5} {'new_pnl':>9}")
    for cid, ts, of, op, nf, npnl in changes:
        op_s = f"{op:.4f}" if op is not None else "None"
        print(f"{cid:<22} {ts:<20} {str(of):>5} {op_s:>9} -> {nf:>5} {npnl:>9.4f}")

    if not apply:
        print("\nDRY RUN — no writes. Re-run with --apply to commit.")
        return

    import json
    snap_path = f"/tmp/pnl24h_backfill_snapshot_{datetime.utcnow():%Y%m%dT%H%M%S}.json"
    with open(snap_path, 'w') as f:
        json.dump([{'cycle_id': c, 'fills_24h': fi, 'pnl_24h': p}
                   for c, fi, p in snapshot], f, indent=2)
    print(f"\nsnapshot written: {snap_path}")

    for cid, ts, of, op, nf, npnl in changes:
        update_debate_outcomes(cid, '24h', nf, npnl)
    print(f"applied: {len(changes)} rows updated.")


if __name__ == '__main__':
    main('--apply' in sys.argv)
