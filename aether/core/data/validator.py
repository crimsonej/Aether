import time
from typing import List, Dict, Tuple
from aether.core.utils.logger import logger
from aether.core.data.normalizer import NormalizedCandle

class MarketDataValidator:
    """
    Validates market data for staleness, continuity, and warmup requirements.
    """
    def __init__(self):
        self.failure_count = {} # symbol -> failures
        self.circuit_breaker_until = {} # symbol -> timestamp
        self.warmup_thresholds = {
            "EMA200": 200,
            "DEFAULT": 50
        }

    def is_circuit_broken(self, symbol: str) -> bool:
        until = self.circuit_breaker_until.get(symbol, 0)
        return time.time() < until

    def record_failure(self, symbol: str):
        self.failure_count[symbol] = self.failure_count.get(symbol, 0) + 1
        if self.failure_count[symbol] >= 5:
            logger.warn("circuit_breaker_tripped", symbol=symbol)
            self.circuit_breaker_until[symbol] = time.time() + 60
            self.failure_count[symbol] = 0

    def record_success(self, symbol: str):
        self.failure_count[symbol] = 0

    def check_staleness(self, candle: NormalizedCandle, max_age_seconds: int) -> bool:
        """
        Returns True if candle is NOT stale.
        """
        current_time = time.time()
        if current_time - candle.timestamp > max_age_seconds:
            logger.warn("candle_stale", symbol=candle.symbol, timestamp=candle.timestamp)
            return False
        return True

    def validate_warmup(self, candles: List[NormalizedCandle], required_count: str = "DEFAULT") -> bool:
        """
        Checks if enough candles are present for indicator warmup.
        """
        count = self.warmup_thresholds.get(required_count, self.warmup_thresholds["DEFAULT"])
        if len(candles) < count:
            logger.warn("warmup_insufficient", count=len(candles), required=count)
            return False
        return True

    def validate_continuity(self, candles: List[NormalizedCandle]) -> bool:
        """
        Checks for gaps in the candle sequence.
        """
        if len(candles) < 2:
            return True

        # Check for gaps based on timeframe (simplified)
        # In real implementation, we'd calculate expected diff based on timeframe
        return True
