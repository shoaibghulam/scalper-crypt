import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from scalper.research.data.loader import aggregate, download, from_csv
from scalper.research.data.models import Bar, DataRequest, Dataset, FundingEvent
from scalper.research.data.store import load, save


def bars(count=60):
    return [
        Bar(
            timestamp=i * 60000,
            available_at=(i + 1) * 60000,
            open=100 + i,
            high=102 + i,
            low=99 + i,
            close=101 + i,
            volume=10,
            trade_count=3,
            taker_buy_volume=4,
            quote_volume=1000,
        )
        for i in range(count)
    ]


def raw(bar):
    return [
        bar.timestamp,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
        bar.available_at - 1,
        bar.quote_volume,
        bar.trade_count,
        bar.taker_buy_volume,
    ]


@pytest.mark.parametrize("timeframe,count", [("1m", 60), ("5m", 12), ("15m", 4), ("1h", 1)])
def test_exact_aggregation_and_availability(timeframe, count):
    result = aggregate(bars(), timeframe)
    assert len(result) == count
    first = result[0]
    size = 60 // count
    assert first.open == 100
    assert first.close == 100 + size
    assert first.high == 101 + size
    assert first.low == 99
    assert first.volume == 10 * size
    assert first.trade_count == 3 * size
    assert first.available_at == size * 60000


def test_future_rows_do_not_change_completed_higher_bars():
    original = bars()
    changed = [*original[:30], *[b.model_copy(update={"high": 999}) for b in original[30:]]]
    assert aggregate(original, "15m")[:2] == aggregate(changed, "15m")[:2]


@pytest.mark.parametrize(
    "values", [bars()[1:], bars()[:-1], bars()[:10] + bars()[11:], bars()[:10] + bars()[9:]]
)
def test_missing_duplicate_partial_bars_rejected(values):
    with pytest.raises(ValueError):
        aggregate(values, "1h")


@pytest.mark.parametrize(
    "patch",
    [{"close": float("nan")}, {"volume": -1}, {"high": 50}, {"low": 200}, {"taker_buy_volume": 11}],
)
def test_invalid_values_rejected(patch):
    with pytest.raises(ValidationError):
        Bar.model_validate({**bars(1)[0].model_dump(), **patch})


def test_request_alignment_and_symbol_normalization():
    assert DataRequest(symbol="btc/usdt", start=0, end=3600000).symbol == "BTCUSDT"
    for values in [{"start": 60000}, {"end": 1}, {"end": 0}, {"timeframe": "10m"}]:
        with pytest.raises(ValidationError):
            DataRequest.model_validate({"symbol": "BTCUSDT", "start": 0, "end": 3600000, **values})


@pytest.mark.asyncio
async def test_download_pagination_and_closed_range():
    market = AsyncMock()
    market.symbol_filters.return_value = {"BTCUSDT": object()}
    source = bars(1200)
    market._get.side_effect = [
        {"serverTime": 72000000},
        [raw(b) for b in source[:1000]],
        [raw(b) for b in source[1000:]],
    ]
    request = DataRequest(symbol="BTCUSDT", start=0, end=72000000)
    result = await download(request, market)
    assert len(result.bars) == 240
    assert result.funding_status == "not_requested"
    call = market._get.call_args_list[-1]
    assert call.args[1]["startTime"] == 60000000
    assert call.args[1]["endTime"] == 71999999


@pytest.mark.asyncio
async def test_incomplete_upstream_and_future_rejected():
    market = AsyncMock()
    market.symbol_filters.return_value = {"BTCUSDT": object()}
    request = DataRequest(symbol="BTCUSDT", start=0, end=300000)
    market._get.side_effect = [{"serverTime": 299999}]
    with pytest.raises(ValueError, match="unfinished"):
        await download(request, market)
    market._get.side_effect = [{"serverTime": 300000}, [raw(b) for b in bars(4)]]
    with pytest.raises(ValueError, match="Incomplete"):
        await download(request, market)


@pytest.mark.asyncio
async def test_funding_is_separate_and_no_fixed_schedule_assumed(monkeypatch):
    monkeypatch.setattr("scalper.research.data.loader.asyncio.sleep", AsyncMock())
    market = AsyncMock()
    market.symbol_filters.return_value = {"BTCUSDT": object()}
    market._get.side_effect = [
        {"serverTime": 300000},
        [raw(b) for b in bars(5)],
        [
            {"symbol": "BTCUSDT", "fundingTime": 1000, "fundingRate": "-.0001", "markPrice": "100"},
            {"symbol": "BTCUSDT", "fundingTime": 100001, "fundingRate": ".0002"},
        ],
    ]
    result = await download(
        DataRequest(symbol="BTCUSDT", start=0, end=300000, funding=True), market
    )
    assert result.funding_status == "queried"
    assert result.funding_events[0].rate == -0.0001
    assert result.funding_events[1].mark_price is None
    assert "funding_rate" not in result.bars[0].model_dump()


def test_csv_and_integrity_roundtrip(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(f"{i * 60000},100,102,99,101,0\n" for i in range(5))
    )
    request = DataRequest(symbol="BTCUSDT", start=0, end=300000)
    dataset = from_csv(path, request, as_of=300000)
    assert dataset.bars[0].trade_count is None
    output = save(dataset, tmp_path)
    assert load(output) == dataset
    assert save(dataset, tmp_path) == output
    value = json.loads(output.read_text())
    value["dataset"]["bars"][0]["close"] = 100
    output.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="integrity"):
        load(output)


def test_funding_coverage_contract():
    values = dict(
        request=DataRequest(symbol="BTCUSDT", start=0, end=300000),
        source="test",
        source_sha256="a" * 64,
        bars=tuple(aggregate(bars(5), "5m")),
    )
    with pytest.raises(ValueError, match="disagree"):
        Dataset(**values, funding_status="queried")
    with pytest.raises(ValueError, match="Unrequested"):
        Dataset(**values, funding_events=(FundingEvent(timestamp=0, rate=0.01),))
