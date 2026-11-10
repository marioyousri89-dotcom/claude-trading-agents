"""Real platform connectors: paper by default, demo only where the broker has one, real money locked."""

import re
import subprocess
import unittest
from pathlib import Path

from trading import platforms as P
from trading.vault import Vault

ROOT = Path(__file__).resolve().parent.parent


class ModeTests(unittest.TestCase):
    def test_everything_starts_in_paper(self):
        rows = P.status({})
        self.assertEqual([r["mode"] for r in rows], ["paper"] * 4)
        self.assertTrue(all(r["live_locked"] for r in rows))
        self.assertFalse(P.LIVE_TRADING_ENABLED)

    def test_live_request_is_refused(self):
        rows = {r["key"]: r for r in P.status({"ETORO_MODE": "live", "MT5_MODE": "LIVE", "MOOMOO_MODE": "real"})}
        self.assertEqual(rows["etoro"]["mode"], "paper")
        self.assertIn("locked", rows["etoro"]["note"])
        self.assertEqual(rows["mt5"]["mode"], "paper")
        self.assertIn("unknown", rows["moomoo"]["note"])

    def test_demo_only_where_supported(self):
        rows = {r["key"]: r for r in P.status({"MT5_MODE": "demo", "MOOMOO_MODE": "demo", "ETORO_MODE": "demo"})}
        self.assertEqual(rows["mt5"]["mode"], "demo")
        self.assertEqual(rows["moomoo"]["mode"], "demo")
        self.assertEqual(rows["etoro"]["mode"], "paper")

    def test_status_never_shows_values(self):
        env = {"ETORO_API_KEY": "sekrit-api-value", "ETORO_USER_KEY": "sekrit-user-value"}
        text = repr(P.status(env))
        self.assertNotIn("sekrit", text)
        self.assertTrue(next(r for r in P.status(env) if r["key"] == "etoro")["configured"])

    def test_vault_holds_platform_secrets_locked(self):
        v = Vault("paper", {"ETORO_API_KEY": "x"})
        self.assertIn("ETORO_API_KEY", v.status()["loaded"])
        with self.assertRaises(PermissionError):
            v.get("ETORO_API_KEY")


class OrderTests(unittest.TestCase):
    def test_paper_order_fills_at_live_quote(self):
        urls = []

        def http(url, headers=None):
            urls.append(url)
            return {"mid": "0.42"}
        c = P.connect("polymarket", {}, http)
        fill = c.place_order("123", "buy", 100)
        self.assertEqual(fill["mode"], "paper")
        self.assertAlmostEqual(fill["price"], 0.42)
        self.assertEqual(c.book.position("123"), 100)
        self.assertIn("clob.polymarket.com/midpoint?token_id=123", urls[0])

    def test_real_money_always_refused(self):
        c = P.connect("polymarket", {}, lambda *a: {"mid": "0.5"})
        with self.assertRaises(P.LiveTradingLocked):
            c.place_order("1", "buy", 1, live=True)
        with self.assertRaises(ValueError):
            c.place_order("1", "hold", 1)

    def test_etoro_quote_uses_headers_from_env(self):
        seen = {}

        def http(url, headers=None):
            seen.update(url=url, headers=headers)
            return {"results": [{"instrumentId": 1001, "bid": 99.0, "ask": 101.0}]}
        c = P.connect("etoro", {"ETORO_API_KEY": "a", "ETORO_USER_KEY": "b"}, http)
        self.assertEqual(c.quote("1001"), 100.0)
        self.assertIn("/api/v2/market-data/rates?instrumentIds=1001", seen["url"])
        self.assertEqual(seen["headers"]["x-api-key"], "a")
        self.assertIn("x-request-id", seen["headers"])
        with self.assertRaises(PermissionError):
            P.connect("etoro", {}, http).quote("1001")

    def test_no_demo_account_means_no_demo_order(self):
        c = P.connect("etoro", {"ETORO_MODE": "demo"}, lambda *a: {})
        self.assertEqual(c.mode, "paper")


class NoSecretsInRepoTests(unittest.TestCase):
    """Fail the build if anything that looks like a real key is committed."""

    PATTERNS = [r"sk-ant-[A-Za-z0-9_-]{20,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                r"ghp_[A-Za-z0-9]{30,}", r"xox[bp]-[A-Za-z0-9-]{20,}", r"\b0x[0-9a-fA-F]{64}\b"]

    def test_no_keys_committed(self):
        try:
            files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        hits = []
        for name in files:
            path = ROOT / name
            if path.suffix in (".png", ".ico") or "vendor/" in name or not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            hits += [f"{name}: {p}" for p in self.PATTERNS if re.search(p, text)]
        self.assertEqual(hits, [])
