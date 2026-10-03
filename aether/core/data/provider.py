from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Protocol

from pydantic import BaseModel, Field

from aether.core.data.normalizer import NormalizedCandle


class MarketStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"


class ProviderCapabilities(BaseModel):
    candles: bool = True
    quotes: bool = True
    forex: bool = False
    crypto: bool = False
    economic_calendar: bool = False
    news: bool = False


class ProviderHealth(BaseModel):
    provider: str
    healthy: bool
    status: str
    checked_at: datetime
    latency_ms: Optional[int] = None
    confidence: int = 0
    stale: bool = False
    error: Optional[str] = None
    details: Dict[str, Optional[str]] = Field(default_factory=dict)


def datetime_from_ts(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class MarketQuote(BaseModel):
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    price: Optional[float]
    timestamp: int
    source: str


class IDataProvider(Protocol):
    name: str

    async def connect(self) -> None:
        ...

    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> List[NormalizedCandle]:
        ...

    async def get_quote(self, symbol: str) -> MarketQuote:
        ...

    async def health(self) -> ProviderHealth:
        ...

    async def capabilities(self) -> ProviderCapabilities:
        ...


class SymbolMapper:
    _provider_symbol_map = {
        "TwelveData": {
            "EURUSD": "EUR/USD",
            "GBPUSD": "GBP/USD",
            "USDJPY": "USD/JPY",
            "AUDUSD": "AUD/USD",
            "USDCHF": "USD/CHF",
            "USDCAD": "USD/CAD",
            "NZDUSD": "NZD/USD",
            "XAUUSD": "XAU/USD",
        },
        "AlphaVantage": {
            "EURUSD": ("EUR", "USD"),
            "GBPUSD": ("GBP", "USD"),
            "USDJPY": ("USD", "JPY"),
            "AUDUSD": ("AUD", "USD"),
            "USDCHF": ("USD", "CHF"),
            "USDCAD": ("USD", "CAD"),
            "NZDUSD": ("NZD", "USD"),
            "XAUUSD": ("XAU", "USD"),
        },
        "Yahoo": {
            "EURUSD": "EURUSD=X",
            "GBPUSD": "GBPUSD=X",
            "USDJPY": "USDJPY=X",
            "AUDUSD": "AUDUSD=X",
            "USDCHF": "USDCHF=X",
            "USDCAD": "USDCAD=X",
            "NZDUSD": "NZDUSD=X",
            "XAUUSD": "XAUUSD=X",
        },
        "Oanda": {
            "EURUSD": "EUR_USD",
            "GBPUSD": "GBP_USD",
            "USDJPY": "USD_JPY",
            "AUDUSD": "AUD_USD",
            "USDCHF": "USD_CHF",
            "USDCAD": "USD_CAD",
            "NZDUSD": "NZD_USD",
            "XAUUSD": "XAU_USD",
        },
        "Finnhub": {
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
        },
        "Frankfurter": {
            "EURUSD": ("EUR", "USD"),
            "GBPUSD": ("GBP", "USD"),
            "USDJPY": ("USD", "JPY"),
            "AUDUSD": ("AUD", "USD"),
            "USDCHF": ("USD", "CHF"),
            "USDCAD": ("USD", "CAD"),
            "NZDUSD": ("NZD", "USD"),
        },
    }

    _provider_timeframe_map = {
        "TwelveData": {
            "15m": "15min",
            "1h": "1h",
            "4h": "4h",
            "1d": "1day",
        },
        "AlphaVantage": {
            "15m": "15min",
            "1h": "60min",
            "4h": "60min",
        },
        "Yahoo": {
            "15m": "15m",
            "1h": "1h",
            "4h": "1h",
        },
        "Oanda": {
            "15m": "M15",
            "1h": "H1",
            "4h": "H4",
        },
        "Finnhub": {
            "15m": 15,
            "1h": 60,
            "4h": 60,
            "1d": "D",
        },
        "Frankfurter": {
            "15m": "1d",
            "1h": "1d",
            "4h": "1d",
            "1d": "1d",
        },
        "CoinGecko": {
            "15m": 1,
            "1h": 1,
            "4h": 1,
            "1d": 30,
        },
    }

    @classmethod
    def to_provider_symbol(cls, provider: str, symbol: str) -> str:
        symbol = symbol.upper()
        mapping = cls._provider_symbol_map.get(provider, {})
        return mapping.get(symbol, symbol)

    @classmethod
    def to_provider_timeframe(cls, provider: str, timeframe: str) -> str:
        mapping = cls._provider_timeframe_map.get(provider, {})
        return mapping.get(timeframe, timeframe)

    @classmethod
    def normalize_internal_symbol(cls, value: str) -> str:
        return value.replace("/", "").replace("=X", "").upper()


def now_timestamp() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def compute_staleness(timeframe: str, last_timestamp: int) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    if timeframe == "15m":
        threshold = 3 * 60 * 60
    elif timeframe == "1h":
        threshold = 6 * 60 * 60
    elif timeframe == "4h":
        threshold = 12 * 60 * 60
    else:
        threshold = 8 * 60 * 60
    return (now - last_timestamp) > threshold


def normalize_timeframe(timeframe: str) -> str:
    return timeframe.lower()
