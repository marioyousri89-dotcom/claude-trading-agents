"""The 8-bot ring, run inside the Dual-Brain. Paper mode only.

Each loop:
  watcher          reads the simulated Polymarket books and token launchpad
  analyst          (the 750-agent engine shards cover spot markets; here it
                   counts the opportunities the specialist bots surface)
  polymarket       scans for neg-risk squeezes, cross-book arbs, maker rebates
  crypto_predictor scores new token pools for traction
  risk_gate        global drawdown halt (from the config)
  executor         sends every leg through Jev; a multi-leg trade executes
                   only if every leg is approved
  reporter         emits events, the 8-bot signal heatmap and the Jev desk
  vault            keeps secrets locked (never needed in paper mode)

Brain Beta wraps every loop; Brain Alpha re-weights the four strategies on the
configured learning interval.

Environment: TICKS (0 = forever), TICK_SECONDS (default 1.0), SEED, EVENTS_DIR,
LEARNING_INTERVAL_SECONDS (overrides the config), ATS_CONFIG, MODE (paper only).
"""

import os
import signal
import time
from pathlib import Path

from . import config as ats_config
from .brains import BrainAlpha, BrainBeta
from .events import EventLog
from .jev import RING_BOTS
from .polymarket import PolymarketBot, SimulatedPolymarket
from .predictor import CryptoPredictor, SimulatedLaunchpad
from .vault import Vault

CAPITAL_USDC = 1_000_000.0
STRATEGIES = ("neg_risk_squeeze", "cross_book_arb", "maker_rebate", "new_token_traction")


def require_paper(mode: str) -> None:
    if mode != "paper":
        raise SystemExit("Only MODE=paper is implemented. Live trading must go through the login gate "
                         "and is not built yet.")


class Ring:
    def __init__(self, cfg: ats_config.ATSConfig, log: EventLog, seed: int = 42, learning_interval: float | None = None):
        self.cfg = cfg
        self.log = log
        self.alpha = BrainAlpha(list(STRATEGIES), learning_interval if learning_interval is not None
                                else cfg.beta.learning_interval_seconds, cfg.beta.learning_enabled)
        self.beta = BrainBeta(cfg)
        self.market = SimulatedPolymarket(seed)
        self.launchpad = SimulatedLaunchpad(seed)
        self.polymarket = PolymarketBot(cfg, seed)
        self.predictor = CryptoPredictor(cfg)
        self.vault = Vault("paper")
        self.pnl = {s: 0.0 for s in STRATEGIES}
        self.peak_equity = CAPITAL_USDC
        self.halted = False
        self.ticks = 0
        self.legs_sent = 0
        self.trades_done = 0

    @property
    def jev(self):
        return self.beta.jev

    def equity(self, pools) -> float:
        return CAPITAL_USDC + sum(self.pnl.values()) + self.predictor.unrealized(pools)

    def step(self) -> None:
        self.ticks += 1
        self.beta.begin()
        weights = self.alpha.weights

        # watcher
        snap = self.market.tick()
        pools = self.launchpad.tick(keep=set(self.predictor.positions))

        # risk gate: global drawdown halt
        equity = self.equity(pools)
        self.peak_equity = max(self.peak_equity, equity)
        if not self.halted and equity < self.peak_equity * (1 - self.cfg.risk.max_drawdown):
            self.halted = True
            self.log.emit("risk", reason="global max drawdown breached; ring halted", equity=round(equity, 2))

        # polymarket + crypto predictor propose; executor sends every leg through Jev
        opportunities = [] if self.halted else self.polymarket.scan(snap, weights)
        approved_trades = 0
        for opp in opportunities:
            decisions = [self.jev.validate(leg) for leg in opp.legs]
            self.legs_sent += len(decisions)
            rejected = [d.code for d in decisions if not d.approved]
            if rejected:
                self.log.emit("jev", strategy=opp.strategy, decision="rejected", codes=sorted(set(rejected)),
                              legs=len(opp.legs))
                continue
            self.pnl[opp.strategy] += opp.locked_pnl
            approved_trades += 1
            if opp.strategy != "maker_rebate" or opp.locked_pnl:
                self.log.emit("trade", bot="polymarket", strategy=opp.strategy, legs=len(opp.legs),
                              notional=round(sum(l["qty"] * l["price"] for l in opp.legs), 2),
                              locked_pnl=round(opp.locked_pnl, 2))

        predictions = self.predictor.propose(pools, self.ticks, weights["new_token_traction"])
        for proposal in predictions:
            if self.halted and proposal["side"] == "buy":
                continue  # halted: exits only
            self.legs_sent += 1
            decision = self.jev.validate(proposal)
            if not decision.approved:
                self.log.emit("jev", strategy="new_token_traction", decision="rejected", codes=[decision.code],
                              legs=1)
                continue
            realized = self.predictor.fill(proposal, self.ticks)
            self.pnl["new_token_traction"] += realized
            approved_trades += 1
            self.log.emit("trade", bot="crypto_predictor", strategy="new_token_traction", side=proposal["side"],
                          token=proposal["instrument"], notional=round(proposal["qty"] * proposal["price"], 2),
                          realized_pnl=round(realized, 2))
        self.trades_done += approved_trades

        # brain alpha: self-adaptive learning
        if self.alpha.due():
            new = self.alpha.learn(self.pnl, scale=CAPITAL_USDC * 0.001)
            self.log.emit("brain", brain="alpha", update=self.alpha.updates,
                          weights={k: round(v, 3) for k, v in new.items()})

        loop_seconds = self.beta.end()
        equity = self.equity(pools)
        self.log.emit(
            "tick", tick=self.ticks, equity=round(equity, 2), pnl=round(equity - CAPITAL_USDC, 2),
            halted=self.halted, orders=approved_trades, loop_ms=round(loop_seconds * 1000, 2),
            loop_target_ms=self.cfg.beta.loop_target_seconds * 1000, loop_overruns=self.beta.overruns,
            jev={"approved": self.jev.counts["approved"], "rejected": self.jev.counts["rejected"]},
        )
        self.log.emit("heatmap", tick=self.ticks, bots=self.heatmap(snap, pools, opportunities, predictions, equity))

    def heatmap(self, snap, pools, opportunities, predictions, equity) -> dict[str, float]:
        """One signal per bot, -1..1, for the HUD."""
        def clip(x):
            return round(max(-1.0, min(1.0, x)), 3)

        mispriced = sum(1 for e in snap.neg_risk if sum(o.yes_ask for o in e.outcomes) < 1) \
            + sum(1 for p in snap.cross_book if p.yes_ask_a + p.no_ask_b < 1)
        jev_total = self.jev.counts["approved"] + self.jev.counts["rejected"]
        drawdown = 1 - equity / self.peak_equity
        poly_edge = sum(o.locked_pnl for o in opportunities)
        signals = {
            "watcher": clip(mispriced / max(1, len(snap.neg_risk) + len(snap.cross_book)) * 2 - 1),
            "analyst": clip(len(opportunities) / 5 - 1),
            "risk_gate": -1.0 if self.halted else clip(1 - 2 * drawdown / self.cfg.risk.max_drawdown),
            "executor": clip(self.jev.counts["approved"] / jev_total * 2 - 1) if jev_total else 0.0,
            "reporter": 1.0,
            "vault": 1.0 if self.vault.status()["locked"] else -1.0,
            "crypto_predictor": clip(self.predictor.best_score * 2 - 1),
            "polymarket": clip(poly_edge / 1000),
        }
        assert set(signals) == set(RING_BOTS)
        return signals


def run(env=os.environ, stream=None) -> Ring:
    require_paper(env.get("MODE", "paper"))
    cfg = ats_config.load(env.get("ATS_CONFIG"))
    ticks = int(env.get("TICKS", 0))
    tick_seconds = float(env.get("TICK_SECONDS", 1.0))
    seed = int(env.get("SEED", 42))
    events_dir = env.get("EVENTS_DIR")
    interval = env.get("LEARNING_INTERVAL_SECONDS")
    log = EventLog("ring", Path(events_dir) / "events-ring.jsonl" if events_dir else None, stream)
    ring = Ring(cfg, log, seed, float(interval) if interval else None)
    log.emit("start", agents=0, bots=list(RING_BOTS), strategies=list(STRATEGIES), mode="paper",
             config_version=cfg.version, capital=CAPITAL_USDC, vault=ring.vault.status(),
             learning_interval_seconds=ring.alpha.interval, loop_target_seconds=cfg.beta.loop_target_seconds)

    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping and (ticks == 0 or ring.ticks < ticks):
            ring.step()
            if tick_seconds:
                time.sleep(max(0.0, tick_seconds - ring.beta.last_loop_seconds))
    except KeyboardInterrupt:
        pass
    log.emit("stop", ticks=ring.ticks, pnl={k: round(v, 2) for k, v in ring.pnl.items()})
    log.close()
    return ring


if __name__ == "__main__":
    run()
