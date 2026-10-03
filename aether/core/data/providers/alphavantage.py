from __future__ import annotations

import time
from datetime import timezone
from typing import List

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
    datetime_from_ts,
)


class AlphaVantageProvider(IDataProvider):
    name = "AlphaVantage"
    _DEFAULT_BASE_URL = "https://www.alphavantage.co"

    def __init__(self, config: dict):
        self.config = config or {}
        self.api_key_env = self.config.get("api_key_env")
        self.base_url = self.config.get("base_url") or self._DEFAULT_BASE_URL
        self.api_key = None

    async def connect(self) -> None:
        if not self.api_key_env:
            raise RuntimeError("AlphaVantage provider requires api_key_env")
        self.api_key = get_secret(self.api_key_env)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, forex=True)

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
                confidence=90 if not stale else 35,
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
        if isinstance(provider_symbol, tuple):
            from_symbol, to_symbol = provider_symbol
        else:
            raise RuntimeError(f"AlphaVantage requires a pair tuple for {symbol}")
        interval = SymbolMapper.to_provider_timeframe(self.name, timeframe)
        url = f"{self.base_url}/query"
        params = {
            "function": "FX_INTRADAY",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "interval": interval,
            "outputsize": "compact",
            "apikey": self.api_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=30) as resp:
                # Detect rate-limit / note responses that come with 200 OK
                body_text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"AlphaVantage candle fetch HTTP {resp.status}: {body_text[:200]}")
                try:
                    data = await resp.json(content_type=None) if hasattr(resp, "json") else __import__("json").loads(body_text)
                except Exception:
                    raise RuntimeError(f"AlphaVantage candle fetch: non-JSON body (status {resp.status}): {body_text[:200]}")
                # AlphaVantage returns rate-limit notes inside the JSON body
                if isinstance(data, dict) and ("Note" in data or "Information" in data):
                    msg = data.get("Note") or data.get("Information")
                    raise RuntimeError(f"AlphaVantage rate-limited: {msg[:200]}")
                time_series_key = next((k for k in data.keys() if "Time Series" in k), None)
                if not time_series_key or not data.get(time_series_key):
                    raise RuntimeError(f"AlphaVantage candle fetch failed: {data}")
                raw = data[time_series_key]
                candles = []
                for timestamp_text in sorted(raw.keys()):
                    entry = raw[timestamp_text]
                    parsed = parser.parse(timestamp_text)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    timestamp = int(parsed.timestamp())
                    candles.append(NormalizedCandle(
                        symbol=symbol,
                        timeframe=timeframe,
                        open=float(entry["1. open"]),
                        high=float(entry["2. high"]),
                        low=float(entry["3. low"]),
                        close=float(entry["4. close"]),
                        volume=0.0,
                        timestamp=timestamp,
                        close_time=timestamp,
                        source=self.name,
                        is_closed=True,
                    ))
                return candles[-count:]

    async def get_quote(self, symbol: str) -> MarketQuote:
        provider_symbol = SymbolMapper.to_provider_symbol(self.name, symbol)
        if isinstance(provider_symbol, tuple):
            from_symbol, to_symbol = provider_symbol
        else:
            raise RuntimeError(f"AlphaVantage requires a pair tuple for {symbol}")
        url = f"{self.base_url}/query"
        params = {
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_symbol,
            "to_currency": to_symbol,
            "apikey": self.api_key,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=20) as resp:
                body_text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"AlphaVantage quote fetch HTTP {resp.status}: {body_text[:200]}")
                try:
                    data = await resp.json(content_type=None) if hasattr(resp, "json") else __import__("json").loads(body_text)
                except Exception:
                    raise RuntimeError(f"AlphaVantage quote fetch: non-JSON body: {body_text[:200]}")
                if isinstance(data, dict) and ("Note" in data or "Information" in data):
                    msg = data.get("Note") or data.get("Information")
                    raise RuntimeError(f"AlphaVantage rate-limited: {msg[:200]}")
                payload = data.get("Realtime Currency Exchange Rate")
                if not payload:
                    raise RuntimeError(f"AlphaVantage quote fetch failed: {data}")
                price = float(payload.get("5. Exchange Rate", 0))
                ts = int(float(payload.get("6. Last Refreshed", now_timestamp())))
                return MarketQuote(
                    symbol=symbol,
                    bid=None,
                    ask=None,
                    price=price,
                    timestamp=ts,
                    source=self.name,
                )
