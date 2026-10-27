"""What counts as a USDT perpetual symbol.

The rule was `[A-Z0-9]{2,25}USDT`, copied into ten places. It refused 18 of the
528 symbols Binance actually lists, and because one bad entry rejects a whole
list, "select all coins" could never start a run.
"""

import re

import pytest

from scalper.symbols import SYMBOL_PATTERN, is_symbol

#: Real listings the old two-character rule refused.
SINGLE_CHARACTER = ["AUSDT", "BUSDT", "CUSDT", "FUSDT", "TUSDT", "WUSDT", "4USDT"]
#: Real listings whose base is not ASCII.
NON_ASCII = ["\u54c8\u57fa\u7c73USDT", "\u9f99\u867eUSDT", "\u5e01\u5b89\u4eba\u751fUSDT"]


class TestAccepts:
    @pytest.mark.parametrize("symbol", ["BTCUSDT", "1000PEPEUSDT", "1MBABYDOGEUSDT"])
    def test_ordinary_symbols(self, symbol):
        assert is_symbol(symbol)

    @pytest.mark.parametrize("symbol", SINGLE_CHARACTER)
    def test_a_single_character_base(self, symbol):
        assert is_symbol(symbol)

    @pytest.mark.parametrize("symbol", NON_ASCII)
    def test_a_non_ascii_base(self, symbol):
        assert is_symbol(symbol)


class TestRefuses:
    @pytest.mark.parametrize(
        "symbol",
        [
            "",
            "USDT",
            "BTCUSD",
            "BTCBUSD",
            "not a symbol",
            "BTC-USDT",
            "BTC_USDT",
        ],
    )
    def test_what_is_not_a_symbol(self, symbol):
        assert not is_symbol(symbol)

    @pytest.mark.parametrize(
        "symbol",
        [
            "../../etc/passwdUSDT",
            "..USDT",
            "a/bUSDT",
            "a\bUSDT",
            "a.bUSDT",
            "a USDT",
            "a\x00USDT",
        ],
    )
    def test_anything_that_could_escape_a_directory(self, symbol):
        # A symbol becomes a directory name in the candle store, so path
        # separators and dots must never survive validation.
        assert not is_symbol(symbol)

    def test_a_base_longer_than_the_cap(self):
        assert not is_symbol("A" * 26 + "USDT")


def test_the_pattern_and_the_helper_agree():
    # Some call sites need a Pydantic `pattern=`, which cannot call a function.
    for symbol in [*SINGLE_CHARACTER, *NON_ASCII, "BTCUSDT"]:
        assert re.fullmatch(SYMBOL_PATTERN, symbol), symbol
    for symbol in ["BTC-USDT", "..USDT", "a/bUSDT"]:
        assert not re.fullmatch(SYMBOL_PATTERN, symbol), symbol
