import json
import random

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scalper.api import quant_lab
from scalper.research.backtest import Backtester
from scalper.research.experiments import ExperimentRequest, atomic_json, run_dataset
from scalper.research.features import Features
from scalper.research.integration import validated_export
from scalper.research.ml import fit_filter, predict
from scalper.research.schema import SearchConfig
from scalper.research.search import random_strategy
from scalper.research.validation import split_periods

from .test_discovery_engine import config, dataset, strategy


def test_complete_pipeline_and_holdout_cannot_change_selection(tmp_path):
    cfg = config(min_trades=2, finalists=2, monte_carlo_samples=20).model_copy(
        update={"optimization_trials": 2}
    )
    original = dataset(1200)
    a = tmp_path / "a"
    a.mkdir()
    first = run_dataset(original, cfg, a, lambda **_: None, lambda: None)
    altered = dataset(
        1200,
        prices=np.r_[
            100 + np.arange(960) * 0.01 + np.sin(np.arange(960) / 9), np.linspace(200, 400, 240)
        ],
    )
    b = tmp_path / "b"
    b.mkdir()
    second = run_dataset(altered, cfg, b, lambda **_: None, lambda: None)
    assert first["portfolio"] == second["portfolio"]
    assert first["selection_audit"] == second["selection_audit"]
    assert [s["strategy_id"] for s in first["strategies"]] == [
        s["strategy_id"] for s in second["strategies"]
    ]
    assert first["total_tested"] >= 8
    rows = [json.loads(line) for line in (a / "BTCUSDT-candidates.jsonl").read_text().splitlines()]
    assert len(rows) == first["total_tested"]
    assert all("training" in row for row in rows)
    assert all("test" in s and "robustness" in s and "reasons" in s for s in first["strategies"])
    json.dumps(first, allow_nan=False)


def test_seeded_generator_respects_limits():
    cfg = config().model_copy(update={"max_complexity": 3})
    a, b = random.Random(42), random.Random(42)
    for _ in range(100):
        left, right = random_strategy(a, "5m", cfg), random_strategy(b, "5m", cfg)
        assert left == right
        assert left.complexity <= 3
        assert left.risk.leverage <= cfg.safety.max_leverage
        assert left.risk.position_size <= cfg.safety.max_position_size


def test_splits_have_embargo_and_no_overlap():
    periods = split_periods(1200, config())
    assert periods["train"][1] + 12 == periods["validation"][0]
    assert periods["validation"][1] + 12 == periods["test"][0]
    with pytest.raises(ValueError, match="Not enough"):
        split_periods(100, config())


def test_ml_fit_uses_completed_training_trades_only():
    f = Features(dataset(1200))
    trades = np.zeros((80, 9))
    trades[:, 0] = np.arange(60, 140)
    trades[:, 1] = trades[:, 0] + 1
    trades[:, 5] = np.tile([-1, 1], 40)
    model, note = fit_filter(f, {"trades": trades, "last": 200}, 42)
    assert model["trained_through"] == 200 and model["training_trades"] == 80
    probabilities = predict(model, f.ml_matrix())
    assert np.all((probabilities >= 0) & (probabilities <= 1))
    assert not probabilities[:20].any()
    assert "training" in note
    json.dumps(model, allow_nan=False)


def test_paper_prefix_and_open_fee_accounting():
    f = Features(dataset(20, prices=np.full(20, 100.0)))
    cfg = SearchConfig(population=8, generations=1, optimization_trials=0)
    result = Backtester(f).run(strategy(), cfg, 1, 10, finalize=False)
    extended = Backtester(f).run(strategy(), cfg, 1, 20, finalize=False)
    np.testing.assert_allclose(result["equity"], extended["equity"][:9])
    assert result["open_position"] is not None
    assert result["metrics"]["fees_paid"] > 0
    assert result["metrics"]["trades"] == 0


def test_api_rejects_traversal_validates_and_cancels(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_lab, "ROOT", tmp_path)
    launched = []
    monkeypatch.setattr(quant_lab, "launch", lambda *args: launched.append(args))
    app = FastAPI()
    app.include_router(quant_lab.router)
    with TestClient(app) as client:
        response = client.post(
            "/api/quant-lab/jobs", json={"symbols": ["BTCUSDT"], "search": {"population": 7}}
        )
        assert response.status_code == 422
        response = client.post("/api/quant-lab/jobs", json={"symbols": ["BTCUSDT"]})
        assert response.status_code == 200
        job = response.json()["id"]
        assert launched
        assert client.post("/api/quant-lab/jobs", json={"symbols": ["ETHUSDT"]}).status_code == 409
        assert client.post(f"/api/quant-lab/jobs/{job}/cancel").status_code == 200
        assert (tmp_path / job / "cancel").exists()
        assert client.get("/api/quant-lab/jobs/not-a-job").status_code == 404
        assert client.get(f"/api/quant-lab/jobs/{job}/download").status_code == 200
        assert client.get("/api/quant-lab/jobs").json()[0]["id"] == job


def test_rejected_or_live_artifact_cannot_be_loaded():
    s = strategy()
    artifact = {
        "schema_version": 1,
        "live_enabled": False,
        "strategy_definition": s.model_dump(mode="json"),
        "strategy_id": s.strategy_id,
        "status": "rejected",
        "search_config": config().model_dump(mode="json"),
    }
    with pytest.raises(ValueError, match="Rejected"):
        validated_export(artifact)
    artifact["status"] = "research_passed"
    assert validated_export(artifact) == s
    artifact["live_enabled"] = True
    with pytest.raises(ValueError, match="live"):
        validated_export(artifact)


def test_symbol_request_rejects_duplicates_and_non_usdt():
    assert ExperimentRequest(symbols=("BTC/USDT",)).symbols == ("BTCUSDT",)
    with pytest.raises(ValueError):
        ExperimentRequest(symbols=("BTCUSDT", "BTC/USDT"))
    with pytest.raises(ValueError):
        ExperimentRequest(symbols=("BTCUSD",))


@pytest.mark.asyncio
async def test_forward_paper_session_and_operator_stop(tmp_path, monkeypatch):
    from scalper.research import paper
    from scalper.research.data.models import DataRequest, Dataset
    from scalper.research.data.store import save
    from scalper.research.experiments import read_json

    initial = dataset(600)
    data_dir = tmp_path / "data"
    stored = save(initial, data_dir)
    monkeypatch.setattr(paper, "DATA", data_dir)
    path = tmp_path / "paper"
    path.mkdir()
    s = strategy()
    artifact = {
        "schema_version": 1,
        "live_enabled": False,
        "strategy_id": s.strategy_id,
        "strategy_definition": s.model_dump(mode="json"),
        "status": "paper_approved",
        "search_config": config().model_dump(mode="json"),
        "symbol": "BTCUSDT",
        "model": None,
    }
    atomic_json(path / "strategy.json", artifact)
    atomic_json(path / "state.json", {"dataset_file": stored.name, "start": 601 * 300000})
    extended = dataset(605)
    appended = Dataset(
        request=DataRequest(symbol="BTCUSDT", start=600 * 300000, end=605 * 300000, funding=True),
        source="test",
        source_sha256="1" * 64,
        bars=extended.bars[600:],
        funding_status="queried",
    )
    appended_path = save(appended, data_dir)

    async def get_data(request, progress):
        assert request.start == 600 * 300000
        return appended, appended_path

    async def sleep(_):
        (path / "cancel").touch()

    monkeypatch.setattr(paper, "get_dataset", get_data)
    monkeypatch.setattr(paper.time, "time", lambda: 605 * 300)
    monkeypatch.setattr(paper.asyncio, "sleep", sleep)
    await paper.execute(path)
    result = read_json(path / "state.json")
    assert result["status"] == "stopped"
    assert result["result"]["first"] == 601
    assert result["result"]["open_position"] is None
    assert result["dataset_segments"] == [stored.name, appended_path.name]
    assert all(t[0] >= 601 for t in result["result"]["trades"])


def test_worker_lock_excludes_parallel_processes_and_releases(tmp_path):
    from scalper.research.locking import acquire, release

    handle = acquire(tmp_path / "worker.lock")
    try:
        with pytest.raises(ValueError, match="already running"):
            acquire(tmp_path / "worker.lock")
    finally:
        release(handle)
    second = acquire(tmp_path / "worker.lock")
    release(second)


def test_dead_worker_becomes_interrupted(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_lab, "ROOT", tmp_path)
    monkeypatch.setattr(quant_lab, "process_alive", lambda _: False)
    job = "f" * 32
    atomic_json(
        tmp_path / job / "state.json", {"id": job, "status": "running", "pid": 123, "created_at": 0}
    )
    assert quant_lab.get(job)["status"] == "interrupted"
