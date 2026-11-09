"""The academy: every agent is trained for its department's job before it starts work.

Each department has its own curriculum:

- Research (analysts) study **politics** and **the economy**: how elections,
  tariffs, sanctions, wars, stimulus and tax news move markets, and how
  inflation, interest-rate, jobs, GDP, unemployment and PMI surprises do. They
  also study **market regimes** (trending or choppy), their day job.
- Trend and Reversion (the decision makers) study **reading numbers** (is this
  chart an uptrend or a downtrend, from its return, moving-average gap and
  slope), **making decisions** (act only when the edge pays for the cost) and
  **how to trade** (take a trade only when win rate x payoff beats the loss).
- Risk studies **volatility** (when to cut size) and **drawdowns** (when a
  losing streak or a drop in equity means de-risk).

How it works: a course is a stream of practice lessons with known textbook
answers. Real life is noisy, so some practice answers are wrong on purpose.
Each agent learns every course with its own small model (online logistic
regression) at its own aptitude (learning speed, drawn from its agent id), then
sits an exam of fresh questions marked against the true textbook answer. It
needs 70% to pass and gets up to three rounds of lessons.

What training changes at work:
- every agent starts with trust in itself set by its exam average, so honours
  graduates start more confident than agents who struggled;
- decision makers hold (vote flat) on calls whose edge doesn't cover the cost;
- analysts read the news wire with what they learned and give the desk a
  macro view (bullish or bearish) per symbol.

Everything is simulated. Nothing here touches real money.
"""

import math
import random
from dataclasses import dataclass
from functools import lru_cache

PASS_MARK = 0.7
LESSONS_PER_ROUND = 100
EXAM_QUESTIONS = 40
MAX_ROUNDS = 3
LABEL_NOISE = 0.15  # share of practice answers that come out "wrong", as markets do
BANK_SIZE = 600
LIVE_COST = 0.1  # what a trade costs, in signal units (fees + slippage), for the decision gate

# Textbook effects on a stock market. Positive is bullish.
POLITICS = {
    "election_uncertainty": -0.6, "stable_government": 0.4, "new_tariffs": -0.8, "trade_deal": 0.7,
    "sanctions": -0.5, "stimulus_bill": 0.6, "war_escalation": -0.9, "ceasefire": 0.6, "tax_cut": 0.5,
    "regulation_crackdown": -0.5,
}
ECONOMY = {
    "inflation": -0.7, "interest_rate": -0.9, "jobs": 0.6, "gdp": 0.7, "unemployment": -0.6, "pmi": 0.5,
    "consumer_confidence": 0.4, "retail_sales": 0.4,
}


@dataclass(frozen=True)
class Course:
    key: str
    name: str
    teaches: str
    answers: tuple[str, str]  # (what a "no" means, what a "yes" means)


COURSES = {c.key: c for c in (
    Course("politics", "Politics", "How elections, trade wars, sanctions, wars and policy move markets",
           ("bearish", "bullish")),
    Course("economy", "Economy", "How inflation, rates, jobs, GDP and other data surprises move markets",
           ("bearish", "bullish")),
    Course("regimes", "Market regimes", "Tell a trending market from a choppy one", ("choppy", "trending")),
    Course("reading_numbers", "Reading numbers", "Read a chart's return, moving-average gap and slope",
           ("downtrend", "uptrend")),
    Course("decisions", "Making decisions", "Act only when the edge pays for the cost", ("hold", "act")),
    Course("trading", "How to trade", "Take a trade only when win rate x payoff beats the loss",
           ("skip", "take")),
    Course("volatility", "Volatility", "Know when the market is too wild for full size", ("full size", "cut size")),
    Course("drawdown", "Drawdowns", "De-risk after a big drop or a losing streak", ("stay", "de-risk")),
)}

CURRICULUM = {
    "research": ("politics", "economy", "regimes"),
    "trend": ("reading_numbers", "decisions", "trading"),
    "reversion": ("reading_numbers", "decisions", "trading"),
    "risk": ("volatility", "drawdown"),
}


# Lessons: (features, true answer). The practice copy may flip the answer (LABEL_NOISE).

def _politics(rng):
    events = rng.sample(sorted(POLITICS), rng.choice((1, 2)))
    x = {k: rng.uniform(0.2, 1.0) for k in events}
    return x, sum(POLITICS[k] * v for k, v in x.items()) > 0


def _economy(rng):
    releases = rng.sample(sorted(ECONOMY), rng.choice((1, 2)))
    x = {k: rng.gauss(0, 1) for k in releases}
    return x, sum(ECONOMY[k] * v for k, v in x.items()) > 0


def _path_features(prices):
    n = len(prices)
    fast = sum(prices[-n // 4:]) / (n // 4)
    slow = sum(prices) / n
    mean_i = (n - 1) / 2
    slope = sum((i - mean_i) * p for i, p in enumerate(prices)) / sum((i - mean_i) ** 2 for i in range(n))
    return {"return": 10 * (prices[-1] / prices[0] - 1), "ma_gap": 50 * (fast / slow - 1), "slope": 100 * slope / slow}


def _reading_numbers(rng):
    drift = rng.choice((-1, 1)) * rng.uniform(0.002, 0.006)
    prices, p = [], 100.0
    for _ in range(20):
        p *= math.exp(rng.gauss(drift, 0.01))
        prices.append(p)
    return _path_features(prices), drift > 0


def _regimes(rng):
    efficiency = rng.uniform(0, 1)
    return {"efficiency": efficiency, "volatility": rng.uniform(0, 1)}, efficiency > 0.35


def _decisions(rng):
    edge, cost = rng.uniform(0, 1), rng.uniform(0, 0.3)
    return {"edge": edge, "cost": cost}, 0.6 * edge > cost + 0.04


def _trading(rng):
    p, b = rng.uniform(0.3, 0.7), rng.uniform(0.5, 3.0)
    return {"win_rate": p, "payoff": b, "expected": p * b}, p * b - (1 - p) > 0


def _volatility(rng):
    ratio, rising = rng.uniform(0.3, 3.0), float(rng.random() < 0.5)
    return {"vol_ratio": ratio, "rising": rising, "rising_ratio": ratio * rising}, ratio > 1.0 or (ratio > 0.8 and rising)


def _drawdown(rng):
    dd, streak = rng.uniform(0, 0.1), rng.randint(0, 6)
    return {"drawdown": 20 * dd, "loss_streak": streak / 3}, dd > 0.04 or streak >= 4


LESSONS = {"politics": _politics, "economy": _economy, "regimes": _regimes, "reading_numbers": _reading_numbers,
           "decisions": _decisions, "trading": _trading, "volatility": _volatility, "drawdown": _drawdown}


@lru_cache(maxsize=None)
def lesson_bank(course: str) -> tuple:
    """A fixed bank of lessons per course, shared by every student (each draws its own order)."""
    rng = random.Random(f"academy:{course}")
    return tuple(LESSONS[course](rng) for _ in range(BANK_SIZE))


class Student:
    """One agent's model of one course: online logistic regression."""

    def __init__(self, rate: float):
        self.rate = rate
        self.weights: dict[str, float] = {}
        self.bias = 0.0

    def score(self, x: dict[str, float]) -> float:
        return self.bias + sum(self.weights.get(k, 0.0) * v for k, v in x.items())

    def answer(self, x: dict[str, float]) -> bool:
        return self.score(x) > 0

    def learn(self, x: dict[str, float], y: bool) -> None:
        z = max(-30.0, min(30.0, self.score(x)))
        err = (1.0 if y else 0.0) - 1 / (1 + math.exp(-z))
        self.bias += self.rate * err
        for k, v in x.items():
            self.weights[k] = self.weights.get(k, 0.0) + self.rate * err * v


@dataclass(frozen=True)
class Result:
    course: str
    score: float
    passed: bool
    rounds: int
    lessons: int


class Training:
    """An agent's diploma: its trained models and its transcript."""

    def __init__(self, department: str, students: dict[str, Student], results: dict[str, Result]):
        self.department = department
        self.students = students
        self.results = results

    @property
    def average(self) -> float:
        return sum(r.score for r in self.results.values()) / len(self.results)

    @property
    def graduated(self) -> bool:
        return all(r.passed for r in self.results.values())

    def knowledge(self, course: str) -> dict[str, float]:
        """What the agent learned in a course: its weight per input, strongest first."""
        w = self.students[course].weights
        return {k: round(w[k], 3) for k in sorted(w, key=lambda k: -abs(w[k]))}

    def transcript(self) -> dict:
        return {"graduated": self.graduated, "average": round(self.average, 3),
                "courses": {k: {"score": round(r.score, 3), "passed": r.passed, "rounds": r.rounds}
                            for k, r in self.results.items()}}


def _study(course: str, rng: random.Random, rate: float) -> tuple[Student, Result]:
    bank = lesson_bank(course)
    student = Student(rate)
    score, rounds, lessons = 0.0, 0, 0
    while rounds < MAX_ROUNDS:
        rounds += 1
        for _ in range(LESSONS_PER_ROUND):
            x, truth = bank[rng.randrange(BANK_SIZE)]
            student.learn(x, truth != (rng.random() < LABEL_NOISE))
            lessons += 1
        exam = [bank[rng.randrange(BANK_SIZE)] for _ in range(EXAM_QUESTIONS)]
        score = sum(student.answer(x) == truth for x, truth in exam) / EXAM_QUESTIONS
        if score >= PASS_MARK:
            break
    return student, Result(course, score, score >= PASS_MARK, rounds, lessons)


@lru_cache(maxsize=None)
def train(agent_id: str, department: str) -> Training:
    """Put one agent through its department's curriculum. Deterministic per agent id."""
    rng = random.Random(f"school:{agent_id}")
    aptitude = rng.uniform(0.05, 0.6)  # every agent learns at its own speed
    students, results = {}, {}
    for course in CURRICULUM[department]:
        students[course], results[course] = _study(course, rng, aptitude)
    return Training(department, students, results)


def enroll(agent) -> None:
    """Train an agent for its department's job and let the training shape how it starts work."""
    if getattr(agent, "training", None) is not None or agent.department not in CURRICULUM:
        return
    agent.training = train(agent.agent_id, agent.department)
    # Exam average 0..1 sets starting trust 0.75..1.25: always above the sit-out level.
    agent.brain.weight = 0.75 + 0.5 * agent.training.average


def should_act(training: Training | None, signal_strength: float, trust: float) -> bool:
    """A decision maker's trained gate: is this call's edge worth what the trade costs?"""
    if training is None or "decisions" not in training.students:
        return True
    return training.students["decisions"].answer({"edge": min(1.0, signal_strength * trust), "cost": LIVE_COST})


def read_headline(training: Training | None, headline: dict) -> float | None:
    """An analyst's read of one headline, -1 (bearish) .. 1 (bullish), from what it learned."""
    if training is None or headline["kind"] not in training.students:
        return None
    student = training.students[headline["kind"]]
    return math.tanh(student.score({headline["event"]: headline["value"]}) - student.bias)


def summarize(agents) -> dict:
    """School report for a group of agents: graduates, average exam and per-course averages."""
    trained = [a.training for a in agents if getattr(a, "training", None) is not None]
    courses: dict[str, list[float]] = {}
    for t in trained:
        for k, r in t.results.items():
            courses.setdefault(k, []).append(r.score)
    return {"trained": len(trained), "graduates": sum(t.graduated for t in trained),
            "exam_sum": round(sum(t.average for t in trained), 4),
            "course_sums": {k: round(sum(v), 4) for k, v in courses.items()}}
