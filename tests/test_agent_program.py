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
    """Stands in for anthropic.Anthropic: looks at one symbol and the worst trend traders, then gives advice."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        assert {t["name"] for t in kw["tools"]} >= {"symbol_trades", "traders", "views", "give_advice"}
        turn = len(self.calls)
        if turn == 1:
            blocks = [SimpleNamespace(type="tool_use", id="a", name="symbol_trades", input={"symbol": "aapl"}),
                      SimpleNamespace(type="tool_use", id="b", name="traders",
                                      input={"department": "trend", "which": "worst"})]
        else:
            blocks = [SimpleNamespace(type="tool_use", id="c", name="give_advice", input={
                "summary": "Trend traders fought the chart.", "departments": [
                    {"department": "trend", "view_need_change": 0.5, "min_edge_change": 0.0,
                     "exit_against_change": -0.05, "why": "most losers were opened against the chart"}]})]
        return SimpleNamespace(stop_reason="tool_use", content=blocks)


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
            self.assertIn("departments", json.loads(fake.calls[0]["messages"][0]["content"]))
            # The answer is applied on a later tick.
            time.sleep(0.2)
            desk.step(desk.last_prices)
            self.assertIsNotNone(desk.last_coach)
            self.assertEqual([x["tool"] for x in desk.last_coach["looked_at"]], ["symbol_trades", "traders"])
            # The second call carried the tool results back to Claude.
            results = fake.calls[1]["messages"][-1]["content"]
            self.assertEqual({r["tool_use_id"] for r in results}, {"a", "b"})
        finally:
            eng.make_coach = original

    def test_coach_change_that_hurt_is_undone(self):
        from trading.desk import Desk
        team = [trader(i, 0, 0) for i in range(3)]
        for a in team:
            a.rules = review.starting_rules("trend")
        desk = SimpleNamespace(agents=team, coach_trial={"trend": {"rate": 0.6, "changes": {"view_need": 0.1}}},
                               last_coach={})
        for a in team:
            a.rules["view_need"] += 0.1
        out = Desk.check_coach_trial(desk, {"departments": {"trend": {"win_rate": 0.4}}})
        self.assertFalse(out["trend"]["kept"])
        self.assertAlmostEqual(team[0].rules["view_need"], review.TREND_VIEW_NEED)

    def test_orders_say_why(self):
        out = io.StringIO()
        engine.run({"AGENT_COUNT": "80", "TICKS": "300", "TICK_SECONDS": "0"}, stream=out)
        orders = [json.loads(x) for x in out.getvalue().splitlines() if '"order"' in x]
        orders = [o for o in orders if "why" in o]
        self.assertTrue(orders)
        self.assertIn("trend", orders[0]["why"])


class CloudDiskTests(unittest.TestCase):
    def test_unwritable_events_folder_falls_back(self):
        from trading import cloud
        with tempfile.TemporaryDirectory() as d:
            blocker = Path(d) / "file"
            blocker.write_text("x")
            got = cloud.writable_dir(blocker / "events", Path(d) / "fallback")
            self.assertEqual(got, Path(d) / "fallback")
            self.assertEqual(cloud.writable_dir(Path(d) / "ok"), Path(d) / "ok")


class JarvisTests(unittest.TestCase):
    def test_jarvis_explains_memory_and_coach(self):
        from trading.jarvis import BuiltinBrain
        snap = {"practice": {"memory": {"on": True, "remembered": 1400}, "coach_status": {"claude": False},
                             "managers": {"actions": [{"manager": "Atlas", "did": "benched 12 traders for an hour"}]}}}
        answer = BuiltinBrain().answer("what does the coach do and do they remember?", snap)
        self.assertIn("1,400 agents remembered", answer)
        self.assertIn("Atlas benched 12", answer)


if __name__ == "__main__":
    unittest.main()
