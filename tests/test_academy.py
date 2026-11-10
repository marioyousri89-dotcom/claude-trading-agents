import io
import json
import unittest

from trading import academy, engine, masters
from trading.agents import Momentum, RegimeAnalyst, build_team
from trading.news import NewsWire


class AcademyTests(unittest.TestCase):
    def test_each_department_studies_its_own_job(self):
        self.assertEqual(academy.CURRICULUM["research"][:2], ("politics", "economy"))
        for dept in ("trend", "reversion"):
            self.assertEqual(academy.CURRICULUM[dept][:3], ("reading_numbers", "decisions", "trading"))
        self.assertEqual(academy.CURRICULUM["risk"][:2], ("volatility", "drawdown"))
        t = academy.train("research-0007", "research")
        self.assertEqual(set(t.results), {"politics", "economy", "regimes"})

    def test_analysts_learn_politics_and_economics(self):
        trained = [academy.train(f"research-{i:04d}", "research") for i in range(100)]
        for course in ("politics", "economy"):
            avg = sum(t.results[course].score for t in trained) / len(trained)
            self.assertGreater(avg, 0.75, course)
        # As a class they learned the textbook: war and tariffs are bearish, deals and jobs bullish.
        def mean(course, key):
            return sum(t.students[course].weights.get(key, 0.0) for t in trained) / len(trained)
        self.assertLess(mean("politics", "war_escalation"), 0)
        self.assertLess(mean("politics", "new_tariffs"), 0)
        self.assertGreater(mean("politics", "trade_deal"), 0)
        self.assertGreater(mean("economy", "jobs"), 0)
        self.assertLess(mean("economy", "unemployment"), 0)
        self.assertLess(mean("economy", "inflation") + mean("economy", "interest_rate"), 0)

    def test_decision_makers_learn_numbers_decisions_and_trading(self):
        trained = [academy.train(f"trend-{i:04d}", "trend") for i in range(100)]
        for course in ("reading_numbers", "decisions", "trading"):
            self.assertGreater(sum(t.results[course].score for t in trained) / 100, 0.75, course)
        best = max(trained, key=lambda t: t.results["decisions"].score)
        self.assertTrue(academy.should_act(best, 1.0, 1.0))  # strong call, trusted: act
        self.assertFalse(academy.should_act(best, 0.02, 1.0))  # weak call: hold, the cost eats the edge
        # Reading numbers: a clean uptrend reads as up, a downtrend as down.
        reader = max(trained, key=lambda t: t.results["reading_numbers"].score).students["reading_numbers"]
        up = academy._path_features([100 * 1.004 ** i for i in range(20)])
        down = academy._path_features([100 * 0.996 ** i for i in range(20)])
        self.assertTrue(reader.answer(up))
        self.assertFalse(reader.answer(down))

    def test_training_is_deterministic_and_individual(self):
        a, b = academy.train("risk-0001", "risk"), academy.train("risk-0002", "risk")
        academy.train.cache_clear()
        self.assertEqual(academy.train("risk-0001", "risk").transcript(), a.transcript())
        self.assertNotEqual(a.students["volatility"].rate, b.students["volatility"].rate)

    def test_enroll_sets_starting_trust_from_exams(self):
        team = build_team(1400, ["AAPL"], seed=1)
        for agent in team:
            self.assertIsNone(agent.training)
            academy.enroll(agent)
            self.assertAlmostEqual(agent.brain.weight, 0.75 + 0.5 * agent.training.average)
            self.assertFalse(agent.brain.sitting_out)
        report = academy.summarize(team)
        self.assertEqual(report["trained"], 1400)
        self.assertGreater(report["graduates"], 1200)

    def test_trained_trader_holds_weak_calls(self):
        # A trader who did well in "Making decisions".
        agent_id = next(f"trend-{i:04d}-momentum" for i in range(1000)
                        if academy.train(f"trend-{i:04d}-momentum", "trend").results["decisions"].score >= 0.95)
        trader = Momentum(agent_id, "X", 5)
        trader.department = "trend"
        academy.enroll(trader)
        for p in [100, 100.01, 100.0, 100.01, 100.0, 100.01, 100.0, 100.01]:
            vote = trader.observe(p)
        self.assertEqual(vote.value, 0.0)
        self.assertGreater(trader.workspace["held"], 0)

    def test_analyst_reads_the_news(self):
        analyst = RegimeAnalyst("research-0003", "X", 10)
        analyst.department = "research"
        academy.enroll(analyst)
        analyst.hear({"symbol": "X", "kind": "politics", "event": "war_escalation", "value": 1.0})
        self.assertLess(analyst.macro_view, 0)
        analyst.macro_view = None
        analyst.hear({"symbol": "X", "kind": "economy", "event": "jobs", "value": 2.0})
        self.assertGreater(analyst.macro_view, 0)


class MastersTests(unittest.TestCase):
    def test_ten_masters_are_taught_to_the_department_that_fits(self):
        self.assertEqual(len(masters.MASTERS), 10)
        self.assertEqual(masters.courses_for("trend"), ("livermore", "turtles", "seykota", "druckenmiller"))
        self.assertEqual(masters.courses_for("reversion"), ("soros", "simons", "buffett"))
        self.assertEqual(masters.courses_for("risk"), ("kovner", "tudor_jones", "thorp"))
        for m in masters.MASTERS:
            self.assertIn(m.course, academy.CURRICULUM[m.department])
            self.assertEqual(academy.COURSES[m.course].name, m.name)
        self.assertFalse(set(masters.BY_COURSE) & set(academy.CURRICULUM["research"]))

    def test_agents_pass_the_masters_exams_and_learn_their_rules(self):
        for dept in ("trend", "reversion", "risk"):
            trained = [academy.train(f"{dept}-{i:04d}", dept) for i in range(60)]
            for course in masters.courses_for(dept):
                self.assertGreater(sum(t.results[course].score for t in trained) / 60, 0.78, course)
        def best(dept, course):
            return max((academy.train(f"{dept}-{i:04d}", dept) for i in range(60)),
                       key=lambda t: t.results[course].score).students[course]
        livermore = best("trend", "livermore")
        self.assertTrue(livermore.answer({"breakout": 1.5, "volume": 0.8}))  # new high, heavy volume: buy
        self.assertFalse(livermore.answer({"breakout": -1.5, "volume": 0.8}))  # still below the high: wait
        seykota = best("trend", "seykota")
        self.assertFalse(seykota.answer({"pnl_r": -0.9, "trend": -1.5}))  # trend broken: cut the loss
        self.assertTrue(seykota.answer({"pnl_r": 1.5, "trend": 1.5}))  # winner, trend intact: ride it
        simons = best("reversion", "simons")
        self.assertTrue(simons.answer({"stretch": 3.5, "half_life": 0.2}))
        self.assertFalse(simons.answer({"stretch": 0.3, "half_life": 1.5}))
        kovner = best("risk", "kovner")
        self.assertTrue(kovner.answer({"stop": 2.0, "size": 2.0, "risk": 4.0}))  # 4% of capital at risk: too big
        self.assertFalse(kovner.answer({"stop": 0.5, "size": 0.4, "risk": 0.2}))
        thorp = best("risk", "thorp")
        self.assertTrue(thorp.answer({"bet": 1.8, "kelly": 0.2}))  # betting far past Kelly
        self.assertFalse(thorp.answer({"bet": 0.2, "kelly": 1.2}))


class PracticeHourTests(unittest.TestCase):
    def test_every_trader_makes_20_practice_trades_an_hour_and_everyone_upgrades(self):
        out = io.StringIO()
        desk = engine.run({"AGENT_COUNT": "1400", "TICKS": "240", "TICK_SECONDS": "0", "PRACTICE_HOUR_TICKS": "120",
                           "SYMBOLS": "AAPL,BTC"}, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        hours = [e for e in events if e["type"] == "hour"]
        self.assertEqual([h["hour"] for h in hours], [1, 2])
        for h in hours:
            self.assertEqual(set(h["trades"]), {"trend", "reversion"})
            for dept in h["trades"].values():
                self.assertGreaterEqual(dept["min"], 20)
                self.assertEqual(dept["under_quota"], 0)
            self.assertEqual(set(h["upgrade"]), {"trend", "reversion", "research", "risk"})
        self.assertTrue(all(a.training.upgrades == 2 for a in desk.agents))
        self.assertTrue(all(a.workspace["trades_total"] >= 40 for a in desk.agents if a.role == "trader"))
        org = [e for e in events if e["type"] == "org"][-1]
        self.assertEqual(org["practice"]["last_hour"]["hour"], 2)
        self.assertIn("practice trades/agent", org["managers"][0]["training"])

    def test_upgrade_retrains_and_gives_benched_graduates_another_chance(self):
        agent = RegimeAnalyst("research-0042", "X", 10)
        agent.department = "research"
        academy.enroll(agent)
        lessons = sum(r.lessons for r in agent.training.results.values())
        agent.brain.weight = 0.01  # benched
        before, after = academy.upgrade(agent, 1)
        self.assertGreater(sum(r.lessons for r in agent.training.results.values()), lessons)
        self.assertEqual(agent.training.upgrades, 1)
        if agent.training.graduated:
            self.assertFalse(agent.brain.sitting_out)
        # Each agent's upgrade changes only its own copy of the training.
        self.assertEqual(academy.train("research-0042", "research").upgrades, 0)


class NewsWireTests(unittest.TestCase):
    def test_news_mostly_points_the_way_the_market_leans(self):
        wire = NewsWire(["X"], seed=3)
        bullish = total = 0
        for t in range(3000):
            for h in wire.tick({"X": 0.01}, t):
                total += 1
                effect = (academy.POLITICS.get(h["event"]) or academy.ECONOMY[h["event"]]) * h["value"]
                bullish += effect > 0
        self.assertGreater(total, 100)
        self.assertGreater(bullish / total, 0.75)


class EngineTrainingTests(unittest.TestCase):
    def test_engine_trains_agents_and_reports_the_academy(self):
        out = io.StringIO()
        desk = engine.run({"TICKS": "60", "TICK_SECONDS": "0"}, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(events[0]["school"]["trained"], 1400)
        self.assertTrue(all(a.training is not None for a in desk.agents))
        org = [e for e in events if e["type"] == "org"][-1]
        research = org["departments"]["research"]["school"]
        self.assertEqual(set(research["courses"]), {"politics", "economy", "regimes"})
        self.assertGreater(research["avg_exam"], 0.75)
        self.assertGreater(org["departments"]["trend"]["held"], 0)
        self.assertTrue(org["news"])
        self.assertTrue(all("graduated" in m["training"] for m in org["managers"]))


if __name__ == "__main__":
    unittest.main()
