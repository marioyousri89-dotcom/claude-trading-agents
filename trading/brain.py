"""Each agent's own brain: it learns from its own results and decides on its own whether and how hard to vote.

After every tick the brain scores the agent's last call against what the market
actually did (a reward from -1 to 1) and updates one number, its trust in its
own strategy, with a multiplicative-weights rule. Its learning rate shrinks as
it gains experience, so a young agent adapts fast and a veteran is steadier.

The brain then shapes the agent's next call:
- trusted (weight above 1): it presses its signal harder, up to the vote limit
- doubtful (weight below 1): it softens its signal
- lost confidence (weight below its own sit-out level): it abstains until a
  paper-only "shadow" score of its signal earns its trust back

Every brain has its own temperament (learning speed and sit-out level), drawn
from its agent id, so no two agents learn the same way. Nothing here touches
real money: rewards come from the simulated market.
"""

import math
import random
from collections import deque

MIN_WEIGHT = 0.05
MAX_WEIGHT = 2.0
MEMORY = 50


class AgentBrain:
    def __init__(self, agent_id: str):
        rng = random.Random(agent_id)
        self.base_rate = rng.uniform(0.05, 0.25)
        self.sit_out_below = rng.uniform(0.3, 0.6)
        self.weight = 1.0
        self.experience = 0
        self.wins = 0
        self.memory: deque[float] = deque(maxlen=MEMORY)

    @property
    def learning_rate(self) -> float:
        return self.base_rate / (1 + self.experience / 500)

    @property
    def skill(self) -> float | None:
        """Share of its calls that were right, or None before its first result."""
        return self.wins / self.experience if self.experience else None

    @property
    def sitting_out(self) -> bool:
        return self.weight < self.sit_out_below

    def learn(self, reward: float) -> None:
        reward = max(-1.0, min(1.0, reward))
        self.experience += 1
        if reward > 0:
            self.wins += 1
        self.memory.append(reward)
        self.weight = max(MIN_WEIGHT, min(MAX_WEIGHT, self.weight * math.exp(self.learning_rate * reward)))

    def shape(self, signal: float, neutral: float, low: float, high: float) -> float | None:
        """Turn the strategy's raw signal into the agent's vote, or None to abstain."""
        if self.sitting_out:
            return None
        return max(low, min(high, neutral + (signal - neutral) * self.weight))

    def snapshot(self) -> dict:
        recent = sum(self.memory) / len(self.memory) if self.memory else None
        return {"weight": round(self.weight, 3), "experience": self.experience,
                "skill": None if self.skill is None else round(self.skill, 3),
                "recent_reward": None if recent is None else round(recent, 3),
                "learning_rate": round(self.learning_rate, 4), "sitting_out": self.sitting_out}
