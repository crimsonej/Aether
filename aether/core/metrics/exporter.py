"""MetricsExporter – registers Prometheus metrics and provides /metrics endpoint.

The exporter is instantiated by the ServiceManager and given references to the
EventBus and ConfigStore.  Subsystems register their own counters/gauges using the
helper methods.  The FastAPI route is added to the gateway server (outside of
this module).
"""

import asyncio
from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest
from prometheus_client import CONTENT_TYPE_LATEST

from ..event_bus import EventBus


class MetricsExporter:
    def __init__(self, config, bus: EventBus):
        self.config = config
        self.bus = bus
        self.registry = CollectorRegistry()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        # Example: register a gauge for active asyncio tasks.
        self.task_gauge = Gauge("aether_active_tasks", "Number of active asyncio tasks", registry=self.registry)
        # Periodically update task gauge.
        self._task: asyncio.Task | None = None

    # ---------------------------------------------------------------------
    # Helper registration methods used by subsystems.
    # ---------------------------------------------------------------------
    def register_counter(self, name: str, documentation: str, labelnames: list[str] = None) -> Counter:
        c = Counter(name, documentation, labelnames=labelnames or [], registry=self.registry)
        self._counters[name] = c
        return c

    def register_gauge(self, name: str, documentation: str, labelnames: list[str] = None) -> Gauge:
        g = Gauge(name, documentation, labelnames=labelnames or [], registry=self.registry)
        self._gauges[name] = g
        return g

    def register_histogram(self, name: str, documentation: str, labelnames: list[str] = None, buckets: list[float] = None) -> Histogram:
        h = Histogram(name, documentation, labelnames=labelnames or [], buckets=buckets, registry=self.registry)
        self._histograms[name] = h
        return h

    # ---------------------------------------------------------------------
    # FastAPI endpoint helper – to be mounted by the gateway router.
    # ---------------------------------------------------------------------
    async def metrics_endpoint(self):
        # Update the task gauge before responding.
        self.task_gauge.set(len(asyncio.all_tasks()))
        return generate_latest(self.registry), CONTENT_TYPE_LATEST

    # ---------------------------------------------------------------------
    # Background task to periodically push custom metrics (optional).
    # ---------------------------------------------------------------------
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._periodic())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _periodic(self) -> None:
        while True:
            # Example placeholder: could collect custom runtime stats here.
            await asyncio.sleep(30)

__all__ = ["MetricsExporter"]
