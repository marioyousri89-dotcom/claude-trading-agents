"""Tests for the cloud app: password login, installable-app assets, and the supervisor."""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import cloud
from trading.dashboard import STATIC, EventHub, make_handler
from trading.webauth import COOKIE, PER_CLIENT_FAILURES, WebAuth

PASSWORD = "correct-horse-battery-staple"


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class WebAuthTests(unittest.TestCase):
    def test_fails_closed_without_password(self):
        with self.assertRaises(SystemExit):
            WebAuth(None, "s", enabled=True)
        with self.assertRaises(SystemExit):
            WebAuth("short", "s", enabled=True)

    def test_from_env(self):
        self.assertFalse(WebAuth.from_env("127.0.0.1", {}).enabled)  # loopback: local use
        self.assertFalse(WebAuth.from_env("0.0.0.0", {"DASHBOARD_AUTH": "off"}).enabled)
        with self.assertRaises(SystemExit):
            WebAuth.from_env("0.0.0.0", {})  # public with no password: refuse to start
        self.assertTrue(WebAuth.from_env("0.0.0.0", {"DASHBOARD_PASSWORD": PASSWORD}).enabled)

    def test_tokens(self):
        clock = FakeClock()
        auth = WebAuth(PASSWORD, "secret", clock=clock)
        token = auth.issue()
        self.assertTrue(auth.valid(token))
        self.assertFalse(auth.valid(token[:-1] + ("0" if token[-1] != "0" else "1")))  # tampered
        self.assertFalse(auth.valid("garbage"))
        self.assertFalse(auth.valid(None))
        clock.t += auth.ttl + 1
        self.assertFalse(auth.valid(token))  # expired

    def test_changing_password_or_secret_signs_everyone_out(self):
        token = WebAuth(PASSWORD, "secret").issue()
        self.assertFalse(WebAuth(PASSWORD + "!", "secret").valid(token))
        self.assertFalse(WebAuth(PASSWORD, "other").valid(token))
        self.assertTrue(WebAuth(PASSWORD, "secret").valid(token))

    def test_rate_limit(self):
        clock = FakeClock()
        auth = WebAuth(PASSWORD, "s", clock=clock)
        for _ in range(PER_CLIENT_FAILURES):
            self.assertTrue(auth.allowed("1.2.3.4"))
            auth.record_failure("1.2.3.4")
        self.assertFalse(auth.allowed("1.2.3.4"))
        self.assertTrue(auth.allowed("5.6.7.8"))
        clock.t += 15 * 60 + 1
        self.assertTrue(auth.allowed("1.2.3.4"))
        self.assertEqual(len(auth._failures), 0)  # expired entries are dropped

    def test_lockout_table_stays_bounded(self):
        from trading import webauth
        clock = FakeClock()
        auth = WebAuth(PASSWORD, "s", clock=clock)
        for i in range(10_000):  # many fake client addresses
            auth.allowed(f"10.0.{i // 256}.{i % 256}")
        self.assertEqual(len(auth._failures), 0)  # checking alone allocates nothing
        for i in range(3_000):
            auth.record_failure(f"fake-{i}")
            if i % 500 == 0:
                clock.t += 15 * 60 + 1
        self.assertLessEqual(len(auth._failures), webauth.MAX_TRACKED_CLIENTS + 500)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "events-0.jsonl").write_text(
            '{"ts":1,"shard":0,"type":"start","agents":250}\n{"ts":2,"shard":0,"type":"tick","tick":1,"pnl":5}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(PASSWORD, "secret")))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = f"{COOKIE}={cookie}"
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        data = resp.read() if path != "/events" else resp.read1(200)
        conn.close()
        return resp, data

    def login(self, password=PASSWORD, ip="9.9.9.9"):
        return self.request("POST", "/login", urllib.parse.urlencode({"password": password}),
                            headers={"X-Forwarded-For": ip})

    def cookie_from(self, resp):
        header = resp.getheader("Set-Cookie")
        return header.split(";")[0].split("=", 1)[1]

    def test_data_needs_login(self):
        resp, _ = self.request("GET", "/")
        self.assertEqual((resp.status, resp.getheader("Location")), (303, "/login"))
        for path in ("/api/state", "/events"):
            resp, _ = self.request("GET", path)
            self.assertEqual(resp.status, 401, path)
        resp, body = self.request("GET", "/health")
        self.assertEqual(json.loads(body), {"ok": True})  # no details leak while signed out

    def test_login_flow(self):
        resp, body = self.request("GET", "/login")
        self.assertEqual(resp.status, 200)
        self.assertIn(b'type="password"', body)
        resp, _ = self.login("wrong-password-123")
        self.assertEqual(resp.getheader("Location"), "/login?error=1")
        self.assertIsNone(resp.getheader("Set-Cookie"))
        resp, _ = self.login()
        self.assertEqual(resp.getheader("Location"), "/")
        set_cookie = resp.getheader("Set-Cookie")
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=2592000"):
            self.assertIn(flag, set_cookie)
        cookie = self.cookie_from(resp)
        resp, body = self.request("GET", "/", cookie=cookie)
        self.assertEqual(resp.status, 200)
        self.assertIn(b"ATS Matrix", body)
        resp, body = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual(json.loads(body)["agents_reporting"], 250)
        resp, body = self.request("GET", "/events", cookie=cookie)
        self.assertEqual(resp.getheader("Content-Type"), "text/event-stream")
        self.assertTrue(body.startswith(b"data: "))
        resp, _ = self.request("POST", "/logout", body="", cookie=cookie)
        self.assertIn("Max-Age=0", resp.getheader("Set-Cookie"))

    def test_lockout_after_repeated_failures(self):
        for _ in range(PER_CLIENT_FAILURES):
            self.login("wrong-password-123", ip="7.7.7.7")
        resp, _ = self.login(ip="7.7.7.7")  # even the right password is refused while locked
        self.assertEqual(resp.getheader("Location"), "/login?error=locked")
        resp, _ = self.login(ip="8.8.8.8")
        self.assertEqual(resp.getheader("Location"), "/")

    def test_stalled_connections_time_out(self):
        self.assertEqual(self.server.RequestHandlerClass.timeout, 60)

    def test_oversized_login_body_rejected(self):
        resp, _ = self.request("POST", "/login", body="password=" + "x" * 5000)
        self.assertEqual(resp.status, 413)

    def test_security_headers(self):
        resp, _ = self.request("GET", "/login")
        self.assertIn("script-src 'self'", resp.getheader("Content-Security-Policy"))
        self.assertEqual(resp.getheader("X-Frame-Options"), "DENY")
        self.assertIsNotNone(resp.getheader("Strict-Transport-Security"))

    def test_unknown_paths_404(self):
        for path in ("/../config/ats_matrix.json", "/icons/../../webauth.py", "/app.css/../x"):
            resp, _ = self.request("GET", path)
            self.assertIn(resp.status, (401, 404), path)

    def test_installable_app_assets(self):
        resp, body = self.request("GET", "/manifest.webmanifest")
        self.assertEqual(resp.status, 200)
        manifest = json.loads(body)
        self.assertEqual(manifest["display"], "standalone")
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)
        self.assertTrue(any(icon.get("purpose") == "maskable" for icon in manifest["icons"]))
        for icon in manifest["icons"] + [{"src": "/icons/apple-touch-icon.png"}]:
            resp, data = self.request("GET", icon["src"])
            self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "image/png"), icon["src"])
            self.assertTrue(data.startswith(b"\x89PNG"))
        resp, _ = self.request("GET", "/sw.js")
        self.assertEqual(resp.status, 200)
        self.assertIn("javascript", resp.getheader("Content-Type"))

    def test_every_static_file_exists(self):
        web = Path(cloud.__file__).parent / "web"
        for name, _ in STATIC.values():
            self.assertTrue((web / name).is_file(), name)


class SupervisorTests(unittest.TestCase):
    def child(self, name, code):
        # A full environment: Python 3.10 on Windows can't start without SYSTEMROOT.
        return cloud.Child(name, [sys.executable, "-c", f"import sys; sys.exit({code})"], dict(os.environ))

    def run_checks(self, sup, clock, steps=40, dt=0.5):
        for _ in range(steps):
            sup.check()
            for c in sup.children:
                if c.proc:
                    c.proc.wait()
            clock.t += dt

    def test_restarts_crashes_with_backoff_and_lets_finished_runs_stop(self):
        clock = FakeClock(0.0)
        crashing, finishing = self.child("crash", 3), self.child("done", 0)
        sup = cloud.Supervisor([crashing, finishing], clock=clock, log=lambda *a, **k: None)
        self.run_checks(sup, clock)
        self.assertTrue(finishing.done)
        self.assertGreaterEqual(crashing.restarts, 3)
        self.assertLess(crashing.restarts, 10)  # backoff: not restarted every check
        self.assertFalse(crashing.done)

    def test_builds_three_shards_and_ring(self):
        children = cloud.build_children({"AGENT_COUNT": "750"})
        self.assertEqual([c.name for c in children], ["engine-0", "engine-1", "engine-2", "ring", "matrix"])
        self.assertEqual({c.env["SHARD_COUNT"] for c in children}, {"3"})
        self.assertEqual([c.env.get("SHARD_ID") for c in children[:3]], ["0", "1", "2"])


if __name__ == "__main__":
    unittest.main()
