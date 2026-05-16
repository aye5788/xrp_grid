from flask import Flask, jsonify, render_template_string, request
import logging
import os
from datetime import datetime, timezone
from database import (
    get_latest_indicators, get_current_grid_state,
    get_latest_inventory, get_recent_magi_decisions,
    get_cost_summary, get_cost_today, get_all_shadow_states,
    get_recent_grid_orders, get_best_shadow_from_db, get_fills_today_count
)
from grid.engine import GridEngine
from grid.pnl import get_pnl_snapshot
from magi.costs import get_fixed_monthly_total, FIXED_SUBSCRIPTIONS
from magi.learning import run_learning_cycle
from guardrails import check_all_guardrails, kill_switch_active
from config import KILL_SWITCH_FILE, MAX_INVENTORY_USD

log = logging.getLogger('dashboard')
app = Flask(__name__)
engine = GridEngine(paper=True)
engine.load_state()

# Shared secret for /api/trigger_magi. Set MAGI_TRIGGER_TOKEN in .env to require
# the token as an X-Magi-Token header or ?token= query param. If unset, the
# endpoint remains open — acceptable when access is restricted by network topology
# (e.g. localhost-only). External exposure requires the token to be set.
MAGI_TRIGGER_TOKEN = os.environ.get('MAGI_TRIGGER_TOKEN', '')

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MAGI — XRP Grid Bot</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: monospace; background: #0a0a0a; color: #00ff88; margin: 0; padding: 20px; }
        h1 { color: #00ffcc; border-bottom: 1px solid #00ff88; padding-bottom: 10px; }
        h2 { color: #00ccff; margin-top: 30px; font-size: 1em; text-transform: uppercase; letter-spacing: 2px; }
        .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin: 20px 0; }
        .card { background: #111; border: 1px solid #00ff8844; padding: 15px; border-radius: 4px; }
        .card .label { color: #888; font-size: 0.8em; margin-bottom: 5px; }
        .card .value { color: #00ff88; font-size: 1.4em; font-weight: bold; }
        .card .sub { color: #666; font-size: 0.75em; margin-top: 4px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.8em; }
        th { color: #00ccff; text-align: left; padding: 6px; border-bottom: 1px solid #00ff8844; }
        td { padding: 6px; border-bottom: 1px solid #ffffff11; }
        .RANGING { color: #00ff88; } .TRENDING { color: #ff4444; } .UNCERTAIN { color: #ffaa00; }
        .MAINTAIN { color: #00ff88; } .TIGHTEN { color: #00ccff; } .WIDEN { color: #ffaa00; } .RECENTRE { color: #ff88ff; } .HALT { color: #ff0000; }
        .CLEAR { color: #00ff88; } .PAUSE_LONGS { color: #ffaa00; } .PAUSE_SHORTS { color: #ffaa00; }
        .LOW { color: #00ff88; } .MEDIUM { color: #ffaa00; } .HIGH { color: #ff4444; }
        .APPROVE { color: #00ff88; } .OVERRIDE { color: #ffaa00; }
        .LIVE { color: #ff4444; font-weight: bold; } .SHADOW { color: #888; }
        .status-ok { color: #00ff88; } .status-err { color: #ff4444; }
        .agent-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin: 15px 0; }
        .agent-card { background: #111; border: 1px solid #00ff8833; padding: 12px; border-radius: 4px; }
        .agent-name { color: #00ccff; font-size: 0.75em; margin-bottom: 6px; text-transform: uppercase; }
        .footer { margin-top: 40px; color: #333; font-size: 0.7em; border-top: 1px solid #222; padding-top: 10px; }
        .pnl-pos { color: #00ff88; }
        .pnl-neg { color: #ff4444; }
        .pnl-zero { color: #666; }
        .side-buy { color: #00ff88; }
        .side-sell { color: #ff4444; }
        .status-filled { color: #00ff88; }
        .status-cancelled { color: #ff4444; }
        .status-open { color: #ffaa00; }

        /* ── Phase 5: agent council panels ───────────────────────── */
        .council-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin: 14px 0; }
        .council-card {
            background: #0e0e16;
            border: 1px solid #4488ff66;
            padding: 14px;
            border-radius: 4px;
            position: relative;
        }
        .council-name {
            color: #88aaff; font-size: 0.8em; letter-spacing: 2px;
            text-transform: uppercase; margin-bottom: 6px;
        }
        .council-pos {
            font-size: 1.4em; font-weight: bold; letter-spacing: 2px;
            margin: 4px 0 8px;
        }
        .conv-track {
            height: 6px; background: #1a1a26; border: 1px solid #444;
            border-radius: 2px; overflow: hidden; margin: 6px 0 8px;
        }
        .conv-fill { height: 100%; background: linear-gradient(to right, #4488ff, #66ccff); }
        .conv-pct { color: #88aaff; font-size: 0.75em; margin-left: 6px; }
        .council-crux { color: #ccccdd; font-size: 0.82em; font-style: italic;
            border-left: 2px solid #4488ff; padding-left: 8px; margin: 8px 0; }
        .council-evidence { color: #aaaacc; font-size: 0.75em; line-height: 1.5;
            margin: 6px 0 0 14px; padding: 0; }
        .council-evidence li { margin: 0; }
        .debate-flag-yes { color: #ffaa00; font-weight: bold; }
        .debate-flag-no  { color: #66cc88; }
        .deadlock-banner {
            background: #330000; border: 2px solid #ff4444;
            color: #ff8888; padding: 10px 14px; margin: 12px 0; text-align: center;
            font-weight: bold; letter-spacing: 1px;
        }
        .override-line {
            background: #221a00; border-left: 3px solid #ffaa00;
            color: #ffcc66; padding: 6px 10px; margin: 8px 0; font-size: 0.82em;
        }
        .accuracy-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
        .accuracy-card {
            background: #0a0e0a; border: 1px solid #00aa6644;
            padding: 12px; border-radius: 4px;
        }
        .accuracy-line { font-size: 0.85em; color: #88cc88; margin: 4px 0; }
        .accuracy-line .num { color: #00ff88; font-weight: bold; }
        .accuracy-line .num.bad { color: #ff8866; }
        details.debate-row > summary { cursor: pointer; padding: 4px; }
        details.debate-row > summary:hover { background: #1a1a2a; }
        details.debate-row[open] > summary { background: #161624; }
        .evolution-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
        .evo-card { background: #0c0c14; border: 1px solid #444466;
            padding: 14px; border-radius: 4px; }
        .evo-title { color: #99aacc; font-size: 0.8em; letter-spacing: 1px;
            text-transform: uppercase; margin-bottom: 8px; }
        .attribution-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
        .attribution-card {
            background: #0c0c14; border: 1px solid #444466;
            padding: 14px; border-radius: 4px;
        }
    </style>
</head>
<body>
    <h1>⬡ MAGI — XRP Grid Bot</h1>
    <div style="color:#666; font-size:0.8em; margin-bottom:20px;">
        {{ now }} EST &nbsp;|&nbsp; Auto-refresh 30s &nbsp;|&nbsp;
        <span class="{{ 'status-ok' if scheduler_alive else 'status-err' }}">
            {{ '● SCHEDULER RUNNING' if scheduler_alive else '● SCHEDULER DOWN' }}
        </span>
        &nbsp;|&nbsp;
        <span class="{{ 'status-err' if kill_switch else 'status-ok' }}">
            {{ '⚠ KILL SWITCH ACTIVE' if kill_switch else '● GUARDRAILS OK' if guardrails_ok else '⚠ GUARDRAIL FAILURE' }}
        </span>
    </div>

    {% if not guardrails_ok %}
    <div style="background:#ff000022; border:1px solid #ff4444; padding:12px; border-radius:4px; margin-bottom:20px; color:#ff4444;">
        <strong>GUARDRAIL FAILURES:</strong>
        {% for f in guardrail_failures %}<div style="margin-top:4px;">• {{ f }}</div>{% endfor %}
    </div>
    {% endif %}

    <h2>Market</h2>
    <div class="grid">
        <div class="card">
            <div class="label">XRP/USD Price</div>
            <div class="value">${{ price }}</div>
        </div>
        <div class="card">
            <div class="label">Vol Regime</div>
            <div class="value {{ vol_regime }}">{{ vol_regime }}</div>
            <div class="sub">ATR pct: {{ atr_pct }}</div>
        </div>
        <div class="card">
            <div class="label">VWAP Deviation</div>
            <div class="value">{{ vwap_dev }}%</div>
        </div>
    </div>

    <h2>LIVE CHART</h2>
    <iframe src="/chart"
            style="width:100%; height:480px; border:1px solid #00ff8844;
                   border-radius:4px; background:#0a0a0a;"
            scrolling="no"></iframe>

    <h2>Grid State</h2>
    <div class="grid">
        <div class="card">
            <div class="label">Centre Price</div>
            <div class="value">${{ grid_centre }}</div>
        </div>
        <div class="card">
            <div class="label">Spacing</div>
            <div class="value">{{ grid_spacing }}%</div>
            <div class="sub">{{ grid_levels }} levels</div>
        </div>
        <div class="card">
            <div class="label">Mode</div>
            <div class="value" style="color:{{ '#ffaa00' if paper_mode else '#ff4444' }};">
                {{ 'PAPER' if paper_mode else 'LIVE' }}
            </div>
        </div>
    </div>

    <!-- ── Phase 5 PANEL 1: Agent Council ───────────────────────── -->
    <h2>Agent Council</h2>
    {% if latest_debate %}
    <div style="color:#666; font-size:0.78em; margin-bottom:6px;">
        cycle <span style="color:#88aaff;">{{ latest_debate.cycle_id }}</span>
        &nbsp;|&nbsp; {{ latest_debate.timestamp }}
        &nbsp;|&nbsp; Debate triggered:
        {% if latest_debate.debate_triggered %}
            <span class="debate-flag-yes">YES</span>
            ({{ latest_debate.conflict_pair or '?' }})
        {% else %}
            <span class="debate-flag-no">no</span>
        {% endif %}
    </div>
    {% if latest_debate.deadlock %}
    <div class="deadlock-banner">⚠ DEADLOCK ON LAST CYCLE — HUMAN REVIEW REQUESTED</div>
    {% endif %}
    {% if council_override_tags %}
    <div class="override-line">Hard rule overrides applied: {{ council_override_tags|join(', ') }}</div>
    {% endif %}
    <div class="council-row">
        {% for agent in ['casper', 'melchior', 'balthasar'] %}
        {% set pos = latest_debate[agent + '_r0_position'] or '—' %}
        {% set conv = latest_debate[agent + '_r0_conviction'] or 0 %}
        {% set crux = latest_debate[agent + '_r0_crux'] or '' %}
        {% set evidence = latest_debate[agent + '_r0_evidence_list'] or [] %}
        <div class="council-card">
            <div class="council-name">{{ agent }}</div>
            <div class="council-pos {{ pos }}">{{ pos }}</div>
            <div class="conv-track"><div class="conv-fill" style="width:{{ (conv*100)|round(0)|int }}%;"></div></div>
            <div class="conv-pct">conviction {{ (conv*100)|round(0)|int }}%</div>
            {% if crux %}<div class="council-crux">"{{ crux }}"</div>{% endif %}
            {% if evidence %}
            <ul class="council-evidence">
                {% for e in evidence %}<li>{{ e }}</li>{% endfor %}
            </ul>
            {% endif %}
        </div>
        {% endfor %}
    </div>
    {% else %}
    <div style="color:#666; margin-bottom:18px;">No debate records yet — first cycle pending.</div>
    {% endif %}

    <h2>Paper P&amp;L</h2>
    <div style="font-size:0.85em; color:#666; margin-bottom:8px;">
        Last fill: <span style="color:{{ fill_age_color }};">{{ fill_age_label }}</span>
        {% if fill_stale %}
        <span style="color:#ff4444; margin-left:12px;">
            ⚠ No fills in 24h+ — metrics below describe historical activity, not current operation.
        </span>
        {% endif %}
    </div>
    <div class="grid">
        <div class="card">
            <div class="label">Realized P&amp;L</div>
            <div class="value {{ 'pnl-pos' if pnl_realized >= 0 else 'pnl-neg' }}">${{ pnl_realized_fmt }}</div>
            <div class="sub">{{ pnl_fill_count }} total fills &nbsp;|&nbsp; {{ pnl_matched_trips }} round trips</div>
        </div>
        <div class="card">
            <div class="label">Unrealized P&amp;L</div>
            <div class="value {{ 'pnl-pos' if pnl_unrealized >= 0 else 'pnl-neg' }}">${{ pnl_unrealized_fmt }}</div>
            <div class="sub">{{ pnl_unmatched_buys }} open buy position{{ 's' if pnl_unmatched_buys != 1 else '' }}</div>
        </div>
        <div class="card">
            <div class="label">Total P&amp;L</div>
            <div class="value {{ 'pnl-pos' if pnl_total >= 0 else 'pnl-neg' }}">${{ pnl_total_fmt }}</div>
            <div class="sub">Fees paid: ${{ pnl_fees_fmt }}</div>
        </div>
        <div class="card">
            <div class="label">vs Best Shadow</div>
            <div class="value {{ 'pnl-pos' if live_vs_shadow >= 0 else 'pnl-neg' }}">{{ live_vs_shadow_fmt }}%</div>
            <div class="sub">
                Live: {{ live_pnl_pct_fmt }}%
                &nbsp;/&nbsp;
                Shadow {{ best_shadow_level or '—' }}-lv: {{ best_shadow_pnl_fmt }}%
            </div>
        </div>
        <div class="card">
            <div class="label">Win Rate</div>
            <div class="value">{{ pnl_win_rate }}%</div>
            <div class="sub">
                {% if pnl_avg_per_trip is not none %}
                avg ${{ pnl_avg_per_trip }} / trip
                {% else %}
                no round trips yet
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="label">Activity</div>
            <div class="value">{{ pnl_fills_today }}</div>
            <div class="sub">
                fills today
                {% if pnl_mins_since is not none %}
                &nbsp;|&nbsp; last {{ pnl_mins_since }}m ago
                {% endif %}
            </div>
        </div>
    </div>

    <h2>Inventory</h2>
    <div class="grid">
        <div class="card">
            <div class="label">XRP Held</div>
            <div class="value">{{ xrp_held }}</div>
        </div>
        <div class="card">
            <div class="label">USD Held</div>
            <div class="value">${{ usd_held }}</div>
        </div>
        <div class="card">
            <div class="label">Net Position</div>
            <div class="value">${{ net_position }}</div>
            <div class="sub">Skew: {{ inventory_skew }}</div>
        </div>
    </div>

    <h2>Recent Orders</h2>
    <details>
        <summary style="cursor:pointer; color:#88aaff;">
            {% if recent_orders %}Recent Orders ({{ recent_orders|length }} rows){% else %}Recent Orders (none yet){% endif %}
        </summary>
        {% if recent_orders %}
        <table>
            <tr>
                <th>Time</th>
                <th>Side</th>
                <th>Price</th>
                <th>Size</th>
                <th>Status</th>
                <th>Fill Price</th>
                <th>Fee</th>
                <th>P&amp;L</th>
            </tr>
            {% for o in recent_orders %}
            {% set order_pnl = order_pnl_map.get(o.order_id) %}
            <tr>
                <td style="color:#666;">{{ (o.filled_at or o.timestamp or '')[:16] }}</td>
                <td class="side-{{ o.side }}">{{ o.side }}</td>
                <td>${{ '%.4f'|format(o.price or 0) }}</td>
                <td>{{ '%.2f'|format(o.size or 0) }}</td>
                <td class="status-{{ o.status }}">{{ o.status }}</td>
                <td>{{ '$%.4f'|format(o.fill_price) if o.fill_price else '—' }}</td>
                <td>{{ '$%.5f'|format(o.fee) if o.fee else '—' }}</td>
                <td class="{{ 'pnl-pos' if order_pnl and order_pnl > 0 else ('pnl-neg' if order_pnl and order_pnl < 0 else 'pnl-zero') }}">
                    {{ '$%.4f'|format(order_pnl) if order_pnl is not none else '—' }}
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div style="color:#666; font-size:0.8em; margin-top:8px;">No orders recorded yet — starts after first MAGI cycle.</div>
        {% endif %}
    </details>

    <h2>Shadow Grid Variants</h2>
    {% if shadow_variants %}
    <table>
        <tr>
            <th>Variant</th>
            <th>Fills (24h)</th>
            <th>Rolling P&amp;L%</th>
            <th>Status</th>
        </tr>
        {% for sv in shadow_variants %}
        <tr>
            <td style="{{ 'color:#00ff88; font-weight:bold;' if sv.level_count == active_levels else '' }}">
                {{ sv.level_count }}-level{{ ' ★' if sv.level_count == active_levels else '' }}
            </td>
            <td>{{ sv.fill_count }}</td>
            <td style="{{ 'color:#00ff88;' if (sv.rolling_pnl_pct or 0) >= 0 else 'color:#ff4444;' }}">
                {{ '%.4f'|format(sv.rolling_pnl_pct or 0) }}%
            </td>
            <td style="{{ 'color:#00ff88;' if sv.level_count == active_levels else 'color:#666;' }}">
                {{ 'ACTIVE' if sv.level_count == active_levels else 'shadow' }}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <div style="color:#666; font-size:0.8em;">Shadow simulation not yet initialised — starts after first observer cycle.</div>
    {% endif %}

    <h2>Costs</h2>

    <div class="grid">
        <div class="card">
            <div class="label">Today's LLM Spend</div>
            <div class="value">${{ cost_today }}</div>
            <div class="sub">{{ calls_today }} calls / {{ tokens_today }} tokens</div>
        </div>
        <div class="card">
            <div class="label">LLM — 30d Actual</div>
            <div class="value">${{ cost_30d }}</div>
            <div class="sub">
                Projected this month: ${{ '%.2f'|format(llm_monthly_projected) }}
                {% if llm_over_budget %}
                <span style="color:#ff4444;"> ⚠ over ${{ llm_monthly_budget }} budget</span>
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="label">DigitalOcean — MTD</div>
            <div class="value">
                ${{ '%.2f'|format(do_mtd) }}
                {% if do_error %}
                <span style="color:#ffaa00; font-size:0.6em;" title="{{ do_error }}"> ⚠ est</span>
                {% endif %}
            </div>
            <div class="sub">
                {% if do_balance is not none %}
                Acct balance: ${{ '%.2f'|format(do_balance) }}
                {% else %}
                Live data unavailable
                {% endif %}
            </div>
        </div>
        <div class="card">
            <div class="label">Total Projected Monthly</div>
            <div class="value {{ 'pnl-neg' if total_projected > llm_monthly_budget + 6 else '' }}">
                ${{ '%.2f'|format(total_projected) }}
            </div>
            <div class="sub">LLM proj + DO MTD</div>
        </div>
    </div>

    <table>
        <tr>
            <th>Agent</th>
            <th>Model</th>
            <th>Calls (30d)</th>
            <th>Tokens (30d)</th>
            <th>Cost (30d)</th>
            <th>Daily Rate</th>
            <th>Credit Left</th>
            <th>Runway</th>
        </tr>
        {% for r in agent_runway %}
        <tr>
            <td>{{ r.agent }}</td>
            <td style="color:#888;">{{ r.model }}</td>
            <td>{{ r.calls }}</td>
            <td>{{ r.tokens }}</td>
            <td>${{ '%.4f'|format(r.cost_30d) }}</td>
            <td>${{ '%.4f'|format(r.daily_rate) }}</td>
            <td>${{ '%.2f'|format(r.credit) }}</td>
            <td style="color:{{ '#ff4444' if r.runway_days < 30 else ('#ffaa00' if r.runway_days < 90 else '#00ff88') }}">
                {% if r.runway_days >= 9999 %}—{% else %}{{ r.runway_days }}d{% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>

    <h2>Manual Actions</h2>
    <div style="margin-top:10px; display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
        <button onclick="triggerMagi(this)" style="background:#00ff8822; color:#00ffcc; border:2px solid #00ff88; padding:10px 24px; font-family:monospace; font-size:1em; font-weight:bold; cursor:pointer; border-radius:4px;">
            Trigger MAGI Cycle
        </button>
        <button onclick="triggerLearning()" style="background:#00ccff11; color:#4488aa; border:1px solid #00ccff33; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            Generate Daily Summary
        </button>
        <button onclick="triggerLearning(true)" style="background:#ffaa0011; color:#887744; border:1px solid #ffaa0033; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            Generate Summary (Weekend Override)
        </button>
        <button onclick="toggleKill()" id="kill-btn" style="background:{{ '#ff000033' if kill_switch else '#33000022' }}; color:{{ '#ff4444' if kill_switch else '#aa3333' }}; border:1px solid {{ '#ff4444' if kill_switch else '#550000' }}; padding:8px 14px; font-family:monospace; font-size:0.8em; cursor:pointer; border-radius:4px;">
            {{ '⬛ DEACTIVATE KILL SWITCH' if kill_switch else '⬛ ACTIVATE KILL SWITCH' }}
        </button>
        <span id="magi-status" style="color:#888; font-size:0.85em;"></span>
        <span id="learning-status" style="color:#888; font-size:0.85em;"></span>
        <span id="kill-status" style="color:#888; font-size:0.85em;"></span>
    </div>
    <script>
    async function triggerMagi(btn) {
        btn.disabled = true;
        btn.textContent = 'Running MAGI cycle...';
        try {
            const r = await fetch('/api/trigger_magi', {
                method: 'POST',
                headers: {'X-Magi-Token': '{{ magi_token }}'}
            });
            const data = await r.json();
            if (data.ok) {
                btn.textContent = 'Cycle complete — refreshing...';
                setTimeout(() => location.reload(), 1500);
            } else {
                btn.textContent = 'Failed: ' + (data.error || 'unknown');
                btn.disabled = false;
            }
        } catch (e) {
            btn.textContent = 'Error: ' + e.message;
            btn.disabled = false;
        }
    }
    function triggerLearning(force) {
        const status = document.getElementById('learning-status');
        status.textContent = 'Running learning cycle...';
        status.style.color = '#ffaa00';
        fetch('/api/trigger_learning' + (force ? '?force=true' : ''), {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                if (data.skipped) {
                    status.textContent = 'Skipped: ' + data.reason;
                    status.style.color = '#888';
                } else {
                    status.textContent = 'Done — ' + data.decisions_count + ' decisions processed';
                    status.style.color = '#00ff88';
                }
            })
            .catch(e => {
                status.textContent = 'Error: ' + e;
                status.style.color = '#ff4444';
            });
    }
    function toggleKill() {
        const status = document.getElementById('kill-status');
        status.textContent = 'Toggling...';
        status.style.color = '#ffaa00';
        fetch('/api/toggle_kill', {method: 'POST'})
            .then(r => r.json())
            .then(data => {
                status.textContent = data.kill_switch ? 'KILL SWITCH ON' : 'Kill switch off';
                status.style.color = data.kill_switch ? '#ff4444' : '#00ff88';
                setTimeout(() => location.reload(), 1000);
            })
            .catch(e => {
                status.textContent = 'Error: ' + e;
                status.style.color = '#ff4444';
            });
    }
    </script>

    <!-- ── Phase 5 PANEL 2: Accuracy Tracker ─────────────────────── -->
    <h2>Council Accuracy</h2>
    <div class="accuracy-grid">
        {% for agent in ['casper', 'melchior', 'balthasar'] %}
        {% set a = council_accuracy[agent] %}
        <div class="accuracy-card">
            <div style="color:#88cc88; font-size:0.78em; letter-spacing:2px;
                        text-transform:uppercase; margin-bottom:6px;">
                {{ agent }}
            </div>
            <div class="accuracy-line">
                7d accuracy: <span class="num">{{ a.acc_7d.accuracy_pct }}%</span>
                <span style="color:#666;">({{ a.acc_7d.positive_outcomes }}/{{ a.acc_7d.total_calls }})</span>
            </div>
            <div class="accuracy-line">
                30d accuracy: <span class="num">{{ a.acc_30d.accuracy_pct }}%</span>
                <span style="color:#666;">({{ a.acc_30d.positive_outcomes }}/{{ a.acc_30d.total_calls }})</span>
            </div>
            <div class="accuracy-line">
                30d capitulation:
                <span class="num {{ 'bad' if a.capit_30d.invalid_revisions > 0 else '' }}">
                    {{ a.capit_30d.invalid_revisions }}/{{ a.capit_30d.total_revisions }}
                </span>
                <span style="color:#666;">({{ a.capit_30d.capitulation_pct }}%)</span>
            </div>
            <div style="margin-top:10px;">
                <div style="color:#666; font-size:0.7em; margin-bottom:4px;">conviction (last 30 cycles)</div>
                {{ conviction_sparklines_svg[agent]|safe }}
            </div>
        </div>
        {% endfor %}
    </div>

    <!-- ── Phase 5 PANEL 4: Evolution ────────────────────────────── -->
    <h2>Council Evolution (30d)</h2>
    <div class="evolution-grid">
        <div class="evo-card">
            <div class="evo-title">Daily agreement rate (% no-debate)</div>
            {{ evolution_agreement_svg|safe }}
            <div style="color:#666; font-size:0.7em; margin-top:4px;">
                {{ evolution_agreement|length }} day{{ '' if evolution_agreement|length == 1 else 's' }} of data
            </div>
        </div>
        <div class="evo-card">
            <div class="evo-title">Average conviction per agent (daily)</div>
            {{ evolution_convictions_svg|safe }}
            <div style="font-size:0.7em; margin-top:6px;">
                <span style="color:#66ccff;">■</span> casper
                &nbsp;<span style="color:#ffcc66;">■</span> melchior
                &nbsp;<span style="color:#ff88aa;">■</span> balthasar
            </div>
        </div>
        <div class="evo-card" style="grid-column:1 / span 2;">
            <div class="evo-title">Hard-rule override counts (30d)</div>
            {% if evolution_overrides %}
                {{ evolution_overrides_svg|safe }}
            {% else %}
                <div style="color:#666; font-size:0.8em;">No overrides triggered in last 30d.</div>
            {% endif %}
        </div>
    </div>

    <!-- ── Phase 5 PANEL 5: Outcome Attribution ──────────────────── -->
    <h2>Outcome Attribution (30d)</h2>
    <div class="attribution-grid">
        <div class="attribution-card">
            <div class="evo-title" style="color:#00ff88;">Best 5 by 24h P&amp;L</div>
            {% if attribution_best %}
            <table>
                <tr><th>Time</th><th>Grid</th><th>C/M/B r0</th><th>Fills 24h</th><th>P&amp;L 24h</th></tr>
                {% for r in attribution_best %}
                <tr>
                    <td style="color:#888;">{{ r.timestamp[:16] }}</td>
                    <td class="{{ r.final_grid_action }}">{{ r.final_grid_action }}</td>
                    <td style="font-size:0.78em;">{{ r.casper_r0_position }} / {{ r.melchior_r0_position }} / {{ r.balthasar_r0_position }}</td>
                    <td>{{ r.fills_24h }}</td>
                    <td class="pnl-pos">${{ '%.4f'|format(r.pnl_24h or 0) }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <div style="color:#666;">No 24h-backfilled cycles yet.</div>
            {% endif %}
        </div>
        <div class="attribution-card">
            <div class="evo-title" style="color:#ff8866;">Worst 5 by 24h P&amp;L</div>
            {% if attribution_worst %}
            <table>
                <tr><th>Time</th><th>Grid</th><th>C/M/B r0</th><th>Fills 24h</th><th>P&amp;L 24h</th></tr>
                {% for r in attribution_worst %}
                <tr>
                    <td style="color:#888;">{{ r.timestamp[:16] }}</td>
                    <td class="{{ r.final_grid_action }}">{{ r.final_grid_action }}</td>
                    <td style="font-size:0.78em;">{{ r.casper_r0_position }} / {{ r.melchior_r0_position }} / {{ r.balthasar_r0_position }}</td>
                    <td>{{ r.fills_24h }}</td>
                    <td class="{{ 'pnl-neg' if (r.pnl_24h or 0) < 0 else 'pnl-pos' }}">${{ '%.4f'|format(r.pnl_24h or 0) }}</td>
                </tr>
                {% endfor %}
            </table>
            {% else %}
            <div style="color:#666;">No 24h-backfilled cycles yet.</div>
            {% endif %}
        </div>
    </div>
    <div class="evo-card" style="margin-top:14px;">
        <div class="evo-title">Fill rate &amp; P&amp;L by grid_action (30d)</div>
        {% if action_summary %}
        <table>
            <tr><th>Action</th><th>Cycles</th><th>Avg fills 24h</th><th>Avg P&amp;L 24h</th></tr>
            {% for r in action_summary %}
            <tr>
                <td class="{{ r.action }}">{{ r.action }}</td>
                <td>{{ r.count }}</td>
                <td>{{ '%.2f'|format(r.avg_fills or 0) }}</td>
                <td class="{{ 'pnl-neg' if (r.avg_pnl or 0) < 0 else 'pnl-pos' }}">${{ '%.4f'|format(r.avg_pnl or 0) }}</td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div style="color:#666;">No 24h-backfilled cycles yet.</div>
        {% endif %}
    </div>

    <!-- ── Phase 5 PANEL 3: Debate Log ───────────────────────────── -->
    <h2>Debate Log</h2>
    <details>
        <summary style="cursor:pointer; color:#88aaff;">
            Last {{ debate_log_rows|length }} triggered debates (click to expand)
        </summary>
        {% if debate_log_rows %}
        <table style="margin-top:10px;">
            <tr>
                <th>Time</th>
                <th>Pair</th>
                <th>Casper held</th>
                <th>Melchior held</th>
                <th>Balthasar held</th>
                <th>Any valid revision</th>
                <th>Grid</th>
                <th>Deadlock</th>
                <th>Fills 6h</th>
            </tr>
            {% for d in debate_log_rows %}
            <tr>
                <td colspan="9" style="padding:0;">
                <details class="debate-row">
                    <summary>
                        <table style="margin:0;"><tr>
                            <td style="width:14%; color:#888;">{{ d.timestamp[:19] }}</td>
                            <td style="width:14%; color:#ffaa00;">{{ d.conflict_pair or '—' }}</td>
                            <td style="width:9%;">{% if d.casper_r1_held    is none %}—{% elif d.casper_r1_held    %}HELD{% else %}revised{% endif %}</td>
                            <td style="width:9%;">{% if d.melchior_r1_held  is none %}—{% elif d.melchior_r1_held  %}HELD{% else %}revised{% endif %}</td>
                            <td style="width:9%;">{% if d.balthasar_r1_held is none %}—{% elif d.balthasar_r1_held %}HELD{% else %}revised{% endif %}</td>
                            <td style="width:11%; color:{{ '#00ff88' if d.any_revision_valid else '#888' }};">
                                {{ 'yes' if d.any_revision_valid else 'no' }}
                            </td>
                            <td style="width:10%;" class="{{ d.final_grid_action }}">{{ d.final_grid_action }}</td>
                            <td style="width:10%;" class="{{ 'HALT' if d.deadlock else '' }}">{{ 'YES' if d.deadlock else '—' }}</td>
                            <td style="width:8%;">{{ d.fills_6h if d.fills_6h is not none else '—' }}</td>
                        </tr></table>
                    </summary>
                    <div style="background:#0c0c14; padding:10px 14px; margin:4px 0; border:1px dashed #444466;">
                        {% for ag in ['casper', 'melchior', 'balthasar'] %}
                        <div style="margin-bottom:8px;">
                            <span style="color:#88aaff; letter-spacing:2px; font-size:0.75em;">{{ ag|upper }}</span>
                            {% set ev = d[ag + '_r0_evidence_list'] or [] %}
                            {% if ev %}<ul style="margin:4px 0 4px 18px; color:#aaaacc; font-size:0.8em;">
                                {% for e in ev %}<li>{{ e }}</li>{% endfor %}
                            </ul>{% endif %}
                            {% set r1 = d[ag + '_r1_text'] %}
                            {% if r1 %}<div style="color:#ccccdd; font-size:0.78em; font-style:italic;
                                margin:4px 0 0 18px;">r1: "{{ r1 }}"</div>{% endif %}
                        </div>
                        {% endfor %}
                    </div>
                </details>
                </td>
            </tr>
            {% endfor %}
        </table>
        {% else %}
        <div style="color:#666; margin-top:10px;">No triggered debates yet.</div>
        {% endif %}
    </details>

    <div class="footer">
        MAGI Phase 5 — XRP/USD Spot Grid Bot — {{ 'Paper' if paper_mode else 'Live' }} Mode
    </div>
</body>
</html>
"""

CHART_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>XRP/USD Live Chart</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { background: #0a0a0a; color: #00ff88; font-family: monospace; overflow: hidden; }
        #chart-container { width: 100vw; height: 100vh; position: relative; }
        #status {
            position: absolute;
            top: 8px;
            left: 8px;
            z-index: 10;
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.75em;
            background: rgba(0,0,0,0.6);
            padding: 4px 8px;
            border-radius: 3px;
            pointer-events: none;
        }
        #status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #ffaa00;
            display: inline-block;
        }
    </style>
</head>
<body>
    <div id="chart-container">
        <div id="status">
            <span id="status-dot"></span>
            <span id="status-text" style="color:#ffaa00;">CONNECTING</span>
        </div>
    </div>
    <script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
    <script>
    var container = document.getElementById('chart-container');
    var chart = LightweightCharts.createChart(container, {
        width: window.innerWidth,
        height: window.innerHeight,
        layout: {
            background: { color: '#0a0a0a' },
            textColor: '#00ff88',
        },
        grid: {
            vertLines: { color: '#ffffff11' },
            horzLines: { color: '#ffffff11' },
        },
        timeScale: {
            timeVisible: true,
            secondsVisible: false,
            rightOffset: 5,
            barSpacing: 6,
            fixLeftEdge: false,
            fixRightEdge: false,
        },
        rightPriceScale: {
            autoScale: true,
            scaleMargins: {
                top: 0.2,
                bottom: 0.2,
            },
        },
    });

    var candleSeries = chart.addCandlestickSeries({
        upColor: '#00ff88',
        downColor: '#ff4444',
        borderUpColor: '#00ff88',
        borderDownColor: '#ff4444',
        wickUpColor: '#00ff88',
        wickDownColor: '#ff4444',
    });

    window.addEventListener('resize', function() {
        chart.applyOptions({ width: window.innerWidth, height: window.innerHeight });
    });

    var priceLines = [];
    var ws = null;
    var lastMessageAt = 0;
    var reconnectAttempt = 0;
    var reconnectTimer = null;
    var backoffMs = 2000;
    var backoffStartTime = null;

    function setStatus(state) {
        var dot = document.getElementById('status-dot');
        var txt = document.getElementById('status-text');
        if (state === 'live') {
            dot.style.background = '#00ff88';
            txt.textContent = 'LIVE';
            txt.style.color = '#00ff88';
        } else if (state === 'reconnecting') {
            dot.style.background = '#ffaa00';
            txt.textContent = 'RECONNECTING';
            txt.style.color = '#ffaa00';
        } else {
            dot.style.background = '#ff4444';
            txt.textContent = 'DISCONNECTED';
            txt.style.color = '#ff4444';
        }
    }

    function parseTime(rfc3339) {
        return Math.floor(new Date(rfc3339).getTime() / 1000);
    }

    function drawGridLevels() {
        fetch('/api/active_grid_levels')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                console.log('Grid levels:', data.levels ? data.levels.length : 0,
                            'centre:', data.centre_price);
                priceLines.forEach(function(pl) { candleSeries.removePriceLine(pl); });
                priceLines = [];

                (data.levels || []).forEach(function(level) {
                    var color = level.side === 'buy' ? '#00ff88' : '#ff4444';
                    var title = level.side === 'buy'
                        ? 'B@' + parseFloat(level.price).toFixed(4)
                        : 'S@' + parseFloat(level.price).toFixed(4);
                    var pl = candleSeries.createPriceLine({
                        price: parseFloat(level.price),
                        color: color,
                        lineStyle: level.side === 'buy' ? 1 : 0,
                        lineWidth: 2,
                        axisLabelVisible: true,
                        title: title,
                    });
                    priceLines.push(pl);
                });

                if (data.centre_price != null) {
                    var cpl = candleSeries.createPriceLine({
                        price: parseFloat(data.centre_price),
                        color: '#00ccff',
                        lineStyle: 2,
                        lineWidth: 2,
                        axisLabelVisible: true,
                        title: 'centre',
                    });
                    priceLines.push(cpl);
                }

                if (data.centre_price != null && data.levels && data.levels.length > 0) {
                    var prices = data.levels.map(function(l) {
                        return parseFloat(l.price);
                    });
                    prices.push(parseFloat(data.centre_price));
                    var minPrice = Math.min.apply(null, prices);
                    var maxPrice = Math.max.apply(null, prices);
                    var padding = (maxPrice - minPrice) * 0.5;
                    chart.priceScale('right').applyOptions({
                        autoScale: true,
                    });
                }
            })
            .catch(function(err) { console.error('Grid levels fetch failed:', err); });
    }

    drawGridLevels();
    setInterval(drawGridLevels, 30000);

    function connect() {
        if (ws) {
            ws.onclose = null;
            ws.onerror = null;
            try { ws.close(); } catch(e) {}
            ws = null;
        }

        ws = new WebSocket('wss://ws.kraken.com/v2');

        ws.onopen = function() {
            reconnectAttempt = 0;
            backoffMs = 2000;
            backoffStartTime = null;
            lastMessageAt = Date.now();
            ws.send(JSON.stringify({
                method: 'subscribe',
                params: { channel: 'ohlc', symbol: ['XRP/USD'], interval: 5 }
            }));
        };

        ws.onmessage = function(event) {
            lastMessageAt = Date.now();
            var msg;
            try { msg = JSON.parse(event.data); } catch(e) { return; }

            var channel = msg.channel;
            var type = msg.type;

            if (channel === 'heartbeat') {
                return;
            }

            if (channel === 'ohlc') {
                var data = msg.data || [];
                if (type === 'snapshot') {
                    console.log('Kraken OHLC snapshot: ' + data.length + ' bars received');
                    var bars = data.map(function(c) {
                        return {
                            time: parseTime(c.interval_begin),
                            open: c.open,
                            high: c.high,
                            low: c.low,
                            close: c.close,
                        };
                    });
                    candleSeries.setData(bars);
                    chart.timeScale().fitContent();
                    setStatus('live');
                } else if (type === 'update') {
                    data.forEach(function(c) {
                        candleSeries.update({
                            time: parseTime(c.interval_begin),
                            open: c.open,
                            high: c.high,
                            low: c.low,
                            close: c.close,
                        });
                    });
                    setStatus('live');
                }
            }
        };

        ws.onclose = function() {
            if (backoffStartTime === null) {
                backoffStartTime = Date.now();
            }

            reconnectAttempt++;
            var delay;
            if (reconnectAttempt === 1) {
                delay = 0;
            } else {
                delay = Math.min(backoffMs, 30000);
                backoffMs = Math.min(backoffMs * 2, 30000);
            }

            var elapsed = Date.now() - backoffStartTime;
            if (elapsed > 60000) {
                setStatus('disconnected');
            } else {
                setStatus('reconnecting');
            }

            clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(connect, delay);
        };

        ws.onerror = function() {
            try { ws.close(); } catch(e) {}
        };
    }

    setInterval(function() {
        if (lastMessageAt === 0) return;
        var silent = Date.now() - lastMessageAt;
        if (silent > 5000 && ws && ws.readyState === WebSocket.OPEN) {
            ws.close();
        } else if (silent <= 5000 && ws && ws.readyState === WebSocket.OPEN) {
            setStatus('live');
        }
    }, 2000);

    connect();
    </script>
</body>
</html>
"""


# ── Phase 5: council data fetch + SVG sparkline helpers ──────────────

def _svg_sparkline(values, w=80, h=24, color='#66ccff'):
    """One-line polyline sparkline. Returns SVG markup as a string."""
    if not values:
        return '<svg width="{}" height="{}"></svg>'.format(w, h)
    vmin = min(values)
    vmax = max(values)
    span = (vmax - vmin) or 1
    n = len(values)
    if n == 1:
        y = h / 2
        return (f'<svg width="{w}" height="{h}">'
                f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" '
                f'stroke="{color}" stroke-width="1.5"/></svg>')
    pts = []
    for i, v in enumerate(values):
        x = i * (w / (n - 1))
        y = h - ((v - vmin) / span) * h
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg width="{w}" height="{h}">'
            f'<polyline points="{" ".join(pts)}" '
            f'fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


def _svg_multiline(series_dict, w=560, h=140,
                   colors=None, y_min=0.0, y_max=1.0):
    """Multi-series line chart on a 0..1 y-axis (default). Returns SVG."""
    colors = colors or {'casper': '#66ccff', 'melchior': '#ffcc66',
                        'balthasar': '#ff88aa'}
    if not series_dict or not any(len(v) > 0 for v in series_dict.values()):
        return '<div style="color:#666; font-size:0.8em;">No data yet.</div>'
    n = max(len(v) for v in series_dict.values())
    if n < 2:
        return '<div style="color:#666; font-size:0.8em;">Need ≥2 data points.</div>'
    span = (y_max - y_min) or 1
    pad = 4
    inner_w = w - 2 * pad
    inner_h = h - 2 * pad
    parts = [f'<svg width="{w}" height="{h}" style="background:#08080f;">']
    # y-axis baseline
    parts.append(f'<line x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}" '
                 f'stroke="#222" stroke-width="1"/>')
    for label, series in series_dict.items():
        if not series:
            continue
        pts = []
        for i, v in enumerate(series):
            x = pad + i * (inner_w / (n - 1))
            y = pad + inner_h - ((float(v) - y_min) / span) * inner_h
            pts.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline points="{" ".join(pts)}" '
                     f'fill="none" stroke="{colors.get(label, "#888")}" '
                     f'stroke-width="1.5"/>')
    parts.append('</svg>')
    return ''.join(parts)


def _svg_agreement(series, w=560, h=140, color='#88cc88'):
    """Single-series area + line for agreement rate 0..100."""
    if not series:
        return '<div style="color:#666; font-size:0.8em;">No data yet.</div>'
    values = [s.get('rate', 0) for s in series]
    return _svg_multiline({'rate': values}, w=w, h=h,
                          colors={'rate': color}, y_min=0.0, y_max=100.0)


def _svg_bars(items, w=540, h=120, color='#ffaa00'):
    """Horizontal bar chart for (tag, count) tuples. Items sorted by caller."""
    if not items:
        return '<div style="color:#666; font-size:0.8em;">No overrides.</div>'
    n = len(items)
    bar_h = max(10, min(22, (h - 8) // n - 4))
    label_w = 160
    total_h = (bar_h + 6) * n + 8
    max_v = max((c for _, c in items), default=1) or 1
    parts = [f'<svg width="{w}" height="{total_h}" style="background:#08080f;">']
    for i, (tag, count) in enumerate(items):
        y = 4 + i * (bar_h + 6)
        bar_w = int((count / max_v) * (w - label_w - 50))
        parts.append(f'<text x="0" y="{y + bar_h - 3}" fill="#ccc" '
                     f'font-size="11">{tag}</text>')
        parts.append(f'<rect x="{label_w}" y="{y}" width="{bar_w}" '
                     f'height="{bar_h}" fill="{color}" opacity="0.85"/>')
        parts.append(f'<text x="{label_w + bar_w + 6}" y="{y + bar_h - 3}" '
                     f'fill="#ffcc66" font-size="11">{count}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def _fetch_council_data():
    """
    Single-call fetcher for all five council panels. Returns a dict that the
    index() route can splat into render_template_string. All values are
    JSON-serialisable (no SQLite Row objects).
    """
    import json as _json
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    from database import (
        get_conn, get_recent_debate_records,
        get_agent_accuracy, get_capitulation_rate,
    )

    recent = get_recent_debate_records(limit=1)
    latest_debate = recent[0] if recent else None

    override_tags = []
    if latest_debate:
        # Parse JSON-encoded evidence into Python lists for the template
        for agent in ('casper', 'melchior', 'balthasar'):
            ev = latest_debate.get(f'{agent}_r0_evidence')
            if ev and isinstance(ev, str):
                try:
                    latest_debate[f'{agent}_r0_evidence_list'] = _json.loads(ev)
                except (ValueError, TypeError):
                    latest_debate[f'{agent}_r0_evidence_list'] = []
            else:
                latest_debate[f'{agent}_r0_evidence_list'] = ev or []
        # Look up the matching legacy magi_decisions row to recover the
        # bracketed override tags from the `notes` field (hard_rule_overrides
        # is not persisted in debate_records).
        conn0 = get_conn()
        nrow = conn0.execute(
            "SELECT notes FROM magi_decisions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn0.close()
        if nrow and nrow['notes']:
            override_tags = sorted(set(_re.findall(r"\[([A-Z_]+)\]", nrow['notes'])))

    # Per-agent accuracy + capitulation cards
    council_accuracy = {}
    for a in ('casper', 'melchior', 'balthasar'):
        council_accuracy[a] = {
            'acc_7d':    get_agent_accuracy(a, 7),
            'acc_30d':   get_agent_accuracy(a, 30),
            'capit_30d': get_capitulation_rate(a, 30),
        }

    conn = get_conn()

    # Sparkline data — last 30 cycles, ordered oldest → newest
    rows = conn.execute(
        "SELECT casper_r0_conviction, melchior_r0_conviction, balthasar_r0_conviction "
        "FROM debate_records ORDER BY id DESC LIMIT 30"
    ).fetchall()
    sparkline_data = {a: [] for a in ('casper', 'melchior', 'balthasar')}
    for r in reversed(rows):
        for a in sparkline_data:
            v = r[f'{a}_r0_conviction']
            sparkline_data[a].append(float(v) if v is not None else 0.0)
    sparklines_svg = {
        a: _svg_sparkline(sparkline_data[a], w=140, h=28,
                          color={'casper': '#66ccff', 'melchior': '#ffcc66',
                                 'balthasar': '#ff88aa'}[a])
        for a in sparkline_data
    }

    # Debate log — last 30 triggered debates
    debate_rows = conn.execute(
        "SELECT cycle_id, timestamp, conflict_pair, "
        "casper_r1_held, melchior_r1_held, balthasar_r1_held, "
        "casper_revision_valid, melchior_revision_valid, balthasar_revision_valid, "
        "casper_r1_text, melchior_r1_text, balthasar_r1_text, "
        "casper_r0_evidence, melchior_r0_evidence, balthasar_r0_evidence, "
        "final_grid_action, deadlock, fills_6h "
        "FROM debate_records WHERE debate_triggered=1 "
        "ORDER BY id DESC LIMIT 30"
    ).fetchall()
    debate_log = []
    for r in debate_rows:
        d = dict(r)
        for a in ('casper', 'melchior', 'balthasar'):
            ev = d.get(f'{a}_r0_evidence')
            try:
                d[f'{a}_r0_evidence_list'] = _json.loads(ev) if ev else []
            except (ValueError, TypeError):
                d[f'{a}_r0_evidence_list'] = []
        d['any_revision_valid'] = (
            d.get('casper_revision_valid') == 1
            or d.get('melchior_revision_valid') == 1
            or d.get('balthasar_revision_valid') == 1
        )
        debate_log.append(d)

    cutoff = (_dt.utcnow() - _td(days=30)).isoformat()

    # Daily agreement rate
    agreement_rows = conn.execute(
        "SELECT DATE(timestamp) AS d, "
        "SUM(CASE WHEN debate_triggered=0 THEN 1 ELSE 0 END) AS agree, "
        "COUNT(*) AS total "
        "FROM debate_records WHERE timestamp >= ? "
        "GROUP BY DATE(timestamp) ORDER BY d ASC",
        (cutoff,)
    ).fetchall()
    agreement_series = [
        {'date': r['d'], 'rate': round((r['agree'] / r['total'] * 100), 2) if r['total'] else 0.0}
        for r in agreement_rows
    ]

    # Daily avg conviction per agent
    conv_rows = conn.execute(
        "SELECT DATE(timestamp) AS d, "
        "AVG(casper_r0_conviction) AS c, "
        "AVG(melchior_r0_conviction) AS m, "
        "AVG(balthasar_r0_conviction) AS b "
        "FROM debate_records WHERE timestamp >= ? "
        "GROUP BY DATE(timestamp) ORDER BY d ASC",
        (cutoff,)
    ).fetchall()
    conviction_series = {
        'dates':     [r['d'] for r in conv_rows],
        'casper':    [float(r['c'] or 0) for r in conv_rows],
        'melchior':  [float(r['m'] or 0) for r in conv_rows],
        'balthasar': [float(r['b'] or 0) for r in conv_rows],
    }

    # Hard-rule overrides — parse from magi_decisions.notes
    notes_rows = conn.execute(
        "SELECT notes FROM magi_decisions WHERE timestamp >= ?", (cutoff,)
    ).fetchall()
    tag_counts = {}
    for r in notes_rows:
        for tag in _re.findall(r"\[([A-Z_]+)\]", r['notes'] or ''):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    override_counts = sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)

    # Outcome attribution
    best_rows = conn.execute(
        "SELECT timestamp, final_grid_action, "
        "casper_r0_position, melchior_r0_position, balthasar_r0_position, "
        "fills_24h, pnl_24h FROM debate_records "
        "WHERE timestamp >= ? AND outcome_24h_backfilled=1 "
        "ORDER BY pnl_24h DESC LIMIT 5", (cutoff,)
    ).fetchall()
    worst_rows = conn.execute(
        "SELECT timestamp, final_grid_action, "
        "casper_r0_position, melchior_r0_position, balthasar_r0_position, "
        "fills_24h, pnl_24h FROM debate_records "
        "WHERE timestamp >= ? AND outcome_24h_backfilled=1 "
        "ORDER BY pnl_24h ASC LIMIT 5", (cutoff,)
    ).fetchall()
    summary_rows = conn.execute(
        "SELECT final_grid_action AS action, COUNT(*) AS count, "
        "AVG(fills_24h) AS avg_fills, AVG(pnl_24h) AS avg_pnl "
        "FROM debate_records WHERE timestamp >= ? AND outcome_24h_backfilled=1 "
        "GROUP BY final_grid_action ORDER BY avg_pnl DESC",
        (cutoff,)
    ).fetchall()

    conn.close()

    # Convert convictions 0..1 → 0..100% so the multiline chart shares a y-axis
    conv_for_chart = {k: [v * 100.0 for v in conviction_series[k]]
                      for k in ('casper', 'melchior', 'balthasar')}

    return {
        'latest_debate':          latest_debate,
        'council_override_tags':  override_tags,
        'council_accuracy':       council_accuracy,
        'conviction_sparklines_svg': sparklines_svg,
        'debate_log_rows':        debate_log,
        'evolution_agreement':    agreement_series,
        'evolution_agreement_svg': _svg_agreement(agreement_series, w=480, h=120),
        'evolution_convictions':  conviction_series,
        'evolution_convictions_svg': _svg_multiline(
            conv_for_chart, w=480, h=120, y_min=0.0, y_max=100.0
        ),
        'evolution_overrides':    override_counts,
        'evolution_overrides_svg': _svg_bars(override_counts, w=960, h=200),
        'attribution_best':       [dict(r) for r in best_rows],
        'attribution_worst':      [dict(r) for r in worst_rows],
        'action_summary':         [dict(r) for r in summary_rows],
    }


def check_scheduler_alive():
    try:
        log_path = '/root/xrp_grid/magi.log'
        if not os.path.exists(log_path):
            return False
        mtime = os.path.getmtime(log_path)
        age_minutes = (datetime.now(timezone.utc).timestamp() - mtime) / 60
        return age_minutes < 90
    except Exception:
        return False


_do_billing_cache = {'data': None, 'expires': 0}


def get_do_billing():
    """Fetch DO month-to-date usage and account balance via DO API.
    Returns dict with keys: mtd_usage, account_balance, error.
    Times out after 3 seconds to avoid blocking dashboard render."""
    import time
    global _do_billing_cache
    if _do_billing_cache['data'] and time.time() < _do_billing_cache['expires']:
        return _do_billing_cache['data']
    from config import DO_API_TOKEN, DO_DROPLET_MONTHLY_USD
    if not DO_API_TOKEN:
        result = {
            'mtd_usage': DO_DROPLET_MONTHLY_USD,
            'account_balance': None,
            'error': 'DO_API_TOKEN not set — using hardcoded fallback'
        }
        _do_billing_cache = {'data': result, 'expires': time.time() + 300}
        return result
    try:
        import urllib.request
        import json
        import socket
        socket.setdefaulttimeout(3)
        req = urllib.request.Request(
            'https://api.digitalocean.com/v2/customers/my/balance',
            headers={
                'Authorization': f'Bearer {DO_API_TOKEN}',
                'Content-Type': 'application/json'
            }
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        result = {
            'mtd_usage': float(data.get('month_to_date_usage', DO_DROPLET_MONTHLY_USD)),
            'account_balance': float(data.get('account_balance', 0)),
            'month_to_date_balance': float(data.get('month_to_date_balance', 0)),
            'error': None
        }
        _do_billing_cache = {'data': result, 'expires': time.time() + 300}
        return result
    except Exception as e:
        result = {
            'mtd_usage': DO_DROPLET_MONTHLY_USD,
            'account_balance': None,
            'month_to_date_balance': None,
            'error': str(e)
        }
        _do_billing_cache = {'data': result, 'expires': time.time() + 300}
        return result


@app.route('/')
def index():
    from zoneinfo import ZoneInfo
    EST = ZoneInfo('America/New_York')
    now = datetime.now(timezone.utc).astimezone(EST).strftime('%Y-%m-%d %H:%M EST')

    indicators = get_latest_indicators('1h') or {}
    grid = get_current_grid_state() or {}
    inventory = get_latest_inventory() or {}
    price = engine.get_current_price() or 0.0

    cost_today_data = get_cost_today()
    cost_30d_data = get_cost_summary(days_back=30)
    total_cost_30d = sum((c.get('cost') or 0) for c in cost_30d_data)
    fixed_breakdown = ', '.join(f"{k}: ${v}" for k, v in FIXED_SUBSCRIPTIONS.items() if v > 0)

    from config import (LLM_MONTHLY_BUDGET_USD,
                        ANTHROPIC_CREDIT_REMAINING,
                        OPENAI_CREDIT_REMAINING,
                        GOOGLE_CREDIT_REMAINING)
    from datetime import date as _date

    do_billing = get_do_billing()
    do_mtd = do_billing['mtd_usage']
    do_balance = do_billing.get('account_balance')
    do_error = do_billing.get('error')

    day_of_month = _date.today().day
    days_in_month = 30
    daily_llm_rate = total_cost_30d / 30 if total_cost_30d else 0
    llm_monthly_projected = daily_llm_rate * days_in_month
    total_projected = llm_monthly_projected + do_mtd
    llm_over_budget = llm_monthly_projected > LLM_MONTHLY_BUDGET_USD

    credit_map = {
        'balthasar': ANTHROPIC_CREDIT_REMAINING,
        'melchior':  OPENAI_CREDIT_REMAINING,
        'casper':    GOOGLE_CREDIT_REMAINING,
    }
    agent_runway = []
    for agent in cost_30d_data:
        name = agent.get('agent', '')
        cost_30d_val = agent.get('cost') or 0
        daily_rate = cost_30d_val / 30
        credit = credit_map.get(name, 0)
        if daily_rate > 0:
            runway_days = int(credit / daily_rate)
        else:
            runway_days = 9999
        agent_runway.append({
            'agent':       name,
            'model':       agent.get('model', ''),
            'calls':       agent.get('calls', 0),
            'tokens':      agent.get('total_tokens', 0),
            'cost_30d':    cost_30d_val,
            'daily_rate':  daily_rate,
            'credit':      credit,
            'runway_days': runway_days,
        })

    guardrails_ok, guardrail_failures = check_all_guardrails()
    ks_active = kill_switch_active()
    shadow_variants = get_all_shadow_states()

    # P&L snapshot
    snap = get_pnl_snapshot(price)
    best_shadow_level, best_shadow_pnl = get_best_shadow_from_db()
    live_pnl_pct = round(snap['total'] / MAX_INVENTORY_USD * 100, 4) if MAX_INVENTORY_USD > 0 else 0.0
    best_shadow_pnl = best_shadow_pnl or 0.0
    live_vs_shadow = round(live_pnl_pct - best_shadow_pnl, 4)

    recent_orders = get_recent_grid_orders(limit=25)

    mins_since = snap.get('time_since_last_fill_minutes')
    if mins_since is None:
        fill_age_label = "no fills yet"
        # Check if any open orders have been sitting > 24h with no fills
        from database import get_conn as _get_conn
        _conn = _get_conn()
        _old_open = _conn.execute(
            """SELECT COUNT(*) FROM grid_orders
               WHERE status='open'
               AND timestamp < datetime('now', '-24 hours')"""
        ).fetchone()[0]
        _conn.close()
        if _old_open > 0:
            fill_age_color = "#ff4444"
            fill_stale = True
        else:
            fill_age_color = "#888"
            fill_stale = False
    else:
        hours = mins_since / 60
        if hours < 1:
            fill_age_label = f"{int(mins_since)} min ago"
        elif hours < 24:
            fill_age_label = f"{hours:.1f} h ago"
        else:
            fill_age_label = f"{hours/24:.1f} d ago"
        fill_age_color = "#ff4444" if hours > 24 else "#00ff88" if hours < 2 else "#ffaa00"
        fill_stale = hours > 24

    return render_template_string(HTML_TEMPLATE,
        now=now,
        price=f"{price:.4f}" if price else "N/A",
        vol_regime=indicators.get('vol_regime', 'N/A'),
        atr_pct=f"{indicators.get('atr_percentile', 0):.1f}",
        vwap_dev=f"{indicators.get('vwap_dev_pct', 0):.3f}",
        grid_centre=f"{grid.get('centre_price', 0):.4f}" if grid.get('centre_price') else "N/A",
        grid_spacing=f"{grid.get('spacing_pct', 0)*100:.3f}" if grid.get('spacing_pct') else "N/A",
        grid_levels=grid.get('levels', 0),
        xrp_held=f"{inventory.get('xrp_held', 0):.4f}",
        usd_held=f"{inventory.get('usd_held', 0):.2f}",
        net_position=f"{inventory.get('net_position_usd', 0):.2f}",
        inventory_skew=f"{inventory.get('inventory_skew', 0):.3f}",
        cost_today=f"{cost_today_data.get('cost') or 0:.4f}",
        calls_today=cost_today_data.get('calls') or 0,
        tokens_today=cost_today_data.get('tokens') or 0,
        cost_30d=f"{total_cost_30d:.4f}",
        cost_breakdown=cost_30d_data,
        fixed_monthly=f"{get_fixed_monthly_total():.2f}",
        fixed_breakdown=fixed_breakdown,
        do_mtd=do_mtd,
        do_balance=do_balance,
        do_error=do_error,
        llm_monthly_projected=round(llm_monthly_projected, 4),
        total_projected=round(total_projected, 4),
        llm_over_budget=llm_over_budget,
        llm_monthly_budget=LLM_MONTHLY_BUDGET_USD,
        agent_runway=agent_runway,
        scheduler_alive=check_scheduler_alive(),
        guardrails_ok=guardrails_ok,
        guardrail_failures=guardrail_failures,
        kill_switch=ks_active,
        paper_mode=engine.paper,
        shadow_variants=shadow_variants,
        active_levels=engine.level_count,
        # P&L tiles
        pnl_realized=snap['realized'],
        pnl_realized_fmt=f"{snap['realized']:.4f}",
        pnl_unrealized=snap['unrealized'],
        pnl_unrealized_fmt=f"{snap['unrealized']:.4f}",
        pnl_total=snap['total'],
        pnl_total_fmt=f"{snap['total']:.4f}",
        pnl_fees_fmt=f"{snap['fees']:.4f}",
        pnl_fill_count=snap['fill_count'],
        pnl_matched_trips=snap['matched_round_trips'],
        pnl_unmatched_buys=snap['unmatched_buys'],
        pnl_win_rate=snap['win_rate'],
        pnl_avg_per_trip=snap['avg_pnl_per_round_trip'],
        pnl_fills_today=snap['fills_today'],
        pnl_mins_since=snap['time_since_last_fill_minutes'],
        fill_age_label=fill_age_label,
        fill_age_color=fill_age_color,
        fill_stale=fill_stale,
        live_pnl_pct_fmt=f"{live_pnl_pct:.4f}",
        best_shadow_level=best_shadow_level,
        best_shadow_pnl_fmt=f"{best_shadow_pnl:.4f}",
        live_vs_shadow=live_vs_shadow,
        live_vs_shadow_fmt=f"{live_vs_shadow:+.4f}",
        # Recent orders table
        recent_orders=recent_orders,
        order_pnl_map=snap['order_pnl_map'],
        magi_token=os.environ.get('MAGI_TRIGGER_TOKEN', ''),
        # Phase 5: agent council panels (single splat from helper)
        **_fetch_council_data(),
    )


@app.route('/api/status')
def api_status():
    indicators = get_latest_indicators('1h') or {}
    grid = get_current_grid_state() or {}
    inventory = get_latest_inventory() or {}
    decisions = get_recent_magi_decisions(1)
    price = engine.get_current_price()
    return jsonify({
        'price': price,
        'vol_regime': indicators.get('vol_regime'),
        'vwap_dev_pct': indicators.get('vwap_dev_pct'),
        'grid_centre': grid.get('centre_price'),
        'grid_spacing_pct': grid.get('spacing_pct'),
        'inventory': {
            'xrp': inventory.get('xrp_held'),
            'usd': inventory.get('usd_held'),
            'net_usd': inventory.get('net_position_usd'),
            'skew': inventory.get('inventory_skew')
        },
        'latest_magi': decisions[0] if decisions else None,
        'scheduler_alive': check_scheduler_alive(),
        'paper_mode': engine.paper
    })


@app.route('/api/pnl')
def api_pnl():
    price = engine.get_current_price() or 0.0
    snap = get_pnl_snapshot(price)
    best_lc, best_shadow_pnl = get_best_shadow_from_db()
    live_pnl_pct = round(snap['total'] / MAX_INVENTORY_USD * 100, 4) if MAX_INVENTORY_USD > 0 else 0.0
    live_minus_shadow = round(live_pnl_pct - (best_shadow_pnl or 0.0), 4)
    return jsonify({
        **snap,
        'best_shadow_level': best_lc,
        'best_shadow_pnl_pct': best_shadow_pnl,
        'live_pnl_pct': live_pnl_pct,
        'live_minus_shadow_pct': live_minus_shadow,
    })


@app.route('/api/recent_orders')
def api_recent_orders():
    limit = min(int(request.args.get('limit', 25)), 200)
    orders = get_recent_grid_orders(limit=limit)
    return jsonify({'orders': orders, 'count': len(orders)})


@app.route('/api/shadow_variants')
def api_shadow_variants():
    shadow_variants = get_all_shadow_states()
    return jsonify({
        'variants': shadow_variants,
        'active_levels': engine.level_count
    })


@app.route('/api/trigger_learning', methods=['POST'])
def trigger_learning():
    force = request.args.get('force', 'false').lower() == 'true'
    try:
        result = run_learning_cycle(force=force)
        return jsonify(result or {'skipped': True, 'reason': 'unknown'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/trigger_magi', methods=['POST'])
def trigger_magi():
    if MAGI_TRIGGER_TOKEN:
        provided = request.headers.get('X-Magi-Token', '') or \
                   request.args.get('token', '')
        if provided != MAGI_TRIGGER_TOKEN:
            return jsonify({'ok': False, 'error': 'Unauthorized'}), 403
    try:
        import urllib.request as _ur
        import json as _json
        import socket as _socket
        _socket.setdefaulttimeout(120)  # MAGI cycle can take ~30s
        req = _ur.Request(
            'http://127.0.0.1:5001/internal/trigger_magi',
            data=b'',
            method='POST'
        )
        with _ur.urlopen(req) as resp:
            result = _json.loads(resp.read().decode())
        from database import mark_magi_decision_applied
        # Mark applied if decision_id available
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/toggle_kill', methods=['POST'])
def toggle_kill():
    try:
        if os.path.exists(KILL_SWITCH_FILE):
            os.remove(KILL_SWITCH_FILE)
            active = False
        else:
            open(KILL_SWITCH_FILE, 'w').close()
            active = True
        return jsonify({'kill_switch': active})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/active_grid_levels')
def api_active_grid_levels():
    from database import get_conn, get_current_grid_state
    conn = get_conn()
    rows = conn.execute(
        "SELECT order_id, side, price FROM grid_orders WHERE status='open' "
        "ORDER BY price ASC"
    ).fetchall()
    conn.close()
    levels = [
        {'order_id': r['order_id'], 'side': r['side'], 'price': r['price']}
        for r in rows
    ]
    grid_state = get_current_grid_state() or {}
    return jsonify({
        'levels': levels,
        'centre_price': grid_state.get('centre_price'),
        'spacing_pct': grid_state.get('spacing_pct'),
        'level_count': grid_state.get('levels'),
    })


@app.route('/chart')
def chart():
    return CHART_HTML_TEMPLATE


# ── Phase 5: council API routes ─────────────────────────────────────

@app.route('/api/council/latest')
def api_council_latest():
    """Most recent debate_records row as JSON (evidence parsed to list)."""
    from database import get_recent_debate_records
    import json as _json
    rows = get_recent_debate_records(limit=1)
    if not rows:
        return jsonify(None)
    row = rows[0]
    for agent in ('casper', 'melchior', 'balthasar'):
        ev = row.get(f'{agent}_r0_evidence')
        if isinstance(ev, str) and ev:
            try:
                row[f'{agent}_r0_evidence'] = _json.loads(ev)
            except (ValueError, TypeError):
                pass
    return jsonify(row)


@app.route('/api/council/history')
def api_council_history():
    """List of recent debate_records. ?limit=N (default 20, max 200)."""
    from database import get_recent_debate_records
    import json as _json
    try:
        n = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        n = 20
    n = max(1, min(n, 200))
    rows = get_recent_debate_records(limit=n)
    for row in rows:
        for agent in ('casper', 'melchior', 'balthasar'):
            ev = row.get(f'{agent}_r0_evidence')
            if isinstance(ev, str) and ev:
                try:
                    row[f'{agent}_r0_evidence'] = _json.loads(ev)
                except (ValueError, TypeError):
                    pass
    return jsonify(rows)


@app.route('/api/council/accuracy')
def api_council_accuracy():
    """{casper, melchior, balthasar} → {acc_7d, acc_30d, capit_30d}."""
    from database import get_agent_accuracy, get_capitulation_rate
    out = {}
    for a in ('casper', 'melchior', 'balthasar'):
        out[a] = {
            'acc_7d':    get_agent_accuracy(a, 7),
            'acc_30d':   get_agent_accuracy(a, 30),
            'capit_30d': get_capitulation_rate(a, 30),
        }
    return jsonify(out)


@app.route('/api/council/evolution')
def api_council_evolution():
    """Time-series data for the evolution charts (30d). All JSON-serialisable."""
    data = _fetch_council_data()
    return jsonify({
        'agreement':         data['evolution_agreement'],
        'convictions':       data['evolution_convictions'],
        'overrides':         data['evolution_overrides'],
        'attribution_best':  data['attribution_best'],
        'attribution_worst': data['attribution_worst'],
        'action_summary':    data['action_summary'],
    })


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)
