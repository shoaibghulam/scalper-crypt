"""What the API knows about the engine, and how confident it is.

The rule this module exists to enforce: never report healthy merely because
nothing has explicitly failed. A dashboard that shows green by default is worse
than one that shows nothing, because the operator stops checking.

So there are four states rather than a boolean. Unknown means we have not
reached the engine yet, which is different from the engine being down. Stale
means the connection is open but no data has arrived recently, which is the
dangerous one: without an age check it looks exactly like a quiet market.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

#: Snapshots arrive at 1 Hz, so anything older than this is a real gap rather
#: than ordinary jitter.
STALE_AFTER_S = 3.0


class LinkState(StrEnum):
    UNKNOWN = "unknown"
    LIVE = "live"
    STALE = "stale"
    DOWN = "down"


class EngineLink:
    """Tracks freshness of engine state, and keeps the last known payload."""

    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.seq = 0
        self._last_message_at: float | None = None
        self._disconnected = False

    @property
    def connected(self) -> bool:
        """Whether the engine has been heard from and has not dropped.

        Stale still counts as connected. The distinction between an open but
        silent link and a closed one matters for diagnosis, and flattening them
        would hide which one is happening.
        """
        return self._last_message_at is not None and not self._disconnected

    def on_snapshot(self, payload: dict[str, Any], *, now: float) -> None:
        if not isinstance(payload, dict):
            raise ValueError("engine snapshot must be an object")
        sequence = int(payload.get("seq", self.seq))
        self.payload = payload
        self.seq = sequence
        self._last_message_at = now
        self._disconnected = False

    def on_disconnect(self, *, now: float) -> None:
        # The payload is deliberately kept. Showing the last known values with
        # their age is more useful than blanking the screen, provided the
        # interface is honest that they are old.
        self._disconnected = True

    def age_seconds(self, *, now: float) -> float | None:
        if self._last_message_at is None:
            return None
        return now - self._last_message_at

    def is_stale(self, *, now: float) -> bool:
        age = self.age_seconds(now=now)
        return age is not None and age > STALE_AFTER_S

    def state(self, *, now: float) -> LinkState:
        if self._disconnected:
            return LinkState.DOWN
        if self._last_message_at is None:
            return LinkState.UNKNOWN
        return LinkState.STALE if self.is_stale(now=now) else LinkState.LIVE

    def describe(self, *, now: float) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "state": self.state(now=now).value,
            "seq": self.seq,
            "age_seconds": self.age_seconds(now=now),
            "last_message_at": self._last_message_at,
        }
