import io
import json
import tempfile
import threading
import unittest
import urllib.request
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path

from trading import engine
from trading.agents import Breakout, MeanReversion, Momentum, build_team
from trading.broker import PaperBroker
from trading.dashboard import EventHub, make_handler
from trading.risk import RiskLimits, RiskManager


class TeamTests(unittest.TestCase):
    def test_750_agents_mixed_team(self):
        team = build_team(750, ["AAPL", "BTC"], seed=1)
        self.assertEqual(len(team), 750)
        self.assertEqual(len({a.agent_id for a in team}), 750)
        roles = Counter(a.role for a in team)
        self.assertEqual(roles, {"trader": 600, "analyst": 120, "risk": 30})
        self.assertEqual({a.symbol for a in team}, {"AAPL", "BTC"})

    def test_shards_partition_the_team(self):
        symbols = ["AAPL", "BTC", "ETH"]
        shards = [engine.shard_agents(750, symbols, i, 3, seed=7) for i in range(3)]
        ids = [a.agent_id for shard in shards for a in shard]
        self.assertEqual(len(ids), 750)
        self.assertEqual(len(set(ids)), 750)
        self.assertEqual([len(s) for s in shards], [250, 250, 250])

    def test_strategies_react_to_prices(self):
        up = [100 + i for i in range(30)]
        momentum, breakout, reversion = Momentum("m", "X", 10), Breakout("b", "X", 10), MeanReversion("r", "X", 10)
        for p in up:
            m, b, r = momentum.observe(p), breakout.observe(p), reversion.observe(p)
        self.assertGreater(m.value, 0)
        self.assertEqual(b.value, 1.0)
        self.assertLess(r.value, 0)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.broker = PaperBroker(cash=100_000)
        self.risk = RiskManager(RiskLimits(max_position_value=10_000, max_order_value=2_000, max_drawdown=0.05), self.broker)
        self.prices = {"X": 100.0}

    def test_order_size_capped(self):
        qty, reason = self.risk.approve("X", 1_000, self.prices)
        self.assertAlmostEqual(qty, 20)
        self.assertEqual(reason, "order size capped")

    def test_position_limit(self):
        self.broker.fill("X", 95, 100.0)
        qty, reason = self.risk.approve("X", 20, self.prices)
        self.assertAlmostEqual(qty, 5)
        self.assertEqual(reason, "position limit")

    def test_drawdown_halts_new_risk_but_allows_reducing(self):
        self.broker.fill("X", 100, 100.0)
        crashed = {"X": 1.0}
        self.risk.update(crashed)
        self.assertTrue(self.risk.halted)
        self.assertEqual(self.risk.approve("X", 10, crashed)[0], 0.0)
        self.assertEqual(self.risk.approve("X", -10, crashed)[0], -10)
        self.assertEqual(self.risk.approve("X", -150, crashed), (-100, "halted: reduced to flatten"))


class EngineTests(unittest.TestCase):
    def test_full_750_agent_run_stays_within_limits(self):
        out = io.StringIO()
        desk = engine.run({"AGENT_COUNT": "750", "TICKS": "200", "TICK_SECONDS": "0"}, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        kinds = Counter(e["type"] for e in events)
        self.assertEqual(kinds["start"], 1)
        self.assertEqual(kinds["tick"], 200)
        self.assertEqual(kinds["stop"], 1)
        self.assertGreater(kinds["order"], 0)
        self.assertGreater(kinds["leaderboard"], 0)
        self.assertEqual(events[0]["agents"], 750)
        limit = desk.risk.limits.max_position_value
        for symbol, qty in desk.broker.positions.items():
            # Small slack: the price can move after the last rebalance.
            self.assertLessEqual(abs(qty * desk.last_prices[symbol]), limit * 1.2)
        self.assertLessEqual(desk.broker.gross_exposure(desk.last_prices), desk.risk.limits.max_gross_exposure * 1.2)

    def test_same_seed_same_result(self):
        env = {"AGENT_COUNT": "60", "TICKS": "100", "TICK_SECONDS": "0"}
        a = engine.run(env, stream=io.StringIO())
        b = engine.run(env, stream=io.StringIO())
        self.assertEqual(a.broker.positions, b.broker.positions)


class DashboardTests(unittest.TestCase):
    def test_hub_tails_shard_files_and_serves_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            for shard in range(3):
                engine.run(
                    {"AGENT_COUNT": "90", "SHARD_ID": str(shard), "SHARD_COUNT": "3", "TICKS": "25",
                     "TICK_SECONDS": "0", "EVENTS_DIR": tmp},
                    stream=io.StringIO(),
                )
            hub = EventHub(Path(tmp))
            hub.poll_files()
            state = hub.state()
            self.assertEqual(state["agents_reporting"], 90)
            self.assertEqual(sorted(state["shards"]), ["0", "1", "2"])

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(hub))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                health = json.load(urllib.request.urlopen(base + "/health"))
                self.assertTrue(health["ok"])
                with urllib.request.urlopen(base + "/events") as resp:
                    first = resp.readline().decode()
                self.assertTrue(first.startswith("data: {"))
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
