"""Subsystem utilities – generic wrapper for existing components.

The platform expects every subsystem to expose ``async start()``, ``async stop()``,
``async health()`` and ``async metrics()`` methods (the ``IHealth`` contract).
Many existing engine classes (e.g., ``ContextEngine``) only provide business
logic and lack lifecycle hooks.  ``SubsystemAdapter`` decorates such objects so
they can be managed by ``ServiceManager`` without modifying the original
implementations.
"""

import asyncio
from typing import Any


class SubsystemAdapter:
    """Wrap an arbitrary object to provide the required lifecycle interface.

    If the wrapped object already defines ``start``/``stop``/``health``/``metrics``
    they are delegated to; otherwise default no‑op implementations are used.
    """

    def __init__(self, name: str, instance: Any):
        self.name = name
        self.instance = instance
        self.state = "stopped"

    async def start(self) -> None:
        self.state = "running"
        if hasattr(self.instance, "start"):
            result = self.instance.start()
            if asyncio.iscoroutine(result):
                await result

    async def stop(self) -> None:
        self.state = "stopped"
        if hasattr(self.instance, "stop"):
            result = self.instance.stop()
            if asyncio.iscoroutine(result):
                await result

    async def health(self) -> dict:
        if hasattr(self.instance, "health"):
            result = self.instance.health()
            if asyncio.iscoroutine(result):
                return await result
            return result
        # Default healthy status.
        return {"status": "OK", "component": self.name}

    async def metrics(self) -> dict:
        if hasattr(self.instance, "metrics"):
            result = self.instance.metrics()
            if asyncio.iscoroutine(result):
                return await result
            return result
        return {}

    def __repr__(self) -> str:
        return f"<SubsystemAdapter {self.name} state={self.state}>"

__all__ = ["SubsystemAdapter"]
