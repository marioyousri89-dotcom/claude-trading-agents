"""The Whole Matrix desk (paper mode): four strategies fused into one decision.

  [sentiment feed] ──> confluence filter ──┐
  [multi-timeframe momentum] ──────────────┼─> combined targets ─> risk layer ─> Jev ─> paper execution
  [cross-asset stat arb] ──────────────────┤
  [regime detection (k-means)] ────────────┘ (decides which logic runs, and how big)

1. Multi-timeframe trend confluence: EMA(8) vs EMA(21) on 1m, 15m, 1h and 1d
   bars. A trend trade needs all four timeframes to agree.
2. Regime-switching ML: k-means on volatility and efficiency features
   (trading/regimes.py). Trend trades run only in trending regimes; high
   volatility halves size; new stat-arb entries pause when a leg is in a
   high-volatility trend (spread breakdown risk).
3. Cross-asset stat arb: a rolling OLS hedge ratio per pair (one day of
   1-minute log prices), spread z-score; enter at |z| > 2, exit at |z| < 0.5,
   stop at |z| > 4. Both legs trade together.
4. Confluence filter and news exits: a sentiment feed gives (score -1..1,
   relevance 0..1) per asset. Strong contrary news (relevance x |score| >= 0.5)
   suppresses a trade and exits an open position immediately; mild contrary
   news downsizes it. The news layer is not connected yet (it needs network
   access and API keys), so the live run uses a neutral feed.

Sizing uses order-book depth so expected slippage stays under the configured
tolerance. Every order passes the risk limits and Jev. Brain Alpha re-weights
the two strategies on the learning interval.

One tick = one simulated minute. On start the desk warms up on 21 simulated
days so the daily timeframe has data, then trades live.
"""

import math
import os
import signal
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from . import config as ats_config
from .brains import BrainAlpha, BrainBeta
from .broker import PaperBroker
from .events import EventLog
from .regimes import RegimeDetector, features
from .risk import RiskLimits, RiskManager
from .xmarket import ASSETS, PAIRS, CrossAssetMarket, Quote

TIMEFRAMES = {"1m": 1, "15m": 15, "1h": 60, "1d": 1440}
EMA_FAST, EMA_SLOW = 8, 21
WARMUP_TICKS = 21 * 1440
FEATURE_BARS = 96  # one day of 15-minute bars
REFIT_EVERY = 1440
ARB_WINDOW = 1440
Z_ENTRY, Z_EXIT, Z_STOP = 2.0, 0.5, 4.0
STOP_COOLDOWN = 60
TREND_SIZE_USD = 30_000.0
ARB_SIZE_USD = 25_000.0
STRONG_NEWS, MILD_NEWS = 0.5, 0.2
CAPITAL_USDC = 500_000.0
MIN_ORDER_USD = 500.0
STRATEGIES = ("trend_confluence", "stat_arb")


@dataclass(frozen=True)
class Sentiment:
    score: float  # -1 very bearish .. +1 very bullish
    relevance: float  # 0 noise .. 1 directly about this asset


class NeutralFeed:
    """Placeholder until a real news/NLP feed is connected: never has news."""

    def read(self, asset: str, tick: int) -> Sentiment | None:
        return None


class EMA:
    def __init__(self, n: int):
        self.k = 2 / (n + 1)
        self.value: float | None = None
        self.count = 0

    def update(self, x: float) -> None:
        self.value = x if self.value is None else self.value + self.k * (x - self.value)
        self.count += 1


class Timeframes:
    """EMA momentum per timeframe for one asset."""

    def __init__(self):
        self.fast = {tf: EMA(EMA_FAST) for tf in TIMEFRAMES}
        self.slow = {tf: EMA(EMA_SLOW) for tf in TIMEFRAMES}

    def update(self, tick: int, price: float) -> None:
        for tf, n in TIMEFRAMES.items():
            if tick % n == 0:
                self.fast[tf].update(price)
                self.slow[tf].update(price)

    def momentum(self) -> dict[str, int]:
        out = {}
        for tf in TIMEFRAMES:
            if self.slow[tf].count < EMA_SLOW:
                out[tf] = 0
            else:
                out[tf] = 1 if self.fast[tf].value > self.slow[tf].value else -1
        return out

    def confluence(self) -> int:
        m = self.momentum()
        if all(v == 1 for v in m.values()):
            return 1
        if all(v == -1 for v in m.values()):
            return -1
        return 0


class RollingPair:
    """O(1) rolling OLS of log(a) on log(b) and the spread's z-score."""

    def __init__(self, window: int = ARB_WINDOW):
        self.window = window
        self.xs: deque[float] = deque()
        self.ys: deque[float] = deque()
        self.sx = self.sy = self.sxx = self.syy = self.sxy = 0.0

    def add(self, y: float, x: float) -> None:
        self.xs.append(x)
        self.ys.append(y)
        self.sx += x
        self.sy += y
        self.sxx += x * x
        self.syy += y * y
        self.sxy += x * y
        if len(self.xs) > self.window:
            ox, oy = self.xs.popleft(), self.ys.popleft()
            self.sx -= ox
            self.sy -= oy
            self.sxx -= ox * ox
            self.syy -= oy * oy
            self.sxy -= ox * oy

    @property
    def ready(self) -> bool:
        return len(self.xs) >= self.window

    def stats(self) -> tuple[float, float] | None:
        """Return (beta, z) for the latest point, or None if not ready / degenerate."""
        n = len(self.xs)
        if n < self.window:
            return None
        mx, my = self.sx / n, self.sy / n
        vx = self.sxx / n - mx * mx
        vy = self.syy / n - my * my
        cov = self.sxy / n - mx * my
        if vx <= 1e-14:
            return None
        beta = cov / vx
        var_s = vy + beta * beta * vx - 2 * beta * cov
        if var_s <= 1e-14:
            return None
        spread = self.ys[-1] - beta * self.xs[-1]
        mean_s = my - beta * mx
        return beta, (spread - mean_s) / math.sqrt(var_s)


class StatArb:
    def __init__(self):
        self.pairs = {(a, b): RollingPair() for a, b, _ in PAIRS}
        self.side = {(a, b): 0 for a, b, _ in PAIRS}  # +1 long spread, -1 short spread
        self.cooldown = {(a, b): 0 for a, b, _ in PAIRS}
        self.last = {(a, b): None for a, b, _ in PAIRS}

    def update(self, quotes: dict[str, Quote]) -> None:
        for (a, b), rp in self.pairs.items():
            rp.add(math.log(quotes[a].price), math.log(quotes[b].price))

    def decide(self, tick: int, blocked: set[tuple[str, str]]) -> list[tuple[str, str, int, str]]:
        """Update spread positions. Returns (a, b, new_side, reason) for changes."""
        changes = []
        for pair, rp in self.pairs.items():
            st = rp.stats()
            self.last[pair] = st
            if st is None:
                continue
            _, z = st
            side = self.side[pair]
            if side and abs(z) > Z_STOP:
                self.side[pair], self.cooldown[pair] = 0, tick + STOP_COOLDOWN
                changes.append((*pair, 0, "stop"))
            elif side and abs(z) < Z_EXIT:
                self.side[pair] = 0
                changes.append((*pair, 0, "reverted"))
            elif not side and abs(z) > Z_ENTRY and abs(z) < Z_STOP and tick >= self.cooldown[pair] \
                    and pair not in blocked:
                self.side[pair] = -1 if z > 0 else 1  # spread rich: short it; cheap: buy it
                changes.append((*pair, self.side[pair], "entry"))
        return changes

    def targets(self, quotes: dict[str, Quote]) -> dict[tuple[str, str], dict[str, float]]:
        """USD targets per pair: long spread = +V in a, -beta*V in b (log-hedged)."""
        out = {}
        for pair, side in self.side.items():
            st = self.last[pair]
            if side and st:
                beta, _ = st
                out[pair] = {pair[0]: side * ARB_SIZE_USD, pair[1]: -side * beta * ARB_SIZE_USD}
        return out


def news_weight(s: Sentiment | None) -> float:
    return 0.0 if s is None else max(0.0, min(1.0, s.relevance)) * min(1.0, abs(s.score))


def contrary(s: Sentiment | None, direction: float) -> bool:
    return s is not None and direction != 0 and (s.score > 0) != (direction > 0) and s.score != 0


class MatrixDesk:
    def __init__(self, cfg: ats_config.ATSConfig, log: EventLog, seed: int = 42, feed=None,
                 learning_interval: float | None = None):
        self.cfg = cfg
        self.log = log
        self.feed = feed or NeutralFeed()
        self.market = CrossAssetMarket(seed)
        self.tf = {a: Timeframes() for a in ASSETS}
        self.closes15 = {a: deque(maxlen=FEATURE_BARS) for a in ASSETS}
        self.detector = RegimeDetector(seed)
        self.regime: dict[str, str | None] = {a: None for a in ASSETS}
        self.arb = StatArb()
        self.broker = PaperBroker(cash=CAPITAL_USDC)
        limits = RiskLimits(max_position_value=60_000, max_order_value=25_000, max_gross_exposure=300_000,
                            max_drawdown=cfg.risk.max_drawdown)
        self.risk = RiskManager(limits, self.broker)
        self.beta = BrainBeta(cfg)
        self.alpha = BrainAlpha(list(STRATEGIES), learning_interval if learning_interval is not None
                                else cfg.beta.learning_interval_seconds, cfg.beta.learning_enabled)
        self.pnl = {s: 0.0 for s in STRATEGIES}  # virtual P&L per strategy, for Brain Alpha
        self._virtual = {s: {a: 0.0 for a in ASSETS} for s in STRATEGIES}  # qty each strategy wants
        self.trend_side = {a: 0 for a in ASSETS}
        self.quotes: dict[str, Quote] = {}
        self.sim_tick = 0
        self.live_ticks = 0
        self.news_actions = 0

    # -- market data -------------------------------------------------------
    def _ingest(self, quotes: dict[str, Quote]) -> None:
        if self.quotes:
            for s in STRATEGIES:
                for a, qty in self._virtual[s].items():
                    self.pnl[s] += qty * (quotes[a].price - self.quotes[a].price)
        self.sim_tick += 1
        self.quotes = quotes
        for a, q in quotes.items():
            self.tf[a].update(self.sim_tick, q.price)
            if self.sim_tick % 15 == 0:
                self.closes15[a].append(q.price)
                feat = features(list(self.closes15[a]))
                if feat:
                    self.detector.observe(feat)
                    self.regime[a] = self.detector.classify(feat)
        self.arb.update(quotes)
        if self.sim_tick % REFIT_EVERY == 0 and self.detector.fit():
            self.regime = {a: self.detector.classify(features(list(self.closes15[a])))
                           if len(self.closes15[a]) >= 10 else None for a in ASSETS}

    def warm_up(self, ticks: int = WARMUP_TICKS) -> None:
        for _ in range(ticks):
            self._ingest(self.market.tick())
        self.log.emit("warmup", sim_days=round(ticks / 1440, 1), regime_fits=self.detector.fits,
                      regimes=self.regime)

    # -- decision ----------------------------------------------------------
    def _trend_targets(self) -> dict[str, float]:
        out = {}
        for a in ASSETS:
            regime = self.regime[a] or ""
            signal = self.tf[a].confluence() if regime.endswith("trending") else 0
            self.trend_side[a] = signal
            if signal:
                out[a] = signal * TREND_SIZE_USD * (0.5 if regime.startswith("high_vol") else 1.0)
        return out

    def _apply_news(self, strategy: str, targets: dict[str, float], key: str) -> dict[str, float]:
        """Confluence filter for one trade (all its legs scale together)."""
        factor = 1.0
        for a, usd in targets.items():
            s = self.feed.read(a, self.sim_tick)
            if not contrary(s, usd):
                continue
            w = news_weight(s)
            if w >= STRONG_NEWS:
                held = self._virtual[strategy][a]
                action = "exit" if held and (held > 0) == (usd > 0) else "suppress"
                self.news_actions += 1
                self.log.emit("news_filter", strategy=strategy, trade=key, asset=a, action=action,
                              score=s.score, relevance=s.relevance)
                return {}
            if w >= MILD_NEWS:
                factor = min(factor, 1 - w)
        if factor < 1.0:
            self.news_actions += 1
            self.log.emit("news_filter", strategy=strategy, trade=key, action="downsize", factor=round(factor, 2))
        return {a: usd * factor for a, usd in targets.items()}

    def step(self) -> None:
        self.live_ticks += 1
        self.beta.begin()
        self._ingest(self.market.tick())
        prices = {a: q.price for a, q in self.quotes.items()}
        self.risk.update(prices)

        # stat arb: block new entries when either leg is in a high-volatility trend
        blocked = {(a, b) for a, b, _ in PAIRS
                   if "high_vol_trending" in (self.regime[a], self.regime[b])}
        for a, b, side, reason in self.arb.decide(self.sim_tick, blocked):
            self.log.emit("signal", strategy="stat_arb", pair=f"{a}/{b}", side=side, reason=reason)

        scale = {s: w * len(STRATEGIES) for s, w in self.alpha.weights.items()}
        combined = {a: 0.0 for a in ASSETS}
        for strategy, trades in (("trend_confluence", {a: {a: v} for a, v in self._trend_targets().items()}),
                                 ("stat_arb", {f"{a}/{b}": t for (a, b), t in self.arb.targets(self.quotes).items()})):
            virtual = {a: 0.0 for a in ASSETS}
            for key, legs in trades.items():
                for a, usd in self._apply_news(strategy, legs, key).items():
                    usd *= scale[strategy]
                    combined[a] += usd
                    virtual[a] += usd / prices[a]
            self._virtual[strategy] = virtual

        orders = 0
        for a in ASSETS:
            if self.risk.halted and combined[a] and (combined[a] > 0) == (self.broker.position(a) > 0):
                combined[a] = 0.0  # halted: only reduce
            q = self.quotes[a]
            desired = combined[a] / q.price - self.broker.position(a)
            # Size to the order book: keep expected slippage within 80% of tolerance.
            max_qty = q.depth_usdc * self.cfg.risk.slippage_tolerance_pct / 100 * 0.8 / q.price
            desired = max(-max_qty, min(max_qty, desired))
            if abs(desired * q.price) < MIN_ORDER_USD:
                continue
            qty, reason = self.risk.approve(a, desired, prices)
            if reason:
                self.log.emit("risk", symbol=a, wanted=round(desired, 6), allowed=round(qty, 6), reason=reason)
            if not qty:
                continue
            decision = self.beta.jev.validate({
                "bot": "matrix", "venue": "spot", "instrument": a, "side": "buy" if qty > 0 else "sell",
                "qty": abs(qty), "price": q.price, "strategy": "whole_matrix",
                "expected_slippage_pct": abs(qty) * q.price / q.depth_usdc, "depth_usdc": q.depth_usdc,
            })
            if not decision.approved:
                self.log.emit("jev", symbol=a, decision="rejected", codes=[decision.code], legs=1)
                continue
            self.broker.fill(a, qty, q.price)
            orders += 1
            self.log.emit("order", symbol=a, qty=round(qty, 6), price=round(q.price, 6),
                          notional=round(qty * q.price, 2))

        if self.alpha.due():
            new = self.alpha.learn(self.pnl)
            self.log.emit("brain", brain="alpha", update=self.alpha.updates,
                          weights={k: round(v, 3) for k, v in new.items()})

        loop = self.beta.end()
        equity = self.broker.equity(prices)
        self.log.emit(
            "tick", tick=self.live_ticks, sim_minute=self.sim_tick, equity=round(equity, 2),
            pnl=round(equity - CAPITAL_USDC, 2), halted=self.risk.halted, orders=orders,
            gross=round(self.broker.gross_exposure(prices), 2), loop_ms=round(loop * 1000, 2),
            jev={"approved": self.beta.jev.counts["approved"], "rejected": self.beta.jev.counts["rejected"]},
        )
        if self.live_ticks % 15 == 1:
            self.log.emit("matrix", **self.snapshot())

    def snapshot(self) -> dict:
        assets = {}
        for a in ASSETS:
            s = self.feed.read(a, self.sim_tick)
            assets[a] = {"regime": self.regime[a], "mtf": self.tf[a].momentum(), "confluence": self.tf[a].confluence(),
                         "sentiment": None if s is None else {"score": s.score, "relevance": s.relevance},
                         "position_usd": round(self.broker.position(a) * self.quotes[a].price, 2)}
        pairs = {}
        for (a, b), st in self.arb.last.items():
            pairs[f"{a}/{b}"] = {"side": self.arb.side[(a, b)],
                                 "beta": None if st is None else round(st[0], 4),
                                 "z": None if st is None else round(st[1], 3)}
        return {"assets": assets, "pairs": pairs, "stormy": self.market.stormy,
                "weights": {k: round(v, 3) for k, v in self.alpha.weights.items()}}


def run(env=os.environ, stream=None) -> MatrixDesk:
    from .ring import require_paper

    require_paper(env.get("MODE", "paper"))
    cfg = ats_config.load(env.get("ATS_CONFIG"))
    ticks = int(env.get("TICKS", 0))
    tick_seconds = float(env.get("TICK_SECONDS", 1.0))
    warmup = int(env.get("WARMUP_TICKS", WARMUP_TICKS))
    events_dir = env.get("EVENTS_DIR")
    interval = env.get("LEARNING_INTERVAL_SECONDS")
    log = EventLog("matrix", Path(events_dir) / "events-matrix.jsonl" if events_dir else None, stream)
    desk = MatrixDesk(cfg, log, int(env.get("SEED", 42)), learning_interval=float(interval) if interval else None)
    log.emit("start", agents=0, mode="paper", config_version=cfg.version, strategies=list(STRATEGIES),
             assets=list(ASSETS), pairs=[f"{a}/{b}" for a, b, _ in PAIRS], timeframes=list(TIMEFRAMES),
             news_feed="neutral (not connected)", capital=CAPITAL_USDC)
    desk.warm_up(warmup)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping and (ticks == 0 or desk.live_ticks < ticks):
            desk.step()
            if tick_seconds:
                time.sleep(max(0.0, tick_seconds - desk.beta.last_loop_seconds))
    except KeyboardInterrupt:
        pass
    log.emit("stop", ticks=desk.live_ticks, pnl={k: round(v, 2) for k, v in desk.pnl.items()})
    log.close()
    return desk


if __name__ == "__main__":
    run()
