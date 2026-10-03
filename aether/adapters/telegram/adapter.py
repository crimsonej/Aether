"""Telegram adapter – both inbound (via webhook) and outbound (signal sending)."""

import asyncio
import json
import logging
from typing import Dict, Any, Optional, List
import aiohttp

from aether.core.adapters.registry import AdapterProtocol
from aether.core.utils.logger import logger


MANIFEST = {
    "name": "telegram",
    "type": "both",  # supports inbound and outbound
    "config_schema": {
        "type": "object",
        "properties": {
            "token_env": {"type": "string"},
            "chat_id_env": {"type": "string"},
            "webhook_url": {"type": "string", "format": "uri"},
            "auth_token_env": {"type": "string"}
        },
        "required": ["token_env", "chat_id_env"]
    }
}


class TelegramAdapter(AdapterProtocol):
    """
    Telegram adapter that can:
      * Validate the bot token via getMe.
      * Send formatted signals to a chat.
      * Receive incoming messages via a webhook (called by the gateway).
    """

    MANIFEST = MANIFEST

    def __init__(self):
        self.token: Optional[str] = None
        self.chat_id: Optional[str] = None
        self.webhook_secret: Optional[str] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._polling_offset: Optional[int] = None
        self.base_url = "https://api.telegram.org"

    # -----------------------------------------------------------------
    # AdapterProtocol interface
    # -----------------------------------------------------------------
    async def validate(self) -> bool:
        """Validate the token by calling getMe."""
        if not self.token:
            logger.warning("[TelegramAdapter] token not set")
            return False
        url = f"{self.base_url}/bot{self.token}/getMe"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            logger.info("[TelegramAdapter] validation successful")
                            return True
                    logger.warning("[TelegramAdapter] validation failed: %s", await resp.text())
        except Exception as e:
            logger.exception("[TelegramAdapter] validation error: %s", e)
        return False

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        """Send a formatted signal to the configured chat."""
        if not self.token or not self.chat_id:
            logger.warning("[TelegramAdapter] missing token or chat_id")
            return False

        text = self._format_signal(signal)
        url = f"{self.base_url}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        logger.info("[TelegramAdapter] signal sent")
                        return True
                    logger.warning("[TelegramAdapter] send failed: %s", data.get("description"))
                else:
                    logger.warning("[TelegramAdapter] send failed HTTP %s", resp.status)
        except Exception as e:
            logger.exception("[TelegramAdapter] exception while sending signal: %s", e)
        return False

    async def receive_message(self, update: Dict[str, Any]) -> None:
        """Called by the webhook handler in the gateway when an Update arrives.
        Extracts the message text and user ID, then forwards to the EventBus as
        a ``user_command`` event.
        """
        try:
            # Support both plain messages and callback_query (inline keyboard presses)
            if update.get("callback_query"):
                cb = update["callback_query"]
                data = cb.get("data")
                user_id = str(cb.get("from", {}).get("id"))
                if not self._is_authorized(user_id):
                    logger.warning("[TelegramAdapter] unauthorized callback from %s", user_id)
                    return
                logger.info("[TelegramAdapter] callback_query from %s: %s", user_id, data)
                bus = self.__class__._event_bus
                if bus:
                    await bus.publish("user_callback", {
                        "data": data,
                        "user": user_id,
                        "source": "telegram",
                    })
                return

            message = update.get("message") or update.get("edited_message")
            if not message:
                return
            user_id = str(message.get("from", {}).get("id"))
            text = message.get("text", "").strip()
            if not text:
                return
            if not self._is_authorized(user_id):
                logger.warning("[TelegramAdapter] unauthorized message from %s: %s", user_id, text)
                return
            logger.info("[TelegramAdapter] inbound message from %s: %s", user_id, text)
            bus = self.__class__._event_bus
            if bus:
                await bus.publish("user_command", {
                    "raw": text,
                    "user": user_id,
                    "source": "telegram"
                })
            else:
                logger.warning("[TelegramAdapter] event bus not set; cannot forward inbound message")
        except Exception as e:
            logger.exception("[TelegramAdapter] error processing inbound message: %s", e)

    def _is_authorized(self, user_id: str) -> bool:
        """Allow all users if allowlist is None/empty; otherwise enforce it."""
        if self.allowed_users is None or len(self.allowed_users) == 0:
            return True
        return str(user_id) in self.allowed_users

    # -----------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------
    def _format_signal(self, signal: Dict[str, Any]) -> str:
        """Create a professional Markdown signal."""
        generated_at = signal.get("generated_at") or signal.get("created_at")
        operator_ts = signal.get("operator_local_timestamp")
        operator_tz = signal.get("operator_timezone", "UTC")
        state = signal.get("state", "PENDING")
        time_lines = []
        if generated_at:
            time_lines.append(f"UTC: {generated_at}")
        if operator_ts:
            time_lines.append(f"Local ({operator_tz}): {operator_ts}")
        timestamps = " | ".join(time_lines) if time_lines else ""
        msg = (
            f"🚀 *{signal.get('symbol', 'UNKNOWN')}* | {signal.get('direction', 'N/A')} | {state}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Conf:* {signal.get('confidence', {}).get('adjusted', 0)}%\n"
            f"🕒 {timestamps}\n"
            f"🛑 *SL:* `{signal.get('trade_construction', {}).get('stop_loss', {}).get('price', 0)}`\n"
            f"✅ *TP:* `{signal.get('trade_construction', {}).get('take_profit', {}).get('price', 0)}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📍 *Entry:* `{signal.get('trade_construction', {}).get('entry', {}).get('price', 0)}`\n"
            f"📈 *Progress:* {signal.get('progress', {}).get('percent_progress_toward_tp', 0):.1f}%\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📝 *Context:* {' '.join([f'#{t}' for t in signal.get('reason_tags', [])])}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )
        return msg

    # -----------------------------------------------------------------
    # Lifecycle hooks called by AdapterRegistry
    # -----------------------------------------------------------------
    async def start(self) -> None:
        """Create the HTTP session, register bot commands, and
        register the inbound-update webhook with Telegram.
        """
        if self.session is None:
            self.session = aiohttp.ClientSession()
        logger.info("[TelegramAdapter] started")

        # Register the slash-command menu so users see suggestions in Telegram
        try:
            await self.set_commands([
                {"command": "start",    "description": "Open the Aether menu"},
                {"command": "menu",     "description": "Open the interactive menu"},
                {"command": "status",   "description": "Show platform health & status"},
                {"command": "signals",  "description": "List active signals"},
                {"command": "watchlist","description": "Show the symbol watchlist"},
                {"command": "models",   "description": "Show the LLM failover chain"},
                {"command": "help",     "description": "Show available commands"},
            ])
        except Exception:
            logger.exception("[TelegramAdapter] set_commands failed")

        # Use a webhook when configured; otherwise receive updates over polling.
        if self.webhook_url:
            try:
                await self.register_webhook()
            except Exception:
                logger.exception("[TelegramAdapter] register_webhook failed")
        else:
            if await self.delete_webhook():
                self._polling_task = asyncio.create_task(self._poll_updates())
                logger.info("[TelegramAdapter] Telegram long polling started")
            else:
                logger.error("[TelegramAdapter] could not clear webhook; polling was not started")

    async def send_startup_ping(self, data: Dict[str, Any] = None) -> bool:
        """Send a startup notification to the configured chat_id.

        Called by the gateway right after launch to confirm the bot is alive.
        Silently no-ops if chat_id is unset (e.g. token-only mode).
        """
        if not self.token:
            return False
        if not self.chat_id:
            logger.info("[TelegramAdapter] chat_id not set; skipping startup ping")
            return False
        import json as _json
        data = data or {}
        try:
            message = (
                f"🟢 *Aether Gateway Started*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕒 {data.get('started_at', '')}\n"
                f"🤖 LLM chain: {data.get('model_chain', 'n/a')}\n"
                f"📡 Data sources: {data.get('data_providers', 'n/a')}\n"
                f"📊 Subsystems: {data.get('subsystems', 'n/a')}\n"
                f"👤 Allowed users: {', '.join(self.allowed_users) if self.allowed_users else 'all'}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"Send /menu to interact."
            )
            return await self.send_message(message)
        except Exception:
            logger.exception("[TelegramAdapter] startup ping failed")
            return False

    async def send_message(self, text: str, reply_markup: dict | None = None) -> bool:
        """Send an arbitrary message with optional reply_markup (inline keyboard)."""
        if not self.token or not self.chat_id:
            logger.warning("[TelegramAdapter] missing token or chat_id")
            return False
        url = f"{self.base_url}/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        return True
        except Exception:
            logger.exception("[TelegramAdapter] send_message failed")
        return False

    async def stop(self) -> None:
        """Close the HTTP session."""
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("[TelegramAdapter] stopped")

    async def close(self) -> None:
        await self.stop()

    async def _poll_updates(self) -> None:
        """Receive Telegram updates for deployments without public webhook ingress."""
        url = f"{self.base_url}/bot{self.token}/getUpdates"
        while True:
            try:
                if self.session is None or self.session.closed:
                    self.session = aiohttp.ClientSession()
                params = {"timeout": 25, "allowed_updates": json.dumps(["message", "callback_query"])}
                if self._polling_offset is not None:
                    params["offset"] = self._polling_offset
                async with self.session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as response:
                    payload = await response.json()
                if not payload.get("ok"):
                    logger.error("[TelegramAdapter] getUpdates failed: %s", payload.get("description"))
                    await asyncio.sleep(3)
                    continue
                for update in payload.get("result", []):
                    await self.receive_message(update)
                    self._polling_offset = int(update["update_id"]) + 1
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError) as exc:
                logger.warning("[TelegramAdapter] polling request failed: %s", exc)
                await asyncio.sleep(3)

    async def set_commands(self, commands: List[Dict[str, str]]) -> bool:
        """
        Register a list of commands with the Telegram Bot API.
        Each command should be a dict: {"command": "start", "description": "Start the bot"}
        """
        if not self.token:
            logger.warning("[TelegramAdapter] token not set, cannot register commands")
            return False
        url = f"{self.base_url}/bot{self.token}/setMyCommands"
        payload = {"commands": commands}
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        logger.info("[TelegramAdapter] commands registered successfully")
                        return True
                    logger.warning("[TelegramAdapter] registration failed: %s", data.get("description"))
        except Exception as e:
            logger.exception("[TelegramAdapter] registration error: %s", e)
        return False

    async def register_webhook(self, webhook_url: Optional[str] = None) -> bool:
        """Register the inbound-update webhook URL with Telegram.

        Without this call, Telegram has nowhere to send user messages, so
        the bot is effectively deaf even though the token is valid.

        Args:
            webhook_url: full https URL Telegram should POST updates to.
                         If omitted, falls back to ``self.webhook_url`` set
                         during ``configure()``.

        Returns:
            True if Telegram acknowledged the registration.
        """
        url_to_register = webhook_url or self.webhook_url
        if not self.token:
            logger.warning("[TelegramAdapter] token not set, cannot register webhook")
            return False
        if not url_to_register:
            logger.warning(
                "[TelegramAdapter] no webhook_url configured — set "
                "delivery.telegram.webhook_url in aether.yaml. "
                "Inbound messages will not be delivered until then."
            )
            return False
        url = f"{self.base_url}/bot{self.token}/setWebhook"
        payload = {"url": url_to_register, "allowed_updates": ["message", "callback_query"]}
        if self.webhook_secret:
            payload["secret_token"] = self.webhook_secret
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        logger.info(
                            "[TelegramAdapter] webhook registered: %s", url_to_register
                        )
                        self.webhook_url = url_to_register
                        return True
                    logger.warning(
                        "[TelegramAdapter] webhook registration failed: %s",
                        data.get("description"),
                    )
                else:
                    logger.warning(
                        "[TelegramAdapter] webhook registration HTTP %s", resp.status
                    )
        except Exception as exc:
            logger.exception("[TelegramAdapter] webhook registration error: %s", exc)
        return False

    async def delete_webhook(self) -> bool:
        """Remove the webhook so the bot can be polled directly (for tests)."""
        if not self.token:
            return False
        url = f"{self.base_url}/bot{self.token}/deleteWebhook"
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            async with self.session.post(url, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False

    # -----------------------------------------------------------------
    # Configuration injection – called by AdapterRegistry after instantiation
    # -----------------------------------------------------------------
    def configure(self, token: str, chat_id: str, webhook_url: Optional[str] = None,
                  allowed_users: Optional[list] = None,
                  webhook_secret: Optional[str] = None) -> None:
        """Populate instance with configuration values from ConfigStore."""
        self.token = token
        self.chat_id = chat_id
        # webhook_url is used by the gateway to register the route; we store it
        # but do not act on it here.
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        # Users permitted to interact with the bot. Empty list = reject all.
        # If None (default), access is unrestricted.
        self.allowed_users = set(str(u) for u in (allowed_users or [])) if allowed_users is not None else None
        logger.info("[TelegramAdapter] configured")


# -----------------------------------------------------------------
# Module‑level variable that the AdapterRegistry will set after loading
# the adapter so that receive_message can publish to the EventBus.
# This avoids a circular import (adapter -> service_manager -> adapter).
# -----------------------------------------------------------------
TelegramAdapter._event_bus = None  # type: ignore