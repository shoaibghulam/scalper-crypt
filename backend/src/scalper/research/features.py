"""Causal NumPy features, cached by indicator parameters for a single dataset."""

from collections import OrderedDict

import numpy as np
from numba import njit

from scalper.research.data.models import Dataset
from scalper.research.schema import Condition, Operand, StrategyDefinition


@njit(cache=True)
def smooth(x, period, alpha):
    out = np.full(len(x), np.nan)
    total = 0.0
    count = 0
    previous = np.nan
    for i in range(len(x)):
        if not np.isfinite(x[i]):
            total, count, previous = 0.0, 0, np.nan
            continue
        if count < period:
            total += x[i]
            count += 1
            if count == period:
                previous = total / period
        else:
            previous = alpha * x[i] + (1 - alpha) * previous
        out[i] = previous
    return out


def rolling(x, period, operation="mean"):
    out = np.full(len(x), np.nan)
    if len(x) >= period:
        windows = np.lib.stride_tricks.sliding_window_view(x, period)
        if operation == "wma":
            values = windows @ np.arange(1, period + 1) / (period * (period + 1) / 2)
        else:
            values = getattr(np, operation)(windows, axis=1)
        out[period - 1 :] = values
    return out


def divide(a, b, default=np.nan):
    return np.divide(a, b, out=np.full_like(a, default), where=b != 0)


class Features:
    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.ohlcv = np.array(
            [[b.open, b.high, b.low, b.close, b.volume] for b in dataset.bars], dtype=float
        )
        self.timestamps = np.array([b.timestamp for b in dataset.bars], dtype=np.int64)
        self.cache = OrderedDict()
        # Bounded cache: large searches cannot retain every period forever.
        self.cache_limit = max(8, min(256, 128_000_000 // max(8, len(dataset.bars) * 8)))

    def operand(self, operand: Operand):
        if operand.indicator is None:
            return np.full(len(self.ohlcv), operand.value)
        return self.indicator(operand.indicator, operand.period)

    def indicator(self, name, p=14):
        key = name, p
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        _o, h, low, c, v = self.ohlcv.T
        previous = np.r_[np.nan, c[:-1]]
        delta = c - previous
        if name == "close":
            result = c
        elif name == "volume":
            result = v
        elif name in {"sma", "wma", "volume_sma", "volatility"}:
            source = (
                v
                if name == "volume_sma"
                else self.indicator("returns")
                if name == "volatility"
                else c
            )
            result = rolling(
                source, p, "wma" if name == "wma" else "std" if name == "volatility" else "mean"
            )
        elif name == "ema":
            result = smooth(c, p, 2 / (p + 1))
        elif name == "rsi":
            gains = smooth(np.maximum(delta, 0), p, 1 / p)
            losses = smooth(np.maximum(-delta, 0), p, 1 / p)
            result = 100 * divide(gains, gains + losses, 0.5)
        elif name == "macd":
            line = self.indicator("ema", p) - self.indicator("ema", min(200, p * 2))
            result = line - smooth(line, 9, 0.2)
        elif name in {"bb_upper", "bb_lower"}:
            result = rolling(c, p) + (2 if name == "bb_upper" else -2) * rolling(c, p, "std")
        elif name in {"atr", "adx"}:
            tr = np.maximum(h - low, np.maximum(abs(h - previous), abs(low - previous)))
            tr[0] = h[0] - low[0]
            atr = smooth(tr, p, 1 / p)
            if name == "atr":
                result = atr
            else:
                up, down = np.r_[0, np.diff(h)], np.r_[0, -np.diff(low)]
                plus = smooth(np.where((up > down) & (up > 0), up, 0), p, 1 / p)
                minus = smooth(np.where((down > up) & (down > 0), down, 0), p, 1 / p)
                result = smooth(100 * divide(abs(plus - minus), plus + minus, 0), p, 1 / p)
        elif name == "stochastic":
            bottom, top = rolling(low, p, "min"), rolling(h, p, "max")
            result = 100 * divide(c - bottom, top - bottom, 0.5)
        elif name == "vwap":
            # Rolling VWAP, not tied to a stock-exchange session.
            result = divide(rolling((h + low + c) / 3 * v, p), rolling(v, p))
        elif name in {"roc", "momentum", "returns"}:
            lag = 1 if name == "returns" else p
            old = np.full(len(c), np.nan)
            old[lag:] = c[:-lag]
            result = c - old if name == "momentum" else divide(c, old) - 1
        else:
            raise ValueError(f"Unknown indicator: {name}")
        self.cache[key] = result
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)
        return result

    def expression(self, rule: Condition):
        # Return truth AND validity: NOT must not turn warm-up NaN into entry.
        if rule.children:
            evaluated = [self.expression(c) for c in rule.children]
            valid = np.logical_and.reduce([x[1] for x in evaluated])
            values = [x[0] for x in evaluated]
            truth = (
                ~values[0]
                if rule.op == "not"
                else np.logical_and.reduce(values)
                if rule.op == "and"
                else np.logical_or.reduce(values)
            )
            return truth & valid, valid
        a, b = self.operand(rule.left), self.operand(rule.right)
        valid = np.isfinite(a) & np.isfinite(b)
        if rule.op in {"cross_up", "cross_down"}:
            prior_valid = np.r_[False, valid[:-1]]
            truth = (
                (a > b) & np.r_[False, a[:-1] <= b[:-1]]
                if rule.op == "cross_up"
                else (a < b) & np.r_[False, a[:-1] >= b[:-1]]
            )
            valid &= prior_valid
        else:
            truth = {"gt": np.greater, "lt": np.less, "ge": np.greater_equal, "le": np.less_equal}[
                rule.op
            ](a, b)
        return truth & valid, valid

    def signals(self, strategy: StrategyDefinition):
        if strategy.timeframe != self.dataset.request.timeframe:
            raise ValueError("Strategy and dataset timeframes differ")
        entry = self.expression(strategy.entry)[0]
        exit_ = (
            self.expression(strategy.exit)[0] if strategy.exit else np.zeros(len(entry), dtype=bool)
        )
        return entry, exit_

    def ml_matrix(self):
        c = self.ohlcv[:, 3]
        return np.column_stack(
            [
                self.indicator("rsi") / 100,
                divide(c, self.indicator("ema", 20)) - 1,
                divide(self.indicator("atr"), c),
                self.indicator("volatility", 20),
                divide(self.ohlcv[:, 4], self.indicator("volume_sma", 20)),
                self.indicator("roc", 5),
                self.indicator("returns"),
                divide(c - self.ohlcv[:, 0], self.ohlcv[:, 1] - self.ohlcv[:, 2], 0),
                self.indicator("adx") / 100,
            ]
        )
