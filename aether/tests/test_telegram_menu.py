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

    async def test_performance_menu_reports_counts(self):
        await self.menu._on_signal_generated({"signal_id": "sig-001"})
        await self.menu._on_signal_closed({"signal_id": "sig-001", "state": "WIN"})
        await self.menu._on_callback({"data": "menu:performance", "user": "123"})
        self.assertIn("Total generated: 1", self.tg_adapter.send_message.call_args.args[0])
        self.assertIn("Wins: 1", self.tg_adapter.send_message.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
