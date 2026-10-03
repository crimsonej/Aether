"""DeliveryManager – subscribes to signal events and sends them via enabled adapters.

The manager listens for ``delivery.signal_ready`` events (published by the
SignalEngine). For each signal it attempts to send via all adapters obtained
from the AdapterRegistry. Failed deliveries are handed off to the persistent
RetryQueue for exponential backoff retries.
"""

import asyncio
from typing import Dict, Any

from aether.core.utils.logger import logger

# These will be injected via `setup` method called by ServiceManager after
# the AdapterRegistry and RetryQueue are instantiated.
class DeliveryManager:
    def __init__(self, config, bus):
        self.config = config
        self.bus = bus
        self._adapter_registry = None  # type: ignore
        self._retry_queue = None       # type: ignore
        self._subscribed = False

    async def setup(self, adapter_registry, retry_queue):
        """Called by ServiceManager after dependencies are ready."""
        self._adapter_registry = adapter_registry
        self._retry_queue = retry_queue

    async def register_events(self, bus):
        """Register our event handlers on the provided EventBus."""
        await bus.subscribe("signal.generated", self._handle_signal)
        await bus.subscribe("signal.activated", self._handle_signal)
        await bus.subscribe("signal.missed", self._handle_signal)
        await bus.subscribe("signal.closed", self._handle_signal)
        await bus.subscribe("signal.expired", self._handle_signal)
        logger.info("[DeliveryManager] subscribed to signal lifecycle events")

    async def _handle_signal(self, event: Dict[str, Any]):
        """Process a signal event: try to send via all adapters, enqueue failures."""
        signal = event  # the payload is the signal dict
        if not self._adapter_registry or not self._retry_queue:
            logger.error("[DeliveryManager] dependencies not set up")
            return
        adapter_names = self._adapter_registry.list_enabled()
        if not adapter_names:
            logger.warning("[DeliveryManager] no adapters enabled")
            return
        for name in adapter_names:
            try:
                adapter = self._adapter_registry.get(name)
                success = await adapter.send_signal(signal)
                if success:
                    logger.info("signal_delivered", platform=name, signal_id=signal.get("signal_id"))
                else:
                    await self._retry_queue.enqueue(name, signal)
                    logger.warning(
                        "signal_send_failed",
                        platform=name,
                        signal_id=signal.get("signal_id"),
                    )
            except Exception as exc:
                logger.exception(
                    "signal_send_error",
                    platform=name,
                    signal_id=signal.get("signal_id"),
                    error=str(exc),
                )
                await self._retry_queue.enqueue(name, signal)

