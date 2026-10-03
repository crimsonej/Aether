import time
import asyncio
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from aether.core.context.models import MarketContext, NewsRisk
from aether.core.utils.logger import logger
from aether.core.data.normalizer import NormalizedCandle

class ContextEngine:
    """
    Analyzes market data to provide situational awareness.
    """
    def __init__(self, config, bus=None):
        self.config = config
        self.bus = bus
        self._candle_cache: Dict[str, List[NormalizedCandle]] = {}
        self.news_data = self._load_static_news()
        self._running = False
        self._task = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.bus:
            await self.bus.subscribe("data.candle", self._on_candle)
        self._task = asyncio.create_task(self._heartbeat())
        logger.info("[ContextEngine] started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[ContextEngine] stopped")

    async def health(self) -> dict:
        return {
            "status": "OK" if self._running else "STOPPED",
            "component": "context_engine"
        }

    async def metrics(self) -> dict:
        return {}

    async def _on_candle(self, event: Dict[str, Any]):
        """Process incoming candle and compute context."""
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            if not symbol or not timeframe or event.get("is_closed") is not True:
                return
            key = f"{symbol}:{timeframe}"
            history = self._candle_cache.setdefault(key, [])
            for candle_payload in [*event.get("warmup_candles", []), event]:
                candle = self._cache_closed_candle(key, history, candle_payload)
                if candle is None and candle_payload is event:
                    return
            candle = history[-1]

            directional = self._get_directional_state(history)
            volatility = self._get_volatility_state(history)
            session, transition = self._get_session_info(candle.timestamp)
            news = self._get_news_risk()
            liquidity = self._get_liquidity_state(session, volatility)
            structure = self._get_market_structure(history)
            from aether.core.context.models import MarketContext
            context = MarketContext(
                directional_state=directional,
                volatility_state=volatility,
                session=session,
                session_transition=transition,
                news_risk=news,
                liquidity_state=liquidity,
                market_structure=structure
            )
            # Publish the context.
            if self.bus:
                await self.bus.publish("context.updated", {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": candle.timestamp,
                    "context_timestamp": candle.timestamp,
                    "context": context.__dict__  # or we could define a serialization method
                })
        except Exception as e:
            logger.exception("[ContextEngine] error processing candle: %s", e)

    def _cache_closed_candle(self, key: str, history: List[NormalizedCandle], payload: Dict[str, Any]):
        if not isinstance(payload, dict) or payload.get("is_closed") is not True:
            return None
        try:
            candle = NormalizedCandle(
                symbol=payload["symbol"],
                timeframe=payload["timeframe"],
                open=payload["open"],
                high=payload["high"],
                low=payload["low"],
                close=payload["close"],
                volume=payload.get("volume", 0.0),
                timestamp=payload["timestamp"],
                close_time=payload.get("close_time", payload["timestamp"]),
                source=payload.get("source") or "unknown",
                is_closed=True,
            )
        except Exception:
            logger.warning("[ContextEngine] rejected malformed candle history for %s", key)
            return None
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if (
            not all(math.isfinite(value) for value in values)
            or candle.volume < 0
            or candle.low > min(candle.open, candle.close)
            or candle.high < max(candle.open, candle.close)
            or candle.low > candle.high
        ):
            logger.warning("[ContextEngine] rejected invalid candle for %s", key)
            return None
        if history and candle.timestamp < history[-1].timestamp:
            return None
        if history and candle.timestamp == history[-1].timestamp:
            history[-1] = candle
        else:
            history.append(candle)
        if len(history) > 300:
            del history[:-300]
        return candle

    async def _heartbeat(self) -> None:
        """Optional heartbeat to keep the engine alive."""
        while self._running:
            await asyncio.sleep(30)
            # Could publish a heartbeat event if needed.

    # Keep the existing helper methods (_get_directional_state, etc.) unchanged.

    def _load_static_news(self) -> List[Dict[str, Any]]:
        """Load static news risk data.

        For V1 the system uses a static JSON file located at
        `data/static_news.json` relative to the project root. If the file is
        missing or malformed we fallback to an empty list, which results in no
        active news risk.
        """
        import json
        import os
        # Resolve path relative to project root (two levels up from this file)
        from pathlib import Path
        project_root = Path(__file__).resolve().parents[3]
        news_path = project_root / "data" / "static_news.json"
        try:
            with open(news_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                # If the JSON is an object with a 'news' key, extract it
                return data.get("news", [])
        except Exception:
            # Gracefully degrade – no static news
            return []

    def compute_context(self, symbol: str, timeframe: str, candles: List[NormalizedCandle]) -> MarketContext:
        """
        Computes the frozen market context for a given signal.
        """
        # In a real system, these would be calls to specific aether/core/context modules
        # For now, we implement the logic here or in helper methods

        directional = self._get_directional_state(candles)
        volatility = self._get_volatility_state(candles)
        session, transition = self._get_session_info(candles[-1].timestamp if candles else None)
        news = self._get_news_risk()
        liquidity = self._get_liquidity_state(session, volatility)
        structure = self._get_market_structure(candles)

        return MarketContext(
            directional_state=directional,
            volatility_state=volatility,
            session=session,
            session_transition=transition,
            news_risk=news,
            liquidity_state=liquidity,
            market_structure=structure
        )

    def _get_directional_state(self, candles: List[NormalizedCandle]) -> str:
        try:
            fast_period = max(2, int(self.config.get("context.directional_fast_period")))
            slow_period = max(fast_period + 1, int(self.config.get("context.directional_slow_period")))
            slope_period = max(1, int(self.config.get("context.directional_slope_period")))
        except Exception:
            fast_period, slow_period, slope_period = 20, 50, 5
        if len(candles) < slow_period + slope_period:
            return "Neutral"

        closes = [candle.close for candle in candles]
        fast_ema = self._ema(closes, fast_period)[-1]
        slow_ema = self._ema(closes, slow_period)
        previous_slow_ema = slow_ema[-slope_period - 1]
        current_slow_ema = slow_ema[-1]
        slope = current_slow_ema - previous_slow_ema
        if fast_ema > current_slow_ema and slope > 0:
            return "Bullish"
        if fast_ema < current_slow_ema and slope < 0:
            return "Bearish"
        return "Neutral"

    @staticmethod
    def _ema(values: List[float], period: int) -> List[float]:
        alpha = 2.0 / (period + 1.0)
        result = [float(values[0])]
        for value in values[1:]:
            result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
        return result

    def _get_volatility_state(self, candles: List[NormalizedCandle]) -> str:
        if len(candles) < 3:
            return "Normal"
        try:
            period = max(2, int(self.config.get("context.atr_period")))
        except Exception:
            period = 14
        try:
            thresholds = [float(value) for value in self.config.get("context.atr_percentiles")]
            if len(thresholds) < 3 or thresholds != sorted(thresholds):
                raise ValueError("invalid ATR percentile thresholds")
            low_threshold, normal_threshold, high_threshold = thresholds[:3]
        except Exception:
            low_threshold, normal_threshold, high_threshold = 20.0, 70.0, 90.0

        true_ranges = []
        for previous, current in zip(candles, candles[1:]):
            true_ranges.append(max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            ))
        if len(true_ranges) < period * 2:
            return "Normal"

        rolling_atr = []
        window_sum = sum(true_ranges[:period])
        rolling_atr.append(window_sum / period)
        for index in range(period, len(true_ranges)):
            window_sum += true_ranges[index] - true_ranges[index - period]
            rolling_atr.append(window_sum / period)
        current_atr = rolling_atr[-1]
        if max(rolling_atr) - min(rolling_atr) <= max(abs(current_atr), 1.0) * 1e-12:
            return "Normal"
        percentile = 100.0 * sum(value < current_atr for value in rolling_atr) / len(rolling_atr)
        if percentile < low_threshold:
            return "Low"
        if percentile < normal_threshold:
            return "Normal"
        if percentile < high_threshold:
            return "High"
        return "Extreme"

    def _get_session_info(self, timestamp: Optional[int] = None) -> tuple[str, bool]:
        now = (
            datetime.fromtimestamp(timestamp, timezone.utc)
            if timestamp is not None
            else datetime.now(timezone.utc)
        )
        hour = now.hour

        if now.weekday() >= 5:
            return "Weekend", False

        # Simplified session rules
        if 0 <= hour < 8:
            return "Asian", False
        elif 12 <= hour < 16:
            return "London-NY Overlap", False
        elif 8 <= hour < 12:
            return "London", False
        elif 13 <= hour < 17:
            return "NY", False
        else:
            return "Off Hours", False

    def _get_news_risk(self) -> NewsRisk:
        # Static check
        return NewsRisk(active=False, impact=None)

    def _get_liquidity_state(self, session: str, volatility: str) -> str:
        if session == "London-NY Overlap":
            return "High"
        if session == "Off Hours":
            return "Low"
        return "Medium"

    def _get_market_structure(self, candles: List[NormalizedCandle]) -> str:
        try:
            swing_order = max(1, int(self.config.get("context.swing_order")))
        except Exception:
            swing_order = 2
        if len(candles) < swing_order * 2 + 3:
            return "Consolidation"

        try:
            lookback = max(swing_order * 2 + 1, int(self.config.get("context.structure_lookback")))
        except Exception:
            lookback = 20
        if len(candles) > lookback:
            previous_range = candles[-lookback - 1:-1]
            last_close = candles[-1].close
            if last_close > max(candle.high for candle in previous_range):
                return "BOS"
            if last_close < min(candle.low for candle in previous_range):
                return "BOS"

        swing_highs = []
        swing_lows = []
        for index in range(swing_order, len(candles) - swing_order):
            high = candles[index].high
            low = candles[index].low
            neighbors = candles[index - swing_order:index + swing_order + 1]
            if all(high > item.high for offset, item in enumerate(neighbors) if offset != swing_order):
                swing_highs.append(high)
            if all(low < item.low for offset, item in enumerate(neighbors) if offset != swing_order):
                swing_lows.append(low)

        last_close = candles[-1].close
        latest_high = swing_highs[-1] if swing_highs else None
        latest_low = swing_lows[-1] if swing_lows else None
        if latest_high is not None and last_close > latest_high:
            return "BOS"
        if latest_low is not None and last_close < latest_low:
            return "BOS"

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            higher_highs = swing_highs[-1] > swing_highs[-2]
            higher_lows = swing_lows[-1] > swing_lows[-2]
            lower_highs = swing_highs[-1] < swing_highs[-2]
            lower_lows = swing_lows[-1] < swing_lows[-2]
            if higher_highs and higher_lows:
                return "Higher Highs"
            if lower_highs and lower_lows:
                return "Lower Lows"

        return "Consolidation"
