"""Live log dashboard. Tails every shard's events-*.jsonl and streams them to the browser.

Endpoints:
  /             live dashboard (installable as a home-screen app)
  /events       Server-Sent Events stream of new events
  /api/state    latest tick, leaderboard, agent count per shard, ring heatmap
                and the Jev decision desk totals (JSON)
  /health       200 once at least one shard is reporting
  /login        password sign-in when the dashboard is reachable from the internet

When auth is on (see trading/webauth.py), /, /events and /api/state need a
signed session cookie; /health then reports only {"ok": ...}.
"""

import json
import os
import urllib.parse
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .webauth import COOKIE, WebAuth

HISTORY = 500
WEB = Path(__file__).resolve().parent / "web"
# Every file the server will ever return, so no request path can reach anything else.
STATIC = {
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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
            if kind in ("start", "tick", "leaderboard", "stop", "heatmap", "matrix"):
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


def _read(name: str) -> bytes:
    return (WEB / name).read_bytes()


def make_handler(hub: EventHub, auth: WebAuth | None = None):
    auth = auth or WebAuth(None, None, enabled=False)
    assets = {path: (_read(name), ctype) for path, (name, ctype) in STATIC.items()}
    index = _read("index.html")
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

        def _session(self) -> str | None:
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == COOKIE:
                    return value
            return None

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
                if path == "/":
                    self._redirect("/login")
                else:
                    self._send(401, b'{"error":"sign in required"}', "application/json")
            elif path == "/":
                self._send(200, index, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            elif path == "/api/state":
                self._send(200, json.dumps(hub.state()).encode(), "application/json", {"Cache-Control": "no-store"})
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
            if path == "/logout":
                return self._redirect("/login", {"Set-Cookie": self._cookie("", 0)})
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


def serve(events_dir: Path, host: str, port: int, auth: WebAuth) -> ThreadingHTTPServer:
    hub = EventHub(events_dir)
    threading.Thread(target=hub.run_tailer, daemon=True).start()
    server = ThreadingHTTPServer((host, port), make_handler(hub, auth))
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
