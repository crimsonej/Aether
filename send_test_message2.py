"""Send a simple test message to the Telegram bot.
Uses the token and chat_id from .env.
"""
import os
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from aether.core.adapters.registry import AdapterRegistry
from aether.core.config.store import ConfigStore

async def main():
    # Project root is the directory containing config/aether.yaml
    project_root = Path(__file__).resolve().parent
    config = ConfigStore(project_root)
    await config.load()
    adapters = AdapterRegistry(config, None)  # No EventBus needed for sending
    tg = adapters.get('telegram')
    if not tg:
        print('Telegram adapter not found')
        return
    ok = await tg.send_message('✅ Test message from Aether – data flow verified!')
    print('Message sent?', ok)

if __name__ == '__main__':
    asyncio.run(main())
