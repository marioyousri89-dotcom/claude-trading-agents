import io
import json
import unittest

from trading import academy, engine
from trading.agents import Momentum, RegimeAnalyst, build_team
from trading.news import NewsWire


class AcademyTests(unittest.TestCase):
    def test_each_department_studies_its_own_job(self):
        self.assertEqual(academy.CURRICULUM["research"][:2], ("politics", "economy"))
        for dept in ("trend", "reversion"):
            self.assertEqual(academy.CURRICULUM[dept], ("reading_numbers", "decisions", "trading"))
        self.assertEqual(academy.CURRICULUM["risk"], ("volatility", "drawdown"))
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
