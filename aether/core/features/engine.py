from typing import Dict, List, Any
import asyncio
import math
from aether.core.features.base import Indicator, FeatureValue
from aether.core.data.normalizer import NormalizedCandle
from aether.core.utils.logger import logger

class IndicatorRegistry:
    """
    Manages available indicators and their configurations.
    """
    def __init__(self):
        self._indicators: Dict[str, Indicator] = {}

    def register(self, indicator: Indicator):
        self._indicators[indicator.name] = indicator
        logger.info("indicator_registered", name=indicator.name)

    def get_indicator(self, name: str) -> Indicator:
        if name not in self._indicators:
            raise KeyError(f"Indicator {name} not found in registry")
        return self._indicators[name]

    def list_enabled(self) -> List[str]:
        return list(self._indicators.keys())

class FeatureEngine:
    """
    Computes a set of indicators for the given market data.
    """
    def __init__(self, registry: IndicatorRegistry, config, bus=None, cache_max_entries: int = 1000):
        self.registry = registry
        self.config = config
        self.bus = bus
        self.cache = {} # (symbol, timeframe, timestamp) -> features
        self._cache_max_entries = max(1, cache_max_entries)
        self._candle_cache: Dict[str, List[NormalizedCandle]] = {}
        self._running = False
        self._task = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self.bus:
            await self.bus.subscribe("context.updated", self._on_context_updated)
            await self.bus.subscribe("data.candle", self._on_candle)
        self._task = asyncio.create_task(self._heartbeat())
        logger.info("[FeatureEngine] started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[FeatureEngine] stopped")

    async def health(self) -> dict:
        return {
            "status": "OK" if self._running else "STOPPED",
            "component": "feature_engine"
        }

    async def metrics(self) -> dict:
        return {}

    async def _on_context_updated(self, event: Dict[str, Any]):
        """Handle incoming context.updated events by computing features and publishing them."""
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            context_dict = event.get("context")
            if not symbol or not timeframe or not context_dict:
                return
            # Retrieve the rolling candles for this symbol/timeframe from cache.
            key = f"{symbol}:{timeframe}"
            candles = self._candle_cache.get(key, [])
            timestamp = event.get("timestamp")
            if timestamp is not None:
                candles = [candle for candle in candles if candle.timestamp <= int(timestamp)]
            if not candles:
                logger.warning("[FeatureEngine] missing candle data for feature computation; publishing empty features")
                features = {}
            else:
                # Compute features using the cached candles list.
                features = self.compute_features(symbol, timeframe, candles)
            if self.bus:
                await self.bus.publish("features.calculated", {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "timestamp": timestamp,
                    "features": features
                })
        except Exception as e:
            logger.exception("[FeatureEngine] error in _on_context_updated: %s", e)

    async def _heartbeat(self) -> None:
        while self._running:
            await asyncio.sleep(30)

    async def _on_candle(self, event: Dict[str, Any]):
        """Cache incoming candle data for feature computation."""
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
                if candle is not None:
                    self.cache.pop((symbol, timeframe, candle.timestamp), None)
        except Exception as e:
            logger.exception("[FeatureEngine] error in _on_candle: %s", e)

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
            logger.warning("[FeatureEngine] rejected malformed candle history for %s", key)
            return None
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if (
            not all(math.isfinite(value) for value in values)
            or candle.volume < 0
            or candle.low > min(candle.open, candle.close)
            or candle.high < max(candle.open, candle.close)
            or candle.low > candle.high
        ):
            logger.warning("[FeatureEngine] rejected invalid candle for %s", key)
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

    def compute_features(self, symbol: str, timeframe: str, candles: list) -> Dict[str, FeatureValue]:
        """Compute feature values for given candles using enabled indicators.
        Results are cached per (symbol, timeframe, latest_timestamp).
        This implementation standardises on `ConfigStore.get(path)` for runtime
        configuration access; if the provided config object lacks `get` we fall
        back to legacy attribute access for tests.
        """
        timestamp = candles[-1].timestamp if candles else 0
        cache_key = (symbol, timeframe, timestamp)
        if cache_key in self.cache:
            return self.cache[cache_key]
        features: Dict[str, FeatureValue] = {}
        # Get enabled indicators from standardized config API
        enabled_indicators = []
        try:
            enabled_indicators = self.config.get("features.enabled")
        except Exception:
            enabled_indicators = None
        # If the standardized getter returned None (or is not present), fall back
        # to legacy attribute access for backwards compatibility in tests.
        if enabled_indicators is None:
            enabled_indicators = getattr(getattr(self.config, "features", None), "enabled", []) or []
        if not isinstance(enabled_indicators, list):
            enabled_indicators = []
        for name in enabled_indicators:
            try:
                indicator = self.registry.get_indicator(name)
                features[name] = indicator.compute(candles)
            except Exception as e:
                logger.error("feature_computation_failed", indicator=name, error=str(e))
        self.cache[cache_key] = features
        while len(self.cache) > self._cache_max_entries:
            self.cache.pop(next(iter(self.cache)))
        return features
