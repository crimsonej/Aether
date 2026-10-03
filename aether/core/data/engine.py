"""DataEngine – periodically fetches market data and publishes normalized candle events.

This engine is provider-agnostic and relies on DataProviderManager for all market
data access. The engine fetches candles for configured watchlist symbols and
publishes normalized ``data.candle`` events.
"""

import asyncio
from typing import List, Dict, Any
from aether.core.data.normalizer import NormalizedCandle
from aether.core.data.provider_manager import DataProviderManager
from aether.core.utils.logger import logger


class DataEngine:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self._task: asyncio.Task | None = None
        self._running = False
        self._provider_manager: DataProviderManager | None = None
        self._interval = 60  # seconds

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._provider_manager = DataProviderManager(self.config, self.bus)
        try:
            await self._provider_manager.start()
        except Exception as e:
            logger.error("[DataEngine] failed to start provider manager: %s", e)
            self._provider_manager = None
        self._task = asyncio.create_task(self._loop())
        logger.info("[DataEngine] started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._provider_manager:
            await self._provider_manager.stop()
            self._provider_manager = None
        logger.info("[DataEngine] stopped")

    async def health(self) -> dict:
        provider_state = "initialized" if self._provider_manager else "missing"
        return {
            "status": "OK" if self._running else "STOPPED",
            "component": "data_engine",
            "details": {"provider_manager": provider_state}
        }

    async def metrics(self) -> dict:
        return {}

    async def _loop(self) -> None:
        while self._running:
            await self._fetch_and_publish()
            await asyncio.sleep(self._interval)

    async def _fetch_and_publish(self) -> None:
        if not self._provider_manager:
            logger.warning("[DataEngine] no provider manager available; skipping fetch")
            return
        try:
            watchlist = self.config.get("watchlist")
            if not watchlist:
                logger.warning("[DataEngine] watchlist empty")
                return
            timeframes = self.config.get("data.timeframes") if self.config.get("data.timeframes") else ["1h"]
            for symbol in watchlist:
                for timeframe in timeframes:
                    try:
                        candles: List[NormalizedCandle] = await self._provider_manager.get_candles(symbol, timeframe, count=1)
                    except Exception as exc:
                        # Rate-limit / outage on a single provider — the
                        # provider manager already logs and marks the
                        # provider failed; we just move on.
                        logger.warning("[DataEngine] %s %s candles unavailable: %s",
                                       symbol, timeframe, exc)
                        continue
                    if not candles:
                        logger.warning("[DataEngine] no candles returned for %s %s", symbol, timeframe)
                        continue
                    candle = candles[0]
                    event = {
                        "symbol": candle.symbol,
                        "timeframe": candle.timeframe,
                        "open": candle.open,
                        "high": candle.high,
                        "low": candle.low,
                        "close": candle.close,
                        "volume": candle.volume,
                        "timestamp": candle.timestamp,
                        "source": candle.source,
                        "is_closed": candle.is_closed,
                    }
                    await self.bus.publish("data.candle", event)
                    logger.debug("[DataEngine] published candle for %s %s", symbol, timeframe)
        except Exception as e:
            logger.warning("[DataEngine] fetch loop non-fatal error: %s", e)


__all__ = ["DataEngine"]
