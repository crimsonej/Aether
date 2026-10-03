"""AIConfigManager – parses natural language commands into ConfigStore intents.

The manager receives raw user text (from Telegram, WhatsApp, or the CLI), builds a
prompt that includes a static system instruction and recent conversation
memory, then streams the response from ``ModelManager``.  The LLM is expected to
return a single JSON object that matches the ``Intent`` schema.

If parsing succeeds, the manager checks permissions via ``PermissionEngine``
and applies the mutation to ``ConfigStore``.  The manager also sends a short
confirmation back to the originating adapter using the adapter's ``send_signal``
method.
"""

import json
import asyncio
from typing import Dict, Any, Callable, Awaitable

from .model_manager import ModelManager
from .config.store import ConfigStore, ConfigValidationError
from .security.permission import PermissionEngine
from .memory.conversation import ConversationMemory
from .utils.logger import logger

# Intent schema (used for validation of the LLM output).  The schema is simple
# enough that a runtime ``jsonschema`` check is cheap.
INTENT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "add_symbol",
                "remove_symbol",
                "enable_platform",
                "disable_platform",
                "set_indicator",
                "set_strategy_param",
            ],
        },
        "payload": {"type": "object"},
    },
    "required": ["action", "payload"],
}


class AIConfigManager:
    def __init__(
        self,
        model_manager: ModelManager,
        config: ConfigStore,
        permission: PermissionEngine,
        conversation: ConversationMemory,
        bus=None,
    ):
        self.model = model_manager
        self.config = config
        self.permission = permission
        self.conversation = conversation
        self.bus = bus  # EventBus instance injected by ServiceManager
        # Mapping from source name (e.g., "telegram") to a callable that can send
        # a message back to the user.
        self._senders: Dict[str, Callable[[str], Awaitable[None]]] = {}

    def register_source_adapter(self, name: str, send_fn: Callable[[str], Awaitable[None]]) -> None:
        """Register a callback used to send a textual response back to the source.
        ``send_fn`` must be an ``async def`` that accepts a single string.
        """
        self._senders[name] = send_fn

    def bind_default_senders(self, adapter_registry: Any) -> None:
        """Auto-register any loaded adapter that exposes ``send_message``.

        The AdapterRegistry stores loaded adapters under ``adapters[name]``.
        Each may implement ``send_message(text)`` (Telegram/Discord/etc.) which
        is a faithful transport for AI replies.  Binding them here means the AI
        has a way to reach the user without the operator wiring it manually.

        Safe to call repeatedly; existing bindings are preserved.
        """
        try:
            loaded = getattr(adapter_registry, "adapters", None) or {}
        except Exception:
            loaded = {}
        for name, adapter in loaded.items():
            if not hasattr(adapter, "send_message"):
                continue
            if name in self._senders:
                continue
            self._senders[name] = adapter.send_message
            logger.info("[AIConfigManager] auto-bound sender for source '%s'", name)

    async def handle_user_command(self, raw_text: str, user: str, source: str) -> None:
        """Entry point for inbound commands.
        ``source`` must match a previously registered adapter name.
        """
        # Record the user utterance for context.
        await self.conversation.add_entry("user", raw_text)
        # Build the LLM prompt.  We support two reply modes:
        #   * Strict JSON intent for command execution (add_symbol, etc.)
        #   * Free-form text reply for greetings, status questions, debug help
        # The LLM decides by either emitting a JSON object matching the
        # schema, or any other text.  We handle both branches below.
        system_prompt = (
            "You are Aether, a deterministic trading-intelligence assistant. "
            "Reply in one of two forms:\n"
            "  1. A strict JSON object matching this schema (only when the "
            "operator is issuing a configuration command):\n"
            + json.dumps(INTENT_SCHEMA, indent=2)
            + "\n  2. Otherwise, a concise free-form text reply.\n"
            "Always prefer text when the message is a greeting, question, or "
            "request for information. Only emit JSON when the user is clearly "
            "asking to change configuration (e.g., 'add EURUSD to watchlist')."
        )
        # Retrieve recent conversation history (max 5 turns).
        recent = self.conversation.recent(5)
        history = "\n".join(f"{e['role']}: {e['text']}" for e in recent)
        prompt = f"{system_prompt}\nConversation history:{history}\nUser: {raw_text}\nAssistant:"
        # Stream the LLM response.  If every provider is exhausted or absent,
        # surface that to the user instead of dropping silently.
        collected = []
        try:
            async for chunk in self.model.generate(prompt):
                collected.append(chunk)
        except RuntimeError as exc:
            msg = (
                "🤖 No AI provider is currently available. "
                "Set an API key (e.g. NVIDIA_API_KEY, OPENAI_API_KEY) in .env "
                "and restart the gateway."
            )
            logger.warning("[AIConfigManager] LLM chain failed: %s", exc)
            await self._send_back(source, msg)
            return
        raw_response = "".join(collected).strip()
        if not raw_response:
            await self._send_back(source, "🤖 The AI returned no text. Try again or check provider health.")
            return
        # The LLM should have returned a JSON object.  Attempt to parse.
        try:
            intent = json.loads(raw_response)
        except json.JSONDecodeError:
            # Free-form reply path: if the model didn't return strict JSON,
            # echo the text back as a chat reply.  This is what makes "hi"
            # actually get answered.
            text = raw_response[:1500]
            await self._send_back(source, text)
            return
        # Validate intent against schema.
        from jsonschema import Draft7Validator
        validator = Draft7Validator(INTENT_SCHEMA)
        errors = list(validator.iter_errors(intent))
        if errors:
            await self._send_back(source, "Your command did not match the expected format. Please try again.")
            return
        # Permission check.
        action = intent["action"]
        if not self.permission.check(user, action):
            await self._send_back(source, "You are not authorized to perform that action.")
            await self._emit_event("ai.permission_denied", {"user": user, "action": action})
            return
        # Apply the intent to the ConfigStore.
        try:
            await self._apply_intent(intent)
        except ConfigValidationError as e:
            await self._send_back(source, f"Configuration error: {e}")
            return
        # Success feedback.
        await self._send_back(source, f"✅ Command '{action}' applied successfully.")
        await self._emit_event("ai.command_executed", {"user": user, "action": action, "payload": intent["payload"]})
        # Record assistant response for future pronoun resolution.
        await self.conversation.add_entry("assistant", f"Command '{action}' applied.")

    async def _apply_intent(self, intent: Dict[str, Any]) -> None:
        """Map the high‑level intent to concrete ConfigStore mutations.
        This method contains the only place where we translate the abstract action
        names into dot‑notation paths.
        """
        action = intent["action"]
        payload = intent["payload"]
        if action == "add_symbol":
            symbol = payload["symbol"].upper()
            # Retrieve current watchlist, append if not present.
            watchlist = self.config.get("watchlist")
            if symbol not in watchlist:
                watchlist.append(symbol)
                await self.config.set("watchlist", watchlist)
        elif action == "remove_symbol":
            symbol = payload["symbol"].upper()
            watchlist = self.config.get("watchlist")
            if symbol in watchlist:
                watchlist.remove(symbol)
                await self.config.set("watchlist", watchlist)
        elif action == "enable_platform":
            platform = payload["platform"].lower()
            await self.config.set(f"delivery.{platform}.enabled", True)
        elif action == "disable_platform":
            platform = payload["platform"].lower()
            await self.config.set(f"delivery.{platform}.enabled", False)
        elif action == "set_indicator":
            indicator = payload["indicator"].lower()
            enabled = payload.get("enabled", True)
            features = self.config.get("features.enabled")
            if enabled and indicator not in features:
                features.append(indicator)
            elif not enabled and indicator in features:
                features.remove(indicator)
            await self.config.set("features.enabled", features)
        elif action == "set_strategy_param":
            name = payload["strategy"]
            param = payload["param"]
            value = payload["value"]
            # Locate the strategy in the list and update its config_path or a
            # params dict (implementation‑specific – here we assume a dict stored
            # under ``strategies.<name>.params`` for simplicity).
            strategies = self.config.get("strategies")
            for strat in strategies:
                if strat["name"] == name:
                    # Expand the strategy dict with a ``params`` sub‑dict if
                    # missing.
                    params = strat.setdefault("params", {})
                    params[param] = value
                    break
            else:
                raise ConfigValidationError(f"Strategy '{name}' not found")
            await self.config.set("strategies", strategies)
        else:
            raise ConfigValidationError(f"Unsupported action: {action}")

    async def _send_back(self, source: str, message: str) -> None:
        """Send a textual reply back to the originating adapter.
        ``source`` must be one of the keys registered via ``register_source_adapter``.
        If no sender is registered for the source, the message is logged at
        INFO level so the operator can still see it.
        """
        sender = self._senders.get(source)
        if not sender:
            logger.info(
                "[AIConfigManager] no sender for source '%s'; message=%r",
                source, message,
            )
            return
        try:
            await sender(message)
        except Exception as exc:
            logger.exception(
                "[AIConfigManager] sender for source '%s' raised: %s", source, exc,
            )

    async def register_events(self, bus) -> None:
        """Register on the EventBus so inbound user_command events are handled."""
        self.bus = bus
        await bus.subscribe("user_command", self._on_user_command)
        # Free-form text routed by TelegramMenuService after the menu has
        # handled known slash commands.  This is what makes "hi" reach the AI.
        await bus.subscribe("user.free_text", self._on_user_command)

    async def _on_user_command(self, event: dict) -> None:
        """EventBus handler bridging user_command → handle_user_command."""
        raw = event.get("raw", "")
        user = event.get("user", "unknown")
        source = event.get("source", "unknown")
        await self.handle_user_command(raw, user, source)

    async def _emit_event(self, topic: str, payload: Dict[str, Any]) -> None:
        """Publish an event on the EventBus (if available)."""
        if self.bus:
            await self.bus.publish(topic, payload)

__all__ = ["AIConfigManager"]
