"""Unit tests for the self-healing data provider fallback chain."""
import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aether.core.data.provider_manager import DataProviderManager  # noqa: E402
from aether.core.data.normalizer import NormalizedCandle  # noqa: E402


class FakeProvider:
    def __init__(self, name: str, *, fail: bool = False, healthy: bool = True):
        self.name = name
        self._fail = fail
        self._healthy = healthy

    async def connect(self):
        return None

    async def capabilities(self):
        from aether.core.data.provider import ProviderCapabilities
        return ProviderCapabilities(candles=True, quotes=True, forex=True, crypto=True)

    async def health(self):
        from aether.core.data.provider import ProviderHealth, datetime_from_ts, now_timestamp
        return ProviderHealth(
            provider=self.name,
            healthy=self._healthy,
            status="ok" if self._healthy else "error",
            checked_at=datetime_from_ts(now_timestamp()),
            latency_ms=10,
            confidence=80 if self._healthy else 0,
            stale=False,
        )

    async def get_candles(self, symbol, timeframe, count=200):
        if self._fail:
            raise RuntimeError(f"{self.name} simulated failure")
        return [NormalizedCandle(
            symbol=symbol, timeframe=timeframe,
            open=1.0, high=1.1, low=0.9, close=1.05, volume=10.0,
            timestamp=int(time.time()), close_time=int(time.time()),
            source=self.name, is_closed=True,
        )]

    async def get_quote(self, symbol):
        if self._fail:
            raise RuntimeError(f"{self.name} simulated failure")
        from aether.core.data.provider import MarketQuote
        return MarketQuote(
            symbol=symbol, bid=1.0, ask=1.0, price=1.0,
            timestamp=int(time.time()), source=self.name,
        )


class FakeConfig:
    def __init__(self, chain: List[str]):
        self._chain = chain

    def get(self, path, default=None):
        if path == "data.provider_chain":
            return self._chain
        if path == "data.providers":
            return {}
        return default


class FakeBus:
    def __init__(self):
        self.published: List[tuple] = []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))


def _make_mgr(chain: List[str], providers: Dict[str, FakeProvider]) -> tuple:
    cfg = FakeConfig(chain=chain)
    bus = FakeBus()
    mgr = DataProviderManager.__new__(DataProviderManager)
    mgr.config = cfg
    mgr.bus = bus
    mgr.providers = providers
    mgr.provider_health = {}
    mgr._backoff_until = {}
    mgr.active_provider_name = None
    mgr.last_successful_provider = None
    mgr.last_successful_update = None
    mgr._market_status = {}
    return mgr, bus


def test_no_key_fallbacks_appended_to_chain():
    """Frankfurter and CoinGecko are always appended at the end of the
    candidate chain so a paid-only chain still has fallbacks.
    """
    mgr, _ = _make_mgr(chain=["TwelveData"], providers={
        "TwelveData": FakeProvider("TwelveData"),
        "Frankfurter": FakeProvider("Frankfurter"),
        "CoinGecko": FakeProvider("CoinGecko"),
    })
    cands = asyncio.run(mgr._candidate_providers("candles"))
    assert cands[0] == "TwelveData"
    assert "Frankfurter" in cands
    assert "CoinGecko" in cands
    assert cands.index("Frankfurter") > cands.index("TwelveData")
    assert cands.index("CoinGecko") > cands.index("Frankfurter")


def test_failover_to_next_provider():
    """When the primary provider fails, get_candles returns data from the
    next provider in the chain.
    """
    mgr, bus = _make_mgr(chain=["TwelveData", "Frankfurter"], providers={
        "TwelveData": FakeProvider("TwelveData", fail=True),
        "Frankfurter": FakeProvider("Frankfurter", fail=False),
    })
    candles = asyncio.run(mgr.get_candles("EURUSD", "1h", count=1))
    assert len(candles) == 1
    assert candles[0].source == "Frankfurter"
    # Failed provider should be in backoff
    assert "TwelveData" in mgr._backoff_until
    # The bus should have published a provider_failed event
    failed_topics = [t for t, _ in bus.published if t == "data.provider_failed"]
    assert failed_topics


def test_keyless_provider_has_shorter_backoff():
    """Keyless providers cool-down faster than keyed ones."""
    mgr, _ = _make_mgr(chain=["TwelveData", "Frankfurter"], providers={
        "TwelveData": FakeProvider("TwelveData"),
        "Frankfurter": FakeProvider("Frankfurter"),
    })
    asyncio.run(mgr._mark_provider_failed("TwelveData", "401 unauthorized"))
    asyncio.run(mgr._mark_provider_failed("Frankfurter", "rate limited"))
    twelvedata_backoff = mgr._backoff_until["TwelveData"]
    frankfurter_backoff = mgr._backoff_until["Frankfurter"]
    assert (twelvedata_backoff - time.time()) >= 50  # >= BACKOFF_SECONDS_KEYED
    assert (frankfurter_backoff - time.time()) <= 20  # <= BACKOFF_SECONDS_KEYLESS


def test_all_providers_fail_raises_clear_error():
    """If every provider fails, the error message names them so the
    operator can act on it.
    """
    mgr, _ = _make_mgr(chain=["TwelveData", "Frankfurter"], providers={
        "TwelveData": FakeProvider("TwelveData", fail=True),
        "Frankfurter": FakeProvider("Frankfurter", fail=True),
    })
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(mgr.get_candles("EURUSD", "1h", count=1))
    assert "no provider" in str(exc_info.value).lower() or "simulated failure" in str(exc_info.value).lower()
