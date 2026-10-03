import json
import logging
from typing import Dict, Any, Optional
import aiohttp

from aether.core.adapters.registry import AdapterProtocol
from aether.core.utils.logger import logger

MANIFEST = {
    "name": "whatsapp",
    "type": "both",
    "config_schema": {
        "type": "object",
        "properties": {
            "auth_token_env": {"type": "string"},
            "phone_number_env": {"type": "string"},
            "api_base": {"type": "string", "format": "uri"}
        },
        "required": ["auth_token_env", "phone_number_env"]
    }
}


class WhatsAppAdapter(AdapterProtocol):
    """
    WhatsApp adapter using Twilio SMS/WhatsApp API.
    """

    MANIFEST = MANIFEST

    def __init__(self):
        self.token: Optional[str] = None
        self.phone_number: Optional[str] = None
        self.api_base = "https://api.twilio.com"
        self.session: Optional[aiohttp.ClientSession] = None

    def configure(self, token: str, phone_number: str, api_base: str = "https://api.twilio.com") -> None:
        self.token = token
        self.phone_number = phone_number
        self.api_base = api_base.rstrip('/')
        logger.info("[WhatsAppAdapter] configured")

    async def start(self) -> None:
        if self.session is None:
            self.session = aiohttp.ClientSession()
        logger.info("[WhatsAppAdapter] started")

    async def stop(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None
        logger.info("[WhatsAppAdapter] stopped")

    async def validate(self) -> bool:
        if not self.token:
            logger.warning("[WhatsAppAdapter] token not set")
            return False
        # Twilio API messages endpoint URL
        url = f"{self.api_base}/2010-04-01/Accounts/{self.token}/Messages.json"
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            auth = aiohttp.BasicAuth(self.token, "")
            async with self.session.request("HEAD", url, auth=auth, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            logger.exception("[WhatsAppAdapter] validation error: %s", e)
            return False

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        if not self.token or not self.phone_number:
            logger.warning("[WhatsAppAdapter] missing token or phone_number")
            return False

        message = (
            f"*Aether Signal*\n"
            f"Strategy: {signal.get('strategy', {}).get('name', 'N/A')}\n"
            f"Symbol: {signal.get('symbol', 'N/A')}\n"
            f"Direction: {signal.get('direction', 'N/A')}\n"
            f"Confidence: {signal.get('confidence', {}).get('adjusted', 'N/A')}%\n"
            f"Entry: {signal.get('trade_construction', {}).get('entry', {}).get('price', 'N/A')}\n"
            f"Take Profit: {signal.get('trade_construction', {}).get('take_profit', {}).get('price', 'N/A')}\n"
            f"Stop Loss: {signal.get('trade_construction', {}).get('stop_loss', {}).get('price', 'N/A')}"
        )
        payload = {
            "To": f"whatsapp:{self.phone_number}",
            "From": "whatsapp:+14155238886",  # Twilio sandbox sender
            "Body": message,
        }
        url = f"{self.api_base}/2010-04-01/Accounts/{self.token}/Messages.json"
        try:
            if self.session is None:
                self.session = aiohttp.ClientSession()
            auth = aiohttp.BasicAuth(self.token, "")
            async with self.session.post(url, data=payload, auth=auth, timeout=10) as resp:
                if resp.status == 201:
                    logger.info("[WhatsAppAdapter] signal sent")
                    return True
                else:
                    logger.warning("[WhatsAppAdapter] send failed: %s", await resp.text())
                    return False
        except Exception as e:
            logger.exception("[WhatsAppAdapter] exception: %s", e)
            return False

WhatsAppAdapter._event_bus = None
