"""The masters: ten of history's great traders, each turned into an academy course.

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

The rules are simplified into numbers so a model can learn them. This is
practice for paper trading only; nothing here touches real money.
"""

import math
from dataclasses import dataclass


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
           "tudor_jones": _tudor_jones, "thorp": _thorp}
