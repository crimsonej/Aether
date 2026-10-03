"""Tests for the delivery layer – DeliveryManager and AetherExplainer."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aether.core.llm.explainer import AetherExplainer
from aether.core.delivery.manager import DeliveryManager


# ---------------------------------------------------------------------------
# AetherExplainer – pure unit test (no I/O)
# ---------------------------------------------------------------------------
class TestAetherExplainer(unittest.TestCase):
    def test_explain_signal_returns_explanation(self):
        explainer = AetherExplainer("MOCK_KEY")
        signal = {
            "signal_id": "test-123",
            "symbol": "EURUSD",
            "direction": "BUY",
            "confidence": {"adjusted": 75},
            "reason_tags": ["ema_alignment", "rsi_confirmation"],
        }
        result = explainer.explain_signal(signal)
        self.assertIn("75%", result.confidence_analysis)
        self.assertEqual(result.signal_id, "test-123")
        self.assertIn("BUY", result.explanation)

    def test_explain_signal_direction_appears_in_explanation(self):
        explainer = AetherExplainer("MOCK_KEY")
        signal = {
            "signal_id": "test-456",
            "symbol": "GBPUSD",
            "direction": "SELL",
            "confidence": {"adjusted": 60},
            "reason_tags": ["macd_cross"],
        }
        result = explainer.explain_signal(signal)
        self.assertIn("SELL", result.explanation)


# ---------------------------------------------------------------------------
# DeliveryManager – async unit test
# ---------------------------------------------------------------------------
class TestDeliveryManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = MagicMock()
        self.bus = MagicMock()
        self.bus.subscribe = AsyncMock()
        self.manager = DeliveryManager(self.config, self.bus)

    async def test_register_events_subscribes_to_signal_emitted(self):
        """DeliveryManager must subscribe to 'signal.emitted'."""
        await self.manager.register_events(self.bus)
        # DeliveryManager subscribes to signal lifecycle events (generated/activated/missed/closed/expired).
        # Verify the handler was registered for at least one lifecycle event.
        self.bus.subscribe.assert_any_await("signal.generated", self.manager._handle_signal)

    async def test_handle_signal_no_registry_logs_error(self):
        """_handle_signal must not crash when dependencies are not set up."""
        # Dependencies not injected – should log an error gracefully.
        with patch("aether.core.delivery.manager.logger") as mock_logger:
            await self.manager._handle_signal({"signal_id": "x", "symbol": "EURUSD"})
            mock_logger.error.assert_called_once()

    async def test_handle_signal_dispatches_to_adapter(self):
        """_handle_signal must call adapter.send_signal for each enabled adapter."""
        mock_adapter = AsyncMock()
        mock_adapter.send_signal = AsyncMock(return_value=True)

        mock_registry = MagicMock()
        mock_registry.list_enabled.return_value = ["telegram"]
        mock_registry.get.return_value = mock_adapter

        mock_retry = MagicMock()
        mock_retry.enqueue = AsyncMock()

        await self.manager.setup(mock_registry, mock_retry)

        signal = {
            "signal_id": "sig-001",
            "symbol": "EURUSD",
            "direction": "BUY",
            "confidence": {"adjusted": 80},
        }
        await self.manager._handle_signal(signal)

        mock_adapter.send_signal.assert_awaited_once_with(signal)
        mock_retry.enqueue.assert_not_awaited()

    async def test_handle_signal_enqueues_on_failure(self):
        """Failed sends must be pushed to the RetryQueue."""
        mock_adapter = AsyncMock()
        mock_adapter.send_signal = AsyncMock(return_value=False)

        mock_registry = MagicMock()
        mock_registry.list_enabled.return_value = ["telegram"]
        mock_registry.get.return_value = mock_adapter

        mock_retry = MagicMock()
        mock_retry.enqueue = AsyncMock()

        await self.manager.setup(mock_registry, mock_retry)

        signal = {"signal_id": "sig-002", "symbol": "GBPUSD"}
        await self.manager._handle_signal(signal)

        mock_retry.enqueue.assert_awaited_once_with("telegram", signal)


if __name__ == "__main__":
    unittest.main()
