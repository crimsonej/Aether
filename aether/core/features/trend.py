import numpy as np
from aether.core.features.base import Indicator, FeatureValue
from aether.core.data.normalizer import NormalizedCandle

class EMAIndicator(Indicator):
    """
    Exponential Moving Average indicator using numpy.
    """
    def __init__(self, period: int = 20):
        super().__init__(f"ema{period}", "trend", period)
        self.period = period

    def compute(self, candles: list) -> FeatureValue:
        if len(candles) < self.lookback:
            return FeatureValue(value=0.0, lookback_required=self.lookback, is_valid=False, category=self.category)

        closes = np.array([c.close for c in candles])

        # EMA calculation: EMA = close * alpha + EMA_prev * (1 - alpha)
        alpha = 2 / (self.period + 1)
        ema = closes[0]
        for price in closes[1:]:
            ema = price * alpha + ema * (1 - alpha)

        return FeatureValue(
            value=float(ema),
            lookback_required=self.lookback,
            is_valid=True,
            category=self.category
        )
