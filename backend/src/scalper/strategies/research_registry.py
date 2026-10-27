"""Research catalog; execution-capable candidates also register for testnet trading."""

from scalper.strategies.btc_selective import BtcSelectiveReversed, BtcSelectiveReversion
from scalper.strategies.changez import CHANGEZ_STRATEGIES
from scalper.strategies.combinations import COMBINATIONS
from scalper.strategies.funded_price_action import FundedPriceAction
from scalper.strategies.ivb import IvbCrypto
from scalper.strategies.laboratory import LAB_STRATEGIES
from scalper.strategies.sma_cross import SMA_CROSS_STRATEGIES
from scalper.strategies.minute_expansion import MINUTE_STRATEGIES
from scalper.strategies.mtf_structure import MultiTimeframeStructure
from scalper.strategies.nine_indicator import NineIndicatorPullback
from scalper.strategies.pdf_advanced_five_minute import ADVANCED_FIVE_MINUTE_STRATEGIES
from scalper.strategies.pdf_five_minute import PDF_FIVE_MINUTE_STRATEGIES
from scalper.strategies.ten_minute import TEN_MINUTE_STRATEGIES

RESEARCH_STRATEGIES = {
    BtcSelectiveReversion.id: BtcSelectiveReversion(),
    BtcSelectiveReversed.id: BtcSelectiveReversed(),
    **CHANGEZ_STRATEGIES,
    **LAB_STRATEGIES, **SMA_CROSS_STRATEGIES, **COMBINATIONS, **MINUTE_STRATEGIES, **PDF_FIVE_MINUTE_STRATEGIES,
    **ADVANCED_FIVE_MINUTE_STRATEGIES,
    "funded-price-action": FundedPriceAction(),
    IvbCrypto.id: IvbCrypto(),
    MultiTimeframeStructure.id: MultiTimeframeStructure(),
    NineIndicatorPullback.id: NineIndicatorPullback(),
    **TEN_MINUTE_STRATEGIES,
}
