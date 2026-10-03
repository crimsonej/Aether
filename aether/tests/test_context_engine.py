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
        self.assertEqual(context.directional_state, "Neutral")
        self.assertEqual(context.market_structure, "Consolidation")
        self.assertIsInstance(context.news_risk, type(context.news_risk))

    @staticmethod
    def _candles_from_closes(closes):
        return [NormalizedCandle(
            symbol="EURUSD", timeframe="1h", open=close, high=close + 0.01,
            low=close - 0.01, close=close, volume=100, timestamp=index,
            close_time=index, source="test", is_closed=True,
        ) for index, close in enumerate(closes)]

    def test_direction_uses_rolling_ema_trend_not_last_candle_color(self):
        rising = self._candles_from_closes([1.0 + index * 0.001 for index in range(80)])
        falling = self._candles_from_closes([1.1 - index * 0.001 for index in range(80)])
        ranging = self._candles_from_closes([1.0] * 80)
        rising[-1] = rising[-1].model_copy(update={"open": rising[-1].close - 0.02})
        falling[-1] = falling[-1].model_copy(update={"open": falling[-1].close + 0.02})

        self.assertEqual(self.engine._get_directional_state(rising), "Bullish")
        self.assertEqual(self.engine._get_directional_state(falling), "Bearish")
        self.assertEqual(self.engine._get_directional_state(ranging), "Neutral")

    def test_market_structure_detects_higher_lows_and_break_of_structure(self):
        candles = []
        for cycle in range(8):
            base = 1.0 + cycle * 0.2
            highs = [0.04, 0.06, 0.08, 0.11, 0.09, 0.07, 0.08, 0.10]
            lows = [0.00, 0.02, 0.04, 0.05, 0.03, -0.02, 0.02, 0.04]
            for high_offset, low_offset in zip(highs, lows):
                close = base + (high_offset + low_offset) / 2
                index = len(candles)
                candles.append(NormalizedCandle(
                    symbol="EURUSD", timeframe="1h", open=close,
                    high=base + high_offset, low=base + low_offset,
                    close=close, volume=100, timestamp=index, close_time=index,
                    source="test", is_closed=True,
                ))

        self.assertEqual(self.engine._get_market_structure(candles), "Higher Highs")
        breakout_close = candles[-1].high + 0.05
        breakout = NormalizedCandle(
            symbol="EURUSD", timeframe="1h", open=breakout_close - 0.005,
            high=breakout_close + 0.005, low=breakout_close - 0.01,
            close=breakout_close, volume=100, timestamp=len(candles),
            close_time=len(candles), source="test", is_closed=True,
        )
        self.assertEqual(self.engine._get_market_structure(candles + [breakout]), "BOS")

    def test_rolling_range_breakout_is_bos_without_prior_swing(self):
        closes = [1.0 + index * 0.0001 for index in range(30)]
        candles = self._candles_from_closes(closes)
        last = candles[-1]
        breakout_close = max(candle.high for candle in candles[-20:]) + 0.02
        candles.append(NormalizedCandle(
            symbol="EURUSD", timeframe="1h", open=last.close,
            high=breakout_close + 0.005, low=last.close - 0.005,
            close=breakout_close, volume=100, timestamp=30, close_time=30,
            source="test", is_closed=True,
        ))

        self.assertEqual(self.engine._get_market_structure(candles), "BOS")

    def test_volatility_state_tracks_atr_percentile(self):
        candles = []
        price = 1.0
        for index in range(80):
            width = 0.001 if index < 79 else 0.1
            candles.append(NormalizedCandle(
                symbol="EURUSD", timeframe="1h", open=price, high=price + width,
                low=price - width, close=price, volume=100, timestamp=index,
                close_time=index, source="test", is_closed=True,
            ))
        self.assertEqual(self.engine._get_volatility_state(candles[:78]), "Normal")
        self.assertEqual(self.engine._get_volatility_state(candles), "Extreme")


class TestContextWarmup(unittest.IsolatedAsyncioTestCase):
    async def test_batch_history_seeds_context_before_latest_bar(self):
        engine = ContextEngine({})
        payloads = []
        for index in range(59):
            close = 1.0 + index * 0.001
            payloads.append({
                "symbol": "EURUSD", "timeframe": "1h", "open": close,
                "high": close + 0.0002, "low": close - 0.0002, "close": close,
                "volume": 10, "timestamp": index, "close_time": index,
                "source": "test", "is_closed": True,
            })
        latest = {
            **payloads[-1], "timestamp": 59, "close_time": 59,
            "open": 1.059, "high": 1.0592, "low": 1.0588, "close": 1.059,
        }

        await engine._on_candle({**latest, "warmup_candles": payloads})

        history = engine._candle_cache["EURUSD:1h"]
        self.assertEqual(len(history), 60)
        self.assertEqual(history[-1].timestamp, 59)
        self.assertEqual(engine._get_directional_state(history), "Bullish")

if __name__ == "__main__":
    unittest.main()
