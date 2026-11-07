import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import control, engine
from trading.brain import AgentBrain
from trading.dashboard import EventHub, make_handler
from trading.org import MANAGERS


class BrainTests(unittest.TestCase):
    def test_learns_from_its_own_results(self):
        brain = AgentBrain("a")
        for _ in range(30):
            brain.learn(1.0)
        self.assertGreater(brain.weight, 1.0)
        self.assertEqual(brain.skill, 1.0)
        # A trusted brain presses its signal harder, within the vote limits.
        self.assertGreater(brain.shape(0.4, 0.0, -1.0, 1.0), 0.4)
        self.assertEqual(brain.shape(0.9, 0.0, -1.0, 1.0), 1.0)

    def test_sits_out_after_losing_and_earns_its_way_back(self):
        brain = AgentBrain("b")
        while not brain.sitting_out:
            brain.learn(-1.0)
        self.assertIsNone(brain.shape(0.5, 0.0, -1.0, 1.0))
        while brain.sitting_out:
            brain.learn(1.0)
        self.assertIsNotNone(brain.shape(0.5, 0.0, -1.0, 1.0))

    def test_every_brain_has_its_own_temperament_and_slows_with_experience(self):
        a, b = AgentBrain("x"), AgentBrain("y")
        self.assertNotEqual((a.base_rate, a.sit_out_below), (b.base_rate, b.sit_out_below))
        young = a.learning_rate
        for _ in range(500):
            a.learn(0.0)
        self.assertAlmostEqual(a.learning_rate, young / 2)


class OrgTests(unittest.TestCase):
    def run_engine(self, **env):
        out = io.StringIO()
        desk = engine.run({"TICKS": "40", "TICK_SECONDS": "0", **env}, stream=out)
        return desk, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_departments_and_three_managers_report(self):
        desk, events = self.run_engine()
        self.assertEqual(events[0]["departments"], {"trend": 350, "reversion": 350, "research": 350, "risk": 350})
        org = [e for e in events if e["type"] == "org"][-1]
        self.assertEqual({k: d["agents"] for k, d in org["departments"].items()},
                         {"trend": 350, "reversion": 350, "research": 350, "risk": 350})
        self.assertEqual(len(org["departments"]["trend"]["votes"]), 350)
        self.assertEqual([m["manager"] for m in org["managers"]], [m.name for m in MANAGERS])
        self.assertEqual(org["managers"][0]["agents"], 700)
        self.assertTrue(all(m["call"] for m in org["managers"]))
        self.assertTrue(all(len(e["prices"]) == 10 for e in events if e["type"] == "tick"))
        # Brains have been learning from their own results (once past their warm-up window).
        self.assertTrue(all(a.brain.experience > 0 for a in desk.agents if a.window < 38))

    def test_slider_resizes_running_shards_and_agents_keep_their_brains(self):
        with tempfile.TemporaryDirectory() as tmp:
            control.write(Path(tmp), 500)
            desk, events = self.run_engine(EVENTS_DIR=tmp, AGENT_COUNT="1400")
            resize = [e for e in events if e["type"] == "resize"]
            self.assertEqual(len(resize), 1)
            self.assertEqual((resize[0]["was"], resize[0]["agents"]), (1400, 2000))
            self.assertEqual(len(desk.agents), 2000)
            first = desk.agents[0]
            control.write(Path(tmp), 350)
            engine.resize(desk, 1400, desk.symbols, 0, 1, 42)
            self.assertEqual(len(desk.agents), 1400)
            self.assertIs(desk.agents[0], first)

    def test_slider_rejects_out_of_range(self):
        for bad in (349, 2501, "500", 500.5, True, None):
            with self.assertRaises(ValueError):
                control.validate(bad)


class DashboardOrgTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        for shard in range(2):
            engine.run({"SHARD_ID": str(shard), "SHARD_COUNT": "2", "TICKS": "30", "TICK_SECONDS": "0",
                        "EVENTS_DIR": self.tmp.name}, stream=io.StringIO())
        self.hub = EventHub(Path(self.tmp.name))
        self.hub.poll_files()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.hub))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def post(self, body: bytes, ctype="application/json"):
        req = urllib.request.Request(self.base + "/api/agents", data=body, method="POST",
                                     headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.load(resp)
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_org_merges_shards(self):
        org = json.load(urllib.request.urlopen(self.base + "/api/org"))
        self.assertEqual(sum(d["agents"] for d in org["departments"].values()), 1400)
        self.assertEqual(len(org["departments"]["research"]["votes"]), 350)
        self.assertEqual(len(org["managers"]), 3)
        self.assertIsNotNone(org["departments"]["trend"]["avg_weight"])
        prices = json.load(urllib.request.urlopen(self.base + "/api/prices"))
        self.assertEqual(len(prices["history"]), 30)
        page = urllib.request.urlopen(self.base + "/3d").read().decode()
        self.assertIn("lightweight-charts@4.1.3", page)
        self.assertIn("three.js/r128", page)

    def test_slider_endpoint(self):
        self.assertEqual(self.post(b'{"per_department": 600}'), (200, {"per_department": 600, "total": 2400}))
        self.assertEqual(control.read(Path(self.tmp.name)), 600)
        self.assertEqual(json.load(urllib.request.urlopen(self.base + "/api/agents")), {"per_department": 600})
        self.assertEqual(self.post(b'{"per_department": 99999}')[0], 400)
        self.assertEqual(self.post(b"per_department=600", "application/x-www-form-urlencoded")[0], 415)
        self.assertEqual(control.read(Path(self.tmp.name)), 600)


if __name__ == "__main__":
    unittest.main()
