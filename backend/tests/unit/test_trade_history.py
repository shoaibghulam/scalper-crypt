"""Closed trades, and what they add up to.

Win rate on its own is the most misleading number in this domain. A cost-aware
study of 78 mean-reversion backtests found fourteen configurations that won 65
percent or more of their trades and still lost money; one returned minus 12.5
percent at a 68 percent win rate. So the summary leads with profit factor and
expectancy, and reports fees separately, because at this timeframe fees are a
large fraction of the edge and a gross figure flatters a losing strategy.
"""

from decimal import Decimal

import pytest

from scalper.engine.trades import ClosedTrade, TradeHistory


def trade(
    realised: str,
    symbol: str = "SOLUSDT",
    outcome: str = "tp",
    fees: str = "0.05",
    **overrides,
) -> ClosedTrade:
    base = dict(
        symbol=symbol,
        bot="bot-1",
        strategy="500-backtest-v1",
        side="LONG",
        quantity=Decimal("2.36"),
        entry=Decimal("105.61"),
        exit_price=Decimal("106.57"),
        leverage=5,
        realised=Decimal(realised),
        fees=Decimal(fees),
        outcome=outcome,
        opened_at=1000.0,
        closed_at=1180.0,
    )
    base.update(overrides)
    return ClosedTrade(**base)


class TestRecord:
    def test_a_trade_knows_how_long_it_was_held(self):
        assert trade("1").duration_seconds == 180.0

    def test_a_win_is_marked_as_one(self):
        assert trade("1.50").is_win is True

    def test_a_loss_is_not_a_win(self):
        assert trade("-0.80").is_win is False

    def test_a_scratch_is_not_a_win(self):
        # Zero after fees is not a victory.
        assert trade("0").is_win is False

    def test_it_reports_the_return_on_margin(self):
        # 50 USDT of margin behind a 5x position.
        record = trade("2.50")
        assert record.roe_pct == pytest.approx(
            float(Decimal("2.50") / (Decimal("105.61") * Decimal("2.36") / 5) * 100),
            abs=0.01,
        )


class TestHistory:
    def test_trades_are_kept_newest_first(self):
        history = TradeHistory()
        history.record(trade("1", closed_at=100.0))
        history.record(trade("2", closed_at=200.0))
        assert history.recent()[0].realised == Decimal("2")

    def test_it_is_capped_so_memory_does_not_grow_without_bound(self):
        history = TradeHistory(limit=3)
        for i in range(10):
            history.record(trade("1", closed_at=float(i)))
        assert len(history.recent()) == 3

    def test_the_cap_discards_the_oldest(self):
        history = TradeHistory(limit=2)
        history.record(trade("1", closed_at=1.0))
        history.record(trade("2", closed_at=2.0))
        history.record(trade("3", closed_at=3.0))
        assert [float(t.realised) for t in history.recent()] == [3.0, 2.0]


class TestSummary:
    def _history(self) -> TradeHistory:
        history = TradeHistory()
        history.record(trade("2.00", outcome="tp", fees="0.05"))
        history.record(trade("3.00", outcome="tp", fees="0.05"))
        history.record(trade("-1.00", outcome="sl", fees="0.05"))
        history.record(trade("-1.50", outcome="sl", fees="0.05"))
        return history

    def test_it_counts_trades(self):
        assert self._history().summary()["trades"] == 4

    def test_it_reports_the_win_rate(self):
        assert self._history().summary()["win_rate"] == pytest.approx(50.0)

    def test_profit_factor_is_gross_win_over_gross_loss(self):
        # 5.00 won against 2.50 lost.
        assert self._history().summary()["profit_factor"] == pytest.approx(2.0)

    def test_expectancy_is_the_average_result_per_trade(self):
        assert self._history().summary()["expectancy"] == pytest.approx(0.625)

    def test_fees_are_reported_separately(self):
        # A gross figure flatters a strategy that trades often.
        assert self._history().summary()["fees"] == pytest.approx(0.2)

    def test_it_counts_how_each_trade_ended(self):
        summary = self._history().summary()
        assert summary["tp_hits"] == 2
        assert summary["sl_hits"] == 2

    def test_an_empty_history_summarises_without_dividing_by_zero(self):
        summary = TradeHistory().summary()
        assert summary["trades"] == 0
        assert summary["win_rate"] is None
        assert summary["profit_factor"] is None

    def test_profit_factor_is_undefined_rather_than_infinite_with_no_losses(self):
        # Reporting infinity as a profit factor reads as a perfect strategy.
        history = TradeHistory()
        history.record(trade("1.00"))
        assert history.summary()["profit_factor"] is None

    def test_the_net_result_is_the_sum(self):
        assert self._history().summary()["net"] == pytest.approx(2.5)
