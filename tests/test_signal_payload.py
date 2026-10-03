import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aether.core.signal.state_manager import SignalStateManager
from aether.core.strategy.trend_following import TrendFollowingV1
from aether.core.context.models import MarketContext, NewsRisk
from aether.core.data.normalizer import NormalizedCandle


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, payload):
        self.events.append((topic, payload))


class FakeConfig:
    def get(self, path, default=None):
        if path == "signal.operator_timezone":
            return "Africa/Kampala"
        return default


def test_signal_state_preserves_trade_construction_for_delivery(tmp_path):
    mgr = SignalStateManager(str(tmp_path), FakeConfig())
    mgr.bus = FakeBus()
    trade = {
        "entry": {"type": "market", "price": 1.2345},
        "stop_loss": {"price": 1.2335},
        "take_profit": {"price": 1.2360},
        "expiry": "20m",
    }

    asyncio.run(mgr._on_signal_emitted({
        "signal_id": "sig-1",
        "symbol": "EURUSD",
        "timeframe": "5m",
        "direction": "BUY",
        "confidence": {"adjusted": 77},
        "reason_tags": ["scalper"],
        "trade": trade,
        "strategy_name": "trend_following_v1",
        "source_candle_timestamp": 123,
        "context_snapshot": {"volatility_state": "Normal"},
        "validation": {"valid": True},
        "market_snapshot_hash": "abc123",
    }))

    topic, payload = next(event for event in mgr.bus.events if event[0] == "signal.generated")
    assert topic == "signal.generated"
    assert payload["entry_price"] == 1.2345
    assert payload["stop_loss_price"] == 1.2335
    assert payload["take_profit_price"] == 1.2360
    assert payload["trade_construction"] == trade
    assert payload["confidence"]["adjusted"] == 77
    assert payload["operator_timezone"] == "Africa/Kampala"
    assert payload["validity_seconds"] == 1200
    assert payload["strategy_name"] == "trend_following_v1"
    assert payload["source_candle_timestamp"] == 123
    assert payload["context_snapshot"] == {"volatility_state": "Normal"}
    assert payload["validation"] == {"valid": True}
    assert payload["market_snapshot_hash"] == "abc123"


def test_signal_state_suppresses_repeated_signals_during_cooldown(tmp_path):
    mgr = SignalStateManager(str(tmp_path), FakeConfig())
    mgr.bus = FakeBus()
    signal = {
        "symbol": "EURUSD",
        "timeframe": "1h",
        "direction": "BUY",
        "strategy_name": "trend_following_v1",
        "context_snapshot": {"volatility_state": "Normal"},
        "trade": {
            "entry": {"type": "market", "price": 1.1},
            "stop_loss": {"price": 1.09},
            "take_profit": {"price": 1.12},
        },
    }

    asyncio.run(mgr._on_signal_emitted({**signal, "signal_id": "sig-1"}))
    asyncio.run(mgr._on_signal_emitted({**signal, "signal_id": "sig-2"}))

    generated = [event for event in mgr.bus.events if event[0] == "signal.generated"]
    suppressed = [event for event in mgr.bus.events if event[0] == "signal.suppressed"]
    assert len(generated) == 1
    assert len(suppressed) == 1
    assert suppressed[0][1]["reason"] == "cooldown"


def test_scalper_strategy_params_change_rr_and_expiry():
    strategy = TrendFollowingV1("trend_following_v1", "1.0.0", {
        "params": {
            "atr_stop_multiplier": 1.0,
            "reward_risk_ratio": 1.5,
            "expiry": {"5m": "20m"},
        }
    })
    context = MarketContext(
        directional_state="Bullish",
        volatility_state="Normal",
        session="London",
        session_transition=False,
        news_risk=NewsRisk(active=False, impact=None),
        liquidity_state="High",
        market_structure="Higher Highs",
    )
    candle = NormalizedCandle(
        symbol="EURUSD",
        timeframe="5m",
        open=1.0,
        high=1.01,
        low=0.99,
        close=1.0,
        volume=1.0,
        timestamp=1,
        close_time=1,
        source="test",
        is_closed=True,
    )

    trade = strategy.construct_trade("EURUSD", "5m", "BUY", context, {}, candle)

    assert trade["expiry"] == "20m"
    assert trade["stop_loss"]["price"] == 0.99
    assert trade["take_profit"]["price"] == 1.015
    assert trade["take_profit"]["rr_ratio"] == 1.5
