"""Chart methods from the classic trading books, each read straight off a price chart.

Every method here has two parts that are used both at the academy and at work:

- `features(prices, d)`: what the method looks at on a chart, for a trade in direction d (+1 long, -1 short);
- `rule(x)`: the method's textbook answer for those features (yes = the setup is there).

At the academy a lesson is a random practice chart, its features and the textbook answer. At work the agent
reads the same features off its own live chart and asks its trained model. When the model says "setup", the
agent takes the trade at full size.

The methods:

- W.D. Gann, angles: price riding above the 1x1 (45-degree) line drawn from the swing low (below it from the
  swing high, for shorts) is a strong trend. One bar of time = one typical bar of price.
- W.D. Gann, Square of 9: prices whose square root sits on a 45-degree step of the square (a quarter of a turn
  in square-root terms) are natural support and resistance; a stretched move into one tends to turn.
- W.D. Gann, time cycles: moves tend to turn 7, 14, 21, 30, 45 or 60 bars after a major high or low.
- Charles Dow, Dow theory: an uptrend makes higher highs and higher lows (a downtrend lower lows and lower highs).
- Nicolas Darvas, boxes: buy the break out of the top of a tight trading box.
- William O'Neil, CAN SLIM: buy leaders trading near their highs in an uptrend.
- R.N. Elliott and Fibonacci: in a trend, buy the pullback that retraces 38.2% to 61.8% of the last swing.
- Richard Wyckoff, the spring: price dips under support, then closes back above it (an upthrust, for shorts).
- Larry Williams, %R: buy when the close sits in the bottom 20% of the recent range, sell in the top 20%.

Paper trading only.
"""

import math

GANN_CYCLES = (7, 14, 21, 30, 45, 60)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _flip(prices, d):
    """Mirror a chart for shorts, so every method can be written for longs only."""
    return prices if d > 0 else [prices[0] ** 2 / p for p in prices]


def _stretch(prices):
    mean = sum(prices) / len(prices)
    sd = (sum((p - mean) ** 2 for p in prices) / len(prices)) ** 0.5
    return (prices[-1] - mean) / sd if sd else 0.0


# Trend methods.

def gann_angle_x(prices, d):
    p = _flip(prices, d)
    low_i = min(range(len(p)), key=p.__getitem__)
    bars = len(p) - 1 - low_i
    # Chart scale: one unit of price is half a typical bar, so the 1x1 climbs half a typical bar every bar.
    unit = 0.5 * sum(abs(b - a) for a, b in zip(p, p[1:])) / (len(p) - 1) or 1e-9
    line = p[low_i] + unit * bars  # the 1x1 (45-degree) line from the swing low
    return {"above_1x1": _clamp((p[-1] - line) / (unit * math.sqrt(bars + 1)), -3, 3), "bars": min(bars, 30) / 10}


def gann_angle_rule(x):
    return x["above_1x1"] > 0 and x["bars"] >= 0.3


def dow_x(prices, d):
    p = _flip(prices, d)
    half = len(p) // 2
    a, b = p[:half], p[half:]
    return {"higher_high": _clamp(50 * (max(b) / max(a) - 1), -3, 3),
            "higher_low": _clamp(50 * (min(b) / min(a) - 1), -3, 3)}


def dow_rule(x):
    return x["higher_high"] > 0 and x["higher_low"] > 0


def darvas_x(prices, d):
    p = _flip(prices, d)
    box = p[-max(4, len(p) // 2):-1]
    top, bottom = max(box), min(box)
    return {"breakout": _clamp(50 * (p[-1] / top - 1), -3, 3), "box_height": _clamp(25 * (top / bottom - 1), 0, 3)}


def darvas_rule(x):
    return x["breakout"] > 0 and x["box_height"] < 1.0  # a break out of a box under 4% tall


def oneil_x(prices, d):
    p = _flip(prices, d)
    return {"off_high": _clamp(50 * (1 - p[-1] / max(p)), 0, 3), "trend": _clamp(25 * (p[-1] / p[0] - 1), -3, 3)}


def oneil_rule(x):
    return x["off_high"] < 0.5 and x["trend"] > 0  # within 1% of the high, and up over the window


def elliott_x(prices, d):
    p = _flip(prices, d)
    high_i = max(range(len(p)), key=p.__getitem__)
    low_i = min(range(high_i + 1), key=p.__getitem__)
    swing = p[high_i] - p[low_i]
    retrace = (p[high_i] - p[-1]) / swing if swing > 0 and high_i < len(p) - 1 else 0.0
    return {"retrace": _clamp(retrace, 0, 1.5), "in_zone": 1 - 8 * abs(retrace - 0.5),
            "swing": _clamp(25 * swing / p[low_i], 0, 3)}


def elliott_rule(x):
    return 0.382 <= x["retrace"] <= 0.618 and x["swing"] > 0.5


# Reversion methods (d is the direction of the fade: +1 buys a drop, -1 sells a rise).

def square_of_9_x(prices, d):
    root = math.sqrt(prices[-1])
    steps = root / 0.25  # one 45-degree step of the square = 0.25 in square-root terms
    near = 1 - 2 * abs(steps - round(steps))  # 1 on a level, 0 halfway between two
    return {"near_level": near, "stretch": _clamp(-d * _stretch(prices), -3, 3)}


def square_of_9_rule(x):
    return x["near_level"] > 0.7 and x["stretch"] > 1.0


def gann_time_x(prices, d):
    p = _flip(prices, d)  # a buy fades a fall: count the bars since the high it fell from
    pivot = max(range(len(p)), key=p.__getitem__)
    bars = len(p) - 1 - pivot
    on_cycle = 1.0 if any(abs(bars - c) <= 1 for c in GANN_CYCLES) else 0.0
    return {"on_cycle": on_cycle, "stretch": _clamp(-d * _stretch(prices), -3, 3)}


def gann_time_rule(x):
    return x["on_cycle"] > 0 and x["stretch"] > 0.5


def wyckoff_x(prices, d):
    p = _flip(prices, d)
    support = min(p[:-3])
    dip = min(p[-3:])
    return {"dip_below": _clamp(50 * (1 - dip / support), -3, 3), "reclaim": _clamp(50 * (p[-1] / support - 1), -3, 3)}


def wyckoff_rule(x):
    return x["dip_below"] > 0 and x["reclaim"] > 0


def williams_x(prices, d):
    hi, lo = max(prices), min(prices)
    r = (hi - prices[-1]) / (hi - lo) if hi > lo else 0.5  # Williams %R as 0 (at the high) .. 1 (at the low)
    return {"r": 4 * (r if d > 0 else 1 - r) - 2}


def williams_rule(x):
    return x["r"] > 1.2  # close in the far 20% of the range


METHODS = {
    "gann_angles": (gann_angle_x, gann_angle_rule), "dow": (dow_x, dow_rule), "darvas": (darvas_x, darvas_rule),
    "oneil": (oneil_x, oneil_rule), "elliott": (elliott_x, elliott_rule),
    "gann_square9": (square_of_9_x, square_of_9_rule), "gann_time": (gann_time_x, gann_time_rule),
    "wyckoff": (wyckoff_x, wyckoff_rule), "williams": (williams_x, williams_rule),
}


def practice_chart(rng):
    """A random practice chart: trends, ranges, pullbacks and shakeouts, 12 to 60 bars long."""
    n = rng.randint(12, 60)
    drift, vol = rng.gauss(0, 0.004), rng.uniform(0.004, 0.02)
    p, prices = rng.uniform(20, 400), []
    turn = rng.randrange(n) if rng.random() < 0.5 else None
    for i in range(n):
        if i == turn:
            drift = -drift * rng.uniform(0.5, 2)
        p *= math.exp(rng.gauss(drift, vol))
        prices.append(p)
    return prices


def lesson(course):
    features, rule = METHODS[course]

    def make(rng):
        # Half the lessons show the setup and half don't, so a student can't pass by always saying "no".
        want = rng.random() < 0.5
        for _ in range(400):
            x = features(practice_chart(rng), rng.choice((1, -1)))
            if rule(x) == want:
                break
        return x, rule(x)
    return make
