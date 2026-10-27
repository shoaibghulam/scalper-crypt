"""Video-inspired crypto IVB; exact assumptions are published with the catalog."""

from bisect import bisect_left
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from math import isfinite
from zoneinfo import ZoneInfo

from scalper.exchange.market_data import Candle
from scalper.strategies.base import Levels, Setup, Strategy, StrategyContext
from scalper.strategies.pdf_five_minute import volatility

NEW_YORK = ZoneInfo("America/New_York")


class IvbCrypto(Strategy):
    id = "ivb-crypto-opening-range"
    name = "IVB 5m: New York Opening Range (Crypto)"
    summary = "09:30-10:00 NY range, 25-day contraction and volume breakout; backtest only."
    backtest_only = True
    signal_interval_s = 300
    history_5m = 27 * 288  # 25 prior daily ranges plus boundary/DST buffer.
    history_1m = 5
    max_trades_per_session = 2
    lookback_days = 25
    max_contraction = 0.7
    volume_multiplier = 1.1
    assumptions = (
        "Video-inspired crypto adaptation, not Fabio Valentini's undisclosed exact model.",
        "All seven calendar days; America/New_York DST; no Nasdaq holiday calendar.",
        "Six 5m bars from 09:30 to 10:00 form the range; fresh close breakouts thereafter.",
        "IB width <= 0.7 times the mean width of the previous 25 complete daily ranges.",
        "Breakout volume >= 1.1 times the previous five same-session bars; both directions.",
        "ATR14 on the last 100 closed 5m bars; frozen 3 ATR stop and 2 ATR target from fill.",
        "At most two filled entries per coin/session; exit at 16:00 NY, no entry at 16:00.",
        "Backtest only: 2/3R conflicts with platform 1.5R trading minimum; no execution hook.",
        "Replay risk, fees and leverage use the experiment settings; funding is not simulated.",
    )

    def session_key(self, timestamp_ms: int) -> str:
        return datetime.fromtimestamp(timestamp_ms / 1000, NEW_YORK).date().isoformat()

    def scheduled_exit(self, opened_at_ms: int) -> int:
        opened = datetime.fromtimestamp(opened_at_ms / 1000, NEW_YORK)
        return int(opened.replace(hour=16, minute=0, second=0, microsecond=0).timestamp() * 1000)

    @staticmethod
    def _opening_range(bars: list[Candle], day: datetime) -> tuple[float, float]:
        start = int(day.replace(hour=9, minute=30, second=0, microsecond=0).timestamp() * 1000)
        index = bisect_left(bars, start, key=lambda bar: bar.open_time)
        opening = bars[index:index + 6]
        if len(opening) != 6 or any(
            bar.open_time != start + n * 300_000
            or bar.close_time != bar.open_time + 299_999
            for n, bar in enumerate(opening)
        ):
            raise ValueError("need 25 complete prior daily opening ranges and today's range")
        if any(not isfinite(b.high) or not isfinite(b.low) or b.low <= 0 or b.high < b.low
               for b in opening):
            raise ValueError("invalid opening range data")
        return min(bar.low for bar in opening), max(bar.high for bar in opening)

    def signal(self, ctx: StrategyContext) -> tuple[str, float]:
        if not ctx.candles_5m or not ctx.candles_1m:
            raise ValueError("insufficient closed candles")
        bars = ctx.candles_5m
        trigger = bars[-1]
        decision = datetime.fromtimestamp((trigger.close_time + 1) / 1000, NEW_YORK)
        minute = decision.hour * 60 + decision.minute
        if not 605 <= minute < 960:
            raise ValueError("entries require a closed breakout after 10:00 and before 16:00 NY")
        if not self.has_enough_history(ctx):
            raise ValueError("need 27 days of 5m history for the prior 25 daily ranges")
        if ctx.candles_1m[-1].close_time != trigger.close_time:
            raise ValueError("waiting for a newly closed 5m candle")
        recent = bars[-100:]
        check = replace(ctx, candles_5m=recent, candles_1m=ctx.candles_1m[-5:])
        if error := self.data_error(check):
            raise ValueError(error)
        if any(b.open_time - a.open_time != 300_000 for a, b in pairwise(recent)):
            raise ValueError("recent candle history has a gap")
        if any(b.open_time % 300_000 or b.close_time != b.open_time + 299_999 for b in recent):
            raise ValueError("invalid 5m candle alignment")
        low, high = self._opening_range(bars, decision)
        widths = []
        for days in range(1, self.lookback_days + 1):
            past_low, past_high = self._opening_range(bars, decision - timedelta(days=days))
            widths.append(past_high - past_low)
        baseline = sum(widths) / len(widths)
        if baseline <= 0 or high <= low or high - low > self.max_contraction * baseline:
            raise ValueError("opening range has not contracted enough")
        previous = bars[-2]
        direction = (
            "LONG" if previous.close <= high < trigger.close
            else "SHORT" if previous.close >= low > trigger.close else None
        )
        if direction is None:
            raise ValueError("waiting for a fresh closing breakout")
        # At 10:05 the preceding five bars all belong to today's opening session.
        average_volume = sum(bar.volume for bar in bars[-6:-1]) / 5
        if average_volume <= 0 or trigger.volume < self.volume_multiplier * average_volume:
            raise ValueError("breakout volume below 1.1x prior five bars")
        value = volatility(recent)
        if value <= 0:
            raise ValueError("no usable ATR")
        return direction, value

    def assess(self, ctx: StrategyContext) -> Setup:
        try:
            direction, _ = self.signal(ctx)
            return Setup.entry(direction, "IVB contracted opening range with volume confirmation")
        except ValueError as exc:
            return Setup.waiting(str(exc))

    def levels(self, ctx: StrategyContext, entry: Decimal, direction: str) -> Levels:
        actual_direction, value = self.signal(ctx)
        if direction != actual_direction:
            raise ValueError("IVB signal direction changed")
        distance = Decimal(str(value))
        sign = Decimal(1 if direction == "LONG" else -1)
        levels = Levels(entry - sign * 3 * distance, entry + sign * 2 * distance)
        if min(levels.stop, levels.target) <= 0:
            raise ValueError("IVB bracket crosses zero")
        return levels

    def describe(self) -> dict[str, object]:
        return {
            **super().describe(), "timeframe": "5m", "research_only": True,
            "backtest_only": True, "group": "ivb-video",
            "source": "docs/research/ivb-video/README.md",
            "max_trades_per_session": self.max_trades_per_session,
            "parameters": {"ib_lookback_days": 25, "max_contraction": 0.7,
                           "volume_multiplier": 1.1, "volume_lookback": 5,
                           "atr_period": 14, "stop_atr": 3, "target_atr": 2},
        }
