# =============================================================
# MAGI SYSTEM — CONFLICT DETECTOR
# Pure Python logic — no API calls, no database access.
# Input:  assumption_matrix (dict with keys melchior, balthasar, casper)
# Output: list of conflict dicts
# =============================================================


def detect(assumption_matrix: dict) -> list:
    """
    Detect contradictions between agent assumptions.
    Returns a list of conflict dicts, empty if none found.
    Each conflict has: type, description, roles_involved.
    Conservative — only flags genuine contradictions, not focus differences.
    """
    conflicts = []

    m = assumption_matrix.get("melchior") or {}
    b = assumption_matrix.get("balthasar") or {}
    c = assumption_matrix.get("casper") or {}

    m_btc      = (m.get("btc_direction") or "").lower()
    c_btc      = (c.get("btc_direction") or "").lower()
    m_vol      = (m.get("vol_regime") or "").lower()
    b_vol      = (b.get("vol_regime") or "").lower()
    m_env      = (m.get("risk_environment") or "").lower()
    c_macro    = (c.get("macro_regime") or "").lower()
    m_vote     = (m.get("_vote") or "").lower()
    b_vote     = (b.get("_vote") or "").lower()
    c_vote     = (c.get("_vote") or "").lower()
    m_evidence = [e.lower() for e in (m.get("_key_evidence") or [])]

    DIRECTIONAL = {"bullish", "bearish"}
    VOL_RANK    = {"low": 0, "medium": 1, "high": 2}

    # ── Rule 1: BTC direction disagreement ──────────────────────
    if m_btc in DIRECTIONAL and c_btc in DIRECTIONAL and m_btc != c_btc:
        conflicts.append({
            "type":          "btc_direction_disagreement",
            "description":   (f"Melchior sees BTC as {m_btc} while "
                              f"Casper sees it as {c_btc}"),
            "roles_involved": ["melchior", "casper"],
        })

    # ── Rule 2: Vol regime disagreement ─────────────────────────
    if m_vol in VOL_RANK and b_vol in VOL_RANK:
        if abs(VOL_RANK[m_vol] - VOL_RANK[b_vol]) > 1:
            conflicts.append({
                "type":          "vol_regime_disagreement",
                "description":   (f"Melchior assumes vol_regime={m_vol} but "
                                  f"Balthasar assumes vol_regime={b_vol}"),
                "roles_involved": ["melchior", "balthasar"],
            })

    # ── Rule 3: Risk environment disagreement ───────────────────
    RISK_OPPOSITES = {("risk_on", "risk_off"), ("risk_off", "risk_on")}
    if (m_env, c_macro) in RISK_OPPOSITES:
        conflicts.append({
            "type":          "risk_environment_disagreement",
            "description":   (f"Melchior's risk_environment is {m_env} but "
                              f"Casper's macro_regime is {c_macro}"),
            "roles_involved": ["melchior", "casper"],
        })

    # ── Rule 4: Premise-incoherent consensus ────────────────────
    # All three voted the same non-flat direction, but Melchior and Casper
    # have contradictory BTC reads — they agree for incompatible reasons.
    if (m_vote and b_vote and c_vote
            and m_vote == b_vote == c_vote
            and m_vote != "flat"
            and m_btc in DIRECTIONAL and c_btc in DIRECTIONAL
            and m_btc != c_btc):
        conflicts.append({
            "type":          "premise_incoherent_consensus",
            "description":   (f"All three vote {m_vote} but from contradictory "
                              f"premises — Melchior reads BTC as {m_btc}, "
                              f"Casper reads BTC as {c_btc}"),
            "roles_involved": ["melchior", "balthasar", "casper"],
        })

    # ── Rule 5: Mean reversion / vol mismatch ───────────────────
    # Melchior votes long citing mean reversion in a low-vol regime
    # where mean reversion has no statistical backing.
    if m_vote == "long" and m_vol == "low":
        mr_keywords = ("mean reversion", "reversion", "vwap", "mean-revert")
        if any(kw in ev for ev in m_evidence for kw in mr_keywords):
            conflicts.append({
                "type":          "mean_reversion_vol_mismatch",
                "description":   ("Melchior votes long citing mean reversion "
                                  "but vol_regime is low — mean reversion edge "
                                  "is statistically inactive in low vol"),
                "roles_involved": ["melchior"],
            })

    return conflicts
