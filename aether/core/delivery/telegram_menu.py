from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Set

from aether.core.utils.logger import logger


class TelegramMenuService:
    """Menu-driven operator UX over Telegram using inline keyboards.

    The service receives callback events from the Telegram adapter and sends
    structured replies using inline keyboards. It also updates runtime
    configuration through the central ConfigStore without requiring manual YAML
    edits.
    """

    MAIN_MENU = [
        [{"text": "📈 Signals", "callback_data": "menu:signals"}],
        [{"text": "📊 Performance", "callback_data": "menu:performance"}],
        [{"text": "📋 Watchlist", "callback_data": "menu:watchlist"}],
        [{"text": "📉 Indicators", "callback_data": "menu:indicators"}],
        [{"text": "⏱ Timeframes", "callback_data": "menu:timeframes"}],
        [{"text": "🤖 Models", "callback_data": "menu:models"}],
        [{"text": "🛠️ Status", "callback_data": "menu:status"}],
        [{"text": "⚙ Settings", "callback_data": "menu:settings"}],
    ]

    SYMBOL_OPTIONS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "BTCUSD", "ETHUSD"]
    INDICATOR_OPTIONS = [
        "ema20", "ema50", "ema200", "rsi", "atr", "adx", "macd",
        "bollinger_width", "higher_highs", "lower_lows", "bos",
        "consolidation", "expansion",
    ]
    TIMEFRAME_OPTIONS = ["5m", "15m", "1h", "4h", "1d"]

    def __init__(self, config: Any, bus: Any, adapters: Any):
        self.config = config
        self.bus = bus
        self.adapters = adapters
        self.stats = {
            "generated": 0,
            "active": 0,
            "wins": 0,
            "losses": 0,
            "expired": 0,
            "missed": 0,
        }
        self.active_signal_ids: Set[str] = set()

    async def register_events(self, bus: Any) -> None:
        await bus.subscribe("user_callback", self._on_callback)
        await bus.subscribe("user_command", self._on_command)
        await bus.subscribe("signal.generated", self._on_signal_generated)
        await bus.subscribe("signal.active", self._on_signal_active)
        await bus.subscribe("signal.activated", self._on_signal_active)
        await bus.subscribe("signal.closed", self._on_signal_closed)
        await bus.subscribe("signal.expired", self._on_signal_closed)
        await bus.subscribe("signal.missed", self._on_signal_closed)
        await bus.subscribe("model.fallback", self._on_model_fallback)
        await bus.subscribe("model.error", self._on_model_error)
        # Provider events for operator visibility
        await bus.subscribe("data.provider_failed", self._on_provider_failed)
        await bus.subscribe("data.provider_changed", self._on_provider_changed)
        await bus.subscribe("data.stale_detected", self._on_stale_detected)

    async def start(self) -> None:
        await self._send_main_menu()

    async def _on_command(self, payload: Dict[str, Any]) -> None:
        raw = (payload.get("raw") or "").strip()
        cmd = raw.lower().split()[0] if raw else ""
        if cmd in ("/menu", "menu", "/start", "start", "/help", "help"):
            await self._send_main_menu()
            if cmd in ("/start", "start"):
                await self._send_text(
                    "👋 Welcome to Aether — Deterministic Trading Intelligence.\n"
                    "Use the menu below to inspect signals, watchlist, indicators, "
                    "and the model chain. Type /menu at any time."
                )
            elif cmd in ("/help", "help"):
                await self._send_text(
                    "Available commands:\n"
                    "/menu   — open the interactive menu\n"
                    "/status — show system health\n"
                    "/signals — list active signals\n"
                    "/watchlist — show current watchlist\n"
                    "/models — show the LLM failover chain\n\n"
                    "Anything else goes to the AI assistant."
                )
        elif cmd in ("/status", "status"):
            await self._route_menu("status")
        elif cmd in ("/signals", "signals"):
            await self._route_menu("signals")
        elif cmd in ("/watchlist", "watchlist"):
            await self._route_menu("watchlist")
        elif cmd in ("/models", "models"):
            await self._route_menu("models")
        elif cmd.startswith("/"):
            await self._send_text(
                f"Unknown command: {raw}\nType /menu or /help to see what's available."
            )
        else:
            # Free-form text (e.g. "hi", "what's the status?", "audit the
            # gateway logs").  Forward on a dedicated topic so the AI
            # assistant gets a turn without re-triggering this menu handler
            # (which already subscribes to user_command).
            await self.bus.publish("user.free_text", payload)

    async def _on_callback(self, payload: Dict[str, Any]) -> None:
        data = payload.get("data") or ""
        action, target, item = self._parse_callback(data)

        if action == "menu":
            await self._route_menu(target)
        elif action == "watchlist":
            await self._route_watchlist(target, item)
        elif action == "indicators":
            await self._route_indicators(target, item)
        elif action == "timeframes":
            await self._route_timeframes(target, item)
        elif action == "models":
            await self._route_models(target, item)
        elif action == "settings":
            await self._route_settings(target)
        else:
            await self._send_text("Unknown action. Use /menu to continue.")

    async def _on_signal_generated(self, payload: Dict[str, Any]) -> None:
        signal_id = payload.get("signal_id")
        if signal_id:
            self.active_signal_ids.add(signal_id)
            self.stats["generated"] += 1
            self.stats["active"] = len(self.active_signal_ids)

    async def _on_signal_active(self, payload: Dict[str, Any]) -> None:
        signal_id = payload.get("signal_id")
        symbol = payload.get("symbol")
        direction = payload.get("direction")
        entry = payload.get("entry_price")
        gen = payload.get("generated_at")
        act = payload.get("activated_at")
        op_ts = payload.get("operator_local_timestamp")
        validity = payload.get("validity_seconds")
        age = payload.get("signal_age_seconds")
        if signal_id:
            self.active_signal_ids.add(signal_id)
            self.stats["active"] = len(self.active_signal_ids)
        text = (
            f"🔔 Signal ACTIVE: {symbol} {direction}\n"
            f"ID: {signal_id}\n"
            f"Entry: {entry}\n"
            f"Generated (UTC): {gen}\n"
            f"Activated (local): {op_ts}\n"
            f"Validity (s): {validity}\n"
            f"Age (s): {age}"
        )
        await self._send_text(text)

    async def _on_signal_closed(self, payload: Dict[str, Any]) -> None:
        signal_id = payload.get("signal_id")
        if signal_id and signal_id in self.active_signal_ids:
            self.active_signal_ids.remove(signal_id)
            self.stats["active"] = len(self.active_signal_ids)
        state = payload.get("state")
        if state == "WIN":
            self.stats["wins"] += 1
        elif state == "LOSS":
            self.stats["losses"] += 1
        elif state == "EXPIRED":
            self.stats["expired"] += 1
        elif state == "MISSED":
            self.stats["missed"] += 1
        # Notify operator with concise reason and metadata
        symbol = payload.get("symbol")
        direction = payload.get("direction")
        close_price = payload.get("close_price")
        reason = payload.get("close_reason") or state
        gen = payload.get("generated_at")
        op_ts = payload.get("operator_local_timestamp")
        validity = payload.get("validity_seconds")
        age = payload.get("signal_age_seconds")
        if state == "WIN":
            header = "🎯 Take Profit hit"
        elif state == "LOSS":
            header = "❌ Stop Loss hit"
        elif state == "EXPIRED":
            header = "⌛ Signal expired"
        else:
            header = "⚠️ Signal missed"
        text = (
            f"{header}: {symbol} {direction}\n"
            f"ID: {signal_id}\n"
            f"Close price: {close_price} \n"
            f"Reason: {reason}\n"
            f"Generated (UTC): {gen}\n"
            f"Operator local: {op_ts}\n"
            f"Validity (s): {validity}\n"
            f"Age (s): {age}"
        )
        await self._send_text(text)

    async def _route_menu(self, section: Optional[str]) -> None:
        if section == "main" or section is None:
            await self._send_main_menu()
        elif section == "signals":
            await self._send_signals_menu()
        elif section == "performance":
            await self._send_performance_menu()
        elif section == "watchlist":
            await self._send_watchlist_menu()
        elif section == "indicators":
            await self._send_indicators_menu()
        elif section == "timeframes":
            await self._send_timeframes_menu()
        elif section == "models":
            await self._send_models_menu()
        elif section == "status":
            await self._send_status_menu()
        elif section == "settings":
            await self._send_settings_menu()
        else:
            await self._send_text("Unknown menu section. Use /menu to continue.")

    async def _route_watchlist(self, action: Optional[str], symbol: Optional[str]) -> None:
        if action == "add" and symbol:
            await self._change_watchlist(symbol, add=True)
        elif action == "remove" and symbol:
            await self._change_watchlist(symbol, add=False)
        elif action == "add_menu":
            await self._send_watchlist_add_menu()
        else:
            await self._send_watchlist_menu()

    async def _route_indicators(self, action: Optional[str], indicator: Optional[str]) -> None:
        if action == "toggle" and indicator:
            await self._toggle_indicator(indicator)
        else:
            await self._send_indicators_menu()

    async def _route_timeframes(self, action: Optional[str], timeframe: Optional[str]) -> None:
        if action == "toggle" and timeframe:
            await self._toggle_timeframe(timeframe)
        else:
            await self._send_timeframes_menu()

    async def _route_models(self, action: Optional[str], model_name: Optional[str]) -> None:
        if action == "promote" and model_name:
            await self._promote_model(model_name)
        else:
            await self._send_models_menu()

    async def _route_settings(self, target: Optional[str]) -> None:
        keyboard = [
            [{"text": "Show Watchlist", "callback_data": "menu:watchlist"}],
            [{"text": "Show Indicators", "callback_data": "menu:indicators"}],
            [{"text": "Show Timeframes", "callback_data": "menu:timeframes"}],
            [{"text": "Show Models", "callback_data": "menu:models"}],
            [{"text": "⬅️ Back", "callback_data": "menu:main"}],
        ]
        await self._send_text("Settings", keyboard)

    async def _send_main_menu(self) -> None:
        await self._send_text("Main Menu", self.MAIN_MENU)

    async def _send_signals_menu(self) -> None:
        await self._send_text(
            "Signals: currently tracking active signals and lifecycle events.",
            [[{"text": "⬅️ Back", "callback_data": "menu:main"}]],
        )

    async def _send_performance_menu(self) -> None:
        win_rate = 0.0
        total_closed = self.stats["wins"] + self.stats["losses"]
        if total_closed:
            win_rate = 100.0 * self.stats["wins"] / total_closed
        text = (
            f"📊 Performance summary\n"
            f"Total generated: {self.stats['generated']}\n"
            f"Active signals: {self.stats['active']}\n"
            f"Wins: {self.stats['wins']}\n"
            f"Losses: {self.stats['losses']}\n"
            f"Expired: {self.stats['expired']}\n"
            f"Missed: {self.stats['missed']}\n"
            f"Win rate: {win_rate:.1f}%\n"
        )
        await self._send_text(text, [[{"text": "⬅️ Back", "callback_data": "menu:main"}]])

    async def _send_watchlist_menu(self) -> None:
        watchlist = await self._get_watchlist()
        keyboard = []
        if watchlist:
            for symbol in watchlist:
                keyboard.append([
                    {"text": f"Remove {symbol}", "callback_data": f"watchlist:remove:{symbol}"}
                ])
        keyboard.append([{"text": "Add symbol", "callback_data": "watchlist:add_menu"}])
        keyboard.append([{"text": "⬅️ Back", "callback_data": "menu:main"}])
        await self._send_text(
            f"Watchlist ({len(watchlist)} symbols): {', '.join(watchlist) if watchlist else 'empty'}",
            keyboard,
        )

    async def _send_watchlist_add_menu(self) -> None:
        keyboard = [
            [{"text": symbol, "callback_data": f"watchlist:add:{symbol}"}]
            for symbol in self.SYMBOL_OPTIONS
        ]
        keyboard.append([{"text": "⬅️ Back", "callback_data": "menu:watchlist"}])
        await self._send_text(
            "Select a symbol to add to the watchlist:",
            keyboard,
        )

    async def _send_indicators_menu(self) -> None:
        enabled = await self._get_enabled_indicators()
        keyboard = []
        for indicator in self.INDICATOR_OPTIONS:
            state = "✅" if indicator in enabled else "⬜"
            keyboard.append([
                {"text": f"{state} {indicator}", "callback_data": f"indicators:toggle:{indicator}"}
            ])
        keyboard.append([{"text": "⬅️ Back", "callback_data": "menu:main"}])
        await self._send_text(
            f"Indicators: {', '.join(enabled) if enabled else 'none selected'}",
            keyboard,
        )

    async def _send_timeframes_menu(self) -> None:
        enabled = await self._get_timeframes()
        keyboard = []
        for timeframe in self.TIMEFRAME_OPTIONS:
            state = "✅" if timeframe in enabled else "⬜"
            keyboard.append([
                {"text": f"{state} {timeframe}", "callback_data": f"timeframes:toggle:{timeframe}"}
            ])
        keyboard.append([{"text": "⬅️ Back", "callback_data": "menu:main"}])
        await self._send_text(
            f"Timeframes: {', '.join(enabled)}",
            keyboard,
        )

    async def _send_models_menu(self) -> None:
        chain = await self._get_model_chain()
        keyboard = [
            [{"text": f"Promote {model}", "callback_data": f"models:promote:{model}"}]
            for model in chain
        ]
        keyboard.append([{"text": "⬅️ Back", "callback_data": "menu:main"}])
        await self._send_text(
            f"Model chain: {' -> '.join(chain)}",
            keyboard,
        )

    async def _send_status_menu(self) -> None:
        enabled_adapters = self._get_enabled_adapters()
        watchlist = await self._get_watchlist()
        indicators = await self._get_enabled_indicators()
        timeframes = await self._get_timeframes()
        chain = await self._get_model_chain()
        text = (
            f"🛠️ System Status\n"
            f"Adapters: {', '.join(enabled_adapters) if enabled_adapters else 'none'}\n"
            f"Watchlist: {len(watchlist)} symbols\n"
            f"Indicators enabled: {len(indicators)}\n"
            f"Timeframes: {', '.join(timeframes)}\n"
            f"Model chain: {' -> '.join(chain)}\n"
        )
        await self._send_text(text, [[{"text": "⬅️ Back", "callback_data": "menu:main"}]])

    async def _send_settings_menu(self) -> None:
        keyboard = [
            [{"text": "Watchlist", "callback_data": "menu:watchlist"}],
            [{"text": "Indicators", "callback_data": "menu:indicators"}],
            [{"text": "Timeframes", "callback_data": "menu:timeframes"}],
            [{"text": "Models", "callback_data": "menu:models"}],
            [{"text": "⬅️ Back", "callback_data": "menu:main"}],
        ]
        await self._send_text("Settings", keyboard)

    async def _change_watchlist(self, symbol: str, add: bool) -> None:
        symbols = await self._get_watchlist()
        if add:
            if symbol not in symbols:
                symbols.append(symbol)
                await self.config.set("watchlist", symbols)
                await self._send_text(f"Added {symbol}.", [[{"text": "⬅️ Back", "callback_data": "menu:watchlist"}]])
                return
            await self._send_text(f"{symbol} is already on the watchlist.", [[{"text": "⬅️ Back", "callback_data": "menu:watchlist"}]])
            return
        if symbol in symbols:
            symbols = [s for s in symbols if s != symbol]
            await self.config.set("watchlist", symbols)
            await self._send_text(f"Removed {symbol}.", [[{"text": "⬅️ Back", "callback_data": "menu:watchlist"}]])
            return
        await self._send_text(f"{symbol} is not present on the watchlist.", [[{"text": "⬅️ Back", "callback_data": "menu:watchlist"}]])

    async def _toggle_indicator(self, indicator: str) -> None:
        enabled = await self._get_enabled_indicators()
        if indicator in enabled:
            enabled = [item for item in enabled if item != indicator]
            action = "Removed"
        else:
            enabled = enabled + [indicator]
            action = "Enabled"
        await self.config.set("features.enabled", enabled)
        await self._send_text(
            f"{action} {indicator}.",
            [[{"text": "⬅️ Back", "callback_data": "menu:indicators"}]],
        )

    async def _toggle_timeframe(self, timeframe: str) -> None:
        enabled = await self._get_timeframes()
        if timeframe in enabled:
            enabled = [item for item in enabled if item != timeframe]
            action = "Disabled"
        else:
            enabled = enabled + [timeframe]
            action = "Enabled"
        await self.config.set("data.timeframes", enabled)
        await self._send_text(
            f"{action} {timeframe}.",
            [[{"text": "⬅️ Back", "callback_data": "menu:timeframes"}]],
        )

    async def _promote_model(self, model_name: str) -> None:
        chain = await self._get_model_chain()
        if model_name not in chain:
            await self._send_text(
                f"Model {model_name} is not in the current chain.",
                [[{"text": "⬅️ Back", "callback_data": "menu:models"}]],
            )
            return
        if chain[0] == model_name:
            await self._send_text(
                f"{model_name} is already first in the chain.",
                [[{"text": "⬅️ Back", "callback_data": "menu:models"}]],
            )
            return
        reordered = [model_name] + [m for m in chain if m != model_name]
        await self.config.set("model_manager.chain", reordered)
        await self._send_text(
            f"Promoted {model_name} to the head of the chain.",
            [[{"text": "⬅️ Back", "callback_data": "menu:models"}]],
        )

    async def _send_text(self, text: str, keyboard: Optional[List[List[Dict[str, str]]]] = None) -> None:
        try:
            tg = self.adapters.get("telegram")
        except Exception:
            tg = None
        if tg and hasattr(tg, "send_message"):
            reply_markup = {"inline_keyboard": keyboard} if keyboard is not None else None
            await tg.send_message(text, reply_markup=reply_markup)

    def _parse_callback(self, data: str) -> tuple[str, Optional[str], Optional[str]]:
        parts = data.split(":", 2)
        action = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else None
        item = parts[2] if len(parts) > 2 else None
        return action, target, item

    async def _get_watchlist(self) -> List[str]:
        try:
            return self.config.get("watchlist")
        except Exception:
            return []

    async def _get_enabled_indicators(self) -> List[str]:
        try:
            return self.config.get("features.enabled")
        except Exception:
            return []

    async def _get_timeframes(self) -> List[str]:
        try:
            return self.config.get("data.timeframes")
        except Exception:
            return ["15m", "1h", "4h"]

    async def _get_model_chain(self) -> List[str]:
        try:
            chain = self.config.get("model_manager.chain")
            if isinstance(chain, list):
                return chain
        except Exception:
            pass
        return ["Claude", "OpenAI", "Gemini", "NVIDIA", "OpenRouter", "Local"]

    def _get_enabled_adapters(self) -> List[str]:
        try:
            return self.adapters.list_enabled()
        except Exception:
            return []

    async def _on_model_fallback(self, payload: Dict[str, Any]) -> None:
        frm = payload.get("from")
        reason = payload.get("reason")
        await self._send_text(f"⚠️ Model fallback: {frm} → reason={reason}")

    async def _on_model_error(self, payload: Dict[str, Any]) -> None:
        provider = payload.get("provider")
        err = payload.get("error")
        await self._send_text(f"⚠️ Model error: {provider} → {err}")

    async def _on_provider_failed(self, payload: Dict[str, Any]) -> None:
        provider = payload.get("provider")
        reason = payload.get("reason")
        ts = payload.get("timestamp")
        await self._send_text(f"⚠️ Data provider failed: {provider} — {reason} ({ts})")

    async def _on_provider_changed(self, payload: Dict[str, Any]) -> None:
        prev = payload.get("previous_provider")
        new = payload.get("new_provider")
        reason = payload.get("reason")
        await self._send_text(f"🔁 Provider switched: {prev or 'none'} → {new} ({reason})")

    async def _on_stale_detected(self, payload: Dict[str, Any]) -> None:
        provider = payload.get("provider")
        symbol = payload.get("symbol")
        timeframe = payload.get("timeframe")
        await self._send_text(f"⚠️ Stale data: {provider} for {symbol} {timeframe}")


__all__ = ["TelegramMenuService"]
