"""PermissionEngine – role based access control for the Aether platform.

Roles and allowed actions are loaded from ``config/permissions.yaml``.  The
engine provides a simple ``check(user, action)`` method that returns ``True`` if
the user is permitted.  The ``user`` string is expected to be a unique username
(e.g., the Telegram ``@handle`` or a system account name).  A ``user_roles``
mapping can be supplied at runtime; if a user is unknown they default to the
``operator`` role.
"""

import yaml
import pathlib
import asyncio
from typing import Dict, List, Any

PERMISSIONS_PATH = pathlib.Path(__file__).resolve().parents[3] / "config" / "permissions.yaml"


class PermissionError(RuntimeError):
    pass


class PermissionEngine:
    def __init__(self, permissions_path: pathlib.Path | None = None):
        self.permissions_path = permissions_path or PERMISSIONS_PATH
        self._permissions: Dict[str, List[str]] = {}
        self._user_roles: Dict[str, str] = {}
        self._load_permissions()
        # Watch the file for changes (simple polling for this design).
        self._watch_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._start_watcher()

    async def stop(self) -> None:
        await self.shutdown()

    def _load_permissions(self) -> None:
        if not self.permissions_path.exists():
            # No permissions file yet – default to a permissive operator role so
            # the platform can boot before formal onboarding.
            self._permissions = {"operator": ["*"], "viewer": ["read_config", "view_signals"]}
            print(f"[PermissionEngine] permissions file not found at {self.permissions_path}; using defaults")
            return
        with self.permissions_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        # Normalise into ``role -> [actions]`` mapping.
        self._permissions = {}
        for role, spec in raw.items():
            actions = spec.get("allowed_actions", [])
            if "*" in actions:
                self._permissions[role] = ["*"]
            else:
                self._permissions[role] = list(actions)

    def assign_role(self, user: str, role: str) -> None:
        """Explicitly bind a user to a role (overrides default mapping)."""
        if role not in self._permissions:
            raise PermissionError(f"Unknown role '{role}'")
        self._user_roles[user] = role

    def role_for(self, user: str) -> str:
        """Return the role for a given user; defaults to ``operator``."""
        return self._user_roles.get(user, "operator")

    def check(self, user: str, action: str) -> bool:
        """Return ``True`` if the user is allowed to perform ``action``.
        ``action`` must be a string matching one of the allowed_action entries.
        A wildcard ``*`` grants all actions.
        """
        role = self.role_for(user)
        allowed = self._permissions.get(role, [])
        return "*" in allowed or action in allowed

    # ---------------------------------------------------------------------
    # Dynamic reload support – polls the file every 10 seconds.
    # ---------------------------------------------------------------------
    async def _watcher(self) -> None:
        last_mtime = self.permissions_path.stat().st_mtime
        while True:
            await asyncio.sleep(10)
            try:
                current = self.permissions_path.stat().st_mtime
                if current != last_mtime:
                    self._load_permissions()
                    last_mtime = current
                    print("[PermissionEngine] permissions reloaded")
            except Exception as exc:
                print(f"[PermissionEngine] watcher error: {exc}")

    def _start_watcher(self) -> None:
        if not self._watch_task:
            self._watch_task = asyncio.create_task(self._watcher())

    async def shutdown(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None

__all__ = ["PermissionEngine", "PermissionError"]
