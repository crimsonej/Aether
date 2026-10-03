import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, List
from zoneinfo import ZoneInfo

from aether.core.utils.logger import logger


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "data"


def _current_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_expiry(expiry: Optional[str], start: datetime) -> Optional[datetime]:
    if not expiry or not isinstance(expiry, str):
        return None
    expiry = expiry.strip().lower()
    try:
        if expiry.endswith('h'):
            return start + timedelta(hours=float(expiry[:-1]))
        if expiry.endswith('d'):
            return start + timedelta(days=float(expiry[:-1]))
        if expiry.endswith('m'):
            return start + timedelta(minutes=float(expiry[:-1]))
    except ValueError:
        return None
    return None


def _format_timestamp(dt: Optional[datetime], tz: ZoneInfo) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(tz).isoformat()


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _load_operator_timezone(config: Optional[Any]) -> str:
    if config is None:
        return "UTC"
    try:
        tz = config.get("signal.operator_timezone", "UTC")
        return tz or "UTC"
    except Exception:
        return "UTC"


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


class SignalStateManager:
    def __init__(self, data_dir: Optional[str] = None, config: Optional[Any] = None):
        if data_dir is None:
            self.data_dir = _default_data_dir()
        else:
            self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.active_signals_file = self.data_dir / "active_signals.json"
        self.history_file = self.data_dir / "signal_journal.json"
        self.cooldown_file = self.data_dir / "cooldown_registry.json"

        # Ensure these are always dicts
        self.active_signals = self._load_json(self.active_signals_file)
        if not isinstance(self.active_signals, dict):
            self.active_signals = {}
        
        self.history = self._load_json(self.history_file)
        if not isinstance(self.history, dict):
            self.history = {}
            
        self.cooldowns = self._load_json(self.cooldown_file)
        if not isinstance(self.cooldowns, dict):
            self.cooldowns = {}

        self.operator_timezone = _load_operator_timezone(config)
        self.operator_zone = _resolve_timezone(self.operator_timezone)
        self.server_timezone = _resolve_timezone("UTC")
        self.bus = None
        self.stats = {
            "generated": 0,
            "activated": 0,
            "wins": 0,
            "losses": 0,
            "expired": 0,
            "missed": 0,
        }

    async def register_events(self, bus: Any) -> None:
        self.bus = bus
        await bus.subscribe("signal.emitted", self._on_signal_emitted)
        await bus.subscribe("data.candle", self._on_candle)

    def _load_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error("json_load_failed", path=str(path), error=str(e))
            return {}

    def _save_json(self, path: Path, data: Any):
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def _signal_price(self, signal: Dict[str, Any], key: str) -> Optional[float]:
        trade = signal.get("trade_construction")
        if not isinstance(trade, dict):
            return None
        return trade.get(key, {}).get("price")

    def _signal_progress(self, signal: Dict[str, Any], current_price: Optional[float]) -> Dict[str, Any]:
        entry = signal.get("entry_price")
        tp = signal.get("take_profit_price")
        sl = signal.get("stop_loss_price")
        if entry is None or tp is None or sl is None or current_price is None:
            return {}
        direction = signal.get("direction")
        if direction == "BUY":
            distance_from_entry = current_price - entry
            distance_to_tp = tp - current_price
            distance_to_sl = current_price - sl
            percent_progress = 0.0
            if tp != entry:
                percent_progress = max(0.0, min(100.0, 100.0 * distance_from_entry / (tp - entry)))
        else:
            distance_from_entry = entry - current_price
            distance_to_tp = current_price - tp
            distance_to_sl = sl - current_price
            percent_progress = 0.0
            if entry != tp:
                percent_progress = max(0.0, min(100.0, 100.0 * distance_from_entry / (entry - tp)))
        return {
            "percent": percent_progress,
            "percent_progress_toward_tp": percent_progress,
            "dist_to_tp": abs(tp - current_price) if tp else None,
            "dist_to_sl": abs(sl - current_price) if sl else None,
        }

    def _signal_base(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        generated_at = signal.get("generated_at") or signal.get("timestamp")
        generated_dt = _parse_iso_datetime(generated_at)
        operator_local = signal.get("operator_local_timestamp") or _format_timestamp(generated_dt, self.operator_zone)
        return {
            "signal_id": signal.get("signal_id"),
            "symbol": signal.get("symbol"),
            "timeframe": signal.get("timeframe"),
            "direction": signal.get("direction"),
            "strategy_name": signal.get("strategy_name"),
            "strategy_version": signal.get("strategy_version"),
            "schema_version": signal.get("schema_version", "1.0"),
            "source_candle_timestamp": signal.get("source_candle_timestamp"),
            "state": signal.get("state"),
            "confidence": signal.get("confidence", {}),
            "reason_tags": signal.get("reason_tags", []),
            "entry_price": signal.get("entry_price"),
            "take_profit_price": signal.get("take_profit_price"),
            "stop_loss_price": signal.get("stop_loss_price"),
            "trade_construction": signal.get("trade_construction", {}),
            "context_snapshot": signal.get("context_snapshot", {}),
            "feature_snapshot": signal.get("feature_snapshot", {}),
            "validation": signal.get("validation", {}),
            "market_snapshot_hash": signal.get("market_snapshot_hash"),
            "progress": signal.get("progress", {}),
            "timestamp": signal.get("timestamp"),
            "generated_at": generated_at,
            "activated_at": signal.get("activated_at"),
            "closed_at": signal.get("closed_at"),
            "close_price": signal.get("close_price"),
            "close_reason": signal.get("close_reason"),
            "operator_timezone": self.operator_timezone,
            "operator_local_timestamp": operator_local,
            "validity_seconds": signal.get("validity_seconds"),
        }

    async def _on_signal_emitted(self, signal_payload: Dict[str, Any]) -> None:
        # Expected payload from StrategyEngine
        signal_id = signal_payload.get("signal_id") or f"sig_{int(time.time())}"
        strategy = signal_payload.get("strategy") or {}
        strategy_name = signal_payload.get("strategy_name") or (
            strategy.get("name") if isinstance(strategy, dict) else None
        ) or "unknown"
        trade = signal_payload.get("trade_construction") or signal_payload.get("trade") or {}
        entry_price = signal_payload.get("entry_price")
        take_profit_price = signal_payload.get("take_profit_price")
        stop_loss_price = signal_payload.get("stop_loss_price")
        if isinstance(trade, dict):
            entry_price = entry_price if entry_price is not None else trade.get("entry", {}).get("price")
            take_profit_price = take_profit_price if take_profit_price is not None else trade.get("take_profit", {}).get("price")
            stop_loss_price = stop_loss_price if stop_loss_price is not None else trade.get("stop_loss", {}).get("price")
        now = _current_utc()
        context_snapshot = signal_payload.get("context_snapshot") or {}
        symbol = signal_payload.get("symbol")
        direction = signal_payload.get("direction")
        if symbol and direction:
            if self.check_cooldown(symbol, direction, strategy_name):
                await self._publish_suppressed(signal_payload, "cooldown")
                return
            if self._is_recent_duplicate(
                symbol, direction, strategy_name, entry_price, now
            ):
                await self._publish_suppressed(signal_payload, "duplicate_signal")
                return

            self.set_cooldown(
                symbol,
                direction,
                strategy_name,
                self._cooldown_seconds(context_snapshot),
            )

        entry_type = trade.get("entry", {}).get("type") if isinstance(trade, dict) else None
        initial_state = "ACTIVE" if entry_type == "market" else "PENDING"
        
        signal = {
            "signal_id": signal_id,
            "symbol": signal_payload.get("symbol"),
            "timeframe": signal_payload.get("timeframe"),
            "direction": signal_payload.get("direction"),
            "strategy_name": strategy_name,
            "strategy_version": signal_payload.get("strategy_version"),
            "schema_version": signal_payload.get("schema_version", "1.0"),
            "source_candle_timestamp": signal_payload.get("source_candle_timestamp"),
            "entry_price": entry_price,
            "take_profit_price": take_profit_price,
            "stop_loss_price": stop_loss_price,
            "state": initial_state,
            "timestamp": now.isoformat(),
            "generated_at": signal_payload.get("generated_at") or now.isoformat(),
            "activated_at": now.isoformat() if initial_state == "ACTIVE" else None,
            "trade_construction": trade,
            "confidence": signal_payload.get("confidence", {}),
            "reason_tags": signal_payload.get("reason_tags", []),
            "context_snapshot": context_snapshot,
            "feature_snapshot": signal_payload.get("feature_snapshot", {}),
            "validation": signal_payload.get("validation", {}),
            "market_snapshot_hash": signal_payload.get("market_snapshot_hash"),
            "expiry": signal_payload.get("expiry") or (trade.get("expiry") if isinstance(trade, dict) else None),
        }
        expiry_dt = _parse_expiry(signal.get("expiry"), datetime.fromisoformat(signal["timestamp"]))
        if expiry_dt is not None:
            signal["validity_seconds"] = int((expiry_dt - datetime.fromisoformat(signal["timestamp"])).total_seconds())
        if initial_state == "ACTIVE":
            signal["progress"] = self._signal_progress(signal, entry_price)
            self.stats["activated"] += 1
        
        self.active_signals[signal_id] = signal
        self._save_json(self.active_signals_file, self.active_signals)
        self.stats["generated"] += 1
        
        logger.info("signal_recorded", signal_id=signal_id, state=initial_state)
        
        # Publish base signal info
        base_signal = self._signal_base(signal)
        await self.bus.publish("signal.generated", base_signal)
        if initial_state == "ACTIVE":
            await self.bus.publish("signal.activated", base_signal)

    async def _publish_suppressed(self, signal: Dict[str, Any], reason: str) -> None:
        logger.info(
            "signal_suppressed",
            signal_id=signal.get("signal_id"),
            symbol=signal.get("symbol"),
            reason=reason,
        )
        if self.bus:
            await self.bus.publish("signal.suppressed", {
                "signal_id": signal.get("signal_id"),
                "symbol": signal.get("symbol"),
                "timeframe": signal.get("timeframe"),
                "direction": signal.get("direction"),
                "reason": reason,
            })

    def _cooldown_seconds(self, context: Dict[str, Any]) -> int:
        volatility = str(context.get("volatility_state", "Normal")).lower()
        bucket = "low" if volatility == "low" else "high" if volatility == "high" else "normal"
        defaults = {"low": 20, "normal": 30, "high": 45}
        try:
            minutes = self.config.get(f"validation.cooldown.{bucket}_volatility")
        except Exception:
            minutes = defaults[bucket]
        try:
            return max(0, int(minutes)) * 60
        except (TypeError, ValueError):
            return defaults[bucket] * 60

    def _is_recent_duplicate(
        self,
        symbol: str,
        direction: str,
        strategy_name: str,
        entry_price: Optional[float],
        now: datetime,
    ) -> bool:
        if entry_price is None:
            return False
        for existing in (*self.active_signals.values(), *self.history.values()):
            if (
                existing.get("symbol") != symbol
                or existing.get("direction") != direction
                or existing.get("strategy_name", "unknown") != strategy_name
            ):
                continue
            generated = _parse_iso_datetime(existing.get("generated_at") or existing.get("timestamp"))
            if generated is None or now - generated > timedelta(hours=2):
                continue
            previous_entry = existing.get("entry_price")
            if previous_entry is None:
                continue
            tolerance = max(abs(float(previous_entry)) * 0.0005, 1e-8)
            if abs(float(entry_price) - float(previous_entry)) <= tolerance:
                return True
        return False

    async def _on_candle(self, candle: Dict[str, Any]):
        try:
            now = _current_utc()
            # FIX: ensure self.active_signals is a dict and use keys safely
            if not isinstance(self.active_signals, dict):
                self.active_signals = {}

            for signal_id in list(self.active_signals.keys()):
                signal = self.active_signals.get(signal_id)
                if not signal:
                    continue
                
                if signal["symbol"] != candle["symbol"] or signal["timeframe"] != candle["timeframe"]:
                    continue
                source_timestamp = signal.get("source_candle_timestamp")
                if source_timestamp is not None and int(candle.get("timestamp", 0)) <= int(source_timestamp):
                    continue

                # 1. PENDING -> ACTIVE (Entry Hit)
                if signal["state"] == "PENDING":
                    if self._entry_hit(signal, candle["low"], candle["high"]):
                        self._activate_signal(signal_id, now)
                        # Publish activation
                        await self.bus.publish("signal.activated", self._signal_base(self.active_signals[signal_id]))
                        continue

                # 2. ACTIVE -> CLOSED (TP/SL Hit)
                if signal["state"] == "ACTIVE":
                    current_price = candle["close"]
                    if await self._close_on_tp_sl(signal_id, candle, current_price, now):
                        continue

                # 3. Check Expiry
                expiry_str = signal.get("expiry")
                expiry_dt = _parse_expiry(expiry_str, datetime.fromisoformat(signal["timestamp"]))
                if expiry_dt and now > expiry_dt:
                    closed = self._close_signal(signal_id, "EXPIRED", "expiry", candle["close"], now)
                    await self.bus.publish("signal.expired", closed)
                    continue

        except Exception as exc:
            logger.exception("[SignalStateManager] error processing candle: %s", exc)

    def _entry_hit(self, signal: Dict[str, Any], low: float, high: float) -> bool:
        entry = signal.get("entry_price")
        if entry is None:
            return False
        return low <= entry <= high

    def _entry_missed(self, signal: Dict[str, Any], high: float, low: float) -> bool:
        entry = signal.get("entry_price")
        if entry is None:
            return False
        threshold = abs(entry) * 0.01
        direction = signal.get("direction")
        if direction == "BUY":
            return high < entry and (entry - high) >= threshold
        if direction == "SELL":
            return low > entry and (low - entry) >= threshold
        return False

    def _activate_signal(self, signal_id: str, now: datetime) -> Dict[str, Any]:
        signal = self.active_signals[signal_id]
        if signal.get("state") != "ACTIVE":
            signal["state"] = "ACTIVE"
            signal["activated_at"] = now.isoformat()
            self.stats["activated"] += 1
        
        # Need current price to calc progress
        current_price = signal.get("entry_price") 
        signal["progress"] = self._signal_progress(signal, current_price)
        self.active_signals[signal_id] = signal
        self._save_json(self.active_signals_file, self.active_signals)
        logger.info("signal_activated", signal_id=signal_id)
        return self._signal_base(signal)

    async def _close_on_tp_sl(self, signal_id: str, candle: Dict[str, Any], current_price: Optional[float], now: datetime) -> bool:
        signal = self.active_signals.get(signal_id)
        if signal is None:
            return False
        tp = signal.get("take_profit_price")
        sl = signal.get("stop_loss_price")
        direction = signal.get("direction")
        if tp is None or sl is None or not isinstance(candle.get("high"), (int, float)) or not isinstance(candle.get("low"), (int, float)):
            return False
        high = candle["high"]
        low = candle["low"]
        hit_win = False
        hit_loss = False
        if direction == "BUY":
            hit_win = high >= tp
            hit_loss = low <= sl
        else:
            hit_win = low <= tp
            hit_loss = high >= sl
        if not hit_win and not hit_loss:
            return False
        if hit_win and hit_loss:
            result = "LOSS"
        elif hit_win:
            result = "WIN"
        else:
            result = "LOSS"
        reason = "tp" if result == "WIN" else "sl"
        closed = self._close_signal(signal_id, result, reason, current_price, now)
        await self.bus.publish("signal.closed", closed)
        return True

    def _close_signal(self, signal_id: str, state: str, reason: str,
                      close_price: Optional[float], now: datetime) -> Dict[str, Any]:
        signal = self.active_signals.pop(signal_id)
        signal["state"] = state
        signal["close_reason"] = reason
        signal["closed_at"] = now.isoformat()
        signal["close_price"] = close_price
        signal["progress"] = self._signal_progress(signal, close_price)
        self.history[signal_id] = signal
        self._save_json(self.active_signals_file, self.active_signals)
        self._save_json(self.history_file, self.history)
        if state == "WIN":
            self.stats["wins"] += 1
        elif state == "LOSS":
            self.stats["losses"] += 1
        elif state == "EXPIRED":
            self.stats["expired"] += 1
        elif state == "MISSED":
            self.stats["missed"] += 1
        logger.info("signal_closed", signal_id=signal_id, state=state, reason=reason)
        return self._signal_base(signal)

    def mark_expired(self, signal_id: str):
        if signal_id in self.active_signals:
            now = _current_utc()
            closed = self._close_signal(signal_id, "EXPIRED", "expiry", None, now)
            logger.info("signal_expired", signal_id=signal_id)
            return closed
        return None

    def check_cooldown(self, symbol: str, direction: str, strategy_name: str) -> bool:
        key = f"{symbol}:{direction}:{strategy_name}"
        expiry = self.cooldowns.get(key, 0)
        return time.time() < expiry

    def set_cooldown(self, symbol: str, direction: str, strategy_name: str, duration: int):
        key = f"{symbol}:{direction}:{strategy_name}"
        self.cooldowns[key] = time.time() + duration
        self._save_json(self.cooldown_file, self.cooldowns)
