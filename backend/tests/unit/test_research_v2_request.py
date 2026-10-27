"""What an offline run is allowed to ask for.

The v2 tab exists to run over the whole local store at once, so the coin cap
has to cover the full USDT perpetual universe. It was 25, which turned "select
all coins" into a bare 422 in the browser with no reason attached.
"""

import pytest
from pydantic import ValidationError

from scalper.api.research import catalog
from scalper.api.research_v2 import MAX_SYMBOLS, V2Request


def request(**overrides):
    payload = {"symbols": ["BTCUSDT"], "strategies": ["ctk-1"], **overrides}
    return V2Request(**payload)


class TestSymbolLimit:
    def test_the_whole_listed_universe_fits_in_one_run(self):
        # Binance lists ~530 USDT perpetuals; picking all of them is the
        # headline use of an offline tab.
        symbols = [f"C{index}USDT" for index in range(528)]
        assert len(request(symbols=symbols).symbols) == 528

    def test_the_cap_is_still_a_real_bound(self):
        with pytest.raises(ValidationError):
            request(symbols=[f"C{index}USDT" for index in range(MAX_SYMBOLS + 1)])

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(ValidationError):
            request(symbols=[])

    def test_an_invalid_symbol_is_still_refused_at_scale(self):
        # A long list must not become a way past the symbol check.
        symbols = [f"C{index}USDT" for index in range(400)] + ["not a symbol"]
        with pytest.raises(ValidationError):
            request(symbols=symbols)

    def test_duplicates_collapse_so_the_cap_counts_real_work(self):
        assert request(symbols=["BTCUSDT", "btcusdt", "BTCUSDT"]).symbols == ["BTCUSDT"]


class TestStrategyLimit:
    def test_the_whole_catalog_fits_in_one_run(self):
        # "Select all" in the picker sends every id the catalog offers, so the
        # cap has to clear the list the UI itself hands the operator.
        offered = [row["id"] for row in catalog()]
        assert len(offered) > 50, "the old cap would not have been a bug"
        assert len(request(strategies=offered).strategies) == len(offered)

    def test_an_unknown_strategy_is_still_refused(self):
        # The cap is not the only guard: order-flow strategies need event data
        # the offline store does not hold.
        with pytest.raises(ValidationError):
            request(strategies=["ctk-1", "not-a-strategy"])

    def test_an_empty_selection_is_refused(self):
        with pytest.raises(ValidationError):
            request(strategies=[])
