"""ServiceManager – central orchestrator for the Aether platform.

The manager is responsible for:
  * Loading the ConfigStore and EventBus.
  * Instantiating every subsystem (data layer, context engine, etc.).
  * Wiring each subsystem to the EventBus (publish/subscribe).
  * Providing async ``start()``, ``stop()`` and ``reload()`` methods.
  * Publishing high‑level lifecycle events (system_started, system_stopped,
    config_reload_success, config_reload_failed).

It deliberately contains no business logic; that lives in the individual
subsystems.  The ServiceManager only coordinates their lifecycles and
ensures a clean shutdown.
"""

import asyncio
import pathlib
import yaml
from typing import Dict, List, Callable, Awaitable, Any

from .config.store import ConfigStore
from .subsystem import SubsystemAdapter
from .event_bus import EventBus
from .utils.logger import logger

# NOTE: Subsystem imports are deliberately placed *inside* the ``_load_subsystems``
# method to avoid circular import problems when those subsystems import the
# manager for type hints.


class ServiceManager:
    def __init__(self, project_root: pathlib.Path | None = None):
        self.project_root = project_root or pathlib.Path(__file__).resolve().parents[2]
        self.config = ConfigStore(self.project_root)
        self.bus = EventBus()
        self._subsystems: Dict[str, Any] = {}
        self._startup_tasks: List[asyncio.Task] = []
        self._running = False
        self._old_config: Dict[str, Any] = {}
        # Keep a reference to the background health poller so we can cancel it.
        self._health_task: asyncio.Task | None = None

    # ---------------------------------------------------------------------
    # Public lifecycle API
    # ---------------------------------------------------------------------
    async def start(self) -> None:
        """Start the entire platform.

        Steps performed:
            1. Load configuration from disk.
            2. Initialise the EventBus.
            3. Dynamically discover and instantiate subsystems.
            4. Start all subsystems (so that dependencies like AdapterRegistry and RetryQueue are running).
            5. Wire subsystem event handlers and perform post-start setup.
            6. Emit a global ``system_started`` event.
        """
        if self._running:
            return
        await self.config.load()
        self._old_config = dict(self.config._data)
        await self.bus.start()
        await self._load_subsystems()
        await self._start_subsystems()
        await self._wire_subsystems()
        self._running = True
        await self.bus.publish("system_started", {"msg": "Aether platform started"})

    async def stop(self) -> None:
        """Stop all subsystems and clean up resources.
        The method is idempotent – calling ``stop`` multiple times is safe.
        """
        if not self._running:
            return
        # Emit a shutdown request so subsystems can gracefully terminate.
        await self.bus.publish("shutdown_requested", {})
        # Cancel the background health task if it exists.
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        # Close subsystems in reverse start order (best effort).
        close_coros = [sub.stop() for sub in reversed(list(self._subsystems.values())) if hasattr(sub, "stop")]
        await asyncio.gather(*close_coros, return_exceptions=True)
        try:
            await self.bus.publish("system_stopped", {"msg": "Aether platform stopped"})
        except Exception:
            pass
        await self.bus.stop()
        self._running = False

    async def reload(self) -> None:
        """Hot‑reload configuration.
        The method loads the new configuration, diffs it against the previous
        version, and publishes ``config_changed`` for each altered path. Subsystems
        may raise ``ConfigReloadError`` to reject a change; the manager will roll
        back to the prior version in that case.
        """
        if not self._running:
            raise RuntimeError("Cannot reload when ServiceManager is not running")
        old_version = self.config.version()
        await self.config.load()  # reload from disk (new version)
        new_version = self.config.version()
        if old_version == new_version:
            # No change on disk – nothing to do.
            return
        # Compute diffs (very simple path‑wise diff for this implementation).
        # For a production system we would use a proper JSON diff library.
        changes: List[tuple[str, Any, Any]] = []
        def _collect(path: str, old_obj: Any, new_obj: Any):
            if isinstance(old_obj, dict) and isinstance(new_obj, dict):
                old_keys = set(old_obj.keys())
                new_keys = set(new_obj.keys())
                for k in old_keys | new_keys:
                    sub_path = f"{path}.{k}" if path else k
                    _collect(sub_path, old_obj.get(k), new_obj.get(k))
            else:
                if old_obj != new_obj:
                    changes.append((path, old_obj, new_obj))
        _collect("", self._old_config, self.config._data)
        # Notify subsystems of each change; they may raise ConfigReloadError.
        rejections: List[Exception] = []
        for path, old, new in changes:
            for sub in self._subsystems.values():
                inst = getattr(sub, "instance", sub)
                if hasattr(inst, "on_config_update"):
                    try:
                        await inst.on_config_update(path, old, new)
                    except Exception as exc:  # Subsystem rejected the change.
                        rejections.append(exc)
        if rejections:
            # Roll back to the previous version.
            # The ConfigStore keeps the prior file on disk; we simply reload it.
            await self.config.load()  # reload old version from file
            await self.bus.publish(
                "config_reload_failed",
                {"errors": [str(e) for e in rejections]},
            )
            raise RuntimeError("Configuration reload failed; changes rolled back")
        # All subsystems accepted the new config.
        self._old_config = dict(self.config._data)
        await self.bus.publish("config_reload_success", {"version": new_version})

    # ---------------------------------------------------------------------
    # Helper methods – subsystem discovery & wiring
    # ---------------------------------------------------------------------
    async def _load_subsystems(self) -> None:
        """Instantiate all core subsystems.
        The imports are lazy to avoid circular dependencies; each subsystem
        receives ``config`` and ``bus`` references.
        """
        # Import statements are placed inside the method.
        from .data.engine import DataEngine
        from .context.engine import ContextEngine
        from .features.engine import FeatureEngine, IndicatorRegistry
        from .strategy.engine import StrategyEngine, StrategyRegistry
        from .signal.state_manager import SignalStateManager
        from .alert_manager import AlertManager
        from .delivery.manager import DeliveryManager
        from .ops.health import HealthMonitor
        from .ops.retry_queue import RetryQueue
        from .model_manager import ModelManager
        from .adapters.registry import AdapterRegistry
        from .metrics.exporter import MetricsExporter
        
        from .agent import AetherAgent
        from .security.permission import PermissionEngine
        from .memory.conversation import ConversationMemory
        from .streaming.manager import StreamingManager

        from .subsystem import SubsystemAdapter

        # Load raw subsystem instances first.
        data = DataEngine(self.config, self.bus)
        context = ContextEngine(self.config, self.bus)
        
        # Features setup
        from .features.trend import EMAIndicator
        from .features.momentum_volatility import RSIIndicator, ATRIndicator, ADXIndicator, MACDIndicator
        ind_registry = IndicatorRegistry()
        ind_registry.register(EMAIndicator(20))
        ind_registry.register(EMAIndicator(50))
        ind_registry.register(EMAIndicator(200))
        ind_registry.register(RSIIndicator(14))
        ind_registry.register(ATRIndicator(14))
        ind_registry.register(ADXIndicator(14))
        ind_registry.register(MACDIndicator(12, 26, 9))
        features = FeatureEngine(ind_registry, self.config, self.bus)
        
        # Strategy setup
        from .strategy.builtin_profiles import build_default_strategies
        from .strategy.trend_following import TrendFollowingV1

        def _load_strategy_config(name: str) -> Dict[str, Any]:
            try:
                strategies_cfg = self.config.get("strategies", [])
            except Exception:
                strategies_cfg = []
            for item in strategies_cfg or []:
                if not isinstance(item, dict) or item.get("name") != name:
                    continue
                config_path = item.get("config_path")
                if not config_path:
                    return item
                path = self.project_root / config_path
                try:
                    return yaml.safe_load(path.read_text()) or item
                except Exception:
                    logger.error("strategy_config_load_failed", strategy=name, path=str(path))
                    return item
            return {}

        strat_registry = StrategyRegistry()
        strat_registry.register(TrendFollowingV1("trend_following_v1", "1.0.0", _load_strategy_config("trend_following_v1")))
        for strategy_instance in build_default_strategies():
            strat_registry.register(strategy_instance)
        strategy = StrategyEngine(strat_registry, self.config)
        
        # Signals setup
        signal = SignalStateManager(str(self.project_root / "data"), self.config)
        alerts = AlertManager(self.config, self.bus)
        
        # Security, Memory, LLM/AI
        permission = PermissionEngine(self.project_root / "config" / "permissions.yaml")
        conversation = ConversationMemory("default_session", self.project_root)
        model = ModelManager(self.config, self.bus)
        agent = AetherAgent(model, self.config, permission, conversation, bus=self.bus)
        streaming = StreamingManager(model)
        
        # Delivery & Adapters
        adapters = AdapterRegistry(self.config, self.bus)
        retry = RetryQueue(self.config, self.bus)
        retry._adapter_registry = adapters  # Avoid double instantiation
        
        delivery = DeliveryManager(self.config, self.bus)

        # Telegram menu service (operator UX)
        from .delivery.telegram_menu import TelegramMenuService
        
        # Health & Monitoring
        health = HealthMonitor(self.config, self.bus)
        metrics = MetricsExporter(self.config, self.bus)

        raw_subsystems = {
            "data": data,
            "context": context,
            "features": features,
            "strategy": strategy,
            "signal": signal,
            "alerts": alerts,
            "permission": permission,
            "conversation": conversation,
            "model": model,
            "agent": agent,
            "streaming": streaming,
            "adapters": adapters,
            "retry": retry,
            "delivery": delivery,
            "health": health,
            "metrics": metrics,
        }
        # Add telegram menu after adapters and delivery exist; it needs adapters registry
        try:
            telegram_menu = TelegramMenuService(self.config, self.bus, adapters, model)
            raw_subsystems["telegram_menu"] = telegram_menu
        except Exception:
            pass
        # Wrap each with SubsystemAdapter to provide uniform lifecycle.
        self._subsystems = {name: SubsystemAdapter(name, obj) for name, obj in raw_subsystems.items()}


    async def _start_subsystems(self):
        """Start all subsystem coroutines."""
        start_coros = [sub.start() for sub in self._subsystems.values() if hasattr(sub, "start")]
        if start_coros:
            await asyncio.gather(*start_coros)


    async def _wire_subsystems(self) -> None:
        """Register subsystem event handlers with the EventBus.
        Subsystems expose an optional ``register_events(bus)`` coroutine; if
        present we invoke it.
        """
        for sub in self._subsystems.values():
            inst = getattr(sub, "instance", sub)
            if hasattr(inst, "register_events"):
                await inst.register_events(self.bus)

        # Register subsystems to HealthMonitor
        health = self._subsystems.get("health")
        if health:
            raw_health = health.instance
            for name, sub in self._subsystems.items():
                if name != "health":
                    raw_health.add_subsystem(sub)

        # Wire DeliveryManager dependencies
        delivery = self._subsystems.get("delivery")
        adapters = self._subsystems.get("adapters")
        retry = self._subsystems.get("retry")
        if delivery and adapters and retry:
            await delivery.instance.setup(adapters.instance, retry.instance)

        # Bind platform transports to AetherAgent's conversational responses.
        agent = self._subsystems.get("agent")
        if agent and adapters and hasattr(agent.instance, "bind_default_senders"):
            try:
                agent.instance.bind_default_senders(adapters.instance)
            except Exception:
                logger.exception("agent_bind_senders_failed")

        # Register ConfigStore watcher so subsystems receive ``config_changed``.
        self.config.watch(self._config_changed_watcher)

    async def _config_changed_watcher(self, path: str, old: Any, new: Any) -> None:
        """Forward config changes onto the EventBus.
        ``config_changed`` payload mirrors the ConfigStore watcher signature.
        """
        await self.bus.publish("config_changed", {"path": path, "old": old, "new": new})

    # ---------------------------------------------------------------------
    # Status helper
    # ---------------------------------------------------------------------
    def status(self) -> Dict[str, str]:
        """Return a simple mapping of subsystem names to their lifecycle state.
        The actual subsystems may expose richer ``status()`` methods; this helper
        provides a quick overview for the gateway API.
        """
        result = {}
        for name, sub in self._subsystems.items():
            state = getattr(sub, "state", "unknown")
            result[name] = state
        return result

# Export the manager class for the gateway module to import.
__all__ = ["ServiceManager"]
