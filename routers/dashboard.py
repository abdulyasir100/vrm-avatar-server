"""GET /dashboard — operational metrics JSON, GET /dashboard/ui — HTML view.

Read-only: every number is derived from existing logs + memory.db. See
services/dashboard_stats.py for the data layer.
"""

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from services import dashboard_stats

router = APIRouter()


@router.get("/dashboard")
async def dashboard_json(window: str = Query("24h", pattern="^(24h|7d|all)$")):
    """Aggregate metrics for the dashboard. Window = 24h | 7d | all."""
    return JSONResponse(dashboard_stats.collect_all(window))


@router.get("/dashboard/ui", response_class=HTMLResponse)
async def dashboard_ui():
    """Operational dashboard UI. Reuses weather-UI aesthetic (starfield,
    glass cards, Catppuccin palette) but grid-laid for 7 metric cards."""
    return _DASHBOARD_HTML


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Avatar Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    background: #0a0e1a;
    color: #e0e6f0;
    padding-bottom: 40px;
  }

  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background:
      radial-gradient(1px 1px at 10% 20%, rgba(150,180,255,0.8), transparent),
      radial-gradient(1px 1px at 30% 60%, rgba(180,200,255,0.6), transparent),
      radial-gradient(1.5px 1.5px at 50% 10%, rgba(200,220,255,0.9), transparent),
      radial-gradient(1px 1px at 70% 80%, rgba(160,190,255,0.5), transparent),
      radial-gradient(1px 1px at 90% 40%, rgba(140,170,255,0.7), transparent),
      radial-gradient(1.5px 1.5px at 20% 90%, rgba(170,200,255,0.6), transparent),
      radial-gradient(1px 1px at 60% 30%, rgba(190,210,255,0.4), transparent),
      radial-gradient(1px 1px at 80% 70%, rgba(130,160,255,0.8), transparent);
    z-index: 0;
    pointer-events: none;
    animation: twinkle 8s ease-in-out infinite alternate;
  }
  @keyframes twinkle { 0%{opacity:.6;} 100%{opacity:1;} }

  .container {
    position: relative;
    z-index: 1;
    max-width: 1280px;
    margin: 0 auto;
    padding: 24px 16px;
  }

  .header { text-align: center; margin-bottom: 24px; }
  .header h1 {
    font-size: 1.6rem;
    font-weight: 700;
    background: linear-gradient(135deg, #60a5fa, #a78bfa, #60a5fa);
    background-size: 200% auto;
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: shimmer 3s linear infinite;
    letter-spacing: 0.5px;
  }
  @keyframes shimmer { to { background-position: 200% center; } }
  .header .sub { color: #7b8db5; margin-top: 6px; font-size: 0.85rem; }
  .header .updated { color: #4a5a7a; margin-top: 2px; font-size: 0.72rem; }

  .toolbar {
    display: flex;
    justify-content: center;
    gap: 8px;
    margin: 20px 0 24px;
  }
  .tab {
    padding: 8px 18px;
    background: rgba(30,40,80,0.5);
    border: 1px solid rgba(96,165,250,0.15);
    border-radius: 999px;
    color: #b8c5e0;
    font-size: 0.85rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    backdrop-filter: blur(10px);
  }
  .tab:hover { border-color: rgba(96,165,250,0.4); color: #fff; }
  .tab.active {
    background: linear-gradient(135deg, rgba(96,165,250,0.4), rgba(167,139,250,0.4));
    border-color: rgba(167,139,250,0.6);
    color: #fff;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 16px;
  }

  .card {
    background: linear-gradient(135deg, rgba(30,40,80,0.7), rgba(20,25,50,0.8));
    border: 1px solid rgba(96,165,250,0.15);
    border-radius: 16px;
    padding: 18px 20px;
    backdrop-filter: blur(10px);
  }
  .card h2 {
    font-size: 0.85rem;
    font-weight: 600;
    color: #a78bfa;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .card h2 .count {
    color: #60a5fa;
    font-weight: 700;
    text-transform: none;
    letter-spacing: 0;
    font-size: 1rem;
  }

  .bar-row {
    display: flex;
    align-items: center;
    margin-bottom: 6px;
    font-size: 0.82rem;
  }
  .bar-row .label {
    width: 120px;
    color: #b8c5e0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .bar-row .bar {
    flex: 1;
    height: 18px;
    background: rgba(96,165,250,0.08);
    border-radius: 4px;
    overflow: hidden;
    margin: 0 8px;
    position: relative;
  }
  .bar-row .bar .fill {
    height: 100%;
    background: linear-gradient(90deg, #60a5fa, #a78bfa);
    border-radius: 4px;
  }
  .bar-row .val {
    color: #e0e6f0;
    font-weight: 600;
    min-width: 32px;
    text-align: right;
  }
  .bar-row.zero .label { color: #4a5a7a; }
  .bar-row.zero .bar .fill { background: #2a334a; }
  .bar-row.zero .val { color: #4a5a7a; }

  .empty { color: #4a5a7a; font-size: 0.85rem; padding: 8px 0; font-style: italic; }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 8px 20px;
  }
  .stat-grid .stat .num {
    font-size: 1.4rem;
    font-weight: 700;
    color: #60a5fa;
  }
  .stat-grid .stat .lbl {
    font-size: 0.72rem;
    color: #7b8db5;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .mood-current {
    display: flex;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 12px;
  }
  .mood-current .value {
    font-size: 2rem;
    font-weight: 700;
    background: linear-gradient(135deg, #60a5fa, #a78bfa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .mood-current .bracket {
    padding: 4px 10px;
    background: rgba(167,139,250,0.2);
    border: 1px solid rgba(167,139,250,0.4);
    border-radius: 999px;
    font-size: 0.72rem;
    color: #c4b5fd;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .app-row {
    padding: 8px 0;
    border-bottom: 1px solid rgba(96,165,250,0.08);
  }
  .app-row:last-child { border-bottom: none; }
  .app-row .name {
    font-weight: 600;
    color: #e0e6f0;
    display: flex;
    justify-content: space-between;
  }
  .app-row .name .commits { color: #60a5fa; }
  .app-row .commit-msg {
    font-size: 0.78rem;
    color: #7b8db5;
    margin-top: 4px;
  }
  .app-row .commit-sha {
    font-size: 0.72rem;
    color: #4a5a7a;
    font-family: monospace;
    margin-top: 2px;
  }
  .app-row .err { color: #f87171; }

  .plugin-row {
    display: grid;
    grid-template-columns: 1fr auto auto auto;
    gap: 12px;
    align-items: center;
    padding: 7px 0;
    border-bottom: 1px solid rgba(96,165,250,0.06);
    font-size: 0.82rem;
  }
  .plugin-row:last-child { border-bottom: none; }
  .plugin-row .name { color: #e0e6f0; font-weight: 500; }
  .plugin-row .num { color: #7b8db5; min-width: 22px; text-align: right; }
  .plugin-row .num.zero { color: #4a5a7a; }
  .plugin-row .badge {
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-weight: 600;
  }
  .plugin-row .badge.dead {
    background: rgba(248,113,113,0.15);
    border: 1px solid rgba(248,113,113,0.4);
    color: #fca5a5;
  }
  .plugin-row .badge.background {
    background: rgba(123,141,181,0.12);
    border: 1px solid rgba(123,141,181,0.3);
    color: #94a3b8;
  }
  .plugin-row .badge.active {
    background: rgba(96,165,250,0.15);
    border: 1px solid rgba(96,165,250,0.4);
    color: #93c5fd;
  }
  .plugin-row.dead .name { color: #fca5a5; }

  .footnote {
    text-align: center;
    color: #4a5a7a;
    font-size: 0.7rem;
    margin-top: 24px;
    line-height: 1.6;
  }

  @media (max-width: 480px) {
    .grid { grid-template-columns: 1fr; }
    .header h1 { font-size: 1.3rem; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Avatar Dashboard</h1>
    <div class="sub" id="char-name">Loading…</div>
    <div class="updated" id="updated">—</div>
  </div>

  <div class="toolbar">
    <button class="tab active" data-window="24h">Last 24h</button>
    <button class="tab" data-window="7d">Last 7d</button>
    <button class="tab" data-window="all">All-time</button>
  </div>

  <div class="grid">
    <div class="card">
      <h2>Tool usage <span class="count" id="tool-count">0</span></h2>
      <div id="tool-usage"></div>
    </div>

    <div class="card">
      <h2>Idle / scheduled output <span class="count" id="idle-count">0</span></h2>
      <div id="idle-categories"></div>
    </div>

    <div class="card">
      <h2>Errors &amp; fallbacks</h2>
      <div class="stat-grid">
        <div class="stat"><div class="num" id="err-total">0</div><div class="lbl">total errors</div></div>
        <div class="stat"><div class="num" id="wakeups">0</div><div class="lbl">sleep wake-ups</div></div>
      </div>
      <div style="margin-top:12px;font-size:.78rem;color:#7b8db5;" id="err-breakdown"></div>
      <div style="margin-top:6px;font-size:.78rem;color:#7b8db5;" id="fallback-hits"></div>
    </div>

    <div class="card">
      <h2>Mood &amp; emotion trend</h2>
      <div class="mood-current">
        <span class="value" id="mood-value">–</span>
        <span class="bracket" id="mood-bracket">unknown</span>
      </div>
      <div id="emotions"></div>
    </div>

    <div class="card">
      <h2>Plugin commands <span class="count" id="plugin-count">0</span></h2>
      <div id="plugin-commands"></div>
    </div>

    <div class="card">
      <h2>Conversation volume</h2>
      <div class="stat-grid">
        <div class="stat"><div class="num" id="conv-user">0</div><div class="lbl">your messages</div></div>
        <div class="stat"><div class="num" id="conv-asst">0</div><div class="lbl">her messages</div></div>
        <div class="stat"><div class="num" id="conv-days">0</div><div class="lbl">active days</div></div>
        <div class="stat"><div class="num" id="conv-ratio">0</div><div class="lbl">her : you ratio</div></div>
      </div>
    </div>

    <div class="card">
      <h2>Plugin scoreboard <span class="count" id="dead-count">—</span></h2>
      <div style="font-size:.72rem;color:#7b8db5;margin-bottom:8px;">tool calls · /p.commands · background events · status</div>
      <div id="plugin-scoreboard"></div>
    </div>

    <div class="card">
      <h2>User state <span class="count" id="user-state-age">—</span></h2>
      <div id="user-state-body" style="font-size:.85rem;line-height:1.5;">
        <div style="color:#7b8db5;">loading…</div>
      </div>
    </div>

    <div class="card">
      <h2>App activity (sandbox commits)</h2>
      <div id="app-activity"></div>
    </div>
  </div>

  <div class="footnote">
    Auto-refresh every 60s.<br>
    Logs rotate at 5 MB &times; 3 backups, so all-time log-derived metrics are bounded by ~20 MB of history.<br>
    Conversation volume reads from <code>memory.db</code> directly, so it can go back further.
  </div>
</div>

<script>
let currentWindow = '24h';

function el(tag, attrs, text) {
  const e = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (k === 'className') e.className = attrs[k];
      else if (k === 'style') e.setAttribute('style', attrs[k]);
      else if (k === 'title') e.title = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
  }
  if (text !== undefined) e.textContent = text;
  return e;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function emptyMsg(node, text) {
  clear(node);
  node.appendChild(el('div', {className: 'empty'}, text));
}

function bars(items, containerId) {
  const container = document.getElementById(containerId);
  clear(container);
  const entries = Object.entries(items);
  if (entries.length === 0) {
    container.appendChild(el('div', {className: 'empty'}, 'No data in this window.'));
    return 0;
  }
  const max = Math.max(...entries.map(function (e) { return e[1]; }), 1);
  let total = 0;
  for (const [label, val] of entries) {
    total += val;
    const pct = (val / max) * 100;
    const row = el('div', {className: 'bar-row' + (val === 0 ? ' zero' : '')});
    row.appendChild(el('span', {className: 'label', title: label}, label));
    const barWrap = el('span', {className: 'bar'});
    barWrap.appendChild(el('span', {className: 'fill', style: 'width:' + pct + '%'}));
    row.appendChild(barWrap);
    row.appendChild(el('span', {className: 'val'}, String(val)));
    container.appendChild(row);
  }
  return total;
}

function renderScoreboard(rows) {
  const node = document.getElementById('plugin-scoreboard');
  clear(node);
  if (!rows || rows.length === 0) {
    node.appendChild(el('div', {className: 'empty'}, 'No plugins enabled.'));
    return 0;
  }
  let deadCount = 0;
  for (const p of rows) {
    if (p.status === 'dead') deadCount++;
    const row = el('div', {className: 'plugin-row' + (p.status === 'dead' ? ' dead' : '')});
    const tools = p.tools.length + ' tool' + (p.tools.length === 1 ? '' : 's');
    const cmds = p.commands.length + ' cmd' + (p.commands.length === 1 ? '' : 's');
    const tip = tools + ' / ' + cmds;
    row.appendChild(el('span', {className: 'name', title: tip}, p.plugin));
    row.appendChild(el('span',
      {className: 'num' + (p.tool_calls === 0 ? ' zero' : ''), title: 'tool calls'},
      String(p.tool_calls)));
    row.appendChild(el('span',
      {className: 'num' + (p.command_calls === 0 ? ' zero' : ''), title: '/p.command invocations'},
      String(p.command_calls)));
    const bg = (typeof p.background_events === 'number') ? p.background_events : 0;
    row.appendChild(el('span',
      {className: 'num' + (bg === 0 ? ' zero' : ''), title: 'background events (reminders, scheduled, idle)'},
      String(bg)));
    const badgeClass = p.status === 'background_only' ? 'background' : p.status;
    const badgeLabel = p.status === 'background_only' ? 'bg-only' : p.status;
    row.appendChild(el('span', {className: 'badge ' + badgeClass}, badgeLabel));
    node.appendChild(row);
  }
  return deadCount;
}

function renderUserState(s) {
  const body = document.getElementById('user-state-body');
  const ageNode = document.getElementById('user-state-age');
  clear(body);
  if (!s || !s.updated_at) {
    body.appendChild(el('div', {className: 'empty'}, 'No state derived yet.'));
    ageNode.textContent = '—';
    return;
  }
  const ageMin = Math.round((Date.now() / 1000 - s.updated_at) / 60);
  ageNode.textContent = ageMin + 'm ago';

  const palette = {
    fit: '#86efac', tired: '#fcd34d', sick: '#fca5a5',
    happy: '#86efac', normal: '#a5b4fc', sad: '#93c5fd',
    stressed: '#fca5a5', busy: '#fcd34d',
    affectionate: '#f9a8d4', playful: '#c4b5fd', angry: '#fca5a5',
    low: '#fca5a5', high: '#86efac'
  };

  function chip(label, value) {
    const color = palette[value] || '#7b8db5';
    const row = el('div', {style: 'display:flex;gap:10px;align-items:baseline;margin-bottom:4px;'});
    row.appendChild(el('span', {style: 'color:#7b8db5;min-width:64px;font-size:.78rem;'}, label));
    row.appendChild(el('span', {style: 'color:' + color + ';font-weight:600;'}, value || '—'));
    body.appendChild(row);
  }

  chip('physical', s.physical);
  chip('mental',   s.mental);
  chip('energy',   s.energy);

  if (s.context) {
    const ctx = el('div', {style: 'color:#b8c5e0;margin-top:8px;padding:8px;background:rgba(96,165,250,0.05);border-radius:8px;border-left:2px solid rgba(96,165,250,0.3);'});
    ctx.textContent = s.context;
    body.appendChild(ctx);
  }
}

function renderApps(list) {
  const node = document.getElementById('app-activity');
  clear(node);
  if (!list || list.length === 0) {
    node.appendChild(el('div', {className: 'empty'}, 'No tracked apps.'));
    return;
  }
  for (const a of list) {
    const row = el('div', {className: 'app-row'});

    const nameRow = el('div', {className: 'name'});
    const nameSpan = el('span');
    nameSpan.appendChild(document.createTextNode(a.app));
    if (a.error) {
      nameSpan.appendChild(document.createTextNode(' '));
      nameSpan.appendChild(el('span', {className: 'err'}, '(' + a.error + ')'));
    }
    nameRow.appendChild(nameSpan);
    nameRow.appendChild(el('span', {className: 'commits'}, a.commits + ' commits'));
    row.appendChild(nameRow);

    if (a.last_commit_msg) {
      row.appendChild(el('div', {className: 'commit-msg'}, a.last_commit_msg));
    }
    if (a.last_commit) {
      row.appendChild(el('div', {className: 'commit-sha'}, a.last_commit));
    }
    node.appendChild(row);
  }
}

async function load(window) {
  currentWindow = window;
  document.querySelectorAll('.tab').forEach(function (t) {
    t.classList.toggle('active', t.dataset.window === window);
  });
  try {
    const r = await fetch('/dashboard?window=' + window);
    const d = await r.json();

    document.getElementById('char-name').textContent = 'Window: ' + d.window;
    document.getElementById('updated').textContent = 'Generated: ' + d.generated_at;

    document.getElementById('tool-count').textContent = bars(d.tool_usage, 'tool-usage');
    document.getElementById('idle-count').textContent = bars(d.idle_categories, 'idle-categories');

    document.getElementById('err-total').textContent = d.error_rate.total;
    document.getElementById('wakeups').textContent = d.error_rate.sleep_wakeups;
    const errBreakdown = Object.entries(d.error_rate.by_level || {})
      .map(function (kv) { return kv[0] + ': ' + kv[1]; }).join(' · ');
    document.getElementById('err-breakdown').textContent = errBreakdown || '(no breakdown)';
    const fbHits = Object.entries(d.error_rate.fallback_hits || {})
      .map(function (kv) { return kv[0] + ': ' + kv[1]; }).join(' · ');
    document.getElementById('fallback-hits').textContent =
      fbHits ? 'Fallback wins: ' + fbHits : 'No fallback hits.';

    document.getElementById('mood-value').textContent =
      d.mood_trend.current === null ? '—' : Math.round(d.mood_trend.current);
    document.getElementById('mood-bracket').textContent = d.mood_trend.bracket;
    bars(d.mood_trend.emotions || {}, 'emotions');

    document.getElementById('plugin-count').textContent = bars(d.plugin_commands, 'plugin-commands');

    const deadCount = renderScoreboard(d.plugin_scoreboard);
    document.getElementById('dead-count').textContent = deadCount > 0
      ? deadCount + ' dead'
      : 'all active';

    document.getElementById('conv-user').textContent = d.conversation_volume.user_msgs;
    document.getElementById('conv-asst').textContent = d.conversation_volume.assistant_msgs;
    document.getElementById('conv-days').textContent = d.conversation_volume.active_days;
    const ratio = d.conversation_volume.user_msgs > 0
      ? (d.conversation_volume.assistant_msgs / d.conversation_volume.user_msgs).toFixed(1)
      : '∞';
    document.getElementById('conv-ratio').textContent = ratio;

    renderApps(d.app_activity);
    renderUserState(d.user_state);
  } catch (err) {
    console.error('dashboard load failed:', err);
    document.getElementById('updated').textContent = 'Error: ' + err.message;
  }
}

document.querySelectorAll('.tab').forEach(function (t) {
  t.addEventListener('click', function () { load(t.dataset.window); });
});

load('24h');
setInterval(function () { load(currentWindow); }, 60000);
</script>
</body>
</html>
"""
