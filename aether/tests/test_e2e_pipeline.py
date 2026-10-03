"""End-to-end integration test verifying the full runtime signal pipeline."""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from aether.core.event_bus import EventBus
from aether.core.context.engine import ContextEngine
from aether.core.features.engine import FeatureEngine, IndicatorRegistry
from aether.core.features.trend import EMAIndicator
from aether.core.features.momentum_volatility import RSIIndicator, ATRIndicator, ADXIndicator
from aether.core.strategy.engine import StrategyEngine, StrategyRegistry
from aether.core.strategy.trend_following import TrendFollowingV1
from aether.core.delivery.manager import DeliveryManager
from aether.core.signal.state_manager import SignalStateManager
from aether.core.data.normalizer import NormalizedCandle


class TestEndToEndSignalPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.bus = EventBus()
        await self.bus.start()

        class MockStrategyConfig:
            def __init__(self, name, enabled):
                self.name = name
                self.enabled = enabled

        class MockConfig:
            def __init__(self):
                self.strategies = [MockStrategyConfig("trend_following_v1", True)]
                self.features = MagicMock()
                self.features.enabled = ["ema200", "rsi", "atr", "adx"]

            def get(self, key, default=None):
                if key == "validation.min_confidence":
                    return 50
                if key == "signal.operator_timezone":
                    return "Europe/London"
                if key.startswith("validation.cooldown."):
                    return 0
                if key == "validation.typical_spread_by_symbol":
                    return {"EURUSD": 0.0002}
                return default

        self.config = MockConfig()
        self.ind_registry = IndicatorRegistry()
        self.ind_registry.register(EMAIndicator(200))
        self.ind_registry.register(RSIIndicator(14))
        self.ind_registry.register(ATRIndicator(14))
        self.ind_registry.register(ADXIndicator(14))

        self.strat_registry = StrategyRegistry()
        self.strat_registry.register(TrendFollowingV1("trend_following_v1", "1.0.0", {}))

        self.context_engine = ContextEngine(self.config, self.bus)
        # Force a deterministic session for tests (independent of current UTC)
        self.context_engine._get_session_info = lambda *_args: ("London", False)
        self.feature_engine = FeatureEngine(self.ind_registry, self.config, self.bus)
        self.strategy_engine = StrategyEngine(self.strat_registry, self.config)
        self.signal_manager = SignalStateManager(self.tempdir.name, self.config)
        self.delivery_manager = DeliveryManager(self.config, self.bus)

        self.mock_adapter = AsyncMock()
        self.mock_adapter.send_signal = AsyncMock(return_value=True)

        self.mock_registry = MagicMock()
        self.mock_registry.list_enabled.return_value = ["mock_adapter"]
        self.mock_registry.get.return_value = self.mock_adapter

        self.mock_retry = MagicMock()
        self.mock_retry.enqueue = AsyncMock()

        await self.delivery_manager.setup(self.mock_registry, self.mock_retry)

        self.generated_events = []
        self.activated_events = []
        self.closed_events = []
        self.missed_events = []
        self.expired_events = []

        async def on_generated(event):
            self.generated_events.append(event)

        async def on_activated(event):
            self.activated_events.append(event)

        async def on_closed(event):
            self.closed_events.append(event)

        async def on_missed(event):
            self.missed_events.append(event)

        async def on_expired(event):
            self.expired_events.append(event)

        await self.context_engine.start()
        await self.feature_engine.start()
        await self.strategy_engine.start()
        await self.strategy_engine.register_events(self.bus)
        await self.signal_manager.register_events(self.bus)
        await self.delivery_manager.register_events(self.bus)

        await self.bus.subscribe("signal.generated", on_generated)
        await self.bus.subscribe("signal.activated", on_activated)
        await self.bus.subscribe("signal.closed", on_closed)
        await self.bus.subscribe("signal.missed", on_missed)
        await self.bus.subscribe("signal.expired", on_expired)

    async def asyncTearDown(self):
        await self.context_engine.stop()
        await self.feature_engine.stop()
        await self.strategy_engine.stop()
        await self.bus.stop()
        self.tempdir.cleanup()

    async def _publish_candles(self, count: int, start_price: float, increment: float):
        for i in range(count):
            price = start_price + (i * increment)
            candle_payload = {
                "symbol": "EURUSD",
                "timeframe": "1h",
                "open": price - 0.002,
                "high": price + 0.003,
                "low": price - 0.004,
                "close": price,
                "volume": 150.0,
                "timestamp": 1620000000.0 + (i * 3600),
                "is_closed": True,
            }
            await self.bus.publish("data.candle", candle_payload)
            await asyncio.sleep(0.0005)

    async def test_signal_activation_and_tp_close(self):
        await self._publish_quote()
        await self._publish_candles(210, 1.0, 0.00001)
        await asyncio.sleep(0.5)

        self.assertGreaterEqual(len(self.generated_events), 1)
        signal = next(e for e in reversed(self.generated_events) if e["symbol"] == "EURUSD" and e["direction"] == "BUY")
        self.assertEqual(signal["state"], "ACTIVE")
        self.assertIn("generated_at", signal)
        self.assertIn("operator_local_timestamp", signal)
        signal_id = signal["signal_id"]
        entry = signal["entry_price"]
        stop = signal["stop_loss_price"]
        target = signal["take_profit_price"]
        padding = abs(target - entry) * 0.1

        # Activate the signal on the next candle
        activation_candle = {
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": entry,
            "high": entry + padding,
            "low": entry - padding,
            "close": entry,
            "volume": 150.0,
            "timestamp": 1620000000.0 + (210 * 3600),
            "is_closed": True,
        }
        await self.bus.publish("data.candle", activation_candle)
        await asyncio.sleep(0.2)

        self.assertGreaterEqual(len(self.activated_events), 1)
        activated = next(e for e in self.activated_events if e["signal_id"] == signal_id)
        self.assertEqual(activated["state"], "ACTIVE")
        self.assertIn("activated_at", activated)

        # Close on TP with a later candle
        tp_candle = {
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": entry,
            "high": target + padding,
            "low": entry - padding,
            "close": target,
            "volume": 150.0,
            "timestamp": 1620000000.0 + (211 * 3600),
            "is_closed": True,
        }
        await self.bus.publish("data.candle", tp_candle)
        await asyncio.sleep(0.2)

        self.assertGreaterEqual(len(self.closed_events), 1)
        closed = next(e for e in self.closed_events if e["signal_id"] == signal_id)
        self.assertEqual(closed["state"], "WIN")
        self.assertEqual(closed["close_reason"], "tp")
        self.assertIn("closed_at", closed)
        self.assertTrue(self.mock_adapter.send_signal.called)

    async def test_signal_activation_and_sl_close(self):
        await self._publish_quote()
        await self._publish_candles(210, 1.0, 0.00001)
        await asyncio.sleep(0.5)

        self.assertGreaterEqual(len(self.generated_events), 1)
        signal = next(e for e in reversed(self.generated_events) if e["symbol"] == "EURUSD" and e["direction"] == "BUY")
        signal_id = signal["signal_id"]
        entry = signal["entry_price"]
        stop = signal["stop_loss_price"]
        target = signal["take_profit_price"]
        padding = abs(target - entry) * 0.1
        # emitted signals are registered ACTIVE immediately
        self.assertEqual(signal["state"], "ACTIVE")

        activation_candle = {
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": entry,
            "high": entry + padding,
            "low": entry - padding,
            "close": entry,
            "volume": 150.0,
            "timestamp": 1620000000.0 + (210 * 3600),
            "is_closed": True,
        }
        await self.bus.publish("data.candle", activation_candle)
        await asyncio.sleep(0.2)

        sl_candle = {
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": entry,
            "high": entry + padding,
            "low": stop - padding,
            "close": stop - padding,
            "volume": 150.0,
            "timestamp": 1620000000.0 + (211 * 3600),
            "is_closed": True,
        }
        await self.bus.publish("data.candle", sl_candle)
        await asyncio.sleep(0.2)

        self.assertGreaterEqual(len(self.closed_events), 1)
        closed = next(e for e in self.closed_events if e["signal_id"] == signal_id)
        self.assertEqual(closed["state"], "LOSS")
        self.assertEqual(closed["close_reason"], "sl")
        self.assertIn("closed_at", closed)
        self.assertTrue(self.mock_adapter.send_signal.called)

    async def _publish_quote(self):
        await self.bus.publish("data.quote", {
            "symbol": "EURUSD",
            "bid": 1.0999,
            "ask": 1.1001,
            "price": 1.1,
            "timestamp": int(__import__("time").time()),
            "source": "test",
        })


if __name__ == "__main__":
    unittest.main()
