"""Long-term memory: what the agents learned survives a restart.

Claude's agent program gives agents memory that outlives a session (memory files the agent reads back when
it starts again). Before this, every restart of the desk wiped what 1,400 agents had learned: their trust in
their own strategy, the rules their 15-minute reviews wrote, their lessons and their trade record.

Now each engine shard writes its agents' learning to `memory-<shard>.json` every `SAVE_EVERY` ticks and when
it stops, and reads it back when it starts. Agents are matched by id, so a resized team keeps the memories
of the agents that stay. A missing, old or damaged file is ignored: the desk then starts fresh, as before.

What is kept per agent: brain (trust, experience, hits, recent results), its own rules and any rule change
still on trial, its lessons, its trade record and virtual P&L. Per desk: Brain Alpha's style weights and
the practice clock (hours, reviews, days). The paper account itself starts fresh on every run. Paper only.
"""

import json
import os
from pathlib import Path

VERSION = 1
SAVE_EVERY = 600  # ticks (5 minutes at 0.5 s ticks)
WORKSPACE_KEYS = ("trade_wins", "trade_losses", "trades_total", "reviews", "lessons", "kept", "undone", "benched")


def agent_state(a) -> dict:
    b = a.brain
    return {"brain": {"weight": b.weight, "experience": b.experience, "wins": b.wins, "memory": list(b.memory)},
            "rules": dict(a.rules), "trial": a.trial, "pnl": a.pnl,
            "workspace": {k: a.workspace[k] for k in WORKSPACE_KEYS if k in a.workspace}}


def save(desk, path: Path) -> None:
    data = {"version": VERSION, "tick": desk.ticks,
            "desk": {"hours": desk.hours, "reviews": desk.reviews, "days": desk.days,
                     "alpha": dict(desk.alpha.weights) if desk.alpha else None},
            "agents": {a.agent_id: agent_state(a) for a in desk.agents}}
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def restore(desk, path: Path) -> int:
    """Load saved learning into the desk's agents. Returns how many agents remembered."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            return 0
        saved = data["agents"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    restored = 0
    for a in desk.agents:
        s = saved.get(a.agent_id)
        if not isinstance(s, dict):
            continue
        try:
            b = s["brain"]
            a.brain.weight = float(b["weight"])
            a.brain.experience = int(b["experience"])
            a.brain.wins = int(b["wins"])
            a.brain.memory.clear()
            a.brain.memory.extend(float(x) for x in b["memory"])
            a.rules.update({k: float(v) for k, v in s["rules"].items() if k in a.rules})
            a.trial = s.get("trial")
            a.pnl = float(s.get("pnl", 0.0))
            a.workspace.update({k: v for k, v in s.get("workspace", {}).items() if k in WORKSPACE_KEYS})
        except (KeyError, TypeError, ValueError):
            continue
        restored += 1
    d = data.get("desk") or {}
    desk.hours = int(d.get("hours", 0))
    desk.reviews = int(d.get("reviews", 0))
    desk.days = int(d.get("days", 0))
    alpha = d.get("alpha")
    if desk.alpha and isinstance(alpha, dict) and set(alpha) == set(desk.alpha.weights):
        desk.alpha.weights = {k: float(v) for k, v in alpha.items()}
        desk.alpha._pnl_at_last = desk.style_pnl()
    return restored
