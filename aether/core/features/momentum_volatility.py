import numpy as np
from aether.core.features.base import Indicator, FeatureValue
from aether.core.data.normalizer import NormalizedCandle

class RSIIndicator(Indicator):
    def __init__(self, period: int = 14):
        super().__init__("rsi", "momentum", period)
        self.period = period

    def compute(self, candles: list) -> FeatureValue:
        if len(candles) < self.lookback + 1:
            return FeatureValue(value=0.0, lookback_required=self.lookback, is_valid=False, category=self.category)

        closes = np.array([c.close for c in candles])
        diffs = np.diff(closes)

        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)

        avg_gain = np.mean(gains[-self.period:])
        avg_loss = np.mean(losses[-self.period:])

        if avg_loss == 0:
            rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        return FeatureValue(value=float(rsi), lookback_required=self.lookback, is_valid=True, category=self.category)

class ATRIndicator(Indicator):
    def __init__(self, period: int = 14):
        super().__init__("atr", "volatility", period)
        self.period = period

    def compute(self, candles: list) -> FeatureValue:
        if len(candles) < self.lookback + 1:
            return FeatureValue(value=0.0, lookback_required=self.lookback, is_valid=False, category=self.category)

        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        closes = np.array([c.close for c in candles])

        tr = np.maximum(highs[1:] - lows[1:],
                        np.maximum(np.abs(highs[1:] - closes[:-1]),
                                   np.abs(lows[1:] - closes[:-1])))

        atr = np.mean(tr[-self.period:])

        return FeatureValue(value=float(atr), lookback_required=self.lookback, is_valid=True, category=self.category)

class ADXIndicator(Indicator):
    def __init__(self, period: int = 14):
        super().__init__("adx", "trend", period * 2)
        self.period = period

    def compute(self, candles: list) -> FeatureValue:
        invalid = FeatureValue(value=0.0, lookback_required=self.lookback, is_valid=False, category=self.category)
        if len(candles) < self.lookback:
            return invalid

        highs = np.asarray([c.high for c in candles], dtype=float)
        lows = np.asarray([c.low for c in candles], dtype=float)
        closes = np.asarray([c.close for c in candles], dtype=float)
        up_moves = np.diff(highs)
        down_moves = -np.diff(lows)
        plus_dm = np.where((up_moves > down_moves) & (up_moves > 0), up_moves, 0.0)
        minus_dm = np.where((down_moves > up_moves) & (down_moves > 0), down_moves, 0.0)
        true_range = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
        )

        smoothed_tr = float(np.sum(true_range[:self.period]))
        smoothed_plus = float(np.sum(plus_dm[:self.period]))
        smoothed_minus = float(np.sum(minus_dm[:self.period]))
        dx_values = []
        for index in range(self.period, len(true_range)):
            smoothed_tr = smoothed_tr - smoothed_tr / self.period + true_range[index]
            smoothed_plus = smoothed_plus - smoothed_plus / self.period + plus_dm[index]
            smoothed_minus = smoothed_minus - smoothed_minus / self.period + minus_dm[index]
            if smoothed_tr == 0:
                dx_values.append(0.0)
                continue
            plus_di = 100.0 * smoothed_plus / smoothed_tr
            minus_di = 100.0 * smoothed_minus / smoothed_tr
            denominator = plus_di + minus_di
            dx_values.append(100.0 * abs(plus_di - minus_di) / denominator if denominator else 0.0)

        initial_dx = []
        if len(true_range) >= self.period:
            first_plus = float(np.sum(plus_dm[:self.period]))
            first_minus = float(np.sum(minus_dm[:self.period]))
            first_tr = float(np.sum(true_range[:self.period]))
            if first_tr:
                plus_di = 100.0 * first_plus / first_tr
                minus_di = 100.0 * first_minus / first_tr
                denominator = plus_di + minus_di
                initial_dx.append(100.0 * abs(plus_di - minus_di) / denominator if denominator else 0.0)
        values = initial_dx + dx_values
        if len(values) < self.period:
            return invalid
        adx = float(np.mean(values[-self.period:]))
        return FeatureValue(value=adx, lookback_required=self.lookback, is_valid=True, category=self.category)

class MACDIndicator(Indicator):
    def __init__(self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9):
        super().__init__("macd", "momentum", slow_period + signal_period - 1)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    def compute(self, candles: list) -> FeatureValue:
        if len(candles) < self.lookback:
            return FeatureValue(value=0.0, lookback_required=self.lookback, is_valid=False, category=self.category)

        closes = np.asarray([c.close for c in candles], dtype=float)
        fast = self._ema(closes, self.fast_period)
        slow = self._ema(closes, self.slow_period)
        macd_line = fast - slow
        signal_line = self._ema(macd_line, self.signal_period)
        return FeatureValue(
            value=float(macd_line[-1] - signal_line[-1]),
            lookback_required=self.lookback,
            is_valid=True,
            category=self.category,
        )

    @staticmethod
    def _ema(values: np.ndarray, period: int) -> np.ndarray:
        alpha = 2.0 / (period + 1.0)
        result = np.empty_like(values, dtype=float)
        result[0] = values[0]
        for index in range(1, len(values)):
            result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
        return result
