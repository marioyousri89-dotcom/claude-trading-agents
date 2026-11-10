"""JARVIS: her knowledge, her brains, the owner-only passphrase, rate limits and the web endpoints."""

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from trading import jarvis as J
from trading.dashboard import EventHub, make_handler
from trading.webauth import COOKIE, WebAuth

PASSWORD = "correct-horse-battery-staple"
PHRASE = "Open the pod bay doors"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


SNAP = {
    "tick": 120, "agents_reporting": 1400, "session_pnl": 1234.5,
    "jev": {"approved": 50, "rejected": 3, "approval_rate": 0.94},
    "departments": {"trend": {"name": "Trend Trading", "agents": 350, "active": 300, "sitting_out": 12, "pnl": 900.0,
                              "skill": 0.55, "avg_trust": 1.0, "graduates": 343, "trained": 350, "exam_avg": 0.86}},
    "managers": [{"manager": "Atlas", "title": "Head of Trading", "call": "favour trend", "training": "ok", "pnl": 10.0}],
    "news": ["Fed holds rates"], "practice": None,
    "platforms": [{"name": "Polymarket", "mode": "paper", "configured": True}],
}


class KnowledgeTests(unittest.TestCase):
    def test_handbook_covers_the_whole_app(self):
        text = J.handbook()
        for word in ("Atlas", "Sage", "Sentinel", "Trend Trading", "Research", "Risk", "Jesse Livermore",
                     "Ed Thorp", "Polymarket", "eToro", "MetaTrader 5", "moomoo", "Jev", "paper", "locked"):
            self.assertIn(word, text)

    def test_snapshot_from_dashboard_reports(self):
        org = {"tick": 7, "departments": {"risk": {"name": "Risk", "agents": 350, "active": 340, "pnl": 1.0,
                                                    "school": {"graduates": 349, "trained": 350, "avg_exam": 0.9}}},
               "managers": [], "news": [{"headline": "Tariffs"}]}
        snap = J.snapshot(org, {"agents_reporting": 1400, "jev_desk": {"approved": 2, "session_pnl": 5.0}})
        self.assertEqual(snap["departments"]["risk"]["graduates"], 349)
        self.assertEqual(snap["news"], ["Tariffs"])
        self.assertEqual(len(snap["platforms"]), 4)
        self.assertEqual(J.snapshot(None, None)["agents_reporting"], 0)


class BuiltinBrainTests(unittest.TestCase):
    brain = J.BuiltinBrain()

    def test_answers(self):
        ask = lambda q: self.brain.answer(q, SNAP)  # noqa: E731
        self.assertIn("1,400 agents", ask("how are we doing?"))
        self.assertIn("343 of 350", ask("tell me about the trend department"))
        self.assertIn("favour trend", ask("what does Atlas say"))
        self.assertIn("locked", ask("which platforms are connected?"))
        self.assertIn("Soros", ask("who are the masters"))
        self.assertIn("Livermore", ask("what does Livermore teach"))
        self.assertIn("Gann", ask("tell me about Gann"))
        self.assertIn("19 master", ask("who are the masters"))
        self.assertIn("Fed holds rates", ask("any news"))
        self.assertIn("JARVIS", ask("who are you"))
        self.assertIn("haven't reported", self.brain.answer("status", {}))


class ClaudeBrainTests(unittest.TestCase):
    def fake(self, response):
        calls = []

        def create(**kw):
            calls.append(kw)
            return response
        client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
        return J.ClaudeBrain(client), calls

    def test_request_shape(self):
        resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="All good.")])
        brain, calls = self.fake(resp)
        self.assertEqual(brain.answer("status?", SNAP, [{"role": "user", "content": "hi"},
                                                      {"role": "assistant", "content": "hello"}]), "All good.")
        kw = calls[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["fallbacks"], "default")
        self.assertIn("HANDBOOK", kw["system"][0]["text"])
        self.assertEqual(len(kw["messages"]), 3)
        self.assertIn("LIVE SNAPSHOT", kw["messages"][-1]["content"])

    def test_refusal(self):
        brain, _ = self.fake(SimpleNamespace(stop_reason="refusal", content=[]))
        self.assertIn("can't", brain.answer("x", SNAP))

    def test_make_brain(self):
        self.assertIsInstance(J.make_brain({}), J.BuiltinBrain)


class OwnerOnlyTests(unittest.TestCase):
    def test_fails_closed_on_the_internet_without_passphrase(self):
        j = J.Jarvis(None, required=True)
        self.assertFalse(j.enabled)
        self.assertFalse(j.unlocked(j.issue()))
        self.assertIn("JARVIS_PASSPHRASE", j.status(False)["reason"])
        self.assertTrue(J.Jarvis(None, required=False).unlocked(None))  # local use
        with self.assertRaises(SystemExit):
            J.Jarvis("short", required=True)

    def test_passphrase_and_tokens(self):
        clock = Clock()
        j = J.Jarvis(PHRASE, required=True, secret="s", clock=clock)
        self.assertTrue(j.check_passphrase("open the pod bay doors."))  # speech varies case and punctuation
        self.assertFalse(j.check_passphrase("open the pod bay"))
        token = j.issue()
        self.assertTrue(j.unlocked(token))
        self.assertFalse(j.unlocked(token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertFalse(j.unlocked(None))
        self.assertFalse(J.Jarvis(PHRASE + " now", True, secret="s").unlocked(token))  # new phrase: relock
        clock.t += j.ttl + 1
        self.assertFalse(j.unlocked(token))

    def test_ask_survives_brain_errors_and_caps_length(self):
        class Broken:
            name = "claude"

            def answer(self, *a):
                raise RuntimeError("network down")
        j = J.Jarvis(PHRASE, True, brain=Broken())
        self.assertIn("1,400", j.ask("c", "status " + "x" * 5000, SNAP))
        self.assertLessEqual(len(j._history["c"][0]["content"]), J.MAX_QUESTION_CHARS)

    def test_rate_limiter(self):
        clock = Clock()
        r = J.RateLimiter(2, 60, clock)
        r.hit("a"); r.hit("a")
        self.assertFalse(r.allowed("a"))
        self.assertTrue(r.allowed("b"))
        clock.t += 61
        self.assertTrue(r.allowed("a"))


class JarvisServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        Path(self.tmp.name, "events-0.jsonl").write_text('{"ts":1,"shard":0,"type":"start","agents":350}\n')
        hub = EventHub(Path(self.tmp.name))
        hub.poll_files()
        self.jarvis = J.Jarvis(PHRASE, required=True, secret="s")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub, WebAuth(PASSWORD, "secret"), self.jarvis))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        resp, _ = self.request("POST", "/login", urllib.parse.urlencode({"password": PASSWORD}),
                               {"Content-Type": "application/x-www-form-urlencoded"})
        self.session = resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def api(self, path, body, unlock=None, extra=None):
        cookie = f"{COOKIE}={self.session}" + (f"; {J.UNLOCK_COOKIE}={unlock}" if unlock else "")
        headers = {"Content-Type": "application/json", "Cookie": cookie, **(extra or {})}
        resp, data = self.request("POST", path, json.dumps(body), headers)
        return resp, json.loads(data or b"{}")

    def test_page_needs_login_and_sends_mic_policy(self):
        resp, _ = self.request("GET", "/jarvis")
        self.assertEqual(resp.getheader("Location"), "/login")
        resp, body = self.request("GET", "/jarvis", headers={"Cookie": f"{COOKIE}={self.session}"})
        self.assertEqual(resp.status, 200)
        self.assertIn(b"jarvis.js", body)
        self.assertIn("microphone=(self)", resp.getheader("Permissions-Policy"))
        for asset in ("/jarvis.js", "/jarvis.css"):
            self.assertEqual(self.request("GET", asset)[0].status, 200)

    def test_unlock_then_ask(self):
        resp, body = self.api("/api/jarvis/ask", {"question": "status"})
        self.assertEqual(resp.status, 403)  # logged in but not unlocked
        resp, body = self.api("/api/jarvis/unlock", {"passphrase": "nope nope nope"})
        self.assertEqual(resp.status, 403)
        resp, body = self.api("/api/jarvis/unlock", {"passphrase": PHRASE})
        self.assertTrue(body["unlocked"])
        cookie = resp.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        token = cookie.split(";")[0].split("=", 1)[1]
        resp, body = self.api("/api/jarvis/ask", {"question": "how many agents?"}, token)
        self.assertEqual(resp.status, 200)
        self.assertEqual(body["brain"], "built-in")
        self.assertTrue(body["reply"])

    def test_unlock_needs_login(self):
        resp, _ = self.request("POST", "/api/jarvis/unlock", json.dumps({"passphrase": PHRASE}),
                               {"Content-Type": "application/json"})
        self.assertEqual(resp.status, 401)

    def test_passphrase_lockout(self):
        for _ in range(J.UNLOCK_FAILURES):
            self.api("/api/jarvis/unlock", {"passphrase": "wrong guess here"})
        resp, _ = self.api("/api/jarvis/unlock", {"passphrase": PHRASE})
        self.assertEqual(resp.status, 429)  # even the right phrase waits out the lockout

    def test_question_rate_limit(self):
        token = self.jarvis.issue()
        for _ in range(J.QUESTIONS_PER_MINUTE):
            self.assertEqual(self.api("/api/jarvis/ask", {"question": "hi"}, token)[0].status, 200)
        self.assertEqual(self.api("/api/jarvis/ask", {"question": "hi"}, token)[0].status, 429)

    def test_cross_site_post_refused(self):
        resp, _ = self.api("/api/jarvis/unlock", {"passphrase": PHRASE}, extra={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)
        host = f"127.0.0.1:{self.server.server_port}"
        resp, _ = self.api("/api/jarvis/unlock", {"passphrase": PHRASE}, extra={"Origin": f"http://{host}"})
        self.assertEqual(resp.status, 200)

    def test_platforms_endpoint_shows_no_secrets(self):
        resp, body = self.request("GET", "/api/platforms", headers={"Cookie": f"{COOKIE}={self.session}"})
        data = json.loads(body)
        self.assertEqual(data["live_trading"], "locked")
        self.assertEqual({p["mode"] for p in data["platforms"]}, {"paper"})
