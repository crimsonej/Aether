import asyncio
import os
import yaml
from pathlib import Path
from aether.core.service_manager import ServiceManager
from aether.core.utils.logger import logger

async def _bridge_flow():
    print("\n[System Check] Initializing ServiceManager...")
    # Ensure we use the project root for config loading
    root = Path(__file__).parents[1]
    sm = ServiceManager(project_root=root)
    
    try:
        await sm.start()
        print("[System Check] Platform started successfully.")
    except Exception as e:
        print(f"[System Check] FAILED to start platform: {e}")
        return

    # 1. Check Model Health
    print("\n[System Check] Verifying AI Model Chain...")
    model_mgr = sm._subsystems["model"].instance
    health = await model_mgr.health()
    print(f"Model Health Status: {health}")
    
    # 2. Check Data Provider Health
    print("\n[System Check] Verifying Data Providers...")
    data_mgr = sm._subsystems["data"].instance
    d_health = await data_mgr.health()
    print(f"Data Health Status: {d_health}")

    # 3. Test Event Flow (Bridge)
    print("\n[System Check] Testing Event Bridge (Simulated Candle)...")
    test_payload = {
        "symbol": "EURUSD",
        "timeframe": "1h",
        "open": 1.0850,
        "high": 1.0860,
        "low": 1.0840,
        "close": 1.0855,
        "volume": 1000.0,
        "timestamp": 1625000000,
        "close_time": 1625000000,
        "source": "test",
        "is_closed": True
    }
    await sm.bus.publish("data.candle", test_payload)
    print("[System Check] Published test candle event.")
    
    # Wait a moment for subsystems to process
    await asyncio.sleep(1)
    
    print("\n[System Check] Diagnostics Complete.")
    await sm.stop()


def test_bridge_flow():
    asyncio.run(_bridge_flow())

if __name__ == "__main__":
    test_bridge_flow()
