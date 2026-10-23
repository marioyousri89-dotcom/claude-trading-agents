"""Run one engine shard. Several shards together run the full agent team.

Configured with environment variables so the same image runs as any shard:
  AGENT_COUNT   total agents across all shards (default 750)
  SHARD_ID      this shard's index, 0-based (default 0)
  SHARD_COUNT   number of shards (default 1)
  SYMBOLS       comma-separated symbols (default AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH)
  TICKS         ticks to run, 0 = forever (default 0)
  TICK_SECONDS  delay between ticks (default 0.5)
  EVENTS_DIR    directory for the shard's events-<id>.jsonl (optional)
  SEED          market/agent seed (default 42)
"""

import os
import signal
import sys
import time
from pathlib import Path

from .agents import build_team
from .broker import PaperBroker
from .desk import Desk
from .events import EventLog
from .market import SimulatedMarket
from .risk import RiskLimits, RiskManager

DEFAULT_SYMBOLS = "AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH"


def shard_agents(agent_count: int, symbols: list[str], shard_id: int, shard_count: int, seed: int):
    if not 0 <= shard_id < shard_count:
        raise SystemExit(f"SHARD_ID must be in 0..{shard_count - 1}")
    return build_team(agent_count, symbols, seed)[shard_id::shard_count]


def run(env=os.environ, stream=None) -> Desk:
    agent_count = int(env.get("AGENT_COUNT", 750))
    shard_id = int(env.get("SHARD_ID", 0))
    shard_count = int(env.get("SHARD_COUNT", 1))
    symbols = [s.strip() for s in env.get("SYMBOLS", DEFAULT_SYMBOLS).split(",") if s.strip()]
    ticks = int(env.get("TICKS", 0))
    tick_seconds = float(env.get("TICK_SECONDS", 0.5))
    seed = int(env.get("SEED", 42))
    events_dir = env.get("EVENTS_DIR")

    agents = shard_agents(agent_count, symbols, shard_id, shard_count, seed)
    log = EventLog(shard_id, Path(events_dir) / f"events-{shard_id}.jsonl" if events_dir else None, stream)
    broker = PaperBroker()
    desk = Desk(agents, broker, RiskManager(RiskLimits(), broker), log)
    # Every shard sees the same market (same seed), but trades its own agents.
    market = SimulatedMarket(symbols, seed=seed)

    roles = {}
    for a in agents:
        roles[a.role] = roles.get(a.role, 0) + 1
    log.emit("start", agents=len(agents), total_agents=agent_count, shard_count=shard_count,
             roles=roles, symbols=symbols, mode="paper")

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping and (ticks == 0 or desk.ticks < ticks):
            desk.step(market.tick())
            if tick_seconds:
                time.sleep(tick_seconds)
    except KeyboardInterrupt:
        pass
    log.emit("stop", ticks=desk.ticks, equity=round(broker.equity(market.prices), 2))
    log.close()
    return desk


if __name__ == "__main__":
    run()
    sys.exit(0)
