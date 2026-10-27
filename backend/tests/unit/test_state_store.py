"""Surviving a restart.

The engine kept bots and their positions in memory only. Restarting it
therefore turned every position it had opened itself into a position it could
not explain, which put reconciliation into the orphan branch and disabled
trading. From the operator side that reads as the system losing track of four
live trades it had opened minutes earlier, which is exactly what it was.

The file is written to be safe to lose, never safe to half-write: a truncated
or hand-edited file must leave the engine in the same state as no file at all,
because the alternative is refusing to start while positions are live.
"""

import json
from decimal import Decimal

from scalper.engine.bots import BotRegistry, ManagedPosition
from scalper.engine.store import StateStore
from scalper.engine.trades import ClosedTrade, TradeHistory


def a_position(symbol: str = "SOLUSDT") -> ManagedPosition:
    return ManagedPosition(
        symbol=symbol,
        side="LONG",
        quantity=Decimal("1.83"),
        entry=Decimal("109.62"),
        stop=Decimal("108.90"),
        target=Decimal("111.06"),
        leverage=10,
        opened_at=1758150000.0,
        stop_armed=True,
        mark=Decimal("109.50"),
    )


def a_registry() -> BotRegistry:
    registry = BotRegistry()
    bot = registry.create(
        symbols=["SOLUSDT"],
        amount=Decimal("200"),
        leverage=10,
        strategy="500-backtest-v1",
        name="Morning breakout",
    )
    bot.positions["SOLUSDT"] = a_position()
    bot.realised = Decimal("1.94")
    bot.tp_hits = 1
    return registry


class TestRoundTrip:
    def test_a_saved_registry_comes_back(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        store.save(a_registry())

        restored = store.load()
        assert [b.name for b in restored.bots.values()] == ["Morning breakout"]

    def test_positions_survive_with_their_levels_intact(self, tmp_path):
        # The stop and target are the whole point. A bot restored without them
        # holds a position it cannot manage or account for.
        store = StateStore(tmp_path / "state.json")
        store.save(a_registry())

        bot = next(iter(store.load().bots.values()))
        position = bot.positions["SOLUSDT"]
        assert position.stop == Decimal("108.90")
        assert position.target == Decimal("111.06")
        assert position.entry == Decimal("109.62")

    def test_decimals_do_not_become_floats(self, tmp_path):
        # Round-tripping through float loses the last place on a price that is
        # later compared against a tick size, which rejected a live order once.
        store = StateStore(tmp_path / "state.json")
        store.save(a_registry())

        position = next(iter(store.load().bots.values())).positions["SOLUSDT"]
        assert isinstance(position.entry, Decimal)
        assert str(position.entry) == "109.62"

    def test_counters_survive(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        store.save(a_registry())

        bot = next(iter(store.load().bots.values()))
        assert bot.tp_hits == 1
        assert bot.realised == Decimal("1.94")

    def test_bot_ids_are_stable_across_a_restart(self, tmp_path):
        # Client order ids are derived from the bot id. A new id after a
        # restart would let a duplicate order through the exchange guard.
        store = StateStore(tmp_path / "state.json")
        registry = a_registry()
        original = next(iter(registry.bots)) 
        store.save(registry)

        assert next(iter(store.load().bots)) == original


class TestDegradedFiles:
    def test_no_file_yields_an_empty_registry(self, tmp_path):
        assert StateStore(tmp_path / "missing.json").load().bots == {}

    def test_a_truncated_file_does_not_stop_the_engine(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"bots": [{"id": "abc"', encoding="utf-8")
        assert StateStore(path).load().bots == {}

    def test_a_bot_with_unreadable_fields_is_skipped_not_fatal(self, tmp_path):
        # One corrupt entry must not discard the others. Losing three good bots
        # to one bad record is a worse outcome than losing the bad one.
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps(
                {
                    "bots": [
                        {"id": "bad"},
                        {
                            "id": "good",
                            "name": "Keeper",
                            "strategy": "500-backtest-v1",
                            "symbols": ["BTCUSDT"],
                            "amount": "100",
                            "leverage": 5,
                            "state": "running",
                            "positions": [],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        restored = StateStore(path).load()
        assert [b.name for b in restored.bots.values()] == ["Keeper"]


class TestWriteSafety:
    def test_a_crash_mid_write_leaves_the_previous_file_intact(
        self, tmp_path, monkeypatch
    ):
        # The reason for writing to a temporary file and renaming. A direct
        # write that dies half way leaves a truncated file where a good one
        # was, and the engine then starts with no record of live positions.
        path = tmp_path / "state.json"
        store = StateStore(path)
        registry = a_registry()
        store.save(registry)
        before = path.read_text(encoding="utf-8")

        def explode(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("scalper.engine.store.json.dump", explode)
        store.save(registry)

        assert path.read_text(encoding="utf-8") == before
        assert StateStore(path).load().bots != {}

    def test_a_repeated_save_of_the_same_registry_is_byte_identical(self, tmp_path):
        # Keys are sorted on the way out, so an unchanged registry does not
        # produce a different file each time it is written.
        path = tmp_path / "state.json"
        store = StateStore(path)
        registry = a_registry()
        store.save(registry)
        before = path.read_text(encoding="utf-8")

        store.save(registry)
        assert path.read_text(encoding="utf-8") == before

    def test_no_stray_temporary_files_are_left_behind(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        store.save(a_registry())
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_a_failed_write_leaves_no_temporary_file_behind(
        self, tmp_path, monkeypatch
    ):
        # One per failed write would otherwise accumulate for the life of the
        # installation, in the directory the engine reads at startup.
        store = StateStore(tmp_path / "state.json")

        def explode(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("scalper.engine.store.json.dump", explode)
        store.save(a_registry())

        assert list(tmp_path.iterdir()) == []


def a_trade(symbol: str = "VVVUSDT", realised: str = "7.66") -> ClosedTrade:
    return ClosedTrade(
        symbol=symbol,
        bot="1 Minute Legend v1 2",
        strategy="1-minute-legend-v1",
        side="LONG",
        quantity=Decimal("74"),
        entry=Decimal("26.49"),
        exit_price=Decimal("27.33"),
        leverage=25,
        realised=Decimal(realised),
        fees=Decimal("0.23"),
        outcome="tp",
        opened_at=1758150000.0,
        closed_at=1758150878.0,
    )


class TestHistorySurvivesRestart:
    """The counters and the history have to come from the same place.

    The Overview tile counted 1 take-profit and 1 stop while the History tab
    showed zero trades. Both were correct about what they could see: per-bot
    counters were written to disk, closed trades were not, so a restart kept
    one and discarded the other. Two numbers describing the same events
    disagreeing on a screen about money is worse than either being absent.
    """

    def test_closed_trades_come_back(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        history = TradeHistory()
        history.record(a_trade())
        store.save(a_registry(), history)

        assert [t.symbol for t in store.load_history().recent()] == ["VVVUSDT"]

    def test_the_result_survives_as_a_decimal(self, tmp_path):
        # Round-tripping money through float loses the last place, and this
        # figure is reconciled against Binance by eye.
        store = StateStore(tmp_path / "state.json")
        history = TradeHistory()
        history.record(a_trade())
        store.save(a_registry(), history)

        trade = store.load_history().recent()[0]
        assert isinstance(trade.realised, Decimal)
        assert str(trade.realised) == "7.66"

    def test_the_summary_matches_after_a_restart(self, tmp_path):
        store = StateStore(tmp_path / "state.json")
        history = TradeHistory()
        history.record(a_trade(realised="7.66"))
        history.record(a_trade(symbol="ZECUSDT", realised="-1.20"))
        store.save(a_registry(), history)

        summary = store.load_history().summary()
        assert summary["trades"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1

    def test_history_is_reported_as_persistent(self, tmp_path):
        # The UI carries a warning that history is lost on restart. Once it is
        # not, that warning has to stop appearing or it becomes noise.
        store = StateStore(tmp_path / "state.json")
        store.save(a_registry(), TradeHistory())
        assert store.load_history().describe()["persistent"] is True

    def test_no_file_yields_an_empty_history(self, tmp_path):
        assert StateStore(tmp_path / "missing.json").load_history().recent() == []

    def test_a_corrupt_trade_is_skipped_not_fatal(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(
            json.dumps({"version": 1, "bots": [], "history": [{"symbol": "BAD"}]}),
            encoding="utf-8",
        )
        assert StateStore(path).load_history().recent() == []

    def test_saving_without_history_does_not_discard_it(self, tmp_path):
        # A footgun worth closing. Any later caller that saves only the
        # registry must not silently wipe the trade record.
        store = StateStore(tmp_path / "state.json")
        history = TradeHistory()
        history.record(a_trade())
        store.save(a_registry(), history)

        store.save(a_registry())
        assert len(store.load_history().recent()) == 1
