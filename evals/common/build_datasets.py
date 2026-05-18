"""
One-shot dataset builder. Run with the eval venv:
    /root/xrp_grid/evals/.venv/bin/python evals/common/build_datasets.py

Writes:
    evals/casper/dataset.jsonl     (8 scenarios)
    evals/melchior/dataset.jsonl   (9 scenarios)
    evals/balthasar/dataset.jsonl  (9 scenarios)

Each sample is a JSON object on its own line with:
    id              — stable integer
    input           — the literal production R0 prompt (council.py:_r0_prompt)
    ground_truth    — the persona-decision-tree-prescribed position
    agent_args.world_state — the synthetic state the factory will inject into
                             the agent's world_state memory block
    tags            — [agent_name, scenario_category]
    metadata.persona_rule_cited — exact persona-file rule that prescribes
                                   the ground_truth (operator spot-check key)
    metadata.source — "persona_example_A" / "claude_md_pattern" / etc.

The R0 prompt is mirrored from magi/council.py:_r0_prompt verbatim so eval
agents see exactly what production agents see.
"""
from __future__ import annotations

import json
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent.parent


def r0_prompt(cycle_id: str) -> str:
    # Mirrors magi/council.py:_r0_prompt EXACTLY (do not drift)
    return (
        f"Cycle {cycle_id}. World state has been updated in your context "
        f"window.\n\n"
        f"BEFORE DECIDING: read your self_model block.\n\n"
        f"If your self_model entry says you have been wrong about this kind "
        f"of call in the past, your DEFAULT must be to revise away from "
        f"that prior failure mode. To override the self_model warning and "
        f"vote the same way again, you MUST cite a specific world_state "
        f"field name and value that meaningfully differentiates today from "
        f"the conditions the self_model describes — for example, 'roc_6h "
        f"has flipped to +0.4 vs the prior negative regime', not 'momentum "
        f"is different'. Naming the self_model conflict in key_evidence "
        f"without resolving it (either by revising your vote or by citing "
        f"a concrete differentiating datum) is not acceptable and will be "
        f"treated as a non-response.\n\n"
        f"If your self_model entry supports your call, cite it briefly in "
        f"key_evidence prefixed with 'self_model:'. If self_model is empty "
        f"or no entry applies, proceed normally — do not invent a "
        f"reflection just to satisfy this rule.\n\n"
        f"Respond ONLY with a single JSON object on one line, no preamble, "
        f"no markdown fences: "
        f'{{"position": "<one of your valid actions>", '
        f'"conviction": <float 0.0-1.0>, '
        f'"key_evidence": [<3-5 short strings citing specific indicators/data '
        f'from world_state; prefix any self_model citation with '
        f"'self_model:'; if you are overriding a self_model warning, one "
        f"evidence entry must name the specific world_state field and "
        f"value that justifies the override>], "
        f'"crux": "<one sentence: the single thing that would change your '
        f'mind>"}}. After responding, you may use core_memory tools to '
        f"append a new observation to your self_model block if this cycle "
        f"taught you something worth recording."
    )


def make_sample(sample_id, agent, scenario_tag, ground_truth, world_state,
                rule_cited, source):
    cycle_id = f"eval_{agent}_{sample_id:03d}"
    return {
        "id": sample_id,
        "input": r0_prompt(cycle_id),
        "ground_truth": ground_truth,
        "agent_args": {"world_state": world_state},
        "tags": [agent, scenario_tag],
        "metadata": {
            "persona_rule_cited": rule_cited,
            "source": source,
            "cycle_id": cycle_id,
        },
    }


# ----- world_state builders (DRY) -----

def ws_casper(indicators_extra=None):
    """Casper-relevant world_state. Casper reads world_state.indicators only.
    Includes the other top-level fields with defaults so the block parses
    cleanly even though Casper doesn't use them."""
    indicators = {
        "current_price": None,
        "ema_50": None, "ema_200": None,
        "adx": None, "adx_pos": None, "adx_neg": None,
        "autocorr_1h": None, "autocorr_4h": None,
        "roc_6h": None,
        "bb_width": None, "bb_upper": None, "bb_lower": None,
        "btc_ema_50": None, "btc_ema_200": None,
        "atr_percentile": None,
    }
    if indicators_extra:
        indicators.update(indicators_extra)
    return {
        "timestamp": "2026-05-17T18:00:00",
        "price": indicators.get("current_price"),
        "indicators": indicators,
        "grid_state": {},
        "inventory": {},
        "open_orders": {"buy_count": 5, "sell_count": 5},
        "hours_since_last_fill": 1.0,
        "hours_since_last_rebuild": 2.0,
        "trajectory": [],
        "market_knowledge": None,
        "hard_rules": {},
    }


def ws_melchior(indicators, grid_state, open_orders, hours_fill,
                hours_rebuild, trajectory=None):
    return {
        "timestamp": "2026-05-17T18:00:00",
        "price": indicators.get("current_price", 1.42),
        "indicators": indicators,
        "grid_state": grid_state,
        "inventory": {"inventory_skew": 0.0},
        "open_orders": open_orders,
        "hours_since_last_fill": hours_fill,
        "hours_since_last_rebuild": hours_rebuild,
        "trajectory": trajectory or [],
        "market_knowledge": None,
        "hard_rules": {},
    }


def ws_balthasar(inventory, open_orders, indicators=None):
    return {
        "timestamp": "2026-05-17T18:00:00",
        "price": (indicators or {}).get("current_price", 1.42),
        "indicators": indicators or {
            "vol_regime": "LOW", "vwap_dev_pct": -0.10, "atr_percentile": 14,
        },
        "grid_state": {},
        "inventory": inventory,
        "portfolio": {
            "total_universe_usd": inventory.get("total_universe_usd"),
            "xrp_value_usd":      inventory.get("xrp_value_usd"),
            "xrp_pct_of_universe": (
                inventory.get("xrp_value_usd", 0)
                / inventory.get("total_universe_usd", 1)
                if inventory.get("total_universe_usd") else None
            ),
            "allocation_skew":    inventory.get("inventory_skew"),
        },
        "open_orders": open_orders,
        "hours_since_last_fill": 1.0,
        "hours_since_last_rebuild": 2.0,
        "trajectory": [],
        "market_knowledge": None,
        "hard_rules": {},
    }


# ===== CASPER (8 scenarios) =====

CASPER = [
    # 1. Persona Example A — RANGING under stale bearish base
    make_sample(
        1, "casper", "ranging_stale_bearish_base", "RANGING",
        ws_casper({
            "current_price": 1.41, "ema_50": 1.42, "ema_200": 1.77,
            "adx": 19.5, "adx_pos": 23.7, "adx_neg": 14.2,
            "roc_6h": 0.05, "autocorr_1h": 0.02, "autocorr_4h": 0.04,
            "atr_percentile": 13,
        }),
        "Casper STEP 3 — |ema_distance_pct|=20.3 BUT STEP 1 cond 4 fails on "
        "all three momentum checks (roc_6h=0.05 fails -0.3, adx_pos>adx_neg, "
        "autocorr_1h=0.02 fails 0.15). Stale base, not a trend.",
        "persona_example_A",
    ),
    # 2. Persona Example B — TRENDING bearish (active)
    make_sample(
        2, "casper", "trending_bearish_active", "TRENDING",
        ws_casper({
            "current_price": 1.05, "ema_50": 1.10, "ema_200": 1.30,
            "adx": 28, "adx_pos": 14, "adx_neg": 29,
            "roc_6h": -0.6, "autocorr_1h": 0.22, "autocorr_4h": 0.18,
            "atr_percentile": 55,
        }),
        "Casper STEP 1 — all 4 conditions hold (distance -19.2%, bearish "
        "stack, price below EMA200, momentum confirmed by roc_6h<=-0.3 AND "
        "adx_neg>adx_pos AND autocorr_1h>0.15).",
        "persona_example_B",
    ),
    # 3. TRENDING bullish (mirror of Example B)
    make_sample(
        3, "casper", "trending_bullish_active", "TRENDING",
        ws_casper({
            "current_price": 1.85, "ema_50": 1.75, "ema_200": 1.40,
            "adx": 26, "adx_pos": 29, "adx_neg": 12,
            "roc_6h": 0.5, "autocorr_1h": 0.18, "autocorr_4h": 0.20,
            "atr_percentile": 60,
        }),
        "Casper STEP 1 bullish — distance +32%, bullish stack, price above "
        "EMA200, three momentum signals agree (roc_6h>=+0.3, adx_pos>adx_neg, "
        "autocorr_1h>0.15).",
        "persona_decision_tree",
    ),
    # 4. STEP 2 biased chop escalation — low ADX but structure+momentum agree
    make_sample(
        4, "casper", "step2_biased_chop_bullish", "TRENDING",
        ws_casper({
            "current_price": 1.65, "ema_50": 1.50, "ema_200": 1.20,
            "adx": 17, "adx_pos": 22, "adx_neg": 12,
            "roc_6h": 0.4, "autocorr_1h": 0.08, "autocorr_4h": 0.05,
            "atr_percentile": 40,
        }),
        "Casper STEP 2 — distance +37.5% > 10, bullish stack, adx_pos>adx_neg, "
        "roc_6h sign agrees with EMA direction. Low ADX does not mean "
        "grid-safe when structure and momentum agree.",
        "persona_decision_tree",
    ),
    # 5. STEP 4 UNCERTAIN — flat stack, mixed autocorrelations
    make_sample(
        5, "casper", "uncertain_flat_stack_mixed", "UNCERTAIN",
        ws_casper({
            "current_price": 1.41, "ema_50": 1.40, "ema_200": 1.42,
            "adx": 15, "adx_pos": 15, "adx_neg": 13,
            "roc_6h": -0.05, "autocorr_1h": 0.05, "autocorr_4h": -0.10,
            "atr_percentile": 28,
        }),
        "Casper STEP 4 — distance -0.7% within ±3%, ema_50/ema_200 within 2%, "
        "autocorrelations one positive one negative.",
        "persona_decision_tree",
    ),
    # 6. STEP 0 MISSING DATA → UNCERTAIN low conviction
    make_sample(
        6, "casper", "missing_all_indicators", "UNCERTAIN",
        ws_casper({
            "current_price": 1.41,
            "ema_50": None, "ema_200": None,
            "adx": None, "adx_pos": None, "adx_neg": None,
            "roc_6h": None, "autocorr_1h": None, "autocorr_4h": None,
            "atr_percentile": None,
        }),
        "Casper STEP 0 — adx, ema_50, ema_200, and roc_6h all NULL.",
        "persona_decision_tree",
    ),
    # 7. CURRENT DIVERGENCE PATTERN — Casper most-recent failure mode
    make_sample(
        7, "casper", "ranging_current_divergence", "RANGING",
        ws_casper({
            "current_price": 1.42, "ema_50": 1.50, "ema_200": 1.68,
            "adx": 18, "adx_pos": 21, "adx_neg": 15,
            "roc_6h": -0.10, "autocorr_1h": 0.03, "autocorr_4h": 0.05,
            "atr_percentile": 14,
        }),
        "Casper STEP 3 — distance -15.5% deep but Step 1 cond 4 fails "
        "(roc_6h=-0.10 fails -0.3; adx_pos>adx_neg fails; autocorr_1h=0.03 "
        "fails 0.15). CLAUDE.md §8: 'STALE base, not a trend' — exact case "
        "Casper has historically miscalled as TRENDING.",
        "claude_md_pattern",
    ),
    # 8. RANGING — true balanced chop near EMA200
    make_sample(
        8, "casper", "ranging_true_chop", "RANGING",
        ws_casper({
            "current_price": 1.45, "ema_50": 1.45, "ema_200": 1.44,
            "adx": 12, "adx_pos": 12, "adx_neg": 11,
            "roc_6h": 0.02, "autocorr_1h": -0.05, "autocorr_4h": 0.03,
            "atr_percentile": 22,
        }),
        "Casper STEP 3 — |distance|=0.7%, ADX<20, no dominant directional "
        "pressure, autocorrs mixed near zero.",
        "persona_decision_tree",
    ),
]

# ===== MELCHIOR (9 scenarios) =====

MELCHIOR = [
    # 1. Persona Example A — TIGHTEN (current bot state)
    make_sample(
        1, "melchior", "tighten_low_vol_max_spacing_stale", "TIGHTEN",
        ws_melchior(
            indicators={
                "vol_regime": "LOW", "atr_percentile": 13,
                "vwap_dev_pct": -0.10, "atr": 0.01,
                "autocorr_1h": 0.02, "autocorr_4h": 0.04,
                "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.025,
                        "pause_longs": False, "pause_shorts": False},
            open_orders={"buy_count": 6, "sell_count": 3},
            hours_fill=48, hours_rebuild=2.0,
            trajectory=[{"fills_per_hour": 0.0, "skew_delta": 0.0,
                         "hours_active": 12}],
        ),
        "Melchior STEP 3 HIGH band — spacing_pct=0.025 ≥ 1.5%, vol_regime=LOW "
        "AND inactive (hours_since_last_fill=48>12) AND |vwap_dev_pct|=0.10 "
        "≤ 1.5 → TIGHTEN. The grid is starving on a wide ladder.",
        "persona_example_A",
    ),
    # 2. Persona Example B — MAINTAIN (cooldown carve-out)
    make_sample(
        2, "melchior", "maintain_cooldown_carveout", "MAINTAIN",
        ws_melchior(
            indicators={
                "vol_regime": "LOW", "atr_percentile": 14,
                "vwap_dev_pct": -0.30, "autocorr_1h": 0.05,
                "autocorr_4h": 0.02, "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.025,
                        "pause_longs": False, "pause_shorts": False},
            open_orders={"buy_count": 6, "sell_count": 3},
            hours_fill=12, hours_rebuild=0.5,
        ),
        "Melchior STEP 1 cooldown carve-out — recently_built=true (0.5<1.0) "
        "AND book_healthy=true (6≥3 and 3≥2). Return MAINTAIN regardless of "
        "other flags.",
        "persona_example_B",
    ),
    # 3. STEP 1 RECENTRE — one-sided book
    make_sample(
        3, "melchior", "recentre_one_sided_book", "RECENTRE",
        ws_melchior(
            indicators={
                "vol_regime": "LOW", "atr_percentile": 14,
                "vwap_dev_pct": -0.05, "autocorr_1h": 0.02,
                "autocorr_4h": -0.03, "inventory_skew": -0.2,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.020},
            open_orders={"buy_count": 0, "sell_count": 8},
            hours_fill=4, hours_rebuild=3.0,
        ),
        "Melchior STEP 1 — one_sided=true (buy_count==0). Highest-precedence "
        "RECENTRE trigger.",
        "persona_decision_tree",
    ),
    # 4. STEP 1 RECENTRE — buy-heavy book
    make_sample(
        4, "melchior", "recentre_buy_heavy", "RECENTRE",
        ws_melchior(
            indicators={
                "vol_regime": "MEDIUM", "atr_percentile": 50,
                "vwap_dev_pct": 0.5, "autocorr_1h": 0.05,
                "autocorr_4h": 0.10, "inventory_skew": 0.1,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.015},
            open_orders={"buy_count": 8, "sell_count": 1},
            hours_fill=6, hours_rebuild=5.0,
        ),
        "Melchior STEP 1 — book_imbalance=(8-1)/9=+0.78 > +0.7 → buy_heavy. "
        "RECENTRE, medium conviction.",
        "persona_decision_tree",
    ),
    # 5. STEP 2 RECENTRE — VWAP drift, book healthy
    make_sample(
        5, "melchior", "recentre_vwap_drift", "RECENTRE",
        ws_melchior(
            indicators={
                "vol_regime": "LOW", "atr_percentile": 20,
                "vwap_dev_pct": 2.5, "autocorr_1h": 0.10,
                "autocorr_4h": 0.05, "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.010},
            open_orders={"buy_count": 4, "sell_count": 4},
            hours_fill=5, hours_rebuild=8.0,
        ),
        "Melchior STEP 2 — |vwap_dev_pct|=2.5 > 1.5, autocorrs not both "
        "strongly positive → centre fix via RECENTRE.",
        "persona_decision_tree",
    ),
    # 6. STEP 3 WIDEN — HIGH vol, low spacing
    make_sample(
        6, "melchior", "widen_high_vol_low_spacing", "WIDEN",
        ws_melchior(
            indicators={
                "vol_regime": "HIGH", "atr_percentile": 80,
                "vwap_dev_pct": 0.20, "autocorr_1h": 0.05,
                "autocorr_4h": 0.02, "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.004},
            open_orders={"buy_count": 5, "sell_count": 5},
            hours_fill=2, hours_rebuild=10.0,
        ),
        "Melchior STEP 3 LOW band — spacing_pct=0.004 < 0.5% AND "
        "vol_regime=HIGH → WIDEN, medium conviction.",
        "persona_decision_tree",
    ),
    # 7. STEP 3 MAINTAIN — MID band, fills above TIGHTEN threshold
    make_sample(
        7, "melchior", "maintain_mid_band_healthy_fills", "MAINTAIN",
        ws_melchior(
            indicators={
                "vol_regime": "LOW", "atr_percentile": 30,
                "vwap_dev_pct": -0.20, "autocorr_1h": 0.04,
                "autocorr_4h": -0.01, "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.012},
            open_orders={"buy_count": 5, "sell_count": 4},
            hours_fill=3, hours_rebuild=6.0,
            trajectory=[{"fills_per_hour": 0.8, "skew_delta": 0.0,
                         "hours_active": 6}],
        ),
        "Melchior STEP 3 MID band — spacing_pct=0.012 in [0.5%, 1.5%], "
        "vol_regime=LOW BUT fills_per_hour=0.8 ≥ 0.5 so TIGHTEN does not "
        "fire. MAINTAIN.",
        "persona_decision_tree",
    ),
    # 8. STEP 1 fall-through then STEP 3 TIGHTEN (the structurally important
    # path: inactive AND small vwap → do NOT RECENTRE, fall through to fix
    # spacing instead)
    make_sample(
        8, "melchior", "tighten_via_fallthrough", "TIGHTEN",
        ws_melchior(
            indicators={
                "vol_regime": "LOW", "atr_percentile": 15,
                "vwap_dev_pct": 0.05, "autocorr_1h": 0.02,
                "autocorr_4h": -0.04, "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.022},
            open_orders={"buy_count": 5, "sell_count": 5},
            hours_fill=15, hours_rebuild=4.0,
            trajectory=[{"fills_per_hour": 0.2, "skew_delta": 0.0,
                         "hours_active": 10}],
        ),
        "Melchior STEP 1: inactive=true (15>12), |vwap_dev_pct|=0.05 ≤ 1.5 → "
        "fall through (NOT RECENTRE). STEP 3 HIGH band: vol_regime=LOW AND "
        "inactive AND vwap small → TIGHTEN. CLAUDE.md §8: 'rebuilding at the "
        "same spacing produces the same dead grid'.",
        "claude_md_pattern",
    ),
    # 9. STEP 0 MISSING DATA → MAINTAIN low conviction
    make_sample(
        9, "melchior", "maintain_missing_data", "MAINTAIN",
        ws_melchior(
            indicators={
                "vol_regime": None, "atr_percentile": None,
                "vwap_dev_pct": None, "autocorr_1h": None,
                "autocorr_4h": None, "inventory_skew": 0.0,
            },
            grid_state={"centre_price": 1.42, "spacing_pct": 0.020},
            open_orders={"buy_count": 5, "sell_count": 5},
            hours_fill=2, hours_rebuild=3.0,
        ),
        "Melchior STEP 0 — vwap_dev_pct, vol_regime, autocorr_1h, autocorr_4h "
        "all NULL → MAINTAIN low conviction.",
        "persona_decision_tree",
    ),
]

# ===== BALTHASAR (9 scenarios) =====

BALTHASAR = [
    # 1. Persona Example A — PAUSE_SHORTS (XRP leg exhausted)
    make_sample(
        1, "balthasar", "pause_shorts_xrp_exhausted", "PAUSE_SHORTS",
        ws_balthasar(
            inventory={
                "xrp_held": 3.5, "usd_held": 65.0, "net_position_usd": 70.10,
                "xrp_value_usd": 5.10, "total_universe_usd": 70.10,
                "inventory_skew": -0.428,
            },
            open_orders={"buy_count": 5, "sell_count": 5},
        ),
        "Balthasar STEP 3 — xrp_value_usd=$5.10 < $10. Balanced book "
        "(no STEP 1 gate); |skew|=0.428 ≤ 0.6 (no STEP 2 action). PAUSE_SHORTS "
        "lets XRP rebuild.",
        "persona_example_A",
    ),
    # 2. Persona Example B — PAUSE_LONGS (USD leg exhausted)
    make_sample(
        2, "balthasar", "pause_longs_usd_exhausted", "PAUSE_LONGS",
        ws_balthasar(
            inventory={
                "xrp_held": 45.0, "usd_held": 4.0, "net_position_usd": 68.35,
                "xrp_value_usd": 64.35, "total_universe_usd": 68.35,
                "inventory_skew": 0.441,
            },
            open_orders={"buy_count": 5, "sell_count": 5},
        ),
        "Balthasar STEP 3 — usd_held=$4 < $10. Balanced book; |skew|=0.441 ≤ "
        "0.6. PAUSE_LONGS lets USD rebuild.",
        "persona_example_B",
    ),
    # 3. STEP 1.1 CLEAR — one-sided book, skew elevated
    make_sample(
        3, "balthasar", "clear_one_sided_book", "CLEAR",
        ws_balthasar(
            inventory={
                "xrp_held": 7.0, "usd_held": 45.0, "net_position_usd": 55.0,
                "xrp_value_usd": 10.0, "total_universe_usd": 55.0,
                "inventory_skew": -0.636,
            },
            open_orders={"buy_count": 0, "sell_count": 8},
        ),
        "Balthasar STEP 1.1 — buy_count==0 → CLEAR overrides Step 2/3 even "
        "though skew is in PAUSE_SHORTS band. Grid is one-sided; Melchior "
        "will RECENTRE.",
        "persona_decision_tree",
    ),
    # 4. STEP 1.4 PAUSE_LONGS — book buy-heavy AND alloc long-heavy
    make_sample(
        4, "balthasar", "pause_longs_step14_combined", "PAUSE_LONGS",
        ws_balthasar(
            inventory={
                "xrp_held": 35.0, "usd_held": 12.0, "net_position_usd": 61.70,
                "xrp_value_usd": 49.70, "total_universe_usd": 61.70,
                "inventory_skew": 0.305,
            },
            open_orders={"buy_count": 15, "sell_count": 2},
        ),
        "Balthasar STEP 1.4 — both sides ≥ 2, order_count_skew=(15-2)/17="
        "+0.76 > +0.7, allocation_skew=+0.305 > +0.3. PAUSE_LONGS.",
        "persona_decision_tree",
    ),
    # 5. STEP 1.5 PAUSE_SHORTS — book sell-heavy AND alloc short-heavy
    make_sample(
        5, "balthasar", "pause_shorts_step15_combined", "PAUSE_SHORTS",
        ws_balthasar(
            inventory={
                "xrp_held": 7.5, "usd_held": 46.0, "net_position_usd": 56.65,
                "xrp_value_usd": 10.65, "total_universe_usd": 56.65,
                "inventory_skew": -0.312,
            },
            open_orders={"buy_count": 2, "sell_count": 15},
        ),
        "Balthasar STEP 1.5 — both sides ≥ 2, order_count_skew=(2-15)/17="
        "-0.76 < -0.7, allocation_skew=-0.312 < -0.3. PAUSE_SHORTS. "
        "xrp_value_usd=$10.65 > $10 so STEP 3 buffer floor does NOT fire.",
        "persona_decision_tree",
    ),
    # 6. STEP 2 HALT — heavy long concentration (skew>0.85)
    make_sample(
        6, "balthasar", "halt_long_concentration", "HALT",
        ws_balthasar(
            inventory={
                "xrp_held": 70.0, "usd_held": 4.0, "net_position_usd": 103.40,
                "xrp_value_usd": 99.40, "total_universe_usd": 103.40,
                "inventory_skew": 0.92,
            },
            open_orders={"buy_count": 5, "sell_count": 5},
        ),
        "Balthasar STEP 2 — allocation_skew=+0.92 > +0.85. Heavy long "
        "concentration → HALT. Balanced book so STEP 1 gates inert.",
        "persona_decision_tree",
    ),
    # 7. STEP 2 PAUSE_LONGS — moderate long concentration
    make_sample(
        7, "balthasar", "pause_longs_step2_moderate", "PAUSE_LONGS",
        ws_balthasar(
            inventory={
                "xrp_held": 50.0, "usd_held": 18.0, "net_position_usd": 89.0,
                "xrp_value_usd": 71.0, "total_universe_usd": 89.0,
                "inventory_skew": 0.70,
            },
            open_orders={"buy_count": 5, "sell_count": 5},
        ),
        "Balthasar STEP 2 — +0.6 < allocation_skew=+0.70 ≤ +0.85 → "
        "PAUSE_LONGS. Balanced book (no STEP 1), buffers fine (no STEP 3 "
        "override).",
        "persona_decision_tree",
    ),
    # 8. STEP 3 PAUSE_LONGS — buffer floor fires under moderate skew
    make_sample(
        8, "balthasar", "pause_longs_step3_buffer", "PAUSE_LONGS",
        ws_balthasar(
            inventory={
                "xrp_held": 20.0, "usd_held": 8.0, "net_position_usd": 36.40,
                "xrp_value_usd": 28.40, "total_universe_usd": 36.40,
                "inventory_skew": 0.560,
            },
            open_orders={"buy_count": 3, "sell_count": 3},
        ),
        "Balthasar STEP 3 — usd_held=$8 < $10. |skew|=0.56 ≤ 0.6 so STEP 2 "
        "did not fire; buffer rule takes precedence per persona note.",
        "persona_decision_tree",
    ),
    # 9. STEP 5 CLEAR — balanced everything (current production state)
    make_sample(
        9, "balthasar", "clear_balanced_baseline", "CLEAR",
        ws_balthasar(
            inventory={
                "xrp_held": 14.0, "usd_held": 47.0, "net_position_usd": 67.0,
                "xrp_value_usd": 20.0, "total_universe_usd": 67.0,
                "inventory_skew": -0.104,
            },
            open_orders={"buy_count": 6, "sell_count": 3},
            indicators={
                "vol_regime": "LOW", "vwap_dev_pct": -0.10,
                "atr_percentile": 14, "current_price": 1.42,
            },
        ),
        "Balthasar STEP 5 default — balanced book (both sides ≥ 2, "
        "|order_skew|=(6-3)/9=0.33 < 0.7), |alloc_skew|=0.104 < 0.6, both "
        "buffers ≥ $10. CLEAR. This is the current live state per "
        "01_CURRENT_STATE.md.",
        "claude_md_pattern",
    ),
]


def write_jsonl(agent: str, samples: list) -> None:
    path = EVALS_DIR / agent / "dataset.jsonl"
    with path.open("w") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(f"wrote {path} ({len(samples)} samples)")


if __name__ == "__main__":
    write_jsonl("casper", CASPER)
    write_jsonl("melchior", MELCHIOR)
    write_jsonl("balthasar", BALTHASAR)
    print(f"\nTotal: {len(CASPER) + len(MELCHIOR) + len(BALTHASAR)} samples")
