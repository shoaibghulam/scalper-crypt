"""Selection uses development periods only; holdout is a final rejection gate."""

import hashlib
import math
from itertools import pairwise

import numpy as np

from scalper.research.backtest import score
from scalper.research.ml import fit_filter, predict
from scalper.research.schema import StrategyDefinition
from scalper.research.search import perturb


def summarize(result):
    indices = np.unique(
        np.linspace(0, len(result["equity"]) - 1, min(1000, len(result["equity"])), dtype=int)
    )
    return {
        "metrics": result["metrics"],
        "first": result["first"],
        "last": result["last"],
        "equity": [[int(result["first"] + i), float(result["equity"][i])] for i in indices],
        "trades": result["trades"].tolist(),
        "open_position": result.get("open_position"),
    }


def split_periods(count, config):
    train_end, validation_end = int(count * 0.6), int(count * 0.8)
    validation_start, test_start = (
        train_end + config.embargo_bars,
        validation_end + config.embargo_bars,
    )
    if (
        train_end < 240
        or validation_end - validation_start < config.walk_forward_folds * 30
        or count - test_start < 60
    ):
        raise ValueError(
            "Not enough history for warm-up, embargoes and walk-forward folds; load more days"
        )
    return {
        "train": [1, train_end],
        "validation": [validation_start, validation_end],
        "test": [test_start, count],
    }


def walk_forward(backtester, candidates, config, periods, check):
    start, end = periods["validation"]
    bounds = np.linspace(start, end, config.walk_forward_folds + 1, dtype=int)
    folds = []
    for left, right in pairwise(bounds):
        check()
        training_end = int(left) - config.embargo_bars
        ranking = []
        for strategy in candidates:
            check()
            result = backtester.run(strategy, config, 1, training_end)
            ranking.append((score(result, strategy, config), strategy))
        selected = max(ranking, key=lambda r: r[0])[1]
        result = backtester.run(selected, config, int(left), int(right))
        folds.append(
            {
                "train": [1, training_end],
                "test": [int(left), int(right)],
                "selected_strategy_id": selected.strategy_id,
                "metrics": result["metrics"],
            }
        )
    return folds


def robustness(backtester, strategy, config, first, last, base, mask, check):
    perturbations, stresses = [], []
    for delta in [-0.20, -0.10, -0.05, 0.05, 0.10, 0.20]:
        check()
        changed = perturb(strategy, delta, config)
        result = backtester.run(changed, config, first, last, mask)
        perturbations.append({"change": delta, "metrics": result["metrics"]})
    for fee_factor, slippage_factor in [(1, 1), (2, 1), (3, 1), (1, 2), (1, 3), (3, 3)]:
        check()
        costs = config.costs.model_copy(
            update={
                "fee": config.costs.fee * fee_factor,
                "slippage": config.costs.slippage * slippage_factor,
                "spread": config.costs.spread * slippage_factor,
            }
        )
        result = backtester.run(
            strategy, config.model_copy(update={"costs": costs}), first, last, mask
        )
        stresses.append(
            {
                "fee_multiplier": fee_factor,
                "friction_multiplier": slippage_factor,
                "metrics": result["metrics"],
            }
        )
    pnl = base["trades"][:, 5]
    rng = np.random.default_rng(config.seed)
    shuffled_drawdowns, bootstrap_profits = [], []
    for _ in range(config.monte_carlo_samples):
        if not len(pnl):
            break
        curve = config.capital + np.r_[0, np.cumsum(rng.permutation(pnl))]
        peak = np.maximum.accumulate(curve)
        shuffled_drawdowns.append(float(np.max(1 - curve / peak)))
        bootstrap_profits.append(float(rng.choice(pnl, len(pnl), replace=True).sum()))
    mc = {
        "samples": config.monte_carlo_samples if len(pnl) else 0,
        "reshuffle_drawdown_p95": float(np.quantile(shuffled_drawdowns, 0.95))
        if len(pnl)
        else None,
        "bootstrap_profit_p05": float(np.quantile(bootstrap_profits, 0.05)) if len(pnl) else None,
        "note": "Trade-order reshuffling and IID bootstrap are diagnostics, not guarantees; "
        "serial dependence is not preserved",
    }
    stable_fraction = sum(
        r["metrics"]["net_profit"] > 0 and not r["metrics"]["halted"] for r in perturbations
    ) / len(perturbations)
    cost_pass = all(r["metrics"]["net_profit"] > 0 and not r["metrics"]["halted"] for r in stresses)
    mc_pass = bool(
        mc["reshuffle_drawdown_p95"] is not None
        and mc["reshuffle_drawdown_p95"] <= config.safety.max_drawdown
        and mc["bootstrap_profit_p05"] > 0
    )
    return {
        "parameter_variations": perturbations,
        "cost_stress": stresses,
        "monte_carlo": mc,
        "stability_fraction": stable_fraction,
        "score": (stable_fraction + cost_pass + mc_pass) / 3,
        "passed": stable_fraction >= 2 / 3 and cost_pass and mc_pass,
    }


def regimes(backtester, result, train_end):
    f = backtester.features
    close, ema = f.ohlcv[:, 3], f.indicator("ema", 50)
    vol, adx = f.indicator("volatility", 20), f.indicator("adx")
    train_vol = vol[:train_end]
    finite = train_vol[np.isfinite(train_vol)]
    lower, upper = np.quantile(finite, [0.25, 0.75]) if len(finite) else (0.0, 0.0)
    groups = {
        "bullish": [],
        "bearish": [],
        "sideways": [],
        "high_volatility": [],
        "low_volatility": [],
    }
    for trade in result["trades"]:
        i = int(trade[0]) - 1
        name = "sideways" if adx[i] < 20 else "bullish" if close[i] > ema[i] else "bearish"
        groups[name].append(float(trade[5]))
        if vol[i] >= upper:
            groups["high_volatility"].append(float(trade[5]))
        elif vol[i] <= lower:
            groups["low_volatility"].append(float(trade[5]))
    return {
        name: {"trades": len(values), "net_profit": sum(values)} for name, values in groups.items()
    }


def distinct_finalists(candidates, count):
    """Deduplicate observed validation behavior before accessing the holdout.

    Matching here does not imply globally equivalent rules. Keep every rule in
    the selection audit, but reserve finalist slots for distinct validation paths.
    """
    selected, duplicates, seen = [], [], {}
    for row in sorted(candidates, key=lambda c: c["score"], reverse=True):
        result = row["validation"]
        fingerprint = hashlib.sha256()
        for name in ("equity", "trades"):
            values = np.ascontiguousarray(result[name], dtype=np.float64)
            fingerprint.update(str(values.shape).encode())
            fingerprint.update(values.tobytes())
        key = fingerprint.hexdigest()
        strategy_id = row["strategy"].strategy_id
        if key in seen:
            duplicates.append({"strategy_id": strategy_id, "same_validation_as": seen[key]})
        else:
            seen[key] = strategy_id
            if len(selected) < count:
                selected.append(row)
    return selected, duplicates


def validate(backtester, ranked, config, periods, check, progress):
    definitions = [StrategyDefinition.model_validate(r["definition"]) for r in ranked]
    folds = walk_forward(backtester, definitions, config, periods, check)
    candidates = []
    for strategy in definitions:
        check()
        training = backtester.run(strategy, config, *periods["train"])
        model, note, mask = None, "Disabled", None
        if config.ml_filter:
            model, note = fit_filter(backtester.features, training, config.seed)
            if model:
                mask = predict(model, backtester.features.ml_matrix()) >= model["threshold"]
        validation = backtester.run(strategy, config, *periods["validation"], mask)
        candidates.append(
            {
                "strategy": strategy,
                "training": training,
                "validation": validation,
                "model": model,
                "ml_note": note,
                "mask": mask,
                "score": score(validation, strategy, config),
            }
        )
    # Freeze order and portfolio membership before any holdout access.
    selected, duplicates = distinct_finalists(candidates, config.finalists)
    portfolio_ids, portfolio_returns = [], []
    for row in selected:
        curve = np.r_[config.capital, row["validation"]["equity"]]
        returns = np.diff(curve) / np.maximum(curve[:-1], 1e-9)
        if returns.std() == 0:
            continue
        if all(abs(float(np.corrcoef(returns, other)[0, 1])) < 0.8 for other in portfolio_returns):
            portfolio_ids.append(row["strategy"].strategy_id)
            portfolio_returns.append(returns)
    results = []
    for row in selected:
        check()
        strategy, validation = row["strategy"], row["validation"]
        progress(f"Robustness checks: {strategy.strategy_id}")
        robust = robustness(
            backtester, strategy, config, *periods["validation"], validation, row["mask"], check
        )
        # Per-strategy forward slices, all subsequent to the discovery training window.
        forward = [
            backtester.run(strategy, config, *fold["test"], row["mask"])["metrics"]
            for fold in folds
        ]
        progress(f"Final unseen test: {strategy.strategy_id}")
        holdout = backtester.run(strategy, config, *periods["test"], row["mask"])
        stressed_costs = config.costs.model_copy(
            update={
                "fee": config.costs.fee * 3,
                "slippage": config.costs.slippage * 3,
                "spread": config.costs.spread * 3,
            }
        )
        stress_holdout = backtester.run(
            strategy,
            config.model_copy(update={"costs": stressed_costs}),
            *periods["test"],
            row["mask"],
        )
        failures = []
        for name, result in [
            ("training", row["training"]),
            ("validation", validation),
            ("unseen test", holdout),
        ]:
            m = result["metrics"]
            if m["net_profit"] <= 0:
                failures.append(f"{name}: net loss after costs")
            if m["trades"] < config.min_trades:
                failures.append(f"{name}: fewer than {config.min_trades} trades")
            if m["halted"]:
                failures.append(f"{name}: safety limit stopped trading")
        if sum(m["net_profit"] > 0 for m in forward) < math.ceil(len(forward) * 2 / 3):
            failures.append("walk-forward: fewer than two thirds of folds profitable")
        if not robust["passed"]:
            failures.append("robustness: parameter, cost or Monte Carlo check failed")
        if stress_holdout["metrics"]["net_profit"] <= 0 or stress_holdout["metrics"]["halted"]:
            failures.append("unseen test: triple trading costs failed")
        if config.ml_filter and row["model"] is None:
            failures.append("ML filter requested but insufficient training evidence")
        results.append(
            {
                "strategy_id": strategy.strategy_id,
                "definition": strategy.model_dump(mode="json"),
                "status": "rejected" if failures else "research_passed",
                "reasons": failures,
                "training": summarize(row["training"]),
                "validation": summarize(validation),
                "test": summarize(holdout),
                "stress_test": summarize(stress_holdout),
                "walk_forward": forward,
                "robustness": robust,
                "regimes": regimes(backtester, holdout, periods["train"][1]),
                "model": row["model"],
                "ml_note": row["ml_note"],
                "validation_score": row["score"],
            }
        )
    return {
        "strategies": results,
        "validation_duplicates": duplicates,
        "walk_forward_selection": folds,
        "selection_audit": [
            {
                "strategy_id": r["strategy"].strategy_id,
                "validation_score": r["score"],
                "metrics": r["validation"]["metrics"],
            }
            for r in candidates
        ],
        "portfolio": {
            "selected_before_holdout": portfolio_ids,
            "maximum_absolute_correlation": 0.8,
            "note": "Diversification proposal only; separate strategy accounts, "
            "not a shared-margin portfolio simulation",
        },
    }
