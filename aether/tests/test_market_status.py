import asyncio
from datetime import datetime, timezone, timedelta

from aether.core.event_bus import EventBus
from aether.core.data.market_status import MarketStatusService
from aether.core.data.provider import MarketStatus


def run(coro):
    return asyncio.run(coro)

# Fixed reference time for deterministic behavior in tests
fixed_now = datetime(2024, 5, 8, 12, 0, tzinfo=timezone.utc)  # Wednesday

def _deterministic_compute(last_candle_ts: int, timeframe: str) -> MarketStatus:
    last_candle = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc)
    # Weekend check
    if fixed_now.weekday() >= 5:
        return MarketStatus.CLOSED
    # Staleness thresholds mirroring production logic
    delta = (fixed_now - last_candle).total_seconds()
    if timeframe == "15m":
        threshold = 3 * 60 * 60
    elif timeframe == "1h":
        threshold = 6 * 60 * 60
    elif timeframe == "4h":
        threshold = 12 * 60 * 60
    else:
        threshold = 8 * 60 * 60
    if delta > threshold:
        return MarketStatus.CLOSED
    if delta > 2 * 60 * 60:
        return MarketStatus.LOW_LIQUIDITY
    return MarketStatus.OPEN


def test_open_and_closed_transitions():
    async def _inner():
        # Use a lightweight mock bus to capture publishes directly.
        published = []

        class MockBus:
            async def publish(self, topic, payload):
                published.append((topic, payload))

        bus = MockBus()
        svc = MarketStatusService({}, bus)
        # Override internal time-based computation to use fixed_now
        def _deterministic_compute(last_candle_ts: int, timeframe: str) -> MarketStatus:
            last_candle = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc)
            # Weekend check
            if fixed_now.weekday() >= 5:
                return MarketStatus.CLOSED
            # Staleness thresholds mirroring production logic
            delta = (fixed_now - last_candle).total_seconds()
            if timeframe == "15m":
                threshold = 3 * 60 * 60
            elif timeframe == "1h":
                threshold = 6 * 60 * 60
            elif timeframe == "4h":
                threshold = 12 * 60 * 60
            else:
                threshold = 8 * 60 * 60
            if delta > threshold:
                return MarketStatus.CLOSED
            if delta > 2 * 60 * 60:
                return MarketStatus.LOW_LIQUIDITY
            return MarketStatus.OPEN
        svc._compute_market_status = _deterministic_compute

        # Use a fixed UTC time so test behavior is deterministic regardless of execution date
        fixed_now = datetime(2024, 5, 8, 12, 0, tzinfo=timezone.utc)  # Wednesday
        now = int(fixed_now.timestamp())
        # recent candle -> open on weekdays, closed on weekends
        await svc._on_candle({"symbol": "EURUSD", "timeframe": "1h", "timestamp": now})
        current_weekday = fixed_now.weekday()
        if current_weekday >= 5:
            assert svc._status.get("EURUSD:1h") == MarketStatus.CLOSED
            assert any(t == "market.closed" for t, _ in published), "expected market.closed on weekend"
        else:
            assert svc._status.get("EURUSD:1h") == MarketStatus.OPEN
            assert any(t == "market.opened" for t, _ in published), "expected market.opened"

        # stale candle -> closed
        old = int((fixed_now - timedelta(days=2)).timestamp())
        await svc._on_candle({"symbol": "EURUSD", "timeframe": "1h", "timestamp": old})
        assert any(t == "market.closed" for t, _ in published), "expected market.closed"

    run(_inner())


def test_low_liquidity():
    async def _inner():
        published = []

        class MockBus:
            async def publish(self, topic, payload):
                published.append((topic, payload))

        bus = MockBus()
        svc = MarketStatusService({}, bus)
        svc._compute_market_status = _deterministic_compute

        ts = int((fixed_now - timedelta(hours=3)).timestamp())
        await svc._on_candle({"symbol": "EURUSD", "timeframe": "1h", "timestamp": ts})
        current_weekday = fixed_now.weekday()
        if current_weekday >= 5:
            assert any(t == "market.closed" for t, _ in published), "expected market.closed on weekend"
        else:
            assert any(t == "market.low_liquidity" for t, _ in published), "expected market.low_liquidity"

    run(_inner())
