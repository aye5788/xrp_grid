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

    <h2>Paper P&amp;L</h2>
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

    <h2>Recent Orders</h2>
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
    <div style="color:#666; font-size:0.8em;">No orders recorded yet — starts after first MAGI cycle.</div>
    {% endif %}

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

    <h2>Costs</h2>
    <div class="grid">
        <div class="card">
            <div class="label">Today's LLM Spend</div>
            <div class="value">${{ cost_today }}</div>
            <div class="sub">{{ calls_today }} calls / {{ tokens_today }} tokens</div>
        </div>
        <div class="card">
            <div class="label">Last 30 Days LLM</div>
            <div class="value">${{ cost_30d }}</div>
        </div>
        <div class="card">
            <div class="label">Fixed Monthly</div>
            <div class="value">${{ fixed_monthly }}</div>
            <div class="sub">{{ fixed_breakdown }}</div>
        </div>
    </div>
    {% if cost_breakdown %}
    <table>
        <tr><th>Agent</th><th>Model</th><th>Calls</th><th>Tokens</th><th>Cost (30d)</th></tr>
        {% for c in cost_breakdown %}
        <tr>
            <td>{{ c.agent }}</td>
            <td style="color:#888;">{{ c.model }}</td>
            <td>{{ c.calls }}</td>
            <td>{{ c.total_tokens }}</td>
            <td>${{ '%.4f'|format(c.cost or 0) }}</td>
        </tr>
        {% endfor %}
    </table>
    {% endif %}

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
            const r = await fetch('/api/trigger_magi', {method: 'POST'});
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

    <h2>Latest MAGI Decision</h2>
    <div style="font-size:0.8em; color:#555; margin-bottom:10px;">
        {% if latest_decision and decision_age_label %}
            Latest cycle: <span style="color:#888;">{{ decision_ts_display }}</span>
            &nbsp;(<span style="color:{{ decision_age_color }};">{{ decision_age_label }}</span>)
        {% elif latest_decision %}
            Latest cycle: <span style="color:#888;">{{ decision_ts_display }}</span>
        {% else %}
            No MAGI cycles run yet.
        {% endif %}
    </div>
    {% if latest_decision %}
    <div class="agent-row">
        <div class="agent-card">
            <div class="agent-name">Melchior — GPT-4o</div>
            <div class="{{ latest_decision.melchior_action }}">{{ latest_decision.melchior_action }}</div>
            <div style="color:#666; font-size:0.75em;">{{ latest_decision.melchior_conviction }} conviction</div>
            <div style="color:#888; font-size:0.75em; margin-top:6px;">{{ latest_decision.melchior_reasoning }}</div>
        </div>
        <div class="agent-card">
            <div class="agent-name">Balthasar — Claude Sonnet 4.6</div>
            <div class="{{ latest_decision.balthasar_action }}">{{ latest_decision.balthasar_action }}</div>
            <div style="color:#666; font-size:0.75em;">{{ latest_decision.balthasar_conviction }} conviction</div>
            <div style="color:#888; font-size:0.75em; margin-top:6px;">{{ latest_decision.balthasar_reasoning }}</div>
        </div>
        <div class="agent-card">
            <div class="agent-name">Casper — Gemini 2.5 Flash</div>
            <div class="{{ latest_decision.casper_action }}">{{ latest_decision.casper_action }}</div>
            <div style="color:#666; font-size:0.75em;">{{ latest_decision.casper_conviction }} conviction</div>
            <div style="color:#888; font-size:0.75em; margin-top:6px;">{{ latest_decision.casper_reasoning }}</div>
        </div>
    </div>
    <div class="card" style="margin-top:10px;">
        <div class="label">Consensus</div>
        <div style="margin-top:6px;">
            Grid: <span class="{{ latest_decision.consensus_grid_action }}">{{ latest_decision.consensus_grid_action }}</span>
            &nbsp;|&nbsp;
            Risk: <span class="{{ latest_decision.consensus_risk_action }}">{{ latest_decision.consensus_risk_action }}</span>
            &nbsp;|&nbsp;
            Regime: <span class="{{ latest_decision.consensus_regime }}">{{ latest_decision.consensus_regime }}</span>
        </div>
        <div style="color:#666; font-size:0.75em; margin-top:6px;">{{ latest_decision.notes }}</div>
        <div style="color:#444; font-size:0.7em; margin-top:4px;">{{ latest_decision.timestamp }}</div>
    </div>
    {% else %}
    <div style="color:#666;">No MAGI decisions recorded yet.</div>
    {% endif %}

    <h2>Recent Decisions</h2>
    <table>
        <tr>
            <th>Time</th>
            <th>Trigger</th>
            <th>Melchior</th>
            <th>Balthasar</th>
            <th>Casper</th>
            <th>Grid</th>
            <th>Risk</th>
        </tr>
        {% for d in decisions %}
        <tr>
            <td style="color:#666;">{{ d.timestamp[:16] }}</td>
            <td style="color:#888;">{{ d.trigger }}</td>
            <td class="{{ d.melchior_action }}">{{ d.melchior_action }}</td>
            <td class="{{ d.balthasar_action }}">{{ d.balthasar_action }}</td>
            <td class="{{ d.casper_action }}">{{ d.casper_action }}</td>
            <td class="{{ d.consensus_grid_action }}">{{ d.consensus_grid_action }}</td>
            <td class="{{ d.consensus_risk_action }}">{{ d.consensus_risk_action }}</td>
        </tr>
        {% endfor %}
    </table>

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


def _decision_age(latest_decision):
    """Return (display_ts, age_label, age_color) for the latest MAGI decision."""
    if not latest_decision:
        return '', None, '#666'
    try:
        ts_str = latest_decision['timestamp']
        if not ts_str:
            return '', None, '#666'
        ts = datetime.fromisoformat(str(ts_str).replace('Z', '+00:00'))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        display_ts = ts_str[:16]
        age_secs = (datetime.now(timezone.utc) - ts).total_seconds()
        age_mins = int(age_secs / 60)
        if age_mins < 60:
            age_label = f"{age_mins} minute{'s' if age_mins != 1 else ''} ago"
        elif age_mins < 120:
            h, m = divmod(age_mins, 60)
            age_label = f"{h}h {m}m ago"
        else:
            age_label = f"{age_mins // 60:.0f} hours ago"
        if age_mins < 120:
            color = '#00ff88'
        elif age_mins < 480:
            color = '#ffaa00'
        else:
            color = '#ff4444'
        return display_ts, age_label, color
    except Exception:
        return str(latest_decision.get('timestamp', ''))[:16] if hasattr(latest_decision, 'get') else '', None, '#666'


@app.route('/')
def index():
    from zoneinfo import ZoneInfo
    EST = ZoneInfo('America/New_York')
    now = datetime.now(timezone.utc).astimezone(EST).strftime('%Y-%m-%d %H:%M EST')

    indicators = get_latest_indicators('1h') or {}
    grid = get_current_grid_state() or {}
    inventory = get_latest_inventory() or {}
    decisions = get_recent_magi_decisions(10)
    latest_decision = decisions[0] if decisions else None
    price = engine.get_current_price() or 0.0

    cost_today_data = get_cost_today()
    cost_30d_data = get_cost_summary(days_back=30)
    total_cost_30d = sum((c.get('cost') or 0) for c in cost_30d_data)
    fixed_breakdown = ', '.join(f"{k}: ${v}" for k, v in FIXED_SUBSCRIPTIONS.items() if v > 0)

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

    decision_ts_display, decision_age_label, decision_age_color = _decision_age(latest_decision)

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
        latest_decision=latest_decision,
        decisions=decisions,
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
        live_pnl_pct_fmt=f"{live_pnl_pct:.4f}",
        best_shadow_level=best_shadow_level,
        best_shadow_pnl_fmt=f"{best_shadow_pnl:.4f}",
        live_vs_shadow=live_vs_shadow,
        live_vs_shadow_fmt=f"{live_vs_shadow:+.4f}",
        # Recent orders table
        recent_orders=recent_orders,
        order_pnl_map=snap['order_pnl_map'],
        # MAGI decision age (FIX 3)
        decision_ts_display=decision_ts_display,
        decision_age_label=decision_age_label,
        decision_age_color=decision_age_color,
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
        from magi.orchestrator import run_cycle
        result = run_cycle(trigger='manual', force=True)
        if result is None:
            return jsonify({'ok': False, 'error': 'Cycle returned None — check guardrails or recent logs'}), 200
        # Apply the decision to the grid — this is what actually
        # cancels orders, pauses sides, or halts the grid.
        # Without this call, decisions are recorded but never enforced.
        consensus = result.get('consensus', {})
        engine.apply_magi_decision(consensus)
        return jsonify({
            'ok': True,
            'consensus': consensus,
            'timestamp': result.get('timestamp'),
        })
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


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)
