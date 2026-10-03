from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List

import aiohttp
from dateutil import parser

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


class FrankfurterProvider(IDataProvider):
    """Free, no-key forex provider backed by the European Central Bank.

    Endpoint: https://api.frankfurter.app
    Rate limit: none enforced (ECB-published, ~daily resolution is the
    bottleneck for intraday timeframes).
    Best for: EUR-based pairs, daily/4h analysis as a last-resort
    fallback when all paid providers are rate-limited.
    """
    name = "Frankfurter"
    _BASE_URL = "https://api.frankfurter.app"

    # Frankfurter only supports these pairs (ECB feed).
    _SUPPORTED = {
        "EURUSD": ("EUR", "USD"),
        "GBPUSD": ("GBP", "USD"),
        "USDJPY": ("USD", "JPY"),
        "AUDUSD": ("AUD", "USD"),
        "USDCHF": ("USD", "CHF"),
        "USDCAD": ("USD", "CAD"),
        "NZDUSD": ("NZD", "USD"),
    }

    def __init__(self, config: dict = None):
        self.config = config or {}

    async def connect(self) -> None:
        return  # No credentials needed.

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, forex=True)

    async def health(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE_URL}/latest?from=EUR&to=USD",
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
                        confidence=60 if ok else 0,
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
        pair = self._SUPPORTED.get(symbol.upper())
        if not pair:
            raise RuntimeError(f"Frankfurter does not support {symbol}")
        from_cur, to_cur = pair

        # Frankfurter timeframe handling: daily only on the free feed.
        # For intraday we still fetch the latest and synthesize one candle.
        # This is a fallback by design — the chain should prefer intraday
        # providers when available.
        async with aiohttp.ClientSession() as session:
            url = f"{self._BASE_URL}/latest"
            params = {"from": from_cur, "to": to_cur}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Frankfurter HTTP {resp.status}")
                data = await resp.json()
                rate = data.get("rates", {}).get(to_cur)
                if rate is None:
                    raise RuntimeError("Frankfurter returned no rate")
                date_str = data.get("date", datetime.now(timezone.utc).date().isoformat())
                ts = int(parser.isoparse(date_str).timestamp())
                return [NormalizedCandle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(rate),
                    high=float(rate),
                    low=float(rate),
                    close=float(rate),
                    volume=0.0,
                    timestamp=ts,
                    close_time=ts,
                    source=self.name,
                    is_closed=True,
                )]

    async def get_quote(self, symbol: str) -> MarketQuote:
        candles = await self.get_candles(symbol, "1h", count=1)
        if not candles:
            raise RuntimeError("Frankfurter returned no quote")
        c = candles[-1]
        return MarketQuote(
            symbol=symbol,
            bid=None,
            ask=None,
            price=c.close,
            timestamp=c.timestamp,
            source=self.name,
        )
