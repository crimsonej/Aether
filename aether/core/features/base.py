from pydantic import BaseModel
from typing import Any, Dict, Optional

class FeatureValue(BaseModel):
    value: float
    lookback_required: int
    is_valid: bool
    category: str

class Indicator:
    """
    Base class for all technical indicators.
    """
    def __init__(self, name: str, category: str, lookback: int):
        self.name = name
        self.category = category
        self.lookback = lookback

    def compute(self, candles: list) -> FeatureValue:
        raise NotImplementedError("Indicators must implement compute()")
