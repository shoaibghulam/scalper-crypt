import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from scalper.config import TESTNET_REST
from scalper.exchange import order_flow as module
from scalper.exchange.order_flow import FlowEvidence, LocalBook, sample_order_flow
from scalper.strategies.order_flow import OrderFlowPullback
from tests.unit.test_ema_pullback import context


def snapshot():
    return {
        "lastUpdateId": 100,
        "bids": [[str(100 - i * 0.01), "10"] for i in range(25)],
        "asks": [[str(100.02 + i * 0.01), "1"] for i in range(25)],
    }


def depth(index=0, stamp=0, **kw):
    return dict(
        {
            "e": "depthUpdate",
            "s": "SOLUSDT",
            "E": stamp,
            "U": 100 + index,
            "u": 101 + index,
            "pu": 100 + index,
            "b": [],
            "a": [],
        },
        **kw,
    )


def evidence():
    now = time.monotonic()
    return FlowEvidence("SOLUSDT", 101.29, 101.31, (0.5,) * 5, 0.4, 5, 2000, 400, now, now, 10, 500)


def test_local_book_bridge_update_delete_duplicate_gap():
    book = LocalBook(snapshot())
    assert not book.apply(depth(U=98, u=99))
    assert book.apply(depth(b=[["100", "20"]]))
    assert book.bids[100] == 20
    assert not book.apply(depth())
    assert book.apply(depth(1, b=[["99.76", "0"], ["90", "0"]]))
    assert 99.76 not in book.bids
    with pytest.raises(ValueError, match="gap"):
        book.apply(depth(2, pu=1))
    with pytest.raises(ValueError, match="gap"):
        LocalBook(snapshot()).apply(depth(U=102, u=103))


@pytest.mark.parametrize("fault", ["cross", "nan", "negative", "empty", "sequence", "coverage"])
def test_bad_book_rejected(fault):
    book = LocalBook(snapshot())
    event = depth()
    if fault == "cross":
        event["b"] = [["101", "1"]]
    if fault == "nan":
        event["b"] = [["NaN", "1"]]
    if fault == "negative":
        event["a"] = [["100.02", "-1"]]
    if fault == "empty":
        event["a"] = [[str(100.02 + i * 0.01), "0"] for i in range(25)]
    if fault == "sequence":
        event.update(U=102, u=101)
    if fault == "coverage":
        event["a"] = [[str(100.02 + i * 0.01), "0"] for i in range(6)] + [["110", "1"]]
    with pytest.raises(ValueError):
        book.apply(event)


def test_flow_confirmation_and_short_mirror():
    evidence().validate("SOLUSDT", "LONG")
    replace(evidence(), imbalances=(-0.5,) * 5, trade_imbalance=-0.4).validate("SOLUSDT", "SHORT")


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "BTCUSDT"},
        {"received_at": 0},
        {"trade_received_at": 0},
        {"latency_ms": 1001},
        {"span_ms": 399},
        {"update_id": 0},
        {"trade_count": 2},
        {"trade_notional": 999},
        {"imbalances": (0.5, 0.5, 0.5, 0.5, -0.5)},
        {"trade_imbalance": -0.2},
        {"bid": 102},
        {"ask": 103},
        {"trade_imbalance": float("nan")},
        {"imbalances": (0.5,) * 4},
        {"imbalances": (2,) * 5},
        {"trade_imbalance": 2},
    ],
)
def test_invalid_flow_never_confirms(changes):
    with pytest.raises(ValueError):
        replace(evidence(), **changes).validate("SOLUSDT", "LONG")


def test_strategy_requires_real_flow_and_rechecks_quote():
    ctx = context()
    strategy = OrderFlowPullback()
    assert not strategy.assess(ctx).ready
    ctx = replace(ctx, order_flow=evidence())
    assert strategy.assess(ctx).ready
    with pytest.raises(ValueError, match="diverged"):
        strategy.levels(ctx, Decimal("101.5"), "LONG")
    assert not strategy.assess(
        replace(ctx, order_flow=replace(evidence(), trade_imbalance=-0.5))
    ).ready


class Socket:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        if self.events:
            return json.dumps(self.events.pop(0))
        await asyncio.Future()


class Market:
    base_url = TESTNET_REST

    async def _get(self, path, params):
        await asyncio.sleep(0)
        assert params["limit"] == 1000
        return snapshot()


def sockets(monkeypatch, fault=None):
    now = int(time.time() * 1000)
    stamps = [now - 600 + i * 100 for i in range(7)]
    ds = [depth(i, t) for i, t in enumerate(stamps)]
    ts = [
        {"e": "aggTrade", "s": "SOLUSDT", "E": t, "a": i, "p": "100", "q": "10", "m": False}
        for i, t in enumerate(stamps)
    ]
    if fault == "depth_gap":
        ds[3]["pu"] = 999
    if fault == "trade_gap":
        ts[3]["a"] = 999
    if fault == "stale":
        for event in ds + ts:
            event["E"] -= 2000
    if fault == "symbol":
        ts[0]["s"] = "WRONG"
    if fault == "side":
        ts[0]["m"] = "false"
    calls = []

    def connect(url, **kwargs):
        calls.append(url)
        return Socket(ds if "@depth" in url else ts)

    monkeypatch.setattr(module, "connect", connect)
    return calls


async def test_collector_uses_routed_streams_snapshot_and_common_window(monkeypatch):
    calls = sockets(monkeypatch)
    result = await sample_order_flow(Market(), "SOLUSDT")
    result.validate("SOLUSDT", "LONG")
    assert result.update_id >= 105
    assert "/public/ws/" in calls[0] and "/market/ws/" in calls[1]


@pytest.mark.parametrize("fault", ["depth_gap", "trade_gap", "stale", "symbol", "side"])
async def test_collector_discards_invalid_sample(monkeypatch, fault):
    sockets(monkeypatch, fault)
    with pytest.raises((ValueError, TimeoutError)):
        await sample_order_flow(Market(), "SOLUSDT")


async def test_collector_rejects_bad_symbol_and_unknown_environment():
    with pytest.raises(ValueError):
        await sample_order_flow(Market(), "../BTC")
    market = Market()
    market.base_url = "https://unknown.invalid"
    with pytest.raises(ValueError):
        await sample_order_flow(market, "SOLUSDT")
