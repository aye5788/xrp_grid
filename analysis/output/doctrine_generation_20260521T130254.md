# Doctrine generation — claude-haiku-4-5
Generated: 20260521T130254 UTC
Cycles analysed: 30 (last LAST_N_CYCLES from debate_records)
Usage: input_tokens=3087, output_tokens=2000, approx_cost=$0.0131

---

# Trading-System Telemetry Analysis: XRP/USD Grid Bot

## 1. Override Frequency Analysis

| Override Tag | Count | Observation & Examples |
|---|---|---|
| **GRID_HEALTHY_NO_RECENTRE** | 16 | Dominates the dataset; fires whenever Melchior votes RECENTRE but book remains bilateral and price near centre. Heavy clustering 2026-05-19T12:45–2026-05-20T20:18 (8 consecutive cycles), then again 2026-05-21T12:00–12:18. All outcomes show `fills=0–2, pnl=0.0` during suppression; pattern suggests override is working as designed but may be *too* conservative during weak-trending regimes (Casper=0.70, Melchior conf=0.80). |
| **RECENTRE_COOLDOWN** | 1 | Fires once at 2026-05-19T12:00 (60min after GRID_DEGENERATE-forced rebuild at 08:00). Outcome: `fills=3, pnl=0.0`. Cooldown duration appears appropriate; no collision with later GRID_HEALTHY blocks. |
| **GRID_DEGENERATE** | 1 | Triggers mandatory RECENTRE at 2026-05-19T08:00 (regime entry). Post-rebuild: `fills=1, pnl=0.0, alive=1`. Indicates book was one-sided; rebuild succeeded in restoring bilateral state. |
| **GEOMETRY_INJECTED_FROM_SCORER** | 1 | Fires at 2026-05-19T16:00 when Melchior geometry is null; RECENTRE proceeds with scorer fallback. Outcome: `fills=2, pnl=0.0`. No visible harm, but indicates Melchior confidence (0.80) masks occasional null geometry—audit Melchior null-rate. |
| **No override (–)** | 11 | Clusters at 2026-05-20T16:00 onwards when Melchior downgrades from RECENTRE→MAINTAIN (conf: 0.80→0.70→0.60). Zero failures in this phase; outcomes stable at `fills=0–2, pnl=0.0`. Suggests bot self-corrects gracefully when conviction weakens. |

**Key Pattern:** GRID_HEALTHY_NO_RECENTRE is suppressing ~16 potential recentres across a volatile-to-ranging transition (Casper conf 0.70–0.80 for 24h, then drops to 0.40 at 2026-05-21T08:00). Despite suppression, fill rate does not collapse—but neither does it accelerate. This hints the override may be *delaying* necessary grid realignment during regime uncertainty.

---

## 2. Proposed Rule Modifications

### PROPOSAL 1
**TARGET:** GRID_HEALTHY_NO_RECENTRE  
**PREDICATE:**  
```
IF (override_GRID_HEALTHY_NO_RECENTRE would fire)  
   AND (Casper.regime == RANGING)  
   AND (Casper.confidence < 0.50)  
   AND (cycle_since_last_recentre > 120 minutes)  
THEN allow RECENTRE despite bilateral book
```

**RATIONALE:**  
Cycles 2026-05-21T08:00 onwards show regime transition into RANGING (Casper conf drops to 0.40). GRID_HEALTHY_NO_RECENTRE still blocks Melchior RECENTRE at 2026-05-21T12:00–12:18 despite weakening trend signal. In ranging regimes with low conviction, a stale grid geometry (anchored to prior trend) may *increase* slippage and missed fills. The 120-min threshold prevents thrashing and respects RECENTRE_COOLDOWN intent.

**CONFIDENCE:** MEDIUM.  
*Supporting evidence:* Cycles 2026-05-20T16:00–2026-05-21T04:00 show Melchior organically downshifting from RECENTRE→MAINTAIN as confidence erodes (0.80→0.70→0.60), and fill counts remain stable (1–2/6h). This suggests the bot *can* tolerate MAINTAIN during weak trends. However, we lack 6h-forward outcomes for the final 4 cycles (2026-05-21T08:04, 12:00, 12:18 all show `outcome=pending`). Backfill those before raising to HIGH.

---

### PROPOSAL 2
**TARGET:** GEOMETRY_INJECTED_FROM_SCORER  
**PREDICATE:**  
```
IF (Melchior.geometry == null)  
   AND (Melchior.conviction >= 0.75)  
THEN log ALERT: "high-confidence RECENTRE with null geometry"  
   AND require manual approval OR fallback to MAINTAIN
```

**RATIONALE:**  
Cycle 2026-05-19T16:00 fires GEOMETRY_INJECTED_FROM_SCORER with Melchior conviction=0.80 (high). Null geometry despite high confidence suggests Melchior is hallucinating conviction or failing silently upstream. Injecting scorer fallback masks this failure. One incident is not a crisis, but the absence of subsequent nulls (next 29 cycles all have explicit actions) raises a flag: is 2026-05-19T16:00 an outlier, or is null-geometry already being filtered upstream without logging?

**CONFIDENCE:** LOW.  
*Supporting evidence:* Single occurrence; no pattern. *What would raise to MEDIUM:* (a) log of upstream Melchior null rates; (b) backtest showing GEOMETRY_INJECTED_FROM_SCORER cycles underperform non-injected RECENTRE by >5% fill-weighted PnL; (c) confirmation that scorer fallback geometry differs materially from Melchior's typical learned geometry.

---

### PROPOSAL 3
**TARGET:** RECENTRE_COOLDOWN  
**PREDICATE:**  
```
IF (override_RECENTRE_COOLDOWN would block)  
   AND (Casper.confidence > 0.75)  
   AND (B.conviction == CLEAR AND B.confidence > 0.70)  
THEN reduce cooldown from 60min → 30min
```

**RATIONALE:**  
Cycle 2026-05-19T08:00–12:00 shows GRID_DEGENERATE forces rebuild, then RECENTRE_COOLDOWN blocks a second rebuild 4h later despite sustained high regime conviction (Casper=0.80, B=CLEAR/0.72). Fill count during cooldown window (2026-05-19T12:00): 3 fills, same as post-forced-rebuild. Halving cooldown when regime and risk signals are in high agreement could permit faster adaptation to secondary degeneracies without thrashing. The 30-min floor still prevents subcycle oscillation.

**CONFIDENCE:** MEDIUM.  
*Supporting evidence:* Cycles 2026-05-19T08:00–12:49 show zero grid-degeneracy signals after the initial one; no thrashing is visible. *What would raise to HIGH:* (a) ablation test: simulate 30-min cooldown on historical 2-week window, measure fill-rate and recentre frequency; (b) confirm GRID_DEGENERATE fires >2× per week on average (if rare, cooldown length is not a bottleneck).

---

## 3. What You Cannot Tell From This Data

1. **Missing world_state fields:**
   - `book.bid_depth, book.ask_depth, book.skew`: GRID_HEALTHY_NO_RECENTRE rationale mentions "bilateral" but no explicit depth/skew metrics appear. Cannot validate whether book is truly balanced.
   - `price.distance_from_grid_centre`, `grid.age_seconds`: Would clarify why GRID_HEALTHY_NO_RECENTRE fires so persistently and whether stale grids are accumulating cost.
   - `Melchior.geometry` (explicit): You log "GEOMETRY_INJECTED_FROM_SCORER" but never show Melchior's actual grid geometry. Cannot audit drift vs. scorer fall