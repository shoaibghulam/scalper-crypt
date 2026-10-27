"""Subscribes the API process to engine state.

Read-only by construction. This task receives snapshots and updates an in
memory view; it never sends a trading instruction, so running it under multiple
uvicorn workers is harmless in a way that an embedded engine would not be.

Reconnection is unbounded and backed off. The engine is restarted by the
supervisor when it crashes, so the dashboard has to assume the link will come
back and keep trying rather than giving up and requiring a page refresh.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from websockets.asyncio.client import connect

from scalper.api.engine_link import EngineLink

log = logging.getLogger("scalper.api.subscriber")

BACKOFF_START_S = 0.5
BACKOFF_CAP_S = 10.0


async def subscribe(
    url: str,
    link: EngineLink,
    stop: asyncio.Event,
    outbound: asyncio.Queue[str] | None = None,
) -> None:
    backoff = BACKOFF_START_S

    while not stop.is_set():
        try:
            async with connect(url, open_timeout=3, max_size=16 * 1024 * 1024) as connection:
                log.info("subscribed to engine at %s", url)
                backoff = BACKOFF_START_S

                async def pump() -> None:
                    """Forward operator commands to the engine."""
                    assert outbound is not None
                    while not stop.is_set():
                        command = await outbound.get()
                        await connection.send(command)

                sender = (
                    asyncio.create_task(pump()) if outbound is not None else None
                )
                try:
                    async for message in connection:
                        link.on_snapshot(json.loads(message), now=time.monotonic())
                        if stop.is_set():
                            break
                finally:
                    if sender is not None:
                        sender.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await sender
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Expected whenever the engine is restarting. Logged at debug so a
            # normal supervisor restart does not fill the log with warnings.
            log.debug("engine link unavailable (%s); retrying in %.1fs", exc, backoff)

        link.on_disconnect(now=time.monotonic())

        if stop.is_set():
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        backoff = min(BACKOFF_CAP_S, backoff * 2)
