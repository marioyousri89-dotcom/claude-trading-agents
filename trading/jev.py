"""Jev: the typed-JSON contract gate. Every trade proposal from every bot passes here.

A proposal must match the contract exactly: no missing or unknown fields, exact
types (a bool is not a number), finite positive numbers, and values inside the
risk config. Anything else is rejected with a machine-readable reason code.
"""

import math
from collections import Counter
from dataclasses import dataclass

from .config import ATSConfig

RING_BOTS = ("watcher", "analyst", "risk_gate", "executor", "reporter", "vault", "crypto_predictor", "polymarket")
TRADING_BOTS = {"analyst", "crypto_predictor", "polymarket"}  # only these may propose trades
VENUES = {"spot", "polymarket", "dex"}
SIDES = {"buy", "sell"}

CONTRACT = {
    "bot": str,
    "venue": str,
    "instrument": str,
    "side": str,
    "qty": float,
    "price": float,
    "strategy": str,
    "expected_slippage_pct": float,
    "depth_usdc": float,
}


@dataclass(frozen=True)
class Proposal:
    bot: str
    venue: str
    instrument: str
    side: str
    qty: float
    price: float
    strategy: str
    expected_slippage_pct: float
    depth_usdc: float

    @property
    def notional(self) -> float:
        return self.qty * self.price

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in CONTRACT}


@dataclass(frozen=True)
class Decision:
    approved: bool
    code: str
    proposal: Proposal | None = None


class Jev:
    def __init__(self, config: ATSConfig):
        self.risk = config.risk
        self.max_paths = config.beta.max_categorical_paths
        self.counts: Counter[str] = Counter()  # "approved" / "rejected" totals
        self.reasons: Counter[str] = Counter()  # rejection codes
        self._paths: set[tuple[str, str, str]] = set()

    def new_loop(self) -> None:
        """Reset the per-loop decision-path budget (max_categorical_paths)."""
        self._paths.clear()

    def validate(self, raw: object) -> Decision:
        decision = self._check(raw)
        self.counts["approved" if decision.approved else "rejected"] += 1
        if not decision.approved:
            self.reasons[decision.code] += 1
        return decision

    def _check(self, raw: object) -> Decision:
        if not isinstance(raw, dict):
            return Decision(False, "not_an_object")
        if set(raw) != set(CONTRACT):
            return Decision(False, "bad_fields")
        for key, kind in CONTRACT.items():
            value = raw[key]
            if kind is float:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    return Decision(False, f"bad_type:{key}")
            elif not isinstance(value, kind) or not value:
                return Decision(False, f"bad_type:{key}")
        p = Proposal(**{k: float(v) if CONTRACT[k] is float else v for k, v in raw.items()})
        if p.bot not in TRADING_BOTS:
            return Decision(False, "bot_not_allowed_to_trade")
        if p.venue not in VENUES:
            return Decision(False, "unknown_venue")
        if p.side not in SIDES:
            return Decision(False, "bad_side")
        if p.qty <= 0 or p.price <= 0 or p.expected_slippage_pct < 0 or p.depth_usdc < 0:
            return Decision(False, "non_positive_value")
        if p.venue == "polymarket":
            if p.price >= 1:
                return Decision(False, "price_out_of_range")
            if p.depth_usdc < self.risk.min_depth_usdc:
                return Decision(False, "insufficient_depth")
        if p.notional > self.risk.max_trade_usdc:
            return Decision(False, "trade_too_large")
        if p.expected_slippage_pct > self.risk.slippage_tolerance_pct:
            return Decision(False, "slippage_too_high")
        path = (p.bot, p.strategy, p.instrument)
        if path not in self._paths and len(self._paths) >= self.max_paths:
            return Decision(False, "path_limit")
        self._paths.add(path)
        return Decision(True, "approved", p)
