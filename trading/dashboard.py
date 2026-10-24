"""Live log dashboard. Tails every shard's events-*.jsonl and streams them to the browser.

Endpoints:
  /             live log page
  /events       Server-Sent Events stream of new events
  /api/state    latest tick, leaderboard and agent count per shard (JSON)
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
        self.shards: dict[int, dict] = {}
        self.cond = threading.Condition()
        self.offsets: dict[Path, int] = {}

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
            shard = self.shards.setdefault(event.get("shard", -1), {})
            kind = event.get("type")
            if kind in ("start", "tick", "leaderboard", "stop"):
                shard[kind] = event
            self.cond.notify_all()

    def since(self, seq: int, timeout: float) -> list[tuple[int, dict]]:
        with self.cond:
            if self.seq <= seq:
                self.cond.wait(timeout)
            return [(s, e) for s, e in self.events if s > seq]

    def state(self) -> dict:
        with self.cond:
            shards = {str(k): v for k, v in sorted(self.shards.items())}
            total = sum(v["start"]["agents"] for v in self.shards.values() if "start" in v)
            return {"shards": shards, "agents_reporting": total, "events_seen": self.seq}

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
#log{padding:0 16px 16px;white-space:pre-wrap;word-break:break-all}
#log div{border-bottom:1px solid #1d2028;padding:2px 0}
</style></head><body>
<header><b>Trading Agents</b> <span class="mut">paper mode · <span id="total">0</span> agents reporting</span></header>
<div id="shards"></div><div id="log"></div>
<script>
const log=document.getElementById('log'),shards={};
function render(){const el=document.getElementById('shards');el.innerHTML='';let total=0;
 for(const [id,s] of Object.entries(shards)){total+=s.agents||0;const pnl=s.pnl||0;
  el.insertAdjacentHTML('beforeend',`<div class="card"><b>shard ${id}</b> <span class="mut">${s.agents||0} agents</span><br>
  tick ${s.tick||0} · equity ${(s.equity||0).toFixed(2)}<br><span class="${pnl>=0?'up':'dn'}">pnl ${pnl.toFixed(2)}</span>${s.halted?' <span class="dn">HALTED</span>':''}</div>`)}
 document.getElementById('total').textContent=total}
fetch('/api/state').then(r=>r.json()).then(st=>{for(const [id,v] of Object.entries(st.shards)){
 const s=shards[id]=shards[id]||{};if(v.start)s.agents=v.start.agents;if(v.tick&&!s.tick)Object.assign(s,v.tick)}render()});
const es=new EventSource('/events');
es.onmessage=m=>{const e=JSON.parse(m.data),s=shards[e.shard]=shards[e.shard]||{};
 if(e.type==='start')s.agents=e.agents;
 if(e.type==='tick'){Object.assign(s,{tick:e.tick,equity:e.equity,pnl:e.pnl,halted:e.halted});render();if(e.orders===0)return}
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
