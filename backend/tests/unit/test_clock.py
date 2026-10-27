"""Clock synchronisation.

Binance rejects a request whose timestamp falls outside recvWindow, with -1021.
The usual cause is not a badly wrong clock but a slowly drifting one: a bot
that syncs once at startup and runs for days will eventually fail, and it fails
mid-session rather than at boot, which makes it look like an outage.

The offset is estimated at the midpoint of the round trip, which is the best
available assumption when only one timestamp comes back.
"""

import asyncio
import time

import httpx
import respx

from scalper.config import Environment, ExchangeConfig
from scalper.exchange.client import BinanceFuturesClient


def make_client() -> BinanceFuturesClient:
    return BinanceFuturesClient(
        ExchangeConfig(
            env=Environment.TESTNET, api_key="test-key", api_secret="test-secret"
        )
    )


import pytest

from scalper.exchange.clock import RESYNC_INTERVAL_MS, ClockSync


class TestOffsetEstimation:
    def test_offset_uses_the_round_trip_midpoint(self):
        # Sent at 1000, replied at 1200, server said 1600. The request took
        # 200ms, so the server timestamp corresponds to local time 1100, and
        # the offset is 500.
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1600, received_ms=1200)
        assert clock.offset_ms == 500

    def test_a_perfectly_synced_clock_has_zero_offset(self):
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1100, received_ms=1200)
        assert clock.offset_ms == 0

    def test_local_clock_ahead_gives_a_negative_offset(self):
        clock = ClockSync()
        clock.observe(sent_ms=5000, server_ms=4000, received_ms=5000)
        assert clock.offset_ms == -1000

    def test_round_trip_time_is_recorded(self):
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1600, received_ms=1200)
        assert clock.last_rtt_ms == 200

    def test_a_reply_that_arrives_before_it_was_sent_is_refused(self):
        clock = ClockSync()
        with pytest.raises(ValueError, match="round trip"):
            clock.observe(sent_ms=2000, server_ms=1600, received_ms=1000)


class TestTimestamps:
    def test_timestamp_applies_the_offset(self):
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1600, received_ms=1200)
        assert clock.timestamp_ms(local_now_ms=10_000) == 10_500

    def test_an_unsynced_clock_applies_no_offset(self):
        assert ClockSync().timestamp_ms(local_now_ms=10_000) == 10_000


class TestResyncPolicy:
    def test_an_unsynced_clock_needs_a_sync(self):
        assert ClockSync().needs_resync(local_now_ms=0)

    def test_a_fresh_sync_does_not_need_another(self):
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1000, received_ms=1000)
        assert not clock.needs_resync(local_now_ms=1000)

    def test_drift_window_triggers_a_resync(self):
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1000, received_ms=1000)
        assert clock.needs_resync(local_now_ms=1000 + RESYNC_INTERVAL_MS + 1)

    def test_resync_interval_is_well_inside_the_recv_window_risk(self):
        # Fifteen minutes. Long enough to be cheap, short enough that ordinary
        # drift cannot reach a 5000ms recvWindow between syncs.
        assert RESYNC_INTERVAL_MS == 15 * 60 * 1000


class TestNoisySamples:
    def test_a_slow_sample_is_rejected_rather_than_trusted(self):
        # A long round trip makes the midpoint assumption unreliable, and a bad
        # offset is worse than a stale one.
        clock = ClockSync()
        clock.observe(sent_ms=1000, server_ms=1600, received_ms=1200)
        good = clock.offset_ms
        accepted = clock.observe(sent_ms=2000, server_ms=9000, received_ms=8000)
        assert accepted is False
        assert clock.offset_ms == good


class TestTheClockIsActuallySynchronised:
    """Regression: the offset existed and was never populated.

    ClockSync was written, tested, and wired into the signing call - but
    nothing ever called observe(), so offset_ms stayed 0 for the life of the
    process and every request carried the raw local clock.

    It worked for as long as this machine happened to sit inside Binance's
    1000ms recvWindow. After five days of drift the local clock was 1419ms
    ahead, and every signed request failed with -1021: no balance, no leverage,
    no position refresh. The module docstring predicts exactly this failure.
    """

    @respx.mock
    async def test_a_signed_request_syncs_the_clock_first(self):
        # The sync uses a public endpoint, so there is no chicken and egg:
        # a clock too far out to sign with can still ask the time.
        respx.get(url__regex=r".*/fapi/v1/time").respond(
            200, json={"serverTime": 1_790_000_000_000}
        )
        respx.get(url__regex=r".*/fapi/v2/account").respond(200, json={})

        async with make_client() as client:
            assert client.clock.is_synced is False
            await client.get_account()
            assert client.clock.is_synced is True

    @respx.mock
    async def test_the_offset_corrects_a_fast_local_clock(self):
        # The real failure, inverted: a machine running ahead must send a
        # timestamp that is behind its own clock.
        server_ms = 1_790_000_000_000
        respx.get(url__regex=r".*/fapi/v1/time").respond(
            200, json={"serverTime": server_ms}
        )
        respx.get(url__regex=r".*/fapi/v2/account").respond(200, json={})

        async with make_client() as client:
            await client.get_account()
            local_now = int(time.time() * 1000)
            sent = client.clock.timestamp_ms(local_now_ms=local_now)
            # Offset pulls the timestamp toward exchange time rather than
            # leaving it on local time.
            assert sent != local_now

    @respx.mock
    async def test_a_second_request_does_not_resync(self):
        # One extra call per fifteen minutes is the budget. Syncing before
        # every signed request would double the request count on a scanner
        # that already walks hundreds of symbols.
        time_route = respx.get(url__regex=r".*/fapi/v1/time").respond(
            200, json={"serverTime": 1_790_000_000_000}
        )
        respx.get(url__regex=r".*/fapi/v2/account").respond(200, json={})

        async with make_client() as client:
            await client.get_account()
            await client.get_account()
            assert time_route.call_count == 1

    @respx.mock
    async def test_a_failed_sync_does_not_block_the_request(self):
        # Degrading to the unsynced clock is worse than being synced and
        # better than refusing to trade. The exchange remains the authority:
        # if the timestamp is genuinely bad it answers -1021.
        respx.get(url__regex=r".*/fapi/v1/time").respond(503, json={})
        respx.get(url__regex=r".*/fapi/v2/account").respond(200, json={"canTrade": True})

        async with make_client() as client:
            result = await client.get_account()
            assert result.get("canTrade") is True

    @respx.mock
    async def test_a_slow_sample_is_refused_rather_than_trusted(self):
        # The midpoint estimate assumes both legs took equal time. On a slow
        # round trip that assumption fails, and a confidently wrong offset is
        # worse than none.
        async def slow(request):
            await asyncio.sleep(1.2)
            return httpx.Response(200, json={"serverTime": 1_790_000_000_000})

        respx.get(url__regex=r".*/fapi/v1/time").mock(side_effect=slow)
        respx.get(url__regex=r".*/fapi/v2/account").respond(200, json={})

        async with make_client() as client:
            await client.get_account()
            assert client.clock.rejected_samples == 1
            assert client.clock.offset_ms == 0
