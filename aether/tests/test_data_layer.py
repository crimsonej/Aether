import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from aether.core.data.oanda_client import OandaClient
from aether.core.data.validator import MarketDataValidator
from aether.core.data.normalizer import NormalizedCandle

class TestMarketDataLayer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Mock the secrets to avoid EnvironmentError
        self.patcher = patch('aether.core.data.oanda_client.get_secret', return_value="mock_secret")
        self.patcher.start()

        self.client = OandaClient("http://mock", "KEY", "ACC")
        self.validator = MarketDataValidator()

    async def asyncTearDown(self):
        self.patcher.stop()

    async def test_normalization(self):
        # Mock Oanda response
        mock_response = [
            {
                "time": "2025-01-15T14:30:00Z",
                "mid": {"o": 1.0842, "h": 1.0850, "l": 1.0835, "c": 1.0845, "v": 100},
                "complete": True,
                "volume": 100
            }
        ]

        with patch.object(self.client, 'get_candles', new_callable=AsyncMock, return_value=mock_response):
            candles = await self.client.fetch_normalized_candles("EURUSD", "1h")
            self.assertEqual(len(candles), 1)
            self.assertEqual(candles[0].symbol, "EURUSD")
            self.assertEqual(candles[0].close, 1.0845)
            self.assertTrue(candles[0].is_closed)

    def test_circuit_breaker(self):
        symbol = "EURUSD"
        self.assertFalse(self.validator.is_circuit_broken(symbol))

        for _ in range(5):
            self.validator.record_failure(symbol)

        self.assertTrue(self.validator.is_circuit_broken(symbol))

    def test_warmup_validation(self):
        # 10 candles should fail EMA200
        candles_10 = [NormalizedCandle(symbol="EURUSD", timeframe="1h", open=1.0, high=1.1, low=0.9, close=1.0, volume=100, timestamp=0, close_time=0, source="oanda", is_closed=True)] * 10
        self.assertFalse(self.validator.validate_warmup(candles_10, "EMA200"))

        # 50 candles should pass default warmup
        candles_50 = [NormalizedCandle(symbol="EURUSD", timeframe="1h", open=1.0, high=1.1, low=0.9, close=1.0, volume=100, timestamp=0, close_time=0, source="oanda", is_closed=True)] * 50
        self.assertTrue(self.validator.validate_warmup(candles_50))

if __name__ == "__main__":
    unittest.main()
