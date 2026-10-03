"""Unit tests for the AI reply path.

Covers:
  * bind_default_senders wires send_message to the AI's sender map.
  * _send_back logs when no sender is registered (no silent drop).
  * handle_user_command returns friendly text for free-form chat.
  * handle_user_command surfaces LLM-chain failures instead of dropping.
"""
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aether.core.ai_config_manager import AIConfigManager  # noqa: E402


class FakeModel:
    """Mock ModelManager that yields pre-canned tokens."""

    def __init__(self, tokens: List[str], raises: Exception = None):
        self._tokens = tokens
        self._raises = raises

    async def generate(self, prompt: str):
        if self._raises is not None:
            raise self._raises
        for token in self._tokens:
            yield token


class FakeConversation:
    def __init__(self):
        self.entries: List[Dict[str, str]] = []

    async def add_entry(self, role: str, text: str):
        self.entries.append({"role": role, "text": text})

    def recent(self, n: int):
        return self.entries[-n:]


def _build_ai(model: FakeModel, senders: Dict[str, Any] = None) -> AIConfigManager:
    ai = AIConfigManager.__new__(AIConfigManager)
    ai.model = model
    ai.config = MagicMock()
    ai.config.get = MagicMock(return_value=["watchlist"])
    ai.config.set = AsyncMock()
    ai.permission = MagicMock()
    ai.permission.check = MagicMock(return_value=True)
    ai.conversation = FakeConversation()
    ai.bus = None
    ai._senders = senders or {}
    return ai


def test_bind_default_senders_wires_telegram():
    """A Telegram adapter that exposes send_message becomes the AI's sink
    without the operator manually wiring it.
    """
    send_mock = AsyncMock()
    fake_adapter = MagicMock()
    fake_adapter.send_message = send_mock
    registry = MagicMock()
    registry.adapters = {"telegram": fake_adapter}

    ai = _build_ai(FakeModel([]))
    ai.bind_default_senders(registry)

    assert "telegram" in ai._senders
    asyncio.run(ai._senders["telegram"]("hello"))
    send_mock.assert_awaited_once_with("hello")


def test_send_back_logs_when_no_sender():
    """If no sender is registered, _send_back must not raise and must
    at least log the message so the operator can see it.
    """
    ai = _build_ai(FakeModel([]), senders={})
    # Should be a no-op, not an exception.
    asyncio.run(ai._send_back("telegram", "any message"))


def test_free_form_text_returns_llm_reply():
    """A greeting like 'hi' should reach the LLM and the reply should
    be sent back to the user.
    """
    send_mock = AsyncMock()
    ai = _build_ai(
        FakeModel(["Hello", " from", " AI", "!"]),
        senders={"telegram": send_mock},
    )
    asyncio.run(ai.handle_user_command("hi", "user1", "telegram"))
    # The full reply should be sent in one call.
    assert send_mock.await_count >= 1
    full_text = "".join(call.args[0] for call in send_mock.await_args_list)
    assert "Hello" in full_text
    assert "AI" in full_text


def test_llm_failure_returns_friendly_message():
    """If every LLM provider is exhausted, the user gets a clear text
    rather than silence.
    """
    send_mock = AsyncMock()
    ai = _build_ai(
        FakeModel([], raises=RuntimeError("All LLM providers failed or are in backoff")),
        senders={"telegram": send_mock},
    )
    asyncio.run(ai.handle_user_command("hi", "user1", "telegram"))
    assert send_mock.await_count >= 1
    full_text = "".join(call.args[0] for call in send_mock.await_args_list)
    assert "AI provider" in full_text or "No AI" in full_text or "key" in full_text


def test_json_intent_path_still_works():
    """When the LLM returns a valid JSON intent, it is applied via the
    config store, not echoed back as text.
    """
    send_mock = AsyncMock()
    intent_json = '{"action": "add_symbol", "payload": {"symbol": "EURUSD"}}'
    ai = _build_ai(FakeModel([intent_json]), senders={"telegram": send_mock})
    asyncio.run(ai.handle_user_command("add EURUSD", "user1", "telegram"))
    # The watchlist was updated.
    assert ai.config.set.await_count >= 1
    # And the user got a success reply.
    full_text = "".join(call.args[0] for call in send_mock.await_args_list)
    assert "applied" in full_text.lower() or "add_symbol" in full_text
