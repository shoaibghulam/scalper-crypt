from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scalper.api.research import ResearchRequest, catalog, evaluate_candles
from scalper.api.research_jobs import AnnualRequest, capabilities
from scalper.indicators.core import rsi
from scalper.strategies import STRATEGIES
from scalper.strategies import pdf_advanced_five_minute as advanced
from scalper.strategies.combinations import fifteen
from tests.unit.test_pdf_five_minute import candle, context, mirror


def flat():
    return [candle(i) for i in range(660)]


def test_catalog_and_explicit_non_candle_requirements():
    rows = {row["id"]: row for row in catalog()}
    assert len(advanced.ADVANCED_FIVE_MINUTE_STRATEGIES) == 7
    for key, strategy in advanced.ADVANCED_FIVE_MINUTE_STRATEGIES.items():
        assert STRATEGIES.get(key) is strategy
        assert strategy.testnet_only
        assert rows[key]["source"] == advanced.SOURCE
        assert rows[key]["source_pages"] == str(strategy.number + 2)
        ResearchRequest(
            csv="unused", strategy=key, quantity_step=0.001, warmup=rows[key]["warmup_candles"]
        )
        AnnualRequest(strategies=[key])
    assert rows["advanced5-ema-adx"]["warmup_candles"] == 3075
    assert rows["advanced5-stochrsi"]["warmup_candles"] == 3075
    missing = capabilities()["unavailable_strategies"]
    assert len(missing) == 3
    for item in missing:
        assert item["id"] not in rows and item["id"] not in STRATEGIES.ids()
        assert item["reason"]
        with pytest.raises(ValueError, match="Unsupported"):
            ResearchRequest(csv="unused", strategy=item["id"], quantity_step=0.001)
        with pytest.raises(ValueError, match="supported"):
            AnnualRequest(strategies=[item["id"]])


@pytest.mark.parametrize("strategy", advanced.ADVANCED_FIVE_MINUTE_STRATEGIES.values())
def test_history_and_closed_candle_guards(strategy):
    ctx = context(flat())
    assert not strategy.assess(replace(ctx, candles_5m=ctx.candles_5m[-20:])).ready
    assert "newly closed" in strategy.assess(replace(ctx, candles_1m=ctx.candles_1m[:-1])).note
    invalid = [*ctx.candles_5m]
    invalid[-1] = replace(invalid[-1], close=float("nan"))
    assert not strategy.assess(replace(ctx, candles_5m=invalid)).ready
    assert not strategy.assess(ctx).ready


def test_stochrsi_matches_independent_wilder_rsi_windows():
    closes = [100 + ((i * 17) % 23) / 10 for i in range(100)]
    rsis = [rsi(closes[:i]) for i in range(15, len(closes) + 1)]
    raw = []
    for end in range(14, len(rsis) + 1):
        window = rsis[end - 14 : end]
        raw.append((window[-1] - min(window)) / (max(window) - min(window)))
    k = [sum(raw[i - 2 : i + 1]) / 3 for i in range(2, len(raw))]
    expected = [(k[i], sum(k[i - 2 : i + 1]) / 3) for i in range(2, len(k))]
    for actual, wanted in zip(advanced.stoch_rsi(closes), expected, strict=True):
        assert actual == pytest.approx(wanted)
    assert all(pair == (0.5, 0.5) for pair in advanced.stoch_rsi([100] * 100))
    line, histogram = advanced.macd_histogram([100] * 100)
    assert all(v == 0 for v in line + histogram)


def test_ichimoku_visible_cloud_uses_26_bar_displacement_not_future_values():
    bars = [candle(i, 100 + i * 0.1) for i in range(100)]
    before = advanced.ichimoku(bars)
    changed = bars[:-26] + [replace(b, high=b.high + 50, close=b.close + 20) for b in bars[-26:]]
    after = advanced.ichimoku(changed)
    assert (before.span_a, before.span_b) == (after.span_a, after.span_b)
    assert before.forward_a != after.forward_a
    known = bars[:-26]
    expected = (advanced.midpoint(known, 9) + advanced.midpoint(known, 26)) / 2
    assert before.span_a == expected
    assert before.span_b == advanced.midpoint(known, 52)
    with pytest.raises(ValueError, match="78"):
        advanced.ichimoku(bars[:77])


def test_15m_context_drops_incomplete_groups_and_has_full_ema200_history():
    bars = flat()[-615:]
    for offset in range(3):
        window = [
            replace(
                b,
                open_time=b.open_time + offset * 300_000,
                close_time=b.close_time + offset * 300_000,
            )
            for b in bars
        ]
        higher = fifteen(window)
        assert len(higher) >= 203
        assert all(b.open_time % 900_000 == 0 for b in higher)
        assert higher[-1].close_time <= window[-1].close_time


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("number", range(1, 8))
def test_all_seven_entry_predicates_and_rejection_controls(monkeypatch, number, side):
    bars = flat()
    sign = 1 if side == "LONG" else -1
    def transform(value):
        return value if sign == 1 else 200 - value
    strategy = advanced.AdvancedFiveMinute(number)
    if number == 1:
        bars[-2] = replace(bars[-2], close=99.9, low=99.5)
        bars[-1] = replace(bars[-1], close=101, high=101.1, volume=150)

        def ema(values, period):
            value = (
                (105 + len(values) * 0.1 if period == 50 else 100 + len(values) * 0.01)
                if len(values) < 300
                else (100 if period == 20 else 99)
            )
            return transform(value)

        monkeypatch.setattr(advanced, "ema", ema)
        monkeypatch.setattr(advanced, "adx", lambda *args: SimpleNamespace(adx=25))
        monkeypatch.setattr(advanced, "volatility", lambda values: 1)
    elif number == 2:
        bars = [candle(i, high=101, low=99) for i in range(660)]
        bars[-1] = replace(bars[-1], close=102, high=102.1, volume=200)
    elif number == 3:
        bars[-1] = replace(bars[-1], open=101.2, low=101, close=102, high=102.1, volume=150)
        monkeypatch.setattr(advanced, "bollinger", lambda values: (transform(100), 0.1))
        monkeypatch.setattr(advanced, "ema", lambda *args: transform(100))
        monkeypatch.setattr(advanced, "session_vwap", lambda values: (transform(100), 0.1))
        monkeypatch.setattr(advanced, "volatility", lambda values: 1.1 if len(values) == 300 else 1)
    elif number == 4:
        bars[-1] = replace(bars[-1], close=102, high=102.1)
        monkeypatch.setattr(advanced, "ema", lambda *args: transform(99))
        monkeypatch.setattr(advanced, "session_vwap", lambda values: (transform(99), 1))
        oscillator = [(0.1, 0.15)] * 3 + [(0.3, 0.2)]
        if sign == -1:
            oscillator = [(1 - k, 1 - d) for k, d in oscillator]
        monkeypatch.setattr(advanced, "stoch_rsi", lambda values: oscillator)
    elif number == 5:
        bars[-1] = replace(bars[-1], close=101.3, high=101.4, volume=150)
        monkeypatch.setattr(
            advanced,
            "macd_histogram",
            lambda values: ([sign] * 4, [v * sign for v in [0.6, 0.4, 0.2, 0.3]]),
        )
        monkeypatch.setattr(advanced, "ema", lambda *args: transform(99))
        monkeypatch.setattr(
            advanced, "session_vwap", lambda values: (transform(99 + (len(values) - 300) * 0.1), 1)
        )
        monkeypatch.setattr(advanced, "volatility", lambda values: 1)
    elif number == 6:
        bars[-1] = replace(bars[-1], close=100.8, high=100.9, volume=150)

        def cloud(values):
            fields = (
                [99, 98, 97, 96, 99, 98]
                if len(values) < 100
                else [100.5, 100, 99, 98.5, 101 if len(values) == 252 else 100, 99]
            )
            return advanced.Cloud(*(transform(v) for v in fields))

        monkeypatch.setattr(advanced, "ichimoku", cloud)
        monkeypatch.setattr(advanced, "volatility", lambda values: 1)
    else:
        bars[-10] = replace(bars[-10], high=110, low=90)
        for index in (-5, -4, -3):
            bars[index] = replace(bars[index], open=91, close=91, low=90.8, high=91.3)
        bars[-2] = replace(bars[-2], open=91, close=90.9, low=89, high=91.2)
        bars[-1] = replace(bars[-1], open=91, close=93, low=90.7, high=93.2, volume=150)
        monkeypatch.setattr(advanced, "session_vwap", lambda values: (transform(92), 1))
    if sign == -1:
        bars = mirror(bars)
    ctx = context(bars)
    setup = strategy.assess(ctx)
    assert setup.ready, setup.note
    assert setup.direction == side
    entry = Decimal(str(ctx.price))
    levels = strategy.execution_levels(ctx, entry, side, Decimal(".001"))
    assert (entry - levels.stop) * sign > 0
    assert (levels.target - entry) * sign >= abs(entry - levels.stop) * Decimal("1.5")
    if number == 7:
        assert levels.target == 100  # Fixed range midpoint, not a fabricated 2R target.
    if number == 4:
        monkeypatch.setattr(advanced, "stoch_rsi", lambda values: [(0.5, 0.5)] * 4)
    else:
        bars[-1] = replace(bars[-1], volume=1)
        ctx = context(bars)
    assert not strategy.assess(ctx).ready


def test_research_accepts_and_applies_longer_15m_ema_warmup(monkeypatch):
    recorded = []

    def replay(bars, **kwargs):
        recorded.append((len(bars), kwargs["start_index"]))
        return {"net": 0, "trades": [], "closed_equity_drawdown_pct": 0}

    monkeypatch.setattr("scalper.api.research.replay", replay)
    request = ResearchRequest(
        csv="unused", strategy="advanced5-ema-adx", warmup=3075, quantity_step=0.001
    )
    bars = [candle(i, interval=60_000) for i in range(3900)]
    report = evaluate_candles(request, bars)
    assert report["warmup_candles"] == 3075
    assert report["data_quality"]["scored_candles"] == 825
    assert recorded == [(3350, 3075)] * 6
