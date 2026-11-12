"""The reading room: one shared read of every symbol's news and chart, which every trader checks before a trade.

Two readers, one job:

- **News**: each headline's *text* ("NVDA: Inflation above forecast") is read for which way it pushes the
  stock. The built-in reader understands the wire's wording with a small finance lexicon; it never peeks
  at the simulator's hidden event codes. With Claude switched on, Claude reads each new headline text
  and says bullish or bearish, how sure it is, and why. Every wording is read once and remembered.
- **Chart**: the price history is read on two time frames (about 20 and 60 bars). A chart is "up" only
  when both frames agree, and how far price has moved against its own noise sets how sure the read is.
  With Claude switched on, Claude also reads a compact chart of every symbol every few minutes and its
  read is blended in.

The room turns both into one view per symbol: a direction from -1 (down) to 1 (up). Traders use it as a
filter (trading/agents.py `Agent._confirm`): no new position against a confident view, and practice trades
open the way the view points. That is what lifts the win rate: fewer trades fighting the market's real lean.

Claude runs on a background thread, so a slow or failed call never holds up a tick; until Claude answers,
the built-in reads stand. Without ANTHROPIC_API_KEY (or the `anthropic` package) only the built-in reads
run. Paper only: nothing here can place an order.
"""

import json
import math
import os
import re
import threading
from collections import deque
from dataclasses import dataclass

CHART_BARS = 120
FAST, SLOW = 20, 60
NEWS_KEEP = 0.97  # per tick: how long a headline keeps moving the view
CHART_SHARE = 0.6  # the view: 60% chart, 40% news
CONFIDENT = 0.25  # a view at least this strong blocks trades against it
CLAUDE_CHART_EVERY = 1800  # ticks between Claude's chart reads, all symbols in one call (15 min at 0.5 s ticks)
CLAUDE_CHART_SHARE = 0.5  # how much of the chart read is Claude's, when Claude has read it
DEFAULT_MODEL = "claude-opus-5-5"

# The built-in reader's finance lexicon: how the wire's phrases push a stock market. Positive is bullish.
PHRASES = {
    "too close to call": -0.6, "clear majority": 0.4, "tariffs": -0.8, "trade deal": 0.7, "sanctions": -0.5,
    "stimulus": 0.6, "escalates": -0.9, "ceasefire": 0.6, "tax cut": 0.5, "crackdown": -0.5,
}
# Data releases: the sign says whether a beat is good for stocks (hot inflation is bad, strong jobs good).
RELEASES = {
    "inflation": -0.7, "rate decision": -0.9, "jobs": 0.6, "gdp": 0.7, "unemployment": -0.6, "pmi": 0.5,
    "consumer confidence": 0.4, "retail sales": 0.4,
}


@dataclass
class View:
    direction: float = 0.0  # -1 down .. 1 up
    chart: float = 0.0
    news: float = 0.0
    claude_chart: float | None = None
    why: str = ""

    @property
    def confident(self) -> bool:
        return abs(self.direction) >= CONFIDENT


def read_headline(text: str) -> float | None:
    """The built-in news reader: -1 bearish .. 1 bullish from the headline's words, None if it can't tell."""
    t = text.split(":", 1)[-1].lower()
    for phrase, push in PHRASES.items():
        if phrase in t:
            return push
    for release, push in RELEASES.items():
        if release in t:
            if "above" in t or "beat" in t or "hike" in t:
                return push
            if "below" in t or "miss" in t or "cut" in t:
                return -push
    return None


def read_chart(prices: list[float]) -> float:
    """The built-in chart reader: -1 clear downtrend .. 1 clear uptrend, 0 when the time frames disagree."""
    if len(prices) < SLOW + 1:
        return 0.0
    rets = [math.log(b / a) for a, b in zip(prices[-SLOW - 1:], prices[-SLOW:])]
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 or 1e-9
    fast = math.log(prices[-1] / prices[-FAST - 1]) / (vol * FAST ** 0.5)
    slow = math.log(prices[-1] / prices[-SLOW - 1]) / (vol * SLOW ** 0.5)
    if fast * slow <= 0:
        return 0.0  # the frames disagree: no clear trend
    return math.copysign(min(1.0, (abs(fast) + abs(slow)) / 4), fast)


class ReadingRoom:
    def __init__(self, symbols: list[str], claude=None):
        self.symbols = list(symbols)
        self.claude = claude  # a ClaudeReader, or None for the built-in reads only
        self.history = {s: deque(maxlen=CHART_BARS) for s in self.symbols}
        self.news = {s: 0.0 for s in self.symbols}
        self.views = {s: View() for s in self.symbols}
        self.ticks = 0
        self.read = {"headlines": 0, "by_claude": 0, "unread": 0, "claude_charts": 0}

    def update(self, prices: dict[str, float], headlines: list[dict] | None = None) -> dict[str, View]:
        self.ticks += 1
        for s in self.symbols:
            self.news[s] *= NEWS_KEEP
            if s in prices:
                self.history[s].append(prices[s])
        for h in headlines or ():
            s = h.get("symbol")
            if s not in self.news:
                continue
            push, by_claude = self._read_news(h["text"])
            self.read["headlines"] += 1
            if push is None:
                self.read["unread"] += 1
                continue
            self.read["by_claude"] += by_claude
            self.news[s] = max(-1.0, min(1.0, self.news[s] + push))
        if self.claude and self.ticks % CLAUDE_CHART_EVERY == 0:
            self.claude.request_charts({s: list(self.history[s]) for s in self.symbols})
        claude_charts = self.claude.charts() if self.claude else {}
        for s in self.symbols:
            v = self.views[s]
            v.chart = read_chart(list(self.history[s]))
            v.news = self.news[s]
            chart = v.chart
            c = claude_charts.get(s)
            if c is not None:
                v.claude_chart = c["direction"]
                v.why = " ".join(x for x in (c.get("pattern", ""), c.get("why", "")) if x and x != "none")
                chart = (1 - CLAUDE_CHART_SHARE) * chart + CLAUDE_CHART_SHARE * c["direction"]
            v.direction = max(-1.0, min(1.0, CHART_SHARE * chart + (1 - CHART_SHARE) * v.news))
        self.read["claude_charts"] = len(claude_charts)
        return self.views

    def _read_news(self, text: str) -> tuple[float | None, bool]:
        if self.claude:
            read = self.claude.headline(text)
            if read is not None:
                return read, True
        return read_headline(text), False

    def snapshot(self) -> dict:
        return {"reader": self.claude.status() if self.claude else {"claude": False},
                "read": dict(self.read),
                "views": {s: {"direction": round(v.direction, 3), "chart": round(v.chart, 3),
                              "news": round(v.news, 3),
                              "claude_chart": None if v.claude_chart is None else round(v.claude_chart, 3),
                              "why": v.why} for s, v in self.views.items()}}


# ---- Claude -------------------------------------------------------------------------------------------

NEWS_SYSTEM = (
    "You are the news desk of a paper-trading team. For each headline, say how it moves the stock or coin it "
    "names over the next hours: direction -1 (clearly bearish) to 1 (clearly bullish); the size is the strength, "
    "0 if it doesn't matter; and one short reason. Judge like an experienced macro trader: hot inflation and rate hikes are bearish, "
    "strong growth and trade deals bullish, a surprise matters more than a scheduled event.")
CHART_SYSTEM = (
    "You are the chart reader of a paper-trading team. For each symbol you get its recent closes (oldest "
    "first, sampled evenly) and its volatility. Read the chart like a seasoned technician: trend on the short "
    "and long view, higher highs and lows, breakouts, exhaustion. Give direction -1 (clear downtrend) to 1 "
    "(clear uptrend), 0 when there is no clear trend, the nearest support and resistance prices, the pattern you "
    "see (e.g. breakout, double top, range, flag, none) and one short reason. Be calibrated: most charts are "
    "noise, so most directions should be small.")

NEWS_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reads"], "properties": {
    "reads": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["id", "direction", "why"],
                                         "properties": {"id": {"type": "integer"}, "direction": {"type": "number"},
                                                        "why": {"type": "string"}}}}}}
CHART_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["reads"], "properties": {
    "reads": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                         "required": ["symbol", "direction", "support", "resistance", "pattern",
                                                      "why"],
                                         "properties": {"symbol": {"type": "string"},
                                                        "direction": {"type": "number"},
                                                        "support": {"type": "number"},
                                                        "resistance": {"type": "number"},
                                                        "pattern": {"type": "string"},
                                                        "why": {"type": "string"}}}}}}


def _sample(prices: list[float], points: int = 30) -> list[float]:
    if len(prices) <= points:
        return [round(p, 3) for p in prices]
    step = (len(prices) - 1) / (points - 1)
    return [round(prices[round(i * step)], 3) for i in range(points)]


class ClaudeReader:
    """Claude reads headline texts and charts on a background thread. Answers are cached; ticks never wait."""

    MAX_BATCH = 40

    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model
        self.cache: dict[str, float] = {}
        self.reasons: dict[str, str] = {}
        self._pending: list[str] = []
        self._charts: dict[str, dict] = {}
        self._chart_job: dict[str, list[float]] | None = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self.calls = 0
        self.errors = 0
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="claude-reader", daemon=True)
        self._thread.start()

    # Called from the tick loop: never blocks.
    def headline(self, text: str) -> float | None:
        key = text.split(":", 1)[-1].strip()  # the wording, not the ticker: "Inflation above forecast"
        with self._lock:
            if key in self.cache:
                return self.cache[key]
            if key not in self._pending:
                self._pending.append(key)
                self._wake.set()
        return None

    def request_charts(self, histories: dict[str, list[float]]) -> None:
        with self._lock:
            self._chart_job = histories
        self._wake.set()

    def charts(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._charts)

    def status(self) -> dict:
        with self._lock:
            return {"claude": True, "model": self.model, "headlines_known": len(self.cache),
                    "waiting": len(self._pending), "calls": self.calls, "errors": self.errors,
                    "last_error": self.last_error}

    # Background thread.
    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=5)
            self._wake.clear()
            with self._lock:
                batch, self._pending = self._pending[:self.MAX_BATCH], self._pending[self.MAX_BATCH:]
                charts, self._chart_job = self._chart_job, None
                if self._pending:
                    self._wake.set()
            if batch:
                self._safely(self._read_news, batch)
            if charts:
                self._safely(self._read_charts, charts)

    def _safely(self, fn, arg) -> None:
        try:
            fn(arg)
        except Exception as e:  # a failed read must never stop the desk; built-in reads stand in
            with self._lock:
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                if fn == self._read_news:
                    for key in arg:  # remember as unreadable so it isn't retried forever
                        self.cache.setdefault(key, read_headline(key) or 0.0)

    def _ask(self, system: str, content: str, schema: dict) -> dict:
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=4096, system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        with self._lock:
            self.calls += 1
        if response.stop_reason == "refusal":
            raise RuntimeError("refused")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)

    def _read_news(self, batch: list[str]) -> None:
        lines = "\n".join(f"{i}. {t}" for i, t in enumerate(batch))
        data = self._ask(NEWS_SYSTEM, f"Headlines:\n{lines}", NEWS_SCHEMA)
        with self._lock:
            for r in data.get("reads", []):
                if 0 <= r.get("id", -1) < len(batch):
                    key = batch[r["id"]]
                    self.cache[key] = max(-1.0, min(1.0, float(r["direction"])))
                    self.reasons[key] = str(r.get("why", ""))[:160]
            for key in batch:
                self.cache.setdefault(key, read_headline(key) or 0.0)

    def _read_charts(self, histories: dict[str, list[float]]) -> None:
        rows = []
        for s, prices in histories.items():
            if len(prices) < 10:
                continue
            rets = [b / a - 1 for a, b in zip(prices, prices[1:])]
            vol = (sum(r * r for r in rets) / len(rets)) ** 0.5
            rows.append({"symbol": s, "closes": _sample(prices), "bars": len(prices),
                         "volatility_per_bar": round(vol, 5)})
        if not rows:
            return
        data = self._ask(CHART_SYSTEM, json.dumps({"charts": rows}), CHART_SCHEMA)
        with self._lock:
            for r in data.get("reads", []):
                if r.get("symbol") in histories:
                    self._charts[r["symbol"]] = {"direction": max(-1.0, min(1.0, float(r["direction"]))),
                                                 "support": r.get("support"), "resistance": r.get("resistance"),
                                                 "pattern": str(r.get("pattern", ""))[:40],
                                                 "why": str(r.get("why", ""))[:160]}


def make_reader(env=os.environ) -> ClaudeReader | None:
    """Claude when switched on (READING_ROOM_CLAUDE not "off"), a key is set and the SDK is installed."""
    if env.get("READING_ROOM_CLAUDE", "on").lower() in ("off", "0", "false", "no"):
        return None
    if not env.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return ClaudeReader(anthropic.Anthropic(timeout=60.0, max_retries=2), env.get("READING_ROOM_MODEL", DEFAULT_MODEL))
