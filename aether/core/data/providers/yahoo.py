from __future__ import annotations

import time
from typing import List, Optional

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


class YahooProvider(IDataProvider):
    name = "Yahoo"
    _DEFAULT_BASE_URL = "https://query1.finance.yahoo.com"

    def __init__(self, config: dict):
        self.config = config or {}
        self.base_url = self.config.get("base_url") or self._DEFAULT_BASE_URL

    async def connect(self) -> None:
        return

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, forex=True, crypto=True)

    async def health(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            quote = await self.get_quote("EURUSD")
            latency_ms = int((time.monotonic() - start) * 1000)
            stale = compute_staleness("1h", quote.timestamp)
            return ProviderHealth(
                provider=self.name,
                healthy=not stale and quote.price is not None,
                status="ok" if quote.price is not None and not stale else "stale" if stale else "error",
                checked_at=datetime_from_ts(now_timestamp()),
                latency_ms=latency_ms,
                confidence=75 if not stale else 20,
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
        url = f"{self.base_url}/v8/finance/chart/{provider_symbol}"
        params = {
            "interval": interval,
            "range": "1d",
        }
        # Yahoo now requires a User-Agent on its chart endpoint
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params, timeout=30) as resp:
                if resp.status == 429:
                    raise RuntimeError("Yahoo rate-limited (HTTP 429)")
                body_text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Yahoo candle fetch HTTP {resp.status}: {body_text[:200]}")
                try:
                    data = await resp.json(content_type=None) if hasattr(resp, "json") else __import__("json").loads(body_text)
                except Exception:
                    raise RuntimeError(f"Yahoo candle fetch: non-JSON body: {body_text[:200]}")
                result = data.get("chart", {}).get("result")
                if not result:
                    err = data.get("chart", {}).get("error")
                    raise RuntimeError(f"Yahoo candle fetch failed: {err or data}")
                chart = result[0]
                timestamps = chart.get("timestamp", [])
                quote = chart.get("indicators", {}).get("quote", [{}])[0]
                opens = quote.get("open", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                closes = quote.get("close", [])
                volumes = quote.get("volume", [])
                candles = []
                for idx, ts in enumerate(timestamps[-count:]):
                    candles.append(NormalizedCandle(
                        symbol=symbol,
                        timeframe=timeframe,
                        open=float(opens[idx] or 0.0),
                        high=float(highs[idx] or 0.0),
                        low=float(lows[idx] or 0.0),
                        close=float(closes[idx] or 0.0),
                        volume=float(volumes[idx] or 0.0),
                        timestamp=int(ts),
                        close_time=int(ts),
                        source=self.name,
                        is_closed=True,
                    ))
                return candles

    async def get_quote(self, symbol: str) -> MarketQuote:
        provider_symbol = SymbolMapper.to_provider_symbol(self.name, symbol)
        url = f"{self.base_url}/v7/finance/quote"
        params = {"symbols": provider_symbol}
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, params=params, timeout=20) as resp:
                if resp.status == 429:
                    raise RuntimeError("Yahoo rate-limited (HTTP 429)")
                body_text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Yahoo quote fetch HTTP {resp.status}: {body_text[:200]}")
                try:
                    data = await resp.json(content_type=None) if hasattr(resp, "json") else __import__("json").loads(body_text)
                except Exception:
                    raise RuntimeError(f"Yahoo quote fetch: non-JSON body: {body_text[:200]}")
                result = data.get("quoteResponse", {}).get("result", [])
                if not result:
                    raise RuntimeError(f"Yahoo quote fetch failed: {data}")
                quote = result[0]
                timestamp = int(quote.get("regularMarketTime", now_timestamp()))
                return MarketQuote(
                    symbol=symbol,
                    bid=float(quote.get("bid", 0)) if quote.get("bid") is not None else None,
                    ask=float(quote.get("ask", 0)) if quote.get("ask") is not None else None,
                    price=float(quote.get("regularMarketPrice", 0)),
                    timestamp=timestamp,
                    source=self.name,
                )
