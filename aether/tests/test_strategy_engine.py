import unittest
from aether.core.strategy.engine import StrategyEngine, StrategyRegistry
from aether.core.strategy.trend_following import TrendFollowingV1
from aether.core.context.models import MarketContext, NewsRisk
from aether.core.features.base import FeatureValue
from aether.core.data.normalizer import NormalizedCandle

class TestStrategyEngine(unittest.TestCase):
    def setUp(self):
        self.registry = StrategyRegistry()
        self.strategy = TrendFollowingV1("trend_following_v1", "1.0.0", {})
        self.registry.register(self.strategy)

        class MockConfig:
            class strategies:
                # This is simplified for the test, should be a list of objects
                pass

        # Manually setup the enabled strategies list
        class MockConfigReal:
            def __init__(self):
                self.strategies = [type('S', (), {'name': 'trend_following_v1', 'enabled': True})()]

        self.engine = StrategyEngine(self.registry, MockConfigReal())

    def test_generate_candidate(self):
        context = MarketContext(
            directional_state="Bullish",
            volatility_state="Normal",
            session="London",
            session_transition=False,
            news_risk=NewsRisk(active=False),
            liquidity_state="High",
            market_structure="Higher Highs"
        )
        features = {
            "ema200": FeatureValue(value=1.0, lookback_required=200, is_valid=True, category="trend"),
            "adx": FeatureValue(value=30, lookback_required=14, is_valid=True, category="trend"),
            "rsi": FeatureValue(value=50, lookback_required=14, is_valid=True, category="momentum"),
            "atr": FeatureValue(value=0.002, lookback_required=14, is_valid=True, category="volatility")
        }
        last_candle = NormalizedCandle(symbol="EURUSD", timeframe="1h", open=1.08, high=1.09, low=1.07, close=1.085, volume=100, timestamp=0, close_time=0, source="oanda", is_closed=True)

        candidate = self.engine.generate_candidate("EURUSD", "1h", context, features, last_candle)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["direction"], "BUY")
        self.assertTrue(candidate["score"] > 60)

    def test_snapshot_identity_is_stable_and_input_sensitive(self):
        snapshot = {"features": {"rsi": 51.0}, "candle": {"timestamp": 123}}
        first_id, first_hash = self.engine._signal_identity("EURUSD", "1h", snapshot)
        reordered_id, reordered_hash = self.engine._signal_identity(
            "EURUSD", "1h", {"candle": {"timestamp": 123}, "features": {"rsi": 51.0}}
        )
        changed_id, changed_hash = self.engine._signal_identity(
            "EURUSD", "1h", {"candle": {"timestamp": 123}, "features": {"rsi": 52.0}}
        )

        self.assertEqual((first_id, first_hash), (reordered_id, reordered_hash))
        self.assertNotEqual((first_id, first_hash), (changed_id, changed_hash))

    def test_context_and_feature_timestamps_must_match(self):
        self.engine._context_timestamps["EURUSD:1h"] = 100
        self.engine._feature_timestamps["EURUSD:1h"] = 101
        self.assertFalse(self.engine._snapshots_match("EURUSD:1h"))
        self.engine._feature_timestamps["EURUSD:1h"] = 100
        self.assertTrue(self.engine._snapshots_match("EURUSD:1h"))


class TestCandleIntake(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = StrategyEngine(StrategyRegistry(), object())

    async def test_only_closed_valid_and_non_stale_candles_are_cached(self):
        base = {
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": 1.08,
            "high": 1.09,
            "low": 1.07,
            "close": 1.085,
            "volume": 100,
            "timestamp": 100,
            "close_time": 101,
            "source": "test-provider",
            "is_closed": True,
        }

        await self.engine._on_candle({**base, "is_closed": False})
        await self.engine._on_candle({**base, "high": 1.06})
        self.assertNotIn("EURUSD:1h", self.engine._candle_cache)

        await self.engine._on_candle(base)
        await self.engine._on_candle({**base, "timestamp": 99, "source": "older-provider"})

        candle = self.engine._candle_cache["EURUSD:1h"]
        self.assertEqual(candle.timestamp, 100)
        self.assertEqual(candle.close_time, 101)
        self.assertEqual(candle.source, "test-provider")

if __name__ == "__main__":
    unittest.main()
