"""The agent team.

- Traders propose a direction for one symbol (+1 buy, -1 sell, 0 flat).
- Analysts judge the regime of one symbol and scale how much the desk trusts
  trend-following versus mean-reversion traders.
- Risk monitors watch volatility and vote to shrink overall exposure.

Agents are staffed into the departments in `org.py`. Each agent keeps its own
workspace (a private notebook of what it has seen and decided) and its own
brain (`brain.py`), which learns from the agent's own results and decides how
hard it votes, or whether it votes at all.
"""

import random
import statistics
from collections import deque
from dataclasses import dataclass

from . import academy
from .brain import AgentBrain
from .org import department_sizes

TREND = "trend"
TRADE_STEP = 0.25  # a position change at least this big counts as a practice trade
REVERSION = "reversion"


@dataclass(frozen=True)
class Vote:
    agent_id: str
    role: str  # "trader" | "analyst" | "risk"
    symbol: str | None
    value: float  # trader: -1..1 direction, analyst: -1 (ranging) .. 1 (trending), risk: 0..1 exposure


class Agent:
    role = "agent"
    kind = "agent"
    # The vote range and the "no opinion" value the brain shapes votes around.
    neutral, low, high = 0.0, -1.0, 1.0

    def __init__(self, agent_id: str, symbol: str, window: int):
        self.agent_id = agent_id
        self.symbol = symbol
        self.window = window
        self.prices: deque[float] = deque(maxlen=window + 1)
        self.position = 0.0  # virtual position, used only to score the agent
        self.pnl = 0.0
        self.department = ""  # set by build_team
        self.brain = AgentBrain(agent_id)
        self.training: academy.Training | None = None  # set by academy.enroll
        self.macro_view: float | None = None  # analysts: what the news says, -1 bearish .. 1 bullish
        # The agent's own space: nothing else writes here.
        self.workspace = {"votes_cast": 0, "sat_out": 0, "held": 0, "last_signal": None, "last_vote": None,
                          "trades_this_hour": 0, "trades_total": 0}
        self.must_practice = False  # set by the desk when the agent is behind its practice-trade quota

    def observe(self, price: float) -> Vote | None:
        if self.prices:
            self.pnl += self.position * (price - self.prices[-1])
            # The brain scores the strategy's last raw signal, even while sitting
            # out, so an agent can earn its way back in.
            if self.workspace["last_signal"] is not None:
                self.brain.learn(self.reward(self.workspace["last_signal"], price))
        self.prices.append(price)
        if len(self.prices) <= self.window:
            return None
        signal = self.decide()
        value = self.brain.shape(signal, self.neutral, self.low, self.high)
        self.workspace["last_signal"] = signal
        self.workspace["last_vote"] = value
        if value is None:
            self.workspace["sat_out"] += 1
            if self.role == "trader":
                # Benched traders still keep up their practice trades (scored, never sent to the desk).
                self._trade(self._practice(signal) if self.must_practice else 0.0)
            return None
        if self.role == "trader" and not self.must_practice and not academy.should_act(
                self.training, abs(signal), self.brain.weight):
            # Trained decision makers hold when the call's edge doesn't pay for the trade.
            value = 0.0
            self.workspace["held"] += 1
            self.workspace["last_vote"] = value
        if self.role == "trader" and self.must_practice:
            value = self._practice(value)
            self.workspace["last_vote"] = value
        self.workspace["votes_cast"] += 1
        if self.role == "trader":
            self._trade(value)
        return Vote(self.agent_id, self.role, self.symbol, value)

    def _practice(self, value: float) -> float:
        """Behind on its practice quota: make sure this tick is a trade (enter, flip or take profit)."""
        if abs(value - self.position) >= TRADE_STEP:
            return value
        if self.position:
            return 0.0  # close the open practice position
        return 1.0 if value >= 0 else -1.0  # open a new one in the signal's direction

    def _trade(self, position: float) -> None:
        if abs(position - self.position) >= TRADE_STEP:
            self.workspace["trades_this_hour"] += 1
            self.workspace["trades_total"] += 1
        self.position = position

    def hear(self, headline: dict) -> None:
        """Read a news headline with what the agent learned at the academy (analysts only)."""
        read = academy.read_headline(self.training, headline)
        if read is not None:
            self.macro_view = max(-1.0, min(1.0, 0.5 * (self.macro_view or 0.0) + read))

    def forget_news(self, keep: float = 0.95) -> None:
        if self.macro_view is not None:
            self.macro_view *= keep

    def reward(self, signal: float, price: float) -> float:
        """How right the last signal was, -1..1. Traders: did the price move their way?"""
        move = price - self.prices[-1]
        return signal if move > 0 else -signal if move < 0 else 0.0

    def decide(self) -> float:
        raise NotImplementedError


class Trader(Agent):
    role = "trader"
    style = TREND


class Momentum(Trader):
    kind = "momentum"

    def decide(self):
        change = self.prices[-1] / self.prices[0] - 1
        return max(-1.0, min(1.0, change * 50))


class MovingAverageCross(Trader):
    kind = "ma_cross"

    def decide(self):
        prices = list(self.prices)
        fast = statistics.fmean(prices[-max(2, self.window // 4):])
        slow = statistics.fmean(prices)
        return 1.0 if fast > slow else -1.0


class Breakout(Trader):
    kind = "breakout"

    def decide(self):
        prior = list(self.prices)[:-1]
        if self.prices[-1] > max(prior):
            return 1.0
        if self.prices[-1] < min(prior):
            return -1.0
        return 0.0


class MeanReversion(Trader):
    kind = "mean_reversion"
    style = REVERSION

    def decide(self):
        prices = list(self.prices)
        stdev = statistics.pstdev(prices)
        if stdev == 0:
            return 0.0
        z = (prices[-1] - statistics.fmean(prices)) / stdev
        return max(-1.0, min(1.0, -z / 2))


class RegimeAnalyst(Agent):
    """Efficiency ratio: net move / total path. Near 1 = trending, near 0 = choppy."""

    role = "analyst"
    kind = "regime_analyst"

    def reward(self, signal, price):
        # A "trending" call is right when the next move continues the last one.
        if len(self.prices) < 2:
            return 0.0
        continued = (price - self.prices[-1]) * (self.prices[-1] - self.prices[-2])
        return signal if continued > 0 else -signal if continued < 0 else 0.0

    def decide(self):
        prices = list(self.prices)
        path = sum(abs(b - a) for a, b in zip(prices, prices[1:]))
        if path == 0:
            return 0.0
        efficiency = abs(prices[-1] - prices[0]) / path
        return max(-1.0, min(1.0, efficiency * 4 - 1))


class VolatilityMonitor(Agent):
    """Votes for less exposure as realised volatility rises above its limit."""

    role = "risk"
    kind = "volatility_monitor"
    neutral, low, high = 1.0, 0.0, 1.0

    def reward(self, signal, price):
        # Cutting size is right before a big move; full size is right in calm markets.
        big = abs(price / self.prices[-1] - 1) > self.vol_limit
        return 1 - 2 * signal if big else 2 * signal - 1

    def __init__(self, agent_id, symbol, window, vol_limit):
        super().__init__(agent_id, symbol, window)
        self.vol_limit = vol_limit

    def decide(self):
        prices = list(self.prices)
        returns = [b / a - 1 for a, b in zip(prices, prices[1:])]
        vol = statistics.pstdev(returns)
        return max(0.0, min(1.0, self.vol_limit / vol)) if vol else 1.0


TREND_TYPES = (Momentum, MovingAverageCross, Breakout)
TRADER_TYPES = TREND_TYPES + (MeanReversion,)


def build_team(count: int, symbols: list[str], seed: int = 0) -> list[Agent]:
    """Build `count` agents split evenly across the departments, spread across symbols, with varied parameters.

    The agents come out untrained; `academy.enroll` trains each one for its department's job.
    """
    rng = random.Random(seed)
    sizes = department_sizes(count)

    def staff(department: str, make) -> list[Agent]:
        members = []
        for i in range(sizes[department]):
            agent = make(i, symbols[i % len(symbols)])
            agent.department = department
            members.append(agent)
        return members

    def trend(i, symbol):
        cls = TREND_TYPES[i % len(TREND_TYPES)]
        return cls(f"trend-{i:04d}-{cls.kind}", symbol, rng.randint(5, 60))

    return (
        staff("trend", trend)
        + staff("reversion", lambda i, s: MeanReversion(f"reversion-{i:04d}", s, rng.randint(5, 60)))
        + staff("research", lambda i, s: RegimeAnalyst(f"research-{i:04d}", s, rng.randint(10, 80)))
        + staff("risk", lambda i, s: VolatilityMonitor(f"risk-{i:04d}", s, rng.randint(10, 40),
                                                       rng.uniform(0.008, 0.02)))
    )
