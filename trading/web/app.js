// ATS Matrix live dashboard. Built with DOM calls only (no innerHTML) so event
// data can never inject markup, and so the strict Content-Security-Policy holds.
const $ = (id) => document.getElementById(id);
const shards = {};
let heat = {};

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
    title.append(el('b', '', id === 'ring' ? '8-bot ring' : `shard ${id}`), ' ',
      el('span', 'mut', id === 'ring' ? 'polymarket + predictor' : `${s.agents || 0} agents`));
    const pnl = s.pnl || 0;
    const line = el('div', pnl >= 0 ? 'up' : 'dn', `pnl ${money(pnl)}`);
    if (s.halted) line.append(' ', el('span', 'dn', 'HALTED'));
    card.append(title, el('div', '', `tick ${s.tick || 0} · equity ${money(s.equity)}`), line);
    return card;
  }));
  $('total').textContent = total;
  renderHud();
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
  render();
}).catch(() => {});

function connect() {
  const es = new EventSource('/events');
  es.onmessage = (m) => {
    $('status').textContent = 'live';
    const e = JSON.parse(m.data);
    const s = shards[e.shard] = shards[e.shard] || {};
    if (e.type === 'start') s.agents = e.agents;
    if (e.type === 'heatmap') { heat = e.bots; renderHud(); return; }
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
