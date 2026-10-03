"""AetherAgent – Aether's conversational planner and permissioned tool executor.

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
import math
import uuid
from typing import Dict, Any, Callable, Awaitable

from .model_manager import ModelManager
from .config.store import ConfigStore, ConfigValidationError
from .security.permission import PermissionEngine
from .memory.conversation import ConversationMemory
from .tools.registry import ToolRegistry
from .tools.web import ApprovedWebDataTool
from .utils.logger import logger

# Intent schema (used for validation of the LLM output).  The schema is simple
# enough that a runtime ``jsonschema`` check is cheap.
INTENT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "tool_call": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "arguments": {"type": "object"},
                    },
                    "required": ["name", "arguments"],
                    "additionalProperties": False,
                }
            },
            "required": ["tool_call"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["action", "payload"],
            "additionalProperties": False,
        },
    ],
}


class AetherAgent:
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
        self.tools = self._build_tool_registry()
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
            logger.info("[AetherAgent] auto-bound sender for source '%s'", name)

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
            "Use only the capabilities listed below. For a clear request that "
            "requires a change or Aether-specific information, return one JSON "
            "tool_call using the supplied name and argument schema. Otherwise "
            "answer concise text. Never claim a tool succeeded until its result "
            "is returned. Never change or invent trading signals, confidence, "
            "stops, targets, or validation. News notifications are inactive if "
            "the calendar source is not configured.\nAvailable capabilities:\n"
            + json.dumps(self._get_tools().specifications(), indent=2)
            + "\nTool-call response schema:\n"
            + json.dumps(INTENT_SCHEMA, indent=2)
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
                "Configure a cloud API key or start Ollama, install a model, "
                "then select it from /models."
            )
            logger.warning("[AetherAgent] reasoning backend failed: %s", exc)
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
        if "tool_call" in intent:
            tool_name = intent["tool_call"]["name"]
            arguments = intent["tool_call"]["arguments"]
        else:
            tool_name = intent["action"]
            arguments = intent["payload"]
        tools = self._get_tools()
        result = await tools.execute(tool_name, arguments, user)
        if not result["ok"]:
            if result["error"] == "permission_denied":
                await self._send_back(source, "You are not authorized to use that capability.")
                await self._emit_event("ai.permission_denied", {"user": user, "tool": tool_name})
            else:
                await self._send_back(source, f"Capability '{tool_name}' was not run: {result.get('details', result['error'])}.")
                await self._emit_event("ai.tool_rejected", {"user": user, "tool": tool_name, "error": result["error"]})
            return
        response = result["result"]
        if response is None:
            response = f"✅ Capability '{tool_name}' completed."
        elif not isinstance(response, str):
            response = json.dumps(response, indent=2, sort_keys=True)
        await self._send_back(source, response)
        await self._emit_event("ai.tool_executed", {"user": user, "tool": tool_name, "arguments": arguments})
        await self.conversation.add_entry("assistant", response)

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry(self.permission)

        def add(name, description, properties, required, handler):
            registry.register(
                name=name,
                description=description,
                parameters={"properties": properties, "required": required},
                permission=name,
                handler=handler,
            )

        no_args = {}
        add("get_system_overview", "Read configured watchlist, providers, timeframes, features, models, alerts, and signal-only mode.", no_args, [], self._get_system_overview)
        add("get_watchlist", "Read the configured market watchlist.", no_args, [], self._get_watchlist_tool)
        add("list_alerts", "Read configured price alerts and news-alert status.", no_args, [], lambda _args: self._format_alerts())
        add("search_web", "Search approved public web sources for market or news context.", {"query": {"type": "string", "minLength": 2}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"], self._search_web_tool)
        add("fetch_web_page", "Fetch an approved public web page and summarize its content.", {"url": {"type": "string", "minLength": 8}}, ["url"], self._fetch_web_page_tool)
        add("add_symbol", "Add one symbol to the watchlist.", {"symbol": {"type": "string", "minLength": 3, "maxLength": 12}}, ["symbol"], lambda args: self._apply_intent({"action": "add_symbol", "payload": args}))
        add("remove_symbol", "Remove one symbol from the watchlist.", {"symbol": {"type": "string", "minLength": 3, "maxLength": 12}}, ["symbol"], lambda args: self._apply_intent({"action": "remove_symbol", "payload": args}))
        add("enable_platform", "Enable one configured delivery platform.", {"platform": {"enum": ["telegram", "discord", "web", "whatsapp"]}}, ["platform"], lambda args: self._apply_intent({"action": "enable_platform", "payload": args}))
        add("disable_platform", "Disable one configured delivery platform.", {"platform": {"enum": ["telegram", "discord", "web", "whatsapp"]}}, ["platform"], lambda args: self._apply_intent({"action": "disable_platform", "payload": args}))
        add("set_indicator", "Enable or disable one supported indicator.", {"indicator": {"enum": ["ema20", "ema50", "ema200", "rsi", "atr", "adx", "macd", "bollinger_width"]}, "enabled": {"type": "boolean"}}, ["indicator"], lambda args: self._apply_intent({"action": "set_indicator", "payload": args}))
        add("add_price_alert", "Create a one-shot alert on a fresh market quote.", {"symbol": {"type": "string"}, "condition": {"enum": ["above", "below"]}, "price": {"type": "number", "exclusiveMinimum": 0}}, ["symbol", "condition", "price"], lambda args: self._apply_intent({"action": "add_price_alert", "payload": args}))
        add("remove_price_alert", "Remove a matching one-shot price alert.", {"symbol": {"type": "string"}, "condition": {"enum": ["above", "below"]}, "price": {"type": "number", "exclusiveMinimum": 0}}, ["symbol", "condition", "price"], lambda args: self._apply_intent({"action": "remove_price_alert", "payload": args}))
        add("set_news_alerts", "Set per-pair news alert preferences; alerts need a configured calendar source.", {"enabled": {"type": "boolean"}, "pairs": {"type": "array", "items": {"type": "string"}, "maxItems": 30}, "minimum_impact": {"enum": ["low", "medium", "high"]}}, [], lambda args: self._apply_intent({"action": "set_news_alerts", "payload": args}))
        return registry

    def _get_tools(self) -> ToolRegistry:
        tools = getattr(self, "tools", None)
        if tools is None:
            tools = self._build_tool_registry()
            self.tools = tools
        return tools

    def _get_config_or(self, path: str, default):
        try:
            return self.config.get(path)
        except Exception:
            return default

    async def _search_web_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = ApprovedWebDataTool()
        limit = int(arguments.get("limit", 5))
        return {"results": await tool.search_web(str(arguments["query"]), limit=limit)}

    async def _fetch_web_page_tool(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = ApprovedWebDataTool()
        return await tool.fetch_page(str(arguments["url"]))

    async def _get_watchlist_tool(self, _arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {"watchlist": self._get_config_or("watchlist", [])}

    async def _get_system_overview(self, _arguments: Dict[str, Any]) -> Dict[str, Any]:
        strategies = self._get_config_or("strategies", [])
        return {
            "watchlist": self._get_config_or("watchlist", []),
            "data_provider_chain": self._get_config_or("data.provider_chain", []),
            "timeframes": self._get_config_or("data.timeframes", []),
            "features": self._get_config_or("features.enabled", []),
            "strategies": [item.get("name") for item in strategies if isinstance(item, dict)],
            "model_chain": self._get_config_or("model_manager.chain", []),
            "alerts": self._alerts(),
            "mode": "deterministic_signal_only",
        }

    async def _apply_intent(self, intent: Dict[str, Any]) -> str | None:
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
        elif action == "add_price_alert":
            symbol = self._validate_symbol(payload.get("symbol"))
            condition = payload.get("condition")
            price = self._finite_positive(payload.get("price"))
            if condition not in ("above", "below"):
                raise ValueError("condition must be 'above' or 'below'")
            alerts = self._alerts()
            alert = {
                "id": uuid.uuid4().hex[:12],
                "symbol": symbol,
                "condition": condition,
                "price": price,
                "enabled": True,
            }
            alerts.setdefault("price", []).append(alert)
            await self.config.set("alerts", alerts)
            return f"✅ Price alert {alert['id']} set: {symbol} {condition} {price:g}."
        elif action == "remove_price_alert":
            symbol = self._validate_symbol(payload.get("symbol"))
            condition = payload.get("condition")
            price = self._finite_positive(payload.get("price"))
            alerts = self._alerts()
            before = alerts.setdefault("price", [])
            alerts["price"] = [
                alert for alert in before
                if not (
                    alert.get("symbol", "").upper() == symbol
                    and alert.get("condition") == condition
                    and math.isclose(float(alert.get("price", -1)), price, rel_tol=1e-10)
                )
            ]
            if len(alerts["price"]) == len(before):
                raise ValueError(f"no matching {symbol} {condition} {price:g} alert was found")
            await self.config.set("alerts", alerts)
            return f"✅ Removed the {symbol} {condition} {price:g} price alert."
        elif action == "set_news_alerts":
            enabled = payload.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be true or false")
            pairs = [self._validate_symbol(pair) for pair in payload.get("pairs", [])]
            impact = str(payload.get("minimum_impact", "high")).lower()
            if impact not in ("low", "medium", "high"):
                raise ValueError("minimum_impact must be low, medium, or high")
            alerts = self._alerts()
            news = alerts.setdefault("news", {})
            news.update({"enabled": enabled, "pairs": sorted(set(pairs)), "minimum_impact": impact})
            await self.config.set("alerts", alerts)
            source_url = news.get("source_url")
            if enabled and not source_url:
                return (
                    "✅ News-alert preferences saved for "
                    f"{', '.join(news['pairs']) or 'no pairs'} ({impact}+ impact). "
                    "Notifications remain inactive until a calendar source URL is configured."
                )
            return f"✅ News alerts {'enabled' if enabled else 'disabled'} for {', '.join(news['pairs']) or 'no pairs'} ({impact}+ impact)."
        else:
            raise ConfigValidationError(f"Unsupported action: {action}")
        return None

    def _alerts(self) -> Dict[str, Any]:
        try:
            current = self.config.get("alerts")
        except (KeyError, AttributeError):
            current = {}
        alerts = dict(current or {})
        alerts.setdefault("price", [])
        alerts.setdefault("news", {})
        return alerts

    @staticmethod
    def _validate_symbol(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("symbol must be text")
        symbol = value.upper().replace("/", "").strip()
        if not symbol.isalnum() or not 3 <= len(symbol) <= 12:
            raise ValueError("symbol must be a supported pair/ticker such as EURUSD or BTCUSD")
        return symbol

    @staticmethod
    def _finite_positive(value: Any) -> float:
        try:
            price = float(value)
        except (TypeError, ValueError):
            raise ValueError("price must be numeric")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be a finite positive number")
        return price

    async def _format_alerts(self) -> str:
        alerts = self._alerts()
        prices = alerts.get("price", [])
        price_lines = [
            f"- {item.get('symbol')} {item.get('condition')} {item.get('price')} (id {item.get('id')})"
            for item in prices if item.get("enabled", True)
        ]
        news = alerts.get("news", {})
        news_text = (
            f"News: {'enabled' if news.get('enabled') else 'disabled'}; "
            f"pairs={', '.join(news.get('pairs', [])) or 'none'}; "
            f"minimum impact={news.get('minimum_impact', 'high')}; "
            f"source={'configured' if news.get('source_url') else 'not configured'}"
        )
        return "Alerts:\n" + ("\n".join(price_lines) if price_lines else "- No price alerts") + "\n" + news_text

    async def _send_back(self, source: str, message: str) -> None:
        """Send a textual reply back to the originating adapter.
        ``source`` must be one of the keys registered via ``register_source_adapter``.
        If no sender is registered for the source, the message is logged at
        INFO level so the operator can still see it.
        """
        sender = self._senders.get(source)
        if not sender:
            logger.info(
                "[AetherAgent] no sender for source '%s'; message=%r",
                source, message,
            )
            return
        try:
            await sender(message)
        except Exception as exc:
            logger.exception(
                "[AetherAgent] sender for source '%s' raised: %s", source, exc,
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

AIConfigManager = AetherAgent

__all__ = ["AetherAgent", "AIConfigManager"]
