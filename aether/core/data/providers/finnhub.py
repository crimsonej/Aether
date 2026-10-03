from __future__ import annotations

import time
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


class FinnhubProvider(IDataProvider):
    """Finnhub.io — 60 req/min on the free tier.

    Sign up at https://finnhub.io/register for a free API key.
    Env var: FINNHUB_API_KEY

    Supports: US stocks, forex, crypto with intraday resolution.
    """
    name = "Finnhub"
    _BASE_URL = "https://finnhub.io/api/v1"

    # Supported pairs (Finnhub uses OANDA-like symbols).
    _SUPPORTED = {
        "EURUSD": "OANDA:EUR_USD",
        "GBPUSD": "OANDA:GBP_USD",
        "USDJPY": "OANDA:USD_JPY",
        "AUDUSD": "OANDA:AUD_USD",
        "USDCHF": "OANDA:USD_CHF",
        "USDCAD": "OANDA:USD_CAD",
        "NZDUSD": "OANDA:NZD_USD",
        "XAUUSD": "OANDA:XAU_USD",
        "BTCUSD": "BINANCE:BTCUSDT",
        "ETHUSD": "BINANCE:ETHUSDT",
    }

    _TIMEFRAME_RESOLUTION = {
        "15m": 15, "1h": 60, "4h": 60, "1d": "D",
    }

    def __init__(self, config: dict):
        self.config = config or {}
        self.api_key_env = self.config.get("api_key_env", "FINNHUB_API_KEY")
        self.api_key = None

    async def connect(self) -> None:
        if not self.api_key_env:
            raise RuntimeError("Finnhub provider requires api_key_env")
        self.api_key = get_secret(self.api_key_env)

    async def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(candles=True, quotes=True, forex=True, crypto=True)

    async def health(self) -> ProviderHealth:
        start = time.monotonic()
        try:
            if self.api_key is None:
                await self.connect()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE_URL}/quote",
                    params={"symbol": "OANDA:EUR_USD", "token": self.api_key},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    ok = resp.status == 200
                    data = await resp.json() if ok else {}
                    has_data = bool(data.get("c"))
                    latency_ms = int((time.monotonic() - start) * 1000)
                    return ProviderHealth(
                        provider=self.name,
                        healthy=ok and has_data,
                        status="ok" if has_data else "error",
                        checked_at=datetime_from_ts(now_timestamp()),
                        latency_ms=latency_ms,
                        confidence=90 if has_data else 0,
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
        fh_symbol = self._SUPPORTED.get(symbol.upper())
        if not fh_symbol:
            raise RuntimeError(f"Finnhub does not support {symbol}")
        resolution = self._TIMEFRAME_RESOLUTION.get(timeframe, 60)
        if resolution == "D":
            resolution = "D"

        # Compute from/to timestamps (last 7 days for intraday, 1 year for daily).
        import time as _t
        to_ts = int(_t.time())
        if resolution == "D":
            from_ts = to_ts - 365 * 24 * 3600
        else:
            from_ts = to_ts - 7 * 24 * 3600

        async with aiohttp.ClientSession() as session:
            url = f"{self._BASE_URL}/stock/candle"
            params = {
                "symbol": fh_symbol,
                "resolution": str(resolution),
                "from": from_ts,
                "to": to_ts,
                "token": self.api_key,
            }
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 429:
                    raise RuntimeError("Finnhub rate-limited (HTTP 429)")
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Finnhub HTTP {resp.status}: {body[:200]}")
                data = await resp.json()
                if data.get("s") != "ok":
                    raise RuntimeError(f"Finnhub: status={data.get('s')} data={data}")
                candles = []
                for i, ts in enumerate(data.get("t", [])):
                    candles.append(NormalizedCandle(
                        symbol=symbol,
                        timeframe=timeframe,
                        open=float(data["o"][i]),
                        high=float(data["h"][i]),
                        low=float(data["l"][i]),
                        close=float(data["c"][i]),
                        volume=float(data.get("v", [0] * len(data["t"]))[i] or 0.0),
                        timestamp=int(ts),
                        close_time=int(ts),
                        source=self.name,
                        is_closed=True,
                    ))
                return candles[-count:]

    async def get_quote(self, symbol: str) -> MarketQuote:
        fh_symbol = self._SUPPORTED.get(symbol.upper())
        if not fh_symbol:
            raise RuntimeError(f"Finnhub does not support {symbol}")
        async with aiohttp.ClientSession() as session:
            url = f"{self._BASE_URL}/quote"
            params = {"symbol": fh_symbol, "token": self.api_key}
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 429:
                    raise RuntimeError("Finnhub rate-limited (HTTP 429)")
                if resp.status != 200:
                    raise RuntimeError(f"Finnhub HTTP {resp.status}")
                data = await resp.json()
                price = data.get("c")
                ts = int(data.get("t", now_timestamp()))
                if price is None or price == 0:
                    raise RuntimeError("Finnhub returned no price")
                return MarketQuote(
                    symbol=symbol,
                    bid=None,
                    ask=None,
                    price=float(price),
                    timestamp=ts,
                    source=self.name,
                )
