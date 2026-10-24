"""Multi-container test: run against a live `docker compose up` stack.

    docker compose up -d --build --wait
    DASHBOARD_URL=http://127.0.0.1:8080 python -m unittest tests.containers.test_stack -v

Skipped when DASHBOARD_URL is not set, so the normal test run stays offline.
"""

import json
import os
import time
import unittest
import urllib.request

URL = os.environ.get("DASHBOARD_URL")
SHARDS = int(os.environ.get("EXPECT_SHARDS", 3))
AGENTS = int(os.environ.get("EXPECT_AGENTS", 750))


def get_state() -> dict:
    with urllib.request.urlopen(URL + "/api/state", timeout=5) as resp:
        return json.load(resp)


@unittest.skipUnless(URL, "set DASHBOARD_URL to run container tests")
class StackTests(unittest.TestCase):
    def wait_for(self, check, timeout=60):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = get_state()
                if check(last):
                    return last
            except OSError:
                pass
            time.sleep(1)
        self.fail(f"condition not met within {timeout}s; last state: {json.dumps(last)[:2000]}")

    def test_all_shards_report_all_agents(self):
        state = self.wait_for(lambda s: len(s["shards"]) == SHARDS and s["agents_reporting"] == AGENTS)
        self.assertEqual(sorted(state["shards"]), [str(i) for i in range(SHARDS)])

    def test_every_shard_keeps_ticking(self):
        first = self.wait_for(lambda s: all("tick" in v for v in s["shards"].values()) and len(s["shards"]) == SHARDS)
        before = {k: v["tick"]["tick"] for k, v in first["shards"].items()}
        after = self.wait_for(lambda s: all(s["shards"][k]["tick"]["tick"] > t for k, t in before.items()))
        for shard in after["shards"].values():
            self.assertEqual(shard["start"]["mode"], "paper")

    def test_live_log_stream(self):
        with urllib.request.urlopen(URL + "/events", timeout=10) as resp:
            self.assertEqual(resp.headers["Content-Type"], "text/event-stream")
            seen = set()
            deadline = time.time() + 30
            while len(seen) < SHARDS and time.time() < deadline:
                line = resp.readline().decode()
                if line.startswith("data: "):
                    seen.add(json.loads(line[6:])["shard"])
        self.assertEqual(seen, set(range(SHARDS)))

    def test_dashboard_page(self):
        with urllib.request.urlopen(URL + "/", timeout=5) as resp:
            self.assertIn(b"EventSource", resp.read())


if __name__ == "__main__":
    unittest.main()
