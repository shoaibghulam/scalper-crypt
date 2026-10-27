"""Ten attempts to make CTK profitable, for each timeframe.

WHY THE SOURCE LOSES. Measured over one year of BTCUSDT 1m candles:

    CTK 1   1149 trades   580 TP   569 SL   win rate 50.5%   net -145.93
    CTK 5    471 trades   246 TP   225 SL   win rate 52.2%   net  -56.09

More take-profits than stop-losses, and still a loss. That is not bad luck,
it is the ratio. Each win pays 0.86 and each loss costs 1.00, so

    break-even win rate = (1 + fees/stop) / (1 + reward/risk)

    CTK 1:  (1 + 0.103) / 1.86 = 59.3% needed,  50.5% delivered  -> -0.164R/trade
    CTK 5:  (1 + 0.069) / 1.86 = 57.5% needed,  52.2% delivered  -> -0.097R/trade

Reconstructing the reported net from those two numbers alone gives -145.93 and
-56.08, which accounts for the whole loss. Nothing exotic is going wrong.

There are exactly three levers, and every variant below pulls one of them:

    raise the reward        v1, v2     lowers the win rate required
    raise the win rate      v3 - v8    better entries, fewer bad fades
    cut fees per unit risk  v9         a wider stop makes the fee share smaller

v10 stacks the ones that are independent.

SELECTION BIAS IS REAL. Twenty variants scored on one symbol and one year will
produce a best performer whether or not any of them has an edge. Treat a
winner here as a hypothesis to re-test on symbols and dates it has never seen,
not as a result. The per-period breakdown in the report exists for this.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from scalper.exchange.market_data import Candle
from scalper.strategies.base import StrategyContext
from scalper.strategies.ctk import SHARED_ASSUMPTIONS, CtkParams, _CtkPo3

VARIANT_COUNT = 10
SOURCE_REWARD = Decimal("0.86")


@dataclass(frozen=True)
class CtkRecipe:
    """One hypothesis about why the fade loses, and the change it implies."""

    label: str
    #: What this variant changes, and why that should matter.
    rationale: str
    params: CtkParams


def break_even_win_rate(reward: Decimal, cost_over_stop: float) -> float:
    """Share of trades that must win for this reward to pay for itself."""
    return (1 + cost_over_stop) / (1 + float(reward))


#: The three changes whose edge beat the no-edge line on BOTH test windows.
#: Everything else tracked 1/(1+R) - the hit rate of a target R times the stop
#: with no directional edge at all - or fell below it.
CLOSE = {"close_confirmed": True}
PUSH = {"manipulation_mult": 1.5}
VOLUME = {"min_volume_ratio": 1.8}

#: A WIDER stop was tried and refuted. It lowers fees as a share of risk, which
#: is real, but the target widens with it and moves outside the horizon the
#: reversion works over: at 10 x ATR the edge went from +4.9 to -3.8. 7 x is
#: the milder setting v10 uses to find where that trade-off turns.
MILD_RISK = 7.0

RECIPES: tuple[CtkRecipe, ...] = (
    CtkRecipe(
        label="Close-confirmed break",
        rationale=(
            "The strongest single survivor: +4.9 and +2.0 points above the "
            "no-edge line on two independent windows. The source fades a wick, "
            "and a spike through the level is the move most likely to keep "
            "going; this asks the bar to close beyond it instead."
        ),
        params=CtkParams(**CLOSE),
    ),
    CtkRecipe(
        label="Deeper manipulation",
        rationale=(
            "Second survivor: +2.6 and +2.8. 0.6 ATR past the box is a small "
            "push to call manipulation; 1.5 takes fewer with more stretch "
            "behind them to revert."
        ),
        params=CtkParams(**PUSH),
    ),
    CtkRecipe(
        label="Volume-confirmed sweep",
        rationale=(
            "Third survivor: +3.9 and +2.9. A stop run that reverses shows up "
            "as volume, while quiet drift past the edge is continuation."
        ),
        params=CtkParams(**VOLUME),
    ),
    CtkRecipe(
        label="Close-confirmed + deeper push",
        rationale=(
            "First of the pairs. Each survivor alone gives two to five points "
            "where about twelve are needed to clear fees, so the question is "
            "whether they stack or overlap - two filters rejecting the same "
            "bad trades add nothing."
        ),
        params=CtkParams(**CLOSE, **PUSH),
    ),
    CtkRecipe(
        label="Close-confirmed + volume",
        rationale=(
            "The second pair. Close confirmation and volume look at different "
            "things - where the bar settled, and how hard it traded - so this "
            "is the pair most likely to be additive rather than redundant."
        ),
        params=CtkParams(**CLOSE, **VOLUME),
    ),
    CtkRecipe(
        label="Deeper push + volume",
        rationale=(
            "The third pair, and the control for the other two: it is the only "
            "one without close confirmation, so comparing it against them "
            "isolates how much that single change is carrying."
        ),
        params=CtkParams(**PUSH, **VOLUME),
    ),
    CtkRecipe(
        label="All three survivors",
        rationale=(
            "Every change that held its edge on both windows, together. If the "
            "three are additive this clears the fee bill; if they overlap it "
            "lands near the best single one with far fewer trades. That "
            "difference is the whole experiment."
        ),
        params=CtkParams(**CLOSE, **PUSH, **VOLUME),
    ),
    CtkRecipe(
        label="All three plus trend",
        rationale=(
            "The trend filter tracked the no-edge line exactly on its own "
            "(-0.3), but it was filtering a stream with no edge in it. On top "
            "of three filters that do have one, it may be selecting among "
            "better trades rather than among coin tosses."
        ),
        params=CtkParams(**CLOSE, **PUSH, **VOLUME, trend_filter=True),
    ),
    CtkRecipe(
        label="All three at 1.2R",
        rationale=(
            "The survivors with a larger reward. Generation one showed the "
            "win rate falling almost exactly as fast as the requirement when "
            "the reward rose - the signature of no edge. Repeating it on a "
            "stream that does have edge tests whether that still holds."
        ),
        params=CtkParams(**CLOSE, **PUSH, **VOLUME, reward_risk=Decimal("1.2")),
    ),
    CtkRecipe(
        label="All three with a 7 ATR stop",
        rationale=(
            "The fee-versus-horizon trade-off at a milder setting. At 10 x ATR "
            "a wider stop destroyed the edge it was meant to protect; 7 x asks "
            "whether there is a middle where fees fall without the target "
            "moving out of reach."
        ),
        params=CtkParams(**CLOSE, **PUSH, **VOLUME, risk_mult=MILD_RISK),
    ),
)


class _CtkVariant(_CtkPo3):
    """A CTK with one hypothesis applied. Subclasses pick the candle series."""

    base_id = "ctk"
    base_name = "CTK"
    #: Fees as a share of the stop, measured on the year that motivated these.
    observed_cost_over_stop = 0.10

    def __init__(self, number: int) -> None:
        if not 1 <= number <= VARIANT_COUNT:
            raise ValueError(f"CTK variant must be 1-{VARIANT_COUNT}")
        recipe = RECIPES[number - 1]
        self.number = number
        self.recipe = recipe
        self.id = f"{self.base_id}-v{number}"
        self.name = f"{self.base_name} v{number}"
        self.params = recipe.params
        # The declared minimum and the actual target must always agree, or the
        # plan gate refuses every trade this variant produces.
        self.min_reward_to_risk = recipe.params.reward_risk
        needed = break_even_win_rate(recipe.params.reward_risk, self.observed_cost_over_stop)
        self.summary = (
            f"{self.base_name} PO3, variant {number} - {recipe.label}. "
            f"{recipe.rationale} Reward {recipe.params.reward_risk}R, so about "
            f"{needed:.0%} of trades must win after fees."
        )
        reward_note = (
            f"Reward to risk is {recipe.params.reward_risk}, not the source 0.86, so "
            f"roughly {needed:.0%} of trades must win after fees."
            if recipe.params.reward_risk != SOURCE_REWARD
            else "Reward to risk is unchanged from the source 0.86; this variant "
            "attacks the win rate instead of the ratio."
        )
        self.assumptions = (
            *SHARED_ASSUMPTIONS,
            f"Variant {number} of {VARIANT_COUNT}: {recipe.label}. {recipe.rationale}",
            reward_note,
            "One of twenty variants scored on the same symbol and period. The best "
            "of twenty looks good whether or not it has an edge - re-test any "
            "winner on symbols and dates it has not seen.",
        )


class Ctk1Variant(_CtkVariant):
    base_id = "ctk-1"
    base_name = "CTK 1"
    interval_ms = 60_000
    history_1m = 120
    history_5m = 20
    observed_cost_over_stop = 0.103

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        return ctx.candles_1m


class Ctk5Variant(_CtkVariant):
    base_id = "ctk-5"
    base_name = "CTK 5"
    interval_ms = 300_000
    # Deliberately NOT overriding signal_interval_s. CTK 5 inherits the 60s
    # default, so a 5-minute setup is re-detected on each of the five minutes
    # it remains the last closed bar. Raising it here would make every variant
    # differ from the base in two ways at once and destroy the attribution
    # this ladder exists for. The cadence is measured separately.
    history_1m = 40
    history_5m = 120
    observed_cost_over_stop = 0.069

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        return ctx.candles_5m


def ctk_variants() -> list[_CtkVariant]:
    """All twenty, in the order they should be read."""
    return [
        *(Ctk1Variant(n) for n in range(1, VARIANT_COUNT + 1)),
        *(Ctk5Variant(n) for n in range(1, VARIANT_COUNT + 1)),
    ]

#: v22: the best configuration the stop-width sweep found.
#:
#: Fees are the dominant term in CTK's loss, not the entry. At the source's
#: 5 x ATR stop a round trip costs about 18% of the risk, which lifts the
#: break-even win rate to roughly 64% - about twelve points above what the
#: fade delivers. Widening the stop is the only lever that moves that term
#: without changing venue.
#:
#: Measured across BTCUSDT, ETHUSDT and DOGEUSDT, sweeping the stop from 4 to
#: 34 x ATR with the three surviving filters held constant:
#:
#:      stop    trades   win%   fee/R   break-even   net
#:       4       282     54.1%   ~22%      65%      -47.49
#:       5       262     57.1%   ~18%      64%      -31.62
#:       8       207     58.9%   ~14%      61%      -14.65
#:      12       134     58.3%    11%      59.8%     -4.85
#:      26        51     55.1%     5%      56.5%     -3.76
#:
#: Twelve is the turn: past it the trade count collapses and the win rate
#: falls faster than the fee saving. It closes the gap from about twelve
#: points to about one and a half - near break-even, NOT profitable. It is
#: published as the best measured configuration, not as a working system.
V22_PARAMS = CtkParams(
    close_confirmed=True,
    manipulation_mult=1.5,
    min_volume_ratio=1.8,
    risk_mult=12.0,
)

V22_RATIONALE = (
    "Fee-optimised CTK. The three filters whose edge held across every test "
    "window, on a 12 x ATR stop chosen by sweeping stop width against fee "
    "drag. A round trip falls from about 18% of the risk to 11%, and the "
    "break-even win rate from roughly 64% to 60% against a delivered 58%. "
    "That is near break-even and short of profitable - the remaining gap is "
    "execution: every leg is currently a market order paying taker fees."
)


class _CtkV22(_CtkPo3):
    """The best configuration found, outside the numbered ladder."""

    params = V22_PARAMS
    min_reward_to_risk = V22_PARAMS.reward_risk
    assumptions = (
        *SHARED_ASSUMPTIONS,
        V22_RATIONALE,
        "The stop width was chosen on BTCUSDT, ETHUSDT and DOGEUSDT. Any coin "
        "in that set is in-sample and its result is optimistic by construction.",
        "Near break-even is not profitable. Measured at roughly 1.5 points "
        "below the win rate it needs; that is inside the noise of a few "
        "hundred trades, so it is a candidate to test, not an edge to trade.",
    )


class Ctk1V22(_CtkV22):
    id = "ctk-1-v22"
    name = "CTK 1 v22"
    summary = (
        "Fee-optimised CTK PO3 on 1m: three confirmed filters, 12 x ATR stop. "
        + V22_RATIONALE
    )
    interval_ms = 60_000
    history_1m = 120
    history_5m = 20

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        return ctx.candles_1m


class Ctk5V22(_CtkV22):
    id = "ctk-5-v22"
    name = "CTK 5 v22"
    summary = (
        "Fee-optimised CTK PO3 on 5m: three confirmed filters, 12 x ATR stop. "
        + V22_RATIONALE
    )
    interval_ms = 300_000
    history_1m = 40
    history_5m = 120

    def _bars(self, ctx: StrategyContext) -> list[Candle]:
        return ctx.candles_5m

