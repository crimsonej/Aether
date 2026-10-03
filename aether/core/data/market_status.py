from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from aether.core.data.provider import MarketStatus, compute_staleness


class MarketStatusService:
    """Compute market status per symbol/timeframe and publish events.

    Subscribes to `data.candle` events (payload must include `symbol`,
    `timeframe` and `timestamp`) and emits `market.opened`, `market.closed`,
    or `market.low_liquidity` when state transitions occur.
    """

    def __init__(self, config: Dict[str, Any], bus: Any):
        self.config = config or {}
        self.bus = bus
        self._status: Dict[str, MarketStatus] = {}

    async def register_events(self, bus: Any) -> None:
        # Register to listen for candle updates
        await bus.subscribe("data.candle", self._on_candle)

    async def _on_candle(self, payload: Dict[str, Any]) -> None:
        try:
            symbol = payload.get("symbol")
            timeframe = payload.get("timeframe")
            ts = payload.get("timestamp") or payload.get("last_candle_timestamp")
            if symbol is None or timeframe is None or ts is None:
                return
            key = f"{symbol}:{timeframe}"
            new_status = self._compute_market_status(int(ts), timeframe)
            previous = self._status.get(key)
            if previous != new_status:
                self._status[key] = new_status
                topic = "market.opened" if new_status == MarketStatus.OPEN else "market.closed"
                if new_status == MarketStatus.LOW_LIQUIDITY:
                    topic = "market.low_liquidity"
                payload_out = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": new_status.value,
                    "last_candle_timestamp": ts,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                }
                if new_status == MarketStatus.CLOSED:
                    payload_out["next_open_time"] = self._next_market_open().isoformat()
                await self.bus.publish(topic, payload_out)
        except Exception as exc:
            # Log and swallow to avoid crashing the bus worker in production.
            try:
                from aether.core.utils.logger import logger
                logger.exception("[MarketStatusService] error in _on_candle: %s", exc)
            except Exception:
                print(f"[MarketStatusService] error in _on_candle: {exc}")
            return

    def _compute_market_status(self, last_candle_ts: int, timeframe: str) -> MarketStatus:
        now = datetime.now(timezone.utc)
        last_candle = datetime.fromtimestamp(last_candle_ts, tz=timezone.utc)
        # Weekend check
        if now.weekday() >= 5:
            return MarketStatus.CLOSED
        # Staleness – treat as closed
        if compute_staleness(timeframe, last_candle_ts):
            return MarketStatus.CLOSED
        # Low liquidity heuristic: last update older than 2 hours
        if (now - last_candle).total_seconds() > 2 * 60 * 60:
            return MarketStatus.LOW_LIQUIDITY
        return MarketStatus.OPEN

    def _next_market_open(self) -> datetime:
        # Simple next-weekday at UTC 00:00 fallback for forex weekends.
        now = datetime.now(timezone.utc)
        # If Saturday or Sunday, advance to Monday 00:00 UTC
        days_ahead = 0
        if now.weekday() == 5:
            days_ahead = 2
        elif now.weekday() == 6:
            days_ahead = 1
        next_open = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
        return next_open


__all__ = ["MarketStatusService"]
