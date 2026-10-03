import asyncio
import time
from datetime import datetime, timedelta, timezone

from aether.core.alert_manager import AlertManager


class FakeConfig:
    def __init__(self, alerts):
        self.data = {"alerts": alerts, "validation.max_quote_age_seconds": 120}
        self.updates = []

    def get(self, path):
        if path == "alerts":
            return self.data["alerts"]
        if path in self.data:
            return self.data[path]
        raise KeyError(path)

    async def set(self, path, value):
        self.updates.append((path, value))
        self.data[path] = value


class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, payload):
        self.events.append((topic, payload))


def test_price_alert_triggers_once_on_fresh_quote():
    config = FakeConfig({"price": [{
        "id": "alert-1", "symbol": "EURUSD", "condition": "above",
        "price": 1.1, "enabled": True,
    }], "news": {}})
    bus = FakeBus()
    manager = AlertManager(config, bus)
    quote = {"symbol": "EURUSD", "bid": 1.1001, "ask": 1.1003, "timestamp": int(time.time())}

    asyncio.run(manager._on_quote(quote))
    asyncio.run(manager._on_quote(quote))

    triggered = [event for topic, event in bus.events if topic == "alert.triggered"]
    assert len(triggered) == 1
    assert triggered[0]["kind"] == "price"
    assert triggered[0]["price"] == 1.1002
    assert config.data["alerts"]["price"][0]["enabled"] is False


def test_price_alert_ignores_stale_quote():
    config = FakeConfig({"price": [{
        "id": "alert-1", "symbol": "EURUSD", "condition": "below",
        "price": 1.2, "enabled": True,
    }], "news": {}})
    bus = FakeBus()
    manager = AlertManager(config, bus)

    asyncio.run(manager._on_quote({
        "symbol": "EURUSD", "price": 1.0, "timestamp": int(time.time()) - 1000,
    }))

    assert not bus.events
    assert config.data["alerts"]["price"][0]["enabled"] is True


def test_calendar_csv_parses_local_time_to_utc():
    future_date = (datetime.now(timezone.utc) + timedelta(days=2)).astimezone(
        timezone.utc
    ).strftime("%m-%d-%Y")
    events = AlertManager.parse_calendar_csv(
        f"Date,Time,Currency,Impact,Event\n{future_date},8:30am,USD,High,CPI Release\n",
        "America/New_York",
    )

    assert len(events) == 1
    assert events[0]["currency"] == "USD"
    assert events[0]["impact"] == "high"
    assert events[0]["title"] == "CPI Release"
    assert events[0]["timestamp"] > int(time.time())


def test_private_calendar_hosts_are_rejected():
    assert AlertManager._is_private_host("localhost") is True
    assert AlertManager._is_private_host("127.0.0.1") is True
    assert AlertManager._is_private_host("192.168.1.20") is True
    assert AlertManager._is_private_host("calendar.example") is False


def test_calendar_response_size_is_bounded():
    class Content:
        async def iter_chunked(self, _size):
            yield b"abcdef"

    class Response:
        content_length = None
        content = Content()
        charset = "utf-8"

    async def read():
        return await AlertManager._read_limited(Response(), 4)

    try:
        asyncio.run(read())
    except ValueError as exc:
        assert "2 MB limit" in str(exc)
    else:
        raise AssertionError("oversized calendar data was accepted")


def test_news_event_matches_pair_currency_and_impact():
    config = FakeConfig({"price": [], "news": {
        "enabled": True,
        "pairs": ["EURUSD"],
        "minimum_impact": "high",
        "lead_minutes": 60,
    }})
    manager = AlertManager(config, FakeBus())
    manager._seen_news = set()
    events = [
        {"id": "high-usd", "currency": "USD", "title": "CPI", "impact": "high", "timestamp": int(time.time()) + 300},
        {"id": "low-eur", "currency": "EUR", "title": "Minor", "impact": "low", "timestamp": int(time.time()) + 300},
        {"id": "high-gbp", "currency": "GBP", "title": "BoE", "impact": "high", "timestamp": int(time.time()) + 300},
    ]

    asyncio.run(manager._notify_matching_events(events, config.data["alerts"]["news"]))

    triggered = [event for topic, event in manager.bus.events if topic == "alert.triggered"]
    assert len(triggered) == 1
    assert triggered[0]["id"] == "high-usd"
    assert triggered[0]["pairs"] == ["EURUSD"]
