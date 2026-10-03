import aiohttp
from typing import List, Dict, Any, Optional
from aether.core.utils.logger import logger
from aether.core.data.normalizer import NormalizedCandle
from aether.core.config.loader import get_secret

class OandaClient:
    """
    Client for interacting with the Oanda REST API to fetch market data.
    """
    def __init__(self, base_url: str, api_key_env: str, account_id_env: str):
        self.base_url = base_url
        self.api_key_env = api_key_env
        self.account_id_env = account_id_env
        self.api_key = get_secret(api_key_env)
        self.account_id = get_secret(account_id_env)
        self.session: Optional[aiohttp.ClientSession] = None
        # For subsystem compatibility
        self._running = False

    # --- Subsystem lifecycle methods ---
    async def start(self) -> None:
        """No-op start for compatibility."""
        self._running = True

    async def stop(self) -> None:
        """Close the HTTP session."""
        self._running = False
        if self.session and not self.session.closed:
            await self.session.close()

    async def health(self) -> dict:
        """Return health status based on session availability."""
        is_ok = self._running
        if self.session and self.session.closed:
            is_ok = False
        return {
            "status": "OK" if is_ok else "ERROR",
            "details": {"api_key_env": self.api_key_env if hasattr(self, 'api_key_env') else "unknown"}
        }

    async def metrics(self) -> dict:
        """Return basic metrics (placeholder)."""
        return {}

    async def get_candles(self, symbol: str, granularity: str, count: int = 200) -> List[Dict[str, Any]]:
        """
        Fetches candles from Oanda API.

        Args:
            symbol (str): Symbol to fetch (e.g., 'EUR_USD').
            granularity (str): Candle timeframe (e.g., 'H1').
            count (int): Number of candles to fetch.

        Returns:
            List[Dict[str, Any]]: Raw candle data from Oanda.
        """
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.api_key}"})
        endpoint = f"{self.base_url}/v3/instruments/{symbol}/candles"
        params = {
            "granularity": granularity,
            "count": count,
            "price": "M" # Mid price
        }

        try:
            async with self.session.get(endpoint, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("candles", [])
        except Exception as e:
            logger.error("oanda_fetch_failed", symbol=symbol, error=str(e))
            return []

    async def fetch_normalized_candles(self, symbol: str, timeframe: str, count: int = 200) -> List[NormalizedCandle]:
        """
        Fetches candles and normalizes them.
        """
        # Map internal timeframe to Oanda granularity
        granularity_map = {
            "15m": "M15",
            "1h": "H1",
            "4h": "H4"
        }
        gran = granularity_map.get(timeframe, "H1")

        # Oanda expects EUR_USD, we use EURUSD
        oanda_symbol = symbol.replace("USD", "_USD") if "USD" in symbol else symbol

        raw_candles = await self.get_candles(oanda_symbol, gran, count)
        normalized = []

        for candle in raw_candles:
            # Oanda candles: {'time': '...', 'mid': {'o': ..., 'h': ..., 'l': ..., 'c': ..., 'v': ...}, 'complete': True}
            try:
                normalized.append(NormalizedCandle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(candle['mid']['o']),
                    high=float(candle['mid']['h']),
                    low=float(candle['mid']['l']),
                    close=float(candle['mid']['c']),
                    volume=float(candle['volume']),
                    timestamp=self._parse_timestamp(candle['time']),
                    close_time=self._parse_timestamp(candle['time']), # Simplified for now
                    source="oanda",
                    is_closed=candle['complete']
                ))
            except KeyError as e:
                logger.warn("candle_parsing_error", error=str(e))

        return normalized

    def _parse_timestamp(self, time_str: str) -> int:
        # Very simplified timestamp parsing for V1 bootstrap
        import dateutil.parser
        return int(dateutil.parser.parse(time_str).timestamp())
