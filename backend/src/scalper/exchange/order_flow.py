"""Bounded event sampling for research confirmation, never a REST depth proxy.

Each sample opens fresh routed streams and reconstructs depth from a snapshot.
A sequence gap invalidates the entire sample; the next request starts from scratch.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import deque
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import connect

from scalper.config import LIVE_REST, LIVE_WS_HOST, TESTNET_REST, TESTNET_WS_HOST

if TYPE_CHECKING:
    from scalper.exchange.market_data import MarketDataClient


def number(value: Any, *, positive: bool = False) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValueError("invalid order-flow number")
    return result


@dataclass(frozen=True)
class FlowEvidence:
    symbol: str
    bid: float
    ask: float
    imbalances: tuple[float, ...]
    trade_imbalance: float
    trade_count: int
    trade_notional: float
    span_ms: int
    received_at: float
    trade_received_at: float
    latency_ms: int
    update_id: int

    def validate(self, symbol: str, direction: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        values = (
            self.bid,
            self.ask,
            self.trade_imbalance,
            self.trade_notional,
            self.received_at,
            self.trade_received_at,
            *self.imbalances,
        )
        if not all(math.isfinite(v) for v in values):
            raise ValueError("non-finite flow evidence")
        if self.symbol != symbol or direction not in ("LONG", "SHORT"):
            raise ValueError("wrong flow symbol or direction")
        if not 0 <= now - self.received_at <= 0.75 or not 0 <= now - self.trade_received_at <= 0.75:
            raise ValueError("stale order-flow evidence")
        if self.bid <= 0 or self.ask <= self.bid or (self.ask - self.bid) / self.bid > 0.0005:
            raise ValueError("order-flow spread failed")
        if not 0 <= self.latency_ms <= 1000 or self.span_ms < 400 or self.update_id <= 0:
            raise ValueError("insufficient flow timing evidence")
        if len(self.imbalances) < 5 or self.trade_count < 3 or self.trade_notional < 1000:
            raise ValueError("insufficient order-flow events")
        sign = 1 if direction == "LONG" else -1
        if any(not -1 <= v <= 1 or v * sign < 0.2 for v in self.imbalances[-5:]):
            raise ValueError("depth imbalance is not persistent")
        if not -1 <= self.trade_imbalance <= 1 or self.trade_imbalance * sign < 0.2:
            raise ValueError("aggressive trade flow does not confirm")


class LocalBook:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.last = int(snapshot["lastUpdateId"])
        self.bridged = False
        self.bids = self._levels(snapshot["bids"])
        self.asks = self._levels(snapshot["asks"])
        if self.last <= 0 or len(self.bids) < 20 or len(self.asks) < 20:
            raise ValueError("insufficient initial depth")
        self.floor, self.ceiling = min(self.bids), max(self.asks)
        self.top()

    @staticmethod
    def _levels(rows: Any) -> dict[float, float]:
        result = {}
        for row in rows:
            price, qty = number(row[0], positive=True), number(row[1])
            if qty:
                result[price] = qty
        return result

    def apply(self, event: dict[str, Any]) -> bool:
        first, final = int(event["U"]), int(event["u"])
        if first > final or first < 0:
            raise ValueError("invalid depth sequence")
        if final < self.last or (self.bridged and final == self.last):
            return False
        if (not self.bridged and not first <= self.last <= final) or (
            self.bridged and int(event["pu"]) != self.last
        ):
            raise ValueError("depth sequence gap; resnapshot required")
        for book, rows in ((self.bids, event["b"]), (self.asks, event["a"])):
            for row in rows:
                price, qty = number(row[0], positive=True), number(row[1])
                if qty:
                    book[price] = qty
                else:
                    book.pop(price, None)
        self.last, self.bridged = final, True
        if len(self.bids) + len(self.asks) > 10000:
            raise ValueError("local depth capacity exceeded")
        self.top()
        return True

    def top(self) -> tuple[float, float, float]:
        bids = sorted(self.bids, reverse=True)[:20]
        asks = sorted(self.asks)[:20]
        if len(bids) < 20 or len(asks) < 20 or bids[0] >= asks[0]:
            raise ValueError("empty or crossed local book")
        if bids[-1] < self.floor or asks[-1] > self.ceiling:
            raise ValueError("top depth left snapshot coverage")
        buy = sum(p * self.bids[p] for p in bids)
        sell = sum(p * self.asks[p] for p in asks)
        return bids[0], asks[0], (buy - sell) / (buy + sell)


async def sample_order_flow(market: MarketDataClient, symbol: str) -> FlowEvidence:
    if not re.fullmatch(r"[A-Z0-9]{3,30}", symbol):
        raise ValueError("invalid flow symbol")
    hosts = {LIVE_REST: LIVE_WS_HOST, TESTNET_REST: TESTNET_WS_HOST}
    if market.base_url not in hosts:
        raise ValueError("unsupported order-flow environment")
    host = hosts[market.base_url]
    queue: asyncio.Queue[tuple[dict[str, Any], float, int]] = asyncio.Queue(maxsize=512)

    async def pump(ws: Any) -> None:
        async for raw in ws:
            event = json.loads(raw)
            queue.put_nowait((event, time.monotonic(), int(time.time() * 1000)))
        raise ValueError("order-flow stream disconnected")

    async with (
        asyncio.timeout(8),
        connect(
            f"{host}/public/ws/{symbol.lower()}@depth@100ms",
            max_queue=256,
            max_size=1048576,
            open_timeout=3,
            close_timeout=0.1,
        ) as depth,
        connect(
            f"{host}/market/ws/{symbol.lower()}@aggTrade",
            max_queue=256,
            max_size=1048576,
            open_timeout=3,
            close_timeout=0.1,
        ) as trades,
    ):
        tasks = [asyncio.create_task(pump(depth)), asyncio.create_task(pump(trades))]
        try:
            book = LocalBook(await market._get("/fapi/v1/depth", {"symbol": symbol, "limit": 1000}))
            samples: deque[tuple[int, float, float, int]] = deque(maxlen=100)
            flow: deque[tuple[int, float, float, float]] = deque(maxlen=4096)
            last_trade = -1
            last_event = {"depthUpdate": -1, "aggTrade": -1}
            while True:
                for task in tasks:
                    if task.done():
                        task.result()
                event, received, wall = await asyncio.wait_for(queue.get(), timeout=1)
                kind = event.get("e")
                if kind not in last_event or event.get("s") != symbol:
                    raise ValueError("unexpected order-flow event")
                stamp = int(event["E"])
                if stamp < last_event[kind] or wall - stamp < 0:
                    raise ValueError(
                        f"delayed or unordered stream event: {kind}, age={wall - stamp}ms"
                    )
                last_event[kind] = stamp
                if kind == "depthUpdate":
                    if not book.apply(event):
                        continue
                    # Replay startup backlog for sequence continuity, never for a signal.
                    if wall - stamp > 1000:
                        samples.clear()
                        continue
                    bid, ask, imbalance = book.top()
                    samples.append((stamp, imbalance, received, wall - stamp))
                else:
                    trade_id = int(event["a"])
                    if trade_id <= last_trade:
                        continue
                    if last_trade >= 0 and trade_id != last_trade + 1:
                        raise ValueError("aggregate trade sequence gap")
                    last_trade = trade_id
                    if not isinstance(event["m"], bool):
                        raise ValueError("invalid trade side")
                    notional = number(event["p"], positive=True) * number(event["q"], positive=True)
                    if wall - stamp > 1000:
                        flow.clear()
                        continue
                    flow.append((stamp, -notional if event["m"] else notional, notional, received))
                if len(samples) < 5 or len(flow) < 3:
                    continue
                # Require a common observation interval, not unrelated snapshots.
                end = samples[-1][0]
                start = max(end - 1500, samples[0][0], flow[0][0])
                ds = [s for s in samples if start <= s[0] <= end]
                ts = [t for t in flow if start <= t[0] <= end]
                if len(ds) < 5 or len(ts) < 3 or ds[-1][0] - ds[-5][0] < 400:
                    continue
                # No slow/stalled feed may accumulate a misleading persistence window.
                if any(b[0] - a[0] > 500 for a, b in pairwise(ds[-5:])):
                    raise ValueError("depth event gap")
                total = sum(t[2] for t in ts)
                bid, ask, _ = book.top()
                return FlowEvidence(
                    symbol,
                    bid,
                    ask,
                    tuple(s[1] for s in ds[-5:]),
                    sum(t[1] for t in ts) / total,
                    len(ts),
                    total,
                    ds[-1][0] - ds[-5][0],
                    samples[-1][2],
                    ts[-1][3],
                    max(s[3] for s in ds[-5:]),
                    book.last,
                )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
