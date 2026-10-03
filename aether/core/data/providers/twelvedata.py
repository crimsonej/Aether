from __future__ import annotations

import asyncio
import time
from typing import List, Optional
from datetime import datetime, timezone

import aiohttp
from dateutil import parser

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
)


class TwelveDataProvider(IDataProvider):
    name = "TwelveData"
    _DEFAULT_BASE_URL = "https://api.twelvedata.com"

    def __init__(self, config: dict):
        self.config = config or {}
        self.api_key_env = self.config.get("api_key_env")
        self.base_url = self.config.get("base_url") or self._DEFAULT_BASE_URL
        self.api_key = None

    async def connect(self) -> None:
        if not self.api_key_env:
            raise RuntimeError("TwelveData provider requires api_key_env")
        self.api_key = get_secret(self.api_key_env)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, forex=True, crypto=True)

    async def health(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            if self.api_key is None:
                await self.connect()
            quote = await self.get_quote("EURUSD")
            latency_ms = int((time.monotonic() - start) * 1000)
            stale = compute_staleness("1h", quote.timestamp)
            return ProviderHealth(
                provider=self.name,
                healthy=not stale and quote.price is not None,
                status="ok" if quote.price is not None and not stale else "stale" if stale else "error",
                checked_at=datetime_from_ts(now_timestamp()),
                latency_ms=latency_ms,
                confidence=95 if not stale else 40,
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
        provider_symbol = SymbolMapper.to_provider_symbol(self.name, symbol)
        interval = SymbolMapper.to_provider_timeframe(self.name, timeframe)
        url = f"{self.base_url}/time_series"
        params = {
            "symbol": provider_symbol,
            "interval": interval,
            "outputsize": count,
            "format": "JSON",
            "apikey": self.api_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=30) as resp:
                data = await resp.json()
                if resp.status != 200 or "values" not in data:
                    raise RuntimeError(f"TwelveData candle fetch failed: {data}")
                candles = []
                for entry in reversed(data["values"]):
                    timestamp = int(parser.isoparse(entry["datetime"]).timestamp())
                    candles.append(NormalizedCandle(
                        symbol=symbol,
                        timeframe=timeframe,
                        open=float(entry["open"]),
                        high=float(entry["high"]),
                        low=float(entry["low"]),
                        close=float(entry["close"]),
                        volume=float(entry.get("volume", 0.0) or 0.0),
                        timestamp=timestamp,
                        close_time=timestamp,
                        source=self.name,
                        is_closed=True,
                    ))
                return candles

    async def get_quote(self, symbol: str) -> MarketQuote:
        provider_symbol = SymbolMapper.to_provider_symbol(self.name, symbol)
        url = f"{self.base_url}/quote"
        params = {
            "symbol": provider_symbol,
            "apikey": self.api_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=20) as resp:
                data = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(f"TwelveData quote fetch failed: {data}")

                # API may return either a wrapper with 'value' or a direct quote payload
                if "value" in data:
                    payload = data["value"]
                else:
                    payload = data

                price = float(payload.get("close") or payload.get("price") or 0)
                bid = float(payload.get("bid") or 0) if payload.get("bid") else None
                ask = float(payload.get("ask") or 0) if payload.get("ask") else None
                ts = now_timestamp()
                return MarketQuote(
                    symbol=symbol,
                    bid=bid,
                    ask=ask,
                    price=price,
                    timestamp=ts,
                    source=self.name,
                )


def datetime_from_ts(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)
