"""Managers that act, not just report.

In Claude's agent program a lead agent *directs* its helper agents: it hands out work, checks results and
stops what isn't working. Atlas, Sage and Sentinel used to only report on their departments. Now, at the end
of every practice hour, each one acts on what its people did that hour:

- **Atlas** (Head of Trading) benches traders that lost most of their trades this hour (at least
  `MIN_TRADES` closed, win rate under `BENCH_BELOW`). A benched trader keeps practising and learning, and
  keeps its 15-minute reviews, but its vote doesn't reach the desk for the next hour.
- **Sage** (Head of Research) benches analysts whose market calls have mostly been wrong (brain hit rate
  under `ANALYST_BELOW` after `ANALYST_EXPERIENCE` calls).
- **Sentinel** (Head of Risk) flags a symbol for the next hour when the desk lost most of its trades in it
  this hour and lost money overall. New risk in a flagged symbol goes in at half size (trading/hooks.py).

No manager benches more than `MAX_BENCH_SHARE` of a department, so every department keeps its voice.
Everything a manager does is written down with a reason. Paper only.
"""

MIN_TRADES = 6
BENCH_BELOW = 0.35
ANALYST_BELOW = 0.45
ANALYST_EXPERIENCE = 300
MAX_BENCH_SHARE = 0.2
PAUSE_MIN_TRADES = 3
PAUSE_WIN_RATE = 0.34


class Managers:
    def __init__(self):
        self.benched: set[str] = set()
        self.paused: set[str] = set()
        self.last: dict | None = None
        self._counts: dict[str, tuple[int, int]] = {}  # trades won/lost at the last check, per agent

    def hour(self, agents, journal, since_tick: int, hour: int) -> dict:
        self.benched = set()
        actions = []

        # Atlas: traders that lost most of their trades this hour sit out the next one.
        by_dept: dict[str, list] = {}
        for a in agents:
            if a.role == "trader":
                w, lost = a.workspace.get("trade_wins", 0), a.workspace.get("trade_losses", 0)
                w0, l0 = self._counts.get(a.agent_id, (0, 0))
                self._counts[a.agent_id] = (w, lost)
                n = (w - w0) + (lost - l0)
                if n >= MIN_TRADES:
                    rate = (w - w0) / n
                    if rate < BENCH_BELOW:
                        by_dept.setdefault(a.department, []).append((rate, a))
        sizes = {}
        for a in agents:
            sizes[a.department] = sizes.get(a.department, 0) + 1
        for dept, bad in by_dept.items():
            bad.sort(key=lambda x: x[0])
            for rate, a in bad[:int(sizes[dept] * MAX_BENCH_SHARE)]:
                self.benched.add(a.agent_id)
                a.workspace["benched"] = a.workspace.get("benched", 0) + 1
                notes = a.workspace.setdefault("lessons", [])
                notes.append(f"Hour {hour}: Atlas benched me for an hour (won {rate:.0%} of my trades).")
                del notes[:-5]
        traders_benched = len(self.benched)
        if traders_benched:
            actions.append({"manager": "Atlas", "did": f"benched {traders_benched} traders for an hour",
                            "why": f"they won under {BENCH_BELOW:.0%} of at least {MIN_TRADES} trades this hour"})

        # Sage: analysts whose calls have mostly been wrong stop advising for an hour.
        analysts = [a for a in agents if a.role == "analyst"]
        wrong = sorted((a for a in analysts if a.brain.experience >= ANALYST_EXPERIENCE
                        and (a.brain.skill or 0) < ANALYST_BELOW), key=lambda a: a.brain.skill or 0)
        wrong = wrong[:int(len(analysts) * MAX_BENCH_SHARE)]
        for a in wrong:
            self.benched.add(a.agent_id)
            a.workspace["benched"] = a.workspace.get("benched", 0) + 1
        if wrong:
            actions.append({"manager": "Sage", "did": f"benched {len(wrong)} analysts for an hour",
                            "why": f"their market calls were right under {ANALYST_BELOW:.0%} of the time"})

        # Sentinel: pause symbols where the desk kept losing this hour.
        self.paused = set()
        for symbol, trades in journal.since(since_tick).items():
            if len(trades) >= PAUSE_MIN_TRADES:
                rate = sum(t["won"] for t in trades) / len(trades)
                if rate < PAUSE_WIN_RATE and sum(t["move"] for t in trades) < 0:
                    self.paused.add(symbol)
        if self.paused:
            actions.append({"manager": "Sentinel", "did": f"cut {', '.join(sorted(self.paused))} to half size for an hour",
                            "why": f"the desk won under {PAUSE_WIN_RATE:.0%} of its trades there and lost money"})

        self.last = {"hour": hour, "actions": actions, "benched": len(self.benched),
                     "paused": sorted(self.paused)}
        return self.last
