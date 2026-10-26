"""Tests for the ATS Matrix V2 pieces: config, Jev, Dual-Brain, Polymarket, predictor, vault, ring."""

import copy
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from trading import config as ats_config
from trading import engine, ring
from trading.brains import MIN_WEIGHT, BrainAlpha, BrainBeta
from trading.jev import RING_BOTS, Jev
from trading.polymarket import (BinaryMarket, CrossBookPair, NegRiskEvent, Outcome, PolymarketBot, taker_fee)
from trading.predictor import CryptoPredictor, Pool, SimulatedLaunchpad, traction_score
from trading.vault import Vault, VaultLocked

CFG = ats_config.load()
RAW = json.loads(ats_config.DEFAULT_PATH.read_text())


def proposal(**overrides) -> dict:
    p = {"bot": "polymarket", "venue": "polymarket", "instrument": "m:YES", "side": "buy", "qty": 100.0,
         "price": 0.5, "strategy": "s", "expected_slippage_pct": 0.1, "depth_usdc": 100_000.0}
    p.update(overrides)
    return p


class ConfigTests(unittest.TestCase):
    def test_shipped_config_matches_blueprint(self):
        self.assertEqual(CFG.version, "2.0.0")
        self.assertEqual(CFG.beta.loop_target_seconds, 1.5)
        self.assertEqual(CFG.beta.max_categorical_paths, 255)
        self.assertEqual(CFG.beta.learning_interval_seconds, 3600)
        self.assertEqual(CFG.risk.max_drawdown, 0.05)
        self.assertEqual(CFG.risk.max_trade_usdc, 100_000)
        self.assertEqual(CFG.risk.slippage_tolerance_pct, 0.5)
        self.assertEqual(CFG.risk.min_depth_usdc, 50_000)
        self.assertEqual(CFG.risk.oracle_buffer, 0.03)
        self.assertEqual(CFG.fee_rates, {"crypto": 0.07, "sports": 0.05, "politics_finance": 0.0624})
        self.assertEqual(CFG.thresholds.maker_band, (0.4, 0.6))

    def test_bad_values_are_rejected(self):
        cases = [
            (("risk_gate_limits", "global_max_drawdown_percent"), "5"),
            (("risk_gate_limits", "max_trade_size_usdc"), -1),
            (("brain_beta_configuration", "max_categorical_paths"), 2.5),
            (("brain_beta_configuration", "autopilot_24_7"), "yes"),
            (("polymarket_fee_constants", "crypto_category_rate"), True),
        ]
        for (section, key), value in cases:
            data = copy.deepcopy(RAW)
            data[section][key] = value
            with self.subTest(key=key), self.assertRaises(ats_config.ConfigError):
                ats_config.parse(data)

    def test_missing_key_and_inverted_band(self):
        data = copy.deepcopy(RAW)
        del data["risk_gate_limits"]["slippage_tolerance_percent"]
        with self.assertRaisesRegex(ats_config.ConfigError, "missing config key"):
            ats_config.parse(data)
        data = copy.deepcopy(RAW)
        data["strategy_activation_thresholds"]["maker_rebate_hunting_price_band"] = {"min_usd": 0.6, "max_usd": 0.4}
        with self.assertRaisesRegex(ats_config.ConfigError, "min_usd must be below"):
            ats_config.parse(data)

    def test_invalid_json_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
        with self.assertRaisesRegex(ats_config.ConfigError, "not valid JSON"):
            ats_config.load(f.name)
        Path(f.name).unlink()


class JevTests(unittest.TestCase):
    def setUp(self):
        self.jev = Jev(CFG)

    def code(self, raw) -> str:
        return self.jev.validate(raw).code

    def test_valid_proposal_approved(self):
        decision = self.jev.validate(proposal())
        self.assertTrue(decision.approved)
        self.assertEqual(decision.proposal.notional, 50.0)

    def test_contract_shape_and_types(self):
        self.assertEqual(self.code("buy"), "not_an_object")
        self.assertEqual(self.code({**proposal(), "extra": 1}), "bad_fields")
        bad = proposal()
        del bad["side"]
        self.assertEqual(self.code(bad), "bad_fields")
        self.assertEqual(self.code(proposal(qty=True)), "bad_type:qty")
        self.assertEqual(self.code(proposal(qty="100")), "bad_type:qty")
        self.assertEqual(self.code(proposal(price=float("nan"))), "bad_type:price")
        self.assertEqual(self.code(proposal(instrument="")), "bad_type:instrument")

    def test_business_rules(self):
        self.assertEqual(self.code(proposal(bot="vault")), "bot_not_allowed_to_trade")
        self.assertEqual(self.code(proposal(venue="cex")), "unknown_venue")
        self.assertEqual(self.code(proposal(side="hold")), "bad_side")
        self.assertEqual(self.code(proposal(qty=-1.0)), "non_positive_value")
        self.assertEqual(self.code(proposal(price=1.0)), "price_out_of_range")
        self.assertEqual(self.code(proposal(depth_usdc=49_999.0)), "insufficient_depth")
        self.assertEqual(self.code(proposal(venue="spot", qty=1001.0, price=100.0)), "trade_too_large")
        self.assertEqual(self.code(proposal(expected_slippage_pct=0.51)), "slippage_too_high")

    def test_path_budget_per_loop(self):
        for i in range(CFG.beta.max_categorical_paths):
            self.assertTrue(self.jev.validate(proposal(instrument=f"m{i}:YES")).approved)
        self.assertEqual(self.code(proposal(instrument="one-too-many:YES")), "path_limit")
        self.assertTrue(self.jev.validate(proposal(instrument="m0:YES")).approved)  # known path still fine
        self.jev.new_loop()
        self.assertTrue(self.jev.validate(proposal(instrument="one-too-many:YES")).approved)

    def test_counts(self):
        self.jev.validate(proposal())
        self.jev.validate(proposal(side="hold"))
        self.assertEqual((self.jev.counts["approved"], self.jev.counts["rejected"]), (1, 1))
        self.assertEqual(dict(self.jev.reasons), {"bad_side": 1})


class BrainTests(unittest.TestCase):
    def test_alpha_learns_toward_winners_and_keeps_a_floor(self):
        now = [0.0]
        alpha = BrainAlpha(["a", "b", "c"], interval_seconds=3600, clock=lambda: now[0])
        self.assertFalse(alpha.due())
        now[0] = 3600
        self.assertTrue(alpha.due())
        w = alpha.learn({"a": 1000.0, "b": 0.0, "c": -1000.0}, scale=500)
        self.assertGreater(w["a"], w["b"])
        self.assertGreater(w["b"], w["c"])
        self.assertAlmostEqual(sum(w.values()), 1.0)
        for _ in range(20):
            now[0] += 3600
            w = alpha.learn({"a": 1000.0 * (alpha.updates + 2), "b": 0.0, "c": -1000.0 * (alpha.updates + 2)})
        self.assertGreaterEqual(min(w.values()), MIN_WEIGHT * 0.9)
        self.assertFalse(alpha.due())

    def test_alpha_disabled(self):
        alpha = BrainAlpha(["a"], interval_seconds=0, enabled=False)
        self.assertFalse(alpha.due())

    def test_beta_counts_overruns(self):
        t = iter([0.0, 1.0, 10.0, 12.0])
        beta = BrainBeta(CFG, clock=lambda: next(t))
        beta.begin()
        self.assertEqual(beta.end(), 1.0)
        beta.begin()
        beta.end()
        self.assertEqual((beta.loops, beta.overruns), (2, 1))


class PolymarketTests(unittest.TestCase):
    def setUp(self):
        self.bot = PolymarketBot(CFG)
        self.w = 0.25  # equal weight across 4 strategies

    def test_fee_peaks_at_half(self):
        self.assertAlmostEqual(taker_fee(0.5, 0.07), 0.0175)
        self.assertLess(taker_fee(0.9, 0.07), taker_fee(0.5, 0.07))

    def test_neg_risk_squeeze(self):
        cheap = NegRiskEvent("e", "sports", [Outcome(f"o{i}", 0.2, 500_000) for i in range(4)])  # sums to 0.80
        opp = self.bot.neg_risk_squeeze(cheap, self.w)
        self.assertIsNotNone(opp)
        self.assertEqual(len(opp.legs), 4)
        self.assertGreater(opp.locked_pnl, 0)
        jev = Jev(CFG)
        self.assertTrue(all(jev.validate(leg).approved for leg in opp.legs))
        fair = NegRiskEvent("e", "sports", [Outcome(f"o{i}", 0.245, 500_000) for i in range(4)])  # 0.98: below epsilon
        self.assertIsNone(self.bot.neg_risk_squeeze(fair, self.w))

    def test_squeeze_needs_edge_after_fees_and_oracle_buffer(self):
        # 1 - 0.95 = 0.05 >= epsilon 0.04, but fees + 3% buffer eat the edge.
        thin = NegRiskEvent("e", "crypto", [Outcome(f"o{i}", 0.2375, 500_000) for i in range(4)])
        self.assertIsNone(self.bot.neg_risk_squeeze(thin, self.w))

    def test_cross_book_arb(self):
        arb = CrossBookPair("p", "sports", 0.40, 0.50, 500_000, 500_000)
        opp = self.bot.cross_book_arb(arb, self.w)
        self.assertIsNotNone(opp)
        self.assertGreater(opp.locked_pnl, 0)
        self.assertIsNone(self.bot.cross_book_arb(CrossBookPair("p", "sports", 0.5, 0.495, 500_000, 500_000), self.w))

    def test_maker_rebate_only_in_band(self):
        self.assertIsNotNone(self.bot.maker_rebate(BinaryMarket("m", "crypto", 0.5, 200_000), self.w))
        self.assertIsNone(self.bot.maker_rebate(BinaryMarket("m", "crypto", 0.7, 200_000), self.w))
        self.assertIsNone(self.bot.maker_rebate(BinaryMarket("m", "crypto", 0.39, 200_000), self.w))

    def test_sizing_respects_limits_at_full_weight(self):
        event = NegRiskEvent("e", "sports", [Outcome(f"o{i}", 0.15, 10_000_000) for i in range(4)])
        opp = self.bot.neg_risk_squeeze(event, weight=1.0)
        jev = Jev(CFG)
        for leg in opp.legs:
            self.assertLessEqual(leg["qty"] * leg["price"], CFG.risk.max_trade_usdc)
            self.assertTrue(jev.validate(leg).approved)


class PredictorTests(unittest.TestCase):
    def pool(self, **kw):
        base = dict(token="T", liquidity_usdc=1_000_000, holder_growth=5, volume_velocity=1.2, dev_wallet_share=0.05,
                    price=1.0, quality=0.9)
        base.update(kw)
        return Pool(**base)

    def test_traction_score_orders_pools(self):
        self.assertGreater(traction_score(self.pool()), 0.9)
        self.assertLess(traction_score(self.pool(dev_wallet_share=0.6, holder_growth=0, volume_velocity=0)), 0.1)

    def test_buys_strong_pool_and_skips_rug_risk(self):
        bot = CryptoPredictor(CFG)
        props = bot.propose([self.pool(), self.pool(token="RUG", dev_wallet_share=0.35, holder_growth=20)], 1, 0.25)
        self.assertEqual([p["instrument"] for p in props], ["T"])
        self.assertTrue(Jev(CFG).validate(props[0]).approved)

    def test_exits_in_chunks_under_slippage_limit(self):
        bot = CryptoPredictor(CFG)
        pool = self.pool(liquidity_usdc=100_000)
        bot.fill({"instrument": "T", "side": "buy", "qty": 10_000.0, "price": 1.0}, tick=0)
        pool.price = 3.0
        sell = bot.propose([pool], tick=100, weight=0.25)[0]
        self.assertEqual(sell["side"], "sell")
        self.assertLess(sell["qty"], 10_000)
        self.assertTrue(Jev(CFG).validate(sell).approved)
        pnl = bot.fill(sell, tick=100)
        self.assertAlmostEqual(pnl, sell["qty"] * 2.0)
        self.assertAlmostEqual(bot.positions["T"].qty, 10_000 - sell["qty"])

    def test_launchpad_keeps_held_pools(self):
        pad = SimulatedLaunchpad(seed=3, launch_prob=1.0, max_pools=5)
        first = pad.tick()[0].token
        for _ in range(20):
            pad.tick(keep={first})
        self.assertIn(first, pad.pools)
        self.assertLessEqual(len(pad.pools), 6)


class VaultTests(unittest.TestCase):
    def test_secrets_locked_and_redacted(self):
        vault = Vault("paper", env={"SOLANA_PRIVATE_KEY": "super-secret-value"})
        self.assertNotIn("super-secret-value", repr(vault))
        self.assertNotIn("super-secret-value", json.dumps(vault.status()))
        self.assertEqual(vault.status()["loaded"], ["SOLANA_PRIVATE_KEY"])
        with self.assertRaises(VaultLocked):
            vault.get("SOLANA_PRIVATE_KEY")


class RingTests(unittest.TestCase):
    def run_ring(self, **env):
        out = io.StringIO()
        r = ring.run({"TICKS": "300", "TICK_SECONDS": "0", **env}, stream=out)
        return r, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_ring_runs_all_bots_and_strategies(self):
        r, events = self.run_ring()
        kinds = Counter(e["type"] for e in events)
        self.assertEqual(kinds["tick"], 300)
        self.assertEqual(kinds["heatmap"], 300)
        traded = {e["strategy"] for e in events if e["type"] == "trade"}
        self.assertEqual(traded, set(ring.STRATEGIES))
        heat = [e for e in events if e["type"] == "heatmap"][-1]["bots"]
        self.assertEqual(set(heat), set(RING_BOTS))
        self.assertTrue(all(-1 <= v <= 1 for v in heat.values()))
        self.assertGreater(r.jev.counts["approved"], 0)
        self.assertEqual(r.beta.overruns, 0)  # every loop well under the 1.5s target
        self.assertEqual(events[0]["vault"]["locked"], True)

    def test_every_executed_leg_passed_jev(self):
        r, events = self.run_ring()
        rejected_codes = Counter(c for e in events if e["type"] == "jev" for c in e["codes"])
        # Only depth rejections remain: bots size under the slippage and trade limits themselves.
        self.assertTrue(set(rejected_codes) <= {"insufficient_depth"}, rejected_codes)

    def test_self_adaptive_learning_updates_weights(self):
        r, events = self.run_ring(LEARNING_INTERVAL_SECONDS="0")
        brain = [e for e in events if e["type"] == "brain"]
        self.assertEqual(len(brain), 300)
        self.assertAlmostEqual(sum(brain[-1]["weights"].values()), 1.0, places=2)
        self.assertNotEqual(brain[-1]["weights"], {s: 0.25 for s in ring.STRATEGIES})

    def test_drawdown_halts_new_risk(self):
        out = io.StringIO()
        r = ring.Ring(CFG, ring.EventLog("ring", None, out))
        for _ in range(50):
            r.step()
        self.assertFalse(r.halted)
        r.pnl["cross_book_arb"] -= ring.CAPITAL_USDC * 0.06  # simulate a 6% loss (limit is 5%)
        out.seek(0)
        out.truncate()
        for _ in range(50):
            r.step()
        self.assertTrue(r.halted)
        trades = [json.loads(line) for line in out.getvalue().splitlines() if '"type":"trade"' in line]
        self.assertTrue(trades)  # open positions are still being exited...
        self.assertTrue(all(t["bot"] == "crypto_predictor" and t["side"] == "sell" for t in trades))  # ...nothing new

    def test_live_mode_refused(self):
        with self.assertRaises(SystemExit):
            ring.run({"MODE": "live", "TICKS": "1"}, stream=io.StringIO())
        with self.assertRaises(SystemExit):
            engine.run({"MODE": "live", "TICKS": "1"}, stream=io.StringIO())


class CapitalTests(unittest.TestCase):
    def test_ring_never_spends_more_than_it_has(self):
        out = io.StringIO()
        r = ring.run({"TICKS": "900", "TICK_SECONDS": "0"}, stream=out)
        ticks = [json.loads(line) for line in out.getvalue().splitlines() if '"type":"tick"' in line]
        self.assertTrue(all(t["free_capital"] >= -1e-6 for t in ticks))
        self.assertGreater(r.capital_rejects, 0)  # the sim offers more trades than capital allows
        self.assertTrue(any('"type":"settle"' in line for line in out.getvalue().splitlines()))

    def test_arbitrage_pnl_is_realised_only_at_resolution(self):
        r = ring.Ring(CFG, ring.EventLog("ring", None, io.StringIO()))
        r.locked.append((r.ticks + 3, 50_000.0, 1_234.0, "cross_book_arb"))
        before = r.pnl["cross_book_arb"]
        r._settle()
        self.assertEqual(r.pnl["cross_book_arb"], before)
        self.assertLess(r.free_capital(), ring.CAPITAL_USDC + sum(r.pnl.values()))
        r.ticks += 3
        r._settle()
        self.assertEqual(r.pnl["cross_book_arb"], before + 1_234.0)
        self.assertEqual(r.locked, [])

    def test_capital_check_blocks_oversized_trade(self):
        r = ring.Ring(CFG, ring.EventLog("ring", None, io.StringIO()))
        self.assertFalse(r._reject_for_capital("cross_book_arb", 10_000))
        self.assertTrue(r._reject_for_capital("cross_book_arb", ring.CAPITAL_USDC * 2))


class LogRotationTests(unittest.TestCase):
    def test_event_log_rotates_and_dashboard_follows(self):
        from trading.dashboard import EventHub
        from trading.events import EventLog
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events-0.jsonl"
            log = EventLog(0, path, stream=io.StringIO(), max_bytes=2_000)
            hub = EventHub(Path(tmp))
            log.emit("start", agents=5)
            hub.poll_files()
            for i in range(200):
                log.emit("tick", tick=i, pad="x" * 50)
                if i % 10 == 0:
                    hub.poll_files()
            hub.poll_files()
            log.close()
            self.assertLessEqual(path.stat().st_size, 2_000)
            self.assertTrue((Path(tmp) / "events-0.jsonl.1").exists())
            self.assertEqual(len(list(Path(tmp).iterdir())), 2)  # only current + one backup
            self.assertEqual(hub.shards["0"]["tick"]["tick"], 199)  # kept following after rotations


class EngineDualBrainTests(unittest.TestCase):
    def test_engine_orders_go_through_jev_and_alpha_learns(self):
        out = io.StringIO()
        desk = engine.run({"AGENT_COUNT": "750", "TICKS": "60", "TICK_SECONDS": "0",
                           "LEARNING_INTERVAL_SECONDS": "0"}, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        orders = sum(1 for e in events if e["type"] == "order")
        self.assertGreater(orders, 0)
        self.assertEqual(desk.beta.jev.counts["approved"], orders)
        self.assertEqual(desk.risk.limits.max_drawdown, 0.05)
        self.assertTrue(any(e["type"] == "brain" for e in events))
        last_tick = [e for e in events if e["type"] == "tick"][-1]
        self.assertIn("jev", last_tick)
        self.assertIn("loop_ms", last_tick)


if __name__ == "__main__":
    unittest.main()
