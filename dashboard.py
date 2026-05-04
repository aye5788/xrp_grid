from flask import Flask, jsonify, render_template_string, request
import logging
import threading
import os
from datetime import datetime, timezone
from database import (
    get_latest_indicators, get_current_grid_state,
    get_latest_inventory, get_recent_magi_decisions,
    get_cost_summary, get_cost_today, get_all_shadow_states
)
from grid.engine import GridEngine
from magi.costs import get_fixed_monthly_total, FIXED_SUBSCRIPTIONS
from magi.learning import run_learning_cycle
from guardrails import check_all_guardrails, kill_switch_active
from config import KILL_SWITCH_FILE

log = logging.getLogger('dashboard')
app = Flask(__name__)
engine = GridEngine(paper=True)

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
    <div style="margin-top:10px;">
        <button onclick="triggerLearning()" style="background:#00ccff22; color:#00ccff; border:1px solid #00ccff; padding:10px 20px; font-family:monospace; font-size:0.9em; cursor:pointer; border-radius:4px;">
            Trigger Learning Cycle
        </button>
        <button onclick="triggerLearning(true)" style="background:#ffaa0022; color:#ffaa00; border:1px solid #ffaa00; padding:10px 20px; font-family:monospace; font-size:0.9em; cursor:pointer; border-radius:4px; margin-left:10px;">
            Force (Weekend Override)
        </button>
        <button onclick="toggleKill()" id="kill-btn" style="background:{{ '#ff000033' if kill_switch else '#33000022' }}; color:{{ '#ff4444' if kill_switch else '#aa3333' }}; border:1px solid {{ '#ff4444' if kill_switch else '#550000' }}; padding:10px 20px; font-family:monospace; font-size:0.9em; cursor:pointer; border-radius:4px; margin-left:10px;">
            {{ '⬛ DEACTIVATE KILL SWITCH' if kill_switch else '⬛ ACTIVATE KILL SWITCH' }}
        </button>
        <span id="learning-status" style="margin-left:15px; color:#888;"></span>
        <span id="kill-status" style="margin-left:15px; color:#888;"></span>
    </div>
    <script>
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

def check_scheduler_alive():
    try:
        import os
        log_path = '/root/xrp_grid/magi.log'
        if not os.path.exists(log_path):
            return False
        mtime = os.path.getmtime(log_path)
        age_minutes = (datetime.now(timezone.utc).timestamp() - mtime) / 60
        return age_minutes < 90
    except:
        return False

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
    price = engine.get_current_price()

    cost_today_data = get_cost_today()
    cost_30d_data = get_cost_summary(days_back=30)
    total_cost_30d = sum((c.get('cost') or 0) for c in cost_30d_data)
    fixed_breakdown = ', '.join(f"{k}: ${v}" for k, v in FIXED_SUBSCRIPTIONS.items() if v > 0)

    guardrails_ok, guardrail_failures = check_all_guardrails()
    ks_active = kill_switch_active()
    shadow_variants = get_all_shadow_states()

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
        active_levels=engine.level_count
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

@app.route('/api/trigger_learning', methods=['POST'])
def trigger_learning():
    force = request.args.get('force', 'false').lower() == 'true'
    try:
        result = run_learning_cycle(force=force)
        return jsonify(result or {'skipped': True, 'reason': 'unknown'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/shadow_variants')
def api_shadow_variants():
    shadow_variants = get_all_shadow_states()
    return jsonify({
        'variants': shadow_variants,
        'active_levels': engine.level_count
    })

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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)
