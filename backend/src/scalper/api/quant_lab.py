"""Disk-backed research/paper workers. No execution-engine commands."""

import os
import re
import subprocess
import sys
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from scalper.research.data.models import INTERVALS
from scalper.research.experiments import (
    ROOT,
    ExperimentRequest,
    atomic_json,
    read_json,
    read_snapshot,
)
from scalper.research.locking import process_alive
from scalper.symbols import is_symbol

router = APIRouter(prefix="/api/quant-lab", tags=["Quant research"])
_workers = []


def directory(job_id):
    if not re.fullmatch("[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Unknown experiment")
    path = ROOT / job_id
    if not (path / "state.json").exists():
        raise HTTPException(404, "Unknown experiment")
    return path


def launch(module, path):
    _workers[:] = [p for p in _workers if p.poll() is None]
    with (path / "worker.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", module, "--job", str(path)],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    _workers.append(process)


@router.get("/jobs")
def history():
    rows = []
    for path in sorted(ROOT.glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
        :50
    ]:
        state = get(path.parent.name)
        rows.append(
            {
                k: state.get(k)
                for k in ["id", "status", "created_at", "phase", "request", "kind", "heartbeat"]
            }
        )
    return rows


@router.post("/jobs")
def start(request: ExperimentRequest):
    if any(
        row["kind"] == "research"
        and row["status"] in {"queued", "running"}
        and time.time() - (row["heartbeat"] or row["created_at"]) < 180
        for row in history()
    ):
        raise HTTPException(409, "A research experiment is already running; cancel it or wait")
    job_id = uuid.uuid4().hex
    path = ROOT / job_id
    state = {
        "id": job_id,
        "kind": "research",
        "status": "queued",
        "created_at": time.time(),
        "heartbeat": time.time(),
        "phase": "Queued",
        "request": request.model_dump(mode="json"),
        "results": [],
        "errors": [],
    }
    atomic_json(path / "state.json", state)
    try:
        launch("scalper.research.experiments", path)
    except OSError as exc:
        state.update(status="failed", phase="Could not launch research worker")
        atomic_json(path / "state.json", state)
        raise HTTPException(503, state["phase"]) from exc
    return state


@router.get("/jobs/{job_id}")
def get(job_id: str):
    state = read_json(directory(job_id) / "state.json")
    if state["status"] in {"queued", "running"} and (
        (state.get("pid") and not process_alive(state["pid"]))
        or (not state.get("pid") and time.time() - state["created_at"] > 60)
    ):
        state.update(status="interrupted", phase="Worker stopped; completed results retained")
        atomic_json(directory(job_id) / "state.json", state)
    if (
        state["status"] in {"queued", "running"}
        and time.time() - state.get("heartbeat", state["created_at"]) > 180
    ):
        state["worker_warning"] = (
            "No recent progress. Worker may be busy or interrupted; inspect worker.log."
        )
    return state


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str):
    (directory(job_id) / "cancel").touch()
    return {"message": "Stop requested; completed results are retained"}


@router.get("/jobs/{job_id}/download")
def download_report(job_id: str):
    # Read then close before streaming to the browser: FileResponse can keep the
    # mutable state file open for the entire download and block Windows replace.
    content = read_snapshot(directory(job_id) / "state.json")
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="quant-{job_id}.json"'},
    )


@router.get("/jobs/{job_id}/candidates/{symbol}")
def candidates(job_id: str, symbol: str):
    if not is_symbol(symbol):
        raise HTTPException(404, "Unknown coin")
    path = directory(job_id) / f"{symbol}-candidates.jsonl"
    if not path.exists():
        raise HTTPException(404, "Candidate archive not available yet")
    return FileResponse(path, media_type="application/x-ndjson", filename=path.name)


def find_strategy(job_id, symbol, strategy_id):
    from scalper.research.integration import export_strategy

    state = get(job_id)
    for coin in state.get("results", []):
        if coin["symbol"] == symbol:
            for result in coin["strategies"]:
                if result["strategy_id"] == strategy_id:
                    return export_strategy(state, coin, result), coin
    raise HTTPException(404, "Unknown strategy result")


@router.get("/jobs/{job_id}/export/{symbol}/{strategy_id}")
def export(job_id: str, symbol: str, strategy_id: str):
    return find_strategy(job_id, symbol, strategy_id)[0]


@router.post("/jobs/{job_id}/paper/{symbol}/{strategy_id}")
def approve_paper(job_id: str, symbol: str, strategy_id: str):
    from scalper.research.integration import validated_export

    artifact, coin = find_strategy(job_id, symbol, strategy_id)
    try:
        validated_export(artifact)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if any(row["kind"] == "paper" and row["status"] in {"queued", "running"} for row in history()):
        raise HTTPException(409, "Stop the existing paper session before starting another")
    paper_id = uuid.uuid4().hex
    path = ROOT / paper_id
    interval = INTERVALS[coin["timeframe"]]
    artifact["status"] = "paper_approved"
    artifact["approved_at"] = time.time()
    atomic_json(path / "strategy.json", artifact)
    state = {
        "id": paper_id,
        "kind": "paper",
        "created_at": time.time(),
        "heartbeat": time.time(),
        "status": "queued",
        "phase": "Paper approved; waiting for future candles",
        "start": (int(time.time() * 1000) // interval + 1) * interval,
        "request": {"symbols": [symbol], "timeframe": coin["timeframe"]},
        "dataset_file": coin["dataset_file"],
        "parent_experiment": job_id,
    }
    atomic_json(path / "state.json", state)
    try:
        launch("scalper.research.paper", path)
    except OSError as exc:
        state.update(status="failed", phase="Paper worker could not start")
        atomic_json(path / "state.json", state)
        raise HTTPException(503, state["phase"]) from exc
    return state
