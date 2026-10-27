"""The engine-to-dashboard link, over a real socket.

This covers the bug that produced a dashboard reading "engine not connected"
while the engine was running perfectly: nothing was ever wired between the two
processes, so the status was hardcoded.

A unit test of the state machine cannot catch that, because the state machine
was correct. Only running a real publisher and a real subscriber against each
other proves the pipe exists.
"""

import asyncio
import contextlib
import socket

import pytest

from scalper.api.engine_link import EngineLink, LinkState
from scalper.api.subscriber import subscribe
from scalper.engine.publisher import SnapshotPublisher


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.mark.asyncio
async def test_a_snapshot_reaches_the_dashboard():
    port = free_port()
    publisher = SnapshotPublisher(
        "127.0.0.1", port, lambda: {"totals": {"open_positions": 3}}
    )
    link = EngineLink()
    stop = asyncio.Event()

    serving = asyncio.create_task(publisher.serve_forever())
    subscribing = asyncio.create_task(
        subscribe(f"ws://127.0.0.1:{port}/engine", link, stop)
    )

    try:
        assert await wait_for(lambda: link.connected), "no snapshot ever arrived"
        assert link.payload["totals"]["open_positions"] == 3
        assert link.seq >= 1
    finally:
        stop.set()
        subscribing.cancel()
        serving.cancel()
        for task in (subscribing, serving):
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_the_sequence_advances_so_gaps_are_detectable():
    port = free_port()
    publisher = SnapshotPublisher("127.0.0.1", port, dict)
    link = EngineLink()
    stop = asyncio.Event()

    serving = asyncio.create_task(publisher.serve_forever())
    subscribing = asyncio.create_task(
        subscribe(f"ws://127.0.0.1:{port}/engine", link, stop)
    )

    try:
        assert await wait_for(lambda: link.seq >= 1)
        first = link.seq
        assert await wait_for(lambda: link.seq > first, timeout=4.0), (
            "the publisher stopped emitting after the initial snapshot"
        )
    finally:
        stop.set()
        subscribing.cancel()
        serving.cancel()
        for task in (subscribing, serving):
            with contextlib.suppress(asyncio.CancelledError):
                await task


@pytest.mark.asyncio
async def test_losing_the_engine_is_reported_as_down():
    # The supervisor restarts the engine, so the dashboard must notice the gap
    # and then recover, rather than silently keeping the last value forever.
    port = free_port()
    publisher = SnapshotPublisher("127.0.0.1", port, dict)
    link = EngineLink()
    stop = asyncio.Event()

    serving = asyncio.create_task(publisher.serve_forever())
    subscribing = asyncio.create_task(
        subscribe(f"ws://127.0.0.1:{port}/engine", link, stop)
    )

    try:
        assert await wait_for(lambda: link.connected)
        serving.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await serving
        assert await wait_for(lambda: not link.connected), (
            "the dashboard kept claiming the engine was connected"
        )
        loop = asyncio.get_running_loop()
        assert link.state(now=loop.time()) is LinkState.DOWN
    finally:
        stop.set()
        subscribing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await subscribing


@pytest.mark.asyncio
async def test_the_subscriber_waits_for_an_engine_that_is_not_up_yet():
    # On a cold start the API can bind its port before the engine is listening.
    # That must be a retry, not a permanent failure.
    link = EngineLink()
    stop = asyncio.Event()
    subscribing = asyncio.create_task(
        subscribe(f"ws://127.0.0.1:{free_port()}/engine", link, stop)
    )
    await asyncio.sleep(0.4)
    assert subscribing.done() is False
    assert link.connected is False

    stop.set()
    subscribing.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await subscribing
