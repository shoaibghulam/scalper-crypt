"""Shared per-process market-data pacing; private protective orders bypass it."""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from contextlib import suppress
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from scalper.envfiles import STATE_DIR

COOLDOWN_DIR = STATE_DIR / "exchange-cooldowns"
log = logging.getLogger(__name__)


def _cooldown_path(url: str) -> Path:
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return COOLDOWN_DIR / (hashlib.sha256(origin.encode()).hexdigest() + ".json")


def shared_deadline(url: str) -> float:
    try:
        value = float(json.loads(_cooldown_path(url).read_text())["until"])
        return value if math.isfinite(value) else 0.0
    except (OSError, ValueError, KeyError, TypeError):
        return 0.0


def save_cooldown(url: str, until: float) -> None:
    """Share ban deadlines between API/engine processes and preserve them on restart."""
    path = _cooldown_path(url)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        until = max(until, shared_deadline(url))
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump({"until": until}, file)
        os.replace(temporary, path)
    except OSError:
        log.warning("Cannot persist exchange cooldown; in-process cooldown remains active")
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def describe(seconds: float) -> str:
    """A pause length an operator can act on."""
    if seconds >= 90:
        return f"about {round(seconds / 60)} min"
    return f"{max(1, math.ceil(seconds))}s"


#: Consecutive rate limits back off 60s, 2m, 4m, 8m, then 16m. A flat pause
#: turned a sixty second limit into a 173 minute ban: the first retry landed
#: while the address was still hot and Binance answered 418 with a three hour
#: deadline. Waiting longer each time costs minutes; not waiting costs hours.
BACKOFF_STEPS = (60.0, 120.0, 240.0, 480.0, 960.0)
BAN_FLOOR = 120.0


#: Binance's per-IP request-weight budget, and where we stop spending it.
#: Reported on every response as X-MBX-USED-WEIGHT-1M; its documentation says
#: a caller that keeps going after a 429 will be auto-banned, so the useful
#: move is to never reach one.
WEIGHT_CAP = 6000
WEIGHT_PAUSE_AT = 4800
WEIGHT_HEADER = "x-mbx-used-weight-1m"


class MarketLimit:
    def __init__(self) -> None:
        self.next_at = 0.0
        #: A ban we actually earned: 418/429, or a deadline shared by another
        #: process. Nothing goes out while this holds, essential or not.
        self.blocked_until = 0.0
        #: A precaution, from the per-IP weight counter being high. That
        #: counter includes traffic this process did not send, so it throttles
        #: bulk market data but must not strand reconciliation.
        self.soft_until = 0.0
        #: Last per-IP weight Binance reported, for the dashboard and tests.
        self.used_weight = 0
        #: Consecutive limits since the last clean response.
        self.strikes = 0

    def banned_for(self, url: str) -> float:
        """Seconds of real ban left, ignoring any precautionary pause."""
        return max(0.0, self.blocked_until - time.monotonic(),
                   shared_deadline(url) - time.time())

    def check(self, url: str, essential: bool = False) -> None:
        """Refuse a request that should not go out yet.

        `essential` marks the small, fixed-cost reads trading correctness
        depends on - reconciliation, position and balance checks. They are
        exempt from the precautionary pause because a handful of weight-5
        calls is never what exhausts a 6,000 budget, and blocking them hands
        someone else's traffic the power to stop this account trading.
        """
        remaining = self.banned_for(url)
        if not essential:
            remaining = max(remaining, self.soft_until - time.monotonic())
        if remaining > 0:
            request = httpx.Request("GET", url)
            response = httpx.Response(429, request=request,
                                      headers={"Retry-After": str(math.ceil(remaining))})
            # Say how long, and why. "Cooling down" with no end in sight reads
            # like a broken connection; the operator needs to know this is an
            # exchange rate limit that clears on its own.
            raise httpx.HTTPStatusError(
                f"Market data cooling down: exchange rate limit, {describe(remaining)} left",
                request=request, response=response)

    async def acquire(self, url: str, weight: int) -> None:
        self.check(url)
        # Reserve without awaiting so concurrent clients share the same schedule.
        now = time.monotonic()
        slot = max(now, self.next_at)
        self.next_at = slot + weight / 20
        await asyncio.sleep(max(0.0, slot - now))
        self.check(url)

    def observe(self, response: httpx.Response) -> None:
        # Binance reports the running per-IP weight on EVERY response, success
        # or failure, and its documentation makes backing off on it the
        # caller's obligation. We were reading this header and throwing it
        # away, so the first thing we ever noticed was the 429 - by which
        # point the next retry earns a ban. Pausing out the remainder of the
        # minute once the budget is mostly spent is what stops that happening.
        #
        # The counter is per IP, so on a shared address it also reflects other
        # traffic: exactly the case that has been banning us.
        raw = response.headers.get(WEIGHT_HEADER)
        if raw is not None:
            try:
                self.used_weight = int(raw)
            except (TypeError, ValueError):
                self.used_weight = 0
            if self.used_weight >= WEIGHT_PAUSE_AT:
                # Precautionary only: this counter is per IP, so a crowded
                # address must not be treated as a ban this process earned.
                self.soft_until = max(self.soft_until, time.monotonic() + 60.0)
            elif self.used_weight < WEIGHT_PAUSE_AT // 2:
                # The address is quiet again; no reason to keep throttling.
                self.soft_until = 0.0
        if response.status_code not in {418, 429}:
            # Only a call that actually went out can clear the streak. While
            # the block holds, requests are refused locally and would
            # otherwise reset the count without proving anything.
            if self.blocked_until <= time.monotonic():
                self.strikes = 0
            return
        self.strikes += 1
        step = BACKOFF_STEPS[min(self.strikes - 1, len(BACKOFF_STEPS) - 1)]
        delay = max(step, BAN_FLOOR) if response.status_code == 418 else step
        # Binance states the IP-ban deadline in the error message, and sends it
        # with 429 as well as 418 - a -1003 "banned until" arrives as 429 first
        # and only becomes 418 if you keep knocking. Reading it for 418 alone
        # meant a twenty-minute ban was served as a sixty-second pause, so the
        # scanner kept retrying into a banned IP, which is what extends a ban.
        try:
            match = re.search(r"banned until (\d{13})", str(response.json().get("msg", "")))
            if match:
                delay = max(delay, int(match.group(1)) / 1000 - time.time())
        except (ValueError, AttributeError, TypeError):
            pass
        value = response.headers.get("Retry-After")
        if value:
            try:
                parsed = float(value)
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    parsed = delay
            if math.isfinite(parsed):
                delay = max(delay, parsed)
        self.blocked_until = max(self.blocked_until, time.monotonic() + delay)
        # Synthetic responses without a request still retain in-memory cooldown.
        with suppress(RuntimeError):
            save_cooldown(str(response.request.url), time.time() + delay)


LIMITS: dict[str, MarketLimit] = {}


def request_weight(path: str, params: dict[str, Any] | None) -> int:
    params = params or {}
    if path.endswith("/ticker/24hr"):
        return 1 if params.get("symbol") else 40
    if path.endswith("/ticker/bookTicker"):
        return 2 if params.get("symbol") else 5
    if path.endswith("/klines"):
        limit = int(params.get("limit", 200))
        return 1 if limit < 100 else 2 if limit < 500 else 5 if limit <= 1000 else 10
    # Conservative allowance for other public endpoints, including depth.
    return 20 if path.endswith("/depth") else 5

async def paced_get(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """A public market-data GET that shares the process-wide pacing.

    Anything calling the exchange directly with its own client is invisible to
    this budget. A research download doing that alongside a running scanner is
    how an IP gets banned: neither knows what the other is spending, and the
    download never notices that the address is already blocked.
    """
    gate = LIMITS.setdefault(base_url, MarketLimit())
    await gate.acquire(base_url + path, request_weight(path, params))
    response = await client.get(base_url + path, params=params)
    gate.observe(response)
    return response


def blocked_for(base_url: str) -> float:
    """Seconds until this origin is worth calling again. 0 when it is now."""
    gate = LIMITS.get(base_url)
    return max(0.0, shared_deadline(base_url)-time.time(),
               max(gate.blocked_until, gate.soft_until)-time.monotonic() if gate else 0.0)


def banned_for(base_url: str) -> float:
    """Seconds of real ban left. A crowded shared IP does not count."""
    gate = LIMITS.get(base_url)
    return gate.banned_for(base_url) if gate else max(
        0.0, shared_deadline(base_url) - time.time())
