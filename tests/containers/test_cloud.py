"""Single-container cloud test: run against `python -m trading.cloud` in Docker.

    docker run -d -p 127.0.0.1:8090:8090 -e PORT=8090 -e DASHBOARD_PASSWORD=... \\
        claude-trading-agents python -m trading.cloud
    CLOUD_URL=http://127.0.0.1:8090 CLOUD_PASSWORD=... python -m unittest tests.containers.test_cloud -v

Skipped unless CLOUD_URL and CLOUD_PASSWORD are set.
"""

import http.client
import json
import os
import time
import unittest
import urllib.parse

URL = os.environ.get("CLOUD_URL")
PASSWORD = os.environ.get("CLOUD_PASSWORD")


@unittest.skipUnless(URL and PASSWORD, "set CLOUD_URL and CLOUD_PASSWORD to run cloud container tests")
class CloudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        parts = urllib.parse.urlsplit(URL)
        cls.host, cls.port = parts.hostname, parts.port

    def request(self, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        headers = {"Cookie": f"ats_session={cookie}"} if cookie else {}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def login(self) -> str:
        resp, _ = self.request("POST", "/login", urllib.parse.urlencode({"password": PASSWORD}))
        self.assertEqual(resp.getheader("Location"), "/")
        return resp.getheader("Set-Cookie").split(";")[0].split("=", 1)[1]

    def test_locked_until_signed_in(self):
        resp, _ = self.request("GET", "/api/state")
        self.assertEqual(resp.status, 401)
        resp, _ = self.request("GET", "/")
        self.assertEqual(resp.getheader("Location"), "/login")

    def test_whole_system_runs_in_one_container(self):
        cookie = self.login()
        deadline = time.time() + 60
        state = {}
        while time.time() < deadline:
            _, body = self.request("GET", "/api/state", cookie=cookie)
            state = json.loads(body)
            if state["agents_reporting"] == 750 and "ring" in state["shards"] and state["heatmap"]:
                break
            time.sleep(1)
        self.assertEqual(state["agents_reporting"], 750)
        self.assertEqual(sorted(state["shards"]), ["0", "1", "2", "ring"])
        self.assertEqual(len(state["heatmap"]), 8)

    def test_health_check_for_the_host(self):
        deadline = time.time() + 60
        while time.time() < deadline:
            resp, body = self.request("GET", "/health")
            if resp.status == 200:
                break
            time.sleep(1)
        self.assertEqual(json.loads(body), {"ok": True})

    def test_app_icon_and_manifest(self):
        resp, body = self.request("GET", "/manifest.webmanifest")
        self.assertEqual(json.loads(body)["name"], "ATS Matrix")
        resp, body = self.request("GET", "/icons/apple-touch-icon.png")
        self.assertEqual(resp.status, 200)
        self.assertTrue(body.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
