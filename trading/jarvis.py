"""JARVIS: the owner's voice assistant. She knows the whole application and reports on it live.

Two brains:
- With ANTHROPIC_API_KEY set and the `anthropic` package installed, she answers with Claude
  (JARVIS_MODEL, default claude-opus-5-5), given the app's handbook and a live snapshot.
- Otherwise she answers from her built-in handbook and the live snapshot, with no network calls.

Owner only:
- The dashboard login comes first (trading/webauth.py).
- Then the owner wakes her with a passphrase, spoken or typed. It comes from JARVIS_PASSPHRASE or,
  if that isn't set, the owner chooses it on first visit and it is stored hashed (PBKDF2) next to the
  events. Unlocking issues a separate signed cookie that lasts JARVIS_UNLOCK_HOURS (default 12).
- Wrong passphrases are rate limited (5 per client per 15 minutes). On a public address she answers
  nothing until a passphrase exists (fails closed).
- Questions are rate limited per client and capped in length.
- She never trades and never reads secrets: she only reads the same reports the dashboard shows.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from . import academy, masters, platforms
from .org import DEPARTMENTS, MANAGERS

UNLOCK_COOKIE = "jarvis_unlock"
MIN_PASSPHRASE_LENGTH = 8
MAX_QUESTION_CHARS = 500
QUESTIONS_PER_MINUTE = 20
UNLOCK_FAILURES = 5
FAILURE_WINDOW = 15 * 60
HISTORY_TURNS = 8
DEFAULT_MODEL = "claude-opus-5-5"


class RateLimiter:
    """At most `limit` events per `window` seconds per key, with a memory cap on tracked keys."""

    def __init__(self, limit: int, window: float, clock=time.time, max_keys: int = 1024):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self._hits: dict[str, deque] = defaultdict(deque)

    def _prune(self, q: deque) -> None:
        cutoff = self.clock() - self.window
        while q and q[0] < cutoff:
            q.popleft()

    def allowed(self, key: str) -> bool:
        q = self._hits.get(key)
        if q is None:
            return True
        self._prune(q)
        if not q:
            del self._hits[key]
            return True
        return len(q) < self.limit

    def hit(self, key: str) -> None:
        if len(self._hits) >= self.max_keys:
            for k in list(self._hits):
                self._prune(self._hits[k])
                if not self._hits[k]:
                    del self._hits[k]
        self._hits[key].append(self.clock())

    def clear(self, key: str) -> None:
        self._hits.pop(key, None)


def handbook() -> str:
    """Everything JARVIS knows about the application, as plain text."""
    lines = [
        "WHAT THIS APP IS",
        "A team of paper-trading AI agents. The market is simulated and every fill is fake: no real money moves.",
        "It runs as one cloud service (Render) or as Docker containers: 3 engine shards, the 8-bot ring and the "
        "Whole Matrix desk, plus a dashboard with password login at /, a 3D view at /3d and JARVIS at /jarvis.",
        "",
        "DEPARTMENTS (default 350 agents each, 1,400 in all; the /3d slider sets 350 to 2,500 per department)",
    ]
    for d in DEPARTMENTS:
        boss = next(m for m in MANAGERS if d.key in m.departments)
        lines.append(f"- {d.name}: {d.job}. Manager: {boss.name}, {boss.title}.")
    lines += [
        "",
        "MANAGERS: Atlas (Head of Trading) runs Trend and Reversion and says which style to favour. "
        "Sage (Head of Research) calls the market trending or choppy and reads the news. "
        "Sentinel (Head of Risk) says what share of normal size to run. They read their departments every 5 ticks.",
        "",
        "BRAINS: every agent has its own learning brain. It scores each call against what the market did, "
        "raises or lowers its trust, presses harder when trusted, softens when doubtful, and sits out after a losing "
        "run until it earns its way back.",
        "",
        "ACADEMY: every agent is trained for its job before it starts and sits exams (70% passes, up to 3 rounds, "
        "about 98% graduate). Exam average sets starting trust (0.75 to 1.25). Courses:",
    ]
    for dept, courses in academy.CURRICULUM.items():
        names = ", ".join(academy.COURSES[c].name for c in courses)
        lines.append(f"- {dept}: {names}")
    lines += ["", f"THE MASTERS ({len(masters.MASTERS)} legendary traders and methods, each a course and exam, and they advise live practice trades):"]
    for m in masters.MASTERS:
        lines.append(f"- {m.name} ({m.department}): {m.rule}. Known for: {m.known_for}.")
    lines += [
        "",
        "READING ROOM: every symbol's news (read by Claude when ANTHROPIC_API_KEY is set, else a finance "
        "lexicon) and chart (two time frames, plus Claude's chart read every 15 minutes) become one view. Traders "
        "only open trades the view backs. Every 15 minutes each trader reviews its own closed trades, writes "
        "lessons and tightens its own rules; once a day the weakest copy the best traders' rules.",
        "PRACTICE HOURS: training never stops. Every hour each Trend and Reversion trader makes at least 20 practice "
        "trades, and every agent gets refresher lessons and re-sits its exams.",
        "",
        "THE DESK AND SAFETY: a coordinator combines votes into one order per symbol. Hard risk limits have the final "
        "say (order size, position size, gross exposure, drawdown halt). Every proposal passes the Jev typed gate "
        "($100k trade cap, 0.5% slippage, $50k Polymarket depth). Brain Alpha re-weights strategies every hour.",
        "",
        "8-BOT RING: Watcher, Analyst, Risk Gate (5% drawdown halt), Executor, Reporter, Vault (secrets only from "
        "environment variables, locked in paper mode), Crypto Predictor (new token pools, skips rug risk), and the "
        "Polymarket bot (negative-risk squeeze, cross-book arbitrage, maker rebates). $1M paper capital.",
        "",
        "WHOLE MATRIX DESK: multi-timeframe trend confluence, k-means regime switching, cross-asset pairs "
        "(Gold/AUDUSD, Nasdaq/Bitcoin, Oil/USDCAD) and a news filter, on a simulated cross-asset market.",
        "",
        "REAL PLATFORMS (trading/platforms.py): connectors for " + ", ".join(p.name for p in platforms.PLATFORMS) +
        ". Every platform starts in paper mode: orders fill on JARVIS's own paper book at the platform's live "
        "prices. MetaTrader 5 and moomoo can also use the broker's own demo account. Real-money orders are locked "
        "in this build. The plan is 1 to 2 weeks of paper trading on live prices before anyone considers real money.",
        "",
        "SECURITY: password login with rate limits and signed cookies; JARVIS needs a second passphrase; strict "
        "Content-Security-Policy; API keys only in environment variables, never in code or logs.",
    ]
    return "\n".join(lines)


def snapshot(org: dict | None, state: dict | None) -> dict:
    """A compact live report for JARVIS, from the dashboard's /api/org and /api/state data."""
    org, state = org or {}, state or {}
    depts = {}
    for key, d in (org.get("departments") or {}).items():
        school = d.get("school") or {}
        depts[key] = {"name": d.get("name"), "agents": d.get("agents", 0), "active": d.get("active", 0),
                      "sitting_out": d.get("sitting_out", 0), "pnl": d.get("pnl", 0.0), "skill": d.get("skill"),
                      "trade_win_rate": d.get("trade_win_rate"), "learned": d.get("reviews_learned", 0),
                      "avg_trust": d.get("avg_weight"), "graduates": school.get("graduates", 0),
                      "trained": school.get("trained", 0), "exam_avg": school.get("avg_exam")}
    desk = state.get("jev_desk") or {}
    return {"tick": org.get("tick", 0), "agents_reporting": state.get("agents_reporting", 0),
            "departments": depts, "managers": org.get("managers") or [],
            "news": [n.get("headline", n) if isinstance(n, dict) else n for n in (org.get("news") or [])[:3]],
            "practice": org.get("practice"), "session_pnl": desk.get("session_pnl"),
            "jev": {"approved": desk.get("approved", 0), "rejected": desk.get("rejected", 0),
                    "approval_rate": desk.get("approval_rate")},
            "platforms": platforms.status()}


def _money(x) -> str:
    x = x or 0.0
    return f"{'-' if x < 0 else ''}${abs(x):,.0f}"


def _pct(x) -> str:
    return "not yet" if x is None else f"{x:.0%}"


class BuiltinBrain:
    """Answers from the handbook and the live snapshot. No network, no model."""

    name = "built-in"

    def answer(self, question: str, snap: dict, history=None) -> str:
        q = question.lower()
        has = lambda *words: any(re.search(rf"\b{w}", q) for w in words)  # noqa: E731
        depts = snap.get("departments") or {}
        if has("hello", "hi\\b", "hey", "good morning", "good evening"):
            return ("Hello. I'm JARVIS. Ask me how the desk is doing, about a department or manager, the academy, "
                    "the masters, the platforms, or safety.")
        if has("who are you", "what are you", "your name"):
            return ("I'm JARVIS, your assistant for this trading system. I know every department, manager, course and "
                    "platform, and I read the live reports. I only answer you, and I never place trades.")
        if has("platform", "broker", "etoro", "metatrader", "mt5", "moomoo", "polymarket", "real money", "live trad"):
            parts = []
            for p in snap.get("platforms") or []:
                keys = ("no keys needed for prices" if not p.get("needs_keys", True) else
                        "keys set" if p["configured"] else "no keys yet")
                parts.append(f"{p['name']}: {p['mode']} mode, {keys}")
            return ("Connected platforms: " + "; ".join(parts) + ". Real-money orders are locked. "
                    "Every order fills on my paper book at the platform's live prices, so we can practise for a "
                    "week or two before talking about real money.")
        for d in DEPARTMENTS:
            if has(d.key, d.name.split()[0].lower()) and d.key in depts:
                x = depts[d.key]
                return (f"{d.name} has {x['agents']:,} agents, {x['active']:,} voting and {x['sitting_out']:,} sitting "
                        f"out. P&L {_money(x['pnl'])}, calls right {_pct(x['skill'])}, "
                        + (f"trades won {_pct(x.get('trade_win_rate'))}, " if x.get("trade_win_rate") is not None else "")
                        + f"{x['graduates']:,} of {x['trained']:,} graduated with an exam average of {_pct(x['exam_avg'])}.")
        for m in snap.get("managers") or []:
            if has(m["manager"].lower()):
                return f"{m['manager']}, {m['title']}, says: {m['call']}. Training: {m['training']}. P&L {_money(m['pnl'])}."
        if has("manager", "atlas", "sage", "sentinel"):
            calls = "; ".join(f"{m['manager']}: {m['call']}" for m in snap.get("managers") or [])
            return ("The managers are Atlas for trading, Sage for research and Sentinel for risk. "
                    + (f"Right now: {calls}." if calls else "They haven't reported yet."))
        plain = re.sub(r"[^\w\s]", "", q)
        named = [m for m in masters.MASTERS
                 if re.search(rf"\b({m.course.split('_')[0]}|{re.escape(m.name.split()[-1].lower())})\b", plain)
                 or (m.course == "turtles" and "turtle" in plain)]
        if named:
            return " ".join(f"{m.name} teaches the {m.department} department: {m.rule}. Known for: {m.known_for}."
                            for m in named[:3])
        if has("master", "legend", "great trader"):
            by_dept = {}
            for m in masters.MASTERS:
                by_dept.setdefault(m.department, []).append(m.name.split(":")[0].split(" (")[0])
            parts = [f"{d.title()}: {', '.join(dict.fromkeys(names))}" for d, names in by_dept.items()]
            return f"{len(masters.MASTERS)} master courses teach the agents. " + ". ".join(parts) + "."
        if has("academy", "train", "school", "exam", "course", "learn", "practice"):
            grads = sum(x["graduates"] for x in depts.values())
            trained = sum(x["trained"] for x in depts.values())
            base = (f"{grads:,} of {trained:,} agents have graduated. " if trained else "")
            return (base + "Every agent is trained for its job and sits exams before it trades, then practises all the "
                    "time: each strategy trader makes at least 20 practice trades an hour, and every agent re-sits its "
                    "exams hourly.")
        if has("news", "headline"):
            news = snap.get("news") or []
            return ("Latest headlines: " + " | ".join(map(str, news))) if news else "No headlines yet."
        if has("risk", "safe", "secur", "protect", "danger"):
            return ("Safety has layers: hard risk limits on order size, position, exposure and drawdown; the Jev gate "
                    "checks every proposal; a 5% drawdown halt in the ring; and everything is paper. The app sits "
                    "behind a password, and I need your passphrase too.")
        if has("how does", "how do", "explain", "pipeline", "work"):
            return ("Prices come in, 1,400 agents in four departments vote, the managers summarise, the desk combines "
                    "the votes into one order per symbol, risk limits and the Jev gate check it, and the paper broker "
                    "fills it. Every agent's brain then learns from the result.")
        # Default: a status report.
        total = snap.get("agents_reporting", 0)
        if not total:
            return "The engines haven't reported yet. Give them a few seconds after start-up."
        jev = snap.get("jev") or {}
        calls = "; ".join(f"{m['manager']}: {m['call']}" for m in snap.get("managers") or [])
        return (f"Status at tick {snap.get('tick', 0):,}: {total:,} agents reporting, session P&L "
                f"{_money(snap.get('session_pnl'))}, Jev approved {jev.get('approved', 0):,} and rejected "
                f"{jev.get('rejected', 0):,}. {calls + '.' if calls else ''} Everything is paper.")


class ClaudeBrain:
    """Answers with Claude, given the handbook (cached) and the live snapshot."""

    name = "claude"

    def __init__(self, client, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model
        self.system = [{"type": "text", "cache_control": {"type": "ephemeral"}, "text": (
            "You are JARVIS, a calm, warm, sharp female assistant who speaks to one person: the owner of this "
            "trading system. Your replies are read aloud, so answer in one to four short plain sentences, with no "
            "lists, markdown or symbols. Use the live snapshot for numbers and never invent figures. You cannot "
            "place trades, change settings or reveal secrets; if asked, say so. Real-money trading is locked; "
            "recommend at least 1 to 2 weeks of paper trading on live prices first. Never give personal financial "
            "advice as certainty.\n\nHANDBOOK\n" + handbook())}]

    def answer(self, question: str, snap: dict, history=None) -> str:
        messages = list(history or [])
        messages.append({"role": "user", "content": f"LIVE SNAPSHOT\n{json.dumps(snap)}\n\nQUESTION\n{question}"})
        response = self.client.beta.messages.create(
            model=self.model, max_tokens=1024, system=self.system, messages=messages,
            output_config={"effort": "low"},  # spoken replies: keep them quick
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if response.stop_reason == "refusal":
            return "I can't help with that one."
        text = " ".join(b.text for b in response.content if b.type == "text").strip()
        return text or "I don't have an answer for that yet."


def make_brain(env=os.environ):
    """Claude when a key and the SDK are present, otherwise the built-in brain."""
    if not env.get("ANTHROPIC_API_KEY"):
        return BuiltinBrain()
    try:
        import anthropic
    except ImportError:
        return BuiltinBrain()
    return ClaudeBrain(anthropic.Anthropic(timeout=30.0, max_retries=1), env.get("JARVIS_MODEL", DEFAULT_MODEL))


class Jarvis:
    """Owner gate and conversation. The passphrase comes from JARVIS_PASSPHRASE, or the owner chooses it
    on first visit (signed in with the dashboard password) and it is stored hashed in `store`."""

    PBKDF2_ROUNDS = 200_000

    def __init__(self, passphrase: str | None, required: bool, brain=None, unlock_hours: float = 12,
                 secret: str | None = None, clock=time.time, store=None):
        if passphrase is not None and len(passphrase) < MIN_PASSPHRASE_LENGTH:
            raise SystemExit(f"JARVIS_PASSPHRASE must be at least {MIN_PASSPHRASE_LENGTH} characters.")
        self.required = required  # True when the app is reachable from the internet
        self.enabled = True
        self.brain = brain or BuiltinBrain()
        self.clock = clock
        self.ttl = int(unlock_hours * 3600)
        self.store = store
        self._secret = (secret or secrets.token_hex(32)).encode()
        self._salt = None  # set when the passphrase was chosen in the app (PBKDF2)
        self._passphrase_set = False
        if passphrase is not None:
            self._set_digest(hashlib.sha256(_normalize(passphrase).encode()).digest())
        elif store is not None:
            self._load(store)
        if not self._passphrase_set:
            self._set_digest(b"")
        self.unlock_failures = RateLimiter(UNLOCK_FAILURES, FAILURE_WINDOW, clock)
        self.questions = RateLimiter(QUESTIONS_PER_MINUTE, 60, clock)
        self._history: dict[str, deque] = {}

    def _set_digest(self, digest: bytes, salt: bytes | None = None) -> None:
        self._digest, self._salt = digest, salt
        self._passphrase_set = bool(digest)
        self._key = hmac.new(self._secret, b"jarvis" + digest, hashlib.sha256).digest()

    def _hash(self, phrase: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", _normalize(phrase).encode(), salt, self.PBKDF2_ROUNDS)

    def _load(self, store) -> None:
        try:
            data = json.loads(Path(store).read_text())
            self._set_digest(bytes.fromhex(data["hash"]), bytes.fromhex(data["salt"]))
        except (OSError, ValueError, KeyError):
            pass

    @classmethod
    def from_env(cls, public: bool, env=os.environ, store=None) -> "Jarvis":
        passphrase = env.get("JARVIS_PASSPHRASE") or None
        return cls(passphrase, public, make_brain(env), float(env.get("JARVIS_UNLOCK_HOURS", 12)),
                   env.get("SESSION_SECRET"), store=store)

    @property
    def needs_passphrase(self) -> bool:
        return self._passphrase_set

    @property
    def needs_setup(self) -> bool:
        """On the internet with no passphrase yet: the signed-in owner chooses one first."""
        return self.required and not self._passphrase_set

    def setup(self, phrase: str) -> bool:
        """Set the passphrase once, from the app. Refused if one is already set."""
        if not self.needs_setup:
            return False
        if len(_normalize(phrase)) < MIN_PASSPHRASE_LENGTH:
            raise ValueError(f"Choose a passphrase of at least {MIN_PASSPHRASE_LENGTH} characters.")
        salt = secrets.token_bytes(16)
        digest = self._hash(phrase, salt)
        if self.store is not None:
            tmp = f"{self.store}.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"salt": salt.hex(), "hash": digest.hex(), "rounds": self.PBKDF2_ROUNDS}, f)
            os.replace(tmp, self.store)
        self._set_digest(digest, salt)
        return True

    def status(self, unlocked: bool) -> dict:
        return {"enabled": self.enabled, "unlocked": unlocked, "brain": self.brain.name,
                "needs_passphrase": self.needs_passphrase, "needs_setup": self.needs_setup, "reason": None}

    def check_passphrase(self, attempt: str) -> bool:
        if not self._passphrase_set:
            return False
        got = (self._hash(attempt, self._salt) if self._salt else
               hashlib.sha256(_normalize(attempt).encode()).digest())
        return hmac.compare_digest(got, self._digest)

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def issue(self) -> str:
        payload = f"{int(self.clock()) + self.ttl}.{secrets.token_hex(8)}"
        return f"{payload}.{self._sign(payload)}"

    def unlocked(self, token: str | None) -> bool:
        if not self._passphrase_set:
            return not self.required  # local use with no passphrase; on the internet, set one first
        if not token or token.count(".") != 2:
            return False
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return False
        try:
            return int(payload.split(".", 1)[0]) > self.clock()
        except ValueError:
            return False

    def ask(self, client: str, question: str, snap: dict) -> str:
        """Answer one question. Caller has already checked login and unlock."""
        question = " ".join(str(question).split())[:MAX_QUESTION_CHARS]
        history = self._history.setdefault(client, deque(maxlen=HISTORY_TURNS * 2))
        if len(self._history) > 256:  # bound memory
            self._history.pop(next(iter(self._history)))
        try:
            reply = self.brain.answer(question, snap, list(history))
        except Exception:  # a network or API error must not take the page down
            reply = BuiltinBrain().answer(question, snap)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": reply})
        return reply


def _normalize(phrase: str) -> str:
    """Speech recognition varies case and punctuation, so compare words only."""
    return " ".join(re.sub(r"[^\w\s]", " ", phrase.lower()).split())
