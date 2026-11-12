"""The coordinator: turns hundreds of agent votes into one risk-checked order per symbol."""

from collections import defaultdict, deque

from . import academy, review

from .agents import REVERSION, Agent, Trader
from .brains import BrainAlpha, BrainBeta
from .broker import PaperBroker
from .events import EventLog
from .org import manager_reports, summarize
from .risk import RiskManager

SIGNAL_THRESHOLD = 0.15
LEADERBOARD_EVERY = 20
ORG_EVERY = 5  # how often the managers report their departments
MACRO_WEIGHT = 0.3  # how far the analysts' read of the news tilts the desk's call
LATEST_NEWS = 8
MIN_PRACTICE_TRADES = 20  # every strategy trader makes at least this many practice trades per hour


class Desk:
    def __init__(self, agents: list[Agent], broker: PaperBroker, risk: RiskManager, log: EventLog,
                 beta: BrainBeta | None = None, alpha: BrainAlpha | None = None, hour_ticks: int | None = None,
                 room=None, reviews: bool = True):
        self.agents = agents
        self.broker = broker
        self.risk = risk
        self.log = log
        self.beta = beta
        # Brain Alpha learns how far to trust each trader style ("trend" / "reversion").
        self.alpha = alpha
        self.ticks = 0
        self.news_view: dict[str, float] = {}  # per symbol, last tick's analysts' news read
        self.last_prices: dict[str, float] = {}
        self.symbols = sorted({a.symbol for a in agents})
        self.news: deque[dict] = deque(maxlen=LATEST_NEWS)
        # A practice hour, in ticks: the quota of practice trades and the hourly upgrade run on it.
        self.hour_ticks = hour_ticks
        self.hours = 0
        self.last_hour: dict | None = None
        # The reading room (trading/reading_room.py): one news + chart view per symbol that traders confirm with.
        self.room = room
        # Every trader reviews its own trades four times a practice hour (every 15 minutes) ...
        self.review_ticks = max(1, hour_ticks // 4) if hour_ticks and reviews else None
        self.reviews = 0
        self.last_review: dict | None = None
        # ... and once a practice day the weakest copy the best.
        self.days = 0
        self.last_share: dict | None = None

    def step(self, prices: dict[str, float], headlines: list[dict] | None = None) -> None:
        self.ticks += 1
        for headline in headlines or ():
            self.news.appendleft(headline)
        self.last_prices = dict(prices)
        if self.beta:
            self.beta.begin()
        views = self.room.update(prices, headlines) if self.room else {}
        votes = defaultdict(lambda: {"trend": [], "reversion": [], "analyst": [], "risk": [], "macro": []})
        agent_votes: dict[str, float] = {}
        self._set_practice_pace()
        by_symbol = defaultdict(list)
        for headline in headlines or ():
            by_symbol[headline["symbol"]].append(headline)
        for agent in self.agents:
            if agent.symbol not in prices:
                continue
            if agent.role == "analyst":
                agent.forget_news()
                for headline in by_symbol.get(agent.symbol, ()):
                    agent.hear(headline)
                if agent.macro_view is not None:
                    votes[agent.symbol]["macro"].append(agent.macro_view)
            if agent.role == "trader":
                agent.news_view = self.news_view.get(agent.symbol)
                agent.view = views.get(agent.symbol)
            vote = agent.observe(prices[agent.symbol])
            if vote is None:
                continue
            agent_votes[agent.agent_id] = vote.value
            if isinstance(agent, Trader):
                votes[agent.symbol]["reversion" if agent.style == REVERSION else "trend"].append(vote.value)
            else:
                votes[agent.symbol][vote.role].append(vote.value)

        self.news_view = {s: sum(v["macro"]) / len(v["macro"]) for s, v in votes.items() if v["macro"]}
        risk_votes = [v for sym in votes.values() for v in sym["risk"]]
        exposure = sum(risk_votes) / len(risk_votes) if risk_votes else 1.0
        self.risk.update(prices)

        orders = 0
        for symbol in self.symbols:
            if symbol not in votes:
                continue
            score = self._score(votes[symbol]) * exposure
            if abs(score) < SIGNAL_THRESHOLD:
                score = 0.0
            target_value = score * self.risk.limits.max_position_value
            desired = target_value / prices[symbol] - self.broker.position(symbol)
            if abs(desired * prices[symbol]) < 100:  # ignore tiny rebalances
                continue
            qty, reason = self.risk.approve(symbol, desired, prices)
            if reason:
                self.log.emit("risk", symbol=symbol, wanted=round(desired, 4), allowed=round(qty, 4), reason=reason)
            if qty and self.beta:
                decision = self.beta.jev.validate(self._proposal(symbol, qty, prices[symbol]))
                if not decision.approved:
                    self.log.emit("jev", symbol=symbol, decision="rejected", codes=[decision.code], legs=1)
                    continue
            if qty:
                self.broker.fill(symbol, qty, prices[symbol])
                orders += 1
                self.log.emit("order", symbol=symbol, qty=round(qty, 4), price=round(prices[symbol], 4),
                              score=round(score, 3))

        if self.alpha and self.alpha.due():
            weights = self.alpha.learn(self.style_pnl())
            self.log.emit("brain", brain="alpha", update=self.alpha.updates,
                          weights={k: round(v, 3) for k, v in weights.items()})

        equity = self.broker.equity(prices)
        extra = {}
        if self.beta:
            loop = self.beta.end()
            extra = {"loop_ms": round(loop * 1000, 2), "loop_overruns": self.beta.overruns,
                     "jev": {"approved": self.beta.jev.counts["approved"], "rejected": self.beta.jev.counts["rejected"]}}
        self.log.emit(
            "tick", tick=self.ticks, agents=len(self.agents), orders=orders, exposure_vote=round(exposure, 3),
            equity=round(equity, 2), pnl=round(equity - self.broker.starting_cash, 2), halted=self.risk.halted,
            prices={s: round(p, 4) for s, p in prices.items()}, **extra,
        )
        if self.review_ticks and self.ticks % self.review_ticks == 0:
            self._review()
        if self.hour_ticks and self.ticks % self.hour_ticks == 0:
            self._end_hour()
            if self.review_ticks and self.hours % 24 == 0:
                self.days += 1
                self.last_share = {"day": self.days, "copied": review.share_best(self.agents)}
                self.log.emit("share", tick=self.ticks, **self.last_share)
        if self.ticks % ORG_EVERY == 0 or self.ticks == 1:
            departments = summarize(self.agents, agent_votes)
            self.log.emit("org", tick=self.ticks, departments=departments, managers=manager_reports(departments),
                          news=list(self.news), practice=self.practice_status(),
                          reading_room=self.room.snapshot() if self.room else None)
        if self.ticks % LEADERBOARD_EVERY == 0:
            self.log.emit("leaderboard", tick=self.ticks, top=self.leaderboard(5))

    def _set_practice_pace(self) -> None:
        """Flag every trader that is behind the pace for MIN_PRACTICE_TRADES this hour."""
        if not self.hour_ticks:
            return
        into = (self.ticks - 1) % self.hour_ticks + 1
        need = MIN_PRACTICE_TRADES * into / self.hour_ticks
        for agent in self.agents:
            if agent.role == "trader":
                agent.must_practice = agent.workspace["trades_this_hour"] < need

    def _end_hour(self) -> None:
        """Close the practice hour: report trades per department, then upgrade every agent."""
        self.hours += 1
        trades: dict[str, list[int]] = defaultdict(list)
        scores: dict[str, list[tuple[float, float]]] = defaultdict(list)
        back_in = 0
        for agent in self.agents:
            if agent.role == "trader":
                trades[agent.department].append(agent.workspace["trades_this_hour"])
                agent.workspace["trades_this_hour"] = 0
            benched = agent.brain.sitting_out
            result = academy.upgrade(agent, self.hours)
            if result:
                scores[agent.department].append(result)
                back_in += benched and not agent.brain.sitting_out
        self.last_hour = {
            "hour": self.hours,
            "trades": {k: {"min": min(v), "avg": round(sum(v) / len(v), 1),
                           "under_quota": sum(t < MIN_PRACTICE_TRADES for t in v)} for k, v in trades.items()},
            "upgrade": {k: {"exam_before": round(sum(b for b, _ in v) / len(v), 4),
                            "exam_after": round(sum(a for _, a in v) / len(v), 4)} for k, v in scores.items()},
            "back_in": back_in,
        }
        self.log.emit("hour", tick=self.ticks, min_trades=MIN_PRACTICE_TRADES, **self.last_hour)

    def _review(self) -> None:
        """Every trader reviews the trades it closed since its last review and learns from its losers."""
        self.reviews += 1
        changed: dict[str, int] = defaultdict(int)
        examples: list[dict] = []
        for agent in self.agents:
            if agent.role != "trader":
                continue
            lessons = review.review(agent, self.reviews)
            if lessons:
                changed[agent.department] += 1
                if len(examples) < 3:
                    examples.append({"agent": agent.agent_id, "lesson": lessons[0]})
        self.last_review = {"review": self.reviews, "learned": dict(changed), "examples": examples}
        self.log.emit("review", tick=self.ticks, **self.last_review)

    def practice_status(self) -> dict | None:
        if not self.hour_ticks:
            return None
        return {"hour": self.hours + 1, "ticks_into_hour": self.ticks % self.hour_ticks, "hour_ticks": self.hour_ticks,
                "min_trades": MIN_PRACTICE_TRADES, "last_hour": self.last_hour,
                "review_ticks": self.review_ticks, "last_review": self.last_review, "last_share": self.last_share}

    def _score(self, v: dict) -> float:
        trend = sum(v["trend"]) / len(v["trend"]) if v["trend"] else 0.0
        reversion = sum(v["reversion"]) / len(v["reversion"]) if v["reversion"] else 0.0
        # Analysts decide whose advice to trust: trending regime favours
        # trend-followers, choppy regime favours mean-reversion traders.
        regime = sum(v["analyst"]) / len(v["analyst"]) if v["analyst"] else 0.0
        trend_weight = (1 + regime) / 2
        if self.alpha:
            # Brain Alpha's learned style weights tilt the analysts' call.
            t = trend_weight * self.alpha.weights["trend"]
            r = (1 - trend_weight) * self.alpha.weights["reversion"]
            trend_weight = t / (t + r) if t + r else 0.5
        score = trend_weight * trend + (1 - trend_weight) * reversion
        # Analysts trained in politics and economics add what the news says.
        macro = sum(v["macro"]) / len(v["macro"]) if v["macro"] else 0.0
        return max(-1.0, min(1.0, score + MACRO_WEIGHT * macro))

    def style_pnl(self) -> dict[str, float]:
        pnl = {"trend": 0.0, "reversion": 0.0}
        for a in self.agents:
            if isinstance(a, Trader):
                pnl["reversion" if a.style == REVERSION else "trend"] += a.pnl
        return pnl

    @staticmethod
    def _proposal(symbol: str, qty: float, price: float) -> dict:
        notional = abs(qty) * price
        return {
            "bot": "analyst", "venue": "spot", "instrument": symbol, "side": "buy" if qty > 0 else "sell",
            "qty": abs(qty), "price": price, "strategy": "agent_consensus",
            # Liquid large-cap books: about 0.01% slippage per $2k traded.
            "expected_slippage_pct": notional / 200_000, "depth_usdc": 10_000_000.0,
        }

    def leaderboard(self, n: int) -> list[dict]:
        traders = sorted((a for a in self.agents if isinstance(a, Trader)), key=lambda a: a.pnl, reverse=True)
        return [{"agent": a.agent_id, "symbol": a.symbol, "pnl": round(a.pnl, 4), "brain": a.brain.snapshot()}
                for a in traders[:n]]
