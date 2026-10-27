"""Regime-switching ML: k-means clustering of market-state features.

Features per asset (computed on 15-minute bars over the last trading day):
- realised volatility of returns
- efficiency ratio: net move / total path (near 1 = trending, near 0 = choppy)

The detector periodically fits k-means (k = 4) on recent feature history, then
names each cluster by where its centroid sits: high or low volatility, and
trending or ranging. Assets are then classified by their nearest centroid.
Pure Python (no numpy), so it runs in the slim container.
"""

import itertools
import math
import random
import statistics

REGIMES = ("low_vol_trending", "low_vol_ranging", "high_vol_trending", "high_vol_ranging")


def kmeans(points: list[tuple[float, ...]], k: int, rng: random.Random, iters: int = 50):
    """Return (centroids, labels). k-means++ seeding, Lloyd iterations."""
    if len(points) < k:
        raise ValueError("need at least k points")
    centroids = [rng.choice(points)]
    while len(centroids) < k:
        d2 = [min(sum((p - c) ** 2 for p, c in zip(pt, cen)) for cen in centroids) for pt in points]
        total = sum(d2)
        if total == 0:
            centroids.append(rng.choice(points))
            continue
        r, acc = rng.random() * total, 0.0
        for pt, d in zip(points, d2):
            acc += d
            if acc >= r:
                centroids.append(pt)
                break
    labels = [0] * len(points)
    for _ in range(iters):
        new = [min(range(k), key=lambda j: sum((p - c) ** 2 for p, c in zip(pt, centroids[j]))) for pt in points]
        if new == labels and _ > 0:
            break
        labels = new
        for j in range(k):
            members = [pt for pt, lab in zip(points, labels) if lab == j]
            if members:
                centroids[j] = tuple(statistics.fmean(dim) for dim in zip(*members))
    return centroids, labels


def name_clusters(centroids: list[tuple[float, float]]) -> dict[int, str]:
    """Assign the 4 regime names to 4 centroids (vol, efficiency) by best overall fit."""
    vols = [c[0] for c in centroids]
    effs = [c[1] for c in centroids]
    vz = [(v - statistics.fmean(vols)) / (statistics.pstdev(vols) or 1) for v in vols]
    ez = [(e - statistics.fmean(effs)) / (statistics.pstdev(effs) or 1) for e in effs]

    def fit(name: str, j: int) -> float:
        return (vz[j] if name.startswith("high") else -vz[j]) + (ez[j] if name.endswith("trending") else -ez[j])

    best = max(itertools.permutations(range(len(centroids))),
               key=lambda perm: sum(fit(name, j) for name, j in zip(REGIMES, perm)))
    return {j: name for name, j in zip(REGIMES, best)}


def features(closes: list[float]) -> tuple[float, float] | None:
    if len(closes) < 10:
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    path = sum(abs(r) for r in rets)
    eff = abs(math.log(closes[-1] / closes[0])) / path if path else 0.0
    return statistics.pstdev(rets), eff


class RegimeDetector:
    def __init__(self, seed: int = 0, history: int = 3000):
        self.rng = random.Random(seed + 4)
        self.samples: list[tuple[float, float]] = []
        self.history = history
        self.centroids: list[tuple[float, float]] | None = None
        self.names: dict[int, str] = {}
        self._scale = (1.0, 1.0)
        self.fits = 0

    def observe(self, feat: tuple[float, float]) -> None:
        self.samples.append(feat)
        if len(self.samples) > self.history:
            del self.samples[: len(self.samples) - self.history]

    def _scaled(self, feat):
        return feat[0] / self._scale[0], feat[1] / self._scale[1]

    def fit(self) -> bool:
        if len(self.samples) < 40:
            return False
        # Standardise so volatility (tiny numbers) and efficiency (0..1) weigh equally.
        self._scale = (statistics.pstdev([s[0] for s in self.samples]) or 1.0,
                       statistics.pstdev([s[1] for s in self.samples]) or 1.0)
        points = [self._scaled(s) for s in self.samples]
        self.centroids, _ = kmeans(points, 4, self.rng)
        self.names = name_clusters(self.centroids)
        self.fits += 1
        return True

    def classify(self, feat: tuple[float, float]) -> str | None:
        if self.centroids is None:
            return None
        p = self._scaled(feat)
        j = min(range(len(self.centroids)), key=lambda i: sum((a - b) ** 2 for a, b in zip(p, self.centroids[i])))
        return self.names[j]
