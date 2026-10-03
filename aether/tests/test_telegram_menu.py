import unittest
from unittest.mock import AsyncMock, MagicMock

from aether.core.delivery.telegram_menu import TelegramMenuService


class TestTelegramMenuService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.watchlist = ["EURUSD"]
        self.features = ["ema20", "rsi"]
        self.timeframes = ["15m", "1h"]
        self.model_chain = ["Claude", "OpenAI"]

        self.config = MagicMock()
        self.config.get.side_effect = self._config_get
        self.config.set = AsyncMock()
        self.config.watch = MagicMock()

        self.bus = MagicMock()
        self.bus.subscribe = AsyncMock()

        self.tg_adapter = MagicMock()
        self.tg_adapter.send_message = AsyncMock()

        self.adapters = MagicMock()
        self.adapters.get.return_value = self.tg_adapter
        self.adapters.list_enabled.return_value = ["telegram"]

        self.menu = TelegramMenuService(self.config, self.bus, self.adapters)
        await self.menu.register_events(self.bus)

    def _config_get(self, path):
        if path == "watchlist":
            return self.watchlist
        if path == "features.enabled":
            return self.features
        if path == "data.timeframes":
            return self.timeframes
        if path == "model_manager.chain":
            return self.model_chain
        raise KeyError(path)

    async def test_register_events_subscribes(self):
        self.bus.subscribe.assert_any_await("user_callback", self.menu._on_callback)
        self.bus.subscribe.assert_any_await("signal.generated", self.menu._on_signal_generated)
        self.bus.subscribe.assert_any_await("signal.closed", self.menu._on_signal_closed)

    async def test_menu_watchlist_add_updates_config(self):
        await self.menu._on_callback({"data": "watchlist:add:GBPUSD", "user": "123"})
        self.config.set.assert_awaited_once_with("watchlist", ["EURUSD", "GBPUSD"])
        self.tg_adapter.send_message.assert_awaited()
        self.assertIn("Added GBPUSD", self.tg_adapter.send_message.call_args.args[0])

    async def test_menu_watchlist_remove_updates_config(self):
        await self.menu._on_callback({"data": "watchlist:remove:EURUSD", "user": "123"})
        self.config.set.assert_awaited_once_with("watchlist", [])
        self.assertIn("Removed EURUSD", self.tg_adapter.send_message.call_args.args[0])

    async def test_menu_indicator_toggle_updates_config(self):
        await self.menu._on_callback({"data": "indicators:toggle:rsi", "user": "123"})
        self.config.set.assert_awaited_once_with("features.enabled", ["ema20"])
        self.assertIn("Removed rsi", self.tg_adapter.send_message.call_args.args[0])

    async def test_menu_timeframe_toggle_updates_config(self):
        await self.menu._on_callback({"data": "timeframes:toggle:4h", "user": "123"})
        self.config.set.assert_awaited_once_with("data.timeframes", ["15m", "1h", "4h"])
        self.assertIn("Enabled 4h", self.tg_adapter.send_message.call_args.args[0])

    async def test_menu_model_promote_updates_config(self):
        await self.menu._on_callback({"data": "models:promote:OpenAI", "user": "123"})
        self.config.set.assert_awaited_once_with("model_manager.chain", ["OpenAI", "Claude"])
        self.assertIn("Promoted OpenAI", self.tg_adapter.send_message.call_args.args[0])

    async def test_menu_selects_an_installed_ollama_model(self):
        model_manager = MagicMock()
        model_manager.chain = ["Local"]
        model_manager.available_models = AsyncMock(return_value=["qwen2.5:3b"])
        menu = TelegramMenuService(self.config, self.bus, self.adapters, model_manager)

        await menu._on_callback({"data": "models:select_ollama:qwen2.5:3b", "user": "123"})

        self.config.set.assert_has_awaits([
            unittest.mock.call("model_manager.ollama.model", "qwen2.5:3b"),
            unittest.mock.call("model_manager.ollama.enabled", True),
            unittest.mock.call("model_manager.chain", ["Ollama", "Local"]),
        ])
        self.assertIn("Enabled local model qwen2.5:3b", self.tg_adapter.send_message.call_args.args[0])

    async def test_models_menu_lists_installed_local_models(self):
        model_manager = MagicMock()
        model_manager.chain = ["Local"]
        model_manager.available_models = AsyncMock(return_value=["qwen2.5:3b"])
        menu = TelegramMenuService(self.config, self.bus, self.adapters, model_manager)

        await menu._send_models_menu()

        text = self.tg_adapter.send_message.call_args.args[0]
        markup = self.tg_adapter.send_message.call_args.kwargs["reply_markup"]
        self.assertIn("Installed local models: qwen2.5:3b", text)
        self.assertTrue(any(
            button["callback_data"] == "models:select_ollama:qwen2.5:3b"
            for row in markup["inline_keyboard"]
            for button in row
        ))

    async def test_alert_events_are_delivered_as_operator_messages(self):
        await self.menu._on_alert_triggered({
            "kind": "price", "symbol": "EURUSD", "condition": "above",
            "target": 1.1, "price": 1.1002,
        })
        self.assertIn("Price alert: EURUSD above 1.1", self.tg_adapter.send_message.call_args.args[0])

        await self.menu._on_alert_triggered({
            "kind": "news", "currency": "USD", "pairs": ["EURUSD"],
            "impact": "high", "title": "CPI", "timestamp": 1900000000,
        })
        self.assertIn("High impact news for EURUSD: CPI", self.tg_adapter.send_message.call_args.args[0])

    async def test_performance_menu_reports_counts(self):
        await self.menu._on_signal_generated({"signal_id": "sig-001"})
        await self.menu._on_signal_closed({"signal_id": "sig-001", "state": "WIN"})
        await self.menu._on_callback({"data": "menu:performance", "user": "123"})
        self.assertIn("Total generated: 1", self.tg_adapter.send_message.call_args.args[0])
        self.assertIn("Wins: 1", self.tg_adapter.send_message.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
