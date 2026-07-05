"""SYNC RATIO — cost-based decision grading: the economic edge of each council
decision. (Eva lore: a pilot's sync ratio measures alignment with the Eva; this
measures how well the council's choices aligned with what the market did.)

For every blind-review council cycle, answer: compared to the alternative it
rejected, how much did the council's choice save or cost? Anchored to the same
reality standard every existing grader uses (grid/forward_sim.simulate — the
recycling-grid forward replay at real fill rules and real maker fees), and to
NO fitted thresholds: the only inputs are the row's own grid geometry and the
exogenous fee floor.

The action space collapses economically to run-the-grid vs don't:
  RUN   (stance DEPLOY / actions MAINTAIN, RECONFIGURE)    -> forward sim alpha
  DON'T (stance STAND_ASIDE / HOLD; actions HALT, PAUSE_*) -> hold the book (0
        grid alpha by definition of alpha-vs-hold)

  edge_pct = alpha(chosen) - alpha(alternative)
           = +sim_alpha if the council ran the grid, else -sim_alpha
  edge_usd = edge_pct x the sim's deployed notional (2 * levels * 1.65 XRP at
             the decision price) — the honest dollar scale of the choice.

Positive edge = the choice beat its alternative. This is the v2 grading basis
the operator mandated 2026-07-04: payoff-asymmetry-aware (a cheap wrong call
grades as a small negative, an expensive one as a large negative), replacing
hit-rate as the LEARNING signal. The binary band-break grades remain untouched
as operator-facing observability.

Phase-1 usage (read-only, writes nothing):
    .venv/bin/python3 -m magi.sync_ratio
prints the acceptance table over the paper run for operator review.
"""
import json
import sqlite3

from grid.forward_sim import simulate, load_1h, WINDOW_H, SPACING_PCT, N_LEVELS

RUN_STANCES = {"DEPLOY"}
DONT_STANCES = {"STAND_ASIDE", "HOLD"}
RUN_ACTIONS = {"MAINTAIN", "RECONFIGURE"}


def _bar_index(ts_keys, timestamp):
    """Index of the decision bar: the last 1h bar at/before the decision
    timestamp (same convention as database._decision_bar_index)."""
    hour_key = (timestamp or "")[:13]
    lo, hi, ans = 0, len(ts_keys) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if ts_keys[mid][:13] <= hour_key:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def grade_decision(row, bars, ts_keys):
    """Grade one debate_records row (dict with timestamp, stance,
    final_grid_action, world_state). Returns a dict or None if ungradeable.

    chose_run is resolved from the stance first (the capital mandate), falling
    back to the applied grid action for stance-less rows."""
    stance = row.get("stance")
    action = row.get("final_grid_action")
    if stance in RUN_STANCES:
        chose_run = True
    elif stance in DONT_STANCES:
        chose_run = False
    elif action in RUN_ACTIONS:
        chose_run = True
    elif action:
        chose_run = False
    else:
        return None

    i = _bar_index(ts_keys, row.get("timestamp"))
    if i is None:
        return None
    matured = i + WINDOW_H < len(bars)

    spacing, levels = SPACING_PCT, N_LEVELS
    try:
        gs = (json.loads(row.get("world_state") or "{}").get("grid_state") or {})
        spacing = float(gs.get("spacing_pct") or spacing)
        levels = int(gs.get("levels") or levels)
    except (TypeError, ValueError):
        pass

    if not matured:
        return {"chose_run": chose_run, "matured": False}

    d = simulate(bars, i, spacing_pct=spacing, n_levels=levels)
    alpha = d["alpha_pct"]                      # run-the-grid vs hold, %
    deployed_usd = 2 * levels * 1.65 * d["p0"]
    edge_pct = alpha if chose_run else -alpha
    return {
        "chose_run": chose_run, "matured": True,
        "alpha_if_run_pct": round(alpha, 3),
        "edge_pct": round(edge_pct, 3),
        "edge_usd": round(edge_pct / 100.0 * deployed_usd, 3),
        "drift_pct": round(d["drift_pct"], 2),
        "deployed_usd": round(deployed_usd, 2),
        "spacing_pct": spacing, "levels": levels,
    }


def grade_paper_run(conn):
    """Grade every blind-review cycle of the current paper run. Read-only."""
    cutoff = conn.execute(
        "SELECT value FROM system_state WHERE key='paper_run_started_utc'"
    ).fetchone()
    cutoff = cutoff[0] if cutoff else "2026-06-26"
    bars = load_1h(conn)
    ts_keys = [b[0] for b in bars]
    rows = conn.execute(
        "SELECT cycle_id, timestamp, trigger, stance, final_grid_action, "
        "world_state FROM debate_records "
        "WHERE council_json IS NOT NULL AND timestamp >= ? ORDER BY timestamp",
        (cutoff,)).fetchall()
    out = []
    for r in rows:
        row = {"timestamp": r[1], "stance": r[3], "final_grid_action": r[4],
               "world_state": r[5]}
        g = grade_decision(row, bars, ts_keys)
        out.append({"cycle_id": r[0], "timestamp": r[1], "trigger": r[2],
                    "stance": r[3], "grade": g})
    return out


def outcome_line(row, bars, ts_keys):
    """Render one decision's factual 72h outcome for the council ledger.
    Facts and dollar edges only — no pass/fail verdicts (the operator's v2
    grading mandate: the seats weigh the asymmetry themselves). Returns None
    when ungradeable or not yet matured (caller renders its own pending text)."""
    g = grade_decision(row, bars, ts_keys)
    if g is None or not g.get("matured"):
        return None
    e = g["edge_usd"]
    if g["chose_run"]:
        what = (f"running the grid beat holding by ${e:+.2f}" if e >= 0
                else f"running the grid lost ${-e:.2f} vs just holding")
    else:
        what = (f"standing aside saved ${e:.2f} vs deploying" if e >= 0
                else f"standing aside gave up ${-e:.2f} vs deploying")
    return f"72h: price {g['drift_pct']:+.1f}%; {what}"


_TRACK_HEADER = ("=== COUNCIL TRACK RECORD — cumulative economics of this "
                 "council's own past choices, by condition (72h-matured; edge "
                 "vs the rejected alternative) ===")
_TRACK_EMPTY = _TRACK_HEADER + "\n(no matured decisions under this configuration yet)"


def track_record_block(conn, config_version, exclude_id=None, exclude_cycle=None):
    """Rendered-block convenience wrapper around track_record() (kept so the
    reviewed Phase-2 call shape and output text stay exactly as approved)."""
    return track_record(conn, config_version, exclude_id, exclude_cycle)["block"]


def track_record(conn, config_version, exclude_id=None, exclude_cycle=None):
    """The condition-bucketed track record: this council's own matured decisions
    aggregated by (tape verdict, exposure cap, choice), cumulative within the
    config boundary (same boundary discipline as get_council_ledger; cumulative
    rather than 21-day-windowed because long memory is this block's purpose).
    Pure SQLite + simulate read — deterministic, no model call.

    Returns {block, buckets, n_matured, total_edge_usd}: `block` is the rendered
    prompt text (explicit empty sentinel when nothing has matured); `buckets` is
    the structured list ({tape, cap, choice, n, edge_usd}) the injection
    flight-recorder logs for credit assignment."""
    empty = {"block": _TRACK_EMPTY, "buckets": [], "n_matured": 0,
             "total_edge_usd": 0.0}
    if config_version is None:
        return empty
    rows = conn.execute(
        "SELECT timestamp, stance, final_grid_action, world_state "
        "FROM debate_records "
        "WHERE config_version = ? AND council_json IS NOT NULL "
        "  AND (? IS NULL OR id != ?) "
        "  AND (? IS NULL OR cycle_id IS NULL OR cycle_id != ?) "
        "ORDER BY timestamp",
        (config_version, exclude_id, exclude_id, exclude_cycle, exclude_cycle),
    ).fetchall()
    if not rows:
        return empty
    bars = load_1h(conn)
    ts_keys = [b[0] for b in bars]
    buckets = {}
    n_mat = 0
    total = 0.0
    for r in rows:
        row = {"timestamp": r[0], "stance": r[1], "final_grid_action": r[2],
               "world_state": r[3]}
        g = grade_decision(row, bars, ts_keys)
        if g is None or not g.get("matured"):
            continue
        try:
            ws = json.loads(r[3] or "{}")
        except (TypeError, ValueError):
            ws = {}
        tape = ((ws.get("tape_verdict") or {}).get("verdict") or "unknown")
        cap = "cap engaged" if (ws.get("exposure_cap") or {}).get("engaged") \
            else "cap off"
        choice = "ran grid" if g["chose_run"] else "stood aside"
        key = (str(tape), cap, choice)
        b = buckets.setdefault(key, {"n": 0, "edge": 0.0})
        b["n"] += 1
        b["edge"] += g["edge_usd"]
        n_mat += 1
        total += g["edge_usd"]
    if not n_mat:
        return empty
    lines = [_TRACK_HEADER]
    bucket_list = []
    for (tape, cap, choice), b in sorted(buckets.items()):
        alt = "holding" if choice == "ran grid" else "deploying"
        lines.append(f"tape {tape.upper()}, {cap} — {choice} {b['n']}x: "
                     f"net ${b['edge']:+.2f} vs {alt}")
        bucket_list.append({"tape": tape, "cap": cap, "choice": choice,
                            "n": b["n"], "edge_usd": round(b["edge"], 3)})
    lines.append(f"all matured decisions ({n_mat}): net ${total:+.2f} "
                 f"vs the rejected alternatives")
    return {"block": "\n".join(lines), "buckets": bucket_list,
            "n_matured": n_mat, "total_edge_usd": round(total, 3)}


def main():
    conn = sqlite3.connect("file:observer.db?mode=ro", uri=True)
    graded = grade_paper_run(conn)
    conn.close()
    total_edge = 0.0
    n_mat = 0
    print(f"{'date/time':17}{'trigger':15}{'choice':13}{'if-run alpha':>13}"
          f"{'edge %':>8}{'edge $':>8}  verdict")
    for g in graded:
        ts = g["timestamp"][:16].replace("T", " ")
        gr = g["grade"]
        if gr is None:
            print(f"{ts:17}{(g['trigger'] or '?'):15}(ungradeable)")
            continue
        choice = "ran grid" if gr["chose_run"] else "stood aside"
        if not gr["matured"]:
            print(f"{ts:17}{(g['trigger'] or '?')[:14]:15}{choice:13}"
                  f"{'—':>13}{'—':>8}{'—':>8}  not matured (72h)")
            continue
        n_mat += 1
        total_edge += gr["edge_usd"]
        verdict = ("saved money vs deploying" if gr["edge_usd"] > 0 and not gr["chose_run"]
                   else "cost vs deploying" if not gr["chose_run"]
                   else "beat holding" if gr["edge_usd"] > 0
                   else "worse than holding")
        print(f"{ts:17}{(g['trigger'] or '?')[:14]:15}{choice:13}"
              f"{gr['alpha_if_run_pct']:>+12.2f}%{gr['edge_pct']:>+7.2f}%"
              f"{gr['edge_usd']:>+8.2f}  {verdict} (drift {gr['drift_pct']:+.1f}%)")
    basis = next((g['grade']['deployed_usd'] for g in graded
                  if g['grade'] and g['grade'].get('deployed_usd')), 0)
    print(f"\nmatured decisions: {n_mat}; cumulative edge vs alternative: "
          f"${total_edge:+.2f} (deployed-notional basis ~${basis:.0f} per decision)")


if __name__ == "__main__":
    main()
