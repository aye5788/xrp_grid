#!/usr/bin/env python3
# =============================================================
# ETH OBSERVER - WEB DASHBOARD
# Run alongside the observer bot.
# Access at http://209.97.145.40:5000 from any browser.
# =============================================================

from flask import Flask, jsonify, render_template_string
import sqlite3
from datetime import datetime, timezone

app = Flask(__name__)
DB_PATH = '/root/eth_observer/observer.db'

# Monthly spend caps — must match orchestrator.py SPEND_CAPS
SPEND_CAPS = {
    "melchior":  10.00,
    "balthasar": 10.00,
    "casper":     0.00,
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>ETH Observer</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #0f0f0f; color: #e0e0e0; padding: 20px; }
        h1 { font-size: 18px; font-weight: 500; color: #fff; margin-bottom: 16px; }
        h2 { font-size: 13px; font-weight: 500; color: #888; text-transform: uppercase;
             letter-spacing: 0.06em; margin: 24px 0 10px; }
        .tabs { display: flex; gap: 4px; margin-bottom: 24px; }
        .tab { padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px;
               color: #888; background: #1a1a1a; border: none; }
        .tab.active { background: #2a2a2a; color: #fff; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
                gap: 12px; margin-bottom: 8px; }
        .card { background: #1a1a1a; border-radius: 8px; padding: 14px 16px; }
        .card .label { font-size: 11px; color: #666; margin-bottom: 4px; }
        .card .value { font-size: 22px; font-weight: 500; color: #fff; }
        .card .sub { font-size: 11px; color: #555; margin-top: 2px; }
        .bull { color: #5a9e2f; }
        .bear { color: #c0392b; }
        .neut { color: #888; }
        .warn { color: #e67e22; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th { text-align: left; padding: 6px 10px; color: #555; font-weight: 400;
             border-bottom: 1px solid #222; }
        td { padding: 6px 10px; border-bottom: 1px solid #1a1a1a; color: #ccc; }
        tr:hover td { background: #1a1a1a; }
        .badge { display: inline-block; font-size: 10px; padding: 2px 6px;
                 border-radius: 3px; }
        .badge-long  { background: #1a2e10; color: #5a9e2f; }
        .badge-short { background: #2e1010; color: #c0392b; }
        .badge-none  { background: #1a1a1a; color: #555; }
        .status { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                  background: #5a9e2f; margin-right: 6px; animation: pulse 2s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        .updated { font-size: 11px; color: #444; margin-top: 20px; }
        .tbl-wrap { background: #1a1a1a; border-radius: 8px; overflow: hidden; }
        .progress-bar-bg { background: #2a2a2a; border-radius: 4px; height: 8px;
                           margin-top: 6px; overflow: hidden; }
        .progress-bar { height: 8px; border-radius: 4px; transition: width 0.3s; }
        .bar-ok   { background: #5a9e2f; }
        .bar-warn { background: #e67e22; }
        .bar-crit { background: #c0392b; }
        .agent-section { margin-bottom: 24px; }
        .agent-label { font-size: 12px; color: #888; margin-bottom: 8px; }
    </style>
</head>
<body>
    <h1><span class="status"></span>ETH Observer Dashboard</h1>

    <div class="tabs">
        <button class="tab active" onclick="showTab('market')">Market</button>
        <button class="tab" onclick="showTab('costs')">API Costs</button>
    </div>

    <!-- MARKET TAB -->
    <div id="tab-market" class="tab-content active">
        <h2>Live State</h2>
        <div class="grid" id="live-grid">
            <div class="card"><div class="label">Loading...</div></div>
        </div>

        <h2>Last 24 Hourly Rows</h2>
        <div class="tbl-wrap">
            <table id="hourly-table">
                <thead><tr>
                    <th>Time (UTC)</th><th>ETH Close</th><th>ETH ret%</th>
                    <th>BTC ret%</th><th>VWAP dev%</th><th>Vol regime</th>
                    <th>Funding</th><th>Premium%</th><th>Signal</th>
                </tr></thead>
                <tbody id="hourly-body"><tr><td colspan="9">Loading...</td></tr></tbody>
            </table>
        </div>

        <h2>Signal Events</h2>
        <div class="tbl-wrap">
            <table id="signal-table">
                <thead><tr>
                    <th>Time (UTC)</th><th>Dir</th><th>ETH price</th>
                    <th>VWAP dev%</th><th>Vol</th><th>Funding</th>
                    <th>Win 1h</th><th>Win 4h</th><th>Ret 1h%</th><th>Ret 4h%</th>
                </tr></thead>
                <tbody id="signal-body"><tr><td colspan="10">Loading...</td></tr></tbody>
            </table>
        </div>
    </div>

    <!-- COST TAB -->
    <div id="tab-costs" class="tab-content">
        <h2>Monthly Spend vs Cap</h2>
        <div class="grid" id="cost-summary-grid">
            <div class="card"><div class="label">Loading...</div></div>
        </div>

        <h2>Per-Agent Breakdown</h2>
        <div id="agent-breakdown"></div>

        <h2>Recent API Calls</h2>
        <div class="tbl-wrap">
            <table id="cost-table">
                <thead><tr>
                    <th>Time (UTC)</th><th>Decision</th><th>Agent</th>
                    <th>Model</th><th>Input tokens</th><th>Output tokens</th><th>Cost (USD)</th>
                </tr></thead>
                <tbody id="cost-body"><tr><td colspan="7">Loading...</td></tr></tbody>
            </table>
        </div>
    </div>

    <div class="updated" id="updated"></div>

<script>
function showTab(name) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    event.target.classList.add('active');
}

function fmt(v, dec=2) {
    if (v === null || v === undefined) return '—';
    return parseFloat(v).toFixed(dec);
}
function clr(v) {
    if (v === null || v === undefined) return 'neut';
    return parseFloat(v) > 0 ? 'bull' : parseFloat(v) < 0 ? 'bear' : 'neut';
}
function fmtFunding(v) {
    if (v === null || v === undefined) return '—';
    return parseFloat(v).toFixed(8);
}
function barClass(pct) {
    if (pct >= 80) return 'bar-crit';
    if (pct >= 50) return 'bar-warn';
    return 'bar-ok';
}
function barColor(pct) {
    return pct >= 80 ? '#c0392b' : pct >= 50 ? '#e67e22' : '#5a9e2f';
}

async function refresh() {
    try {
        const r = await fetch('/api/data');
        const d = await r.json();

        // ── Market tab ─────────────────────────────────────────
        const live = d.live;
        document.getElementById('live-grid').innerHTML = `
            <div class="card">
                <div class="label">ETH Perp</div>
                <div class="value">$${fmt(live.eth_price)}</div>
                <div class="sub">BTC $${fmt(live.btc_price, 0)}</div>
            </div>
            <div class="card">
                <div class="label">VWAP Dev</div>
                <div class="value class="${clr(live.vwap_dev)}">${fmt(live.vwap_dev, 3)}%</div>
                <div class="sub">VWAP $${fmt(live.vwap_24h)}</div>
            </div>
            <div class="card">
                <div class="label">Funding Rate</div>
                <div class="value">${fmtFunding(live.funding_rate)}</div>
                <div class="sub">per hour</div>
            </div>
            <div class="card">
                <div class="label">Vol Regime</div>
                <div class="value">${live.vol_regime || '—'}</div>
                <div class="sub">24h std ${fmt(live.vol_std, 4)}%</div>
            </div>
            <div class="card">
                <div class="label">Avg Spread</div>
                <div class="value">${fmt(live.avg_spread, 4)}%</div>
                <div class="sub">bid-ask cost</div>
            </div>
            <div class="card">
                <div class="label">Signals (total)</div>
                <div class="value">${d.stats.total || 0}</div>
                <div class="sub">Win 1h: ${d.stats.win_1h ? (d.stats.win_1h*100).toFixed(1)+'%' : '—'}</div>
            </div>
        `;

        const hbody = d.hourly.map(r => {
            const sig = r.signal_long ? '<span class="badge badge-long">LONG</span>'
                      : r.signal_short ? '<span class="badge badge-short">SHORT</span>'
                      : '<span class="badge badge-none">—</span>';
            return `<tr>
                <td>${r.timestamp}</td>
                <td>$${fmt(r.eth_close)}</td>
                <td class="${clr(r.eth_ret_pct)}">${fmt(r.eth_ret_pct, 3)}%</td>
                <td class="${clr(r.btc_ret_pct)}">${fmt(r.btc_ret_pct, 3)}%</td>
                <td class="${clr(r.vwap_dev_pct)}">${fmt(r.vwap_dev_pct, 3)}%</td>
                <td>${r.vol_regime || '—'}</td>
                <td>${fmtFunding(r.funding_rate)}</td>
                <td class="${clr(r.premium_pct)}">${fmt(r.premium_pct, 4)}%</td>
                <td>${sig}</td>
            </tr>`;
        }).join('');
        document.getElementById('hourly-body').innerHTML = hbody || '<tr><td colspan="9">No data yet</td></tr>';

        const sbody = d.signals.map(r => {
            const dir = r.direction === 'long'
                ? '<span class="badge badge-long">LONG</span>'
                : '<span class="badge badge-short">SHORT</span>';
            const w1 = r.win_1h === null ? '—' : r.win_1h ? '✓' : '✗';
            const w4 = r.win_4h === null ? '—' : r.win_4h ? '✓' : '✗';
            return `<tr>
                <td>${r.timestamp}</td>
                <td>${dir}</td>
                <td>$${fmt(r.eth_price)}</td>
                <td class="${clr(r.vwap_dev_pct)}">${fmt(r.vwap_dev_pct, 3)}%</td>
                <td>${r.vol_regime || '—'}</td>
                <td>${fmtFunding(r.funding_rate)}</td>
                <td>${w1}</td>
                <td>${w4}</td>
                <td class="${clr(r.outcome_1h)}">${fmt(r.outcome_1h, 3)}%</td>
                <td class="${clr(r.outcome_4h)}">${fmt(r.outcome_4h, 3)}%</td>
            </tr>`;
        }).join('');
        document.getElementById('signal-body').innerHTML = sbody || '<tr><td colspan="10">No signal events yet</td></tr>';

        // ── Cost tab ───────────────────────────────────────────
        const costs = d.costs;
        const caps  = d.spend_caps;

        // Summary cards
        const totalSpent = costs.total_all_time || 0;
        const totalThisMonth = costs.total_this_month || 0;
        const totalCap = (caps.melchior || 0) + (caps.balthasar || 0);
        const monthPct = totalCap > 0 ? (totalThisMonth / totalCap * 100) : 0;

        document.getElementById('cost-summary-grid').innerHTML = `
            <div class="card">
                <div class="label">Total This Month</div>
                <div class="value ${monthPct >= 80 ? 'bear' : monthPct >= 50 ? 'warn' : 'bull'}">
                    $${totalThisMonth.toFixed(4)}
                </div>
                <div class="sub">of $${totalCap.toFixed(2)} combined cap</div>
                <div class="progress-bar-bg">
                    <div class="progress-bar ${barClass(monthPct)}"
                         style="width:${Math.min(monthPct,100)}%"></div>
                </div>
            </div>
            <div class="card">
                <div class="label">Total All Time</div>
                <div class="value">$${totalSpent.toFixed(4)}</div>
                <div class="sub">${costs.total_activations || 0} MAGI activations</div>
            </div>
            <div class="card">
                <div class="label">Avg Cost / Activation</div>
                <div class="value">$${costs.avg_cost_per_activation ? costs.avg_cost_per_activation.toFixed(4) : '—'}</div>
                <div class="sub">all three agents</div>
            </div>
            <div class="card">
                <div class="label">Monthly Projection</div>
                <div class="value ${costs.monthly_projection > totalCap * 0.8 ? 'bear' : 'bull'}">
                    $${costs.monthly_projection ? costs.monthly_projection.toFixed(2) : '—'}
                </div>
                <div class="sub">at current burn rate</div>
            </div>
        `;

        // Per-agent breakdown
        const agents = ['melchior', 'balthasar', 'casper'];
        const agentLabels = {
            melchior:  'Melchior — Claude Sonnet 4.6',
            balthasar: 'Balthasar — GPT-4o',
            casper:    'Casper — Gemini 2.5 Pro (free tier)'
        };
        let breakdownHtml = '';
        for (const agent of agents) {
            const a = costs.by_agent && costs.by_agent[agent] ? costs.by_agent[agent] : {};
            const cap = caps[agent] || 0;
            const spent = a.month_cost || 0;
            const pct = cap > 0 ? (spent / cap * 100) : 0;
            const isFree = cap === 0;

            breakdownHtml += `
            <div class="agent-section">
                <div class="agent-label">${agentLabels[agent]}</div>
                <div class="grid">
                    <div class="card">
                        <div class="label">This Month</div>
                        <div class="value ${isFree ? 'bull' : pct >= 80 ? 'bear' : 'bull'}">
                            ${isFree ? 'FREE' : '$' + spent.toFixed(4)}
                        </div>
                        <div class="sub">${isFree ? 'Google free tier' : 'of $' + cap.toFixed(2) + ' cap'}</div>
                        ${!isFree ? `<div class="progress-bar-bg">
                            <div class="progress-bar ${barClass(pct)}"
                                 style="width:${Math.min(pct,100)}%"></div>
                        </div>` : ''}
                    </div>
                    <div class="card">
                        <div class="label">All Time</div>
                        <div class="value">${isFree ? 'FREE' : '$' + (a.total_cost || 0).toFixed(4)}</div>
                        <div class="sub">${a.total_calls || 0} calls</div>
                    </div>
                    <div class="card">
                        <div class="label">Avg Tokens / Call</div>
                        <div class="value" style="font-size:16px">
                            ${a.avg_input_tokens ? Math.round(a.avg_input_tokens) : '—'}
                            <span style="font-size:11px;color:#555"> in</span>
                        </div>
                        <div class="sub">${a.avg_output_tokens ? Math.round(a.avg_output_tokens) : '—'} out</div>
                    </div>
                </div>
            </div>`;
        }
        document.getElementById('agent-breakdown').innerHTML = breakdownHtml;

        // Recent calls table
        const cbody = (costs.recent_calls || []).map(r => `<tr>
            <td>${r.timestamp}</td>
            <td>#${r.decision_id}</td>
            <td>${r.agent}</td>
            <td style="color:#555;font-size:11px">${r.model}</td>
            <td>${r.input_tokens ? r.input_tokens.toLocaleString() : '—'}</td>
            <td>${r.output_tokens ? r.output_tokens.toLocaleString() : '—'}</td>
            <td class="${r.cost_usd > 0 ? 'neut' : 'bull'}">$${r.cost_usd ? r.cost_usd.toFixed(6) : '0.000000'}</td>
        </tr>`).join('');
        document.getElementById('cost-body').innerHTML = cbody ||
            '<tr><td colspan="7" style="color:#555;padding:20px">No API calls logged yet — costs appear after first MAGI activation</td></tr>';

        document.getElementById('updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();

    } catch(e) {
        console.error(e);
    }
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML)

@app.route('/api/data')
def api_data():
    conn = get_db()
    try:
        # Latest hourly row
        latest = conn.execute("""
            SELECT * FROM hourly ORDER BY timestamp DESC LIMIT 1
        """).fetchone()

        live = {}
        if latest:
            live = {
                'eth_price':    latest['eth_close'],
                'btc_price':    latest['btc_close'],
                'vwap_24h':     latest['vwap_24h'],
                'vwap_dev':     latest['vwap_dev_pct'],
                'vol_regime':   latest['vol_regime'],
                'vol_std':      latest['vol_24h_std'],
                'funding_rate': latest['funding_rate'],
                'premium_pct':  latest['premium_pct'],
                'avg_spread':   latest['avg_spread_pct'],
            }

        # Last 24 hourly rows
        hourly  = [dict(r) for r in conn.execute("""
            SELECT * FROM hourly ORDER BY timestamp DESC LIMIT 24
        """).fetchall()]

        # Signal events
        signals = [dict(r) for r in conn.execute("""
            SELECT * FROM signal_events ORDER BY timestamp DESC LIMIT 50
        """).fetchall()]

        # Signal stats
        stats = conn.execute("""
            SELECT COUNT(*) as total,
                   AVG(win_1h) as win_1h,
                   AVG(win_4h) as win_4h,
                   AVG(outcome_1h) as avg_ret_1h
            FROM signal_events WHERE outcome_1h IS NOT NULL
        """).fetchone()

        # ── Cost data ──────────────────────────────────────────
        costs = {}

        # Check if api_costs table exists yet
        has_costs = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='api_costs'
        """).fetchone()

        if has_costs:
            now_utc   = datetime.now(timezone.utc)
            month_start = now_utc.strftime("%Y-%m-01")

            # Totals
            totals = conn.execute("""
                SELECT
                    COUNT(DISTINCT decision_id) as total_activations,
                    SUM(cost_usd) as total_all_time,
                    SUM(CASE WHEN timestamp >= ? THEN cost_usd ELSE 0 END) as total_this_month
                FROM api_costs
            """, (month_start,)).fetchone()

            total_activations = totals["total_activations"] or 0
            total_all_time    = totals["total_all_time"] or 0.0
            total_this_month  = totals["total_this_month"] or 0.0

            # Avg cost per activation
            avg_cost = total_all_time / total_activations if total_activations > 0 else 0

            # Monthly projection — based on activations so far this month
            days_elapsed = max(now_utc.day, 1)
            days_in_month = 30
            monthly_projection = (total_this_month / days_elapsed) * days_in_month if total_this_month > 0 else 0

            # Per-agent breakdown
            by_agent = {}
            agent_rows = conn.execute("""
                SELECT
                    agent,
                    COUNT(*) as total_calls,
                    SUM(cost_usd) as total_cost,
                    SUM(CASE WHEN timestamp >= ? THEN cost_usd ELSE 0 END) as month_cost,
                    AVG(input_tokens) as avg_input_tokens,
                    AVG(output_tokens) as avg_output_tokens
                FROM api_costs
                GROUP BY agent
            """, (month_start,)).fetchall()

            for row in agent_rows:
                by_agent[row["agent"]] = dict(row)

            # Recent calls
            recent_calls = [dict(r) for r in conn.execute("""
                SELECT timestamp, decision_id, agent, model,
                       input_tokens, output_tokens, cost_usd
                FROM api_costs
                ORDER BY timestamp DESC
                LIMIT 30
            """).fetchall()]

            costs = {
                "total_activations":      total_activations,
                "total_all_time":         total_all_time,
                "total_this_month":       total_this_month,
                "avg_cost_per_activation": avg_cost,
                "monthly_projection":     monthly_projection,
                "by_agent":               by_agent,
                "recent_calls":           recent_calls,
            }

        return jsonify({
            'live':       live,
            'hourly':     hourly,
            'signals':    signals,
            'stats':      dict(stats) if stats else {},
            'costs':      costs,
            'spend_caps': SPEND_CAPS,
        })

    finally:
        conn.close()

@app.route('/api/snapshot')
def api_snapshot():
    conn = get_db()
    try:
        now_utc = datetime.now(timezone.utc)

        def _parse_ts(ts_str):
            if not ts_str:
                return None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            return None

        def _minutes_since(ts_str):
            dt = _parse_ts(ts_str)
            if dt is None:
                return None
            return int((now_utc - dt).total_seconds() / 60)

        # ── Observer ──────────────────────────────────────────────
        hourly_row = conn.execute("""
            SELECT * FROM hourly ORDER BY timestamp DESC LIMIT 1
        """).fetchone()

        observer = {"latest_hourly_timestamp": None}
        if hourly_row:
            h  = dict(hourly_row)
            ts = h.get("timestamp")
            observer = {
                "latest_hourly_timestamp":  ts,
                "minutes_since_last_write": _minutes_since(ts),
                "eth_price":                h.get("eth_close"),
                "vwap_dev_pct":             h.get("vwap_dev_pct"),
                "vol_regime":               h.get("vol_regime"),
                "btc_price":                h.get("btc_close"),
                "btc_ret_pct":              h.get("btc_ret_pct"),
                "funding_rate":             h.get("funding_rate"),
                "avg_spread_pct":           h.get("avg_spread_pct"),
                "coinbase_kraken_premium":  h.get("premium_pct"),
            }

        # ── Market context ────────────────────────────────────────
        market_context = {}
        has_mc = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='market_context'
        """).fetchone()
        if has_mc:
            mc_row = conn.execute("""
                SELECT * FROM market_context ORDER BY timestamp DESC LIMIT 1
            """).fetchone()
            if mc_row:
                mc = dict(mc_row)
                market_context = {
                    "timestamp":             mc.get("timestamp"),
                    "eth_dvol":              mc.get("eth_dvol"),
                    "put_call_ratio":        mc.get("put_call_ratio"),
                    "put_call_direction":    mc.get("put_call_direction"),
                    "eth_netflow_4h":        mc.get("eth_netflow_4h"),
                    "eth_netflow_direction": mc.get("eth_netflow_direction"),
                    "dxy_value":             mc.get("dxy_value"),
                    "yield_10y":             mc.get("yield_10y"),
                }

        # ── MAGI ──────────────────────────────────────────────────
        total_decisions = conn.execute(
            "SELECT COUNT(*) as n FROM magi_decisions"
        ).fetchone()["n"]

        latest_dec = conn.execute("""
            SELECT * FROM magi_decisions ORDER BY timestamp DESC LIMIT 1
        """).fetchone()

        magi = {"total_decisions": total_decisions}
        if latest_dec:
            d      = dict(latest_dec)
            ts     = d.get("timestamp")
            m_btc  = d.get("melchior_btc_direction_assumed")
            c_btc  = d.get("casper_btc_direction_assumed")
            b_risk = d.get("balthasar_risk_assessment")

            btc_mismatch = bool(
                m_btc and c_btc
                and m_btc != c_btc
                and m_btc != "neutral"
                and c_btc != "neutral"
            )
            permissive_on_bearish_btc = bool(
                m_btc == "bearish" and b_risk == "permissive"
            )

            magi = {
                "total_decisions":              total_decisions,
                "latest_decision_timestamp":    ts,
                "minutes_since_last_decision":  _minutes_since(ts),
                "latest_consensus":             d.get("consensus_result"),
                "latest_consensus_reason":      d.get("consensus_reason"),
                "latest_contracts":             d.get("contracts_decided"),
                "melchior_vote":                d.get("melchior_vote"),
                "balthasar_vote":               d.get("balthasar_vote"),
                "casper_vote":                  d.get("casper_vote"),
                "melchior_vol_regime_assumed":  d.get("melchior_vol_regime_assumed"),
                "balthasar_risk_assessment":    b_risk,
                "casper_macro_regime":          d.get("casper_macro_regime"),
                "assumption_mismatch_flagged":  btc_mismatch or permissive_on_bearish_btc,
            }

        # ── Costs ─────────────────────────────────────────────────
        costs = {
            "total_this_month_usd":     0.0,
            "melchior_this_month_usd":  0.0,
            "balthasar_this_month_usd": 0.0,
            "casper_this_month_usd":    0.0,
        }
        has_costs = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='api_costs'
        """).fetchone()
        if has_costs:
            month_start = now_utc.strftime("%Y-%m-01")
            rows = conn.execute("""
                SELECT agent, SUM(cost_usd) as month_cost
                FROM api_costs
                WHERE timestamp >= ?
                GROUP BY agent
            """, (month_start,)).fetchall()

            per_agent   = {r["agent"]: (r["month_cost"] or 0.0) for r in rows}
            total_month = sum(per_agent.values())
            costs = {
                "total_this_month_usd":     round(total_month, 6),
                "melchior_this_month_usd":  round(per_agent.get("melchior",  0.0), 6),
                "balthasar_this_month_usd": round(per_agent.get("balthasar", 0.0), 6),
                "casper_this_month_usd":    round(per_agent.get("casper",    0.0), 6),
            }

        # ── Performance ───────────────────────────────────────────
        performance = {}
        has_decisions = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='magi_decisions'
        """).fetchone()
        if has_decisions:
            perf = conn.execute("""
                SELECT
                    COUNT(*) as total_decisions,
                    SUM(CASE WHEN consensus_result IN ('long','short') THEN 1 ELSE 0 END)
                        as total_trades,
                    SUM(CASE WHEN consensus_result NOT IN ('long','short') THEN 1 ELSE 0 END)
                        as total_no_trades,
                    SUM(CASE WHEN consensus_result = 'long'  THEN 1 ELSE 0 END) as long_signals,
                    SUM(CASE WHEN consensus_result = 'short' THEN 1 ELSE 0 END) as short_signals,
                    SUM(CASE WHEN outcome_1h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN 1 ELSE 0 END)
                        as completed_1h,
                    SUM(CASE WHEN win_1h = 1 THEN 1 ELSE 0 END) as wins_1h,
                    SUM(CASE WHEN win_1h = 0
                              AND outcome_1h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN 1 ELSE 0 END)
                        as losses_1h,
                    AVG(CASE WHEN outcome_1h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN outcome_1h END)
                        as avg_outcome_1h,
                    AVG(CASE WHEN outcome_4h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN outcome_4h END)
                        as avg_outcome_4h,
                    AVG(CASE WHEN outcome_8h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN outcome_8h END)
                        as avg_outcome_8h
                FROM magi_decisions
            """).fetchone()

            completed_1h = perf["completed_1h"] or 0
            wins_1h      = perf["wins_1h"] or 0
            win_rate_1h  = round(wins_1h / completed_1h, 3) if completed_1h > 0 else None

            def _avg(v):
                return round(v, 4) if v is not None else None

            regime_rows = conn.execute("""
                SELECT
                    COALESCE(vol_regime_at_trigger, 'unknown') as regime,
                    COUNT(*) as decisions,
                    SUM(CASE WHEN consensus_result IN ('long','short') THEN 1 ELSE 0 END)
                        as trades,
                    AVG(CASE WHEN outcome_1h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN outcome_1h END)
                        as avg_1h,
                    AVG(CASE WHEN outcome_4h IS NOT NULL
                              AND consensus_result IN ('long','short') THEN outcome_4h END)
                        as avg_4h
                FROM magi_decisions
                GROUP BY COALESCE(vol_regime_at_trigger, 'unknown')
                ORDER BY regime
            """).fetchall()

            by_regime = {
                rr["regime"]: {
                    "decisions": rr["decisions"],
                    "trades":    rr["trades"],
                    "avg_1h":    _avg(rr["avg_1h"]),
                    "avg_4h":    _avg(rr["avg_4h"]),
                }
                for rr in regime_rows
            }

            trade_rows = conn.execute("""
                SELECT timestamp, consensus_result, vol_regime_at_trigger,
                       vwap_dev_at_trigger, outcome_1h, outcome_4h, win_1h, win_4h
                FROM magi_decisions
                WHERE consensus_result IN ('long','short')
                ORDER BY timestamp DESC
                LIMIT 5
            """).fetchall()

            recent_trades = [
                {
                    "timestamp":  tr["timestamp"],
                    "consensus":  tr["consensus_result"],
                    "vol_regime": tr["vol_regime_at_trigger"],
                    "vwap_dev":   tr["vwap_dev_at_trigger"],
                    "outcome_1h": tr["outcome_1h"],
                    "outcome_4h": tr["outcome_4h"],
                    "win_1h":     tr["win_1h"],
                    "win_4h":     tr["win_4h"],
                }
                for tr in trade_rows
            ]

            performance = {
                "total_decisions": perf["total_decisions"] or 0,
                "total_trades":    perf["total_trades"]    or 0,
                "total_no_trades": perf["total_no_trades"] or 0,
                "long_signals":    perf["long_signals"]    or 0,
                "short_signals":   perf["short_signals"]   or 0,
                "completed_1h":    completed_1h,
                "wins_1h":         wins_1h,
                "losses_1h":       perf["losses_1h"]       or 0,
                "win_rate_1h":     win_rate_1h,
                "avg_outcome_1h":  _avg(perf["avg_outcome_1h"]),
                "avg_outcome_4h":  _avg(perf["avg_outcome_4h"]),
                "avg_outcome_8h":  _avg(perf["avg_outcome_8h"]),
                "by_regime":       by_regime,
                "recent_trades":   recent_trades,
            }

        resp = jsonify({
            "generated_at":   now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "observer":       observer,
            "market_context": market_context,
            "magi":           magi,
            "costs":          costs,
            "performance":    performance,
        })
        resp.headers["Content-Type"] = "application/json"
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        conn.close()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
