"""Descriptive research diagnostics, not a statistical profitability certificate."""

from typing import Any


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [trade for row in rows for trade in row["trades"]]
    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [t["net"] for t in trades if t["net"] < 0]
    net = sum(row["net"] for row in rows)
    return {
        "trades": len(trades), "net": net,
        "win_rate": 100 * len(wins) / len(trades) if trades else None,
        "expectancy": net / len(trades) if trades else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "worst_period_drawdown_pct": max((r["closed_equity_drawdown_pct"] for r in rows), default=0),
        "positive_periods": sum(row["net"] > 0 for row in rows),
        "periods": len(rows),
    }


def diagnostics(results: list[dict[str, Any]], minimum_trades: int) -> dict[str, Any]:
    development = aggregate([r for r in results if r["period"] < 3 and not r["stress"]])
    stress = aggregate([r for r in results if r["period"] < 3 and r["stress"]])
    holdout = aggregate([r for r in results if r["period"] == 3 and not r["stress"]])
    holdout_stress = aggregate([r for r in results if r["period"] == 3 and r["stress"]])
    if development["trades"] < minimum_trades or stress["trades"] < minimum_trades:
        status = "insufficient_trades"
    elif development["net"] <= 0 or stress["net"] <= 0:
        status = "not_shortlisted"
    elif stress["positive_periods"] < 2:
        status = "inconsistent"
    else:
        status = "shortlist_candidate"
    return {
        "development": development, "development_stress": stress,
        "holdout": holdout, "holdout_stress": holdout_stress,
        "status": status, "minimum_trades": minimum_trades,
        "ranking_basis": "Periods 1–2 stressed net profit only; period 3 excluded from ranking.",
        "status_note": "Shortlist thresholds are screening heuristics, not confidence levels or live approval.",
    }
