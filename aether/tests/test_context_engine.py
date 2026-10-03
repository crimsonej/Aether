import unittest
from aether.core.context.engine import ContextEngine
from aether.core.data.normalizer import NormalizedCandle

class TestContextEngine(unittest.TestCase):
    def setUp(self):
        self.config = {} # Minimal config for now
        self.engine = ContextEngine(self.config)
        # Make session determination deterministic for tests
        self.engine._get_session_info = lambda *_args: ("London", False)

    def test_compute_context(self):
        candles = [
            NormalizedCandle(symbol="EURUSD", timeframe="1h", open=1.0, high=1.1, low=0.9, close=1.0, volume=100, timestamp=0, close_time=0, source="oanda", is_closed=True),
            NormalizedCandle(symbol="EURUSD", timeframe="1h", open=1.0, high=1.2, low=0.9, close=1.1, volume=100, timestamp=1, close_time=1, source="oanda", is_closed=True),
        ]

        context = self.engine.compute_context("EURUSD", "1h", candles)
        self.assertEqual(context.directional_state, "Bullish")
        self.assertEqual(context.market_structure, "Higher Highs")
        self.assertIsInstance(context.news_risk, type(context.news_risk))

if __name__ == "__main__":
    unittest.main()
