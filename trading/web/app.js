// ATS Matrix live dashboard. Built with DOM calls only (no innerHTML) so event
// data can never inject markup, and so the strict Content-Security-Policy holds.
const $ = (id) => document.getElementById(id);
const shards = {};
let heat = {};
let matrixSnap = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function color(v) {
  const a = Math.min(1, Math.abs(v));
  return v >= 0 ? `rgba(63,185,80,${0.15 + a * 0.6})` : `rgba(248,81,73,${0.15 + a * 0.6})`;
}

function money(v) {
  return (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function renderHud() {
  const grid = $('heat');
  grid.replaceChildren(...Object.entries(heat).map(([bot, v]) => {
    const cell = el('div', 'cell');
    cell.style.background = color(v);
    cell.append(el('span', '', bot.replace('_', ' ')), el('b', '', v.toFixed(2)));
    return cell;
  }));
  let ap = 0, rj = 0, pnl = 0;
  for (const s of Object.values(shards)) {
    ap += s.jev?.approved || 0;
    rj += s.jev?.rejected || 0;
    pnl += s.pnl || 0;
  }
  const desk = $('desk');
  const rate = ap + rj ? (100 * ap / (ap + rj)).toFixed(1) + '%' : '–';
  desk.replaceChildren(
    el('div', '', `requests ${ap + rj}`),
    el('div', 'up', `approved ${ap}`), el('div', 'dn', `rejected ${rj}`),
    el('div', '', `approval ${rate}`),
    el('div', pnl >= 0 ? 'up' : 'dn', `session P&L ${money(pnl)}`),
  );
}

function render() {
  let total = 0;
  $('shards').replaceChildren(...Object.entries(shards).map(([id, s]) => {
    total += s.agents || 0;
    const card = el('div', 'card');
    const title = el('div');
    const names = { ring: ['8-bot ring', 'polymarket + predictor'], matrix: ['Whole Matrix', '4 fused strategies'] };
    const [name, sub] = names[id] || [`shard ${id}`, `${s.agents || 0} agents`];
    title.append(el('b', '', name), ' ', el('span', 'mut', sub));
    const pnl = s.pnl || 0;
    const line = el('div', pnl >= 0 ? 'up' : 'dn', `pnl ${money(pnl)}`);
    if (s.halted) line.append(' ', el('span', 'dn', 'HALTED'));
    card.append(title, el('div', '', `tick ${s.tick || 0} · equity ${money(s.equity)}`), line);
    return card;
  }));
  $('total').textContent = total;
  renderHud();
}

const ARROW = { 1: '▲', '-1': '▼', 0: '·' };

function renderMatrix() {
  if (!matrixSnap) return;
  $('matrixpanel').hidden = false;
  const w = matrixSnap.weights || {};
  $('matrixsub').textContent = `${matrixSnap.stormy ? 'stormy' : 'calm'} market · weights trend ${w.trend_confluence ?? '–'} / arb ${w.stat_arb ?? '–'}`;
  const head = el('tr');
  for (const h of ['asset', 'regime', '1m', '15m', '1h', '1d', 'position $']) head.append(el('th', '', h));
  const rows = Object.entries(matrixSnap.assets).map(([asset, v]) => {
    const tr = el('tr');
    tr.append(el('td', '', asset), el('td', (v.regime || '').endsWith('trending') ? 'up' : 'mut', (v.regime || 'learning…').replaceAll('_', ' ')));
    for (const tf of ['1m', '15m', '1h', '1d']) {
      const m = v.mtf[tf];
      tr.append(el('td', m > 0 ? 'up' : m < 0 ? 'dn' : 'mut', ARROW[m]));
    }
    tr.append(el('td', v.position_usd > 0 ? 'up' : v.position_usd < 0 ? 'dn' : 'mut', money(v.position_usd)));
    return tr;
  });
  $('mtable').replaceChildren(head, ...rows);
  $('pairs').replaceChildren(...Object.entries(matrixSnap.pairs).map(([pair, p]) => {
    const side = p.side > 0 ? 'long spread' : p.side < 0 ? 'short spread' : 'flat';
    return el('div', p.side ? 'up' : 'mut', `${pair}  z ${p.z ?? '–'}  ${side}`);
  }));
}

function toLogin(resp) {
  if (resp.status === 401) { location.href = '/login'; throw new Error('signed out'); }
  return resp;
}

fetch('/api/state', { credentials: 'same-origin' }).then(toLogin).then((r) => r.json()).then((st) => {
  for (const [id, v] of Object.entries(st.shards)) {
    const s = shards[id] = shards[id] || {};
    if (v.start) s.agents = v.start.agents;
    if (v.tick && !s.tick) Object.assign(s, v.tick);
  }
  heat = st.heatmap || {};
  matrixSnap = st.shards.matrix?.matrix || null;
  render();
  renderMatrix();
}).catch(() => {});

function connect() {
  const es = new EventSource('/events');
  es.onmessage = (m) => {
    $('status').textContent = 'live';
    const e = JSON.parse(m.data);
    const s = shards[e.shard] = shards[e.shard] || {};
    if (e.type === 'start') s.agents = e.agents;
    if (e.type === 'heatmap') { heat = e.bots; renderHud(); return; }
    if (e.type === 'matrix') { matrixSnap = e; renderMatrix(); return; }
    if (e.type === 'tick') {
      Object.assign(s, { tick: e.tick, equity: e.equity, pnl: e.pnl, halted: e.halted, jev: e.jev });
      render();
      if (e.orders === 0) return;
    }
    const rest = Object.fromEntries(Object.entries(e).filter(([k]) => !['ts', 'shard', 'type'].includes(k)));
    const row = el('div', '', `${new Date(e.ts * 1000).toLocaleTimeString()}  [${e.shard}] ${e.type}  ${JSON.stringify(rest)}`);
    const log = $('log');
    log.prepend(row);
    while (log.children.length > 300) log.lastChild.remove();
  };
  es.onerror = () => {
    $('status').textContent = 'reconnecting…';
    // A 401 closes the stream for good; check whether the session expired.
    fetch('/api/state', { credentials: 'same-origin' }).then(toLogin).catch(() => {});
  };
}
connect();

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
