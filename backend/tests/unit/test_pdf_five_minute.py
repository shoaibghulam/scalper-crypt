from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from scalper.api.research import ResearchRequest, catalog
from scalper.backtest import replay
from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.strategies import STRATEGIES
from scalper.strategies import pdf_five_minute as pdf
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext
from scalper.strategies.research_registry import RESEARCH_STRATEGIES


def candle(i, close=100.0, *, interval=300_000, **kwargs):
    values = dict(open=close, high=close + 0.1, low=close - 0.1, volume=100)
    values.update(kwargs)
    return Candle(
        i * interval,
        close=close,
        close_time=(i + 1) * interval - 1,
        quote_volume=0,
        taker_buy_base=0,
        **values,
    )


def context(bars=None):
    five = bars or [candle(i) for i in range(360)]
    last = five[-1].open_time // 60_000 + 4
    one = [candle(i, five[-1].close, interval=60_000) for i in range(last - 199, last + 1)]
    price = five[-1].close
    return StrategyContext(
        "TESTUSDT", five, one, Ticker("TESTUSDT", price, 1e9, 0), BookTop("TESTUSDT", price, price)
    )


def mirror(bars):
    return [
        replace(b, open=200 - b.open, high=200 - b.low, low=200 - b.high, close=200 - b.close)
        for b in bars
    ]


def test_all_five_are_discoverable_with_source_and_5m_timing():
    assert len(pdf.PDF_FIVE_MINUTE_STRATEGIES) == 5
    rows = {row["id"]: row for row in catalog()}
    for key, strategy in pdf.PDF_FIVE_MINUTE_STRATEGIES.items():
        assert STRATEGIES.get(key) is strategy
        assert strategy.testnet_only and not strategy.requires_order_flow
        assert rows[key]["source"] == pdf.SOURCE
        assert rows[key]["source_pages"]
        assert rows[key]["timeframe"] == "5m"
        assert rows[key]["warmup_candles"] >= strategy.history_5m * 5
        ResearchRequest(csv="unused", strategy=key, quantity_step=0.001)


@pytest.mark.parametrize("strategy", pdf.PDF_FIVE_MINUTE_STRATEGIES.values())
def test_history_alignment_closed_timeframe_and_bad_data_guards(strategy):
    ctx = context()
    assert not strategy.assess(replace(ctx, candles_1m=[])).ready
    assert "newly closed" in strategy.assess(replace(ctx, candles_1m=ctx.candles_1m[:-1])).note
    assert not strategy.assess(replace(ctx, book=BookTop("TESTUSDT", float("nan"), 100))).ready
    broken = [*ctx.candles_5m]
    broken[-2] = replace(
        broken[-2],
        open_time=broken[-2].open_time - 300_000,
        close_time=broken[-2].close_time - 300_000,
    )
    assert "gap" in strategy.assess(replace(ctx, candles_5m=broken)).note
    assert not strategy.assess(ctx).ready


def test_session_vwap_weights_volume_and_resets_at_utc_midnight():
    old = [candle(i, 500) for i in range(288)]
    session = [candle(i, 100, high=100, low=100) for i in range(288, 300)]
    session[-1] = candle(299, 104, high=104, low=104, volume=300)
    mean, sigma = pdf.session_vwap(old + session)
    assert mean == pytest.approx((1100 + 312) / 14)
    assert sigma > 0
    with pytest.raises(ValueError, match="complete UTC"):
        pdf.session_vwap(session[1:])
    with pytest.raises(ValueError, match="twelve"):
        pdf.session_vwap(old + session[:11])
    with pytest.raises(ValueError, match="no volume"):
        pdf.session_vwap([replace(b, volume=0) for b in session])


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ema_pullback_has_real_symmetric_closed_bar_signals(side):
    bars = [candle(i, 85 + i * 0.04, open=85 + (i - 1) * 0.04) for i in range(360)]
    bars[-2] = replace(bars[-2], open=99.3, high=99.4, low=99.0, close=99.1)
    bars[-1] = replace(bars[-1], open=99.1, high=99.7, low=99.05, close=99.6, volume=150)
    if side == "SHORT":
        bars = mirror(bars)
    strategy = pdf.PdfFiveMinute(1)
    result = strategy.assess(context(bars))
    assert result.ready, result.note
    assert result.direction == side
    bars[-1] = replace(bars[-1], volume=1)
    assert not strategy.assess(context(bars)).ready


@pytest.mark.parametrize("number", [2, 3, 4])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_vwap_and_band_entry_predicates_with_explicit_features(monkeypatch, number, side):
    # Indicator values isolate the PDF's entry predicates; VWAP mathematics,
    # timing, guards and real-candle EMA/retest examples are tested separately.
    bars = [candle(i) for i in range(360)]
    sign = 1 if side == "LONG" else -1
    strategy = pdf.PdfFiveMinute(number)
    if number == 2:
        bars[-8] = replace(bars[-8], high=105)
        bars[-2] = replace(bars[-2], open=100, close=99.8, low=99.6)
        bars[-1] = replace(bars[-1], open=99.8, close=100.3, high=100.4, low=99.7, volume=150)
        monkeypatch.setattr(pdf, "ema", lambda values, period: 100 if period == 9 else 99.9)
        monkeypatch.setattr(pdf, "session_vwap", lambda data: (99.6 + (len(data) - 300) * 0.1, 1))
    else:
        bars[-2] = replace(bars[-2], close=94.9, open=95.5, high=95.6, low=94.8)
        bars[-1] = replace(bars[-1], close=95.2, open=94.9, high=95.3, low=94.85)
        monkeypatch.setattr(pdf, "ema", lambda values, period: 100)
        monkeypatch.setattr(pdf, "session_vwap", lambda data: (100, 2.5))
        monkeypatch.setattr(pdf, "bollinger", lambda data: (100, 2.5))
    if side == "SHORT":
        bars = mirror(bars)
        if number == 2:
            monkeypatch.setattr(pdf, "ema", lambda values, period: 100 if period == 9 else 100.1)
            monkeypatch.setattr(
                pdf, "session_vwap", lambda data: (100.4 - (len(data) - 300) * 0.1, 1)
            )
    monkeypatch.setattr(pdf, "volatility", lambda data: 1.0)
    monkeypatch.setattr(pdf, "adx", lambda *args: SimpleNamespace(adx=10))
    monkeypatch.setattr(pdf, "rsi", lambda values: 25 if sign == 1 else 75)
    ctx = context(bars)
    setup = strategy.assess(ctx)
    assert setup.ready, setup.note
    assert setup.direction == side
    levels = strategy.levels(ctx, Decimal(str(ctx.price)), side)
    if number in (3, 4):
        assert levels.target == Decimal("100")
        monkeypatch.setattr(pdf, "rsi", lambda values: 50)
        assert not strategy.assess(ctx).ready
    else:
        assert levels.target == (Decimal("105") if sign == 1 else Decimal("95"))
        bars[-1] = replace(bars[-1], volume=1)
        assert not strategy.assess(context(bars)).ready


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_volume_breakout_requires_expansion_and_first_retest(side):
    bars = [candle(i, high=101, low=99) for i in range(360)]
    bars[-2] = replace(bars[-2], open=100, close=102, high=102.2, volume=200)
    bars[-1] = replace(bars[-1], open=101, low=100.9, high=102, close=101.8)
    if side == "SHORT":
        bars = mirror(bars)
    strategy = pdf.PdfFiveMinute(5)
    setup = strategy.assess(context(bars))
    assert setup.ready, setup.note
    assert setup.direction == side
    entry = Decimal(str(bars[-1].close))
    levels = strategy.levels(context(bars), entry, side)
    assert abs(levels.target - entry) == 2 * abs(entry - levels.stop)
    bars[-2] = replace(bars[-2], volume=10)
    assert not strategy.assess(context(bars)).ready


@pytest.mark.parametrize("number", range(1, 6))
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_entry_chase_stop_floor_and_fixed_target_rounding(monkeypatch, number, side):
    strategy = pdf.PdfFiveMinute(number)
    sign = 1 if side == "LONG" else -1
    fixed = number in (2, 3, 4)
    signal = pdf.PdfSignal(side, 100 - sign * 0.2, 0.2, 100 + sign * 2 if fixed else None)
    monkeypatch.setattr(strategy, "signal", lambda ctx: signal)
    entry = Decimal("100")
    levels = strategy.execution_levels(context(), entry, side, Decimal(".01"))
    assert abs(entry - levels.stop) >= Decimal(".45")
    assert (levels.target - entry) * sign >= abs(entry - levels.stop) * Decimal("1.5")
    if fixed:
        assert levels.target == Decimal(str(signal.target))
    with pytest.raises(ValueError, match="half an ATR"):
        strategy.levels(context(), Decimal("100.2"), side)
    if fixed:
        monkeypatch.setattr(
            strategy, "signal", lambda ctx: replace(signal, target=100 + sign * 0.3)
        )
        with pytest.raises(ValueError, match=r"minimum 1\.5R"):
            strategy.levels(context(), entry, side)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_replay_passes_actual_entry_to_levels_and_rejects_chased_entries(monkeypatch, side):
    calls = []

    class Probe(Strategy):
        id = "entry-price-probe"

        def assess(self, ctx):
            return Setup.entry(side, "test")

        def levels(self, ctx, entry, direction):
            calls.append(entry)
            if abs(entry - Decimal(str(ctx.price))) > Decimal(".2"):
                raise ValueError("chased")
            sign = Decimal(1 if direction == "LONG" else -1)
            return Levels(entry - sign, entry + sign * 2)

    monkeypatch.setitem(RESEARCH_STRATEGIES, Probe.id, Probe())
    sign = 1 if side == "LONG" else -1
    bars = [candle(0, interval=60_000), candle(1, 100 + sign * 0.5, interval=60_000)]
    result = replay(bars, strategy_id=Probe.id, slippage_bps=0, fee_rate=0)
    assert calls == [Decimal(str(100 + sign * 0.5))]
    assert result["trade_count"] == 0
    assert result["rejected_entries"] == 1
