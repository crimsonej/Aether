from __future__ import annotations

from typing import Any, Dict

from aether.core.context.models import MarketContext
from aether.core.data.normalizer import NormalizedCandle
from aether.core.features.base import FeatureValue
from aether.core.strategy.base import Strategy
from aether.core.strategy.trend_following import TrendFollowingV1


class ApprovedBuiltinStrategy(Strategy):
    """Common helper for the approved built-in profile set."""

    def _direction(self, context: MarketContext) -> str:
        if context.directional_state == "Bullish":
            return "BUY"
        if context.directional_state == "Bearish":
            return "SELL"
        if context.market_structure in {"Higher Highs", "Breakout Up"}:
            return "BUY"
        if context.market_structure in {"Lower Lows", "Breakout Down"}:
            return "SELL"
        return "NEUTRAL"

    @staticmethod
    def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
        return max(lower, min(upper, value))

    def _construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        atr = features.get("atr").value if features.get("atr") else 0.01
        multiplier = float(self.config.get("params", {}).get("atr_stop_multiplier", 1.0)) if isinstance(self.config, dict) else 1.0
        rr = float(self.config.get("params", {}).get("reward_risk_ratio", 2.0)) if isinstance(self.config, dict) else 2.0
        stop_distance = max(atr * multiplier, 0.0001)

        if direction == "BUY":
            entry = float(last_candle.close)
            sl = entry - stop_distance
            tp = entry + (stop_distance * rr)
        elif direction == "SELL":
            entry = float(last_candle.close)
            sl = entry + stop_distance
            tp = entry - (stop_distance * rr)
        else:
            entry = float(last_candle.close)
            sl = entry
            tp = entry

        expiry = self.config.get("params", {}).get("expiry", {}).get(timeframe, "8h") if isinstance(self.config, dict) else "8h"
        return {
            "entry": {"type": "market", "price": entry},
            "stop_loss": {"price": sl, "method": "ATR_DYNAMIC", "multiplier": multiplier},
            "take_profit": {"price": tp, "method": "FIXED_RR", "rr_ratio": rr},
            "expiry": expiry,
        }


class FVGStrategy(ApprovedBuiltinStrategy):
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        return self._direction(context)

    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        score = 62.0
        if context.directional_state == "Bullish":
            score += 18
        elif context.directional_state == "Bearish":
            score += 18
        if context.market_structure in {"Higher Highs", "Lower Lows"}:
            score += 12
        if features.get("ema200") and features["ema200"].is_valid:
            score += 8
        if context.news_risk.active and context.news_risk.impact == "high":
            score -= 20
        return self._clamp(score)

    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        return self._construct_trade(symbol, timeframe, direction, context, features, last_candle)


class DemandSupplyStrategy(ApprovedBuiltinStrategy):
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        if context.market_structure in {"Higher Highs", "Breakout Up"}:
            return "BUY"
        if context.market_structure in {"Lower Lows", "Breakout Down"}:
            return "SELL"
        return self._direction(context)

    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        score = 60.0
        if context.liquidity_state == "High":
            score += 12
        if context.market_structure in {"Higher Highs", "Lower Lows"}:
            score += 18
        if features.get("rsi") and features["rsi"].is_valid:
            score += 8
        if context.news_risk.active:
            score -= 15
        return self._clamp(score)

    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        return self._construct_trade(symbol, timeframe, direction, context, features, last_candle)


class LiquidityStrategy(ApprovedBuiltinStrategy):
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        if context.liquidity_state == "High" and context.directional_state == "Bullish":
            return "BUY"
        if context.liquidity_state == "High" and context.directional_state == "Bearish":
            return "SELL"
        return self._direction(context)

    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        score = 58.0
        if context.liquidity_state == "High":
            score += 22
        if context.directional_state in {"Bullish", "Bearish"}:
            score += 10
        if features.get("adx") and features["adx"].is_valid and features["adx"].value > 25:
            score += 12
        return self._clamp(score)

    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        return self._construct_trade(symbol, timeframe, direction, context, features, last_candle)


class SupportResistanceStrategy(ApprovedBuiltinStrategy):
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        if context.market_structure in {"Higher Highs", "Breakout Up"}:
            return "BUY"
        if context.market_structure in {"Lower Lows", "Breakout Down"}:
            return "SELL"
        return self._direction(context)

    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        score = 56.0
        if context.directional_state == "Bullish":
            score += 14
        elif context.directional_state == "Bearish":
            score += 14
        if context.market_structure in {"Higher Highs", "Lower Lows"}:
            score += 12
        if features.get("ema50") and features["ema50"].is_valid:
            score += 8
        return self._clamp(score)

    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        return self._construct_trade(symbol, timeframe, direction, context, features, last_candle)


class OrderBlockStrategy(ApprovedBuiltinStrategy):
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        return self._direction(context)

    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        score = 59.0
        if context.market_structure in {"Higher Highs", "Lower Lows"}:
            score += 18
        if context.volatility_state == "Normal":
            score += 8
        if features.get("adx") and features["adx"].is_valid and features["adx"].value > 25:
            score += 10
        if context.news_risk.active and context.news_risk.impact == "high":
            score -= 18
        return self._clamp(score)

    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                        context: MarketContext, features: Dict[str, FeatureValue],
                        last_candle: NormalizedCandle) -> Dict[str, Any]:
        return self._construct_trade(symbol, timeframe, direction, context, features, last_candle)


def build_default_strategies():
    return [
        TrendFollowingV1("trend_following_v1", "1.0.0", {}),
        FVGStrategy("fvg", "1.0.0", {}),
        DemandSupplyStrategy("demand_supply", "1.0.0", {}),
        LiquidityStrategy("liquidity", "1.0.0", {}),
        SupportResistanceStrategy("support_resistance", "1.0.0", {}),
        OrderBlockStrategy("order_block", "1.0.0", {}),
    ]
