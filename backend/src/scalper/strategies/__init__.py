"""Every strategy this build knows about.

Adding one is a two-line change here plus its own module. The scanner, the
engine, the API and the dashboard all discover strategies through this
registry, so none of them needs to know which strategies exist.
"""

from scalper.strategies.base import (
    Levels,
    Setup,
    Strategy,
    StrategyContext,
    StrategyRegistry,
)
from scalper.strategies.black import MinuteBlack
from scalper.strategies.ctk import Ctk1, Ctk5
from scalper.strategies.ctk_variants import Ctk1V22, Ctk5V22, ctk_variants
from scalper.strategies.ema_pullback import EmaTrendPullback
from scalper.strategies.legend import MinuteLegendV1
from scalper.strategies.orb500 import Backtest500V1
from scalper.strategies.order_flow import OrderFlowPullback
from scalper.strategies.research import (
    LiquiditySweepReclaim,
    RangeVwapReversion,
    RollingBreakoutRetest,
)
from scalper.strategies.research_registry import RESEARCH_STRATEGIES
from scalper.strategies.vwap_band import VwapBandReversion

STRATEGIES = StrategyRegistry()
STRATEGIES.register(VwapBandReversion())
STRATEGIES.register(Backtest500V1())
STRATEGIES.register(MinuteLegendV1())
STRATEGIES.register(MinuteBlack())
STRATEGIES.register(EmaTrendPullback())
STRATEGIES.register(Ctk1())
STRATEGIES.register(Ctk5())
# Twenty variants that each attack one term of the break-even inequality the
# source fails; see ctk_variants for the arithmetic.
for _variant in ctk_variants():
    STRATEGIES.register(_variant)
# The best configuration the stop-width sweep found; see ctk_variants.
STRATEGIES.register(Ctk1V22())
STRATEGIES.register(Ctk5V22())
STRATEGIES.register(RollingBreakoutRetest())
STRATEGIES.register(RangeVwapReversion())
STRATEGIES.register(LiquiditySweepReclaim())
STRATEGIES.register(OrderFlowPullback())

# Research candidates are available for testnet evaluation, including Auto Trade.
for candidate in RESEARCH_STRATEGIES.values():
    if candidate.backtest_only:
        continue
    candidate.testnet_only = True
    candidate.manual_only = False
    STRATEGIES.register(candidate)

#: Used when a caller does not name one.
DEFAULT_STRATEGY_ID = VwapBandReversion.id

__all__ = [
    "DEFAULT_STRATEGY_ID",
    "STRATEGIES",
    "Levels",
    "Setup",
    "Strategy",
    "StrategyContext",
    "StrategyRegistry",
]
