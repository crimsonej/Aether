"""StreamingManager – fan out LLM token streams to outbound adapters.

The manager receives a prompt, asks ``ModelManager.generate`` for a token
iterator, and forwards each token to all registered sinks (Telegram,
WhatsApp, Dashboard).  Sinks are simple ``async def sink(token: str)``
coroutines.  Back‑pressure is handled by per‑sink bounded queues; when a
queue is full the oldest token is dropped and a warning is emitted.
"""

import asyncio
from typing import Callable, Awaitable, Dict, List

from ..model_manager import ModelManager


class StreamingManager:
    def __init__(self, model_manager: ModelManager):
        self.model = model_manager
        # Mapping from sink name to (queue, sink coroutine).
        self._sinks: Dict[str, asyncio.Queue] = {}
        self._sink_tasks: List[asyncio.Task] = []
        # Back‑pressure parameters.
        self._queue_maxsize = 200
        self._drop_warning_interval = 30  # seconds
        self._last_warning: Dict[str, float] = {}

    def register_sink(self, name: str, sink: Callable[[str], Awaitable[None]]) -> None:
        """Register a sink coroutine that will receive every token.
        ``name`` is used for identification in logs/metrics.
        """
        if name in self._sinks:
            raise ValueError(f"Sink '{name}' already registered")
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_maxsize)
        self._sinks[name] = q
        # Create a background task that consumes from the queue and calls the sink.
        async def _worker():
            while True:
                token = await q.get()
                try:
                    await sink(token)
                except Exception as exc:
                    print(f"[StreamingManager] sink '{name}' error: {exc}")
                finally:
                    q.task_done()
        task = asyncio.create_task(_worker(), name=f"stream_sink_{name}")
        self._sink_tasks.append(task)

    async def stream(self, prompt: str) -> None:
        """Run the prompt through ModelManager and distribute tokens.
        The method completes when the model stream ends.
        """
        async for token in self.model.generate(prompt):
            # Push token into each sink queue, dropping oldest if full.
            for name, q in self._sinks.items():
                try:
                    q.put_nowait(token)
                except asyncio.QueueFull:
                    # Drop the oldest item to make room.
                    try:
                        _ = q.get_nowait()
                        q.task_done()
                        q.put_nowait(token)
                        now = asyncio.get_event_loop().time()
                        last = self._last_warning.get(name, 0)
                        if now - last > self._drop_warning_interval:
                            print(f"[StreamingManager] back‑pressure: dropped token for sink '{name}'")
                            self._last_warning[name] = now
                    except Exception as exc:
                        print(f"[StreamingManager] failed to drop token for sink '{name}': {exc}")
        # Wait for all sink queues to be drained before returning.
        await asyncio.gather(*(q.join() for q in self._sinks.values()))

    async def shutdown(self) -> None:
        """Cancel all background sink workers."""
        for task in self._sink_tasks:
            task.cancel()
        await asyncio.gather(*self._sink_tasks, return_exceptions=True)
        self._sink_tasks.clear()
        self._sinks.clear()

__all__ = ["StreamingManager"]
