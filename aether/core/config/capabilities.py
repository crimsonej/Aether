from typing import List, Dict
from pydantic import BaseModel

class CapabilityRegistry(BaseModel):
    enabled_indicators: List[str]
    enabled_timeframes: List[str]
    enabled_symbols: List[str]
    enabled_strategies: List[str]

def get_default_capabilities():
    return CapabilityRegistry(
        enabled_indicators=["ema20", "rsi", "atr"],
        enabled_timeframes=["15m", "1h", "4h"],
        enabled_symbols=["EURUSD", "XAUUSD"],
        enabled_strategies=["trend_following_v1"]
    )
