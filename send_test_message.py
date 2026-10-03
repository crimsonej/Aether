"""Send a simple test message to the Telegram bot.
Uses the configured token and chat_id from .env.
"""
import os
import asyncio
from dotenv import load_dotenv
load_dotenv()

from aether.core.adapters.registry import AdapterRegistry

async def main():
    # Registry reads config and creates adapters
    from aether.core.config.store import ConfigStore
    from pathlib import Path
    config = ConfigStore(Path(__file__).resolve().parents[2])
    await config.load()
    adapters = AdapterRegistry(config, None)  # bus not needed for send_message
    # Get telegram adapter (configured already)
    tg = adapters.get('telegram')
    if not tg:
        print('Telegram adapter not found')
        return
    # Send a message
    ok = await tg.send_message('✅ Test message from Aether – data flow is OK!')
    print('Message sent?' , ok)

if __name__ == '__main__':
    asyncio.run(main())
