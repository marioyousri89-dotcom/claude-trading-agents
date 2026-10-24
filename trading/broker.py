"""Paper broker: simulated fills with fees. No real orders are ever sent."""


class PaperBroker:
    def __init__(self, cash: float = 100_000.0, fee_bps: float = 1.0):
        self.starting_cash = cash
        self.cash = cash
        self.fee_bps = fee_bps
        self.positions: dict[str, float] = {}
        self.fees_paid = 0.0

    def fill(self, symbol: str, qty: float, price: float) -> None:
        notional = qty * price
        fee = abs(notional) * self.fee_bps / 10_000
        self.cash -= notional + fee
        self.fees_paid += fee
        self.positions[symbol] = self.positions.get(symbol, 0.0) + qty

    def position(self, symbol: str) -> float:
        return self.positions.get(symbol, 0.0)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(qty * prices[s] for s, qty in self.positions.items())

    def gross_exposure(self, prices: dict[str, float]) -> float:
        return sum(abs(qty * prices[s]) for s, qty in self.positions.items())
