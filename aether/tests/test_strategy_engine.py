import unittest
import time
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

    def test_quote_history_provides_typical_spread_when_config_missing(self):
        self.engine._quote_spread_history["EURUSD"] = [0.00008, 0.00010, 0.00012]

        typical = self.engine._resolve_typical_spread("EURUSD")

        self.assertAlmostEqual(typical, 0.00010)

    def test_context_and_feature_timestamps_must_match(self):
        self.engine._context_timestamps["EURUSD:1h"] = 100
        self.engine._feature_timestamps["EURUSD:1h"] = 101
        self.assertFalse(self.engine._snapshots_match("EURUSD:1h"))
        self.engine._feature_timestamps["EURUSD:1h"] = 100
        self.assertTrue(self.engine._snapshots_match("EURUSD:1h"))

    def test_symbol_readiness_reports_quote_and_spread_status(self):
        self.engine._quote_spread_history["EURUSD"] = [0.00008, 0.00010, 0.00012]
        self.engine._quote_cache["EURUSD"] = {"spread": 0.00009, "timestamp": int(time.time())}

        ready = self.engine.symbol_readiness("EURUSD")
        self.assertTrue(ready["ready"])
        self.assertEqual(ready["reason"], "fresh_quote_and_valid_spread")

        no_quote = self.engine.symbol_readiness("GBPUSD")
        self.assertFalse(no_quote["ready"])
        self.assertEqual(no_quote["reason"], "missing_quote")

    def test_registry_registers_approved_builtin_profiles(self):
        names = {
            "trend_following_v1",
            "fvg",
            "demand_supply",
            "liquidity",
            "support_resistance",
            "order_block",
        }
        for name in names:
            self.assertIsNotNone(self.registry.get_strategy(name), f"missing strategy profile: {name}")


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

    async def test_quote_cache_accepts_only_fresh_valid_bid_ask(self):
        await self.engine._on_quote({
            "symbol": "EURUSD", "bid": 1.1, "ask": 1.1002,
            "timestamp": int(time.time()), "source": "test",
        })
        self.assertIn("EURUSD", self.engine._quote_cache)

        await self.engine._on_quote({
            "symbol": "GBPUSD", "bid": 1.2, "ask": 1.2002,
            "timestamp": int(time.time()) - 10000, "source": "test",
        })
        await self.engine._on_quote({
            "symbol": "USDJPY", "bid": 150.0, "ask": 149.0,
            "timestamp": int(time.time()), "source": "test",
        })
        self.assertNotIn("GBPUSD", self.engine._quote_cache)
        self.assertNotIn("USDJPY", self.engine._quote_cache)

if __name__ == "__main__":
    unittest.main()
