"""Trigger the configured startup ping via the Telegram adapter.
This mimics the behavior that occurs when the gateway starts.
"""
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from aether.core.service_manager import ServiceManager

async def main():
    sm = ServiceManager()
    await sm.start()
    adapters = sm._subsystems.get('adapters')
    tg = adapters.instance.get('telegram') if adapters else None
    if not tg:
        print('Telegram adapter not available')
        await sm.stop()
        return
    # Send the ping (same data the gateway would send)
    ok = await tg.send_startup_ping()
    print('Startup ping sent?', ok)
    await sm.stop()

if __name__ == '__main__':
    asyncio.run(main())
