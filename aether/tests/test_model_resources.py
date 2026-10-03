import asyncio
from pathlib import Path

import pytest
import yaml

from aether.core.model_manager import ModelManager, OllamaProvider
from aether.core.config.schema import AetherConfig


class FakeConfig:
    def __init__(self, ollama):
        self.values = {
            "model_manager.chain": ["Ollama", "Local"],
            "model_manager.ollama": ollama,
        }

    def get(self, path):
        if path not in self.values:
            raise KeyError(path)
        return self.values[path]


class FakeBus:
    async def publish(self, *_args, **_kwargs):
        return None


def test_ollama_is_available_only_when_enabled_and_configured():
    enabled = ModelManager(FakeConfig({
        "enabled": True,
        "base_url": "http://localhost:11434/",
        "model": "qwen2.5:3b",
    }), FakeBus())
    assert enabled.chain == ["Ollama", "Local"]
    assert isinstance(enabled.providers["Ollama"], OllamaProvider)
    assert enabled.providers["Ollama"].base_url == "http://localhost:11434"

    disabled = ModelManager(FakeConfig({"enabled": False}), FakeBus())
    assert disabled.chain == ["Local"]
    assert "Ollama" not in disabled.providers


def test_ollama_config_update_rebuilds_provider_chain():
    config = FakeConfig({"enabled": False})
    manager = ModelManager(config, FakeBus())
    config.values["model_manager.ollama"] = {
        "enabled": True,
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen2.5:3b",
    }

    asyncio.run(manager.on_config_update("model_manager.ollama.enabled", False, True))

    assert manager.chain == ["Ollama", "Local"]
    assert manager.providers["Ollama"].model == "qwen2.5:3b"


def test_ollama_stream_reads_native_ndjson(monkeypatch):
    class FakeContent:
        def __aiter__(self):
            async def lines():
                yield b'{"message":{"content":"Hello"},"done":false}\n'
                yield b'{"message":{"content":" world"},"done":true}\n'
            return lines()

    class FakeResponse:
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("aether.core.model_manager.aiohttp.ClientSession", lambda **_kwargs: FakeSession())
    provider = OllamaProvider({"enabled": True, "model": "qwen2.5:3b"})

    async def collect():
        return [chunk async for chunk in provider.stream("Explain this signal")]

    assert asyncio.run(collect()) == ["Hello", " world"]


def test_active_configuration_validates_and_enabled_ollama_requires_model():
    repo = Path(__file__).resolve().parents[2]
    raw_config = yaml.safe_load((repo / "config" / "aether.yaml").read_text(encoding="utf-8"))
    validated = AetherConfig(**raw_config)
    assert validated.model_manager.ollama.enabled is True
    assert validated.model_manager.ollama.model == "nemotron-3-super:cloud"

    raw_config["model_manager"]["ollama"] = {"enabled": True, "model": ""}
    with pytest.raises(ValueError, match="model.*required"):
        AetherConfig(**raw_config)


def test_ollama_health_and_model_discovery(monkeypatch):
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self):
            return {"models": [{"name": "qwen2.5:3b"}, {"name": "gemma3:4b"}]}

    class FakeSession:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr("aether.core.model_manager.aiohttp.ClientSession", FakeSession)
    provider = OllamaProvider({"model": "qwen2.5:3b"})

    async def check():
        return await provider.ping(), await provider.list_models()

    healthy, models = asyncio.run(check())
    assert healthy is True
    assert models == ["qwen2.5:3b", "gemma3:4b"]
