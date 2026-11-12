"""The Claude coach: once a practice hour, Claude reviews how the desk traded and tunes the traders' rules.

The agents' 15-minute reviews (trading/review.py) each look only at the agent's own few trades. The coach
looks at the whole desk, the way a head coach watches the game tape: per trading department, how many trades
were won, how many of the losers were opened against the news and chart or on weak signals, whether losers
ran bigger than winners, which symbols kept losing, and what the managers did. It also sees the advice it gave
last hour and what happened after it, so it learns from its own calls (the agent loop: act, check, adjust).

It answers with small, bounded changes to each department's rules (how much the reading room must agree,
the weakest signal worth trading, how fast to exit when the view turns), each with a one-line reason. Every
trader in that department applies them, and its own reviews keep fine-tuning from there. A change can never
move a rule by more than `MAX_CHANGE` per hour or past the review limits.

Claude runs on a background thread, so the desk never waits on it. It is on only when ANTHROPIC_API_KEY is
set and COACH is not "off". Paper only: the coach changes rules, it can't place an order.
"""

import json
import os
import threading

from . import review
from .reading_room import DEFAULT_MODEL

MAX_CHANGE = 0.1
LIMITS = {"view_need": (-0.5, review.MAX_VIEW_NEED), "min_edge": (0.0, review.MAX_MIN_EDGE),
          "exit_against": (0.15, 1.0)}
FIELDS = {"view_need": "view_need_change", "min_edge": "min_edge_change", "exit_against": "exit_against_change"}

SYSTEM = (
    "You are the head coach of a paper-trading desk of automated traders. Once an hour you get the desk's "
    "results for the hour. Traders in each department share three rules: view_need (how much the news-and-chart "
    "view must agree before a new trade, -1..1; higher = pickier), min_edge (weakest signal strength worth "
    "trading, 0..1), exit_against (how clearly the view must turn against an open trade before exiting, 0..1; "
    "lower = exit sooner). Diagnose why trades lost and propose small changes per department (each between "
    "-0.1 and 0.1, 0 when the rule is fine), with one short plain-English reason. Raising pickiness lifts the "
    "win rate but cuts the number of trades; don't choke a department that is winning. You also see your last "
    "hour's advice and what happened after it: if it hurt, reverse it.")

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["departments", "summary"], "properties": {
    "summary": {"type": "string"},
    "departments": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["department", "view_need_change", "min_edge_change", "exit_against_change", "why"],
        "properties": {"department": {"type": "string"}, "view_need_change": {"type": "number"},
                       "min_edge_change": {"type": "number"}, "exit_against_change": {"type": "number"},
                       "why": {"type": "string"}}}}}}


def apply(agents, advice: dict) -> dict[str, dict]:
    """Apply the coach's per-department changes to every trader in the department. Returns what changed."""
    changed = {}
    for d in advice.get("departments", []):
        dept = d.get("department")
        deltas = {}
        for rule, field in FIELDS.items():
            try:
                x = float(d.get(field, 0.0))
            except (TypeError, ValueError):
                x = 0.0
            x = max(-MAX_CHANGE, min(MAX_CHANGE, x))
            if abs(x) >= 0.005:
                deltas[rule] = round(x, 3)
        if not deltas:
            continue
        n = 0
        for a in agents:
            if a.role != "trader" or a.department != dept:
                continue
            for rule, x in deltas.items():
                lo, hi = LIMITS[rule]
                a.rules[rule] = round(max(lo, min(hi, a.rules[rule] + x)), 3)
            notes = a.workspace.setdefault("lessons", [])
            notes.append(f"Coach (Claude): {str(d.get('why', ''))[:140]}")
            del notes[:-5]
            n += 1
        if n:
            changed[dept] = {"traders": n, "changes": deltas, "why": str(d.get("why", ""))[:200]}
    return changed


class Coach:
    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._advice: dict | None = None
        self._wake = threading.Event()
        self.calls = 0
        self.errors = 0
        self.last_error = ""
        self.last_summary = ""
        self._thread = threading.Thread(target=self._run, name="claude-coach", daemon=True)
        self._thread.start()

    def request(self, report: dict) -> None:
        with self._lock:
            self._job = report
        self._wake.set()

    def take_advice(self) -> dict | None:
        with self._lock:
            advice, self._advice = self._advice, None
        return advice

    def status(self) -> dict:
        with self._lock:
            return {"claude": True, "model": self.model, "calls": self.calls, "errors": self.errors,
                    "last_error": self.last_error, "summary": self.last_summary}

    def _run(self) -> None:
        while True:
            self._wake.wait(timeout=5)
            self._wake.clear()
            with self._lock:
                job, self._job = self._job, None
            if job is None:
                continue
            try:
                advice = self._ask(job)
            except Exception as e:  # a failed call must never stop the desk; the agents' own reviews go on
                with self._lock:
                    self.errors += 1
                    self.last_error = f"{type(e).__name__}: {e}"[:200]
                continue
            with self._lock:
                self._advice = advice
                self.last_summary = str(advice.get("summary", ""))[:300]

    def _ask(self, report: dict) -> dict:
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=4096, system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(report)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        with self._lock:
            self.calls += 1
        if response.stop_reason == "refusal":
            raise RuntimeError("refused")
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)


def make_coach(env=os.environ) -> Coach | None:
    if env.get("COACH", "on").lower() in ("off", "0", "false", "no") or not env.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return Coach(anthropic.Anthropic(timeout=120.0, max_retries=2), env.get("COACH_MODEL", DEFAULT_MODEL))
