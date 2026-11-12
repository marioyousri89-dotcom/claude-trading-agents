"""Live prices for paper trading: the same desk and agents, trading real market prices with paper money.

MARKET=live swaps the simulated market for this feed. A background thread polls public price endpoints
(no keys, no accounts): Coinbase for BTC and ETH, Yahoo Finance's chart endpoint for stocks (US stocks
are delayed a little and only move during market hours). Every tick returns the latest known prices, so
the desk never waits on the network. A symbol that hasn't been fetched yet keeps its last price; if a
feed fails, the error is counted and the last price stands.

This module only reads prices. It has no way to place an order: every fill still goes to the paper
broker, and real money stays locked (trading/platforms.py).
"""

import json
import threading
import time
import urllib.request

CRYPTO = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD"}
POLL_SECONDS = 15.0
USER_AGENT = "Mozilla/5.0 (paper-trading price reader)"


def _get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URLs only)
        return json.loads(resp.read().decode())


def fetch_price(symbol: str, get=_get_json) -> float:
    if symbol in CRYPTO:
        data = get(f"https://api.coinbase.com/v2/prices/{CRYPTO[symbol]}/spot")
        return float(data["data"]["amount"])
    data = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d")
    return float(data["chart"]["result"][0]["meta"]["regularMarketPrice"])


class LiveMarket:
    """Drop-in for SimulatedMarket: tick() returns {symbol: price}. `drift` is empty (no hidden truth live)."""

    def __init__(self, symbols: list[str], poll_seconds: float = POLL_SECONDS, fetch=fetch_price,
                 start_thread: bool = True, wait_first: float = 20.0):
        self.symbols = list(symbols)
        self.poll_seconds = poll_seconds
        self.fetch = fetch
        self.prices: dict[str, float] = {}
        self.drift: dict[str, float] = {}
        self.errors = 0
        self.last_error = ""
        self.polls = 0
        self._lock = threading.Lock()
        self.poll_once()
        if start_thread:
            threading.Thread(target=self._run, name="live-prices", daemon=True).start()
            deadline = time.time() + wait_first
            while len(self.prices) < len(self.symbols) and time.time() < deadline:
                time.sleep(0.5)

    def poll_once(self) -> None:
        for s in self.symbols:
            try:
                price = self.fetch(s)
                if price > 0:
                    with self._lock:
                        self.prices[s] = price
            except Exception as e:  # a failed fetch keeps the last price
                with self._lock:
                    self.errors += 1
                    self.last_error = f"{s}: {type(e).__name__}: {e}"[:200]
        self.polls += 1

    def _run(self) -> None:
        while True:
            time.sleep(self.poll_seconds)
            self.poll_once()

    def tick(self) -> dict[str, float]:
        with self._lock:
            return dict(self.prices)

    def status(self) -> dict:
        with self._lock:
            return {"market": "live", "symbols": len(self.symbols), "priced": len(self.prices),
                    "polls": self.polls, "errors": self.errors, "last_error": self.last_error}
