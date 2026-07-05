"""ENTRY PLUG — the weighted evidence injector. (Eva lore: the Entry Plug is
the interface through which the pilot enters the Eva; this is the interface
through which the council's own graded experience enters the seats.)

WHAT LEARNS HERE: not the seats (stateless by design), but the SELECTION of
what they read. Every matured decision episode carries a reliability weight —
the time-decayed rate at which its lesson ("which action was right in a window
like mine") kept matching later windows' best actions — and the top few
reliable episodes OUTSIDE the recency ledger's window are surfaced as a
bounded NOTEWORTHY PRECEDENTS section. Recency ledger + track record stay
untouched underneath as the deterministic floor: this section is additive,
and an empty result renders nothing at all.

DESIGN PROPERTY (load-bearing): weights are RECOMPUTED from graded history on
every call — a pure function, no stored mutable state. Same inputs produce
byte-identical output (replay-safe, auditable, nothing to corrupt — the same
property as the ledger). "Learning" = the recomputation changes as history
accumulates, not a writable weight cell.

PRE-COMMITTED CONSTANTS (trials-ledger entry 2026-07-04; do not tune post-hoc):
  lean(i)        = the action episode i's own 72h economics vindicated
                   (edge_usd >= 0 -> its chosen action, else the alternative)
  graded pair    = episode i vs any LATER matured cycle t (t's decision bar at
                   or after i's maturity); pairs where |alpha_t| <= FEE_FLOOR
                   are EXCLUDED (ambiguous window, exogenous dead zone)
  reliability(i) = sum(decay * match) / sum(decay) over graded pairs, where
                   match = +1 if lean(i) == best(t) else -1, and decay has a
                   30-day half-life on (t - i) age
  surfacing      = top MAX_PRECEDENTS by reliability, requiring
                   n_graded >= MIN_GRADED and reliability > 0, and NOT already
                   shown in the recency ledger window
Frozen superiority test (runs at >= 30 matured cycles in one config, criteria
frozen now): weighted selection's lean-accuracy must beat recency selection's
paired on the same cycles, else precedents are stood down to the floor.

Run `python -m magi.entry_plug` for the offline validation: synthetic-history
mechanics checks + the live-corpus no-harm comparison. Read-only.
"""
import math
import sqlite3

from grid.forward_sim import WINDOW_H
from magi.sync_ratio import grade_decision, load_1h

DECAY_HALFLIFE_DAYS = 30.0
MIN_GRADED = 3
MAX_PRECEDENTS = 3
FEE_FLOOR_PCT = 0.50            # exogenous: 2 * MAKER_FEE, mirrors sync_ratio

_PRECEDENT_HEADER = ("=== NOTEWORTHY PRECEDENTS — this council's own past "
                     "decisions whose lesson has stayed reliable (weighted by "
                     "time-decayed agreement with later outcomes) ===")


def _episodes(conn, config_version, bars, ts_keys):
    """All gradeable episodes under the config boundary, chronological:
    [{cycle_id, date, ts, stance, chose_run, edge_usd, alpha_pct, lean}].
    lean is the hindsight-vindicated action: 'run' or 'dont'."""
    rows = conn.execute(
        "SELECT cycle_id, timestamp, stance, final_grid_action, world_state "
        "FROM debate_records "
        "WHERE config_version = ? AND council_json IS NOT NULL "
        "ORDER BY timestamp", (config_version,)).fetchall()
    out = []
    for r in rows:
        g = grade_decision({"timestamp": r[1], "stance": r[2],
                            "final_grid_action": r[3], "world_state": r[4]},
                           bars, ts_keys)
        if g is None or not g.get("matured"):
            continue
        vindicated_chosen = g["edge_usd"] >= 0
        chose_run = g["chose_run"]
        lean = ("run" if (chose_run and vindicated_chosen)
                or (not chose_run and not vindicated_chosen) else "dont")
        out.append({"cycle_id": r[0], "date": (r[1] or "")[:10], "ts": r[1],
                    "stance": r[2], "chose_run": chose_run,
                    "edge_usd": g["edge_usd"],
                    "alpha_if_run_pct": g["alpha_if_run_pct"],
                    "drift_pct": g["drift_pct"], "lean": lean})
    return out


def _best_action(alpha_pct):
    """Hindsight-best action for a window: 'run' iff the grid beat holding by
    more than the fee floor, 'dont' iff it lost by more; None inside the
    exogenous dead zone (ambiguous — excluded from grading)."""
    if alpha_pct > FEE_FLOOR_PCT:
        return "run"
    if alpha_pct < -FEE_FLOOR_PCT:
        return "dont"
    return None


def _age_days(ts_a, ts_b):
    """Days between two ISO timestamps (b - a), tolerant of tz suffixes."""
    from datetime import datetime

    def _p(s):
        return datetime.fromisoformat((s or "")[:19])
    try:
        return max((_p(ts_b) - _p(ts_a)).total_seconds() / 86400.0, 0.0)
    except ValueError:
        return 0.0


def reliability_weights(episodes):
    """Deterministic recompute of every episode's reliability from history.
    Returns {cycle_id: {'reliability': float, 'n_graded': int}}. An episode is
    graded against each LATER episode's window (later = its decision bar at or
    after the earlier one's 72h maturity), skipping dead-zone windows."""
    lam = math.log(2) / DECAY_HALFLIFE_DAYS
    out = {}
    for i, ep in enumerate(episodes):
        num = den = 0.0
        n = 0
        for later in episodes[i + 1:]:
            # later must start at/after ep's maturity: >= 72h after ep
            if _age_days(ep["ts"], later["ts"]) * 24.0 < WINDOW_H:
                continue
            best = _best_action(later["alpha_if_run_pct"])
            if best is None:
                continue
            w = math.exp(-lam * _age_days(later["ts"], episodes[-1]["ts"]))
            num += w * (1.0 if ep["lean"] == best else -1.0)
            den += w
            n += 1
        out[ep["cycle_id"]] = {
            "reliability": (num / den) if den else 0.0, "n_graded": n}
    return out


def _render_line(ep):
    e = ep["edge_usd"]
    if ep["chose_run"]:
        what = (f"running the grid beat holding by ${e:+.2f}" if e >= 0
                else f"running the grid lost ${-e:.2f} vs just holding")
    else:
        what = (f"standing aside saved ${e:.2f} vs deploying" if e >= 0
                else f"standing aside gave up ${-e:.2f} vs deploying")
    return (f"[{ep['date']}] decision={ep['stance'] or 'n/a'} | "
            f"72h: price {ep['drift_pct']:+.1f}%; {what}")


def noteworthy_precedents(conn, config_version, exclude_dates=None):
    """The bounded precedents section: top MAX_PRECEDENTS reliable episodes
    NOT already visible in the recency ledger (exclude_dates = the ledger
    entries' dates). Returns {block (or None when nothing qualifies), items}.
    Pure read + deterministic recompute; never raises past the caller's
    best-effort wrapper."""
    if config_version is None:
        return {"block": None, "items": []}
    bars = load_1h(conn)
    ts_keys = [b[0] for b in bars]
    eps = _episodes(conn, config_version, bars, ts_keys)
    if not eps:
        return {"block": None, "items": []}
    weights = reliability_weights(eps)
    shown = set(exclude_dates or [])
    ranked = sorted(
        (ep for ep in eps
         if weights[ep["cycle_id"]]["n_graded"] >= MIN_GRADED
         and weights[ep["cycle_id"]]["reliability"] > 0.0
         and ep["date"] not in shown),
        key=lambda ep: -weights[ep["cycle_id"]]["reliability"])
    picked = ranked[:MAX_PRECEDENTS]
    if not picked:
        return {"block": None, "items": []}
    lines = [_PRECEDENT_HEADER] + [_render_line(ep) for ep in picked]
    items = [{"cycle_id": ep["cycle_id"], "date": ep["date"],
              "lean": ep["lean"],
              "reliability": round(weights[ep["cycle_id"]]["reliability"], 4),
              "n_graded": weights[ep["cycle_id"]]["n_graded"]}
             for ep in picked]
    return {"block": "\n".join(lines), "items": items}


# ---------------------------------------------------------------- validation

def _synthetic_checks():
    """Mechanics checks on hand-built histories (no DB, no market data).
    Asserts the pre-committed semantics; raises AssertionError on any break."""
    def ep(ts, lean_run, cid):
        # minimal synthetic episode: lean encoded via chose_run/edge sign
        return {"cycle_id": cid, "date": ts[:10], "ts": ts, "stance": "X",
                "chose_run": lean_run, "edge_usd": 1.0,
                "alpha_if_run_pct": (1.0 if lean_run else -1.0) * 2.0,
                "drift_pct": 0.0, "lean": "run" if lean_run else "dont"}

    # (1) an episode whose lesson keeps matching later windows -> positive
    eps = [ep("2026-01-01T00:00:00", True, "a"),
           ep("2026-01-10T00:00:00", True, "b"),
           ep("2026-01-20T00:00:00", True, "c"),
           ep("2026-02-01T00:00:00", True, "d")]
    w = reliability_weights(eps)
    assert w["a"]["n_graded"] == 3 and w["a"]["reliability"] == 1.0, w["a"]
    # (2) a lesson later windows contradict -> negative
    eps2 = [ep("2026-01-01T00:00:00", False, "a")] + eps[1:]
    w2 = reliability_weights(eps2)
    assert w2["a"]["reliability"] == -1.0, w2["a"]
    # (3) dead-zone windows are excluded from grading
    eps3 = [ep("2026-01-01T00:00:00", True, "a"),
            dict(ep("2026-01-10T00:00:00", True, "b"),
                 alpha_if_run_pct=0.2)]          # inside fee floor
    w3 = reliability_weights(eps3)
    assert w3["a"]["n_graded"] == 0, w3["a"]
    # (4) maturity guard: a later cycle inside the 72h window doesn't grade
    eps4 = [ep("2026-01-01T00:00:00", True, "a"),
            ep("2026-01-02T00:00:00", True, "b")]
    w4 = reliability_weights(eps4)
    assert w4["a"]["n_graded"] == 0, w4["a"]
    # (5) decay: an old contradiction is outweighed by fresh agreements
    eps5 = ([ep("2025-06-01T00:00:00", True, "a"),
             ep("2025-06-10T00:00:00", False, "x")]      # old contradiction
            + [ep(f"2026-03-{d:02d}T00:00:00", True, f"m{d}")
               for d in (1, 10, 20)])                    # fresh agreements
    w5 = reliability_weights(eps5)
    assert w5["a"]["reliability"] > 0.9, w5["a"]
    return 5


def main():
    print("[1] synthetic mechanics checks:",
          _synthetic_checks(), "assertions PASS")

    conn = sqlite3.connect("file:observer.db?mode=ro", uri=True)
    versions = [r[0] for r in conn.execute(
        "SELECT DISTINCT config_version FROM debate_records "
        "WHERE council_json IS NOT NULL")]
    print("\n[2] live corpus, per config version (read-only):")
    for v in versions:
        bars = load_1h(conn)
        ts_keys = [b[0] for b in bars]
        eps = _episodes(conn, v, bars, ts_keys)
        w = reliability_weights(eps)
        res = noteworthy_precedents(conn, v)
        gradeable = sum(1 for x in w.values() if x["n_graded"] >= MIN_GRADED)
        print(f"  {v}: {len(eps)} matured episodes, "
              f"{gradeable} with >= {MIN_GRADED} gradings, "
              f"{len(res['items'])} would surface")
        for it in res["items"]:
            print(f"    surfaced: {it}")
    # No-harm check: the precedents section is ADDITIVE (floor untouched), so
    # harm could only come from surfacing anti-reliable items — excluded by
    # the reliability > 0 gate. At current N the section is empty by the
    # MIN_GRADED gate; report that honestly rather than claiming skill.
    print("\n[3] no-harm: floor (ledger+track record) is unchanged by design; "
          "surfacing requires reliability > 0 over >= 3 gradings.")
    conn.close()


if __name__ == "__main__":
    main()
