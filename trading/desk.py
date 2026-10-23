"""The coordinator: turns hundreds of agent votes into one risk-checked order per symbol."""

from collections import defaultdict

from .agents import REVERSION, Agent, Trader
from .brains import BrainAlpha, BrainBeta
from .broker import PaperBroker
from .events import EventLog
from .risk import RiskManager

SIGNAL_THRESHOLD = 0.15
LEADERBOARD_EVERY = 20


class Desk:
    def __init__(self, agents: list[Agent], broker: PaperBroker, risk: RiskManager, log: EventLog,
                 beta: BrainBeta | None = None, alpha: BrainAlpha | None = None):
        self.agents = agents
        self.broker = broker
        self.risk = risk
        self.log = log
        self.beta = beta
        # Brain Alpha learns how far to trust each trader style ("trend" / "reversion").
        self.alpha = alpha
        self.ticks = 0
        self.last_prices: dict[str, float] = {}
        self.symbols = sorted({a.symbol for a in agents})

    def step(self, prices: dict[str, float]) -> None:
        self.ticks += 1
        self.last_prices = dict(prices)
        if self.beta:
            self.beta.begin()
        votes = defaultdict(lambda: {"trend": [], "reversion": [], "analyst": [], "risk": []})
        for agent in self.agents:
            if agent.symbol not in prices:
                continue
            vote = agent.observe(prices[agent.symbol])
            if vote is None:
                continue
            if isinstance(agent, Trader):
                votes[agent.symbol]["reversion" if agent.style == REVERSION else "trend"].append(vote.value)
            else:
                votes[agent.symbol][vote.role].append(vote.value)

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
            **extra,
        )
        if self.ticks % LEADERBOARD_EVERY == 0:
            self.log.emit("leaderboard", tick=self.ticks, top=self.leaderboard(5))

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
        return trend_weight * trend + (1 - trend_weight) * reversion

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
        return [{"agent": a.agent_id, "symbol": a.symbol, "pnl": round(a.pnl, 4)} for a in traders[:n]]
