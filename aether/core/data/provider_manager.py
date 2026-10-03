from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from aether.core.data.provider import (
    IDataProvider,
    MarketQuote,
    MarketStatus,
    ProviderCapabilities,
    ProviderHealth,
    SymbolMapper,
    compute_staleness,
    now_timestamp,
    datetime_from_ts,
)
from aether.core.utils.logger import logger

from .providers.alphavantage import AlphaVantageProvider
from .providers.coingecko import CoinGeckoProvider
from .providers.finnhub import FinnhubProvider
from .providers.frankfurter import FrankfurterProvider
from .providers.oanda import OandaProvider
from .providers.twelvedata import TwelveDataProvider
from .providers.yahoo import YahooProvider


class DataProviderManager:
    # Order: paid/key-based providers first (best data), then no-key fallbacks
    # so the chain has a working source even when every API key is exhausted.
    DEFAULT_CHAIN = [
        "TwelveData",     # 8 req/min free
        "Finnhub",        # 60 req/min free (needs key)
        "AlphaVantage",   # 25 req/day free
        "Yahoo",          # no key, IP-limited
        "Oanda",          # 1000 req/day free (needs key)
        "Frankfurter",    # no key, unlimited (daily resolution)
        "CoinGecko",      # no key, ~10-30 req/min (crypto only)
    ]
    # No-key fallbacks are always appended last in the candidate chain
    # (operator can't forget them; they appear in the right place even if
    # misconfigured).
    NO_KEY_FALLBACKS = ["Frankfurter", "CoinGecko"]
    # Backoff per provider category: keyless providers recover quickly because
    # they fail for transient reasons (rate-limit at the CDN), while a keyed
    # provider that just returned 401 should stay out of rotation longer.
    BACKOFF_SECONDS_KEYED = 60
    BACKOFF_SECONDS_KEYLESS = 15
    HEALTH_POLL_INTERVAL = 180

    # Names of providers that don't need an API key.
    KEYLESS_PROVIDERS = {"Yahoo", "Frankfurter", "CoinGecko"}

    PROVIDER_CLASSES: Dict[str, Type[IDataProvider]] = {
        "TwelveData": TwelveDataProvider,
        "Finnhub": FinnhubProvider,
        "AlphaVantage": AlphaVantageProvider,
        "Yahoo": YahooProvider,
        "Oanda": OandaProvider,
        "Frankfurter": FrankfurterProvider,
        "CoinGecko": CoinGeckoProvider,
    }

    def __init__(self, config: Any, bus: Any):
        self.config = config
        self.bus = bus
        self.providers: Dict[str, IDataProvider] = {}
        self.provider_health: Dict[str, ProviderHealth] = {}
        self._backoff_until: Dict[str, float] = {}
        self.active_provider_name: Optional[str] = None
        self.last_successful_provider: Optional[str] = None
        self.last_successful_update: Optional[int] = None
        self._market_status: Dict[str, MarketStatus] = {}
        self._health_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self.state: str = "ACTIVE"
        self._initialize_providers()

    def _initialize_providers(self) -> None:
        configured_chain = None
        try:
            configured_chain = self.config.get("data.provider_chain")
        except Exception:
            configured_chain = None

        if isinstance(configured_chain, list) and configured_chain:
            chain = [p for p in configured_chain if p in self.PROVIDER_CLASSES]
        else:
            chain = [p for p in self.DEFAULT_CHAIN if p in self.PROVIDER_CLASSES]

        provider_configs = {}
        try:
            provider_configs = self.config.get("data.providers") or {}
        except Exception:
            provider_configs = {}

        legacy_oanda = None
        try:
            legacy_oanda = self.config.get("data.oanda")
        except Exception:
            legacy_oanda = None

        for name in chain:
            cfg = provider_configs.get(name, {})
            if name == "Oanda" and not cfg and legacy_oanda:
                cfg = legacy_oanda
            enabled = cfg.get("enabled", True)
            if not enabled:
                continue
            provider_cls = self.PROVIDER_CLASSES.get(name)
            if provider_cls is None:
                continue
            self.providers[name] = provider_cls(cfg)
            logger.info("[DataProviderManager] added provider %s", name)

        if not self.providers:
            raise RuntimeError("No enabled data providers configured")

    async def start(self) -> None:
        await self._connect_providers()
        await self._refresh_health()
        self._refresh_state()
        self._health_task = asyncio.create_task(self._health_loop())
        self._poll_task = asyncio.create_task(self._polling_loop())
        logger.info("[DataProviderManager] started")

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("[DataProviderManager] stopped")

    async def _connect_providers(self) -> None:
        for name, provider in self.providers.items():
            try:
                await provider.connect()
            except Exception as exc:
                logger.warning("[DataProviderManager] provider connect failed: %s %s", name, exc)
                await self._mark_provider_failed(name, str(exc))

    async def _health_loop(self) -> None:
        while True:
            try:
                await self._refresh_health()
            except Exception as exc:
                logger.exception("[DataProviderManager] health loop error: %s", exc)
            await asyncio.sleep(self.HEALTH_POLL_INTERVAL)

    async def _refresh_health(self) -> None:
        for name, provider in self.providers.items():
            try:
                health = await provider.health()
                self.provider_health[name] = health
                await self._publish_provider_health(health)
            except Exception as exc:
                await self._mark_provider_failed(name, str(exc))
        self._refresh_state()

    async def _polling_loop(self) -> None:
        """Background loop. All cadence / count parameters are config-driven.

        A single provider failure never aborts the cycle: we move on to the
        next symbol/timeframe so the operator still gets partial data.
        """
        cycle_failures = 0
        while True:
            successful_symbols: List[str] = []
            failed_symbols: List[Dict[str, str]] = []
            cycle_start = time.time()
            try:
                # Resolve polling config (all optional, with sensible defaults)
                try:
                    symbols = self.config.get("data.symbols")
                except Exception:
                    symbols = ["EURUSD"]
                if not symbols: symbols = ["EURUSD"]

                try:
                    timeframes = self.config.get("data.timeframes")
                except Exception:
                    timeframes = ["1h"]
                if not timeframes: timeframes = ["1h"]

                try:
                    poll_count = self.config.get("data.poll_count")
                except Exception:
                    poll_count = 1
                if not poll_count: poll_count = 1

                try:
                    stagger_seconds = float(self.config.get("data.request_stagger_seconds") or 0)
                except Exception:
                    stagger_seconds = 0.0

                for symbol in symbols:
                    symbol_ok = True
                    for tf in timeframes:
                        try:
                            # 1. Candles
                            candles = await self.get_candles(symbol, tf, count=poll_count)
                        except Exception as exc:
                            logger.warning(
                                "[DataProviderManager] candles failed for %s %s: %s — "
                                "trying next symbol",
                                symbol, tf, exc,
                            )
                            failed_symbols.append({"symbol": symbol, "timeframe": tf, "stage": "candles", "error": str(exc)})
                            symbol_ok = False
                            break
                        if candles:
                            candle = candles[-1]
                            await self.bus.publish("data.candle", candle.model_dump())
                            self._update_market_status(symbol, tf, candle.timestamp)

                        # 2. Quote
                        try:
                            quote = await self.get_quote(symbol)
                        except Exception as exc:
                            logger.warning(
                                "[DataProviderManager] quote failed for %s: %s",
                                symbol, exc,
                            )
                            failed_symbols.append({"symbol": symbol, "timeframe": tf, "stage": "quote", "error": str(exc)})
                            # Quote failure isn't fatal for the candle we've
                            # already got — keep the symbol as "ok" if candles
                            # succeeded.
                            continue
                        await self.bus.publish("data.quote", quote.model_dump())

                        if stagger_seconds > 0:
                            await asyncio.sleep(stagger_seconds)

                    if symbol_ok:
                        successful_symbols.append(symbol)

                cycle_ok = not failed_symbols
                if cycle_ok:
                    cycle_failures = 0
                else:
                    cycle_failures += 1

                # Surface cycle-level health on the bus so the operator menu
                # and the dashboard can show "last fetch worked for EURUSD
                # via Frankfurter, BTCUSD failed".
                try:
                    await self.bus.publish("data.fetch_cycle_complete", {
                        "successful_symbols": successful_symbols,
                        "failed_symbols": failed_symbols,
                        "provider_used": self.last_successful_provider,
                        "duration_ms": int((time.time() - cycle_start) * 1000),
                        "active_provider": self.active_provider_name,
                    })
                except Exception:
                    logger.exception("[DataProviderManager] publish cycle event failed")

                # If every provider is in backoff, wait for the longest backoff
                if not cycle_ok and self._backoff_until:
                    soonest = min(self._backoff_until.values()) - time.time()
                    if 0 < soonest < 60:
                        logger.info("[DataProviderManager] all providers cooling off; "
                                    "skipping next poll in %.0fs", soonest)
                        await asyncio.sleep(soonest)
                        continue

            except Exception as exc:
                logger.exception("[DataProviderManager] polling loop error: %s", exc)
                cycle_failures += 1

            try:
                interval = self.config.get("data.poll_interval")
            except Exception:
                interval = 60
            if not interval: interval = 60
            await asyncio.sleep(interval)

    async def get_candles(self, symbol: str, timeframe: str, count: int = 200) -> List[NormalizedCandle]:
        candidate_names = await self._candidate_providers("candles")
        last_error = None
        for name in candidate_names:
            provider = self.providers[name]
            try:
                candles = await provider.get_candles(symbol, timeframe, count)
                if candles:
                    closed_candles = [candle for candle in candles if candle.is_closed]
                    if not closed_candles:
                        await self._mark_provider_failed(name, "no_closed_candles")
                        continue
                    closed_candles.sort(key=lambda candle: candle.timestamp)
                    latest = closed_candles[-1]
                    if compute_staleness(timeframe, latest.timestamp):
                        await self._publish_stale_detected(name, symbol, timeframe, latest.timestamp)
                        await self._mark_provider_failed(name, "stale_data")
                        continue

                    previous_provider = self.active_provider_name or self.last_successful_provider
                    self.active_provider_name = name
                    self.last_successful_provider = name
                    self.last_successful_update = now_timestamp()
                    if previous_provider != name:
                        await self._publish_provider_changed(
                            previous_provider,
                            name,
                            "failover" if previous_provider is not None else "initial_selection",
                        )
                    return closed_candles[-count:]
            except Exception as exc:
                last_error = exc
                await self._mark_provider_failed(name, str(exc))
        
        if last_error:
            raise last_error
        raise RuntimeError("no provider available for candles")

    async def get_quote(self, symbol: str) -> MarketQuote:
        candidate_names = await self._candidate_providers("quotes")
        last_error = None
        for name in candidate_names:
            provider = self.providers[name]
            try:
                quote = await provider.get_quote(symbol)
                if quote:
                    self.active_provider_name = name
                    self.last_successful_provider = name
                    self.last_successful_update = now_timestamp()
                    return quote
            except Exception as exc:
                last_error = exc
                await self._mark_provider_failed(name, str(exc))
        
        if last_error:
            raise last_error
        raise RuntimeError("no provider available for quotes")

    async def health(self) -> Dict[str, Any]:
        return {name: health.model_dump() for name, health in self.provider_health.items()}

    async def _candidate_providers(self, capability: str) -> List[str]:
        """Return usable providers that advertise the given capability,
        ordered by the configured chain (data.provider_chain) so that
        failover is deterministic and matches the user's intent.
        """
        # Try to read the user-configured chain order
        chain_order: List[str] = []
        try:
            chain_order = list(self.config.get("data.provider_chain") or [])
        except Exception:
            chain_order = []
        # If no chain configured, fall back to the dict insertion order
        if not chain_order:
            chain_order = list(self.providers.keys())
        # Always append the no-key fallbacks (deduplicated, in the canonical
        # order) so the chain can never be empty just because the operator
        # forgot to enable them.
        for fallback in self.NO_KEY_FALLBACKS:
            if fallback in self.providers and fallback not in chain_order:
                chain_order.append(fallback)

        candidates: List[str] = []
        for name in chain_order:
            if name not in self.providers:
                continue
            if not self._is_provider_usable(name):
                continue
            try:
                caps = await self.providers[name].capabilities()
            except Exception:
                caps = ProviderCapabilities()
            if getattr(caps, capability, False):
                candidates.append(name)
        return candidates

    def _is_provider_usable(self, name: str) -> bool:
        if name in self._backoff_until and time.time() < self._backoff_until[name]:
            return False
        health = self.provider_health.get(name)
        # No health record yet → allow the call (this is the cold-start path).
        # A failed health record → skip. An explicit healthy record → allow.
        if health is None:
            return True
        return bool(health.healthy)

    def _refresh_state(self) -> None:
        if any(health.healthy for health in self.provider_health.values()):
            if self.state == "SUSPENDED":
                self.state = "ACTIVE"
            elif any(health.confidence < 50 for health in self.provider_health.values() if health.healthy):
                self.state = "DEGRADED"
            else:
                self.state = "ACTIVE"
        else:
            self.state = "SUSPENDED"

    async def _mark_provider_failed(self, name: str, reason: str) -> None:
        # Keyless providers get a shorter cool-down so transient rate-limits
        # (usually at the CDN/proxy level) clear quickly.  Keyed providers
        # stay out of rotation longer to avoid hammering a bad credential.
        backoff = (
            self.BACKOFF_SECONDS_KEYLESS
            if name in self.KEYLESS_PROVIDERS
            else self.BACKOFF_SECONDS_KEYED
        )
        self._backoff_until[name] = time.time() + backoff
        self.provider_health[name] = ProviderHealth(
            provider=name,
            healthy=False,
            status="failed",
            checked_at=datetime.fromtimestamp(time.time(), tz=timezone.utc),
            latency_ms=None,
            confidence=0,
            stale=False,
            error=reason,
            details={"backoff_until": str(self._backoff_until[name]), "backoff_seconds": str(backoff)},
        )
        await self._publish_provider_failed(name, reason)
        await self._publish_provider_health(self.provider_health[name])
        self._refresh_state()

    def _update_market_status(self, symbol: str, timeframe: str, last_candle_ts: int) -> None:
        status = self._compute_market_status(last_candle_ts, timeframe)
        key = f"{symbol}:{timeframe}"
        previous = self._market_status.get(key)
        self._market_status[key] = status
        if previous != status:
            topic = "market.opened" if status == MarketStatus.OPEN else "market.closed"
            if status == MarketStatus.LOW_LIQUIDITY:
                topic = "market.low_liquidity"
            payload = {
                "symbol": symbol,
                "timeframe": timeframe,
                "status": status.value,
                "last_candle_timestamp": last_candle_ts,
                "detected_at": datetime.now(timezone.utc).isoformat(),
            }
            if self.bus:
                asyncio.create_task(self.bus.publish(topic, payload))

    def _compute_market_status(self, last_candle_ts: int, timeframe: str) -> MarketStatus:
        now = datetime.now(timezone.utc)
        last_candle = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc)
        if now.weekday() >= 5:
            return MarketStatus.CLOSED
        if compute_staleness(timeframe, last_candle_ts):
            return MarketStatus.CLOSED
        if (now - last_candle).total_seconds() > 2 * 60 * 60:
            return MarketStatus.LOW_LIQUIDITY
        return MarketStatus.OPEN

    async def _publish_provider_changed(self, previous_provider: Optional[str], provider: str, reason: str) -> None:
        if self.bus:
            await self.bus.publish("data.provider_changed", {
                "previous_provider": previous_provider,
                "new_provider": provider,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    async def _publish_provider_failed(self, provider: str, reason: str) -> None:
        if self.bus:
            await self.bus.publish("data.provider_failed", {
                "provider": provider,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    async def _publish_provider_health(self, health: ProviderHealth) -> None:
        if self.bus:
            await self.bus.publish("data.provider_health", health.model_dump())

    async def _publish_stale_detected(self, provider: str, symbol: str, timeframe: str, last_candle_ts: int) -> None:
        if self.bus:
            await self.bus.publish("data.stale_detected", {
                "provider": provider,
                "symbol": symbol,
                "timeframe": timeframe,
                "last_candle_time": datetime.fromtimestamp(last_candle_ts, tz=timezone.utc).isoformat(),
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "reason": "stale_data",
            })
