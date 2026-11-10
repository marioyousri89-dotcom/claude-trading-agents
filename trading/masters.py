"""The masters: history's great traders and the classic book methods, each turned into an academy course.

Every master's best known, documented rule becomes a course of practice
lessons and an exam, the same way as the other academy courses. A lesson is a
market situation (a few numbers) and the master's answer to it. Each agent
learns the rule with its own small model, sits the exam, and re-sits it every
practice hour. The departments study the masters whose style fits their job:

Trend (follow the move)
- Jesse Livermore: buy only when price breaks out to a new high on heavy volume.
- Richard Dennis and the Turtles: enter when price breaks its 20-day high or low.
- Ed Seykota: cut losses, ride winners. Hold while the trend holds and the stop isn't hit.
- Stanley Druckenmiller: bet big only when the trend and liquidity (the Fed) agree.

Reversion (fade the move)
- George Soros: reflexivity. Fade a boom once price is far above value and the buying is tiring.
- Jim Simons: trade the statistics. Fade a stretched price, but only if it snaps back fast.
- Warren Buffett: be greedy when others are fearful, and only with a margin of safety.

Risk (protect the money)
- Bruce Kovner: put the stop where you're proven wrong, then size so it risks about 1% of capital.
- Paul Tudor Jones: only take 5:1 reward to risk, and nothing good happens below the 200-day average.
- Ed Thorp: never bet more than the Kelly fraction says the edge is worth.

Plus the chart methods from the classic books (trading/methods.py): W.D. Gann's angles, Square of 9 and time
cycles, Dow theory, Darvas boxes, O'Neil's CAN SLIM, Elliott/Fibonacci pullbacks, the Wyckoff spring and Larry
Williams' %R. At work (bottom of this file) every agent uses what it learned on its own practice trades.

The rules are simplified into numbers so a model can learn them. This is
practice for paper trading only; nothing here touches real money.
"""

import math
from dataclasses import dataclass

from . import methods


@dataclass(frozen=True)
class Master:
    course: str
    name: str
    era: str
    known_for: str
    rule: str
    department: str
    answers: tuple[str, str]  # (what a "no" means, what a "yes" means)


MASTERS = (
    Master("livermore", "Jesse Livermore", "1877-1940", "Called the 1907 and 1929 crashes",
           "Buy only when price breaks to a new high on heavy volume", "trend", ("wait", "buy the breakout")),
    Master("turtles", "Richard Dennis", "1949-", "Turned $1,600 into a fortune and taught the Turtles",
           "Enter when price breaks its 20-day high or low", "trend", ("stand aside", "enter")),
    Master("seykota", "Ed Seykota", "1946-", "Pioneer of computer trend following",
           "Cut losses, ride winners: hold while the trend holds and the stop isn't hit", "trend", ("exit", "hold")),
    Master("druckenmiller", "Stanley Druckenmiller", "1953-", "30 years without a losing year",
           "Bet big only when the trend and liquidity agree", "trend", ("small", "press the bet")),
    Master("soros", "George Soros", "1930-", "Broke the Bank of England in 1992",
           "Reflexivity: fade a boom once price is far above value and buying is tiring", "reversion",
           ("leave it", "fade the boom")),
    Master("simons", "Jim Simons", "1938-2024", "Medallion fund, about 66% a year before fees",
           "Fade a stretched price, but only if it usually snaps back fast", "reversion", ("skip", "fade")),
    Master("buffett", "Warren Buffett", "1930-", "Six decades of compounding at Berkshire Hathaway",
           "Be greedy when others are fearful, with a margin of safety", "reversion", ("wait", "buy the fear")),
    Master("kovner", "Bruce Kovner", "1945-", "Built Caxton into a giant macro fund",
           "Stop where you're proven wrong, size so it risks about 1% of capital", "risk", ("ok", "too big")),
    Master("tudor_jones", "Paul Tudor Jones", "1954-", "Tripled his money in the 1987 crash",
           "Take only 5:1 reward to risk, and never trade long below the 200-day average", "risk",
           ("block", "approve")),
    Master("thorp", "Ed Thorp", "1932-", "Beat blackjack, then the market, with the Kelly formula",
           "Never bet more than the Kelly fraction says the edge is worth", "risk", ("ok", "too big")),
    # The chart methods from the classic books (trading/methods.py), read straight off the chart.
    Master("gann_angles", "W.D. Gann: angles", "1878-1955", "Wrote 'Truth of the Stock Tape' and 'How to Make Profits'",
           "A trend is strong while price rides above the 1x1 (45-degree) line from the swing low", "trend",
           ("weak", "strong trend")),
    Master("dow", "Charles Dow", "1851-1902", "Founded the Wall Street Journal and the Dow averages",
           "An uptrend makes higher highs and higher lows", "trend", ("no trend", "trend")),
    Master("darvas", "Nicolas Darvas", "1920-1977", "Wrote 'How I Made $2,000,000 in the Stock Market'",
           "Buy the break out of the top of a tight box", "trend", ("wait", "buy the box")),
    Master("oneil", "William O'Neil", "1933-2023", "Wrote 'How to Make Money in Stocks' (CAN SLIM)",
           "Buy leaders trading near their highs in an uptrend", "trend", ("pass", "buy the leader")),
    Master("elliott", "R.N. Elliott (Fibonacci)", "1871-1948", "Wrote 'The Wave Principle'",
           "In a trend, buy the pullback that retraces 38.2% to 61.8% of the last swing", "trend",
           ("wait", "buy the pullback")),
    Master("gann_square9", "W.D. Gann: Square of 9", "1878-1955", "Gann's square of numbers for price levels",
           "A stretched move into a Square of 9 level tends to turn", "reversion", ("no level", "fade at level")),
    Master("gann_time", "W.D. Gann: time cycles", "1878-1955", "'When time is up, price will reverse'",
           "Moves tend to turn 7, 14, 21, 30, 45 or 60 bars after a major high or low", "reversion",
           ("not yet", "time to turn")),
    Master("wyckoff", "Richard Wyckoff", "1873-1934", "Wrote 'Studies in Tape Reading', founded the Wyckoff method",
           "The spring: price dips under support, then closes back above it", "reversion", ("no", "spring")),
    Master("williams", "Larry Williams", "1942-", "Won the 1987 world futures cup turning $10,000 into $1.1 million",
           "%R: buy when the close is in the bottom 20% of the range, sell in the top 20%", "reversion",
           ("neutral", "extreme")),
)

BY_COURSE = {m.course: m for m in MASTERS}


def courses_for(department: str) -> tuple[str, ...]:
    return tuple(m.course for m in MASTERS if m.department == department)


# Lessons: (features, the master's answer). Features are scaled to roughly -2..2 so a small model can learn them.

def _livermore(rng):
    gap = rng.uniform(-0.04, 0.04)  # close vs the prior high, as a fraction
    volume = rng.uniform(0.5, 2.5)  # volume vs its average
    return {"breakout": 50 * gap, "volume": volume - 1.5}, gap > 0 and volume > 1.5


def _turtles(rng):
    prices, p = [], 100.0
    drift = rng.gauss(0, 0.004)
    for _ in range(21):
        p *= math.exp(rng.gauss(drift, 0.012))
        prices.append(p)
    hi, lo, last = max(prices[:-1]), min(prices[:-1]), prices[-1]
    beyond = max(last / hi - 1, 1 - last / lo)  # how far past the 20-day channel, either side
    return {"past_channel": 50 * beyond}, beyond > 0


def _seykota(rng):
    pnl_r = rng.uniform(-2.0, 4.0)  # open profit in R (multiples of the planned risk)
    trend = rng.uniform(-1.0, 1.0)  # price vs its trend line, + means the trend still holds
    return {"pnl_r": pnl_r / 2, "trend": 2 * trend}, trend > 0 and pnl_r > -1


def _druckenmiller(rng):
    trend, liquidity = rng.uniform(-1, 1), rng.uniform(-1, 1)  # liquidity: + is the Fed easing
    return {"trend": trend, "liquidity": liquidity}, trend + liquidity > 1.0


def _soros(rng):
    over_value = rng.uniform(-0.2, 0.8)  # price / value - 1
    tiring = rng.uniform(-1, 1)  # + means the buying is slowing down
    return {"over_value": 3 * over_value, "tiring": tiring}, over_value > 0.3 and tiring > 0


def _simons(rng):
    stretch = abs(rng.gauss(0, 1.6))  # how many standard deviations price is from its mean
    half_life = rng.uniform(1, 20)  # days it usually takes to close half the gap
    return {"stretch": stretch, "half_life": half_life / 10}, stretch > 1.5 + half_life / 20


def _buffett(rng):
    fear, margin = rng.uniform(0, 1), rng.uniform(-0.3, 0.6)  # fear 0..1, margin = value / price - 1
    return {"fear": 2 * fear, "margin": 4 * margin}, fear + 2 * margin > 1.0


def _kovner(rng):
    stop = rng.uniform(0.01, 0.08)  # distance to the stop, fraction of price
    size = rng.uniform(0.05, 0.5)  # position, fraction of capital
    risk = stop * size
    return {"stop": 25 * stop, "size": 4 * size, "risk": 100 * risk}, risk > 0.01


def _tudor_jones(rng):
    reward_risk = rng.uniform(0.5, 10)
    vs_200 = rng.uniform(-0.15, 0.15)  # price vs the 200-day average
    return {"reward_risk": reward_risk / 5, "vs_200": 10 * vs_200}, reward_risk >= 5 and vs_200 > 0


def _thorp(rng):
    win, payoff = rng.uniform(0.4, 0.7), rng.uniform(0.8, 2.5)
    kelly = max(0.0, win - (1 - win) / payoff)
    bet = rng.uniform(0, 0.4)
    return {"bet": 5 * bet, "kelly": 5 * kelly}, bet > kelly


LESSONS = {"livermore": _livermore, "turtles": _turtles, "seykota": _seykota, "druckenmiller": _druckenmiller,
           "soros": _soros, "simons": _simons, "buffett": _buffett, "kovner": _kovner,
           "tudor_jones": _tudor_jones, "thorp": _thorp, **{k: methods.lesson(k) for k in methods.METHODS}}


# ---------------------------------------------------------------------------------------------------------------
# At work: the masters' rules shape real practice-trade decisions.
#
# Each agent asks its *own* trained model of a master (what it learned at the academy, so a weak student follows
# the rule badly) about the live market in front of it, using the same inputs as the lessons:
#
# - Trend traders: Livermore and the Turtles turn a call that comes with a breakout into a full-size position;
#   Druckenmiller presses to full size when the trend and the news agree; Seykota closes a position whose trend
#   broke or whose loss passed one R (cut losses), and keeps winners running.
# - Reversion traders: Simons skips fades that aren't stretched enough for how slowly the price snaps back;
#   Soros fades a tiring boom at full size; Buffett buys real fear with a margin of safety at full size.
# - Risk monitors: Kovner cuts exposure until the stop risks about 1% of capital; Tudor Jones cuts exposure while
#   price is below its long average; Thorp cuts exposure the monitor's own track record can't justify.
#
# Every time a master changes a decision the agent notes it in its workspace ("masters": course -> count).

R_MOVE = 0.02  # one R: the move a position's planned risk covers (2% of price)
POSITION_SHARE = 0.2  # a full-size position, as a share of capital (desk: 20k max per symbol on 100k)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _asks(agent, course, x) -> bool | None:
    training = getattr(agent, "training", None)
    if training is None or course not in training.students:
        return None
    return training.students[course].answer(x)


def _note(agent, course) -> None:
    used = agent.workspace.setdefault("masters", {})
    used[course] = used.get(course, 0) + 1


def _returns(prices):
    return [b / a - 1 for a, b in zip(prices, prices[1:])]


def _livermore_x(prices, d):
    prior = prices[:-1]
    gap = prices[-1] / max(prior) - 1 if d > 0 else 1 - prices[-1] / min(prior)
    rets = [abs(r) for r in _returns(prices)]
    avg = sum(rets[:-1]) / max(1, len(rets) - 1)
    volume = _clamp(rets[-1] / avg, 0.5, 2.5) if avg else 1.0  # activity on the last move, a stand-in for volume
    return {"breakout": 50 * gap, "volume": volume - 1.5}


def _turtles_x(prices):
    prior = prices[:-1]
    beyond = max(prices[-1] / max(prior) - 1, 1 - prices[-1] / min(prior))
    return {"past_channel": 50 * beyond}


def _trend(prices):
    fast = sum(prices[-max(2, len(prices) // 4):]) / max(2, len(prices) // 4)
    return fast / (sum(prices) / len(prices)) - 1


def _half_life(prices):
    mean = sum(prices) / len(prices)
    dev = [p - mean for p in prices]
    num = sum(a * b for a, b in zip(dev, dev[1:]))
    den = sum(a * a for a in dev[:-1])
    phi = num / den if den else 0.0
    if phi <= 0:
        return 1.0
    if phi >= 1:
        return 20.0
    return _clamp(math.log(0.5) / math.log(phi), 1.0, 20.0)


TREND_METHODS = ("gann_angles", "dow", "darvas", "oneil", "elliott")
REVERSION_METHODS = ("gann_square9", "gann_time", "wyckoff", "williams")


def _chart_says(agent, course, prices, d) -> bool:
    """Ask the agent's model of a chart method whether its setup is on the live chart."""
    if len(prices) < 8:
        return False
    if _asks(agent, course, methods.METHODS[course][0](prices, d)):
        _note(agent, course)
        return True
    return False


def advise_trader(agent, value: float) -> float:
    """Let the trader's masters adjust its position call (-1..1). Returns the new call."""
    prices = list(agent.prices)
    if len(prices) < 3:
        return value
    d = 1.0 if value > 0 else -1.0 if value < 0 else 0.0
    if agent.department == "trend":
        # Seykota: manage the open position first. Cut a loser or a broken trend, let a winner run.
        held = 1.0 if agent.position > 0 else -1.0 if agent.position < 0 else 0.0
        entry = agent.workspace.get("entry")
        if held and entry and d in (0.0, held):
            pnl_r = held * (prices[-1] / entry - 1) / R_MOVE
            trend = _clamp(held * 100 * _trend(prices), -1, 1)
            hold = _asks(agent, "seykota", {"pnl_r": _clamp(pnl_r, -2, 4) / 2, "trend": 2 * trend})
            if hold is False:
                _note(agent, "seykota")
                return 0.0
            if hold and d == 0.0:
                _note(agent, "seykota")
                return agent.position  # ride the winner instead of stepping aside
        if not d or abs(value) >= 1.0:
            return value
        if _asks(agent, "livermore", _livermore_x(prices, d)):
            _note(agent, "livermore")
            return d
        if _asks(agent, "turtles", _turtles_x(prices)) and d * (prices[-1] - prices[-2]) > 0:
            _note(agent, "turtles")
            return d
        for course in TREND_METHODS:
            if _chart_says(agent, course, prices, d):
                return d
        news = getattr(agent, "news_view", None) or 0.0
        trend = _clamp(d * 50 * (prices[-1] / prices[0] - 1), -1, 1)
        if _asks(agent, "druckenmiller", {"trend": trend, "liquidity": _clamp(d * news, -1, 1)}):
            _note(agent, "druckenmiller")
            return d
        return value
    if agent.department == "reversion" and d:
        mean = sum(prices) / len(prices)
        sd = (sum((p - mean) ** 2 for p in prices) / len(prices)) ** 0.5
        if not sd:
            return value
        stretch = abs(prices[-1] - mean) / sd
        if _asks(agent, "simons", {"stretch": stretch, "half_life": _half_life(prices) / 10}) is False:
            _note(agent, "simons")
            return 0.0
        for course in REVERSION_METHODS:
            if _chart_says(agent, course, prices, d):
                return d
        rets = _returns(prices)
        if d < 0:  # fading a rise
            avg = sum(rets) / len(rets)
            spread = (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5 or 1.0
            tiring = _clamp((avg - rets[-1]) / spread, -1, 1)
            # x10: gaps over a short window are small, so a 3% stretch here counts like a 30% overvaluation.
            if _asks(agent, "soros", {"over_value": 3 * (prices[-1] / mean - 1) * 10, "tiring": tiring}):
                _note(agent, "soros")
                return -1.0
        else:  # buying a drop
            fear = _clamp((1 - prices[-1] / max(prices)) / 0.05, 0, 1)
            if _asks(agent, "buffett", {"fear": 2 * fear, "margin": 4 * (mean / prices[-1] - 1) * 10}):
                _note(agent, "buffett")
                return 1.0
    return value


def advise_risk(agent, exposure: float) -> float:
    """Let a risk monitor's masters trim its exposure vote (0..1). Returns the new vote."""
    prices = list(agent.prices)
    if len(prices) < 3 or exposure <= 0:
        return exposure
    rets = _returns(prices)
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5
    # Kovner: the stop sits two typical moves away; shrink size until that stop risks about 1% of capital.
    stop = _clamp(2 * vol, 0.01, 0.08)
    for _ in range(3):
        size = POSITION_SHARE * exposure
        if not _asks(agent, "kovner", {"stop": 25 * stop, "size": 4 * size, "risk": 100 * stop * size}):
            break
        exposure *= 0.75
        _note(agent, "kovner")
    # Tudor Jones: nothing good happens below the long average. Assume a qualifying 5:1 setup, check the trend.
    vs_long = _clamp(prices[-1] / (sum(prices) / len(prices)) - 1, -0.15, 0.15)
    if _asks(agent, "tudor_jones", {"reward_risk": 1.0, "vs_200": 10 * vs_long}) is False:
        exposure *= 0.75
        _note(agent, "tudor_jones")
    # Thorp: the monitor's own record is its edge. Don't bet more than Kelly says that edge is worth.
    brain = agent.brain
    wins = [m for m in brain.memory if m > 0]
    losses = [-m for m in brain.memory if m < 0]
    if brain.experience >= 30 and wins and losses:
        p = len(wins) / (len(wins) + len(losses))
        b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
        kelly = _clamp(p - (1 - p) / b, 0, 0.4)
        if _asks(agent, "thorp", {"bet": 5 * POSITION_SHARE * exposure, "kelly": 5 * kelly}):
            exposure *= 0.75
            _note(agent, "thorp")
    return exposure
