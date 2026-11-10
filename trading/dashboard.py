"""Live log dashboard. Tails every shard's events-*.jsonl and streams them to the browser.

Endpoints:
  /             live dashboard (installable as a home-screen app)
  /3d           3D cluster view: every department, manager and pipeline stage live,
                with TradingView price and P&L charts and the agent slider
  /api/org      departments and manager reports merged across shards (JSON)
  /api/prices   recent price and P&L history for the charts (JSON)
  /api/agents   GET the agent slider's setting; POST {"per_department": n} to resize
                every shard (JSON only, so other sites can't post to it)
  /events       Server-Sent Events stream of new events
  /api/state    latest tick, leaderboard, agent count per shard, ring heatmap
                and the Jev decision desk totals (JSON)
  /jarvis       JARVIS, the owner's voice assistant with the hologram (trading/jarvis.py)
  /api/jarvis   GET her status; POST /api/jarvis/unlock {"passphrase"} and /api/jarvis/ask {"question"}
  /api/platforms  real platform connectors: mode and whether keys are set (names only)
  /health       200 once at least one shard is reporting
  /login        password sign-in when the dashboard is reachable from the internet

When auth is on (see trading/webauth.py), /, /3d, /events and the /api/
endpoints need a signed session cookie; /health then reports only {"ok": ...}.
"""

import json
import os
import urllib.parse
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import control, platforms
from .jarvis import UNLOCK_COOKIE, Jarvis, snapshot
from .org import DEPARTMENTS, finish_school, manager_reports
from .webauth import COOKIE, WebAuth

HISTORY = 500
PRICE_HISTORY = 600
WEB = Path(__file__).resolve().parent / "web"
# Every file the server will ever return, so no request path can reach anything else.
STATIC = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/3d.css": ("3d.css", "text/css; charset=utf-8"),
    "/3d.js": ("3d.js", "text/javascript; charset=utf-8"),
    "/jarvis.css": ("jarvis.css", "text/css; charset=utf-8"),
    "/jarvis.js": ("jarvis.js", "text/javascript; charset=utf-8"),
    # Vendored so the strict CSP (scripts only from this site) holds and the cloud app needs no CDN.
    "/vendor/three.min.js": ("vendor/three.min.js", "text/javascript; charset=utf-8"),
    "/vendor/lightweight-charts.js": ("vendor/lightweight-charts.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/offline.html": ("offline.html", "text/html; charset=utf-8"),
    "/icons/icon-192.png": ("icons/icon-192.png", "image/png"),
    "/icons/icon-512.png": ("icons/icon-512.png", "image/png"),
    "/icons/icon-maskable-512.png": ("icons/icon-maskable-512.png", "image/png"),
    "/icons/apple-touch-icon.png": ("icons/apple-touch-icon.png", "image/png"),
    "/icons/favicon-32.png": ("icons/favicon-32.png", "image/png"),
}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
MAX_FORM_BYTES = 4096
# Only JARVIS's page may use the microphone; nothing may use the camera, location or payment APIs.
PERMISSIONS = "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), interest-cohort=()"


class EventHub:
    def __init__(self, events_dir: Path):
        self.events_dir = events_dir
        self.events: deque[tuple[int, dict]] = deque(maxlen=HISTORY)
        self.seq = 0
        self.shards: dict[str, dict] = {}
        self.cond = threading.Condition()
        self.offsets: dict[Path, tuple[int, int]] = {}  # path -> (inode, offset)
        # Every engine shard sees the same market, so one shard's ticks give the price history.
        self.price_history: deque[dict] = deque(maxlen=PRICE_HISTORY)
        self.equity: dict[int, dict[str, float]] = {}

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
            if kind in ("start", "tick", "leaderboard", "stop", "heatmap", "matrix", "org"):
                shard[kind] = event
            if kind == "resize" and "start" in shard:
                shard["start"] = {**shard["start"], "agents": event["agents"], "total_agents": event["total_agents"]}
            if kind == "tick" and "prices" in event and isinstance(event.get("shard"), int):
                self._record_tick(event)  # engine shards only; the ring and matrix trade other books
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
                          "votes": [], "vote_sum": 0.0, "weight_sum": 0.0, "wins": 0, "experience": 0, "held": 0,
                          "macro_sum": 0.0, "macro_count": 0, "traders": 0, "trades_hour_sum": 0,
                          "trades_hour_min": None, "trades_total": 0,
                          "school": {"trained": 0, "graduates": 0, "exam_sum": 0.0, "course_sums": {}}}
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
                m["held"] += dept.get("held", 0)
                for k in ("traders", "trades_hour_sum", "trades_total"):
                    m[k] += dept.get(k, 0)
                if dept.get("trades_hour_min") is not None:
                    m["trades_hour_min"] = (dept["trades_hour_min"] if m["trades_hour_min"] is None
                                            else min(m["trades_hour_min"], dept["trades_hour_min"]))
                m["macro_sum"] += dept.get("macro_sum", 0.0)
                m["macro_count"] += dept.get("macro_count", 0)
                school = dept.get("school")
                if school:
                    ms = m["school"]
                    for k in ("trained", "graduates", "exam_sum"):
                        ms[k] += school[k]
                    for k, v in school["course_sums"].items():
                        ms["course_sums"][k] = ms["course_sums"].get(k, 0.0) + v
                if dept.get("avg_weight") is not None:
                    m["weight_sum"] += dept["avg_weight"] * dept["agents"]
                if dept["avg_vote"] is not None:
                    m["vote_sum"] += dept["avg_vote"] * dept["active"]
        for m in merged.values():
            m["avg_vote"] = round(m.pop("vote_sum") / m["active"], 4) if m["active"] else None
            m["avg_weight"] = round(m.pop("weight_sum") / m["agents"], 4) if m["agents"] else None
            m["skill"] = round(m["wins"] / m["experience"], 4) if m["experience"] else None
            m["pnl"] = round(m["pnl"], 2)
            finish_school(m)
        tick = max((r["tick"] for r in reports), default=0)
        latest = max(reports, key=lambda r: r["tick"], default={})
        per_department = control.read(self.events_dir)
        return {"tick": tick, "departments": merged, "managers": manager_reports(merged), "news": latest.get("news", []),
                "practice": latest.get("practice"),
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


def _read(name: str) -> bytes:
    return (WEB / name).read_bytes()


def make_handler(hub: EventHub, auth: WebAuth | None = None, jarvis: Jarvis | None = None):
    auth = auth or WebAuth(None, None, enabled=False)
    jarvis = jarvis or Jarvis(None, required=auth.enabled)
    assets = {path: (_read(name), ctype) for path, (name, ctype) in STATIC.items()}
    index = _read("index.html")
    cluster_page = _read("3d.html")
    jarvis_page = _read("jarvis.html")
    login_page = _read("login.html").decode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 60  # close idle or stalled connections (e.g. a request sent byte by byte)

        def log_message(self, *args):
            pass

        def _headers(self, status: int, ctype: str, length: int | None = None, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Permissions-Policy", PERMISSIONS)
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if auth.enabled:
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self._headers(status, ctype, len(body), extra)
            self.wfile.write(body)

        def _redirect(self, to: str, extra: dict | None = None):
            self._send(303, b"", "text/plain", {"Location": to, **(extra or {})})

        def _cookie(self, value: str, max_age: int) -> str:
            return f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; Secure; SameSite=Lax"

        def _session(self, cookie: str = COOKIE) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == cookie:
                    return value
            return None

        def _json(self, status: int, body: dict, extra: dict | None = None):
            self._send(status, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store", **(extra or {})})

        def _same_origin(self) -> bool:
            """Refuse POSTs a browser sent from another site (CSRF). Non-browser clients send no Origin."""
            origin = self.headers.get("Origin")
            if not origin or origin == "null" and not auth.enabled:
                return True
            return urllib.parse.urlsplit(origin).netloc == self.headers.get("Host", "")

        def _jarvis_unlocked(self) -> bool:
            return jarvis.unlocked(self._session(UNLOCK_COOKIE))

        def _authed(self) -> bool:
            return auth.valid(self._session())

        def _client(self) -> str:
            # Behind Render's proxy the client address is the first X-Forwarded-For entry.
            forwarded = self.headers.get("X-Forwarded-For", "")
            return forwarded.split(",")[0].strip() or self.client_address[0]

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in assets:
                body, ctype = assets[path]
                self._send(200, body, ctype, {"Cache-Control": "no-cache" if path == "/sw.js" else "public, max-age=3600"})
            elif path == "/health":
                state = hub.state()
                ok = state["agents_reporting"] > 0
                body = {"ok": ok} if auth.enabled else {"ok": ok, **state}
                self._send(200 if ok else 503, json.dumps(body).encode(), "application/json")
            elif path == "/login":
                if not auth.enabled or self._authed():
                    return self._redirect("/")
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                message = {"1": "Wrong password.", "locked": "Too many attempts. Try again in 15 minutes."}
                error = message.get(query.get("error", [""])[0], "")
                page = login_page.replace("<!--ERROR-->", f'<div class="err">{error}</div>' if error else "")
                self._send(200, page.encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif not self._authed():
                if path in ("/", "/3d", "/jarvis"):
                    self._redirect("/login")
                else:
                    self._send(401, b'{"error":"sign in required"}', "application/json")
            elif path == "/":
                self._send(200, index, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/3d":
                self._send(200, cluster_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/jarvis":
                self._send(200, jarvis_page, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/jarvis":
                self._json(200, jarvis.status(self._jarvis_unlocked()))
            elif path == "/api/platforms":
                self._json(200, {"platforms": platforms.status(), "live_trading": "locked"})
            elif path == "/api/state":
                self._send(200, json.dumps(hub.state()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/org":
                self._send(200, json.dumps(hub.org()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/prices":
                self._send(200, json.dumps(hub.prices()).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/api/agents":
                body = {"per_department": control.read(hub.events_dir)}
                self._send(200, json.dumps(body).encode(), "application/json", {"Cache-Control": "no-store"})
            elif path == "/events":
                self.stream()
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            # Consume the body first so the connection stays in sync, and never
            # read more than a login form needs.
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = MAX_FORM_BYTES + 1
            if length > MAX_FORM_BYTES or length < 0:
                self.close_connection = True
                return self._send(413, b"too large", "text/plain", {"Connection": "close"})
            raw = self.rfile.read(length) if length else b""
            if not self._same_origin():
                return self._send(403, b'{"error":"cross-site request refused"}', "application/json")
            if path == "/logout":
                return self._redirect("/login", {"Set-Cookie": self._cookie("", 0)})
            if path == "/api/agents":
                return self._set_agents(raw)
            if path in ("/api/jarvis/unlock", "/api/jarvis/ask", "/api/jarvis/lock"):
                return self._jarvis_post(path, raw)
            if path != "/login" or not auth.enabled:
                return self._send(404, b"not found", "text/plain")
            client = self._client()
            if not auth.allowed(client):
                return self._redirect("/login?error=locked")
            form = urllib.parse.parse_qs(raw.decode("utf-8", "replace"))
            if auth.check_password(form.get("password", [""])[0]):
                auth.record_success(client)
                return self._redirect("/", {"Set-Cookie": self._cookie(auth.issue(), auth.max_age)})
            auth.record_failure(client)
            self._redirect("/login?error=1")

        def _set_agents(self, raw: bytes):
            if not self._authed():
                return self._send(401, b'{"error":"sign in required"}', "application/json")
            # Requiring a JSON content type means a browser on another site must
            # ask permission first (a CORS preflight), which this server never grants.
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._send(415, b'{"error":"send JSON"}', "application/json")
            try:
                per_department = json.loads(raw or b"null").get("per_department")
                control.write(hub.events_dir, per_department)
            except (ValueError, AttributeError) as e:
                return self._send(400, json.dumps({"error": str(e)}).encode(), "application/json")
            except OSError:
                return self._send(503, b'{"error":"could not save the setting"}', "application/json")
            body = {"per_department": per_department, "total": control.total(per_department)}
            self._send(200, json.dumps(body).encode(), "application/json")

        def _jarvis_post(self, path: str, raw: bytes):
            if not self._authed():
                return self._json(401, {"error": "sign in required"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(415, {"error": "send JSON"})
            if path == "/api/jarvis/lock":
                return self._json(200, {"unlocked": False},
                                  {"Set-Cookie": f"{UNLOCK_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"})
            if not jarvis.enabled:
                return self._json(403, jarvis.status(False))
            try:
                body = json.loads(raw or b"null")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            client = self._client()
            if path == "/api/jarvis/unlock":
                if not jarvis.unlock_failures.allowed(client):
                    return self._json(429, {"error": "Too many wrong passphrases. Try again in 15 minutes."})
                if jarvis.check_passphrase(str(body.get("passphrase", ""))):
                    jarvis.unlock_failures.clear(client)
                    cookie = (f"{UNLOCK_COOKIE}={jarvis.issue()}; Path=/; Max-Age={jarvis.ttl}; HttpOnly; Secure; "
                              "SameSite=Strict")
                    return self._json(200, {"unlocked": True}, {"Set-Cookie": cookie})
                jarvis.unlock_failures.hit(client)
                return self._json(403, {"unlocked": False, "error": "That isn't the passphrase."})
            if not self._jarvis_unlocked():
                return self._json(403, {"error": "Say or type your passphrase to wake JARVIS."})
            question = body.get("question")
            if not isinstance(question, str) or not question.strip():
                return self._json(400, {"error": "ask a question"})
            if not jarvis.questions.allowed(client):
                return self._json(429, {"error": "Slow down a little: too many questions this minute."})
            jarvis.questions.hit(client)
            reply = jarvis.ask(client, question, snapshot(hub.org(), hub.state()))
            self._json(200, {"reply": reply, "brain": jarvis.brain.name})

        def stream(self):
            self._headers(200, "text/event-stream", extra={"Cache-Control": "no-cache", "Connection": "close"})
            self.close_connection = True
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


def serve(events_dir: Path, host: str, port: int, auth: WebAuth, jarvis: Jarvis | None = None) -> ThreadingHTTPServer:
    hub = EventHub(events_dir)
    threading.Thread(target=hub.run_tailer, daemon=True).start()
    jarvis = jarvis or Jarvis.from_env(public=auth.enabled)
    server = ThreadingHTTPServer((host, port), make_handler(hub, auth, jarvis))
    server.daemon_threads = True
    mode = "password login on" if auth.enabled else "no login (local only)"
    print(f"dashboard on http://{host}:{port} reading {events_dir} ({mode})", flush=True)
    return server


def main() -> None:
    events_dir = Path(os.environ.get("EVENTS_DIR", "events"))
    events_dir.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT") or os.environ.get("PORT") or 8080)
    serve(events_dir, host, port, WebAuth.from_env(host)).serve_forever()


if __name__ == "__main__":
    main()
