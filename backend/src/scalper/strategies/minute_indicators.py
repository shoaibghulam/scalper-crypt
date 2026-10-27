"""Causal indicator hypotheses; shared execution/risk belongs to the wrapper.

Definitions and departures from sources: docs/research/one-minute-expansion/
indicator-research.md. Inputs must be validated, closed one-minute candles.
"""

from functools import lru_cache
from math import log, sqrt

from scalper.exchange.market_data import Candle

_CS = "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/"
RECIPES = [
    (
        "Aroon consolidation release",
        _CS + "aroon",
        "Aroon25 Up reaches 100 with Down below30 after consolidation.",
    ),
    (
        "Vortex threshold transition",
        _CS + "vortex-indicator",
        "VI14+ crosses1.10 after VI- falls below0.90.",
    ),
    (
        "True Strength reacceleration",
        _CS + "true-strength-index",
        "Positive TSI25,13 crosses its EMA7 after a decline.",
    ),
    ("TRIX momentum transition", _CS + "trix", "TRIX15 crosses zero above its EMA9 signal."),
    (
        "Ultimate confirmed divergence",
        _CS + "ultimate-oscillator",
        "UO7,14,28 confirms two causally known divergent price lows.",
    ),
    (
        "Chande extreme recovery",
        "https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/cmo",
        "CMO20 crosses back above minus50.",
    ),
    (
        "Fisher turn confirmation",
        "https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/E-F/FisherTransform",
        "Fisher10 turns up below zero then price closes above trigger high.",
    ),
    (
        "Hull turning point",
        "https://alanhull.com/the-hull-moving-average/",
        "HMA20 turns up after two falling slopes with close above HMA.",
    ),
    (
        "Elder Force correction",
        _CS + "force-index",
        "Force2 recovers above zero while EMA22 rises.",
    ),
    (
        "Mass reversal bulge",
        _CS + "mass-index",
        "Mass9,25 falls below26.5 after exceeding27 in a downtrend, then price confirms.",
    ),
]


def _ema(values: list[float], period: int) -> list[float]:
    """First-observation seed, with wrapper supplying at least 200 warmup bars."""
    result = [values[0]]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(result[-1] + alpha * (value - result[-1]))
    return result


def _wma(values: list[float], period: int) -> list[float]:
    result = [values[0]] * len(values)
    for t in range(period - 1, len(values)):
        result[t] = sum((i + 1) * v for i, v in enumerate(values[t - period + 1 : t + 1])) / (
            period * (period + 1) / 2
        )
    return result


def _cross(a: list[float], b: list[float], t: int, up: bool = True) -> bool:
    return (a[t - 1] <= b[t - 1] and a[t] > b[t]) if up else (a[t - 1] >= b[t - 1] and a[t] < b[t])


def _divergence(
    c: list[float], h: list[float], low: list[float], uo: list[float]
) -> tuple[bool, bool]:
    """Pivots require two right-hand bars and are never backdated."""
    end = len(c) - 1
    results = []
    for long in (True, False):
        price = low if long else h
        pivots = [
            i
            for i in range(2, end - 1)
            if all(
                price[i] < price[j] if long else price[i] > price[j]
                for j in (i - 2, i - 1, i + 1, i + 2)
            )
        ]
        valid = False
        if len(pivots) >= 2:
            a, b = pivots[-2:]
            if 5 <= b - a <= 30 and b + 2 <= end <= b + 12:
                if long:
                    level = max(uo[a : b + 1])
                    valid = (
                        price[b] < price[a]
                        and uo[b] > uo[a]
                        and uo[a] < 30
                        and min(price[b + 1 :]) >= price[b]
                        and uo[end - 1] <= level < uo[end]
                    )
                else:
                    level = min(uo[a : b + 1])
                    valid = (
                        price[b] > price[a]
                        and uo[b] < uo[a]
                        and uo[a] > 70
                        and max(price[b + 1 :]) <= price[b]
                        and uo[end - 1] >= level > uo[end]
                    )
                # Only the first post-confirmation breakout consumes this pair.
                if valid:
                    valid = not any(
                        uo[j] > level if long else uo[j] < level for j in range(b + 2, end)
                    )
        results.append(valid)
    return results[0], results[1]


@lru_cache(maxsize=512)
def signals(bars: tuple[Candle, ...]) -> tuple[tuple[bool, bool], ...]:
    """Return ten long/short pairs in RECIPES order, using no future data."""
    if len(bars) < 200:
        return ((False, False),) * 10
    c = [b.close for b in bars]
    h = [b.high for b in bars]
    low = [b.low for b in bars]
    n = len(c)
    t = n - 1
    zero = [0.0] * n
    change = [0.0] + [c[i] - c[i - 1] for i in range(1, n)]
    tr = [h[0] - low[0]] + [
        max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1])) for i in range(1, n)
    ]
    up, down, vp, vm, uo, cmo = ([0.0] * n for _ in range(6))
    for i in range(28, n):
        hi = max(range(i - 25, i + 1), key=lambda j: (h[j], j))
        lo = min(range(i - 25, i + 1), key=lambda j: (low[j], -j))
        up[i], down[i] = 100 * (25 - i + hi) / 25, 100 * (25 - i + lo) / 25
        denom = sum(tr[i - 13 : i + 1])
        if denom:
            vp[i] = sum(abs(h[j] - low[j - 1]) for j in range(i - 13, i + 1)) / denom
            vm[i] = sum(abs(low[j] - h[j - 1]) for j in range(i - 13, i + 1)) / denom
        ratios = []
        for length in (7, 14, 28):
            denominator = sum(tr[i - length + 1 : i + 1])
            bp = sum(c[j] - min(low[j], c[j - 1]) for j in range(i - length + 1, i + 1))
            ratios.append(bp / denominator if denominator else 0.5)
        uo[i] = 100 * (4 * ratios[0] + 2 * ratios[1] + ratios[2]) / 7
        changes = change[i - 19 : i + 1]
        denom = sum(abs(v) for v in changes)
        cmo[i] = 100 * sum(changes) / denom if denom else 0
    # Single-use setup state is reconstructed causally from the supplied history.
    aroon = (False, False)
    arm = -100
    vortex = (False, False)
    arms = [-100, -100]
    for i in range(29, n):
        if up[i] < 50 and down[i] < 50:
            arm = i
        aroon = (
            i - arm <= 10 and up[i] == 100 and down[i] < 30,
            i - arm <= 10 and down[i] == 100 and up[i] < 30,
        )
        if any(aroon):
            arm = -100
        if vm[i - 1] >= 0.9 > vm[i]:
            arms[0] = i
        if vp[i - 1] >= 0.9 > vp[i]:
            arms[1] = i
        vortex = (
            i - arms[0] <= 5 and vp[i - 1] <= 1.1 < vp[i] and vm[i] < 0.9,
            i - arms[1] <= 5 and vm[i - 1] <= 1.1 < vm[i] and vp[i] < 0.9,
        )
        for side in range(2):
            if vortex[side]:
                arms[side] = -100
    numer = _ema(_ema(change, 25), 13)
    denom_series = _ema(_ema([abs(v) for v in change], 25), 13)
    tsi = [100 * a / b if b else 0 for a, b in zip(numer, denom_series, strict=False)]
    ts = _ema(tsi, 7)
    tsi_signal = (
        _cross(tsi, ts, t) and tsi[t] > 0 and any(tsi[j] < tsi[j - 1] for j in range(t - 3, t)),
        _cross(tsi, ts, t, False)
        and tsi[t] < 0
        and any(tsi[j] > tsi[j - 1] for j in range(t - 3, t)),
    )
    triple = _ema(_ema(_ema(c, 15), 15), 15)
    tx = [0.0] + [100 * (triple[i] / triple[i - 1] - 1) for i in range(1, n)]
    txs = _ema(tx, 9)
    trix_signal = (
        _cross(tx, zero, t) and tx[t] > txs[t],
        _cross(tx, zero, t, False) and tx[t] < txs[t],
    )
    median = [(a + b) / 2 for a, b in zip(h, low, strict=False)]
    normalized = [0.0] * n
    for i in range(9, n):
        window = median[i - 9 : i + 1]
        width = max(window) - min(window)
        normalized[i] = 2 * (median[i] - min(window)) / width - 1 if width else 0
    smooth = _ema(normalized, 5)
    clipped = [max(-0.999, min(0.999, x)) for x in smooth]
    fisher = [0.5 * log((1 + x) / (1 - x)) for x in clipped]
    fisher_signal = (
        fisher[t - 1] > fisher[t - 2]
        and fisher[t - 2] <= fisher[t - 3]
        and fisher[t - 1] < 0
        and c[t] > h[t - 1],
        fisher[t - 1] < fisher[t - 2]
        and fisher[t - 2] >= fisher[t - 3]
        and fisher[t - 1] > 0
        and c[t] < low[t - 1],
    )
    fast, slow = _wma(c, 10), _wma(c, 20)
    hull = _wma([2 * a - b for a, b in zip(fast, slow, strict=False)], int(sqrt(20)))
    hull_signal = (
        hull[t] > hull[t - 1] < hull[t - 2] < hull[t - 3] and c[t] > hull[t],
        hull[t] < hull[t - 1] > hull[t - 2] > hull[t - 3] and c[t] < hull[t],
    )
    force = _ema([v * b.volume for v, b in zip(change, bars, strict=False)], 2)
    trend = _ema(c, 22)
    force_signal = (
        _cross(force, zero, t) and trend[t] > trend[t - 1],
        _cross(force, zero, t, False) and trend[t] < trend[t - 1],
    )
    single = _ema([a - b for a, b in zip(h, low, strict=False)], 9)
    double = _ema(single, 9)
    ratio = [a / b if b else 1 for a, b in zip(single, double, strict=False)]
    mass = [sum(ratio[max(0, i - 24) : i + 1]) for i in range(n)]
    bulge, bias, ready = -100, 0, -100
    mass_signal = (False, False)
    for i in range(29, n):
        if mass[i - 1] <= 27 < mass[i]:
            bulge, bias, ready = i, (1 if c[i] < c[i - 20] else -1 if c[i] > c[i - 20] else 0), -100
        if i - bulge <= 30 and mass[i - 1] >= 26.5 > mass[i]:
            ready = i
            bulge = -100
        mass_signal = (
            i - ready <= 5 and bias == 1 and c[i] > h[i - 1],
            i - ready <= 5 and bias == -1 and c[i] < low[i - 1],
        )
        if any(mass_signal):
            ready = -100
    return (
        aroon,
        vortex,
        tsi_signal,
        trix_signal,
        _divergence(c, h, low, uo),
        (cmo[t - 1] <= -50 < cmo[t], cmo[t - 1] >= 50 > cmo[t]),
        fisher_signal,
        hull_signal,
        force_signal,
        mass_signal,
    )
