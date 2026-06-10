"""Deterministic real-outcome MEMORY for Casper — gives the agent recall, not a
blank slate each cycle, WITHOUT vendor LLM-consolidation or Vertex billing.

The memory is the truth of the tape: over 8y of XRP/USD, for each daily setup we
record its indicator fingerprint and what a recycling grid ACTUALLY did over the
next 72h (harvest vs bleed — the forward_label.py outcome). At decision time we
retrieve the k most similar past setups and report their real outcomes. That text
is what gets injected into Casper's prompt — controlled, auditable, free recall.

This is the deterministic backend the project scoped (NOT VertexAiMemoryBankService).

Build the corpus once, then query:
    /root/xrp_grid/venv/bin/python optimize/casper/recall.py            # build + demo
"""

import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# NOTE: the heavy deps (observer/ta via build_forward_cases) are imported lazily
# inside build_corpus() only — so the query path (load_corpus/recall/recall_text)
# stays dependency-light and runs in the eval venv.

CORPUS = _HERE / "recall_corpus.json"
DRAFT = _HERE / "forward_cases.draft.jsonl"
STRIDE_H = 72            # one memory per 3 days (daily-resolution indicators);
                         # dense enough for nearest-neighbour recall, fast to build
MIN_I = 210 * 24        # need ~200 daily bars for ema_200
EXCLUDE_DAYS = 30       # never let a query retrieve its own neighbourhood
K = 8


def _fp(emaDist, adx, adx_pos, adx_neg, roc):
    """Normalised fingerprint of a setup (the dims Casper actually reasons over)."""
    di = (adx_neg or 0) - (adx_pos or 0)
    return (emaDist / 20.0, (adx or 0) / 10.0, di / 10.0, (roc or 0) / 3.0)


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def build_corpus(start_i, end_i, append=True):
    """Build memories for bar indices [start_i, end_i) and append to the corpus.
    Chunked so each call returns fast with visible progress."""
    import time
    from build_forward_cases import load_bars, reconstruct_indicators
    from forward_label import simulate, label
    t0 = time.time()
    bars = load_bars()
    bars_t = [(b["timestamp"], b["high"], b["low"], b["close"]) for b in bars]
    n = len(bars)
    start_i, end_i = max(start_i, MIN_I), min(end_i, n - 73)
    corpus = json.loads(CORPUS.read_text()) if (append and CORPUS.exists()) else []
    added = 0
    for i in range(start_i, end_i, STRIDE_H):
        ind = reconstruct_indicators(bars, i)
        if not ind or ind.get("ema_200") is None or ind.get("adx") is None:
            continue
        price, e200 = bars[i]["close"], ind["ema_200"]
        d = simulate(bars_t, i)
        lab, _ = label(d)
        corpus.append({
            "ts": bars[i]["timestamp"][:19],
            "emaDist": round((price - e200) / e200 * 100, 2), "adx": ind["adx"],
            "adx_pos": ind.get("adx_pos"), "adx_neg": ind.get("adx_neg"),
            "roc_6h": ind.get("roc_6h"),
            "label": lab, "alpha": round(d["alpha_pct"], 3),
            "drift": round(d["drift_pct"], 2),
        })
        added += 1
    CORPUS.write_text(json.dumps(corpus), encoding="utf-8")
    last = corpus[-1]["ts"][:10] if corpus else "—"
    nxt = end_i if end_i < n - 73 else "DONE"
    print(f"chunk bars {start_i}-{end_i}: +{added}  total={len(corpus)}  "
          f"through {last}  {time.time()-t0:.0f}s  next_start={nxt}")
    return len(corpus)


def load_corpus():
    if not CORPUS.exists():
        raise SystemExit("no corpus yet — run: recall.py build <start_i> <end_i>")
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _ep(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "").split("+")[0]).timestamp()


def recall(corpus, emaDist, adx, adx_pos, adx_neg, roc, query_ts=None, k=K):
    """Retrieve the k most similar past setups and summarise their real outcomes."""
    q = _fp(emaDist, adx, adx_pos, adx_neg, roc)
    cand = corpus
    if query_ts is not None:
        qe = _ep(query_ts)
        cand = [c for c in corpus if abs(_ep(c["ts"]) - qe) > EXCLUDE_DAYS * 86400]
    ranked = sorted(cand, key=lambda c: _dist(q, _fp(
        c["emaDist"], c["adx"], c["adx_pos"], c["adx_neg"], c["roc_6h"])))[:k]
    harvested = [c for c in ranked if c["label"] == "RANGING"]
    bled = [c for c in ranked if c["label"] == "TRENDING"]
    mean_alpha = sum(c["alpha"] for c in ranked) / len(ranked) if ranked else 0.0
    verdict = ("RANGING" if len(harvested) > len(bled)
               else "TRENDING" if len(bled) > len(harvested) else "MIXED")
    return {"verdict": verdict, "n_harvest": len(harvested), "n_bleed": len(bled),
            "mean_alpha": round(mean_alpha, 3), "analogs": ranked}


def recall_text(rec):
    """The memory string injected into Casper's prompt."""
    n = rec["n_harvest"] + rec["n_bleed"]
    closest = rec["analogs"][0] if rec["analogs"] else None
    s = (f"REAL-OUTCOME MEMORY ({n} most-similar historical XRP setups): "
         f"{rec['n_harvest']}/{n} HARVESTED (grid RANGING-favourable), "
         f"{rec['n_bleed']}/{n} BLED (TRENDING-hostile); "
         f"mean grid alpha {rec['mean_alpha']:+.2f}% over 72h.")
    if closest:
        s += (f" Closest: {closest['ts'][:10]} (ADX {closest['adx']:.0f}, "
              f"{closest['emaDist']:+.0f}% vs EMA200, roc {closest['roc_6h']}) "
              f"-> grid {closest['label']} ({closest['alpha']:+.2f}%).")
    return s


def demo():
    corpus = load_corpus()
    cases = [json.loads(l) for l in DRAFT.read_text().splitlines() if l.strip()]
    print(f"\n=== what real-outcome memory surfaces for each eval case "
          f"(corpus={len(corpus)}, excluding ±{EXCLUDE_DAYS}d) ===")
    hdr = f"{'#':>2} {'date':11} {'truth':9} {'memory_says':11} {'harvest:bleed':13} {'meanα%':>7}  match?"
    print(hdr); print("-" * len(hdr))
    agree = 0
    for c in cases:
        m = c["metadata"]
        ts = c["agent_args"]["world_state"]["timestamp"]
        rec = recall(corpus, m["ema_distance_pct"], m["recon_adx"],
                     m["recon_adx_pos"], m["recon_adx_neg"], m["recon_roc_6h"],
                     query_ts=ts)
        gt = c["ground_truth"]
        ok = rec["verdict"] == gt
        agree += ok
        ratio = f"{rec['n_harvest']}:{rec['n_bleed']}"
        print(f"{c['id']:>2} {ts[:10]:11} {gt:9} {rec['verdict']:11} "
              f"{ratio:13} {rec['mean_alpha']:>7.2f}  {'✓' if ok else '·'}")
    print(f"\nmemory verdict matches reality-label on {agree}/{len(cases)} cases")
    print("\nexample injected memory string (case 11, the bearish-base range Casper mis-called TRENDING):")
    c = next(c for c in cases if c["id"] == 11)
    m = c["metadata"]
    rec = recall(corpus, m["ema_distance_pct"], m["recon_adx"], m["recon_adx_pos"],
                 m["recon_adx_neg"], m["recon_roc_6h"],
                 query_ts=c["agent_args"]["world_state"]["timestamp"])
    print("  " + recall_text(rec))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "build":
        build_corpus(int(a[1]), int(a[2]))
    else:
        demo()
