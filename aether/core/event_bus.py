"""EventBus – lightweight async publish/subscribe system.

The bus supports hierarchical topics (dot notation) and wildcard
subscriptions using the ``*`` suffix (e.g., ``delivery.*``).  Each subscriber gets
its own asyncio.Queue to avoid head‑of‑line blocking.  The implementation is
intentionally minimal but includes back‑pressure handling and basic metrics.
"""

import asyncio
from collections import defaultdict
from typing import Callable, Awaitable, Any, Dict, Set, List


class Subscriber:
    def __init__(self, handler: Callable[[Any], Awaitable[None]], maxsize: int = 100):
        self.handler = handler
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.task: asyncio.Task | None = None

    async def _worker(self) -> None:
        while True:
            payload = await self.queue.get()
            try:
                await self.handler(payload)
            except Exception as exc:
                # In production we would log via the central logger.
                print(f"[EventBus] subscriber handler error: {exc}")
            finally:
                self.queue.task_done()

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._worker())

    def stop(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()


class EventBus:
    def __init__(self):
        # Mapping from exact topic -> set of Subscriber objects.
        self._subscribers: Dict[str, Set[Subscriber]] = defaultdict(set)
        # Wildcard subscriptions (topic prefix ending with '*').
        self._wildcards: List[tuple[str, Subscriber]] = []
        self._running = False

    async def start(self) -> None:
        self._running = True
        # Start background workers for all existing subscribers.
        for subs in self._subscribers.values():
            for sub in subs:
                sub.start()
        for _, sub in self._wildcards:
            sub.start()

    async def stop(self) -> None:
        self._running = False
        # Cancel all subscriber tasks.
        for subs in self._subscribers.values():
            for sub in subs:
                sub.stop()
        for _, sub in self._wildcards:
            sub.stop()

    async def publish(self, topic: str, payload: Any) -> None:
        """Publish an event to a topic.
        The method delivers the payload to all exact-match subscribers and to any
        wildcard subscriber whose pattern matches the topic.
        """
        if not self._running:
            raise RuntimeError("EventBus is not started")
        # Exact matches.
        for subscriber in self._subscribers.get(topic, set()):
            await self._enqueue(subscriber, payload)
        # Wildcard matches.
        for prefix, subscriber in self._wildcards:
            if topic.startswith(prefix):
                await self._enqueue(subscriber, payload)

    async def _enqueue(self, subscriber: Subscriber, payload: Any) -> None:
        try:
            subscriber.queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the oldest item to make room (simple back‑pressure policy).
            try:
                _ = subscriber.queue.get_nowait()
                subscriber.queue.put_nowait(payload)
                print(f"[EventBus] back‑pressure: dropped oldest event for subscriber {subscriber.handler}")
            except Exception as exc:
                print(f"[EventBus] failed to enqueue payload after drop: {exc}")

    async def subscribe(self, topic: str, handler: Callable[[Any], Awaitable[None]], maxsize: int = 0) -> None:
        """Subscribe a coroutine handler to a topic.
        ``topic`` may end with ``*`` to indicate a wildcard prefix (e.g.,
        ``delivery.*``).  The handler receives the *payload* only (topic is known
        from the subscription context).

        ``maxsize`` of 0 (default) means unbounded — no events are dropped.
        Set a positive value to apply FIFO back-pressure (drop oldest).
        """
        if maxsize is None or maxsize <= 0:
            maxsize = 0
        subscriber = Subscriber(handler, maxsize=maxsize)
        if topic.endswith("*"):
            prefix = topic[:-1]
            self._wildcards.append((prefix, subscriber))
        else:
            self._subscribers[topic].add(subscriber)
        # Start the worker immediately if the bus is already running.
        if self._running:
            subscriber.start()

    def unsubscribe(self, topic: str, handler: Callable[[Any], Awaitable[None]]) -> None:
        """Remove a previously registered handler from a topic."""
        # Exact match removal.
        if not topic.endswith("*"):
            subs = self._subscribers.get(topic)
            if not subs:
                return
            to_remove = [s for s in subs if s.handler == handler]
            for s in to_remove:
                subs.discard(s)
                s.stop()
            if not subs:
                del self._subscribers[topic]
        else:
            prefix = topic[:-1]
            self._wildcards = [(p, s) for p, s in self._wildcards if not (p == prefix and s.handler == handler)]

    # ---------------------------------------------------------------------
    # Simple metrics helpers (used by MetricsExporter)
    # ---------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """Return basic statistics about the bus (queue lengths, subscriber counts)."""
        stats: Dict[str, Any] = {}
        for topic, subs in self._subscribers.items():
            stats[topic] = {
                "subscriber_count": len(subs),
                "queue_lengths": [sub.queue.qsize() for sub in subs],
            }
        for prefix, sub in self._wildcards:
            stats[f"{prefix}*"] = {
                "subscriber_count": 1,
                "queue_length": sub.queue.qsize(),
            }
        return stats

__all__ = ["EventBus"]
