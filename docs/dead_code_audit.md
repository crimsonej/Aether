Dead Code Audit

Date: 2026-05-31

Summary: This audit lists placeholder implementations, unused/mocked modules, duplicate methods, unreachable code and other items that should be reviewed before deletion.

Categories

- Safe to Delete (low risk):
  - None automatically recommended; manual review advised before deletion.

- Needs Implementation (functional placeholders):
  - `aether/core/gateway/server.py` — multiple `pass` branches in request handlers. [file](aether/core/gateway/server.py)
  - `aether/core/context/engine.py` — placeholder comments and `pass` in context computation. [file](aether/core/context/engine.py)
  - `aether/core/strategy/base.py` — strategy interface contains `pass` methods to implement. [file](aether/core/strategy/base.py)
  - `aether/adapters/web/adapter.py` and `aether/adapters/discord/adapter.py` — skeleton adapters with `pass` methods. [file](aether/adapters/web/adapter.py), [file](aether/adapters/discord/adapter.py)
  - `aether/core/security/permission.py` — contains `pass` and incomplete permission logic. [file](aether/core/security/permission.py)

- Mock/Testing Logic (should remain but be isolated):
  - `aether/core/adapters/registry.py` uses `mock_token`/`mock_chat_id` fallback when secrets unavailable — acceptable for dev, but ensure it is not used in production. [file](aether/core/adapters/registry.py#L100-L120)
  - Tests contain mocks and AsyncMock usage (expected). See `aether/tests/*`.

- Potential Architectural Risk (requires design decision):
  - `aether/core/data/provider_manager.py` currently uses in-memory backoff and health; consider external redis-backed state if multiple processes run.
  - `aether/core/model_manager.py` publishes events on fallback; ensure these are rate-limited to avoid notification storms.
  - `aether/core/data/oanda_client.py` includes Oanda-specific mapping and is still referenced in some modules; ensure complete removal if Oanda is deprecated.

- Duplicate/Unreachable Patterns:
  - Various `except Exception: pass` sites (e.g., `aether/core/service_manager.py`, `aether/core/ops/health.py`) — these can mask errors; prefer logging and explicit handling.

Action items

- Review and implement context engine and gateway handlers before enabling production traffic.
- Replace `pass` exception swallowing with proper logging in critical subsystems.
- Decide whether to keep skeleton adapters (web/discord) or remove them until implemented.
- Migrate deprecations (Pydantic `.dict()` -> `.model_dump()`).

Files referenced (examples):
- [aether/core/gateway/server.py](aether/core/gateway/server.py)
- [aether/core/context/engine.py](aether/core/context/engine.py)
- [aether/core/strategy/base.py](aether/core/strategy/base.py)
- [aether/adapters/web/adapter.py](aether/adapters/web/adapter.py)
- [aether/adapters/discord/adapter.py](aether/adapters/discord/adapter.py)
- [aether/core/security/permission.py](aether/core/security/permission.py)
- [aether/core/adapters/registry.py](aether/core/adapters/registry.py)

Recommendation: Triage items into a short-term backlog (must-fix before production), medium-term (improve robustness), and long-term (architectural changes). I can produce a prioritized backlog next if you want.
