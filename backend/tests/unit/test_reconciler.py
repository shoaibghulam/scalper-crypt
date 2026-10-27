"""Startup reconciliation.

The exchange is the source of truth. Our record is a cache and an audit log,
and every catastrophic-loss story in this domain inverts that.

Four buckets, each needing a different response, and the dangerous one is the
orphan: a position on the exchange that we have no record of. The instinct is
to clean it up. Cancelling its orders removes the stop protecting a real
position, and flattening it closes a trade the operator may have opened by
hand. Both have happened in production systems. So an orphan is parked, given
a protective stop if it lacks one, and surfaced for a human.
"""

import pytest

from scalper.engine.reconciler import (
    Bucket,
    ExchangePosition,
    KnownPosition,
    reconcile,
)


def on_exchange(symbol: str, qty: float = 0.5, entry: float = 100.0) -> ExchangePosition:
    return ExchangePosition(symbol=symbol, quantity=qty, entry_price=entry)


def in_records(symbol: str, qty: float = 0.5, bot: str = "alpha") -> KnownPosition:
    return KnownPosition(symbol=symbol, quantity=qty, bot_id=bot)


class TestAdoption:
    def test_a_matching_position_is_adopted(self):
        result = reconcile([on_exchange("BTCUSDT")], [in_records("BTCUSDT")])
        assert result.bucket_of("BTCUSDT") is Bucket.ADOPT

    def test_an_adopted_position_keeps_its_bot(self):
        result = reconcile([on_exchange("BTCUSDT")], [in_records("BTCUSDT", bot="beta")])
        assert result.adopted[0].bot_id == "beta"

    def test_adoption_does_not_require_a_human(self):
        result = reconcile([on_exchange("BTCUSDT")], [in_records("BTCUSDT")])
        assert result.is_clean is True


class TestClosedWhileDown:
    def test_a_position_we_know_of_but_the_exchange_does_not_is_closed(self):
        result = reconcile([], [in_records("BTCUSDT")])
        assert result.bucket_of("BTCUSDT") is Bucket.CLOSED

    def test_closing_does_not_block_startup(self):
        # It closed by itself while we were down. That is ordinary.
        result = reconcile([], [in_records("BTCUSDT")])
        assert result.is_clean is True


class TestOrphans:
    def test_a_position_we_have_no_record_of_is_an_orphan(self):
        result = reconcile([on_exchange("ETHUSDT")], [])
        assert result.bucket_of("ETHUSDT") is Bucket.ORPHAN

    def test_an_orphan_blocks_trading(self):
        # Trading around a position we do not understand is how the same symbol
        # gets opened twice.
        result = reconcile([on_exchange("ETHUSDT")], [])
        assert result.is_clean is False

    def test_an_orphan_is_never_flattened_automatically(self):
        result = reconcile([on_exchange("ETHUSDT")], [])
        assert result.orphans[0].symbol == "ETHUSDT"
        assert result.close_requests == []

    def test_an_orphan_never_has_its_orders_cancelled(self):
        # The documented production failure: a cleanup sweep cancels the stop
        # protecting a position it did not recognise.
        result = reconcile([on_exchange("ETHUSDT")], [])
        assert result.cancel_requests == []

    def test_only_the_orphaned_symbol_is_blocked(self):
        result = reconcile(
            [on_exchange("ETHUSDT"), on_exchange("BTCUSDT")], [in_records("BTCUSDT")]
        )
        assert result.blocked_symbols == {"ETHUSDT"}


class TestSizeMismatch:
    def test_a_different_size_is_flagged(self):
        result = reconcile([on_exchange("BTCUSDT", qty=0.3)], [in_records("BTCUSDT", qty=0.5)])
        assert result.bucket_of("BTCUSDT") is Bucket.RESIZED

    def test_the_exchange_quantity_wins(self):
        result = reconcile([on_exchange("BTCUSDT", qty=0.3)], [in_records("BTCUSDT", qty=0.5)])
        assert result.resized[0].quantity == 0.3

    def test_a_partial_fill_during_the_gap_does_not_block_trading(self):
        result = reconcile([on_exchange("BTCUSDT", qty=0.3)], [in_records("BTCUSDT", qty=0.5)])
        assert result.is_clean is True


class TestZeroQuantity:
    def test_a_flat_exchange_entry_is_not_a_position(self):
        # positionRisk lists every symbol, most with zero quantity.
        result = reconcile([on_exchange("BTCUSDT", qty=0.0)], [])
        assert result.orphans == []
        assert result.is_clean is True


class TestReporting:
    def test_the_report_counts_every_bucket(self):
        result = reconcile(
            [on_exchange("BTCUSDT"), on_exchange("ETHUSDT"), on_exchange("SOLUSDT", qty=0.2)],
            [in_records("BTCUSDT"), in_records("SOLUSDT", qty=0.5), in_records("XRPUSDT")],
        )
        summary = result.describe()
        assert summary["adopted"] == 1
        assert summary["orphaned"] == 1
        assert summary["closed"] == 1
        assert summary["resized"] == 1

    def test_the_report_says_whether_trading_may_resume(self):
        assert reconcile([], []).describe()["clean"] is True

    def test_an_empty_account_reconciles_clean(self):
        result = reconcile([], [])
        assert result.is_clean is True
        assert result.adopted == []


class TestDeterminism:
    @pytest.mark.parametrize("repeat", range(3))
    def test_reconciling_twice_gives_the_same_answer(self, repeat):
        exchange = [on_exchange("BTCUSDT"), on_exchange("ETHUSDT")]
        known = [in_records("BTCUSDT")]
        first = reconcile(exchange, known).describe()
        second = reconcile(exchange, known).describe()
        assert first == second
