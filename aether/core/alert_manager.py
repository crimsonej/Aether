"""User-configured price and economic-calendar alert monitor."""

import asyncio
import csv
import hashlib
import io
import ipaddress
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import aiohttp

from aether.core.utils.logger import logger


class AlertManager:
    IMPACT_RANK = {"low": 1, "medium": 2, "high": 3}

    def __init__(self, config, bus, session_factory=None):
        self.config = config
        self.bus = bus
        self._session_factory = session_factory or aiohttp.ClientSession
        self._calendar_task: Optional[asyncio.Task] = None
        self._seen_news = self._load_seen_news()
        self._news_status = "not_configured"

    async def start(self) -> None:
        await self._ensure_calendar_task()

    async def stop(self) -> None:
        if self._calendar_task:
            self._calendar_task.cancel()
            try:
                await self._calendar_task
            except asyncio.CancelledError:
                pass
            self._calendar_task = None

    async def register_events(self, bus) -> None:
        self.bus = bus
        await bus.subscribe("data.quote", self._on_quote)
        await bus.subscribe("config_changed", self._on_config_changed)

    async def health(self) -> dict:
        alerts = self._alerts()
        news = alerts.get("news", {})
        configured = bool(news.get("enabled") and news.get("source_url"))
        return {
            "status": "OK" if not configured or self._news_status == "ok" else "DEGRADED",
            "component": "alert_manager",
            "details": {"news_calendar": self._news_status},
        }

    async def metrics(self) -> dict:
        return {"seen_news_events": len(self._seen_news)}

    def _alerts(self) -> Dict[str, Any]:
        try:
            alerts = self.config.get("alerts")
        except Exception:
            alerts = {}
        if hasattr(alerts, "model_dump"):
            alerts = alerts.model_dump(mode="json")
        alerts = dict(alerts or {})
        alerts.setdefault("price", [])
        alerts.setdefault("news", {})
        return alerts

    async def _on_quote(self, quote: Dict[str, Any]) -> None:
        symbol = str(quote.get("symbol", "")).upper().replace("/", "")
        price = self._number(quote.get("price"))
        if price is None:
            bid = self._number(quote.get("bid"))
            ask = self._number(quote.get("ask"))
            if bid is not None and ask is not None:
                price = (bid + ask) / 2
        timestamp = self._number(quote.get("timestamp"))
        if not symbol or price is None or timestamp is None:
            return
        max_age = self._config_value("validation.max_quote_age_seconds", 120)
        if timestamp > time.time() or time.time() - timestamp > max_age:
            return

        alerts = self._alerts()
        changed = False
        for alert in alerts.get("price", []):
            if not alert.get("enabled", True) or alert.get("symbol", "").upper() != symbol:
                continue
            target = self._number(alert.get("price"))
            condition = alert.get("condition")
            hit = target is not None and (
                (condition == "above" and price >= target)
                or (condition == "below" and price <= target)
            )
            if not hit:
                continue
            alert["enabled"] = False
            alert["triggered_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
            await self.bus.publish("alert.triggered", {
                "kind": "price",
                "alert_id": alert.get("id"),
                "symbol": symbol,
                "condition": condition,
                "target": target,
                "price": price,
                "timestamp": int(timestamp),
            })
        if changed:
            await self.config.set("alerts", alerts)

    async def _on_config_changed(self, event: Dict[str, Any]) -> None:
        if str(event.get("path", "")).startswith("alerts.news"):
            await self._ensure_calendar_task()

    async def _ensure_calendar_task(self) -> None:
        alerts = self._alerts().get("news", {})
        source_url = alerts.get("source_url")
        enabled = bool(alerts.get("enabled") and source_url)
        if enabled:
            parsed = urlparse(str(source_url))
            if parsed.scheme != "https" or not parsed.hostname or self._is_private_host(parsed.hostname):
                self._news_status = "invalid_source"
                return
            if self._calendar_task is None or self._calendar_task.done():
                self._calendar_task = asyncio.create_task(self._calendar_loop())
        elif self._calendar_task:
            self._calendar_task.cancel()
            try:
                await self._calendar_task
            except asyncio.CancelledError:
                pass
            self._calendar_task = None
            self._news_status = "not_configured" if not source_url else "disabled"

    async def _calendar_loop(self) -> None:
        while True:
            alerts = self._alerts().get("news", {})
            source_url = alerts.get("source_url")
            if not (alerts.get("enabled") and source_url):
                self._news_status = "disabled"
                return
            try:
                timeout = aiohttp.ClientTimeout(total=12, connect=4)
                async with self._session_factory(timeout=timeout) as session:
                    async with session.get(source_url, allow_redirects=False) as response:
                        response.raise_for_status()
                        body = await self._read_limited(response, 2_000_000)
                events = self.parse_calendar_csv(body, alerts.get("source_timezone", "America/New_York"))
                await self._notify_matching_events(events, alerts)
                self._news_status = "ok"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._news_status = "error"
                logger.warning("news_calendar_fetch_failed", error=str(exc))
                await self.bus.publish("alert.source_error", {"kind": "news", "error": str(exc)})
            interval = max(30, int(alerts.get("poll_interval_seconds", 300)))
            await asyncio.sleep(interval)

    @classmethod
    def parse_calendar_csv(cls, content: str, timezone_name: str) -> List[Dict[str, Any]]:
        text = (content or "").strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = []
            if isinstance(payload, list):
                events = []
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    row = {
                        "currency": str(item.get("country", item.get("currency", ""))).upper(),
                        "event": str(item.get("title", item.get("event", ""))).strip(),
                        "impact": str(item.get("impact", "")).lower(),
                        "date": str(item.get("date", "")).strip(),
                        "time": str(item.get("time", item.get("datetime", ""))).strip(),
                    }
                    events.extend(cls._parse_calendar_rows([row], timezone_name))
                return events
        zone = ZoneInfo(timezone_name)
        rows = csv.DictReader(io.StringIO(content))
        return cls._parse_calendar_rows(rows, timezone_name)

    @classmethod
    def _parse_calendar_rows(cls, rows: List[Dict[str, Any]] | csv.DictReader, timezone_name: str) -> List[Dict[str, Any]]:
        zone = ZoneInfo(timezone_name)
        events: List[Dict[str, Any]] = []
        for row in rows:
            normalized = {str(key).strip().lower(): (value or "").strip() for key, value in row.items() if key}
            currency = normalized.get("currency", normalized.get("country", "")).upper()
            title = normalized.get("event", normalized.get("title", "")).strip()
            impact = normalized.get("impact", "").lower()
            date_value = normalized.get("date", "")
            time_value = normalized.get("time", normalized.get("datetime", ""))
            if not currency or not title or impact not in cls.IMPACT_RANK:
                continue
            event_time = cls._parse_calendar_time(date_value, time_value, zone)
            if event_time is None:
                continue
            identity = f"{currency}|{title}|{int(event_time.timestamp())}"
            event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            events.append({
                "id": event_id,
                "currency": currency,
                "title": title,
                "impact": impact,
                "timestamp": int(event_time.timestamp()),
            })
        return events

    @staticmethod
    def _parse_calendar_time(date_value: str, time_value: str, zone: ZoneInfo):
        if not date_value or not time_value or time_value.lower() in {"all day", "tentative", ""}:
            return None
        parsed_date = None
        for date_format in ("%m-%d-%Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed_date = datetime.strptime(date_value, date_format).date()
                break
            except ValueError:
                continue
        if parsed_date is None:
            try:
                parsed_date = datetime.fromisoformat(date_value).date()
            except ValueError:
                return None
        parsed_time = None
        for time_format in ("%I:%M%p", "%I:%M %p", "%H:%M"):
            try:
                parsed_time = datetime.strptime(time_value.upper().replace(" ", ""), time_format).time()
                break
            except ValueError:
                continue
        if parsed_time is None:
            return None
        return datetime.combine(parsed_date, parsed_time, tzinfo=zone).astimezone(timezone.utc)

    async def _notify_matching_events(self, events: List[Dict[str, Any]], settings: Dict[str, Any]) -> None:
        pairs = {str(pair).upper().replace("/", "") for pair in settings.get("pairs", [])}
        currencies = {currency for pair in pairs for currency in self._pair_currencies(pair)}
        minimum = self.IMPACT_RANK.get(str(settings.get("minimum_impact", "high")).lower(), 3)
        now = int(time.time())
        lead = int(settings.get("lead_minutes", 60)) * 60
        dirty = False
        for event in events:
            event_id = event["id"]
            until_event = int(event["timestamp"]) - now
            if (
                event_id in self._seen_news
                or event["currency"] not in currencies
                or self.IMPACT_RANK.get(event["impact"], 0) < minimum
                or until_event < 0
                or until_event > lead
            ):
                continue
            self._seen_news.add(event_id)
            dirty = True
            await self.bus.publish("alert.triggered", {
                "kind": "news",
                **event,
                "pairs": sorted(pair for pair in pairs if event["currency"] in self._pair_currencies(pair)),
            })
        if dirty:
            self._save_seen_news()

    @staticmethod
    def _pair_currencies(pair: str) -> set[str]:
        if len(pair) == 6 and pair.isalpha():
            return {pair[:3], pair[3:]}
        if pair in {"XAUUSD", "XAGUSD"}:
            return {pair[:3], "USD"}
        return {pair}

    @staticmethod
    def _is_private_host(hostname: str) -> bool:
        normalized = hostname.strip("[]").lower()
        if normalized == "localhost" or normalized.endswith(".localhost") or normalized == "metadata.google.internal":
            return True
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            return False
        return not address.is_global

    @staticmethod
    async def _read_limited(response, maximum_bytes: int) -> str:
        if response.content_length is not None and response.content_length > maximum_bytes:
            raise ValueError("calendar response exceeds 2 MB limit")
        body = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            body.extend(chunk)
            if len(body) > maximum_bytes:
                raise ValueError("calendar response exceeds 2 MB limit")
        return body.decode(response.charset or "utf-8", errors="replace")

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            import math
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _config_value(self, path: str, default=None):
        try:
            value = self.config.get(path)
            return default if value is None else value
        except Exception:
            return default

    @staticmethod
    def _seen_path() -> Path:
        return Path(__file__).resolve().parents[2] / "data" / "seen_news_alerts.json"

    def _load_seen_news(self) -> set[str]:
        try:
            values = json.loads(self._seen_path().read_text(encoding="utf-8"))
            return set(values[-2000:]) if isinstance(values, list) else set()
        except Exception:
            return set()

    def _save_seen_news(self) -> None:
        path = self._seen_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            recent = list(self._seen_news)[-2000:]
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(recent), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            logger.warning("news_alert_state_save_failed", error=str(exc))
