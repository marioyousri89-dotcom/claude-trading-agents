"""The pieces modelled on Claude's agent program: hooks, desk journal, managers, trials, memory, coach."""

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from trading import coach, engine, hooks, memory, review
from trading.agents import Momentum
from trading.managers import Managers
from trading.reading_room import View


class HookTests(unittest.TestCase):
    def test_blocks_new_risk_against_a_confident_view(self):
        c = hooks.OrderCheck("AAPL", 10, 0, 100, view=View(direction=-0.5))
        qty, notes = hooks.run_hooks(c)
        self.assertEqual(qty, 0)
        self.assertIn("against_the_room", notes[0])

    def test_never_blocks_getting_out(self):
        c = hooks.OrderCheck("AAPL", -10, 10, 100, view=View(direction=0.9), paused=True, streak=5)
        self.assertEqual(hooks.run_hooks(c), (-10, []))

    def test_flip_shrinks_only_the_new_side(self):
        c = hooks.OrderCheck("AAPL", -30, 10, 100, paused=True)
        qty, _ = hooks.run_hooks(c)
        self.assertEqual(qty, -20)  # closes the 10 long, opens 10 short instead of 20
        c = hooks.OrderCheck("AAPL", -30, 10, 100, view=View(direction=0.9))
        self.assertEqual(hooks.run_hooks(c)[0], -10)  # blocked: only closes

    def test_losing_streak_halves(self):
        qty, notes = hooks.run_hooks(hooks.OrderCheck("AAPL", 10, 0, 100, streak=2))
        self.assertEqual(qty, 5)
        self.assertTrue(notes)

    def test_journal_records_round_trips_and_streaks(self):
        j = hooks.DeskJournal()
        j.fill("X", 0, 10, 100, 1)
        j.fill("X", 10, 10, 110, 2)  # average entry 105
        closed = j.fill("X", 20, -20, 100, 3)
        self.assertFalse(closed["won"])
        j.fill("X", 0, -5, 100, 4)
        j.fill("X", -5, 5, 105, 5)
        self.assertEqual(j.streak("X"), 2)
        self.assertEqual(j.summary(0)["X"]["trades"], 2)


def trader(i, wins, losses, dept="trend"):
    a = Momentum(f"t{i}", "AAPL", 5)
    a.department = dept
    a.workspace["trade_wins"], a.workspace["trade_losses"] = wins, losses
    return a


class ManagerTests(unittest.TestCase):
    def test_atlas_benches_losers_but_keeps_most_of_the_department(self):
        team = [trader(i, 1, 9) for i in range(10)] + [trader(10 + i, 8, 2) for i in range(10)]
        acted = Managers().hour(team, hooks.DeskJournal(), 0, 1)
        self.assertEqual(acted["benched"], 4)  # at most 20% of 20
        self.assertEqual(acted["actions"][0]["manager"], "Atlas")

    def test_sentinel_pauses_a_losing_symbol(self):
        j = hooks.DeskJournal()
        for t in range(3):
            j.fill("TSLA", 0, 1, 100, t * 2 + 1)
            j.fill("TSLA", 1, -1, 99, t * 2 + 2)
        m = Managers()
        m.hour([], j, 0, 1)
        self.assertEqual(m.paused, {"TSLA"})


class TrialTests(unittest.TestCase):
    def test_a_change_that_hurt_is_undone(self):
        a = trader(0, 0, 0)
        a.rules = review.starting_rules("trend")
        # Review 1: losers all fought the view -> raises view_need and puts the change on trial.
        a.journal = [{"won": False, "move": -0.01, "agree": -0.5, "edge": 0.5}] * 3 + \
                    [{"won": True, "move": 0.01, "agree": 0.5, "edge": 0.5}] * 3
        review.review(a, 1)
        self.assertIsNotNone(a.trial)
        raised = a.rules["view_need"]
        # Review 2: the win rate fell -> undone.
        a.journal = [{"won": False, "move": -0.01, "agree": 0.5, "edge": 0.5}] * 5 + \
                    [{"won": True, "move": 0.01, "agree": 0.5, "edge": 0.5}]
        lessons = review.review(a, 2)
        self.assertLess(a.rules["view_need"], raised)
        self.assertIn("undone", lessons[0])
        self.assertEqual(a.workspace["undone"], 1)


class MemoryTests(unittest.TestCase):
    def test_learning_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"AGENT_COUNT": "80", "TICKS": "300", "TICK_SECONDS": "0", "EVENTS_DIR": d,
                   "PRACTICE_HOUR_TICKS": "100"}
            first = engine.run(env, stream=io.StringIO())
            self.assertTrue((Path(d) / "memory-0.json").exists())
            out = io.StringIO()
            second = engine.run({**env, "TICKS": "1"}, stream=out)
            start = json.loads(out.getvalue().splitlines()[0])
            self.assertEqual(start["remembered"], 80)
            a, b = first.agents[0], second.agents[0]
            self.assertGreaterEqual(b.brain.experience, a.brain.experience)
            self.assertGreaterEqual(second.hours, first.hours)

    def test_damaged_memory_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "memory-0.json").write_text("{not json")
            out = io.StringIO()
            engine.run({"AGENT_COUNT": "80", "TICKS": "2", "TICK_SECONDS": "0", "EVENTS_DIR": d}, stream=out)
            self.assertEqual(json.loads(out.getvalue().splitlines()[0])["remembered"], 0)

    def test_memory_off(self):
        with tempfile.TemporaryDirectory() as d:
            engine.run({"AGENT_COUNT": "80", "TICKS": "2", "TICK_SECONDS": "0", "EVENTS_DIR": d, "MEMORY": "off"},
                       stream=io.StringIO())
            self.assertFalse((Path(d) / "memory-0.json").exists())


class FakeCoachClient:
    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(json.loads(kw["messages"][0]["content"]))
        text = json.dumps({"summary": "Trend traders fought the chart.", "departments": [
            {"department": "trend", "view_need_change": 0.5, "min_edge_change": 0.0, "exit_against_change": -0.05,
             "why": "most losers were opened against the chart"}]})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class CoachTests(unittest.TestCase):
    def test_changes_are_bounded_and_explained(self):
        team = [trader(i, 0, 0) for i in range(3)] + [trader(9, 0, 0, dept="reversion")]
        for a in team:
            a.rules = review.starting_rules(a.department)
        changed = coach.apply(team, {"departments": [
            {"department": "trend", "view_need_change": 0.5, "min_edge_change": 0, "exit_against_change": 0,
             "why": "x"}]})
        self.assertEqual(changed["trend"]["changes"], {"view_need": coach.MAX_CHANGE})
        self.assertAlmostEqual(team[0].rules["view_need"], review.TREND_VIEW_NEED + coach.MAX_CHANGE)
        self.assertEqual(team[3].rules, review.starting_rules("reversion"))
        self.assertIn("Coach (Claude)", team[0].workspace["lessons"][-1])

    def test_desk_asks_the_coach_hourly_and_applies_the_answer(self):
        fake = FakeCoachClient()
        from trading import engine as eng
        original = eng.make_coach
        eng.make_coach = lambda env: coach.Coach(fake)
        try:
            out = io.StringIO()
            desk = engine.run({"AGENT_COUNT": "80", "TICKS": "120", "TICK_SECONDS": "0",
                               "PRACTICE_HOUR_TICKS": "100"}, stream=out)
            deadline = time.time() + 5
            while not fake.calls and time.time() < deadline:
                time.sleep(0.02)
            self.assertTrue(fake.calls)
            self.assertIn("departments", fake.calls[0])
            # The answer is applied on a later tick.
            time.sleep(0.2)
            desk.step(desk.last_prices)
            self.assertIsNotNone(desk.last_coach)
        finally:
            eng.make_coach = original

    def test_orders_say_why(self):
        out = io.StringIO()
        engine.run({"AGENT_COUNT": "80", "TICKS": "300", "TICK_SECONDS": "0"}, stream=out)
        orders = [json.loads(x) for x in out.getvalue().splitlines() if '"order"' in x]
        orders = [o for o in orders if "why" in o]
        self.assertTrue(orders)
        self.assertIn("trend", orders[0]["why"])


if __name__ == "__main__":
    unittest.main()
