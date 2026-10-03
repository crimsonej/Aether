import math
from typing import Dict, Any

class ValidationFirewall:
    """
    The final gate for signals before emission.
    """
    def __init__(self, config):
        self.config = config

    def validate(self, signal_candidate: Dict[str, Any]) -> Dict[str, Any]:
        failed_checks = []
        warnings = []
        penalties = 0.0

        try:
            score = float(signal_candidate.get("score"))
        except (TypeError, ValueError):
            score = math.nan
        if not math.isfinite(score) or not 0 <= score <= 100:
            failed_checks.append("invalid_confidence")
            score = 0.0

        try:
            min_confidence = float(self.config.get("validation.min_confidence", 60))
        except (AttributeError, TypeError, ValueError):
            min_confidence = 60.0
        if score < min_confidence:
            failed_checks.append("insufficient_confidence")

        direction = signal_candidate.get("direction")
        if direction not in ("BUY", "SELL"):
            failed_checks.append("invalid_direction")

        context = signal_candidate.get("context")
        session = self._get(context, "session")
        volatility = self._get(context, "volatility_state")
        news_risk = self._get(context, "news_risk")
        liquidity = self._get(context, "liquidity_state")

        if session in ("Off Hours", "Weekend"):
            failed_checks.append("disallowed_session")
        if volatility == "Extreme":
            failed_checks.append("extreme_volatility")
        if self._get(news_risk, "active") and self._get(news_risk, "impact") == "high":
            failed_checks.append("high_impact_news")
        elif self._get(news_risk, "active") and self._get(news_risk, "impact") == "medium":
            penalties += 15
            warnings.append("medium_impact_news")
        if liquidity == "Low":
            penalties += 15
            warnings.append("low_liquidity")

        trade = signal_candidate.get("trade") or {}
        entry = self._price(trade, "entry")
        stop = self._price(trade, "stop_loss")
        target = self._price(trade, "take_profit")
        if None in (entry, stop, target):
            failed_checks.append("invalid_trade_prices")
        elif direction == "BUY" and not stop < entry < target:
            failed_checks.append("invalid_trade_structure")
        elif direction == "SELL" and not target < entry < stop:
            failed_checks.append("invalid_trade_structure")

        current_spread = self._finite_number(signal_candidate.get("current_spread"))
        typical_spread = self._finite_number(signal_candidate.get("typical_spread"))
        if current_spread is None or typical_spread is None or typical_spread <= 0:
            warnings.append("spread_data_unavailable")
        else:
            try:
                spread_limit = float(self.config.get("validation.max_spread_multiplier", 2.0))
            except (AttributeError, TypeError, ValueError):
                spread_limit = 2.0
            if current_spread < 0 or current_spread > typical_spread * spread_limit:
                failed_checks.append("excessive_spread")

        adjusted_score = max(0.0, min(100.0, score - penalties))
        if adjusted_score < min_confidence and "insufficient_confidence" not in failed_checks:
            failed_checks.append("insufficient_adjusted_confidence")

        return {
            "valid": not failed_checks,
            "reason": failed_checks[0] if failed_checks else None,
            "score": score,
            "adjusted_score": adjusted_score,
            "failed_checks": failed_checks,
            "warnings": warnings,
        }

    @staticmethod
    def _get(value: Any, key: str):
        if isinstance(value, dict):
            return value.get(key)
        return getattr(value, key, None)

    @classmethod
    def _price(cls, trade: Dict[str, Any], key: str):
        level = trade.get(key)
        value = cls._get(level, "price")
        number = cls._finite_number(value)
        return number if number is not None and number > 0 else None

    @staticmethod
    def _finite_number(value: Any):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
