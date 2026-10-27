"""Publishes engine state to the API process.

A localhost websocket server rather than a shared database table or a message
broker. The process boundary is what matters here, not the transport: the API
must be restartable without touching a live position, and the engine must be
the only writer of trading state.

Snapshots are coalesced and emitted at a fixed rate rather than forwarded per
exchange tick. The operator asked to see profit and distance-to-target update
about once a second, which is exactly this, and throttling at the transport
means the dashboard cannot be made to stutter by a component written naively
later.

Each snapshot carries a monotonic sequence number so a subscriber can tell a
gap from a quiet period.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger("scalper.engine.publisher")

PUBLISH_INTERVAL_S = 1.0


class SnapshotPublisher:
    def __init__(
        self,
        host: str,
        port: int,
        build_snapshot: Callable[[], dict[str, Any]],
        on_command: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.build_snapshot = build_snapshot
        # Commands arrive on the same connection that carries state out. The
        # web process can therefore be restarted freely: it reconnects, and no
        # command is executed anywhere but here.
        self.on_command = on_command
        self.seq = 0
        self._clients: set[ServerConnection] = set()
        self._command_lock = asyncio.Lock()

    async def _handle(self, connection: ServerConnection) -> None:
        self._clients.add(connection)
        log.info("dashboard subscribed (%d connected)", len(self._clients))
        try:
            # Send immediately so a reconnecting dashboard does not wait a full
            # interval showing stale or empty values.
            await connection.send(self._encode())
            async for message in connection:
                await self._dispatch(message)
        finally:
            self._clients.discard(connection)
            log.info("dashboard unsubscribed (%d connected)", len(self._clients))

    async def _dispatch(self, message: str | bytes) -> None:
        """Run one command from the dashboard.

        Errors are caught and logged rather than propagated: a malformed
        command must not close the connection that every other dashboard is
        also using, and must never stop the engine managing open positions.
        """
        if self.on_command is None:
            return
        try:
            command = json.loads(message)
        except (TypeError, ValueError):
            log.warning("ignoring a command that is not valid JSON")
            return
        if not isinstance(command, dict):
            log.warning("ignoring a command that is not an object")
            return
        try:
            async with self._command_lock:
                await self.on_command(command)
        except Exception:
            log.exception("command failed: %s", command.get("type"))

    def _encode(self) -> str:
        self.seq += 1
        payload = self.build_snapshot()
        payload["seq"] = self.seq
        return json.dumps(payload)

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(PUBLISH_INTERVAL_S)
            if not self._clients:
                continue
            message = self._encode()
            # Gather rather than await in sequence: one slow subscriber must
            # not delay the others, and a dead one must not stall the loop.
            await asyncio.gather(
                *(client.send(message) for client in list(self._clients)),
                return_exceptions=True,
            )

    async def serve_forever(self) -> None:
        # Only the local API process connects here. Browser origins must not
        # be allowed to turn an unrelated website into a trading client.
        async with serve(self._handle, self.host, self.port, origins=[None]):
            log.info("publishing state on ws://%s:%d", self.host, self.port)
            await self._broadcast_loop()
