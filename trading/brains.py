"""Dual-Brain.

Brain Alpha (planner): owns the strategy weights and runs the self-adaptive
learning loop. On each interval it reweights strategies by the P&L they made
since the last update (multiplicative weights, bounded so no strategy is ever
switched fully off or given everything).

Brain Beta (reactive): runs every loop. It resets Jev's per-loop path budget and
times the loop against execution_loop_target_seconds.
"""

import math
import time

from .config import ATSConfig
from .jev import Jev

MIN_WEIGHT = 0.05
LEARNING_RATE = 2.0


class BrainAlpha:
    def __init__(self, strategies: list[str], interval_seconds: float, enabled: bool = True, clock=time.monotonic):
        self.weights = {s: 1 / len(strategies) for s in strategies}
        self.interval = interval_seconds
        self.enabled = enabled
        self.clock = clock
        self.last_update = clock()
        self.updates = 0
        self._pnl_at_last = {s: 0.0 for s in strategies}

    def due(self) -> bool:
        return self.enabled and self.clock() - self.last_update >= self.interval

    def learn(self, cumulative_pnl: dict[str, float], scale: float | None = None) -> dict[str, float]:
        """Reweight from P&L earned since the last update.

        `scale` normalises P&L (e.g. a fraction of capital). If omitted, the
        strategies are compared relative to each other's moves this interval.
        """
        deltas = {s: cumulative_pnl.get(s, 0.0) - self._pnl_at_last[s] for s in self.weights}
        if scale is None:
            scale = sum(abs(d) for d in deltas.values()) or 1.0
        for s in self.weights:
            delta = deltas[s]
            self.weights[s] *= math.exp(LEARNING_RATE * max(-1.0, min(1.0, delta / scale)))
            self._pnl_at_last[s] = cumulative_pnl.get(s, 0.0)
        total = sum(self.weights.values())
        self.weights = {s: max(MIN_WEIGHT, w / total) for s, w in self.weights.items()}
        total = sum(self.weights.values())
        self.weights = {s: w / total for s, w in self.weights.items()}
        self.last_update = self.clock()
        self.updates += 1
        return dict(self.weights)


class BrainBeta:
    def __init__(self, config: ATSConfig, clock=time.perf_counter):
        self.jev = Jev(config)
        self.target = config.beta.loop_target_seconds
        self.clock = clock
        self.loops = 0
        self.overruns = 0
        self.last_loop_seconds = 0.0
        self._started = 0.0

    def begin(self) -> None:
        self.jev.new_loop()
        self._started = self.clock()

    def end(self) -> float:
        self.last_loop_seconds = self.clock() - self._started
        self.loops += 1
        if self.last_loop_seconds > self.target:
            self.overruns += 1
        return self.last_loop_seconds
