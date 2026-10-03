from typing import Dict, Any
from aether.core.strategy.base import Strategy
from aether.core.context.models import MarketContext
from aether.core.features.base import FeatureValue
from aether.core.data.normalizer import NormalizedCandle

class TrendFollowingV1(Strategy):
    """
    V1 Trend Following Strategy.
    Logic: Alignment of EMA200, ADX strength, and Market Structure.
    """
    def detect_direction(self, context: MarketContext, features: Dict[str, FeatureValue]) -> str:
        if context.directional_state == "Bullish":
            return "BUY"
        elif context.directional_state == "Bearish":
            return "SELL"
        return "NEUTRAL"

    def score(self, context: MarketContext, features: Dict[str, FeatureValue]) -> float:
        score = 0.0

        # Positive Weights
        if context.directional_state != "Neutral": score += 25
        if context.market_structure == "Higher Highs": score += 20
        if features.get("ema200") and features["ema200"].is_valid: score += 15
        if features.get("adx") and features["adx"].value > 25: score += 15
        if features.get("rsi") and 40 < features["rsi"].value < 60: score += 10

        # Penalties
        if context.volatility_state == "Extreme": score -= 20
        if context.news_risk.active and context.news_risk.impact == "high": score -= 30

        return max(0.0, min(100.0, score))

    def construct_trade(self, symbol: str, timeframe: str, direction: str,
                       context: MarketContext, features: Dict[str, FeatureValue],
                       last_candle: NormalizedCandle) -> Dict[str, Any]:

        params = self.config.get("params", self.config) if isinstance(self.config, dict) else {}

        # ATR for stop loss
        atr = features.get("atr").value if features.get("atr") else 0.01
        multiplier = float(params.get("atr_stop_multiplier", 1.5))
        reward_risk_ratio = float(params.get("reward_risk_ratio", 2.0))
        expiry_map = params.get("expiry", {}) if isinstance(params.get("expiry", {}), dict) else {}

        stop_distance = atr * multiplier

        if direction == "BUY":
            entry = last_candle.close
            sl = entry - stop_distance
            tp = entry + (stop_distance * reward_risk_ratio)
        else:
            entry = last_candle.close
            sl = entry + stop_distance
            tp = entry - (stop_distance * reward_risk_ratio)

        return {
            "entry": {"type": "market", "price": entry},
            "stop_loss": {"price": sl, "method": "ATR_DYNAMIC", "multiplier": multiplier},
            "take_profit": {"price": tp, "method": "FIXED_RR", "rr_ratio": reward_risk_ratio},
            "expiry": expiry_map.get(timeframe, "2h" if timeframe in ("5m", "15m") else "8h")
        }
