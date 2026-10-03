import asyncio
import time
from datetime import datetime, timezone, timedelta

import pytest

from aether.core.event_bus import EventBus
from aether.core.data.provider_manager import DataProviderManager
from aether.core.data.provider import MarketQuote, ProviderHealth
from aether.core.data.normalizer import NormalizedCandle


class MockProviderBase:
    name = "Mock"

    async def connect(self):
        return

    async def capabilities(self):
        return type("C", (), {"candles": True, "quotes": True})()


class TimeoutProvider(MockProviderBase):
    name = "TimeoutProvider"

    async def get_candles(self, symbol, timeframe, count=200):
        await asyncio.sleep(0)  # yield
        raise asyncio.TimeoutError("timeout")

    async def get_quote(self, symbol):
        raise asyncio.TimeoutError("timeout")


class RateLimitProvider(MockProviderBase):
    name = "RateLimitProvider"

    async def get_candles(self, symbol, timeframe, count=200):
        raise RuntimeError("rate_limit")

    async def get_quote(self, symbol):
        raise RuntimeError("rate_limit")


class StaleProvider(MockProviderBase):
    name = "StaleProvider"

    async def get_candles(self, symbol, timeframe, count=200):
        old_ts = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
        return [NormalizedCandle(symbol=symbol, timeframe=timeframe, open=1, high=2, low=0.5, close=1.5, volume=0.0, timestamp=old_ts, close_time=old_ts, source=self.name, is_closed=True)]

    async def get_quote(self, symbol):
        old_ts = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
        return MarketQuote(symbol=symbol, bid=None, ask=None, price=1.0, timestamp=old_ts, source=self.name)


class GoodProvider(MockProviderBase):
    name = "GoodProvider"

    async def get_candles(self, symbol, timeframe, count=200):
        ts = int(datetime.now(timezone.utc).timestamp())
        return [NormalizedCandle(symbol=symbol, timeframe=timeframe, open=1, high=2, low=0.5, close=1.5, volume=0.0, timestamp=ts, close_time=ts, source=self.name, is_closed=True)]

    async def get_quote(self, symbol):
        ts = int(datetime.now(timezone.utc).timestamp())
        return MarketQuote(symbol=symbol, bid=None, ask=None, price=1.0, timestamp=ts, source=self.name)


def test_failover_and_events():
    async def _inner():
        bus = EventBus()
        await bus.start()

        manager = DataProviderManager({}, bus)
        # Inject mock providers: primary fails, secondary succeeds
        manager.providers = {
            "primary": TimeoutProvider(),
            "secondary": GoodProvider(),
        }

        events = {"changed": [], "failed": [], "stale": []}

        async def on_changed(payload):
            events["changed"].append(payload)

        async def on_failed(payload):
            events["failed"].append(payload)

        async def on_stale(payload):
            events["stale"].append(payload)

        await bus.subscribe("data.provider_changed", on_changed)
        await bus.subscribe("data.provider_failed", on_failed)
        await bus.subscribe("data.stale_detected", on_stale)

        # Ensure health mapping so providers considered usable
        for name in manager.providers:
            manager.provider_health[name] = ProviderHealth(provider=name, healthy=True, status="ok", checked_at=datetime.now(timezone.utc), confidence=90)

        candles = await manager.get_candles("EURUSD", "1h", count=1)
        assert candles, "expected candles from secondary provider"
        # Wait briefly for async publishes
        await asyncio.sleep(0.1)
        assert events["failed"], "expected provider_failed event"
        assert events["changed"], "expected provider_changed event"

        await bus.stop()

    asyncio.run(_inner())


def test_stale_detection_marks_failed():
    async def _inner():
        bus = EventBus()
        await bus.start()

        manager = DataProviderManager({}, bus)
        manager.providers = {"stale": StaleProvider(), "good": GoodProvider()}
        for name in manager.providers:
            manager.provider_health[name] = ProviderHealth(provider=name, healthy=True, status="ok", checked_at=datetime.now(timezone.utc), confidence=80)

        events = {"stale": [], "failed": []}

        async def on_stale(payload):
            events["stale"].append(payload)

        async def on_failed(payload):
            events["failed"].append(payload)

        await bus.subscribe("data.stale_detected", on_stale)
        await bus.subscribe("data.provider_failed", on_failed)

        candles = await manager.get_candles("EURUSD", "1h", count=1)
        assert candles, "expected candles from fallback good provider"
        await asyncio.sleep(0.1)
        assert events["stale"], "expected stale_detected event"
        assert events["failed"], "expected provider_failed event for stale provider"

        await bus.stop()

    asyncio.run(_inner())


def test_recovery_of_provider_via_health_refresh():
    async def _inner():
        bus = EventBus()
        await bus.start()

        manager = DataProviderManager({}, bus)
        # provider 'flaky' initially unhealthy, later healthy
        class FlakyProvider(GoodProvider):
            name = "flaky"

            def __init__(self):
                self._healthy = False

            async def health(self):
                # Return a ProviderHealth model so manager can call .dict()
                ts = int(datetime.now(timezone.utc).timestamp())
                return ProviderHealth(provider=self.name, healthy=self._healthy, status=("ok" if self._healthy else "error"), checked_at=datetime.fromtimestamp(ts, tz=timezone.utc), latency_ms=10, confidence=(90 if self._healthy else 0), stale=False)

        flaky = FlakyProvider()
        manager.providers = {"flaky": flaky, "good": GoodProvider()}
        # start with flaky unhealthy
        flaky._healthy = False
        manager.provider_health = {"flaky": (await flaky.health())}

        # initially, get_candles should use good provider
        manager.provider_health["good"] = ProviderHealth(provider="good", healthy=True, status="ok", checked_at=datetime.now(timezone.utc), confidence=90)
        candles = await manager.get_candles("EURUSD", "1h", count=1)
        assert candles

        # make flaky healthy and refresh health
        flaky._healthy = True
        await manager._refresh_health()
        # flaky should now be marked healthy
        assert manager.provider_health["flaky"].healthy is True

        await bus.stop()

    asyncio.run(_inner())
