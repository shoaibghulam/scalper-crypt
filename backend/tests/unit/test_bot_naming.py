"""Naming a bot something an operator recognises.

Every bot was called bot-1, bot-2, bot-3. With one bot that is adequate and
with five it is useless: the position table showed four rows all labelled
bot-2, which says nothing about what those trades were or why they were taken.

A name should survive being read a week later, so it defaults to the strategy
rather than to a counter, and the operator can override it.
"""

from decimal import Decimal

from scalper.engine.bots import BotRegistry, default_bot_name


class TestDefaultName:
    def test_the_strategy_leads_the_name(self):
        assert default_bot_name("500-backtest-v1", ["BTCUSDT"], 1) == "500 Backtest V1 1"

    def test_an_unknown_strategy_falls_back_to_its_id(self):
        # Rather than raising. A bot that exists must be nameable even if the
        # strategy registry has moved on since it was created.
        assert default_bot_name("mystery", ["BTCUSDT"], 2).startswith("mystery")

    def test_the_index_distinguishes_two_bots_on_one_strategy(self):
        first = default_bot_name("500-backtest-v1", ["BTCUSDT"], 1)
        second = default_bot_name("500-backtest-v1", ["BTCUSDT"], 2)
        assert first != second


class TestRegistryNaming:
    def _create(self, registry, **kwargs):
        base = dict(
            symbols=["BTCUSDT"],
            amount=Decimal("100"),
            leverage=5,
            strategy="500-backtest-v1",
        )
        base.update(kwargs)
        return registry.create(**base)

    def test_a_supplied_name_is_used(self):
        registry = BotRegistry()
        bot = self._create(registry, name="Morning breakout")
        assert bot.name == "Morning breakout"

    def test_no_name_falls_back_to_the_strategy(self):
        registry = BotRegistry()
        assert "bot-" not in self._create(registry).name

    def test_whitespace_only_names_are_treated_as_absent(self):
        registry = BotRegistry()
        assert self._create(registry, name="   ").name != "   "

    def test_a_name_is_trimmed(self):
        registry = BotRegistry()
        assert self._create(registry, name="  Scalp A  ").name == "Scalp A"

    def test_an_overlong_name_is_truncated_rather_than_rejected(self):
        # It is a label. Refusing to create a bot over a cosmetic field, after
        # the operator has chosen symbols and size, would be the wrong trade.
        registry = BotRegistry()
        bot = self._create(registry, name="x" * 200)
        assert 0 < len(bot.name) <= 64

    def test_duplicate_names_are_allowed_but_ids_stay_unique(self):
        # Two bots may legitimately be called the same thing. The id is what
        # the system keys on, and the operator sees their own label.
        registry = BotRegistry()
        first = self._create(registry, name="Scalp")
        second = self._create(registry, name="Scalp")
        assert first.id != second.id

    def test_the_index_counts_bots_on_the_same_strategy_only(self):
        registry = BotRegistry()
        self._create(registry, strategy="500-backtest-v1")
        other = self._create(registry, strategy="vwap-band-reversion")
        assert other.name.endswith("1")


class TestMarginMode:
    """Isolated or cross, chosen per bot rather than hardcoded.

    The engine used to set ISOLATED on every symbol regardless. The two modes
    are genuinely different risk postures and the choice belongs to the
    operator:

    ISOLATED caps a loss at the margin posted for that one position. A
    liquidation takes that margin and nothing else.

    CROSSED backs the position with the whole wallet balance. Liquidation is
    further away, which is why it feels safer, but the thing at stake is the
    entire account rather than one position's margin.

    Binance spells the second one CROSSED, not CROSS. Sending the wrong
    spelling is rejected, so the value is normalised rather than trusted.
    """

    def _create(self, registry, **kwargs):
        base = dict(
            symbols=["BTCUSDT"],
            amount=Decimal("100"),
            leverage=5,
            strategy="500-backtest-v1",
        )
        base.update(kwargs)
        return registry.create(**base)

    def test_isolated_is_the_default(self):
        # The conservative posture stays the default: an operator who does not
        # choose should not have their whole wallet backing a trade.
        registry = BotRegistry()
        assert self._create(registry).margin_mode == "ISOLATED"

    def test_cross_is_accepted(self):
        registry = BotRegistry()
        assert self._create(registry, margin_mode="CROSSED").margin_mode == "CROSSED"

    def test_the_common_misspelling_is_normalised(self):
        # Binance calls it CROSSED. "CROSS" is what everybody types, and every
        # UI labels it that way, so accept it rather than failing the order.
        registry = BotRegistry()
        assert self._create(registry, margin_mode="CROSS").margin_mode == "CROSSED"

    def test_case_is_normalised(self):
        registry = BotRegistry()
        assert self._create(registry, margin_mode="isolated").margin_mode == "ISOLATED"

    def test_an_unknown_mode_falls_back_to_isolated(self):
        # Never guess CROSSED from a value we do not recognise. Falling back to
        # the posture that risks less is the only safe direction to be wrong.
        registry = BotRegistry()
        assert self._create(registry, margin_mode="nonsense").margin_mode == "ISOLATED"

    def test_blank_falls_back_to_isolated(self):
        registry = BotRegistry()
        assert self._create(registry, margin_mode="").margin_mode == "ISOLATED"

    def test_the_mode_is_reported_to_the_dashboard(self):
        registry = BotRegistry()
        bot = self._create(registry, margin_mode="CROSSED")
        assert bot.describe()["margin_mode"] == "CROSSED"


class TestRemovingBots:
    """Clearing out bots that will never trade again.

    Eight bots sat permanently in error on the dashboard, every one of them
    failed by bugs that have since been fixed. There was no way to dismiss
    them, so the panel that exists to make failures visible was itself buried
    under stale ones - which is the same problem it was built to solve.

    Removing is a record operation, not a trading one. A bot still holding a
    position is refused, because deleting its record would turn a managed
    position into an orphan and disable trading on the next restart.
    """

    def _create(self, registry, **kwargs):
        base = dict(
            symbols=["BTCUSDT"],
            amount=Decimal("100"),
            leverage=5,
            strategy="500-backtest-v1",
        )
        base.update(kwargs)
        return registry.create(**base)

    def test_a_bot_holding_nothing_can_be_removed(self):
        registry = BotRegistry()
        bot = self._create(registry)
        assert registry.remove(bot.id) is True
        assert registry.bots == {}

    def test_a_bot_holding_a_position_is_refused(self):
        # Deleting the record would leave the exchange holding a position
        # nobody can explain, which disables trading on the next restart.
        from scalper.engine.bots import ManagedPosition

        registry = BotRegistry()
        bot = self._create(registry)
        bot.positions["BTCUSDT"] = ManagedPosition(
            symbol="BTCUSDT",
            side="LONG",
            quantity=Decimal("1"),
            entry=Decimal("100"),
            stop=Decimal("99"),
            target=Decimal("103"),
            leverage=5,
            opened_at=0.0,
        )
        assert registry.remove(bot.id) is False
        assert bot.id in registry.bots

    def test_removing_an_unknown_bot_is_not_an_error(self):
        # The dashboard may ask twice, or ask about a bot a restart already
        # dropped. Idempotent rather than raising.
        assert BotRegistry().remove("nope") is False

    def test_other_bots_are_untouched(self):
        registry = BotRegistry()
        keep = self._create(registry, name="Keep")
        drop = self._create(registry, name="Drop")
        registry.remove(drop.id)
        assert list(registry.bots) == [keep.id]
