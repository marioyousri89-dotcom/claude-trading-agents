"""Simulated cross-asset market for the Whole Matrix desk. Paper mode only.

One tick = one minute. Each pair shares a common factor plus a mean-reverting
spread, so the pairs are genuinely cointegrated (what statistical arbitrage
needs). On top of that:
- a market-wide volatility regime switches between calm and stormy;
- each factor's drift switches between trending and ranging phases;
- every asset has order-book depth (USD within 2% of mid) for slippage.

USDCAD loads negatively on its factor, so the oil pair has a negative hedge
ratio and the stat-arb code must handle signs generally.
"""

import math
import random
from dataclasses import dataclass

# asset: (start price, order-book depth within 2% in USD)
ASSETS = {
    "GOLD": (2400.0, 5_000_000),
    "AUDUSD": (0.66, 20_000_000),
    "NDX": (20_000.0, 10_000_000),
    "BTC": (70_000.0, 3_000_000),
    "WTI": (75.0, 4_000_000),
    "USDCAD": (1.37, 20_000_000),
}
# (asset a, asset b, sign of b's loading on the shared factor)
PAIRS = (("GOLD", "AUDUSD", 1), ("NDX", "BTC", 1), ("WTI", "USDCAD", -1))

CALM_VOL, STORMY_VOL = 0.0006, 0.0018  # per-minute volatility of the factor
VOL_SWITCH = 1 / 3000  # chance per minute that the market-wide vol regime flips
TREND_SWITCH = 1 / 2500  # chance per minute that a factor's trend phase changes
TREND_DRIFT = 0.00012  # per-minute drift while trending
SPREAD_REVERSION = 0.004  # OU mean reversion of each pair's spread, per minute
SPREAD_VOL = 0.0004


@dataclass
class Quote:
    price: float
    depth_usdc: float


class CrossAssetMarket:
    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed + 3)
        self.stormy = False
        self.ticks = 0
        self.factor = {i: 0.0 for i in range(len(PAIRS))}
        self.drift = {i: 0.0 for i in range(len(PAIRS))}
        self.spread = {i: 0.0 for i in range(len(PAIRS))}
        self.noise = {a: 0.0 for a in ASSETS}

    @property
    def vol(self) -> float:
        return STORMY_VOL if self.stormy else CALM_VOL

    def tick(self) -> dict[str, Quote]:
        rng = self.rng
        self.ticks += 1
        if rng.random() < VOL_SWITCH:
            self.stormy = not self.stormy
        quotes = {}
        for i, (a, b, sign) in enumerate(PAIRS):
            if rng.random() < TREND_SWITCH:
                self.drift[i] = rng.choice((0.0, 0.0, TREND_DRIFT, -TREND_DRIFT))
            self.factor[i] += self.drift[i] + rng.gauss(0, self.vol)
            self.spread[i] += -SPREAD_REVERSION * self.spread[i] + rng.gauss(0, SPREAD_VOL * (2 if self.stormy else 1))
            f, s = self.factor[i], self.spread[i]
            for asset, log_move in ((a, f + 0.5 * s), (b, sign * (f - 0.5 * s))):
                # Small idiosyncratic noise so prices are not perfectly collinear.
                self.noise[asset] = 0.9 * self.noise[asset] + rng.gauss(0, self.vol * 0.15)
                start, depth = ASSETS[asset]
                depth_now = depth * (0.6 if self.stormy else 1.0)  # books thin out in stormy markets
                quotes[asset] = Quote(start * math.exp(log_move + self.noise[asset]), depth_now)
        return quotes
