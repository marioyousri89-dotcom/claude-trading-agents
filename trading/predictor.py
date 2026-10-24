"""Crypto Predictor bot: scores newly launched token pools for early traction.

Paper mode only: launches are simulated. Each pool has a hidden quality that
drives its future price; the bot only sees noisy features (liquidity, holder
growth, volume velocity, dev-wallet share) and must infer it.
"""

import math
import random
from dataclasses import dataclass

from .config import ATSConfig

BUY_SCORE = 0.7
MAX_DEV_SHARE = 0.2  # skip pools where the deployer holds too much (rug risk)
HOLD_TICKS = 20
BASE_BUDGET_USDC = 2_000.0


@dataclass
class Pool:
    token: str
    liquidity_usdc: float
    holder_growth: float  # new holders per tick, % of holders
    volume_velocity: float  # volume / liquidity
    dev_wallet_share: float
    price: float
    quality: float  # hidden from the bot
    rugged: bool = False


@dataclass
class Position:
    token: str
    qty: float
    entry: float
    opened_tick: int


def traction_score(pool: Pool) -> float:
    z = (1.2 * math.log10(max(pool.liquidity_usdc, 1) / 100_000)
         + 0.35 * pool.holder_growth
         + 1.5 * pool.volume_velocity
         - 6.0 * pool.dev_wallet_share)
    return 1 / (1 + math.exp(-z))


class SimulatedLaunchpad:
    def __init__(self, seed: int = 0, launch_prob: float = 0.5, max_pools: int = 40):
        self.rng = random.Random(seed + 2)
        self.launch_prob = launch_prob
        self.max_pools = max_pools
        self.pools: dict[str, Pool] = {}
        self.launched = 0

    def tick(self, keep: set[str] = frozenset()) -> list[Pool]:
        """Advance one tick. Pools in `keep` (open positions) are never evicted."""
        rng = self.rng
        if rng.random() < self.launch_prob:
            q = rng.random()
            self.launched += 1
            token = f"NEW{self.launched:04d}"
            self.pools[token] = Pool(
                token,
                liquidity_usdc=10 ** rng.uniform(4.5, 6.5) * (0.5 + q),
                holder_growth=max(0.0, rng.gauss(4 * q, 1.0)),
                volume_velocity=max(0.0, rng.gauss(q, 0.3)),
                dev_wallet_share=min(0.9, max(0.0, rng.gauss(0.25 - 0.2 * q, 0.08))),
                price=1.0, quality=q,
            )
        for pool in self.pools.values():
            if pool.rugged:
                continue
            if pool.dev_wallet_share > 0.3 and rng.random() < 0.02:
                pool.rugged, pool.price = True, pool.price * 0.05
                continue
            pool.price *= math.exp(rng.gauss((pool.quality - 0.45) * 0.03, 0.04))
        evictable = [t for t in self.pools if t not in keep]
        for token in evictable[:max(0, len(self.pools) - self.max_pools)]:
            del self.pools[token]
        return list(self.pools.values())


class CryptoPredictor:
    def __init__(self, config: ATSConfig):
        self.cfg = config
        self.positions: dict[str, Position] = {}
        self.realized = 0.0
        self.best_score = 0.0

    def _proposal(self, pool: Pool, side: str, qty: float) -> dict:
        notional = qty * pool.price
        return {
            "bot": "crypto_predictor", "venue": "dex", "instrument": pool.token, "side": side,
            "qty": round(qty, 6), "price": pool.price, "strategy": "new_token_traction",
            # Constant-product AMM: price impact is about 2x the fraction of the pool traded.
            "expected_slippage_pct": round(notional / pool.liquidity_usdc * 200, 4),
            "depth_usdc": round(pool.liquidity_usdc, 2),
        }

    def propose(self, pools: list[Pool], tick: int, weight: float) -> list[dict]:
        out = []
        by_token = {p.token: p for p in pools}
        for pos in list(self.positions.values()):
            pool = by_token.get(pos.token)
            if pool is not None and (tick - pos.opened_tick >= HOLD_TICKS or pool.rugged):
                # Exit in chunks that stay under the slippage tolerance; the
                # rest is sold on later ticks.
                max_qty = pool.liquidity_usdc * self.cfg.risk.slippage_tolerance_pct / 200 * 0.8 / pool.price
                out.append(self._proposal(pool, "sell", min(pos.qty, max_qty)))
        self.best_score = 0.0
        for pool in pools:
            if pool.rugged or pool.token in self.positions:
                continue
            score = traction_score(pool)
            self.best_score = max(self.best_score, score)
            if score < BUY_SCORE or pool.dev_wallet_share > MAX_DEV_SHARE:
                continue
            # Stay under the slippage tolerance (impact ~ 2 x notional / liquidity).
            budget = min(BASE_BUDGET_USDC * weight * 4, pool.liquidity_usdc * self.cfg.risk.slippage_tolerance_pct / 200 * 0.8)
            if budget >= 10:
                out.append(self._proposal(pool, "buy", budget / pool.price))
        return out

    def fill(self, proposal: dict, tick: int) -> float:
        """Apply an approved proposal; return realized P&L."""
        token, qty, price = proposal["instrument"], proposal["qty"], proposal["price"]
        if proposal["side"] == "buy":
            self.positions[token] = Position(token, qty, price, tick)
            return 0.0
        pos = self.positions.get(token)
        if pos is None:
            return 0.0
        sold = min(qty, pos.qty)
        pnl = sold * (price - pos.entry)
        pos.qty -= sold
        if pos.qty <= 1e-9:
            del self.positions[token]
        self.realized += pnl
        return pnl

    def unrealized(self, pools: list[Pool]) -> float:
        prices = {p.token: p.price for p in pools}
        return sum(p.qty * (prices.get(p.token, p.entry) - p.entry) for p in self.positions.values())
