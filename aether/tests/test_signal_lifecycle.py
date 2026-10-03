import asyncio
from datetime import datetime, timezone

from aether.core.signal.state_manager import SignalStateManager


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, payload):
        self.events.append((topic, payload))


def test_same_candle_stop_and_target_hit_resolves_as_loss(tmp_path):
    manager = SignalStateManager(str(tmp_path))
    manager.bus = FakeBus()
    manager.active_signals["signal-1"] = {
        "signal_id": "signal-1",
        "symbol": "EURUSD",
        "timeframe": "1h",
        "direction": "BUY",
        "state": "ACTIVE",
        "entry_price": 1.1,
        "stop_loss_price": 1.0,
        "take_profit_price": 1.2,
        "trade_construction": {},
    }

    closed = asyncio.run(manager._close_on_tp_sl(
        "signal-1",
        {"high": 1.25, "low": 0.95},
        1.15,
        datetime.now(timezone.utc),
    ))

    assert closed is True
    event = next(payload for topic, payload in manager.bus.events if topic == "signal.closed")
    assert event["state"] == "LOSS"
    assert event["close_reason"] == "sl"