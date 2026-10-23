"""Simulated market feed. Deterministic for a given seed, so runs are reproducible."""

import math
import random


class SimulatedMarket:
    def __init__(self, symbols: list[str], seed: int = 0, start_price: float = 100.0, volatility: float = 0.01):
        self.rng = random.Random(seed)
        self.volatility = volatility
        self.prices = {s: start_price * (1 + 0.5 * self.rng.random()) for s in symbols}
        self.drift = {s: 0.0 for s in symbols}

    def tick(self) -> dict[str, float]:
        for symbol, price in self.prices.items():
            # Slowly wandering drift gives trending and ranging regimes,
            # so different strategy types win at different times.
            self.drift[symbol] = 0.98 * self.drift[symbol] + self.rng.gauss(0, self.volatility * 0.05)
            shock = self.rng.gauss(self.drift[symbol], self.volatility)
            self.prices[symbol] = max(0.01, price * math.exp(shock))
        return dict(self.prices)
