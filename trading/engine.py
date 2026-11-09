"""Run one engine shard. Several shards together run the full agent team.

Configured with environment variables so the same image runs as any shard:
  AGENT_COUNT   total agents across all shards, split evenly over 4 departments (default 1400)
  SHARD_ID      this shard's index, 0-based (default 0)
  SHARD_COUNT   number of shards (default 1)
  SYMBOLS       comma-separated symbols (default AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH)
  TICKS         ticks to run, 0 = forever (default 0)
  TICK_SECONDS  delay between ticks (default 0.5)
  EVENTS_DIR    directory for the shard's events-<id>.jsonl (optional). The dashboard's
                agent slider writes control.json here; shards resize to match.
  SEED          market/agent seed (default 42)
  ATS_CONFIG    risk/brain config file (default config/ats_matrix.json)
  LEARNING_INTERVAL_SECONDS  override the config's self-adaptive learning interval
  MODE          only "paper" is implemented
"""

import os
import signal
import sys
import time
from pathlib import Path

from . import academy
from . import config as ats_config
from . import control
from .agents import build_team
from .org import DEPARTMENTS, MIN_DEPARTMENT_SIZE
from .brains import BrainAlpha, BrainBeta
from .broker import PaperBroker
from .desk import Desk
from .events import EventLog
from .market import SimulatedMarket
from .news import NewsWire
from .risk import RiskLimits, RiskManager

DEFAULT_SYMBOLS = "AAPL,MSFT,NVDA,AMZN,GOOG,TSLA,META,AMD,BTC,ETH"
DEFAULT_AGENT_COUNT = MIN_DEPARTMENT_SIZE * len(DEPARTMENTS)  # 350 per department
CONTROL_EVERY = 10  # ticks between checks of the agent slider


def shard_agents(agent_count: int, symbols: list[str], shard_id: int, shard_count: int, seed: int):
    if not 0 <= shard_id < shard_count:
        raise SystemExit(f"SHARD_ID must be in 0..{shard_count - 1}")
    agents = build_team(agent_count, symbols, seed)[shard_id::shard_count]
    for agent in agents:
        academy.enroll(agent)  # every agent is trained for its department's job before it starts
    return agents


def resize(desk: Desk, agent_count: int, symbols: list[str], shard_id: int, shard_count: int, seed: int) -> int:
    """Grow or shrink the shard's team to match `agent_count`. Agents that stay keep their brains."""
    current = {a.agent_id: a for a in desk.agents}
    wanted = shard_agents(agent_count, symbols, shard_id, shard_count, seed)
    desk.agents = [current.get(a.agent_id, a) for a in wanted]
    return len(desk.agents)


def run(env=os.environ, stream=None) -> Desk:
    from .ring import require_paper

    require_paper(env.get("MODE", "paper"))
    cfg = ats_config.load(env.get("ATS_CONFIG"))
    agent_count = int(env.get("AGENT_COUNT", DEFAULT_AGENT_COUNT))
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
    interval = env.get("LEARNING_INTERVAL_SECONDS")
    alpha = BrainAlpha(["trend", "reversion"], float(interval) if interval else cfg.beta.learning_interval_seconds,
                       cfg.beta.learning_enabled)
    limits = RiskLimits(max_drawdown=cfg.risk.max_drawdown)
    desk = Desk(agents, broker, RiskManager(limits, broker), log, beta=BrainBeta(cfg), alpha=alpha)
    # Every shard sees the same market (same seed), but trades its own agents.
    market = SimulatedMarket(symbols, seed=seed)
    news = NewsWire(symbols, seed=seed)

    roles, departments = {}, {}
    for a in agents:
        roles[a.role] = roles.get(a.role, 0) + 1
        departments[a.department] = departments.get(a.department, 0) + 1
    log.emit("start", agents=len(agents), total_agents=agent_count, shard_count=shard_count,
             roles=roles, departments=departments, symbols=symbols, mode="paper", config_version=cfg.version,
             learning_interval_seconds=alpha.interval, school=academy.summarize(agents))

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping and (ticks == 0 or desk.ticks < ticks):
            if events_dir and desk.ticks % CONTROL_EVERY == 0:
                per_department = control.read(Path(events_dir))
                if per_department and control.total(per_department) != agent_count:
                    before = len(desk.agents)
                    agent_count = control.total(per_department)
                    after = resize(desk, agent_count, symbols, shard_id, shard_count, seed)
                    log.emit("resize", agents=after, was=before, total_agents=agent_count,
                             per_department=per_department)
            prices = market.tick()
            desk.step(prices, news.tick(market.drift, desk.ticks + 1))
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
