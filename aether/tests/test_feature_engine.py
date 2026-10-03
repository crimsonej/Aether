import unittest
from aether.core.features.engine import FeatureEngine, IndicatorRegistry
from aether.core.features.trend import EMAIndicator
from aether.core.features.momentum_volatility import ADXIndicator, MACDIndicator, RSIIndicator, ATRIndicator
from aether.core.data.normalizer import NormalizedCandle

class TestFeatureEngine(unittest.TestCase):
    def setUp(self):
        self.registry = IndicatorRegistry()
        self.registry.register(EMAIndicator(20))
        self.registry.register(RSIIndicator())
        self.registry.register(ATRIndicator())

        # Mock config implementing the standardized .get(path) API
        class MockConfig:
            def get(self, path, default=None):
                if path == "features.enabled":
                    return ["ema20", "rsi", "atr"]
                return default

        self.engine = FeatureEngine(self.registry, MockConfig())

    def test_compute_features(self):
        candles = [NormalizedCandle(symbol="EURUSD", timeframe="1h", open=1.0, high=1.1, low=0.9, close=1.0, volume=100, timestamp=i, close_time=i, source="oanda", is_closed=True) for i in range(100)]

        features = self.engine.compute_features("EURUSD", "1h", candles)

        self.assertIn("ema20", features)
        self.assertIn("rsi", features)
        self.assertIn("atr", features)
        self.assertTrue(features["ema20"].is_valid)
        self.assertTrue(features["rsi"].is_valid)

    def test_adx_and_macd_use_market_data_and_enforce_warmup(self):
        upward = [
            NormalizedCandle(
                symbol="EURUSD", timeframe="1h", open=float(index), high=index + 1.5,
                low=index - 0.5, close=float(index + 1), volume=100,
                timestamp=index, close_time=index, source="test", is_closed=True,
            )
            for index in range(60)
        ]
        downward = [
            candle.model_copy(update={
                "open": -candle.open,
                "high": -candle.low,
                "low": -candle.high,
                "close": -candle.close,
            })
            for candle in upward
        ]
        adx = ADXIndicator(14)
        macd = MACDIndicator(12, 26, 9)

        self.assertFalse(adx.compute(upward[:27]).is_valid)
        self.assertFalse(macd.compute(upward[:33]).is_valid)
        self.assertGreater(adx.compute(upward).value, 90)
        self.assertGreater(macd.compute(upward).value, 0)
        self.assertLess(macd.compute(downward).value, 0)

    def test_feature_result_cache_is_bounded(self):
        engine = FeatureEngine(IndicatorRegistry(), self.engine.config, cache_max_entries=2)
        for timestamp in range(3):
            candle = NormalizedCandle(
                symbol="EURUSD", timeframe="1h", open=1.0, high=1.1, low=0.9,
                close=1.0, volume=1.0, timestamp=timestamp, close_time=timestamp,
                source="test", is_closed=True,
            )
            engine.compute_features("EURUSD", "1h", [candle])

        self.assertEqual(len(engine.cache), 2)
        self.assertNotIn(("EURUSD", "1h", 0), engine.cache)


class TestFeatureCandleIntake(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_and_open_candles_do_not_expand_history(self):
        engine = FeatureEngine(IndicatorRegistry(), object())
        candle = {
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 10,
            "timestamp": 100,
            "source": "test",
            "is_closed": True,
        }

        await engine._on_candle({**candle, "is_closed": False})
        await engine._on_candle(candle)
        await engine._on_candle({**candle, "close": 1.06})

        history = engine._candle_cache["EURUSD:1h"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].close, 1.06)

if __name__ == "__main__":
    unittest.main()
