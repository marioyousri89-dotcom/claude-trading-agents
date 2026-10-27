"""Live log dashboard. Tails every shard's events-*.jsonl and streams them to the browser.

Endpoints:
  /             live log page
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

HISTORY = 500


class EventHub:
    def __init__(self, events_dir: Path):
        self.events_dir = events_dir
        self.events: deque[tuple[int, dict]] = deque(maxlen=HISTORY)
        self.seq = 0
        self.shards: dict[str, dict] = {}
        self.cond = threading.Condition()
        self.offsets: dict[Path, tuple[int, int]] = {}  # path -> (inode, offset)

    def poll_files(self) -> None:
        for path in sorted(self.events_dir.glob("events-*.jsonl")):
            with open(path, "rb") as f:
                st = os.fstat(f.fileno())
                inode, offset = self.offsets.get(path, (st.st_ino, 0))
                if inode != st.st_ino or st.st_size < offset:
                    # The engine rotated the file. Finish the old one (now <name>.1)
                    # first so no events are lost, then read the new one from the top.
                    self._finish_rotated(path, inode, offset)
                    offset = 0
                f.seek(offset)
                chunk = f.read()
            end = chunk.rfind(b"\n") + 1  # only consume complete lines
            if not end:
                self.offsets[path] = (st.st_ino, offset)
                continue
            self.offsets[path] = (st.st_ino, offset + end)
            self._consume(chunk[:end])

    def _finish_rotated(self, path: Path, inode: int, offset: int) -> None:
        backup = path.with_name(path.name + ".1")
        try:
            with open(backup, "rb") as f:
                if os.fstat(f.fileno()).st_ino != inode:
                    return  # rotated more than once since the last poll; that tail is gone
                f.seek(offset)
                self._consume(f.read())
        except FileNotFoundError:
            pass

    def _consume(self, chunk: bytes) -> None:
        for line in chunk.splitlines():
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
            if kind in ("start", "tick", "leaderboard", "stop", "heatmap"):
                shard[kind] = event
            self.cond.notify_all()

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
<header><b>Trading Agents</b> <span class="mut">paper mode · <span id="total">0</span> agents reporting</span></header>
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
            elif self.path == "/api/state":
                self._send(200, json.dumps(hub.state()).encode(), "application/json")
            elif self.path == "/events":
                self.stream()
            else:
                self._send(404, b"not found", "text/plain")

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
