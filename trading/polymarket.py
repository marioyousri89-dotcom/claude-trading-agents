"""Polymarket bot: simulated prediction markets and the three configured strategies.

Paper mode only: books are simulated and nothing is sent to Polymarket.

Assumptions (the config gives rates, not formulas):
- Taker fee per share = category_rate * p * (1 - p). It peaks at p = 0.5, which
  is why maker-rebate hunting targets the 0.40-0.60 band.
- Makers earn REBATE_SHARE of the taker fee paid against their filled quotes.
- Expected slippage (%) = order notional / 2%-depth, i.e. eating the whole 2%
  band moves the average fill by about 1%.
- The oracle-dispute buffer is a minimum net edge (fraction of cost) that a
  locked-in arbitrage must clear, to cover the risk of a disputed resolution.
"""

import random
from dataclasses import dataclass, field

from .config import ATSConfig

REBATE_SHARE = 0.25
MAKER_FILL_PROB = 0.2
MAKER_QUOTE_OFFSET = 0.01
CATEGORIES = ("crypto", "sports", "politics_finance")


def taker_fee(price: float, rate: float) -> float:
    return rate * price * (1 - price)


def slippage_pct(notional: float, depth: float) -> float:
    return notional / depth if depth > 0 else float("inf")  # whole 2% band ~ 1% average slippage


@dataclass
class Outcome:
    name: str
    yes_ask: float
    depth_usdc: float


@dataclass
class NegRiskEvent:
    event_id: str
    category: str
    outcomes: list[Outcome]


@dataclass
class CrossBookPair:
    market_id: str
    category: str
    yes_ask_a: float  # YES on book A
    no_ask_b: float  # NO on book B, same outcome
    depth_a: float
    depth_b: float


@dataclass
class BinaryMarket:
    market_id: str
    category: str
    mid: float
    depth_usdc: float


@dataclass
class Snapshot:
    neg_risk: list[NegRiskEvent] = field(default_factory=list)
    cross_book: list[CrossBookPair] = field(default_factory=list)
    binaries: list[BinaryMarket] = field(default_factory=list)


@dataclass
class Opportunity:
    strategy: str
    legs: list[dict]  # Jev proposal dicts; all must be approved or none execute
    locked_pnl: float  # paper P&L booked if every leg fills


class SimulatedPolymarket:
    def __init__(self, seed: int = 0, events: int = 6, pairs: int = 8, binaries: int = 10, mispricing: float = 0.05):
        self.rng = random.Random(seed)
        self.n_events, self.n_pairs, self.n_binaries = events, pairs, binaries
        self.mispricing = mispricing
        self.ticks = 0

    def _depth(self) -> float:
        return self.rng.choice((20_000, 60_000, 150_000, 400_000)) * self.rng.uniform(0.8, 1.2)

    def tick(self) -> Snapshot:
        self.ticks += 1
        rng = self.rng
        snap = Snapshot()
        for i in range(self.n_events):
            k = rng.randint(3, 6)
            raw = [rng.random() + 0.05 for _ in range(k)]
            fair = [r / sum(raw) for r in raw]
            # Normally the asks sum to a bit over 1 (the book's edge); sometimes
            # a stale book lets them sum to under 1.
            markup = rng.uniform(0.005, 0.03) if rng.random() > self.mispricing else -rng.uniform(0.03, 0.15)
            asks = [min(0.99, max(0.01, f + markup / k + rng.gauss(0, 0.003))) for f in fair]
            snap.neg_risk.append(NegRiskEvent(
                f"event-{i}", CATEGORIES[i % 3],
                [Outcome(f"outcome-{j}", round(a, 4), self._depth()) for j, a in enumerate(asks)],
            ))
        for i in range(self.n_pairs):
            p = rng.uniform(0.1, 0.9)
            gap = rng.uniform(0.005, 0.03) if rng.random() > self.mispricing else -rng.uniform(0.02, 0.10)
            snap.cross_book.append(CrossBookPair(
                f"pair-{i}", CATEGORIES[i % 3],
                round(min(0.99, p + gap / 2), 4), round(min(0.99, 1 - p + gap / 2), 4),
                self._depth(), self._depth(),
            ))
        for i in range(self.n_binaries):
            snap.binaries.append(BinaryMarket(f"market-{i}", CATEGORIES[i % 3], round(rng.uniform(0.05, 0.95), 4),
                                              self._depth()))
        return snap


class PolymarketBot:
    STRATEGIES = ("neg_risk_squeeze", "cross_book_arb", "maker_rebate")

    def __init__(self, config: ATSConfig, seed: int = 0):
        self.cfg = config
        self.rng = random.Random(seed + 1)

    def _leg(self, strategy, instrument, side, qty, price, depth) -> dict:
        return {
            "bot": "polymarket", "venue": "polymarket", "instrument": instrument, "side": side,
            "qty": round(qty, 4), "price": price, "strategy": strategy,
            "expected_slippage_pct": round(slippage_pct(qty * price, depth), 4), "depth_usdc": round(depth, 2),
        }

    def _budget(self, weight: float, min_depth: float) -> float:
        # Size so expected slippage stays at 80% of the tolerance even at full
        # scale. Brain Alpha's weight scales within that: equal weights give
        # half size, a strategy it favours can grow to the cap.
        cap = min(self.cfg.risk.max_trade_usdc, min_depth * self.cfg.risk.slippage_tolerance_pct * 0.8)
        scale = weight * len(self.STRATEGIES)
        return cap / 2 * min(scale, 2.0)

    def neg_risk_squeeze(self, event: NegRiskEvent, weight: float) -> Opportunity | None:
        total = sum(o.yes_ask for o in event.outcomes)
        if 1 - total < self.cfg.thresholds.neg_risk_epsilon:
            return None
        rate = self.cfg.fee_rates[event.category]
        min_depth = min(o.depth_usdc for o in event.outcomes)
        sets = self._budget(weight, min_depth) / total
        fees = sum(taker_fee(o.yes_ask, rate) for o in event.outcomes)
        slip = sum(o.yes_ask * slippage_pct(sets * o.yes_ask, o.depth_usdc) / 100 for o in event.outcomes)  # $/set
        edge = 1 - total - fees - slip  # per set: one YES of each outcome pays exactly 1
        if edge < self.cfg.risk.oracle_buffer * total:
            return None
        legs = [self._leg("neg_risk_squeeze", f"{event.event_id}:{o.name}:YES", "buy", sets, o.yes_ask, o.depth_usdc)
                for o in event.outcomes]
        return Opportunity("neg_risk_squeeze", legs, edge * sets)

    def cross_book_arb(self, pair: CrossBookPair, weight: float) -> Opportunity | None:
        cost = pair.yes_ask_a + pair.no_ask_b
        if 1 - cost < self.cfg.thresholds.cross_book_min_spread:
            return None
        rate = self.cfg.fee_rates[pair.category]
        shares = self._budget(weight, min(pair.depth_a, pair.depth_b)) / cost
        fees = taker_fee(pair.yes_ask_a, rate) + taker_fee(pair.no_ask_b, rate)
        slip = (pair.yes_ask_a * slippage_pct(shares * pair.yes_ask_a, pair.depth_a)
                + pair.no_ask_b * slippage_pct(shares * pair.no_ask_b, pair.depth_b)) / 100
        edge = 1 - cost - fees - slip
        if edge < self.cfg.risk.oracle_buffer * cost:
            return None
        legs = [self._leg("cross_book_arb", f"{pair.market_id}:A:YES", "buy", shares, pair.yes_ask_a, pair.depth_a),
                self._leg("cross_book_arb", f"{pair.market_id}:B:NO", "buy", shares, pair.no_ask_b, pair.depth_b)]
        return Opportunity("cross_book_arb", legs, edge * shares)

    def maker_rebate(self, market: BinaryMarket, weight: float) -> Opportunity | None:
        lo, hi = self.cfg.thresholds.maker_band
        if not lo <= market.mid <= hi:
            return None
        bid, ask = round(market.mid - MAKER_QUOTE_OFFSET, 4), round(market.mid + MAKER_QUOTE_OFFSET, 4)
        shares = self._budget(weight, market.depth_usdc) * 0.25 / market.mid
        rebate_per_share = REBATE_SHARE * taker_fee(market.mid, self.cfg.fee_rates[market.category])
        fills = sum(self.rng.random() < MAKER_FILL_PROB for _ in range(2))
        # Maker quotes rest on the book, so they don't pay taker slippage.
        legs = [dict(self._leg("maker_rebate", f"{market.market_id}:YES", "buy", shares, bid, market.depth_usdc),
                     expected_slippage_pct=0.0),
                dict(self._leg("maker_rebate", f"{market.market_id}:YES", "sell", shares, ask, market.depth_usdc),
                     expected_slippage_pct=0.0)]
        return Opportunity("maker_rebate", legs, fills * shares * rebate_per_share)

    def scan(self, snap: Snapshot, weights: dict[str, float]) -> list[Opportunity]:
        found = []
        for e in snap.neg_risk:
            found.append(self.neg_risk_squeeze(e, weights["neg_risk_squeeze"]))
        for p in snap.cross_book:
            found.append(self.cross_book_arb(p, weights["cross_book_arb"]))
        for m in snap.binaries:
            found.append(self.maker_rebate(m, weights["maker_rebate"]))
        return [o for o in found if o is not None]
