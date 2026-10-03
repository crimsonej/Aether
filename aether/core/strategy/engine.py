import asyncio
import hashlib
import json
import math
from typing import Dict, List, Any, Optional
from aether.core.strategy.base import Strategy
from aether.core.strategy.trend_following import TrendFollowingV1
from aether.core.context.models import MarketContext
from aether.core.features.base import FeatureValue
from aether.core.utils.logger import logger
from aether.core.data.normalizer import NormalizedCandle

class StrategyRegistry:
    def __init__(self):
        self._strategies: Dict[str, Strategy] = {}

    def register(self, strategy: Strategy):
        self._strategies[strategy.name] = strategy

    def get_strategy(self, name: str) -> Optional[Strategy]:
        return self._strategies.get(name)

    def get_enabled_strategies(self, config) -> List[Strategy]:
        # Prefer runtime ConfigStore API: config.get("strategies").
        # Fall back to attribute-style access for legacy objects used in tests.
        strategies_cfg = None
        try:
            strategies_cfg = config.get("strategies")
        except (AttributeError, KeyError, TypeError):
            strategies_cfg = getattr(config, "strategies", None)
        if strategies_cfg is None:
            strategies_cfg = getattr(config, "strategies", None)

        if strategies_cfg is None:
            return []

        enabled_names = []
        for s in strategies_cfg:
            if isinstance(s, dict):
                name = s.get("name")
                enabled = s.get("enabled", False)
            else:
                name = getattr(s, "name", None)
                enabled = getattr(s, "enabled", False)
            if enabled and name:
                enabled_names.append(name)

        return [self._strategies[name] for name in enabled_names if name in self._strategies]

class StrategyEngine:
    """
    Orchestrates the signal generation pipeline.
    Subscribes to context.updated and features.calculated events, combines them,
    and emits signal.emitted events when a signal is generated.
    """
    def __init__(self, registry: StrategyRegistry, config):
        self.registry = registry
        self.config = config
        self._running = False
        self._bus = None
        self._candle_cache: Dict[str, NormalizedCandle] = {}
        self._candle_history: Dict[str, Dict[int, NormalizedCandle]] = {}
        # Cache for latest context and features per (symbol, timeframe)
        self._context_cache: Dict[str, Dict[str, Any]] = {}  # key: f"{symbol}:{timeframe}"
        self._features_cache: Dict[str, Dict[str, Any]] = {}
        self._context_timestamps: Dict[str, int] = {}
        self._feature_timestamps: Dict[str, int] = {}
        from aether.core.strategy.validation import ValidationFirewall
        self.firewall = ValidationFirewall(config)
        # Track market-closed keys to suppress signal generation
        self._market_closed: set[str] = set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # Note: bus will be set via register_events (called by ServiceManager after bus is ready)
        logger.info("[StrategyEngine] started")

    async def stop(self) -> None:
        self._running = False
        logger.info("[StrategyEngine] stopped")

    async def health(self) -> dict:
        return {
            "status": "OK" if self._running else "STOPPED",
            "component": "strategy_engine"
        }

    async def metrics(self) -> dict:
        return {}

    async def register_events(self, bus):
        """Called by ServiceManager to set the event bus and subscribe to topics."""
        self._bus = bus
        await bus.subscribe("context.updated", self._on_context_updated)
        await bus.subscribe("data.candle", self._on_candle)
        await bus.subscribe("features.calculated", self._on_features_calculated)
        await bus.subscribe("market.closed", self._on_market_closed)
        await bus.subscribe("market.opened", self._on_market_opened)
        await bus.subscribe("market.low_liquidity", self._on_market_low_liquidity)
        logger.info("[StrategyEngine] subscribed to context.updated, data.candle, and features.calculated")

    async def _on_market_closed(self, event: Dict[str, Any]):
        """Mark the (symbol:timeframe) key as closed and publish suppression event."""
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            if not symbol or not timeframe:
                return
            key = self._make_key(symbol, timeframe)
            self._market_closed.add(key)
            # publish signal.suppressed with next open time if provided
            payload = {"symbol": symbol, "timeframe": timeframe, "reason": "market_closed"}
            if event.get("next_open_time"):
                payload["next_open_time"] = event.get("next_open_time")
            if self._bus:
                await self._bus.publish("signal.suppressed", payload)
        except Exception as e:
            logger.exception("[StrategyEngine] error in _on_market_closed: %s", e)

    async def _on_market_opened(self, event: Dict[str, Any]):
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            if not symbol or not timeframe:
                return
            key = self._make_key(symbol, timeframe)
            if key in self._market_closed:
                self._market_closed.discard(key)
        except Exception as e:
            logger.exception("[StrategyEngine] error in _on_market_opened: %s", e)

    async def _on_market_low_liquidity(self, event: Dict[str, Any]):
        try:
            # For now just log; strategies may optionally use this signal
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            logger.warning("[StrategyEngine] low liquidity detected for %s %s", symbol, timeframe)
        except Exception as e:
            logger.exception("[StrategyEngine] error in _on_market_low_liquidity: %s", e)

    def _make_key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    def _snapshots_match(self, key: str) -> bool:
        context_timestamp = self._context_timestamps.get(key)
        feature_timestamp = self._feature_timestamps.get(key)
        if context_timestamp is None and feature_timestamp is None:
            return True
        return context_timestamp == feature_timestamp

    @staticmethod
    def _signal_identity(symbol: str, timeframe: str, snapshot: Dict[str, Any]):
        snapshot_bytes = json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        snapshot_hash = hashlib.sha256(snapshot_bytes).hexdigest()
        signal_id = f"{symbol}_{timeframe}_{snapshot_hash[:20]}"
        return signal_id, snapshot_hash

    async def _on_context_updated(self, event: Dict[str, Any]):
        """Handle incoming context.updated events."""
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            context_dict = event.get("context")
            if not symbol or not timeframe or not context_dict:
                return
            key = self._make_key(symbol, timeframe)
            self._context_cache[key] = context_dict
            if event.get("timestamp") is not None:
                self._context_timestamps[key] = int(event["timestamp"])
            # If we have features for this key, try to generate a signal.
            if key in self._features_cache and self._snapshots_match(key):
                await self._maybe_generate_signal(symbol, timeframe)
        except Exception as e:
            logger.exception("[StrategyEngine] error in _on_context_updated: %s", e)

    async def _on_features_calculated(self, event: Dict[str, Any]):
        # Existing implementation unchanged
        """Handle incoming features.calculated events."""
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            features_dict = event.get("features")
            if not symbol or not timeframe or not features_dict:
                return
            key = self._make_key(symbol, timeframe)
            self._features_cache[key] = features_dict
            if event.get("timestamp") is not None:
                self._feature_timestamps[key] = int(event["timestamp"])
            # If we have context for this key, try to generate a signal.
            if key in self._context_cache and self._snapshots_match(key):
                await self._maybe_generate_signal(symbol, timeframe)
        except Exception as e:
            logger.exception("[StrategyEngine] error in _on_features_calculated: %s", e)

    async def _on_candle(self, event: Dict[str, Any]):
        """Handle incoming data.candle events to cache the last candle per symbol/timeframe."""
        try:
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            if not symbol or not timeframe or event.get("is_closed") is not True:
                return
            candle = NormalizedCandle(
                symbol=symbol,
                timeframe=timeframe,
                open=event.get("open"),
                high=event.get("high"),
                low=event.get("low"),
                close=event.get("close"),
                volume=event.get("volume", 0.0),
                timestamp=event.get("timestamp"),
                close_time=event.get("close_time", event.get("timestamp")),
                source=event.get("source") or "unknown",
                is_closed=True,
            )
            key = self._make_key(symbol, timeframe)
            prices = (candle.open, candle.high, candle.low, candle.close, candle.volume)
            if (
                not all(math.isfinite(value) for value in prices)
                or candle.volume < 0
                or candle.low > min(candle.open, candle.close)
                or candle.high < max(candle.open, candle.close)
                or candle.low > candle.high
            ):
                logger.warning("[StrategyEngine] rejected invalid candle for %s %s", symbol, timeframe)
                return
            previous = self._candle_cache.get(key)
            if previous is not None and candle.timestamp < previous.timestamp:
                logger.warning("[StrategyEngine] rejected out-of-order candle for %s %s", symbol, timeframe)
                return
            self._candle_cache[key] = candle
            history = self._candle_history.setdefault(key, {})
            history[candle.timestamp] = candle
            if len(history) > 300:
                oldest_timestamp = min(history)
                del history[oldest_timestamp]
        except Exception as e:
            logger.exception("[StrategyEngine] error in _on_candle: %s", e)

    async def _maybe_generate_signal(self, symbol: str, timeframe: str):
        """If we have both context and features, run the strategy and emit a signal."""
        key = self._make_key(symbol, timeframe)
        # Respect market-closed state: do not generate signals while closed
        if key in self._market_closed:
            logger.info("[StrategyEngine] suppressing signal generation for closed market %s", key)
            return
        context_dict = self._context_cache.get(key)
        features_dict = self._features_cache.get(key)
        if context_dict is None or features_dict is None:
            return
        # We need to reconstruct the objects as expected by the strategy.
        # For simplicity, we'll assume the cached dicts can be used directly.
        # In a more robust system, we would have proper DTOs.
        from aether.core.context.models import MarketContext
        from aether.core.features.base import FeatureValue
        # Rebuild FeatureValue objects if needed; for now we assume the dicts are compatible.
        context = MarketContext(**context_dict)
        features = {k: FeatureValue(**v) if isinstance(v, dict) else v for k, v in features_dict.items()}
        # Attempt to retrieve the latest candle for this symbol/timeframe.
        context_timestamp = self._context_timestamps.get(key)
        candle = (
            self._candle_history.get(key, {}).get(context_timestamp)
            if context_timestamp is not None
            else self._candle_cache.get(key)
        )
        if candle is None:
            logger.warning("[StrategyEngine] missing candle data for signal generation; skipping")
            return
        if context_timestamp is not None and candle.timestamp != context_timestamp:
            logger.warning("[StrategyEngine] candle snapshot mismatch for %s %s", symbol, timeframe)
            return
        # Generate a candidate using the available candle.
        candidate = self.generate_candidate(symbol, timeframe, context, features, candle)
        if not candidate:
            logger.info("[StrategyEngine] no candidate generated for %s %s", symbol, timeframe)
            return
        
        # Validate candidate via firewall
        validation = self.firewall.validate(candidate)
        if not validation.get("valid", False):
            logger.warning("[StrategyEngine] candidate failed validation: %s", validation.get("reason"))
            return

        if self._bus:
            # The signal payload is consumed by:
            #   * SignalStateManager._on_signal_emitted (reads ``trade``)
            #   * DeliveryManager / TelegramAdapter (read ``trade_construction``)
            # We populate both keys so a downstream rename doesn't break the
            # other consumer.
            trade = candidate["trade"]
            context_snapshot = context.model_dump(mode="json")
            feature_snapshot = {
                name: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                for name, value in sorted(features.items())
            }
            snapshot = {
                "candle": candle.model_dump(mode="json"),
                "context": context_snapshot,
                "features": feature_snapshot,
                "strategy": {
                    "name": candidate["strategy"].name,
                    "version": candidate["strategy"].version,
                    "config": candidate["strategy"].config,
                },
                "direction": candidate["direction"],
                "trade": trade,
            }
            signal_id, snapshot_hash = self._signal_identity(symbol, timeframe, snapshot)
            reason_tags = [f"direction_{candidate['direction'].lower()}"]
            if context.market_structure:
                reason_tags.append(f"structure_{context.market_structure.lower().replace(' ', '_')}")
            if features.get("ema200") and features["ema200"].is_valid:
                reason_tags.append("ema200_available")
            if features.get("adx") and features["adx"].is_valid and features["adx"].value > 25:
                reason_tags.append("adx_strength")
            if features.get("rsi") and features["rsi"].is_valid and 40 < features["rsi"].value < 60:
                reason_tags.append("rsi_confirmation")
            signal = {
                "schema_version": "1.0",
                "signal_id": signal_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": candidate["direction"],
                "strategy_name": candidate.get("strategy_name", "unknown"),
                "strategy_version": candidate["strategy"].version,
                "timestamp": candle.close_time,
                "source_candle_timestamp": candle.timestamp,
                "context_snapshot": context_snapshot,
                "feature_snapshot": feature_snapshot,
                "market_snapshot_hash": snapshot_hash,
                "confidence": {
                    "raw": int(candidate["score"]),
                    "adjusted": int(validation.get("adjusted_score", candidate["score"])),
                },
                "validation": validation,
                "reason_tags": reason_tags,
                "trade": trade,
                "trade_construction": trade,
                "entry_price": trade.get("entry", {}).get("price"),
                "stop_loss_price": trade.get("stop_loss", {}).get("price"),
                "take_profit_price": trade.get("take_profit", {}).get("price"),
                "expiry": trade.get("expiry"),
            }
            await self._bus.publish("signal.emitted", signal)
            logger.info("[StrategyEngine] emitted signal: %s", signal["signal_id"])

        # The following code would be used if we had a last candle:
        # last_candle = NormalizedCandle(...)  # we would need to get this from somewhere
        # candidate = self.generate_candidate(symbol, timeframe, context, features, last_candle)
        # if candidate and self._bus:
        #     signal = {
        #         "signal_id": f"{symbol}_{timeframe}_{int(asyncio.get_event_loop().time())}",
        #         "symbol": symbol,
        #         "direction": candidate["direction"],
        #         "confidence": {"adjusted": int(candidate["score"])},
        #         "reason_tags": [],
        #         "trade_construction": candidate["trade"]
        #     }
        #     await self._bus.publish("signal.emitted", signal)
        #     logger.info("[StrategyEngine] emitted signal: %s", signal["signal_id"])

    def generate_candidate(self, symbol: str, timeframe: str,
                           context: MarketContext, features: Dict[str, FeatureValue],
                           last_candle: NormalizedCandle) -> Optional[Dict[str, Any]]:

        strategies = self.registry.get_enabled_strategies(self.config)

        best_signal = None
        max_score = -1.0

        for strategy in strategies:
            atr_feature = features.get("atr")
            if (
                atr_feature is None
                or not atr_feature.is_valid
                or not math.isfinite(atr_feature.value)
                or atr_feature.value <= 0
            ):
                continue
            direction = strategy.detect_direction(context, features)
            if direction == "NEUTRAL":
                continue

            score = strategy.score(context, features)

            if score > max_score:
                max_score = score
                best_signal = {
                    "strategy": strategy,
                    "strategy_name": strategy.name,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "direction": direction,
                    "score": score,
                    "context": context,
                    "trade": strategy.construct_trade(symbol, timeframe, direction, context, features, last_candle)
                }

        return best_signal
