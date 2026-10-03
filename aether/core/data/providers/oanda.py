from __future__ import annotations

import time
from typing import List

from aether.core.config.loader import get_secret
from aether.core.data.normalizer import NormalizedCandle
from aether.core.data.provider import (
    IDataProvider,
    MarketQuote,
    ProviderCapabilities,
    ProviderHealth,
    SymbolMapper,
    compute_staleness,
    now_timestamp,
    datetime_from_ts,
)
from aether.core.data.oanda_client import OandaClient


class OandaProvider(IDataProvider):
    name = "Oanda"

    def __init__(self, config: dict):
        self.config = config or {}
        self.base_url = self.config.get("base_url")
        self.api_key_env = self.config.get("api_key_env")
        self.account_id_env = self.config.get("account_id_env")
        self._client: OandaClient | None = None

    async def connect(self) -> None:
        if not self.api_key_env or not self.account_id_env:
            raise RuntimeError("Oanda provider requires api_key_env and account_id_env")
        base_url = self.base_url or "https://api-fxtrade.oanda.com"
        self._client = OandaClient(base_url, self.api_key_env, self.account_id_env)
        await self._client.start()

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, forex=True)

    async def health(self) -> ProviderHealth:
        if self._client is None:
            await self.connect()
        start = time.monotonic()
        try:
            candles = await self.get_candles("EURUSD", "1h", count=1)
            latency_ms = int((time.monotonic() - start) * 1000)
            stale = compute_staleness("1h", candles[0].timestamp) if candles else True
            return ProviderHealth(
                provider=self.name,
                healthy=bool(candles) and not stale,
                status="ok" if candles and not stale else "stale" if stale else "error",
                checked_at=datetime_from_ts(now_timestamp()),
                latency_ms=latency_ms,
                confidence=85 if not stale else 30,
                stale=stale,
                details={"source": self.base_url},
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                status="error",
                checked_at=datetime_from_ts(now_timestamp()),
                latency_ms=latency_ms,
                confidence=0,
                stale=False,
                error=str(exc),
                details={"source": self.base_url},
            )

    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> List[NormalizedCandle]:
        if self._client is None:
            await self.connect()
        provider_symbol = SymbolMapper.to_provider_symbol(self.name, symbol)
        return await self._client.fetch_normalized_candles(provider_symbol, timeframe, count)

    async def get_quote(self, symbol: str) -> MarketQuote:
        if self._client is None:
            await self.connect()
        provider_symbol = SymbolMapper.to_provider_symbol(self.name, symbol)
        candles = await self._client.fetch_normalized_candles(provider_symbol, "1h", count=1)
        if not candles:
            raise RuntimeError("Oanda quote fetch failed")
        candle = candles[-1]
        return MarketQuote(
            symbol=symbol,
            bid=None,
            ask=None,
            price=candle.close,
            timestamp=candle.timestamp,
            source=self.name,
        )
