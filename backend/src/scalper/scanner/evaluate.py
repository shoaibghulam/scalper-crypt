"""Evaluate one symbol against the strategy filters.

Pure functions over candle arrays. No network, no clock, no exchange. That
makes this the part of the system that can be backtested honestly: the same
code runs over historical bars and over live ones, so a result obtained in a
backtest is a result about the code that actually trades.

Rejections are first-class. A scanner that silently returns nothing is
untunable, so every symbol that fails carries the stage and the reason, and the
dashboard shows them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from scalper.exchange.market_data import BookTop, Candle, Ticker
from scalper.exchange.order_flow import FlowEvidence
from scalper.indicators.core import (
    adx,
    atr,
    efficiency_ratio,
    percentile_rank,
    rsi,
    rvol,
    true_ranges,
    vwap_bands,
)
from scalper.scanner.config import Regime, ScanConfig
from scalper.strategies import DEFAULT_STRATEGY_ID, STRATEGIES
from scalper.strategies.base import Strategy, StrategyContext


@dataclass(frozen=True)
class Rejection:
    symbol: str
    stage: str
    reason: str


@dataclass(frozen=True)
class Candidate:
    """A symbol that passed every filter, with the evidence for the decision."""

    symbol: str
    price: float
    quote_volume_24h: float
    change_24h_pct: float
    spread_bps: float

    atr_pct: float
    atr_percentile: float
    adx: float
    plus_di: float
    minus_di: float
    efficiency_ratio: float
    rvol_5m: float
    rvol_1m: float
    regime: Regime

    vwap: float
    sigma: float
    sigma_distance: float
    rsi_1m: float
    delta_pct: float

    stop_distance_pct: float
    cost_to_stop: float
    breakeven_win_rate: float

    #: True when the strategy entry conditions hold on the last closed bar.
    #: Economics say the symbol is worth trading; this says now is the moment.
    setup_ready: bool = False
    setup_note: str = ""
    #: Which way the strategy would trade, when it would trade at all.
    direction: str | None = None

    expires_at: float | None = None
    score: float = 0.0
    notes: list[str] = field(default_factory=list)


def _atr_pct(candles: list[Candle], period: int = 14) -> float:
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    value = atr(highs, lows, closes, period)
    return 0.0 if closes[-1] == 0 else value / closes[-1] * 100.0


def _atr_pct_series(candles: list[Candle], period: int = 14, window: int = 100) -> list[float]:
    """ATR percent history, for placing the current reading in its own range.

    A single forward pass. The previous version recomputed the whole smoothing
    for every window, which compounded with the quadratic ATR underneath it and
    made this the slowest step in a market scan.
    """
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    closes = [c.close for c in candles]
    trs = true_ranges(highs, lows, closes)

    # True range at bar i depends only on bars i and i-1, so the true ranges of
    # a prefix are exactly the prefix of the true ranges. That is what makes a
    # running value equivalent to recomputing each window.
    running = sum(trs[:period]) / period

    start = max(period + 1, len(candles) - window)
    out: list[float] = []
    for end in range(period + 1, len(candles) + 1):
        running = (running * (period - 1) + trs[end - 1]) / period
        if end >= start:
            close = closes[end - 1]
            out.append(0.0 if close == 0 else running / close * 100.0)
    return out


def classify_regime(adx_value: float, er_value: float, config: ScanConfig) -> Regime:
    """Trend, range, or neither.

    The two measures must agree. ADX reads strength and the efficiency ratio
    reads directness, and when they disagree the market is doing something that
    neither a continuation nor a mean-reversion strategy handles well. Standing
    aside is the honest answer there.
    """
    trending = adx_value >= config.trend_adx and er_value >= config.trend_efficiency_ratio
    ranging = adx_value < config.range_adx and er_value <= config.range_efficiency_ratio

    if trending and not ranging:
        return Regime.TREND
    if ranging and not trending:
        return Regime.RANGE
    return Regime.NO_TRADE


def evaluate_symbol(
    symbol: str,
    ticker: Ticker,
    book: BookTop,
    candles_5m: list[Candle],
    candles_1m: list[Candle],
    config: ScanConfig,
    strategy: Strategy | None = None,
    order_flow: FlowEvidence | None = None,
) -> Candidate | Rejection:
    """Run the economic gates, then ask the strategy about timing.

    The gates are shared because they are properties of the instrument and
    the fee schedule rather than of the idea being traded. Whether a setup
    is live right now is the strategy decision, and every strategy answers
    it differently.
    """
    strategy = strategy or STRATEGIES.get(DEFAULT_STRATEGY_ID)
    if len(candles_5m) < 60 or len(candles_1m) < 40:
        return Rejection(symbol, "data", "not enough closed candles yet")

    context = StrategyContext(symbol, candles_5m, candles_1m, ticker, book, order_flow)
    error = strategy.data_error(context)
    if error:
        return Rejection(symbol, "data", error)

    closes_5m = [c.close for c in candles_5m]
    highs_5m = [c.high for c in candles_5m]
    lows_5m = [c.low for c in candles_5m]
    volumes_5m = [c.volume for c in candles_5m]

    atr_pct = _atr_pct(candles_5m)
    if atr_pct < config.min_atr_pct:
        return Rejection(
            symbol,
            "volatility",
            f"ATR {atr_pct:.2f}% is below the {config.min_atr_pct:.2f}% floor, so "
            "the stop would be too tight to clear its own fees",
        )
    if atr_pct > config.max_atr_pct:
        return Rejection(
            symbol,
            "volatility",
            f"ATR {atr_pct:.2f}% is above {config.max_atr_pct:.2f}%, which is news "
            "or listing chaos rather than a tradeable regime",
        )

    history = _atr_pct_series(candles_5m)
    atr_percentile = percentile_rank(history, atr_pct)

    stop_fraction = max((atr_pct / 100.0) * config.atr_stop_multiple, config.min_stop_fraction)
    cost_to_stop = config.round_trip_cost / stop_fraction
    if cost_to_stop > config.max_cost_to_stop:
        return Rejection(
            symbol,
            "economics",
            f"fees would be {cost_to_stop:.0%} of the stop distance, above the "
            f"{config.max_cost_to_stop:.0%} limit",
        )

    # Regime and relative volume describe this minute rather than this
    # symbol, so they inform the ranking and the ready flag instead of
    # rejecting. Gating on them returns an empty list most of the time, which
    # tells the operator nothing and invites loosening the gates that matter.
    adx_result = adx(highs_5m, lows_5m, closes_5m)
    er_value = efficiency_ratio(closes_5m)
    regime = classify_regime(adx_result.adx, er_value, config)
    rvol_5m = rvol(volumes_5m)

    bands = vwap_bands(
        [c.high for c in candles_1m],
        [c.low for c in candles_1m],
        [c.close for c in candles_1m],
        [c.volume for c in candles_1m],
    )
    price = candles_1m[-1].close

    # Break-even at 2R against this stop, which is the shape actually traded.
    breakeven = (1 + cost_to_stop) / 3.0
    rsi_fast = rsi([c.close for c in candles_1m], 7)
    sigma_distance = bands.sigma_distance(price)

    context = StrategyContext(
        symbol=symbol,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
        ticker=ticker,
        book=book,
        order_flow=order_flow,
    )
    setup = strategy.assess(context)
    ready, setup_note = setup.ready, setup.note
    if ready and setup.direction in {"LONG", "SHORT"}:
        entry = Decimal(str(book.ask if setup.direction == "LONG" else book.bid))
        try:
            levels = strategy.levels(context, entry, setup.direction)
        except ValueError as exc:
            return Rejection(symbol, "setup", str(exc))
        sign = Decimal(1) if setup.direction == "LONG" else Decimal(-1)
        risk = (entry - levels.stop) * sign
        reward = (levels.target - entry) * sign
        if risk <= 0 or reward <= 0:
            return Rejection(symbol, "economics", "strategy levels do not straddle entry")
        stop_fraction = float(risk / entry)
        cost_to_stop = config.round_trip_cost / stop_fraction
        if stop_fraction < config.min_stop_fraction or cost_to_stop > config.max_cost_to_stop:
            return Rejection(symbol, "economics", "strategy stop fails cost/distance limits")
        if reward / risk < Decimal("1.5"):
            return Rejection(symbol, "economics", "strategy target is below 1.5R")
        breakeven = (1 + cost_to_stop) / (1 + float(reward / risk))

    return Candidate(
        symbol=symbol,
        price=price,
        quote_volume_24h=ticker.quote_volume,
        change_24h_pct=ticker.price_change_pct,
        spread_bps=book.spread_bps,
        atr_pct=atr_pct,
        atr_percentile=atr_percentile,
        adx=adx_result.adx,
        plus_di=adx_result.plus_di,
        minus_di=adx_result.minus_di,
        efficiency_ratio=er_value,
        rvol_5m=rvol_5m,
        rvol_1m=rvol([c.volume for c in candles_1m]),
        regime=regime,
        vwap=bands.vwap,
        sigma=bands.sigma,
        sigma_distance=sigma_distance,
        rsi_1m=rsi_fast,
        delta_pct=candles_1m[-1].delta_pct,
        stop_distance_pct=stop_fraction * 100.0,
        cost_to_stop=cost_to_stop,
        breakeven_win_rate=breakeven,
        expires_at=(
            candles_1m[-1].close_time / 1000 + strategy.max_signal_age_s
            if hasattr(strategy, "max_signal_age_s")
            else None
        ),
        setup_ready=ready,
        setup_note=setup_note,
        direction=setup.direction,
    )


def assess_setup(
    *,
    regime: Regime,
    sigma_distance: float,
    rsi_fast: float,
    rvol_5m: float,
    config: ScanConfig,
) -> tuple[bool, str]:
    """Whether the VWAP band strategy would enter on the last closed bar.

    The two sigma threshold is not a tunable. Loosening it to one and a half
    roughly triples the signal count and halves the edge, which is the single
    most common way a mean-reversion strategy is ruined in tuning.
    """
    if regime is Regime.TREND:
        return False, "trending; the mean-reversion strategy stands aside"
    if regime is Regime.NO_TRADE:
        return False, "regime unclear; ADX and efficiency disagree"
    if rvol_5m < config.min_rvol_5m:
        return False, f"volume {rvol_5m:.2f}x average is too quiet to fade"

    if sigma_distance <= -2.0 and rsi_fast < 20:
        return True, f"stretched {sigma_distance:.1f} sigma below VWAP, RSI {rsi_fast:.0f}"
    if sigma_distance >= 2.0 and rsi_fast > 80:
        return True, f"stretched {sigma_distance:+.1f} sigma above VWAP, RSI {rsi_fast:.0f}"

    return False, f"only {sigma_distance:+.1f} sigma from VWAP; waiting for 2.0"


def score_candidate(candidate: Candidate, config: ScanConfig) -> float:
    """Rank candidates against each other.

    Six weights, and only these six are ever fitted. Indicator periods stay at
    conventional values, because searching periods and thresholds together
    spans millions of combinations and the maximum of that search is noise.

    ATR percentile scores as distance from about the seventieth: too quiet and
    there is no move to capture, too wild and the stop gets run.
    """
    activity = min(candidate.rvol_5m / 3.0, 1.0)
    volatility = max(0.0, 1.0 - abs(candidate.atr_percentile - 70.0) / 70.0)
    regime_confidence = min(abs(candidate.adx - 21.5) / 10.0, 1.0)
    spread = max(0.0, 1.0 - candidate.spread_bps / config.max_spread_bps)
    liquidity = min(candidate.quote_volume_24h / 1_000_000_000.0, 1.0)

    # Distance from vwap counts only for the mean-reversion family. In a trend,
    # being far from vwap is a reason to stay out rather than a setup.
    setup = (
        min(abs(candidate.sigma_distance) / 2.0, 1.0) if candidate.regime is Regime.RANGE else 0.0
    )

    # A live setup outranks a merely tradeable symbol, because the list is
    # sorted for someone deciding what to trade right now.
    ready_bonus = 3.0 if candidate.setup_ready else 0.0

    return round(
        activity * 1.0
        + volatility * 1.0
        + regime_confidence * 0.8
        + spread * 0.8
        + liquidity * 0.5
        + setup * 1.2
        + ready_bonus,
        4,
    )
