"""A crowded IP is not the same emergency as a ban.

`x-mbx-used-weight-1m` counts the whole address, not this process. On a shared
or NAT'd IP it can sit near the limit because of traffic we did not send -
measured here at 1,228 weight a minute of our own against a 4,800 pause, with
a pause firing in a minute where we sent nothing at all, and zero 429s in the
entire run.

Backing bulk market data off that signal is right. Disabling trading on it is
not: reconciliation needs a handful of weight-5 account reads, those are never
what exhausts a budget, and refusing them strands the fleet over someone
else's traffic. A real 418/429 is different - that is us, and everything
stops.
"""

import time

import httpx
import pytest

from scalper.exchange import market_limits
from scalper.exchange.market_limits import MarketLimit, blocked_for

URL = "https://example.test"


def response(status: int, weight: str | None = None) -> httpx.Response:
    headers = {"x-mbx-used-weight-1m": weight} if weight else {}
    return httpx.Response(status, request=httpx.Request("GET", URL + "/fapi/v1/klines"),
                          headers=headers)


@pytest.fixture(autouse=True)
def clean():
    market_limits.LIMITS.clear()
    yield
    market_limits.LIMITS.clear()


class TestCrowdedAddress:
    def test_a_high_shared_counter_pauses_bulk_market_data(self):
        gate = MarketLimit()
        gate.observe(response(200, "5000"))
        with pytest.raises(httpx.HTTPStatusError):
            gate.check(URL)

    def test_but_it_does_not_look_like_a_ban(self):
        # What the engine asks before deciding whether trading can continue.
        gate = MarketLimit()
        market_limits.LIMITS[URL] = gate
        gate.observe(response(200, "5000"))
        assert blocked_for(URL) > 0
        assert gate.banned_for(URL) == 0

    def test_essential_reads_are_still_allowed(self):
        gate = MarketLimit()
        gate.observe(response(200, "5000"))
        # Reconciliation must be able to run: a few weight-5 account reads are
        # never what exhausts a 6,000 budget.
        gate.check(URL, essential=True)


class TestRealBan:
    def test_a_429_blocks_everything_including_essential_reads(self):
        gate = MarketLimit()
        gate.observe(response(429))
        with pytest.raises(httpx.HTTPStatusError):
            gate.check(URL, essential=True)

    def test_a_418_blocks_everything_including_essential_reads(self):
        gate = MarketLimit()
        gate.observe(response(418))
        with pytest.raises(httpx.HTTPStatusError):
            gate.check(URL, essential=True)

    def test_a_ban_is_reported_as_one(self):
        gate = MarketLimit()
        market_limits.LIMITS[URL] = gate
        gate.observe(response(429))
        assert gate.banned_for(URL) > 0
        assert blocked_for(URL) > 0


class TestRecovery:
    def test_a_soft_pause_clears_when_the_counter_falls(self):
        gate = MarketLimit()
        gate.observe(response(200, "5000"))
        assert gate.soft_until > time.monotonic()
        gate.observe(response(200, "10"))
        gate.check(URL)

    def test_a_ban_does_not_clear_just_because_weight_fell(self):
        # The address is banned; a quiet counter proves nothing.
        gate = MarketLimit()
        gate.observe(response(429))
        gate.observe(response(200, "10"))
        with pytest.raises(httpx.HTTPStatusError):
            gate.check(URL, essential=True)
