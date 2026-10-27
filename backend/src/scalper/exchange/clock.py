"""Clock synchronisation against exchange time.

Binance rejects a request whose timestamp falls outside recvWindow, with -1021.
The usual cause is not a badly wrong clock but a slowly drifting one, so a bot
that synchronises once at startup and then runs for days fails mid-session
rather than at boot, which makes the failure look like an outage.

Only one timestamp comes back from the exchange, so the offset is estimated at
the midpoint of the round trip. That assumes the outbound and inbound legs took
equal time, which is reasonable for a fast request and unreliable for a slow
one. Slow samples are therefore discarded rather than trusted: a stale offset
is better than a confidently wrong one.
"""

from __future__ import annotations

RESYNC_INTERVAL_MS = 15 * 60 * 1000

# Beyond this the midpoint assumption stops being credible. A healthy request
# to Binance completes in tens of milliseconds.
MAX_TRUSTED_RTT_MS = 1000


class ClockSync:
    """Tracks the offset between the local clock and exchange time."""

    def __init__(self) -> None:
        self.offset_ms = 0
        self.last_rtt_ms: int | None = None
        self.last_sync_local_ms: int | None = None
        self.rejected_samples = 0

    @property
    def is_synced(self) -> bool:
        return self.last_sync_local_ms is not None

    def observe(self, *, sent_ms: int, server_ms: int, received_ms: int) -> bool:
        """Record one time sample. Returns whether it was accepted."""
        rtt = received_ms - sent_ms
        if rtt < 0:
            raise ValueError(
                f"round trip cannot be negative (sent {sent_ms}, received {received_ms})"
            )

        if rtt > MAX_TRUSTED_RTT_MS:
            # Keeping the previous offset is the conservative choice.
            self.rejected_samples += 1
            return False

        local_midpoint = sent_ms + rtt // 2
        self.offset_ms = server_ms - local_midpoint
        self.last_rtt_ms = rtt
        self.last_sync_local_ms = received_ms
        return True

    def timestamp_ms(self, *, local_now_ms: int) -> int:
        """Local time expressed in exchange time."""
        return local_now_ms + self.offset_ms

    def needs_resync(self, *, local_now_ms: int) -> bool:
        if self.last_sync_local_ms is None:
            return True
        return local_now_ms - self.last_sync_local_ms > RESYNC_INTERVAL_MS
