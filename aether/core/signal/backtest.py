"""Historical replay using Aether's runtime context, feature, strategy, and validation logic."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from aether.core.context.engine import ContextEngine
from aether.core.data.normalizer import NormalizedCandle
from aether.core.features.engine import FeatureEngine, IndicatorRegistry
from aether.core.features.momentum_volatility import ADXIndicator, ATRIndicator, MACDIndicator, RSIIndicator
from aether.core.features.trend import EMAIndicator
from aether.core.strategy.engine import StrategyEngine, StrategyRegistry
from aether.core.strategy.trend_following import TrendFollowingV1
from aether.core.strategy.validation import ValidationFirewall
from aether.core.utils.logger import logger


class BacktestEngine:
    """Replay validated historical candles without consulting future bars."""

    def __init__(self, data_path: str, config):
        self.data_path = Path(data_path)
        self.config = config
        self._historical_spreads: Dict[int, float] = {}

    def run_replay(self, symbol: str, timeframe: str, start_date: str, end_date: str):
        logger.info("backtest_started", symbol=symbol, timeframe=timeframe, start=start_date, end=end_date)
        start_ts = self._parse_bound(start_date, end_of_day=False)
        end_ts = self._parse_bound(end_date, end_of_day=True)
        if start_ts > end_ts:
            raise ValueError("start_date must not be after end_date")

        candles = self._load_historical_data(symbol, timeframe)
        candles = [c for c in candles if c.timestamp <= end_ts]
        if not candles:
            return self._report([])

        context_engine = ContextEngine(self.config)
        feature_engine = self._make_feature_engine()
        strategy_engine = self._make_strategy_engine()
        firewall = ValidationFirewall(self.config)
        warmup = max((indicator.lookback for indicator in feature_engine.registry._indicators.values()), default=1) + 1
        timeframe_seconds = self._timeframe_seconds(timeframe)
        cooldown_until: Dict[str, int] = {}
        outcomes = []

        for index in range(warmup - 1, len(candles) - 1):
            candle = candles[index]
            if candle.timestamp < start_ts:
                continue

            history = candles[:index + 1]
            context = context_engine.compute_context(symbol, timeframe, history)
            features = feature_engine.compute_features(symbol, timeframe, history)
            candidate = strategy_engine.generate_candidate(symbol, timeframe, context, features, candle)
            if candidate is None:
                continue

            candidate["current_spread"] = self._historical_spreads.get(candle.timestamp)
            typical_spreads = self._config_get("validation.typical_spread_by_symbol", {}) or {}
            candidate["typical_spread"] = typical_spreads.get(symbol)

            validation = firewall.validate(candidate)
            if not validation["valid"]:
                continue

            strategy_name = candidate.get("strategy_name", "unknown")
            key = f"{symbol}:{candidate['direction']}:{strategy_name}"
            if candle.timestamp < cooldown_until.get(key, 0):
                continue
            cooldown_until[key] = candle.timestamp + self._cooldown_seconds(context, timeframe_seconds)

            outcome = self._resolve_outcome(candidate, candles, index, timeframe_seconds)
            outcomes.append(outcome)

        logger.info("backtest_completed", symbol=symbol, timeframe=timeframe, signals=len(outcomes))
        return self._report(outcomes)

    def _make_feature_engine(self) -> FeatureEngine:
        registry = IndicatorRegistry()
        for indicator in (
            EMAIndicator(20), EMAIndicator(50), EMAIndicator(200),
            RSIIndicator(14), ATRIndicator(14), ADXIndicator(14), MACDIndicator(12, 26, 9),
        ):
            registry.register(indicator)
        return FeatureEngine(registry, self.config)

    def _make_strategy_engine(self) -> StrategyEngine:
        strategy_config: Dict[str, Any] = {}
        strategies = self._config_get("strategies", []) or []
        for item in strategies:
            item = item if isinstance(item, dict) else getattr(item, "__dict__", {})
            if item.get("name") != "trend_following_v1" or not item.get("enabled", False):
                continue
            config_path = item.get("config_path")
            if config_path:
                path = self.data_path.parent / config_path
                if path.is_file():
                    try:
                        import yaml
                        strategy_config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    except Exception as exc:
                        logger.warning("backtest_strategy_config_failed", path=str(path), error=str(exc))
            break
        registry = StrategyRegistry()
        registry.register(TrendFollowingV1("trend_following_v1", "1.0.0", strategy_config))
        return StrategyEngine(registry, self.config)

    def _load_historical_data(self, symbol: str, timeframe: str) -> List[NormalizedCandle]:
        stem = f"{symbol}_{timeframe}"
        json_path = self.data_path / f"{stem}.json"
        csv_path = self.data_path / f"{stem}.csv"
        try:
            if json_path.is_file():
                rows = json.loads(json_path.read_text(encoding="utf-8"))
            elif csv_path.is_file():
                with csv_path.open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            else:
                return []
        except (OSError, ValueError, csv.Error) as exc:
            logger.error("historical_load_failed", error=str(exc), symbol=symbol, timeframe=timeframe)
            return []

        candles = []
        self._historical_spreads = {}
        for row in rows:
            try:
                timestamp = int(float(row["timestamp"]))
                if isinstance(row.get("close_time"), str) and row["close_time"]:
                    close_time = int(float(row["close_time"]))
                else:
                    close_time = timestamp
                row = {**row, "timestamp": timestamp, "close_time": close_time}
                row.setdefault("source", "backtest")
                row.setdefault("is_closed", True)
                candle = NormalizedCandle(**row)
                if not candle.is_closed:
                    continue
                if not all(map(self._finite, (candle.open, candle.high, candle.low, candle.close, candle.volume))):
                    continue
                if candle.volume < 0 or candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close):
                    continue
                spread = self._finite(row.get("spread", row.get("current_spread")))
                if spread is not None and spread >= 0:
                    self._historical_spreads[candle.timestamp] = spread
                candles.append(candle)
            except (KeyError, TypeError, ValueError):
                continue
        candles.sort(key=lambda candle: candle.timestamp)
        return [candle for i, candle in enumerate(candles) if i == 0 or candle.timestamp > candles[i - 1].timestamp]

    def _resolve_outcome(self, candidate: Dict[str, Any], candles: List[NormalizedCandle], index: int, timeframe_seconds: int):
        trade = candidate["trade"]
        entry = float(trade["entry"]["price"])
        stop = float(trade["stop_loss"]["price"])
        target = float(trade["take_profit"]["price"])
        direction = candidate["direction"]
        risk = abs(entry - stop)
        horizon = self._duration_seconds(trade.get("expiry")) or timeframe_seconds * 24
        deadline = candles[index].timestamp + horizon

        for future in candles[index + 1:]:
            if future.timestamp > deadline:
                break
            if direction == "BUY":
                hit_stop, hit_target = future.low <= stop, future.high >= target
            else:
                hit_stop, hit_target = future.high >= stop, future.low <= target
            if hit_stop and hit_target:
                return self._outcome(candidate, "LOSS", stop, risk, future.timestamp)
            if hit_stop:
                return self._outcome(candidate, "LOSS", stop, risk, future.timestamp)
            if hit_target:
                return self._outcome(candidate, "WIN", target, risk, future.timestamp)

        final_index = min(index + max(1, horizon // timeframe_seconds), len(candles) - 1)
        close_price = candles[final_index].close
        signed_return = (close_price - entry) if direction == "BUY" else (entry - close_price)
        result = "TIMEOUT" if final_index > index else "UNRESOLVED"
        return self._outcome(candidate, result, close_price, risk, candles[final_index].timestamp, signed_return)

    @staticmethod
    def _outcome(candidate, outcome, close_price, risk, close_timestamp, signed_return=None):
        direction = candidate["direction"]
        entry = float(candidate["trade"]["entry"]["price"])
        if signed_return is None:
            signed_return = close_price - entry if direction == "BUY" else entry - close_price
        return {
            "symbol": candidate.get("symbol"),
            "timeframe": candidate.get("timeframe"),
            "strategy": candidate.get("strategy_name"),
            "direction": direction,
            "score": candidate["score"],
            "outcome": outcome,
            "rr_achieved": signed_return / risk if risk else 0.0,
            "close_price": close_price,
            "close_timestamp": close_timestamp,
        }

    @staticmethod
    def _report(results):
        wins = sum(result["outcome"] == "WIN" for result in results)
        losses = sum(result["outcome"] == "LOSS" for result in results)
        resolved = wins + losses
        return {
            "total_signals": len(results),
            "resolved_signals": resolved,
            "win_rate": wins / resolved if resolved else 0.0,
            "average_rr": sum(result["rr_achieved"] for result in results) / len(results) if results else 0.0,
            "details": results,
        }

    def _cooldown_seconds(self, context, timeframe_seconds):
        volatility = context.volatility_state.lower()
        bucket = "low" if volatility == "low" else "high" if volatility == "high" else "normal"
        try:
            minutes = int(self.config.get(f"validation.cooldown.{bucket}_volatility"))
        except Exception:
            minutes = {"low": 20, "normal": 30, "high": 45}[bucket]
        return max(0, minutes * 60)

    def _config_get(self, path, default=None):
        try:
            return self.config.get(path)
        except Exception:
            return default

    @staticmethod
    def _parse_bound(value: str, end_of_day: bool) -> int:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            parsed = parsed.replace(hour=23, minute=59, second=59) if end_of_day else parsed
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())

    @staticmethod
    def _duration_seconds(value: Optional[str]) -> int:
        if not isinstance(value, str):
            return 0
        try:
            amount = float(value[:-1])
            unit = value[-1].lower()
            return int(amount * {"m": 60, "h": 3600, "d": 86400}[unit])
        except (ValueError, KeyError, IndexError):
            return 0

    @staticmethod
    def _timeframe_seconds(timeframe: str) -> int:
        return BacktestEngine._duration_seconds(timeframe) or 3600

    @staticmethod
    def _finite(value: float) -> Optional[float]:
        import math
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
