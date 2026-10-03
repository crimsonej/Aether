"""Send a test signal event to verify Telegram delivery.
Requires the gateway to be running (ServiceManager will load config and adapters).
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
from aether.core.service_manager import ServiceManager

async def main():
    # Initialise ServiceManager (loads config, adapters, delivery, etc.)
    sm = ServiceManager()
    await sm.start()
    # Build a minimal signal payload – same shape the StrategyEngine would emit.
    payload = {
        "signal_id": "test-123",
        "symbol": "EURUSD",
        "direction": "LONG",
        "entry_price": 1.2345,
        "generated_at": "2026-06-03T00:00:00Z",
        "activated_at": "2026-06-03T00:00:10Z",
        "operator_local_timestamp": "2026-06-03 00:00:10",
        "operator_timezone": "UTC",
        "validity_seconds": 3600,
        "signal_age_seconds": 10,
        "state": "PENDING",
    }
    # Publish the active signal – TelegramMenuService listens to "signal.active"
    await sm.bus.publish("signal.active", payload)
    # Give it a moment to deliver
    await asyncio.sleep(5)
    await sm.stop()

if __name__ == "__main__":
    asyncio.run(main())
