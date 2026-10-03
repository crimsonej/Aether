from __future__ import annotations

import time
from typing import List

import aiohttp

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


class CoinGeckoProvider(IDataProvider):
    """Free crypto provider (no API key required for the public endpoint).

    Rate limit: ~10-30 req/min shared across your IP.
    Best for: BTC, ETH, and other top-100 coins when all paid
    forex providers are rate-limited.
    """
    name = "CoinGecko"
    _BASE_URL = "https://api.coingecko.com/api/v3"

    # Map our internal symbols to CoinGecko coin IDs.
    _SUPPORTED = {
        "BTCUSD": "bitcoin",
        "ETHUSD": "ethereum",
        "SOLUSD": "solana",
        "ADAUSD": "cardano",
        "XRPUSD": "ripple",
        "DOGEUSD": "dogecoin",
    }

    def __init__(self, config: dict = None):
        self.config = config or {}

    async def connect(self) -> None:
        return

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, crypto=True, forex=False)

    async def health(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE_URL}/ping",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    ok = resp.status == 200
                    latency_ms = int((time.monotonic() - start) * 1000)
                    return ProviderHealth(
                        provider=self.name,
                        healthy=ok,
                        status="ok" if ok else "error",
                        checked_at=datetime_from_ts(now_timestamp()),
                        latency_ms=latency_ms,
                        confidence=70 if ok else 0,
                        stale=False,
                        details={"source": self._BASE_URL},
                    )
        except Exception as exc:
            return ProviderHealth(
                provider=self.name,
                healthy=False,
                status="error",
                checked_at=datetime_from_ts(now_timestamp()),
                latency_ms=int((time.monotonic() - start) * 1000),
                confidence=0,
                stale=False,
                error=str(exc),
                details={"source": self._BASE_URL},
            )

    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> List[NormalizedCandle]:
        coin_id = self._SUPPORTED.get(symbol.upper())
        if not coin_id:
            raise RuntimeError(f"CoinGecko does not support {symbol}")
        # Map timeframe to CoinGecko "days" param.
        days_map = {"1h": 1, "4h": 1, "1d": 30}
        days = days_map.get(timeframe, 1)

        async with aiohttp.ClientSession() as session:
            url = f"{self._BASE_URL}/coins/{coin_id}/market_chart"
            params = {"vs_currency": "usd", "days": days}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 429:
                    raise RuntimeError("CoinGecko rate-limited (HTTP 429)")
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"CoinGecko HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
                prices = data.get("prices", [])
                if not prices:
                    raise RuntimeError("CoinGecko returned no prices")
                candles = []
                for ts_ms, price in prices[-count:]:
                    ts = int(ts_ms / 1000)
                    candles.append(NormalizedCandle(
                        symbol=symbol,
                        timeframe=timeframe,
                        open=float(price),
                        high=float(price),
                        low=float(price),
                        close=float(price),
                        volume=0.0,
                        timestamp=ts,
                        close_time=ts,
                        source=self.name,
                        is_closed=True,
                    ))
                return candles

    async def get_quote(self, symbol: str) -> MarketQuote:
        coin_id = self._SUPPORTED.get(symbol.upper())
        if not coin_id:
            raise RuntimeError(f"CoinGecko does not support {symbol}")
        async with aiohttp.ClientSession() as session:
            url = f"{self._BASE_URL}/simple/price"
            params = {"ids": coin_id, "vs_currencies": "usd"}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 429:
                    raise RuntimeError("CoinGecko rate-limited (HTTP 429)")
                if resp.status != 200:
                    raise RuntimeError(f"CoinGecko HTTP {resp.status}")
                data = await resp.json()
                price = data.get(coin_id, {}).get("usd")
                if price is None:
                    raise RuntimeError("CoinGecko returned no price")
                return MarketQuote(
                    symbol=symbol,
                    bid=None,
                    ask=None,
                    price=float(price),
                    timestamp=now_timestamp(),
                    source=self.name,
                )
