"""The org chart: four departments of agents, run by three managers.

Every agent belongs to one department and keeps its own workspace (its own
notes, last vote and score). Agents share the same platforms: one market feed,
one desk, one risk gate and one Jev gate.

Managers don't trade. Each tick they read their departments' votes and report
a summary, which the dashboard shows as the org chart and the 3D cluster view.
"""

from dataclasses import dataclass

from . import academy

MIN_DEPARTMENT_SIZE = 350


@dataclass(frozen=True)
class Department:
    key: str
    name: str
    role: str  # agent role the department staffs
    job: str


@dataclass(frozen=True)
class Manager:
    key: str
    name: str
    title: str
    departments: tuple[str, ...]


DEPARTMENTS = (
    Department("trend", "Trend Trading", "trader", "Momentum, moving-average cross and breakout traders"),
    Department("reversion", "Reversion Trading", "trader", "Mean-reversion traders who fade stretched moves"),
    Department("research", "Research", "analyst", "Regime analysts: trending or choppy, so the desk knows whom to trust"),
    Department("risk", "Risk", "risk", "Volatility monitors who vote to shrink exposure"),
)

MANAGERS = (
    Manager("atlas", "Atlas", "Head of Trading", ("trend", "reversion")),
    Manager("sage", "Sage", "Head of Research", ("research",)),
    Manager("sentinel", "Sentinel", "Head of Risk", ("risk",)),
)

DEPARTMENT_KEYS = tuple(d.key for d in DEPARTMENTS)


def department_sizes(count: int) -> dict[str, int]:
    """Split `count` agents evenly across the departments (earlier ones take the remainder)."""
    base, extra = divmod(count, len(DEPARTMENTS))
    return {d.key: base + (1 if i < extra else 0) for i, d in enumerate(DEPARTMENTS)}


def summarize(agents, votes: dict[str, float]) -> dict[str, dict]:
    """Per-department report: headcount, active voters, average vote, P&L, brains and each agent's latest vote.

    `votes` maps agent_id to the vote it cast this tick. Agents still warming up
    (not enough price history yet) or whose brain chose to sit out report None.
    """
    report = {d.key: {"agents": 0, "active": 0, "sitting_out": 0, "vote_sum": 0.0, "pnl": 0.0, "weight_sum": 0.0,
                      "wins": 0, "experience": 0, "held": 0, "macro_sum": 0.0, "macro_count": 0, "votes": [],
                      "traders": 0, "trades_hour_sum": 0, "trades_hour_min": None, "trades_total": 0,
                      "trade_wins": 0, "trade_losses": 0, "reviews_learned": 0,
                      "masters_used": {}}
              for d in DEPARTMENTS}
    members = {d.key: [] for d in DEPARTMENTS}
    for a in agents:
        members[a.department].append(a)
        dept = report[a.department]
        dept["held"] += a.workspace.get("held", 0)
        for course, n in a.workspace.get("masters", {}).items():
            dept["masters_used"][course] = dept["masters_used"].get(course, 0) + n
        if a.role == "trader":
            t = a.workspace.get("trades_this_hour", 0)
            dept["traders"] += 1
            dept["trades_hour_sum"] += t
            dept["trades_total"] += a.workspace.get("trades_total", 0)
            dept["trade_wins"] += a.workspace.get("trade_wins", 0)
            dept["trade_losses"] += a.workspace.get("trade_losses", 0)
            dept["reviews_learned"] += bool(a.workspace.get("lessons"))
            dept["trades_hour_min"] = t if dept["trades_hour_min"] is None else min(dept["trades_hour_min"], t)
        if a.macro_view is not None:
            dept["macro_sum"] += a.macro_view
            dept["macro_count"] += 1
        dept["agents"] += 1
        dept["pnl"] += a.pnl
        dept["weight_sum"] += a.brain.weight
        dept["wins"] += a.brain.wins
        dept["experience"] += a.brain.experience
        if a.brain.sitting_out:
            dept["sitting_out"] += 1
        v = votes.get(a.agent_id)
        if v is None:
            dept["votes"].append(None)
            continue
        dept["active"] += 1
        dept["vote_sum"] += v
        dept["votes"].append(round(v, 2))
    for dept in report.values():
        dept["avg_vote"] = round(dept.pop("vote_sum") / dept["active"], 4) if dept["active"] else None
        dept["avg_weight"] = round(dept.pop("weight_sum") / dept["agents"], 4) if dept["agents"] else None
        dept["pnl"] = round(dept["pnl"], 2)
        dept["macro_sum"] = round(dept["macro_sum"], 4)
    for key, dept in report.items():
        dept["school"] = academy.summarize(members[key])
        finish_school(dept)
    return report


def finish_school(dept: dict) -> None:
    """Turn a department's school sums into graduation rate, exam average and course averages."""
    school = dept["school"]
    n = school["trained"]
    school["avg_exam"] = round(school["exam_sum"] / n, 4) if n else None
    school["courses"] = {k: {"name": academy.COURSES[k].name, "avg": round(v / n, 4) if n else None}
                         for k, v in school["course_sums"].items()}
    dept["macro"] = round(dept["macro_sum"] / dept["macro_count"], 4) if dept["macro_count"] else None


def manager_reports(departments: dict[str, dict]) -> list[dict]:
    """What each manager tells the desk, read off its departments' reports."""
    out = []
    for m in MANAGERS:
        own = [departments[k] for k in m.departments if k in departments]
        agents = sum(d["agents"] for d in own)
        active = sum(d["active"] for d in own)
        pnl = round(sum(d["pnl"] for d in own), 2)
        if m.key == "atlas":
            trend, rev = departments.get("trend", {}), departments.get("reversion", {})
            call = ("favour trend" if abs(trend.get("avg_vote") or 0) >= abs(rev.get("avg_vote") or 0)
                    else "favour reversion")
        elif m.key == "sage":
            regime = departments.get("research", {}).get("avg_vote")
            call = "warming up" if regime is None else ("trending market" if regime > 0 else "choppy market")
            macro = departments.get("research", {}).get("macro")
            if macro is not None and abs(macro) >= 0.05:
                call += f", news {'bullish' if macro > 0 else 'bearish'}"
        else:
            exposure = departments.get("risk", {}).get("avg_vote")
            call = "warming up" if exposure is None else f"run {exposure:.0%} of normal size"
        trained = sum(d.get("school", {}).get("trained", 0) for d in own)
        graduates = sum(d.get("school", {}).get("graduates", 0) for d in own)
        exam = sum(d.get("school", {}).get("exam_sum", 0.0) for d in own)
        training = (f"{graduates}/{trained} graduated, exam avg {exam / trained:.0%}" if trained else "not trained")
        traders = sum(d.get("traders", 0) for d in own)
        if traders:
            mins = [d["trades_hour_min"] for d in own if d.get("trades_hour_min") is not None]
            avg = sum(d.get("trades_hour_sum", 0) for d in own) / traders
            training += f"; {avg:.0f} practice trades/agent this hour (min {min(mins) if mins else 0})"
        out.append({"manager": m.name, "title": m.title, "departments": list(m.departments),
                    "agents": agents, "active": active, "pnl": pnl, "call": call, "training": training})
    return out
