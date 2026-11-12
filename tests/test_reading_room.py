import io
import json
import math
import time
import unittest
from types import SimpleNamespace

from trading import engine, review
from trading.agents import Momentum, build_team
from trading.live_market import LiveMarket, fetch_price
from trading.news import NewsWire
from trading.reading_room import ClaudeReader, ReadingRoom, View, read_chart, read_headline


def trend(n, step):
    return [100 * math.exp(step * i + 0.001 * math.sin(i)) for i in range(n)]


class NewsReadingTests(unittest.TestCase):
    def test_built_in_reader_understands_the_words(self):
        self.assertLess(read_headline("AAPL: New tariffs announced"), 0)
        self.assertGreater(read_headline("AAPL: Trade deal signed"), 0)
        self.assertLess(read_headline("NVDA: Inflation above forecast"), 0)  # hot inflation is bad
        self.assertGreater(read_headline("NVDA: Inflation below forecast"), 0)
        self.assertGreater(read_headline("BTC: Jobs report above forecast"), 0)
        self.assertLess(read_headline("BTC: Unemployment above forecast"), 0)
        self.assertIsNone(read_headline("ETH: Something unrelated"))

    def test_reader_agrees_with_the_textbook_on_every_wire_headline(self):
        wire = NewsWire(["AAPL"], seed=3)
        checked = 0
        for t in range(3000):
            for h in wire.tick({"AAPL": 0.001 if t % 200 < 100 else -0.001}, t):
                from trading.academy import ECONOMY, POLITICS
                truth = POLITICS.get(h["event"], 0) if h["kind"] == "politics" else ECONOMY[h["event"]] * h["value"]
                self.assertEqual(read_headline(h["text"]) > 0, truth > 0, h["text"])
                checked += 1
        self.assertGreater(checked, 50)


class ChartReadingTests(unittest.TestCase):
    def test_reads_up_down_and_no_trend(self):
        self.assertGreater(read_chart(trend(120, 0.003)), 0.5)
        self.assertLess(read_chart(trend(120, -0.003)), -0.5)
        self.assertEqual(read_chart(trend(30, 0.003)), 0.0)  # not enough history
        # Up on the long view, down on the short: no clear trend.
        prices = trend(80, 0.004) + [trend(80, 0.004)[-1] * math.exp(-0.004 * i) for i in range(1, 30)]
        self.assertEqual(read_chart(prices), 0.0)

    def test_room_blends_chart_and_news(self):
        room = ReadingRoom(["AAPL"])
        for p in trend(100, 0.003):
            views = room.update({"AAPL": p})
        self.assertGreater(views["AAPL"].direction, 0.25)
        for _ in range(3):
            views = room.update({"AAPL": p}, [{"symbol": "AAPL", "text": "AAPL: Conflict escalates"}])
        self.assertLess(views["AAPL"].news, 0)
        self.assertEqual(room.read["headlines"], 3)


class FakeClaude:
    """Stands in for anthropic.Anthropic: answers in the JSON shape the reading room asks for."""

    def __init__(self):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        assert kw["output_config"]["format"]["type"] == "json_schema"
        body = kw["messages"][0]["content"]
        if body.startswith("Headlines"):
            n = len(body.splitlines()) - 1
            reads = [{"id": i, "direction": -0.9, "why": "bearish"} for i in range(n)]
        else:
            reads = [{"symbol": r["symbol"], "direction": 0.8, "support": 1.0, "resistance": 2.0,
                      "pattern": "breakout", "why": "higher highs"} for r in json.loads(body)["charts"]]
        text = json.dumps({"reads": reads})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class ClaudeReaderTests(unittest.TestCase):
    def wait(self, cond):
        deadline = time.time() + 5
        while not cond() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(cond())

    def test_claude_reads_each_wording_once_and_never_blocks(self):
        fake = FakeClaude()
        reader = ClaudeReader(fake)
        self.assertIsNone(reader.headline("AAPL: Trade deal signed"))  # not read yet: built-in stands in
        self.wait(lambda: reader.headline("MSFT: Trade deal signed") is not None)
        self.assertEqual(reader.headline("NVDA: Trade deal signed"), -0.9)  # same wording, other ticker: cached
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["model"], "claude-opus-5-5")

    def test_claude_chart_read_is_blended_in(self):
        fake = FakeClaude()
        reader = ClaudeReader(fake)
        reader.request_charts({"AAPL": trend(120, 0.0)})
        self.wait(lambda: "AAPL" in reader.charts())
        room = ReadingRoom(["AAPL"], claude=reader)
        view = room.update({"AAPL": 100.0})["AAPL"]
        self.assertEqual(view.claude_chart, 0.8)
        self.assertIn("breakout", view.why)
        self.assertGreater(view.direction, 0)

    def test_failed_call_keeps_the_desk_running(self):
        def boom(**kw):
            raise RuntimeError("network down")
        reader = ClaudeReader(SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom))))
        reader.headline("AAPL: New tariffs announced")
        self.wait(lambda: reader.errors == 1)
        self.assertLess(reader.headline("AAPL: New tariffs announced"), 0)  # built-in read remembered


class ConfirmationTests(unittest.TestCase):
    def trader(self, view):
        a = Momentum("trend-0001-momentum", "AAPL", 5)
        a.department = "trend"
        a.rules = review.starting_rules("trend")
        a.view = View(direction=view)
        a.prices.extend([100, 101, 102, 103, 104, 105])
        return a

    def test_no_new_trade_against_the_view(self):
        self.assertEqual(self.trader(-0.5)._confirm(1.0, 1.0), 0.0)
        self.assertEqual(self.trader(0.0)._confirm(1.0, 1.0), 0.0)  # trend needs real agreement
        self.assertEqual(self.trader(0.6)._confirm(1.0, 1.0), 1.0)

    def test_open_trade_closed_when_view_turns_and_rides_while_it_backs_it(self):
        a = self.trader(-0.7)
        a.position = 1.0
        self.assertEqual(a._confirm(1.0, 1.0), 0.0)
        b = self.trader(0.6)
        b.position = 1.0
        self.assertEqual(b._confirm(-0.4, -0.4), 1.0)  # signal wobbled, news and chart still back it

    def test_closed_trades_are_journaled(self):
        a = self.trader(0.6)
        a._trade(1.0)
        a.prices.append(110)
        a._trade(0.0)
        self.assertEqual(a.workspace["trade_wins"], 1)
        self.assertTrue(a.journal[0]["won"])
        self.assertGreater(a.journal[0]["agree"], 0)


class ReviewTests(unittest.TestCase):
    def agent(self, trades):
        a = Momentum("trend-0002-momentum", "AAPL", 5)
        a.department = "trend"
        a.rules = review.starting_rules("trend")
        a.journal = list(trades)
        return a

    def test_losing_against_the_view_raises_the_bar_and_writes_a_lesson(self):
        trades = [{"won": False, "move": -0.01, "agree": -0.1, "edge": 0.5}] * 5 + \
                 [{"won": True, "move": 0.01, "agree": 0.5, "edge": 0.5}] * 5
        a = self.agent(trades)
        before = a.rules["view_need"]
        lessons = review.review(a, 1)
        self.assertGreater(a.rules["view_need"], before)
        self.assertTrue(lessons)
        self.assertIn("Review 1", a.workspace["lessons"][0])
        self.assertEqual(a.journal, [])

    def test_weak_signals_losing_raises_min_edge(self):
        trades = [{"won": False, "move": -0.01, "agree": 0.5, "edge": 0.1}] * 5 + \
                 [{"won": True, "move": 0.01, "agree": 0.5, "edge": 0.9}] * 5
        a = self.agent(trades)
        review.review(a, 1)
        self.assertGreater(a.rules["min_edge"], 0)

    def test_too_few_trades_changes_nothing(self):
        a = self.agent([{"won": False, "move": -0.01, "agree": -0.5, "edge": 0.1}])
        self.assertEqual(review.review(a, 1), [])
        self.assertEqual(a.rules, review.starting_rules("trend"))

    def test_weakest_copy_the_best(self):
        team = [a for a in build_team(40, ["AAPL"]) if a.department == "trend"]
        for i, a in enumerate(team):
            a.workspace["trade_wins"], a.workspace["trade_losses"] = i, 10
            a.rules = {**a.rules, "view_need": i / 100}
        copied = review.share_best(team)
        self.assertEqual(copied["trend"], len(team) // 5)
        weakest = team[0]
        self.assertGreaterEqual(weakest.rules["view_need"], team[-len(team) // 5].rules["view_need"])
        self.assertIn("Copied", weakest.workspace["lessons"][-1])


class EngineWiringTests(unittest.TestCase):
    def test_reviews_run_every_quarter_hour_and_report(self):
        out = io.StringIO()
        desk = engine.run({"TICKS": "200", "TICK_SECONDS": "0", "AGENT_COUNT": "200", "PRACTICE_HOUR_TICKS": "80",
                           "READING_ROOM_CLAUDE": "off"}, stream=out)
        events = [json.loads(line) for line in out.getvalue().splitlines()]
        reviews = [e for e in events if e["type"] == "review"]
        self.assertEqual(len(reviews), 200 // 20)
        org = [e for e in events if e["type"] == "org"][-1]
        self.assertIn("views", org["reading_room"])
        self.assertGreater(sum(d["trade_wins"] + d["trade_losses"] for d in org["departments"].values()), 0)
        self.assertEqual(desk.review_ticks, 20)

    def test_reading_room_can_be_switched_off(self):
        desk = engine.run({"TICKS": "50", "TICK_SECONDS": "0", "AGENT_COUNT": "200", "READING_ROOM": "off"},
                          stream=io.StringIO())
        self.assertIsNone(desk.room)


class LiveMarketTests(unittest.TestCase):
    def test_parses_coinbase_and_yahoo(self):
        def get(url):
            if "coinbase" in url:
                return {"data": {"amount": "65000.5"}}
            return {"chart": {"result": [{"meta": {"regularMarketPrice": 190.25}}]}}
        self.assertEqual(fetch_price("BTC", get), 65000.5)
        self.assertEqual(fetch_price("AAPL", get), 190.25)

    def test_failed_feed_keeps_last_price(self):
        calls = {"n": 0}

        def fetch(symbol):
            calls["n"] += 1
            if calls["n"] > 2:
                raise OSError("down")
            return 100.0 + calls["n"]
        m = LiveMarket(["AAPL", "BTC"], fetch=fetch, start_thread=False)
        self.assertEqual(m.tick(), {"AAPL": 101.0, "BTC": 102.0})
        m.poll_once()
        self.assertEqual(m.tick(), {"AAPL": 101.0, "BTC": 102.0})
        self.assertEqual(m.errors, 2)
        self.assertEqual(m.drift, {})


if __name__ == "__main__":
    unittest.main()
