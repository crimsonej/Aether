"""AdapterRegistry – dynamic discovery and lifecycle management of platform adapters.

Adapters live under ``aether/adapters/<platform>/`` and export a module-level
``MANIFEST`` dictionary describing their name, type (inbound/outbound/both)
and a JSON schema for their configuration.  The registry reads the global
``ConfigStore`` to decide which adapters are enabled and instantiates them.

The registry also reacts to ``config_changed`` events to hot‑reload adapters
when the ``delivery.<adapter>.enabled`` flag toggles.
"""

import importlib
import pathlib
import asyncio
import os
from typing import Dict, Any, Callable, Awaitable

from ..config.store import ConfigStore
from ..event_bus import EventBus

# The public Adapter protocol (simplified for this design).  Concrete adapters
# implement these methods; the registry only checks for their presence.

class AdapterProtocol:
    async def validate(self) -> bool:
        raise NotImplementedError

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        raise NotImplementedError

    async def receive_message(self, message: Dict[str, Any]) -> None:  # optional
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class AdapterRegistry:
    def __init__(self, config: ConfigStore, bus: EventBus):
        self.config = config
        self.bus = bus
        self.adapters: Dict[str, AdapterProtocol] = {}
        # Register for config change notifications.
        self.config.watch(self._config_watcher)

    async def start(self) -> None:
        """Load all enabled adapters."""
        await self.load_all()

    async def stop(self) -> None:
        """Unload all adapters."""
        for name in list(self.adapters.keys()):
            await self.unload(name)

    async def load_all(self) -> None:
        """Discover adapter modules and instantiate the enabled ones.
        The method is idempotent; calling it again will reload any changes.
        """
        adapters_path = pathlib.Path(__file__).resolve().parents[2] / "adapters"
        for entry in adapters_path.iterdir():
            if not entry.is_dir() or entry.name.startswith("__"):
                continue
            module_name = f"aether.adapters.{entry.name}.adapter"
            try:
                mod = importlib.import_module(module_name)
            except Exception as exc:
                print(f"[AdapterRegistry] failed to import {module_name}: {exc}")
                continue
            manifest = getattr(mod, "MANIFEST", None)
            if not manifest:
                print(f"[AdapterRegistry] module {module_name} missing MANIFEST; skipping")
                continue
            name = manifest.get("name")
            if not name:
                print(f"[AdapterRegistry] MANIFEST in {module_name} lacks 'name' key")
                continue
            enabled_path = f"delivery.{name}.enabled"
            try:
                enabled = self.config.get(enabled_path)
            except KeyError:
                # If the config does not contain a key for this adapter we treat
                # it as disabled.
                enabled = False
            if not enabled:
                # Ensure a previously loaded adapter is torn down.
                await self.unload(name)
                continue
            # Instantiate the adapter class; convention: class name is
            # ``<Name>Adapter`` (e.g., ``TelegramAdapter``).
            class_name = f"{name.capitalize()}Adapter"
            adapter_cls = getattr(mod, class_name, None)
            if not adapter_cls:
                print(f"[AdapterRegistry] {module_name} missing class {class_name}")
                continue
            # Create the instance and store it.
            # Instantiate the adapter class
            instance = adapter_cls()

            # Configure the adapter with resolved environment variables
            try:
                from aether.core.config.loader import get_secret
                if name == "telegram":
                    token_env = self.config.get("delivery.telegram.token_env")
                    chat_id_env = self.config.get("delivery.telegram.chat_id_env")
                    # Token is required; chat_id is optional (commands/webhook work
                    # without it; only outbound messages need a chat_id).
                    token = get_secret(token_env)
                    chat_id = (os.environ.get(chat_id_env) or "").strip() if chat_id_env else ""
                    # webhook_url is optional — if set, we'll register it with Telegram
                    # on start() so the bot can receive inbound messages.
                    webhook_url = None
                    try:
                        webhook_url = self.config.get("delivery.telegram.webhook_url")
                    except Exception:
                        webhook_url = None
                    # Optional allowlist of Telegram user IDs permitted to interact
                    # with the bot. If absent/empty, the bot is open.
                    allowed_users = None
                    try:
                        allowed_users = self.config.get("delivery.telegram.allowed_users")
                    except Exception:
                        allowed_users = None
                    try:
                        webhook_secret_env = self.config.get("delivery.telegram.auth_token_env")
                    except Exception:
                        webhook_secret_env = None
                    webhook_secret = (os.environ.get(webhook_secret_env) or "").strip() if webhook_secret_env else None
                    instance.configure(
                        token, chat_id or None,
                        webhook_url=webhook_url,
                        allowed_users=allowed_users,
                        webhook_secret=webhook_secret or None,
                    )
                elif name == "whatsapp":
                    token_env = self.config.get("delivery.whatsapp.auth_token_env")
                    phone_env = self.config.get("delivery.whatsapp.phone_number_env")
                    token = get_secret(token_env)
                    phone = get_secret(phone_env)
                    instance.configure(token, phone)
            except Exception as e:
                print(f"[AdapterRegistry] failed to configure adapter {name}: {e}")
                # Skip loading this adapter; missing credentials should not be masked.
                continue

            # Only register and store the adapter if configuration succeeded.
            self.adapters[name] = instance
            # If the adapter defines an ``register_events`` coroutine we let it
            # hook into the bus (e.g., inbound webhook handling).
            if hasattr(instance, "register_events"):
                await instance.register_events(self.bus)
            # Provide the event bus to adapters that need it for inbound handling.
            # We look for a class attribute `_event_bus` that the AdapterRegistry
            # will set after loading.
            if hasattr(adapter_cls, '_event_bus'):
                adapter_cls._event_bus = self.bus
            # Lifecycle: call start() on the adapter if defined. The adapter
            # uses this hook to open sessions, register webhooks, and call
            # setMyCommands. Failures here must not prevent the adapter from
            # being registered (it may still be able to receive messages).
            if hasattr(instance, "start"):
                try:
                    await instance.start()
                except Exception as exc:
                    print(f"[AdapterRegistry] adapter {name} start() failed: {exc}")
            print(f"[AdapterRegistry] loaded adapter '{name}' (enabled={enabled})")

    async def unload(self, name: str) -> None:
        """Gracefully shut down and remove an adapter.
        If the adapter is not loaded, the call is a no‑op.
        """
        adapter = self.adapters.pop(name, None)
        if adapter:
            if hasattr(adapter, "close"):
                try:
                    await adapter.close()
                except Exception as exc:
                    print(f"[AdapterRegistry] error closing adapter {name}: {exc}")
            print(f"[AdapterRegistry] unloaded adapter '{name}'")

    def get(self, name: str) -> AdapterProtocol:
        """Return a loaded adapter instance; raises ``KeyError`` if not enabled."""
        if name not in self.adapters:
            raise KeyError(f"Adapter '{name}' is not loaded or is disabled")
        return self.adapters[name]

    def list_enabled(self) -> list[str]:
        """Return the list of currently enabled adapter names."""
        return list(self.adapters.keys())

    # ---------------------------------------------------------------------
    # Config change handling – hot‑reload adapters on flag toggle.
    # ---------------------------------------------------------------------
    async def _config_watcher(self, path: str, old: Any, new: Any) -> None:
        # We only care about changes to ``delivery.<adapter>.enabled``.
        if not path.startswith("delivery.") or not path.endswith(".enabled"):
            return
        adapter_name = path.split(".")[1]
        if new and not old:
            # Enabled now – load the adapter.
            await self.load_all()
        elif not new and old:
            # Disabled now – unload.
            await self.unload(adapter_name)

    # ---------------------------------------------------------------------
    # Optional helper for broadcasting outbound signals.
    # ---------------------------------------------------------------------
    async def broadcast_signal(self, signal: Dict[str, Any]) -> None:
        """Send a signal to all *outbound* adapters.
        Adapters that do not implement ``send_signal`` are ignored.
        """
        for name, adapter in self.adapters.items():
            if hasattr(adapter, "send_signal"):
                try:
                    await adapter.send_signal(signal)
                except Exception as exc:
                    print(f"[AdapterRegistry] failed to send signal via {name}: {exc}")

__all__ = ["AdapterRegistry", "AdapterProtocol"]
