import httpx
import pytest

from scalper.exchange.market_data import MarketDataClient
from scalper.exchange.market_limits import (
    BACKOFF_STEPS,
    LIMITS,
    WEIGHT_HEADER,
    WEIGHT_PAUSE_AT,
    MarketLimit,
    describe,
    paced_get,
    request_weight,
)


def test_weights_charge_whole_market_ticker_more_than_single_coin():
    assert request_weight('/fapi/v1/ticker/24hr', None) == 40
    assert request_weight('/fapi/v1/ticker/24hr', {'symbol': 'XTZUSDT'}) == 1
    assert request_weight('/fapi/v1/klines', {'limit': 500}) == 5


async def test_cooldown_shared_between_clients_and_no_request_sent(monkeypatch):
    from scalper.exchange import market_limits
    monkeypatch.setattr(market_limits, 'LIMITS', {})
    # market_data imports the dictionary, so isolate both references.
    monkeypatch.setattr('scalper.exchange.market_data.LIMITS', market_limits.LIMITS)
    calls = []
    def reply(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '120'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply), base_url='https://test.invalid') as http:
        first = MarketDataClient('https://test.invalid', http=http)
        second = MarketDataClient('https://test.invalid', http=http)
        with pytest.raises(httpx.HTTPStatusError):
            await first.tickers('XTZUSDT')
        with pytest.raises(httpx.HTTPStatusError) as failure:
            await second.book_tops('BCHUSDT')
        assert int(failure.value.response.headers['Retry-After']) >= 119
        assert len(calls) == 1


async def test_pacing_reserves_weight_without_overlapping_slots(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr('scalper.exchange.market_limits.time.monotonic', lambda: 100.0)
    sleep = AsyncMock()
    monkeypatch.setattr('scalper.exchange.market_limits.asyncio.sleep', sleep)
    gate = MarketLimit()
    await gate.acquire('https://test.invalid', 40)
    await gate.acquire('https://test.invalid', 1)
    assert sleep.await_args_list[1].args[0] == 2.0


async def test_single_symbol_quotes_send_symbol_and_parse_object(monkeypatch):
    from unittest.mock import AsyncMock
    market = MarketDataClient('https://test.invalid')
    market._get = AsyncMock(side_effect=[
        {'symbol': 'XTZUSDT', 'lastPrice': '1', 'priceChangePercent': '2', 'quoteVolume': '1000'},
        {'symbol': 'XTZUSDT', 'bidPrice': '1', 'askPrice': '1.01'},
    ])
    assert 'XTZUSDT' in await market.tickers('XTZUSDT')
    assert 'XTZUSDT' in await market.book_tops('XTZUSDT')
    assert all(c.args[1] == {'symbol': 'XTZUSDT'} for c in market._get.await_args_list)

def test_ban_deadline_and_http_date_are_respected(monkeypatch):
    monkeypatch.setattr('scalper.exchange.market_limits.time.time', lambda: 1000000000.0)
    monkeypatch.setattr('scalper.exchange.market_limits.time.monotonic', lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(418, json={'msg': 'IP banned until 1000000600000'}))
    assert gate.blocked_until == 700.0
    gate.observe(httpx.Response(429, headers={'Retry-After': 'garbage'}))
    assert gate.blocked_until == 700.0


# A twenty-minute ban arrived as -1003 over HTTP 429 and was served as a
# sixty-second pause, because the deadline was only read for 418. The scanner
# then knocked once a minute for twenty minutes, which is what makes Binance
# extend a ban rather than let it expire.
@pytest.mark.parametrize("status", [429, 418])
def test_a_stated_ban_deadline_is_read_whatever_the_status(monkeypatch, status):
    monkeypatch.setattr("scalper.exchange.market_limits.time.time", lambda: 1000000000.0)
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(status, json={
        "code": -1003,
        "msg": "Way too many requests; IP(1.2.3.4) banned until 1000001200000",
    }))
    assert gate.blocked_until == 1300.0   # 100 + 1200 seconds


def test_a_rate_limit_without_a_deadline_keeps_its_floor(monkeypatch):
    monkeypatch.setattr("scalper.exchange.market_limits.time.time", lambda: 1000000000.0)
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(429, json={"code": -1003, "msg": "Way too many requests"}))
    assert gate.blocked_until == 160.0


def test_the_refusal_says_why_and_for_how_long(monkeypatch):
    # "Market data cooling down" alone reads like a broken connection.
    monkeypatch.setattr("scalper.exchange.market_limits.time.time", lambda: 1000000000.0)
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(429, json={"msg": "banned until 1000001200000"}))
    with pytest.raises(httpx.HTTPStatusError) as refused:
        gate.check("https://test.invalid/fapi/v1/klines")
    assert "rate limit" in str(refused.value)
    assert "20 min" in str(refused.value)


def test_pause_lengths_read_as_something_to_act_on():
    assert describe(9.2) == "10s"
    assert describe(1200) == "about 20 min"


async def test_a_download_refuses_to_call_a_banned_address(monkeypatch):
    # The research downloader used its own client and never consulted this
    # budget, so it kept calling an address the rest of the process knew was
    # banned - and spent the allowance that got it banned in the first place.
    monkeypatch.setattr("scalper.exchange.market_limits.LIMITS", {})
    base = "https://test.invalid"
    LIMITS.clear()
    from scalper.exchange import market_limits

    market_limits.LIMITS.setdefault(base, MarketLimit()).observe(
        httpx.Response(429, json={"msg": "banned until 99999999999999"})
    )
    sent = []

    class Recording:
        async def get(self, url, params=None):
            sent.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), json=[])

    with pytest.raises(httpx.HTTPStatusError):
        await paced_get(Recording(), base, "/fapi/v1/klines", {"limit": 1500})
    assert sent == []


async def test_a_download_hitting_the_limit_slows_everything_else(monkeypatch):
    monkeypatch.setattr("scalper.exchange.market_limits.LIMITS", {})
    from scalper.exchange import market_limits

    base = "https://test.invalid"

    class Limited:
        async def get(self, url, params=None):
            return httpx.Response(
                429, request=httpx.Request("GET", url), json={"msg": "Way too many requests"}
            )

    await paced_get(Limited(), base, "/fapi/v1/klines", {"limit": 1500})
    assert market_limits.LIMITS[base].blocked_until > 0


def test_persistent_cooldown_survives_new_gate():
    import time

    from scalper.exchange.market_limits import save_cooldown, shared_deadline
    url = 'https://test.invalid/fapi/v2/account'
    deadline = time.time() + 300
    save_cooldown(url, deadline)
    save_cooldown(url, deadline - 100)
    assert shared_deadline(url) == deadline
    with pytest.raises(httpx.HTTPStatusError):
        MarketLimit().check(url)
    MarketLimit().check('https://different.invalid/fapi/v2/account')


def test_consecutive_limits_back_off_further_each_time(monkeypatch):
    """The escalation that turned a 60s pause into a 173 minute ban.

    Measured live: a -1003 soft limit paused reads for 60s, the very next
    signed call 84 seconds later landed while the address was still hot, and
    Binance answered 418 with a deadline almost three hours out. A flat retry
    interval is what converts a short limit into a long ban.
    """
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    seen = []
    for _ in BACKOFF_STEPS:
        gate.blocked_until = 0.0
        gate.observe(httpx.Response(429, json={"code": -1003, "msg": "Too many requests"}))
        seen.append(gate.blocked_until - 100.0)
    assert seen == list(BACKOFF_STEPS)
    assert seen == sorted(seen), "each retry must wait longer than the last"


def test_the_backoff_is_capped(monkeypatch):
    # Escalating for ever would leave the engine dark after a transient burst.
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    for _ in range(12):
        gate.blocked_until = 0.0
        gate.observe(httpx.Response(429, json={"msg": "Too many requests"}))
    assert gate.blocked_until - 100.0 == BACKOFF_STEPS[-1]


def test_a_clean_response_resets_the_streak_only_once_the_block_expired(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: now[0])
    gate = MarketLimit()
    gate.observe(httpx.Response(429, json={"msg": "Too many requests"}))
    assert gate.strikes == 1
    # Still inside the pause: a local refusal is not evidence the limit lifted.
    gate.observe(httpx.Response(200, json={}))
    assert gate.strikes == 1
    now[0] += BACKOFF_STEPS[0] + 1
    gate.observe(httpx.Response(200, json={}))
    assert gate.strikes == 0


def test_a_stated_ban_deadline_still_wins_over_the_step(monkeypatch):
    monkeypatch.setattr("scalper.exchange.market_limits.time.time", lambda: 1000000000.0)
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(429, json={"msg": "banned until 1000010000000"}))
    assert gate.blocked_until == 10100.0


def test_the_used_weight_header_pauses_before_a_429(monkeypatch):
    """Binance's documented obligation, which we were ignoring.

    The per-IP weight arrives on every response, success included. We read it
    and threw it away, so the first signal we ever acted on was the 429 - and
    the retry after that is what earns the ban. Backing off while the budget
    is nearly spent is what prevents the sequence starting.
    """
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(200, headers={WEIGHT_HEADER: str(WEIGHT_PAUSE_AT)}, json={}))
    assert gate.used_weight == WEIGHT_PAUSE_AT
    # Bulk market data stops, which is what prevents the 429-then-ban sequence.
    assert gate.soft_until == 160.0
    with pytest.raises(httpx.HTTPStatusError):
        gate.check("https://example.test")
    # ...but it is a precaution about a shared counter, not a ban we earned,
    # so it must not read as one. Account reads still have to get through.
    assert gate.banned_for("https://example.test") == 0
    gate.check("https://example.test", essential=True)


def test_a_quiet_minute_is_not_throttled(monkeypatch):
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(200, headers={WEIGHT_HEADER: "120"}, json={}))
    assert gate.used_weight == 120
    assert gate.blocked_until == 0.0


def test_a_missing_or_junk_weight_header_is_survivable(monkeypatch):
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(200, json={}))
    gate.observe(httpx.Response(200, headers={WEIGHT_HEADER: "not-a-number"}, json={}))
    assert gate.blocked_until == 0.0


def test_weight_from_a_shared_address_still_throttles_us(monkeypatch):
    # The counter is per IP, so traffic we did not send still counts. That is
    # precisely the case that has been banning this setup.
    monkeypatch.setattr("scalper.exchange.market_limits.time.monotonic", lambda: 100.0)
    gate = MarketLimit()
    gate.observe(httpx.Response(200, headers={WEIGHT_HEADER: "5900"}, json={}))
    assert gate.soft_until > 100.0
    with pytest.raises(httpx.HTTPStatusError):
        gate.check("https://example.test")
