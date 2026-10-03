import asyncio

from aether.core.data.provider import MarketQuote
from aether.core.data.provider_manager import DataProviderManager


class FakeProvider:
    def __init__(self, name, quote_ok=True, candle_ok=True):
        self.name = name
        self.quote_ok = quote_ok
        self.candle_ok = candle_ok

    async def connect(self):
        return None

    async def capabilities(self):
        return type("Caps", (), {"quotes": True, "candles": True})()

    async def get_quote(self, symbol):
        if not self.quote_ok:
            raise RuntimeError("quote failed")
        return MarketQuote(symbol=symbol, bid=1.1, ask=1.1002, price=1.1001, timestamp=1700000000, source=self.name)

    async def get_candles(self, symbol, timeframe, count=200):
        if not self.candle_ok:
            raise RuntimeError("candles failed")
        return [type("C", (), {"is_closed": True, "timestamp": 1700000000})()]

    async def health(self):
        return type("H", (), {"healthy": True})()


class FakeConfig:
    def __init__(self):
        self.values = {
            "data.provider_chain": ["Yahoo", "Frankfurter"],
            "data.providers": {"Yahoo": {"enabled": True}, "Frankfurter": {"enabled": True}},
            "data.timeframes": ["1h", "15m"],
            "watchlist": ["EURUSD"],
            "validation.max_quote_age_seconds": 120,
        }

    def get(self, path, default=None):
        return self.values.get(path, default)


async def _run():
    manager = DataProviderManager(FakeConfig(), bus=None)
    manager.providers = {
        "Yahoo": FakeProvider("Yahoo", quote_ok=True, candle_ok=True),
        "Frankfurter": FakeProvider("Frankfurter", quote_ok=False, candle_ok=False),
    }
    manager.active_provider_name = "Yahoo"
    return await manager.verify_symbol("EURUSD", ["1h", "15m"])


def test_verify_symbol_reports_provider_readiness():
    result = asyncio.run(_run())
    assert result["symbol"] == "EURUSD"
    assert result["status"] == "degraded"
    assert result["providers"]["Yahoo"]["quote_ok"] is True
    assert result["providers"]["Yahoo"]["timeframes"]["1h"] == "ok"
    assert result["providers"]["Frankfurter"]["quote_ok"] is False
