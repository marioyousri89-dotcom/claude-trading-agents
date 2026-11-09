"""Simulated news wire: political and economic headlines for the analysts to read.

Headlines are noisy hints about each symbol's hidden drift in the simulated
market: most of the time the news points the way the market is really leaning,
sometimes it doesn't. The wire has its own random stream, so it never changes
the prices themselves. Paper only.
"""

import random

from .academy import ECONOMY, POLITICS

HEADLINE_CHANCE = 0.08  # per symbol per tick
HONEST = 0.7  # share of headlines that point the way the market is really leaning
LATEST = 8

_TEXT = {
    "election_uncertainty": "Election too close to call", "stable_government": "Coalition secures clear majority",
    "new_tariffs": "New tariffs announced", "trade_deal": "Trade deal signed", "sanctions": "Fresh sanctions imposed",
    "stimulus_bill": "Stimulus bill passes", "war_escalation": "Conflict escalates", "ceasefire": "Ceasefire agreed",
    "tax_cut": "Tax cut approved", "regulation_crackdown": "Regulators launch crackdown",
    "inflation": "Inflation", "interest_rate": "Central bank rate decision", "jobs": "Jobs report",
    "gdp": "GDP", "unemployment": "Unemployment", "pmi": "Factory PMI", "consumer_confidence": "Consumer confidence",
    "retail_sales": "Retail sales",
}


class NewsWire:
    def __init__(self, symbols: list[str], seed: int = 0):
        self.rng = random.Random(f"news:{seed}")
        self.symbols = list(symbols)
        self.latest: list[dict] = []

    def tick(self, drift: dict[str, float], tick: int) -> list[dict]:
        out = []
        for symbol in self.symbols:
            if self.rng.random() >= HEADLINE_CHANCE:
                continue
            leaning = 1 if drift.get(symbol, 0.0) >= 0 else -1
            bullish = leaning > 0 if self.rng.random() < HONEST else self.rng.random() < 0.5
            out.append(self._headline(symbol, bullish, tick))
        self.latest = (out + self.latest)[:LATEST]
        return out

    def _headline(self, symbol: str, bullish: bool, tick: int) -> dict:
        if self.rng.random() < 0.5:
            event = self.rng.choice(sorted(k for k, v in POLITICS.items() if (v > 0) == bullish))
            value = round(self.rng.uniform(0.4, 1.0), 3)
            text = _TEXT[event]
            kind = "politics"
        else:
            event = self.rng.choice(sorted(ECONOMY))
            size = self.rng.uniform(0.5, 2.0)
            # A surprise in the direction that's bullish for this release, or the opposite.
            value = round(size if (ECONOMY[event] > 0) == bullish else -size, 3)
            text = f"{_TEXT[event]} {'above' if value > 0 else 'below'} forecast"
            kind = "economy"
        return {"tick": tick, "symbol": symbol, "kind": kind, "event": event, "value": value,
                "text": f"{symbol}: {text}"}
