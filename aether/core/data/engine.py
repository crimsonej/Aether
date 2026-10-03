"""DataEngine lifecycle wrapper for the provider manager and market-data polling."""

from aether.core.data.provider_manager import DataProviderManager
from aether.core.utils.logger import logger


class DataEngine:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self._running = False
        self._provider_manager: DataProviderManager | None = None

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
        logger.info("[DataEngine] started")

    async def stop(self) -> None:
        self._running = False
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

__all__ = ["DataEngine"]
