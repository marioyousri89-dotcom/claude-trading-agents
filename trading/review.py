"""The 15-minute review: every trader looks back at its own closed trades and learns from its mistakes.

Each trader keeps a journal of the trades it closed (trading/agents.py). Every review period (15 minutes of
practice by default) it reads its own journal and asks three questions about its losers:

1. **Did I fight the news and the chart?** If trades opened against (or without) the reading room's view lost
   more often than trades that agreed with it, the agent raises its own bar: from now on it needs more
   agreement before it opens a trade.
2. **Did I trade on weak signals?** If its weak-signal trades lost more often than its strong ones, it raises
   its own minimum signal strength.
3. **Did I hang on after the news and chart turned?** If big losers were mostly trades the view had turned
   against, it exits sooner when that happens.

When its record is good and its bar is high, it relaxes a little so it keeps trading and keeps learning.
Every change is written down as a lesson in the agent's own workspace, so the 3D page and JARVIS can show
what each agent learned. No two agents end up with the same rules: each learns from its own trades only.

`share_best` is the daily step: the weakest traders in each department copy the rules and trust of that
department's best traders (by trade win rate and P&L), then keep learning on their own.

Paper only.
"""

JOURNAL_MAX = 200
MIN_TRADES = 4  # a review needs at least this many closed trades to say anything
STEP = 0.05
MAX_VIEW_NEED = 0.6
TREND_VIEW_NEED = 0.3
MAX_MIN_EDGE = 0.6
HOLD_AGREE = 0.25  # an open trade rides through signal wobbles while the view backs it at least this much
COPY_SHARE = 0.2  # the bottom 20% copy the top 20%


def starting_rules(department: str = "") -> dict:
    # view_need: how much the reading room must agree before a new trade (-0.25 = "not clearly against").
    # min_edge: the weakest signal the agent will trade on. exit_against: how clearly the view must turn
    # against an open trade before the agent gets out.
    # Trend traders start strict: their trades only pay when the market really leans their way. Reversion
    # traders fade moves, so they start only refusing trades the view clearly opposes.
    return {"view_need": TREND_VIEW_NEED if department == "trend" else -0.25, "min_edge": 0.0, "exit_against": 0.5}


def _win_rate(trades: list[dict]) -> float | None:
    return sum(t["won"] for t in trades) / len(trades) if trades else None


def review(agent, period: int) -> list[str]:
    """One review of the agent's journal. Changes its rules, returns the lessons it wrote (may be empty)."""
    trades = [t for t in agent.journal if not t.get("forced")] or agent.journal
    agent.journal = []
    agent.workspace["reviews"] = agent.workspace.get("reviews", 0) + 1
    if len(trades) < MIN_TRADES:
        return []
    rules = agent.rules
    lessons = []

    # 1. Fighting the reading room.
    against = [t for t in trades if t.get("agree", 0.0) <= 0]
    agreed = [t for t in trades if t.get("agree", 0.0) > 0]
    wa, wg = _win_rate(against), _win_rate(agreed)
    if wa is not None and (wg is None or wa < wg) and wa < 0.5 and rules["view_need"] < MAX_VIEW_NEED:
        rules["view_need"] = round(min(MAX_VIEW_NEED, rules["view_need"] + STEP), 3)
        lessons.append(f"Lost {1 - wa:.0%} of trades the news and chart didn't back: need more agreement "
                       f"(now {rules['view_need']:+.2f}).")

    # 2. Weak signals.
    edges = sorted(t.get("edge", 0.0) for t in trades)
    middle = edges[len(edges) // 2]
    weak = [t for t in trades if t.get("edge", 0.0) < middle]
    strong = [t for t in trades if t.get("edge", 0.0) >= middle]
    ww, ws = _win_rate(weak), _win_rate(strong)
    if ww is not None and ws is not None and ww < ws and ww < 0.5 and rules["min_edge"] < MAX_MIN_EDGE:
        rules["min_edge"] = round(min(MAX_MIN_EDGE, max(rules["min_edge"] + STEP, 0.5 * middle)), 3)
        lessons.append(f"Weak signals won only {ww:.0%}: skip signals under {rules['min_edge']:.2f}.")

    # 3. Holding on after the view turned: losers bigger than winners means exits came too late.
    losers = [t["move"] for t in trades if not t["won"]]
    winners = [t["move"] for t in trades if t["won"]]
    if losers and winners and -sum(losers) / len(losers) > 1.5 * sum(winners) / len(winners) \
            and rules["exit_against"] > 0.15:
        rules["exit_against"] = round(max(0.15, rules["exit_against"] - STEP), 3)
        lessons.append(f"Losers ran bigger than winners: exit once the view turns {rules['exit_against']:.2f} "
                       f"against.")

    # Doing well with a high bar: relax a little so it keeps trading and learning.
    rate = _win_rate(trades)
    if not lessons and rate is not None and rate >= 0.6 and rules["view_need"] > 0:
        rules["view_need"] = round(rules["view_need"] - STEP / 2, 3)
        lessons.append(f"Won {rate:.0%}: relax the bar a little (now {rules['view_need']:+.2f}).")

    if lessons:
        notes = agent.workspace.setdefault("lessons", [])
        notes.extend(f"Review {period}: {x}" for x in lessons)
        del notes[:-5]  # keep the five latest
    return lessons


def trade_record(agent) -> tuple[float, float]:
    """(trade win rate, P&L) for ranking. Agents without closed trades rank last."""
    w, l = agent.workspace.get("trade_wins", 0), agent.workspace.get("trade_losses", 0)
    return (w / (w + l) if w + l >= 10 else -1.0, agent.pnl)


def share_best(agents) -> dict[str, int]:
    """The daily step: in each department the weakest traders copy the best traders' rules and trust."""
    copied: dict[str, int] = {}
    by_dept: dict[str, list] = {}
    for a in agents:
        if a.role == "trader":
            by_dept.setdefault(a.department, []).append(a)
    for dept, members in by_dept.items():
        ranked = sorted(members, key=trade_record, reverse=True)
        n = int(len(ranked) * COPY_SHARE)
        if n == 0:
            continue
        for i, weak in enumerate(ranked[-n:]):
            best = ranked[i % n]
            weak.rules = dict(best.rules)
            weak.brain.weight = max(weak.brain.weight, best.brain.weight)
            notes = weak.workspace.setdefault("lessons", [])
            notes.append(f"Copied {best.agent_id}'s rules (one of the department's best).")
            del notes[:-5]
        copied[dept] = n
    return copied
