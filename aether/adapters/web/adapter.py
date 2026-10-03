from typing import Dict, Any
from aether.core.adapters.registry import AdapterProtocol

MANIFEST = {
    "name": "web",
    "type": "outbound",
    "config_schema": {
        "type": "object",
        "properties": {
            "endpoint_url": {"type": "string", "format": "uri"}
        },
        "required": ["endpoint_url"]
    }
}

class WebAdapter(AdapterProtocol):
    MANIFEST = MANIFEST

    async def validate(self) -> bool:
        return True

    async def send_signal(self, signal: Dict[str, Any]) -> bool:
        return True

    async def close(self) -> None:
        pass
