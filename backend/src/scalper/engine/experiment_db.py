"""Transactional SQLite research mirror of the complete persisted history."""

import json
import sqlite3
from pathlib import Path
from typing import Any


def save_snapshot(path: Path, trades: list[dict[str, Any]], settings: dict[str, Any]) -> None:
    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS closed_trades (
            trade_index INTEGER PRIMARY KEY, strategy TEXT NOT NULL,
            symbol TEXT NOT NULL, closed_at REAL NOT NULL, verified INTEGER NOT NULL,
            net TEXT NOT NULL, fees TEXT NOT NULL, payload TEXT NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS by_strategy ON closed_trades(strategy)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS experiment_settings (id INTEGER PRIMARY KEY, payload TEXT)"
        )
        connection.execute("DELETE FROM closed_trades")
        connection.executemany(
            "INSERT INTO closed_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    i,
                    t["strategy"],
                    t["symbol"],
                    t["closed_at"],
                    int(t.get("verified", False)),
                    str(t["realised"]),
                    str(t["fees"]),
                    json.dumps(t, sort_keys=True),
                )
                for i, t in enumerate(trades)
            ],
        )
        connection.execute(
            "INSERT OR REPLACE INTO experiment_settings VALUES (1, ?)",
            (json.dumps(settings, sort_keys=True),),
        )
