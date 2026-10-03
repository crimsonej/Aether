from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from aether.core.context.models import MarketContext
from aether.core.features.base import FeatureValue
from aether.core.data.normalizer import NormalizedCandle # Fixed typo in import

class Strategy(ABC):
    """
    Base class for all trading strategies.
    """
    def __init__(self, name: str, version: str, config: Dict[str, Any]):
        self.name = name
        self.version = version
        self.config = config

    @abstractmethod
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        """
        Returns "BUY", "SELL", or "NEUTRAL".
        """
        pass

    @abstractmethod
    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        """
        Returns a confidence score from 0 to 100.
        """
        pass

    @abstractmethod
    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        """
        Returns a trade construction proposal.
        """
        pass
