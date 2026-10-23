"""Hard risk limits. Agents can only propose; these rules have the final say."""

from dataclasses import dataclass

from .broker import PaperBroker


@dataclass
class RiskLimits:
    max_position_value: float = 20_000.0  # per symbol, absolute
    max_order_value: float = 5_000.0
    max_gross_exposure: float = 80_000.0
    max_drawdown: float = 0.10  # fraction of starting equity; breach halts trading


class RiskManager:
    def __init__(self, limits: RiskLimits, broker: PaperBroker):
        self.limits = limits
        self.broker = broker
        self.peak_equity = broker.starting_cash
        self.halted = False

    def update(self, prices: dict[str, float]) -> None:
        equity = self.broker.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        if not self.halted and equity < self.peak_equity * (1 - self.limits.max_drawdown):
            self.halted = True

    def approve(self, symbol: str, qty: float, prices: dict[str, float]) -> tuple[float, str | None]:
        """Return the allowed quantity (possibly reduced to 0) and a reason if changed."""
        price = prices[symbol]
        current = self.broker.position(symbol)
        if self.halted:
            # Once halted, only trades that reduce risk are allowed.
            if current == 0 or (qty > 0) == (current > 0) or abs(qty) > abs(current):
                return 0.0, "halted: max drawdown breached"
            return qty, None
        reason = None
        max_order_qty = self.limits.max_order_value / price
        if abs(qty) > max_order_qty:
            qty, reason = max_order_qty * (1 if qty > 0 else -1), "order size capped"
        max_pos_qty = self.limits.max_position_value / price
        target = max(-max_pos_qty, min(max_pos_qty, current + qty))
        if target != current + qty:
            qty, reason = target - current, "position limit"
        gross_after = self.broker.gross_exposure(prices) - abs(current * price) + abs((current + qty) * price)
        if gross_after > self.limits.max_gross_exposure and abs(current + qty) > abs(current):
            return 0.0, "gross exposure limit"
        return qty, reason
