"""ConfigStore – central mutable configuration repository.

This module provides an async‑friendly, atomic JSON‑backed configuration store
with schema validation, versioning, and an event‑driven change notification
mechanism. All runtime components (service manager, adapters, AetherAgent
manager, etc.) read from and write to this store; the rest of the system
reacts to the `config_changed` events published on the EventBus.
"""

import yaml
import asyncio
import pathlib
from typing import Any, Callable, Dict, Awaitable

from aether.core.config.schema import AetherConfig

# The JSON‑Schema is kept as a fallback metadata reference or ignored in favor of Pydantic.
CONFIG_SCHEMA = {}

class ConfigValidationError(Exception):
    """Raised when a configuration mutation does not conform to the schema."""


class ConfigStore:
    """Singleton‑ish async configuration store.

    The store is deliberately lightweight – it does not try to be a full‑blown
    key/value database.  All state lives in a single YAML file under the project
    root (`config/aether.yaml`).  Consumers interact via the dot‑notation
    ``get``/``set`` helpers.
    """

    _instance: "ConfigStore | None" = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls, *args, **kwargs):  # pragma: no cover – defensive singleton pattern
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, project_root: pathlib.Path | None = None):
        # ``project_root`` is injected by the ServiceManager at start‑up.  If not
        # supplied we fall back to the repository root (two levels up from this file).
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.project_root = project_root or pathlib.Path(__file__).resolve().parents[3]
        self.config_path = self._resolve_config_path()
        self._data: Dict[str, Any] = {}
        self._version: int = 0
        self._watchers: list[Callable[[str, Any, Any], Awaitable[None]]] = []

    def _resolve_config_path(self) -> pathlib.Path:
        """Return the first existing config path, checking canonical locations.

        Priority order:
          1. ``<project_root>/config/aether.yaml``  – written by CLI onboarding.
          2. ``<project_root>/aether/config/aether.yaml`` – legacy package location.
        """
        candidates = [
            self.project_root / "config" / "aether.yaml",
            self.project_root / "aether" / "config" / "aether.yaml",
        ]
        for path in candidates:
            if path.exists():
                return path
        # Fall back to the canonical path even if it doesn't exist yet so that
        # ``load()`` can raise a clear ``FileNotFoundError``.
        return candidates[0]

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------
    async def load(self) -> None:
        """Load the YAML file into memory and validate it.
        Raises ``ConfigValidationError`` if the on‑disk file is invalid.
        """
        async with self._lock:
            if not self.config_path.exists():
                raise FileNotFoundError(f"Config file not found: {self.config_path}")
            content = self.config_path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not data:
                data = {}
            # Ensure watchlist is present for compatibility with tests and managers
            if "watchlist" not in data:
                data["watchlist"] = []
            try:
                # Validate using Pydantic
                AetherConfig(**data)
            except Exception as e:
                raise ConfigValidationError(f"Configuration validation failed: {e}")
            self._data = data
            # ``schema_version`` is part of the file – we also keep a numeric
            # version that increments on each successful ``save``.
            self._version = data.get("config_version", 1)

    async def _save_unlocked(self) -> None:
        try:
            # Validate before we ever touch the file system.
            AetherConfig(**self._data)
        except Exception as e:
            raise ConfigValidationError(f"Configuration validation failed: {e}")
        # Increment version and embed it.
        self._version += 1
        self._data["config_version"] = self._version
        # Write to a temp file then rename – this guarantees atomicity on
        # POSIX compliant filesystems.
        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(yaml.dump(self._data, default_flow_style=False), encoding="utf-8")
        tmp_path.replace(self.config_path)

    async def save(self) -> None:
        """Write the in‑memory configuration back to disk atomically.
        The method increments ``config_version`` and validates before persisting.
        """
        async with self._lock:
            await self._save_unlocked()

    # ---------------------------------------------------------------------
    # Helper utilities – dot‑notation path handling
    # ---------------------------------------------------------------------
    def _traverse(self, path: str, create_missing: bool = False) -> tuple[dict, str]:
        """Navigate ``self._data`` according to a dotted path.
        Returns the parent dict and the final key.
        """
        parts = path.split(".")
        cur: dict = self._data
        for part in parts[:-1]:
            if part not in cur:
                if create_missing:
                    cur[part] = {}
                else:
                    raise KeyError(f"Path '{path}' does not exist (missing '{part}')")
            cur = cur[part]
            if not isinstance(cur, dict):
                raise KeyError(f"Path '{path}' traverses a non‑dict at '{part}'")
        return cur, parts[-1]

    def get(self, path: str) -> Any:
        """Retrieve a value using dot notation. Raises ``KeyError`` if missing."""
        parent, key = self._traverse(path, create_missing=False)
        if key not in parent:
            raise KeyError(f"Key '{key}' not found in path '{path}'")
        return parent[key]

    async def set(self, path: str, value: Any) -> None:
        """Set a value, validate the change, persist, and notify watchers.
        ``create_missing`` allows creation of intermediate containers.
        """
        async with self._lock:
            parent, key = self._traverse(path, create_missing=True)
            old = parent.get(key, None)
            parent[key] = value
            try:
                await self._save_unlocked()
            except Exception:
                # Roll back in‑memory change on failure.
                if old is None:
                    del parent[key]
                else:
                    parent[key] = old
                raise
            await self._emit_change(path, old, value)

    async def delete(self, path: str) -> None:
        """Delete a key and persist the change. Raises ``KeyError`` if missing."""
        async with self._lock:
            parent, key = self._traverse(path, create_missing=False)
            if key not in parent:
                raise KeyError(f"Key '{key}' not found in path '{path}'")
            old = parent[key]
            del parent[key]
            try:
                await self._save_unlocked()
            except Exception:
                # Restore on failure.
                parent[key] = old
                raise
            await self._emit_change(path, old, None)

    # ---------------------------------------------------------------------
    # Watcher support – used by ServiceManager & others
    # ---------------------------------------------------------------------
    def watch(self, callback: Callable[[str, Any, Any], Awaitable[None]]) -> None:
        """Register a coroutine to be called on every config mutation.
        The callback receives ``(path, old_value, new_value)``.
        """
        self._watchers.append(callback)

    async def _emit_change(self, path: str, old: Any, new: Any) -> None:
        """Notify all registered watchers about a configuration change."""
        for cb in self._watchers:
            try:
                await cb(path, old, new)
            except Exception as exc:
                # Watcher failures should not break the store – we log and
                # continue.  In a real deployment we would use the central logger.
                print(f"[ConfigStore] watcher error for path '{path}': {exc}")

    # ---------------------------------------------------------------------
    # Convenience accessors used throughout the code base
    # ---------------------------------------------------------------------
    def version(self) -> int:
        """Return the on‑disk ``config_version`` – useful for hot‑reload checks."""
        return self._version

    # The store is deliberately tiny – any additional helper methods should be
    # added only after a concrete need is identified.

__all__ = ["ConfigStore", "ConfigValidationError"]
