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
            # For simplicity, we just compute context for the candle's symbol and timeframe.
            # In a full implementation we would keep a rolling window of candles.
            symbol = event.get("symbol")
            timeframe = event.get("timeframe")
            if not symbol or not timeframe or event.get("is_closed") is not True:
                return
            from aether.core.data.normalizer import NormalizedCandle
            candle = NormalizedCandle(
                symbol=symbol,
                timeframe=timeframe,
                open=event["open"],
                high=event["high"],
                low=event["low"],
                close=event["close"],
                volume=event.get("volume", 0.0),
                timestamp=event["timestamp"],
                close_time=event.get("close_time", event["timestamp"]),
                source=event.get("source") or "unknown",
                is_closed=True,
            )
            values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
            if (
                not all(math.isfinite(value) for value in values)
                or candle.volume < 0
                or candle.low > min(candle.open, candle.close)
                or candle.high < max(candle.open, candle.close)
                or candle.low > candle.high
            ):
                logger.warning("[ContextEngine] rejected invalid candle for %s %s", symbol, timeframe)
                return
            key = f"{symbol}:{timeframe}"
            history = self._candle_cache.setdefault(key, [])
            if history and candle.timestamp < history[-1].timestamp:
                logger.warning("[ContextEngine] rejected out-of-order candle for %s %s", symbol, timeframe)
                return
            if history and candle.timestamp == history[-1].timestamp:
                history[-1] = candle
            else:
                history.append(candle)
            if len(history) > 300:
                del history[:-300]

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
                    "context": context.__dict__  # or we could define a serialization method
                })
        except Exception as e:
            logger.exception("[ContextEngine] error processing candle: %s", e)

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
        # Simplified: Check last candle close vs EMA (mocked here)
        if not candles: return "Neutral"
        # Just a mock for now: if close > open, Bullish
        last = candles[-1]
        return "Bullish" if last.close > last.open else "Bearish"

    def _get_volatility_state(self, candles: List[NormalizedCandle]) -> str:
        # Simplified: Use ATR percentile logic (mocked)
        return "Normal"

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
        # Simplified: check for higher highs (mocked)
        if len(candles) < 2: return "Consolidation"
        return "Higher Highs" if candles[-1].high > candles[-2].high else "Lower Lows"
