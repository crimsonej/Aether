import json
from datetime import datetime, timezone

from aether.core.signal.backtest import BacktestEngine


class FakeConfig:
    def get(self, path):
        values = {
            "strategies": [{"name": "trend_following_v1", "enabled": True}],
            "features.enabled": ["ema20", "ema50", "ema200", "rsi", "atr", "adx", "macd"],
            "validation.min_confidence": 60,
            "validation.max_spread_multiplier": 2.0,
            "validation.typical_spread_by_symbol": {"EURUSD": 0.0002},
            "validation.cooldown.normal_volatility": 0,
            "validation.cooldown.low_volatility": 0,
            "validation.cooldown.high_volatility": 0,
        }
        if path not in values:
            raise KeyError(path)
        return values[path]


def write_candles(path, count=240):
    start = int(datetime(2024, 1, 1, 1, tzinfo=timezone.utc).timestamp())
    candles = []
    for index in range(count):
        close = 1.1 + index * 0.0002
        candles.append({
            "symbol": "EURUSD",
            "timeframe": "1h",
            "open": close - 0.0001,
            "high": close + 0.0002,
            "low": close - 0.0002,
            "close": close,
            "volume": 100,
            "timestamp": start + index * 3600,
            "close_time": start + (index + 1) * 3600,
            "source": "fixture",
            "is_closed": True,
            "spread": 0.0001,
        })
    path.write_text(json.dumps(candles), encoding="utf-8")


def test_backtest_replays_candles_and_never_invents_a_win(tmp_path):
    write_candles(tmp_path / "EURUSD_1h.json")
    engine = BacktestEngine(str(tmp_path), FakeConfig())

    first = engine.run_replay("EURUSD", "1h", "2024-01-01", "2024-01-10")
    second = engine.run_replay("EURUSD", "1h", "2024-01-01", "2024-01-10")

    assert first["total_signals"] > 0
    assert first["details"] == second["details"]
    assert all(item["outcome"] in {"WIN", "LOSS", "TIMEOUT", "UNRESOLVED"} for item in first["details"])
    assert first["win_rate"] == second["win_rate"]


def test_backtest_missing_history_returns_zero_results(tmp_path):
    result = BacktestEngine(str(tmp_path), FakeConfig()).run_replay(
        "EURUSD", "1h", "2024-01-01", "2024-01-10"
    )

    assert result == {
        "total_signals": 0,
        "resolved_signals": 0,
        "win_rate": 0.0,
        "average_rr": 0.0,
        "details": [],
    }


def test_backtest_without_historical_spreads_emits_no_signals(tmp_path):
    write_candles(tmp_path / "EURUSD_1h.json")
    candles = json.loads((tmp_path / "EURUSD_1h.json").read_text(encoding="utf-8"))
    for candle in candles:
        candle.pop("spread")
    (tmp_path / "EURUSD_1h.json").write_text(json.dumps(candles), encoding="utf-8")

    result = BacktestEngine(str(tmp_path), FakeConfig()).run_replay(
        "EURUSD", "1h", "2024-01-01", "2024-01-10"
    )

    assert result["total_signals"] == 0
    assert result["win_rate"] == 0.0
