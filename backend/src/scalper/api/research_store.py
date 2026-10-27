"""Append-only SQLite archive, separate from trading state and credentials."""

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from scalper.symbols import SYMBOL_PATTERN

RUNS_PATH = Path(__file__).resolve().parents[4] / "var" / "research.sqlite"


def connect() -> sqlite3.Connection:
    RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(RUNS_PATH, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created REAL, "
               "strategy TEXT, dataset_label TEXT, report TEXT NOT NULL)")
    # Favourites lived in localStorage, which meant they belonged to one
    # browser on one machine: clearing site data or opening the dashboard
    # elsewhere lost the shortlist a backtest run was chosen from.
    db.execute("CREATE TABLE IF NOT EXISTS favorites (symbol TEXT NOT NULL, "
               "strategy TEXT NOT NULL, created REAL NOT NULL, "
               "PRIMARY KEY (symbol, strategy))")
    return db


SYMBOL = re.compile(SYMBOL_PATTERN)


def _pair(symbol: str, strategy: str) -> tuple[str, str]:
    """Validate a favourite before it reaches the database."""
    symbol = str(symbol).strip().upper()
    strategy = str(strategy).strip()
    if not SYMBOL.fullmatch(symbol):
        raise HTTPException(422, "Choose a valid USDT perpetual symbol")
    if not 1 <= len(strategy) <= 80 or not strategy.replace("-", "").replace(" ", "").isalnum():
        raise HTTPException(422, "Unrecognised strategy identifier")
    return symbol, strategy


def list_favorites() -> list[dict[str, Any]]:
    db = connect()
    try:
        rows = db.execute(
            "SELECT symbol, strategy, created FROM favorites ORDER BY created"
        ).fetchall()
    finally:
        db.close()
    return [{"symbol": s, "strategy": st, "created": c} for s, st, c in rows]


def add_favorite(symbol: str, strategy: str) -> list[dict[str, Any]]:
    symbol, strategy = _pair(symbol, strategy)
    db = connect()
    try:
        with db:
            db.execute(
                "INSERT OR IGNORE INTO favorites VALUES (?, ?, ?)",
                (symbol, strategy, time.time()),
            )
    finally:
        db.close()
    return list_favorites()


def remove_favorite(symbol: str, strategy: str) -> list[dict[str, Any]]:
    symbol, strategy = _pair(symbol, strategy)
    db = connect()
    try:
        with db:
            db.execute(
                "DELETE FROM favorites WHERE symbol = ? AND strategy = ?",
                (symbol, strategy),
            )
    finally:
        db.close()
    return list_favorites()


def save_run(report: dict[str, Any]) -> dict[str, Any]:
    saved = {**report, "run_id": uuid.uuid4().hex, "saved_at": time.time()}
    config = saved["config"]
    db = connect()
    try:
        with db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", (
                saved["run_id"], saved["saved_at"], config["strategy"],
                config.get("dataset_label", "CSV"), json.dumps(saved, allow_nan=False),
            ))
    finally:
        db.close()
    return saved


def list_runs() -> list[dict[str, Any]]:
    db = connect()
    try:
        return [dict(zip(("id", "saved_at", "strategy", "dataset_label"), row, strict=True))
                for row in db.execute("SELECT id, created, strategy, dataset_label FROM runs "
                                      "ORDER BY created DESC LIMIT 50")]
    finally:
        db.close()


def tested_strategies() -> list[str]:
    """Include the entire archive, not just the 50 most recent reports."""
    db = connect()
    try:
        return [row[0] for row in db.execute(
            "SELECT DISTINCT strategy FROM runs WHERE strategy IS NOT NULL ORDER BY strategy"
        )]
    finally:
        db.close()


def load_run(run_id: str) -> dict[str, Any]:
    db = connect()
    try:
        row = db.execute("SELECT report FROM runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        raise HTTPException(404, "Research run not found")
    report: dict[str, Any] = json.loads(row[0])
    return report
