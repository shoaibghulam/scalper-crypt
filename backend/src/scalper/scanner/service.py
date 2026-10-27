"""Scan caching.

A scan costs roughly fifty requests and four seconds, so it must not run once
per browser poll. Results are cached briefly and shared: a dozen open tabs
produce one scan, not a dozen.

The cache deliberately serves a stale result while a refresh is in flight
rather than queueing callers behind it. Stale market data with a visible age is
more useful than a spinner, and the dashboard shows that age.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, replace
from typing import Any

from scalper.exchange.market_data import MarketDataClient
from scalper.scanner.config import ScanConfig
from scalper.scanner.scan import ScanResult, run_scan
from scalper.strategies import DEFAULT_STRATEGY_ID, STRATEGIES

log = logging.getLogger("scalper.scanner")

CACHE_TTL_S = 20.0

#: A forced refresh still cannot run more often than this. Without a floor,
#: repeated force requests queue behind the lock and each runs a full scan.
MIN_FORCED_INTERVAL_S = 5.0


class ScannerService:
    def __init__(
        self,
        base_url: str,
        config: ScanConfig | None = None,
        strategy_id: str = DEFAULT_STRATEGY_ID,
    ) -> None:
        self.base_url = base_url
        self.config = config or ScanConfig()
        self.strategy = STRATEGIES.get(strategy_id)
        self._result: ScanResult | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def age_seconds(self) -> float | None:
        if self._result is None:
            return None
        return time.monotonic() - self._fetched_at

    def _is_fresh(self) -> bool:
        age = self.age_seconds
        return age is not None and age < CACHE_TTL_S

    async def get(self, force: bool = False) -> ScanResult | None:
        if self._is_fresh() and not force:
            return self._result

        # Recorded before waiting for the lock, so that a caller which queued
        # behind a scan can tell that scan already satisfied it.
        requested_at = time.monotonic()

        # One scan at a time. Without the lock, a burst of requests on a cold
        # cache would each start their own scan and multiply the request weight
        # by the number of callers.
        async with self._lock:
            if self._is_fresh() and not force:
                return self._result

            # A forced refresh that completed while this caller was waiting
            # satisfies it too. Skipping this check let every queued force
            # request run its own full scan, which is roughly fifty requests
            # each and the documented route to a multi-day exchange ban.
            if self._result is not None and self._fetched_at > requested_at:
                return self._result

            # And a floor on how often a force can trigger real work at all.
            age = self.age_seconds
            if force and age is not None and age < MIN_FORCED_INTERVAL_S:
                return self._result
            try:
                async with MarketDataClient(self.base_url) as market:
                    self._result = await run_scan(market, self.config, self.strategy)
                self._fetched_at = time.monotonic()
                self.last_error = None
            except Exception as exc:
                # Keep the previous result. A failed refresh should degrade the
                # age shown in the interface rather than blanking the screen.
                #
                # Logged with a traceback because this catch is broad enough to
                # swallow a genuine programming fault, and a one-line string in
                # the interface is not enough to diagnose one.
                log.exception("market scan failed")
                self.last_error = str(exc)
        return self._result

    def describe(self) -> dict[str, Any]:
        result = self._result
        if result is None:
            return {
                "ready": False,
                "error": self.last_error,
                "strategy": self.strategy.describe(),
                "candidates": [],
                "rejections": [],
                "stats": {},
            }

        now = time.time()
        candidates = [
            replace(c, setup_ready=False, setup_note="signal expired; wait for a new close")
            if c.setup_ready and c.expires_at is not None and now > c.expires_at
            else c
            for c in result.candidates
        ]
        return {
            "ready": True,
            "error": self.last_error,
            "strategy": self.strategy.describe(),
            "age_seconds": self.age_seconds,
            "scanned_at": result.scanned_at,
            "stats": {
                "universe_size": result.universe_size,
                "shortlist_size": result.shortlist_size,
                "candidate_count": len(result.candidates),
                "ready_count": sum(1 for c in candidates if c.setup_ready),
                "duration_s": result.duration_s,
                "rejection_counts": result.rejection_counts,
            },
            "candidates": [{**asdict(c), "regime": c.regime.value} for c in candidates],
            # Capped: the full list is several hundred entries, almost all of
            # them the same liquidity rejection, and the point is to show the
            # operator why rather than to dump the universe.
            "rejections": [asdict(r) for r in result.rejections[:80]],
        }
