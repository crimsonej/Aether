from typing import Dict, Any
from aether.core.adapters.registry import AdapterProtocol

MANIFEST = {
    "name": "discord",
    "type": "outbound",
    "config_schema": {
        "type": "object",
        "properties": {
            "webhook_url": {"type": "string", "format": "uri"}
        },
        "required": ["webhook_url"]
    }
}

class DiscordAdapter(AdapterProtocol):
    MANIFEST = MANIFEST

    async def validate(self) -> bool:
        return True

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        return True

    async def close(self) -> None:
        pass
