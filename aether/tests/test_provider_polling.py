import asyncio
import time

from aether.core.data.normalizer import NormalizedCandle
from aether.core.data.provider import MarketQuote
from aether.core.data.provider_manager import DataProviderManager


class FakeConfig:
    def get(self, path):
        values = {
            "data.timeframes": ["5m", "15m", "1h"],
            "data.poll_count": 80,
            "data.request_stagger_seconds": 0,
            "data.poll_interval": 60,
        }
        if path == "watchlist":
            return ["EURUSD", "GBPUSD"]
        return values[path]


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, payload):
        self.events.append((topic, payload))


def test_poll_cycle_fetches_one_quote_per_symbol(monkeypatch):
    manager = DataProviderManager.__new__(DataProviderManager)
    manager.config = FakeConfig()
    manager.bus = FakeBus()
    manager._backoff_until = {}
    manager.last_successful_provider = "test"
    manager.active_provider_name = "test"
    manager.candle_calls = []
    manager.quote_calls = []

    async def get_candles(symbol, timeframe, count):
        manager.candle_calls.append((symbol, timeframe, count))
        now = int(time.time())
        return [
            NormalizedCandle(
                symbol=symbol, timeframe=timeframe, open=1.0, high=1.1,
                low=0.9, close=1.04, volume=10.0, timestamp=now - 3600,
                close_time=now - 3600, source="test", is_closed=True,
            ),
            NormalizedCandle(
                symbol=symbol, timeframe=timeframe, open=1.04, high=1.1,
                low=0.9, close=1.05, volume=10.0, timestamp=now,
                close_time=now, source="test", is_closed=True,
            ),
        ]

    async def get_quote(symbol):
        manager.quote_calls.append(symbol)
        now = int(time.time())
        return MarketQuote(symbol=symbol, bid=1.0, ask=1.0002, price=1.0001, timestamp=now, source="test")

    manager.get_candles = get_candles
    manager.get_quote = get_quote
    manager._update_market_status = lambda *_args: None

    class PollComplete(Exception):
        pass

    async def stop_after_cycle(_delay):
        raise PollComplete

    monkeypatch.setattr("aether.core.data.provider_manager.asyncio.sleep", stop_after_cycle)

    async def run_once():
        try:
            await manager._polling_loop()
        except PollComplete:
            pass

    asyncio.run(run_once())

    assert len(manager.candle_calls) == 6
    assert manager.quote_calls == ["EURUSD", "GBPUSD"]
    assert sum(topic == "data.quote" for topic, _ in manager.bus.events) == 2
    candle_events = [payload for topic, payload in manager.bus.events if topic == "data.candle"]
    assert len(candle_events) == 6
    assert len(candle_events[0]["warmup_candles"]) == 1
    assert candle_events[0]["timestamp"] > candle_events[0]["warmup_candles"][0]["timestamp"]
