"""The agent team.

- Traders propose a direction for one symbol (+1 buy, -1 sell, 0 flat).
- Analysts judge the regime of one symbol and scale how much the desk trusts
  trend-following versus mean-reversion traders.
- Risk monitors watch volatility and vote to shrink overall exposure.
"""

import random
import statistics
from collections import deque
from dataclasses import dataclass

TREND = "trend"
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

    def __init__(self, agent_id: str, symbol: str, window: int):
        self.agent_id = agent_id
        self.symbol = symbol
        self.window = window
        self.prices: deque[float] = deque(maxlen=window + 1)
        self.position = 0.0  # virtual position, used only to score the agent
        self.pnl = 0.0

    def observe(self, price: float) -> Vote | None:
        if self.prices:
            self.pnl += self.position * (price - self.prices[-1])
        self.prices.append(price)
        if len(self.prices) <= self.window:
            return None
        value = self.decide()
        if self.role == "trader":
            self.position = value
        return Vote(self.agent_id, self.role, self.symbol, value)

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

    def __init__(self, agent_id, symbol, window, vol_limit):
        super().__init__(agent_id, symbol, window)
        self.vol_limit = vol_limit

    def decide(self):
        prices = list(self.prices)
        returns = [b / a - 1 for a, b in zip(prices, prices[1:])]
        vol = statistics.pstdev(returns)
        return max(0.0, min(1.0, self.vol_limit / vol)) if vol else 1.0


TRADER_TYPES = (Momentum, MovingAverageCross, Breakout, MeanReversion)
TEAM_MIX = {"trader": 0.80, "analyst": 0.16, "risk": 0.04}


def build_team(count: int, symbols: list[str], seed: int = 0) -> list[Agent]:
    """Build `count` agents spread evenly across symbols with varied parameters."""
    rng = random.Random(seed)
    n_analysts = round(count * TEAM_MIX["analyst"])
    n_risk = max(1, round(count * TEAM_MIX["risk"])) if count >= 3 else 0
    n_traders = count - n_analysts - n_risk
    team: list[Agent] = []
    for i in range(n_traders):
        cls = TRADER_TYPES[i % len(TRADER_TYPES)]
        team.append(cls(f"trader-{i:04d}-{cls.kind}", symbols[i % len(symbols)], rng.randint(5, 60)))
    for i in range(n_analysts):
        team.append(RegimeAnalyst(f"analyst-{i:04d}", symbols[i % len(symbols)], rng.randint(10, 80)))
    for i in range(n_risk):
        team.append(
            VolatilityMonitor(f"risk-{i:04d}", symbols[i % len(symbols)], rng.randint(10, 40), rng.uniform(0.008, 0.02))
        )
    return team
