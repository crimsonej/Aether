import asyncio

from aether.core.data.engine import DataEngine


class FakeProviderManager:
    def __init__(self, config, bus):
        self.started = 0
        self.stopped = 0
        self.fetches = 0

    async def start(self):
        self.started += 1

    async def stop(self):
        self.stopped += 1

    async def get_candles(self, *args, **kwargs):
        self.fetches += 1
        return []


def test_data_engine_delegates_polling_without_starting_a_second_fetcher(monkeypatch):
    monkeypatch.setattr("aether.core.data.engine.DataProviderManager", FakeProviderManager)

    async def run_test():
        engine = DataEngine(config=object(), bus=object())
        await engine.start()
        await asyncio.sleep(0)

        assert engine._provider_manager.started == 1
        assert engine._provider_manager.fetches == 0

        await engine.stop()
        assert engine._provider_manager is None

    asyncio.run(run_test())
