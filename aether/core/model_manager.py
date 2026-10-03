"""ModelManager – production LLM provider chain with real HTTP streaming.

Each provider reads its API key from the environment at ping()/stream() time so
a key can be added after startup without restarting the process.

Failover order (default): Claude → OpenAI → Gemini → NVIDIA → OpenRouter → Local
"""

from __future__ import annotations

import asyncio
import os
import json
from typing import AsyncIterator, Dict, List, Optional, Any

import aiohttp

from aether.core.utils.logger import logger

 
# ---------------------------------------------------------------------------
# Provider base
# ---------------------------------------------------------------------------

class LLMProviderProtocol:
    name: str = "base"

    async def ping(self) -> bool:
        raise NotImplementedError

    async def list_models(self) -> List[str]:
        raise NotImplementedError

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Concrete providers
# ---------------------------------------------------------------------------

class ClaudeProvider(LLMProviderProtocol):
    name = "Claude"
    _API_URL = "https://api.anthropic.com/v1/messages"
    _MODELS_URL = "https://api.anthropic.com/v1/models"
    _DEFAULT_MODEL = "claude-3-haiku-20240307"

    def _key(self) -> Optional[str]:
        return os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

    async def ping(self) -> bool:
        key = self._key()
        if not key:
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    self._API_URL,
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": self._DEFAULT_MODEL, "max_tokens": 1,
                          "messages": [{"role": "user", "content": "ping"}]},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    return resp.status in (200, 529)
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        key = self._key()
        if not key:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    self._MODELS_URL,
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"]

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        key   = self._key()
        model = getattr(self, "model_id", None) or os.environ.get("CLAUDE_MODEL", self._DEFAULT_MODEL)
        if not key:
            raise RuntimeError("CLAUDE_API_KEY not set")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self._API_URL,
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 2048, "stream": True,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    text = line.decode().strip()
                    if text.startswith("data:"):
                        payload = text[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            obj = json.loads(payload)
                            chunk = obj.get("delta", {}).get("text", "")
                            if chunk:
                                yield chunk
                        except Exception:
                            pass


class OpenAIProvider(LLMProviderProtocol):
    name = "OpenAI"
    _API_URL = "https://api.openai.com/v1/chat/completions"
    _DEFAULT_MODEL = "gpt-3.5-turbo"

    def _key(self) -> Optional[str]:
        return os.environ.get("OPENAI_API_KEY")

    async def ping(self) -> bool:
        key = self._key()
        if not key:
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        key = self._key()
        if not key:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m["id"] for m in data.get("data", []) if "gpt" in m["id"]]
        except Exception:
            pass
        return ["gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "gpt-4o"]

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        key   = self._key()
        model = getattr(self, "model_id", None) or os.environ.get("OPENAI_MODEL", self._DEFAULT_MODEL)
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self._API_URL,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json={"model": model, "stream": True,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    text = line.decode().strip()
                    if text.startswith("data:"):
                        payload = text[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            obj   = json.loads(payload)
                            chunk = obj["choices"][0]["delta"].get("content", "")
                            if chunk:
                                yield chunk
                        except Exception:
                            pass


class GeminiProvider(LLMProviderProtocol):
    name = "Gemini"
    _DEFAULT_MODEL = "gemini-1.5-flash"

    def _key(self) -> Optional[str]:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def _model(self) -> str:
        return getattr(self, "model_id", None) or os.environ.get("GEMINI_MODEL", self._DEFAULT_MODEL)

    async def ping(self) -> bool:
        key = self._key()
        if not key:
            return False
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        key = self._key()
        if not key:
            return []
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m["name"].split("/")[-1] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        except Exception:
            pass
        return ["gemini-1.5-flash", "gemini-1.5-pro"]

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        key   = self._key()
        model = self._model()
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models"
               f"/{model}:streamGenerateContent?key={key}&alt=sse")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    text = line.decode().strip()
                    if text.startswith("data:"):
                        payload = text[5:].strip()
                        try:
                            obj = json.loads(payload)
                            for part in (obj.get("candidates", [{}])[0]
                                         .get("content", {}).get("parts", [])):
                                chunk = part.get("text", "")
                                if chunk:
                                    yield chunk
                        except Exception:
                            pass


class NVIDIAProvider(LLMProviderProtocol):
    name = "NVIDIA"
    _API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    _DEFAULT_MODEL = "meta/llama3-8b-instruct"

    def _key(self) -> Optional[str]:
        return os.environ.get("NVIDIA_API_KEY")

    async def ping(self) -> bool:
        key = self._key()
        if not key:
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        key = self._key()
        if not key:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return ["meta/llama3-8b-instruct", "meta/llama3-70b-instruct"]

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        key   = self._key()
        model = getattr(self, "model_id", None) or os.environ.get("NVIDIA_MODEL", self._DEFAULT_MODEL)
        if not key:
            raise RuntimeError("NVIDIA_API_KEY not set")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self._API_URL,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
                json={"model": model, "stream": True,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    text = line.decode().strip()
                    if text.startswith("data:"):
                        payload = text[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            obj   = json.loads(payload)
                            chunk = obj["choices"][0]["delta"].get("content", "")
                            if chunk:
                                yield chunk
                        except Exception:
                            pass


class OpenRouterProvider(LLMProviderProtocol):
    name = "OpenRouter"
    _API_URL = "https://openrouter.ai/api/v1/chat/completions"
    _DEFAULT_MODEL = "mistralai/mistral-7b-instruct"

    def _key(self) -> Optional[str]:
        return os.environ.get("OPENROUTER_API_KEY")

    async def ping(self) -> bool:
        key = self._key()
        if not key:
            return False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        key = self._key()
        if not key:
            return []
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return [m["id"] for m in data.get("data", [])]
        except Exception:
            pass
        return ["mistralai/mistral-7b-instruct", "openai/gpt-3.5-turbo"]

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        key   = self._key()
        model = getattr(self, "model_id", None) or os.environ.get("OPENROUTER_MODEL", self._DEFAULT_MODEL)
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self._API_URL,
                headers={"Authorization": f"Bearer {key}", "content-type": "application/json",
                         "HTTP-Referer": "https://aether.ai", "X-Title": "Aether"},
                json={"model": model, "stream": True,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.content:
                    text = line.decode().strip()
                    if text.startswith("data:"):
                        payload = text[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            obj   = json.loads(payload)
                            chunk = obj["choices"][0]["delta"].get("content", "")
                            if chunk:
                                yield chunk
                        except Exception:
                            pass


class OllamaProvider(LLMProviderProtocol):
    """Optional local model provider using Ollama's native HTTP API."""
    name = "Ollama"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.base_url = (
            os.environ.get("OLLAMA_BASE_URL")
            or str(config.get("base_url", "http://127.0.0.1:11434"))
        ).rstrip("/")
        self.model = str(config.get("model", "" )).strip()
        self.keep_alive = config.get("keep_alive", "5m")

    async def ping(self) -> bool:
        if not self.model:
            return False
        try:
            timeout = aiohttp.ClientTimeout(total=3, connect=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/api/tags") as response:
                    if response.status != 200:
                        return False
                    payload = await response.json()
                    available = {item.get("name") for item in payload.get("models", [])}
                    return self.model in available or f"{self.model}:latest" in available
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        try:
            timeout = aiohttp.ClientTimeout(total=5, connect=2)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/api/tags") as response:
                    if response.status != 200:
                        return []
                    payload = await response.json()
                    return [item["name"] for item in payload.get("models", []) if item.get("name")]
        except Exception:
            return []

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        if not self.model:
            raise RuntimeError("Ollama model is not configured")
        timeout = aiohttp.ClientTimeout(total=180, connect=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": True,
                    "keep_alive": self.keep_alive,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.content:
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    text = payload.get("message", {}).get("content", "")
                    if text:
                        yield text
                    if payload.get("done"):
                        break


class LocalProvider(LLMProviderProtocol):
    """Offline fallback – yields word tokens so the platform never hard-fails."""
    name = "Local"

    _OFFLINE_MSG = (
        "[Aether – offline mode] All cloud AI providers are currently unavailable. "
        "Your request has been logged. Please check your API keys or try again later."
    )

    async def ping(self) -> bool:
        return True

    async def list_models(self) -> List[str]:
        return ["local-v1"]

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        # Yield word by word so downstream sinks receive multiple tokens
        for word in self._OFFLINE_MSG.split():
            await asyncio.sleep(0)  # yield control to event loop
            yield word + " "


# ---------------------------------------------------------------------------
# ModelManager
# ---------------------------------------------------------------------------

class ModelManager:
    DEFAULT_CHAIN = ["Ollama", "Claude", "OpenAI", "Gemini", "NVIDIA", "OpenRouter", "Local"]
    BACKOFF_SECONDS = 60

    _PROVIDER_CLASSES: Dict[str, type] = {
        "Claude":     ClaudeProvider,
        "OpenAI":     OpenAIProvider,
        "Gemini":     GeminiProvider,
        "NVIDIA":     NVIDIAProvider,
        "OpenRouter": OpenRouterProvider,
        "Ollama":     OllamaProvider,
        "Local":      LocalProvider,
    }

    def __init__(self, config, bus):
        self.config    = config
        self.bus       = bus
        self.providers: Dict[str, LLMProviderProtocol] = {}
        self.chain:     List[str] = []
        self.unhealthy: Dict[str, float] = {}
        self._init_providers()

    def _init_providers(self) -> None:
        try:
            configured = self.config.get("model_manager.chain")
            if isinstance(configured, list) and all(isinstance(p, str) for p in configured):
                self.chain = configured
            else:
                self.chain = list(self.DEFAULT_CHAIN)
        except Exception:
            self.chain = list(self.DEFAULT_CHAIN)
        # Pre-flight: drop providers that have no API key in the environment so
        # the chain doesn't ping-fail-then-backoff on every request.
        # Each provider exposes the env var name(s) it consults; we read them
        # directly and filter. Local always passes.
        keyed_providers: Dict[str, type] = {}
        ollama_config = self._get_config("model_manager.ollama", {}) or {}
        for name, cls in self._PROVIDER_CLASSES.items():
            if name == "Local":
                keyed_providers[name] = cls
                continue
            if name == "Ollama":
                if isinstance(ollama_config, dict) and ollama_config.get("enabled", False):
                    keyed_providers[name] = cls
                else:
                    logger.info("[ModelManager] Ollama is disabled")
                continue
            # Instantiate transiently just to read the key, then drop it.
            tmp = cls()
            if hasattr(tmp, "_key") and tmp._key():
                keyed_providers[name] = cls
            else:
                logger.info("[ModelManager] %s has no API key – removed from chain", name)
        configured_models = self._get_config("model_manager.models", {}) or {}
        self.providers = {}
        for name, cls in keyed_providers.items():
            provider = cls(ollama_config) if name == "Ollama" else cls()
            if name != "Ollama" and isinstance(configured_models, dict):
                provider.model_id = configured_models.get(name)
            self.providers[name] = provider
        # Retain configured priority, then include any other credentialed providers
        # as failovers, with Local as the final offline fallback.
        configured_chain = [
            name for name in self.chain
            if name in self.providers and name != "Local"
        ]
        self.chain = configured_chain + [
            name for name in self.DEFAULT_CHAIN
            if name in self.providers and name not in configured_chain and name != "Local"
        ]
        if "Local" not in self.providers:
            self.providers["Local"] = LocalProvider()
        self.chain.append("Local")
        logger.info("[ModelManager] chain: %s", self.chain)

    async def health(self) -> Dict[str, bool]:
        results: Dict[str, bool] = {}
        for name, provider in self.providers.items():
            try:
                results[name] = await provider.ping()
            except Exception:
                results[name] = False
        return results

    async def available_models(self, provider_name: str) -> List[str]:
        provider = self.providers.get(provider_name)
        if provider is None and provider_name == "Ollama":
            config = self._get_config("model_manager.ollama", {}) or {}
            provider = OllamaProvider(config)
        if provider is None:
            return []
        try:
            return await provider.list_models()
        except Exception:
            return []

    def available_providers(self) -> List[str]:
        return [name for name in self.DEFAULT_CHAIN if name in self.providers]

    async def register_events(self, bus) -> None:
        await bus.subscribe("config_changed", self._on_config_changed)

    async def _on_config_changed(self, event: Dict[str, Any]) -> None:
        path = event.get("path", "")
        if (path == "model_manager.chain" or path.startswith("model_manager.ollama")
            or path.startswith("model_manager.models")):
            await self.reload_providers()

    async def reload_providers(self) -> None:
        self.unhealthy.clear()
        self._init_providers()
        logger.info("[ModelManager] providers reloaded")

    async def on_config_update(self, path: str, old: Any, new: Any) -> None:
        """React to runtime configuration changes.

        Implements the minimal hook so that updates to `model_manager.chain`
        applied via `ConfigStore.set()` (e.g., from the Telegram menu) will
        be applied immediately without restart.
        """
        try:
            if path == "model_manager.chain":
                await self.reload_providers()
            elif path.startswith("model_manager.ollama") or path.startswith("model_manager.models"):
                await self.reload_providers()
        except Exception as exc:
            logger.exception("[ModelManager] failed to apply config update: %s", exc)

    async def generate(self, prompt: str) -> AsyncIterator[str]:
        """Yield token strings from the first healthy provider in the chain."""
        loop_time = asyncio.get_event_loop().time
        for name in self.chain:
            if loop_time() < self.unhealthy.get(name, 0):
                logger.debug("[ModelManager] %s in backoff, skipping", name)
                continue
            provider = self.providers.get(name)
            if provider is None:
                continue
            try:
                healthy = await provider.ping()
            except Exception:
                healthy = False
            if not healthy:
                self.unhealthy[name] = loop_time() + self.BACKOFF_SECONDS
                await self.bus.publish("model.fallback", {"from": name, "reason": "ping_failed"})
                logger.warning("[ModelManager] %s unhealthy – falling back", name)
                continue
            try:
                async for token in provider.stream(prompt):
                    yield token
                return  # success – stop chain
            except Exception as exc:
                self.unhealthy[name] = loop_time() + self.BACKOFF_SECONDS
                await self.bus.publish("model.error",    {"provider": name, "error": str(exc)})
                await self.bus.publish("model.fallback", {"from": name, "reason": "stream_error"})
                logger.warning("[ModelManager] %s stream error: %s – falling back", name, exc)
        raise RuntimeError("All LLM providers failed or are in backoff")

    def _get_config(self, path: str, default=None):
        try:
            value = self.config.get(path)
            return default if value is None else value
        except Exception:
            return default

    async def start(self) -> None:
        logger.info("[ModelManager] started")

    async def stop(self) -> None:
        logger.info("[ModelManager] stopped")

    async def metrics(self) -> dict:
        return {"chain": self.chain, "unhealthy": list(self.unhealthy.keys())}


__all__ = ["ModelManager"]
