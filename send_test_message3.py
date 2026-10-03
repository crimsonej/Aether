"""Send a simple test message to the Telegram bot via the full ServiceManager.
We spin up the ServiceManager (which creates the EventBus and adapters),
then directly call the telegram adapter's send_message.
"""
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from aether.core.service_manager import ServiceManager

async def main():
    sm = ServiceManager()
    await sm.start()
    # Get telegram adapter from the registry (subsystem "adapters")
    adapters = sm._subsystems.get('adapters')
    if not adapters:
        print('Adapters subsystem not found')
        await sm.stop()
        return
    tg = adapters.instance.get('telegram')
    if not tg:
        print('Telegram adapter not loaded')
        await sm.stop()
        return
    ok = await tg.send_message('✅ Test message from Aether – data flow is OK!')
    print('Message sent?', ok)
    await sm.stop()

if __name__ == '__main__':
    asyncio.run(main())
