"""Seeded bounded evolution plus training-only Optuna parameter refinement."""

import random
from collections import OrderedDict

import numpy as np

from scalper.research.backtest import score
from scalper.research.schema import Condition, Operand, Risk, StrategyDefinition


def compare(name, period, op, other=None, other_period=14, value=None):
    return Condition(
        op=op,
        left=Operand(indicator=name, period=period),
        right=Operand(indicator=other, period=other_period) if other else Operand(value=value),
    )


def random_rule(rng):
    p = rng.choice([5, 9, 14, 20, 30, 50])
    op = rng.choice(["gt", "lt", "cross_up", "cross_down"])
    family = rng.randrange(10)
    if family == 0:
        return compare(rng.choice(["ema", "sma", "wma"]), p, op, "ema", rng.choice([20, 50, 100]))
    if family == 1:
        return compare(
            rng.choice(["rsi", "stochastic"]), p, op, value=rng.choice([20, 30, 45, 55, 70, 80])
        )
    if family == 2:
        return compare("close", p, op, rng.choice(["bb_upper", "bb_lower", "vwap"]), p)
    if family == 3:
        return compare("volume", p, rng.choice(["gt", "lt"]), "volume_sma", p)
    if family == 4:
        return compare("adx", p, rng.choice(["gt", "lt"]), value=rng.choice([15, 20, 25, 30]))
    if family == 5:
        return compare(
            rng.choice(["roc", "returns"]), p, op, value=rng.choice([-0.01, -0.002, 0, 0.002, 0.01])
        )
    if family == 6:
        return compare(rng.choice(["macd", "momentum"]), p, op, value=0)
    if family == 7:
        return compare("atr", p, op, "atr", min(200, p * 2))
    if family == 8:
        return compare("volatility", p, op, value=rng.choice([0.001, 0.003, 0.01]))
    return compare("close", p, op, rng.choice(["ema", "sma", "wma"]), p)


def random_strategy(rng, timeframe, config):
    entry = random_rule(rng)
    if config.max_complexity >= 3 and rng.random() < 0.6:
        entry = Condition(op=rng.choice(["and", "or"]), children=(entry, random_rule(rng)))
    risk = Risk(
        stop_loss=rng.choice([0.004, 0.008, 0.012, 0.02]),
        take_profit=rng.choice([0.008, 0.016, 0.024, 0.04]),
        trailing_stop=rng.choice([0, 0, 0.008, 0.015]),
        position_size=min(config.safety.max_position_size, rng.choice([0.10, 0.20, 0.25])),
        leverage=min(config.safety.max_leverage, rng.choice([1, 1, 2, 3, 4, 5])),
        cooldown=rng.choice([0, 3, 6, 12]),
        max_hold=rng.choice([12, 36, 72, 144]),
    )
    exit_rule = (
        random_rule(rng) if entry.size < config.max_complexity and rng.random() < 0.3 else None
    )
    return StrategyDefinition(
        timeframe=timeframe,
        direction=rng.choice(["long", "short"]),
        entry=entry,
        risk=risk,
        exit=exit_rule,
        order_type="limit" if rng.random() < 0.1 else "market",
        limit_offset=rng.choice([0.0005, 0.001, 0.002]),
    )


def perturb(strategy, delta, config):
    raw = strategy.model_dump(mode="json")

    def walk(node):
        for side in ("left", "right"):
            operand = node[side]
            if operand:
                if operand["indicator"]:
                    operand["period"] = max(2, min(200, round(operand["period"] * (1 + delta))))
                elif operand["value"]:
                    operand["value"] *= 1 + delta
        for child in node["children"]:
            walk(child)

    walk(raw["entry"])
    if raw["exit"]:
        walk(raw["exit"])
    for key, low, high in [
        ("stop_loss", 0.001, 0.10),
        ("take_profit", 0.001, 0.30),
        ("trailing_stop", 0, 0.10),
        (
            "position_size",
            min(0.01, config.safety.max_position_size),
            config.safety.max_position_size,
        ),
    ]:
        raw["risk"][key] = max(low, min(high, raw["risk"][key] * (1 + delta)))
    return StrategyDefinition.model_validate(raw)


def evolve(backtester, config, first, last, emit, archive, check):
    rng = random.Random(config.seed)
    timeframe = backtester.dataset.request.timeframe
    population = [random_strategy(rng, timeframe, config) for _ in range(config.population)]
    cache, leaders = OrderedDict(), {}
    tested = 0
    keep = max(10, config.finalists * 4)

    def evaluate(strategy):
        nonlocal tested
        check()
        key = strategy.strategy_id
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        result = backtester.run(strategy, config, first, last)
        row = {
            "strategy_id": key,
            "definition": strategy.model_dump(mode="json"),
            "training": result["metrics"],
            "score": score(result, strategy, config),
        }
        archive(row)
        tested += 1
        cache[key] = row
        if len(cache) > 4096:
            cache.popitem(last=False)
        leaders[key] = row
        if len(leaders) > keep:
            del leaders[min(leaders, key=lambda k: leaders[k]["score"])]
        return row

    for generation in range(config.generations):
        ranked = sorted([evaluate(s) for s in population], key=lambda r: r["score"], reverse=True)
        profits = [r["training"]["net_profit"] for r in ranked]
        emit(
            {
                "generation": generation + 1,
                "total_tested": tested,
                "population": len(ranked),
                "best": ranked[0]["score"],
                "median": float(np.median([r["score"] for r in ranked])),
                "worst": ranked[-1]["score"],
                "profitable": sum(p > 0 for p in profits),
                "rejected": sum(
                    r["training"]["trades"] < config.min_trades
                    or r["training"]["net_profit"] <= 0
                    or r["training"]["halted"]
                    for r in ranked
                ),
                "best_net_profit": max(profits),
                "best_sharpe": max(r["training"]["sharpe"] for r in ranked),
                "lowest_drawdown": min(r["training"]["max_drawdown"] for r in ranked),
                "best_strategy": ranked[0],
            }
        )
        elites = [
            StrategyDefinition.model_validate(r["definition"])
            for r in ranked[: max(1, round(config.population * config.elite_fraction))]
        ]
        population = elites.copy()
        while len(population) < config.population:
            parent = rng.choice(elites)
            child = random_strategy(rng, timeframe, config)
            if rng.random() < config.mutation_probability:
                child = perturb(parent, rng.choice([-0.20, -0.10, -0.05, 0.05, 0.10, 0.20]), config)
                if rng.random() < 0.3:
                    child = child.model_copy(update={"entry": random_rule(rng)})
            if rng.random() < config.crossover_probability:
                mate = rng.choice(elites)
                combined = (
                    Condition(op=rng.choice(["and", "or"]), children=(parent.entry, mate.entry))
                    if max(parent.entry.depth, mate.entry.depth) < 4
                    and parent.entry.size + mate.entry.size < 15
                    else parent.entry
                )
                if combined.size <= config.max_complexity:
                    child = child.model_copy(update={"entry": combined, "risk": mate.risk})
            try:
                child = StrategyDefinition.model_validate(child.model_dump())
            except ValueError:
                continue
            if child.complexity <= config.max_complexity:
                population.append(child)
    if config.optimization_trials:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        seeds = sorted(leaders.values(), key=lambda r: r["score"], reverse=True)
        study = optuna.create_study(
            direction="maximize", sampler=optuna.samplers.TPESampler(seed=config.seed)
        )

        def objective(trial):
            base = StrategyDefinition.model_validate(
                seeds[trial.suggest_int("base", 0, len(seeds) - 1)]["definition"]
            )
            risk = base.risk.model_dump()
            risk.update(
                stop_loss=trial.suggest_float("stop_loss", 0.002, 0.04, log=True),
                take_profit=trial.suggest_float("take_profit", 0.004, 0.08, log=True),
                cooldown=trial.suggest_int("cooldown", 0, 20),
                max_hold=trial.suggest_int("max_hold", 6, 180),
                trailing_stop=trial.suggest_categorical(
                    "trailing_stop", [0.0, 0.004, 0.008, 0.016]
                ),
            )
            return evaluate(base.model_copy(update={"risk": Risk(**risk)}))["score"]

        study.optimize(objective, n_trials=config.optimization_trials)
    return sorted(leaders.values(), key=lambda r: r["score"], reverse=True), tested
