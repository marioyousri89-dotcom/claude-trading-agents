"""Live log dashboard. Tails every shard's events-*.jsonl and streams them to the browser.

Endpoints:
  /             live log page
  /3d           3D cluster view: every department, manager and pipeline stage live,
                with TradingView price and equity charts
  /api/org      departments and manager reports merged across shards (JSON)
  /api/prices   recent price and equity history for the charts (JSON)
  /api/agents   GET the agent slider's setting; POST {"per_department": n} to resize
                every shard (JSON only, so other sites can't post to it)
  /events       Server-Sent Events stream of new events
  /api/state    latest tick, leaderboard, agent count per shard, ring heatmap
                and the Jev decision desk totals (JSON)
  /health       200 once at least one shard is reporting
"""

import json
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import control
from .cluster_page import CLUSTER_PAGE
from .org import DEPARTMENTS, manager_reports

HISTORY = 500
PRICE_HISTORY = 600


class EventHub:
    def __init__(self, events_dir: Path):
        self.events_dir = events_dir
        self.events: deque[tuple[int, dict]] = deque(maxlen=HISTORY)
        self.seq = 0
        self.shards: dict[str, dict] = {}
        self.cond = threading.Condition()
        self.offsets: dict[Path, int] = {}
        # Every engine shard sees the same market, so one shard's ticks give the price history.
        self.price_history: deque[dict] = deque(maxlen=PRICE_HISTORY)
        self.equity: dict[int, dict[str, float]] = {}

    def poll_files(self) -> None:
        for path in sorted(self.events_dir.glob("events-*.jsonl")):
            offset = self.offsets.get(path, 0)
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1  # only consume complete lines
            if not end:
                continue
            self.offsets[path] = offset + end
            for line in chunk[:end].splitlines():
                try:
                    self.add(json.loads(line))
                except ValueError:
                    continue

    def add(self, event: dict) -> None:
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, event))
            shard = self.shards.setdefault(str(event.get("shard", "?")), {})
            kind = event.get("type")
            if kind in ("start", "tick", "leaderboard", "stop", "heatmap", "org"):
                shard[kind] = event
            if kind == "resize" and "start" in shard:
                shard["start"] = {**shard["start"], "agents": event["agents"], "total_agents": event["total_agents"]}
            if kind == "tick" and "prices" in event:
                self._record_tick(event)
            self.cond.notify_all()

    def _record_tick(self, event: dict) -> None:
        tick = event["tick"]
        self.equity.setdefault(tick, {})[str(event.get("shard"))] = event.get("pnl", 0.0)
        if len(self.equity) > PRICE_HISTORY:
            del self.equity[min(self.equity)]
        if not self.price_history or tick > self.price_history[-1]["tick"]:
            self.price_history.append({"tick": tick, "ts": event["ts"], "prices": event["prices"]})

    def prices(self) -> dict:
        with self.cond:
            # Only ticks every shard has reported, so a lagging shard doesn't dent the line.
            shards = max((len(v) for v in self.equity.values()), default=0)
            pnl = [{"tick": t, "pnl": round(sum(v.values()), 2)} for t, v in sorted(self.equity.items())
                   if len(v) == shards]
            return {"history": list(self.price_history), "pnl": pnl}

    def org(self) -> dict:
        """Merge every shard's latest department report into one org chart."""
        with self.cond:
            reports = [v["org"] for v in self.shards.values() if "org" in v]
        merged = {d.key: {"name": d.name, "job": d.job, "agents": 0, "active": 0, "sitting_out": 0, "pnl": 0.0,
                          "votes": [], "vote_sum": 0.0, "weight_sum": 0.0, "wins": 0, "experience": 0}
                  for d in DEPARTMENTS}
        for report in reports:
            for key, dept in report["departments"].items():
                m = merged.get(key)
                if m is None:
                    continue
                m["agents"] += dept["agents"]
                m["active"] += dept["active"]
                m["pnl"] += dept["pnl"]
                m["votes"].extend(dept["votes"])
                m["sitting_out"] += dept.get("sitting_out", 0)
                m["wins"] += dept.get("wins", 0)
                m["experience"] += dept.get("experience", 0)
                if dept.get("avg_weight") is not None:
                    m["weight_sum"] += dept["avg_weight"] * dept["agents"]
                if dept["avg_vote"] is not None:
                    m["vote_sum"] += dept["avg_vote"] * dept["active"]
        for m in merged.values():
            m["avg_vote"] = round(m.pop("vote_sum") / m["active"], 4) if m["active"] else None
            m["avg_weight"] = round(m.pop("weight_sum") / m["agents"], 4) if m["agents"] else None
            m["skill"] = round(m["wins"] / m["experience"], 4) if m["experience"] else None
            m["pnl"] = round(m["pnl"], 2)
        tick = max((r["tick"] for r in reports), default=0)
        per_department = control.read(self.events_dir)
        return {"tick": tick, "departments": merged, "managers": manager_reports(merged),
                "slider": {"per_department": per_department, "min": control.MIN_DEPARTMENT_SIZE,
                           "max": control.MAX_DEPARTMENT_SIZE, "departments": len(DEPARTMENTS)}}

    def since(self, seq: int, timeout: float) -> list[tuple[int, dict]]:
        with self.cond:
            if self.seq <= seq:
                self.cond.wait(timeout)
            return [(s, e) for s, e in self.events if s > seq]

    def state(self) -> dict:
        with self.cond:
            shards = dict(sorted(self.shards.items()))
            total = sum(v["start"].get("agents", 0) for v in shards.values() if "start" in v)
            ticks = [v["tick"] for v in shards.values() if "tick" in v]
            approved = sum(t.get("jev", {}).get("approved", 0) for t in ticks)
            rejected = sum(t.get("jev", {}).get("rejected", 0) for t in ticks)
            heatmap = shards.get("ring", {}).get("heatmap", {}).get("bots", {})
            desk = {"approved": approved, "rejected": rejected,
                    "approval_rate": round(approved / (approved + rejected), 4) if approved + rejected else None,
                    "session_pnl": round(sum(t.get("pnl", 0) for t in ticks), 2)}
            return {"shards": shards, "agents_reporting": total, "events_seen": self.seq,
                    "heatmap": heatmap, "jev_desk": desk}

    def run_tailer(self, interval: float = 0.25) -> None:
        while True:
            try:
                self.poll_files()
            except OSError:
                pass
            time.sleep(interval)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Agents Live</title>
<style>
:root{--bg:#0f1115;--fg:#e6e6e6;--mut:#8a8f98;--up:#3fb950;--dn:#f85149;--card:#171a21}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 ui-monospace,Menlo,Consolas,monospace}
header{padding:12px 16px;border-bottom:1px solid #222}
#shards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;padding:12px 16px}
.card{background:var(--card);padding:10px;border-radius:6px}
.mut{color:var(--mut)}.up{color:var(--up)}.dn{color:var(--dn)}
#hud{display:grid;grid-template-columns:2fr 1fr;gap:8px;padding:0 16px 12px}
@media (max-width:700px){#hud{grid-template-columns:1fr}}
#heat{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}
.cell{padding:8px 4px;border-radius:4px;text-align:center;font-size:12px}
#log{padding:0 16px 16px;white-space:pre-wrap;word-break:break-all}
#log div{border-bottom:1px solid #1d2028;padding:2px 0}
</style></head><body>
<header><b>Trading Agents</b> <span class="mut">paper mode · <span id="total">0</span> agents reporting</span>
 · <a href="/3d" style="color:#58a6ff">3D cluster view</a></header>
<div id="shards"></div>
<div id="hud"><div class="card"><b>Signal heatmap</b> <span class="mut">8-bot ring</span><div id="heat"></div></div>
<div class="card"><b>Jev decision desk</b><div id="desk" class="mut">waiting…</div></div></div>
<div id="log"></div>
<script>
const log=document.getElementById('log'),shards={};let heat={};
function color(v){const a=Math.min(1,Math.abs(v));return v>=0?`rgba(63,185,80,${.15+a*.6})`:`rgba(248,81,73,${.15+a*.6})`}
function renderHud(){document.getElementById('heat').innerHTML=Object.entries(heat).map(([b,v])=>
 `<div class="cell" style="background:${color(v)}">${b.replace('_',' ')}<br><b>${v.toFixed(2)}</b></div>`).join('');
 let ap=0,rj=0,pnl=0;for(const s of Object.values(shards)){ap+=s.jev?.approved||0;rj+=s.jev?.rejected||0;pnl+=s.pnl||0}
 document.getElementById('desk').innerHTML=`requests ${ap+rj}<br><span class="up">approved ${ap}</span> · <span class="dn">rejected ${rj}</span><br>`+
 `approval ${ap+rj?(100*ap/(ap+rj)).toFixed(1):'–'}%<br>session P&amp;L <span class="${pnl>=0?'up':'dn'}">${pnl.toFixed(2)}</span>`}
function render(){const el=document.getElementById('shards');el.innerHTML='';let total=0;
 for(const [id,s] of Object.entries(shards)){total+=s.agents||0;const pnl=s.pnl||0;
  el.insertAdjacentHTML('beforeend',`<div class="card"><b>${id==='ring'?'8-bot ring':'shard '+id}</b> <span class="mut">${id==='ring'?'polymarket + predictor':(s.agents||0)+' agents'}</span><br>
  tick ${s.tick||0} · equity ${(s.equity||0).toFixed(2)}<br><span class="${pnl>=0?'up':'dn'}">pnl ${pnl.toFixed(2)}</span>${s.halted?' <span class="dn">HALTED</span>':''}</div>`)}
 document.getElementById('total').textContent=total;renderHud()}
fetch('/api/state').then(r=>r.json()).then(st=>{for(const [id,v] of Object.entries(st.shards)){
 const s=shards[id]=shards[id]||{};if(v.start)s.agents=v.start.agents;if(v.tick&&!s.tick)Object.assign(s,v.tick)}heat=st.heatmap||{};render()});
const es=new EventSource('/events');
es.onmessage=m=>{const e=JSON.parse(m.data),s=shards[e.shard]=shards[e.shard]||{};
 if(e.type==='start')s.agents=e.agents;
 if(e.type==='heatmap'){heat=e.bots;renderHud();return}
 if(e.type==='tick'){Object.assign(s,{tick:e.tick,equity:e.equity,pnl:e.pnl,halted:e.halted,jev:e.jev});render();if(e.orders===0)return}
 const d=document.createElement('div');d.textContent=new Date(e.ts*1000).toLocaleTimeString()+'  ['+e.shard+'] '+e.type+'  '+
  JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k])=>!['ts','shard','type'].includes(k))));
 log.prepend(d);while(log.children.length>300)log.lastChild.remove()};
</script></body></html>"""


def make_handler(hub: EventHub):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/health":
                state = hub.state()
                ok = state["agents_reporting"] > 0
                self._send(200 if ok else 503, json.dumps({"ok": ok, **state}).encode(), "application/json")
            elif self.path == "/3d":
                self._send(200, CLUSTER_PAGE.encode(), "text/html; charset=utf-8")
            elif self.path == "/api/org":
                self._send(200, json.dumps(hub.org()).encode(), "application/json")
            elif self.path == "/api/agents":
                self._send(200, json.dumps({"per_department": control.read(hub.events_dir)}).encode(),
                           "application/json")
            elif self.path == "/api/prices":
                self._send(200, json.dumps(hub.prices()).encode(), "application/json")
            elif self.path == "/api/state":
                self._send(200, json.dumps(hub.state()).encode(), "application/json")
            elif self.path == "/events":
                self.stream()
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/agents":
                self._send(404, b"not found", "text/plain")
                return
            # Requiring a JSON content type means a browser on another site must
            # ask permission first (a CORS preflight), which this server never grants.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self._send(415, b'{"error":"send JSON"}', "application/json")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 1024:
                    raise ValueError("body too large or empty")
                per_department = json.loads(self.rfile.read(length)).get("per_department")
                control.write(hub.events_dir, per_department)
            except (ValueError, AttributeError) as e:
                self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
                return
            except OSError:
                self._send(503, b'{"error":"could not save the setting"}', "application/json")
                return
            body = {"per_department": per_department, "total": control.total(per_department)}
            self._send(200, json.dumps(body).encode(), "application/json")

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            # Replay recent history so a fresh page isn't empty, then follow live.
            seq = max(0, hub.seq - 100)
            try:
                while True:
                    batch = hub.since(seq, timeout=15)
                    if not batch:
                        self.wfile.write(b": keep-alive\n\n")
                    for seq, event in batch:
                        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def main() -> None:
    events_dir = Path(os.environ.get("EVENTS_DIR", "events"))
    events_dir.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", 8080))
    hub = EventHub(events_dir)
    threading.Thread(target=hub.run_tailer, daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(hub))
    server.daemon_threads = True
    print(f"dashboard on http://{host}:{port} reading {events_dir}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
