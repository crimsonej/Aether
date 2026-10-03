import asyncio
from unittest.mock import AsyncMock, MagicMock

from aether.core.ai_config_manager import AIConfigManager
from aether.core.config.schema import AetherConfig
import yaml
from pathlib import Path


class FakeModel:
    def __init__(self, response):
        self.response = response

    async def generate(self, _prompt):
        yield self.response


class FakeConversation:
    async def add_entry(self, *_args):
        return None

    def recent(self, _count):
        return []


class FakeConfig:
    def __init__(self):
        self.values = {"alerts": {"price": [], "news": {}}}
        self.set_calls = []

    def get(self, path):
        return self.values[path]

    async def set(self, path, value):
        self.set_calls.append((path, value))
        self.values[path] = value


def build_ai(intent):
    ai = AIConfigManager.__new__(AIConfigManager)
    ai.model = FakeModel(intent)
    ai.config = FakeConfig()
    ai.permission = MagicMock()
    ai.permission.check.return_value = True
    ai.conversation = FakeConversation()
    ai.bus = None
    ai._senders = {"telegram": AsyncMock()}
    return ai


def test_conversation_adds_valid_pair_price_alert():
    ai = build_ai('{"action":"add_price_alert","payload":{"symbol":"eur/usd","condition":"above","price":1.1}}')

    asyncio.run(ai.handle_user_command("alert me above 1.10 on EURUSD", "operator", "telegram"))

    path, alerts = ai.config.set_calls[0]
    assert path == "alerts"
    assert alerts["price"][0]["symbol"] == "EURUSD"
    assert alerts["price"][0]["condition"] == "above"
    assert alerts["price"][0]["price"] == 1.1
    ai.permission.check.assert_called_once_with("operator", "add_price_alert")
    assert "EURUSD above 1.1" in ai._senders["telegram"].call_args.args[0]


def test_conversation_news_preference_does_not_claim_source_exists():
    ai = build_ai('{"action":"set_news_alerts","payload":{"enabled":true,"pairs":["EURUSD"],"minimum_impact":"high"}}')

    asyncio.run(ai.handle_user_command("alert me for high impact news on EURUSD", "operator", "telegram"))

    path, alerts = ai.config.set_calls[0]
    assert path == "alerts"
    assert alerts["news"]["enabled"] is True
    assert alerts["news"]["pairs"] == ["EURUSD"]
    assert "calendar source URL is configured" in ai._senders["telegram"].call_args.args[0]


def test_conversation_rejects_invalid_price_without_config_mutation():
    ai = build_ai('{"action":"add_price_alert","payload":{"symbol":"EURUSD","condition":"up","price":-1}}')

    asyncio.run(ai.handle_user_command("set an alert", "operator", "telegram"))

    assert ai.config.set_calls == []
    assert "was not run" in ai._senders["telegram"].call_args.args[0]


def test_provider_independent_tool_call_envelope_executes_registered_tool():
    ai = build_ai('{"tool_call":{"name":"add_price_alert","arguments":{"symbol":"GBPUSD","condition":"below","price":1.25}}}')

    asyncio.run(ai.handle_user_command("alert me if GBPUSD falls below 1.25", "operator", "telegram"))

    path, alerts = ai.config.set_calls[0]
    assert path == "alerts"
    assert alerts["price"][0]["symbol"] == "GBPUSD"
    assert alerts["price"][0]["condition"] == "below"
    assert "GBPUSD below 1.25" in ai._senders["telegram"].call_args.args[0]


def test_prompt_lists_capabilities_and_dispatches_read_tool():
    class CapturingModel(FakeModel):
        prompt = ""

        async def generate(self, prompt):
            self.prompt = prompt
            yield self.response

    response = '{"tool_call":{"name":"get_watchlist","arguments":{}}}'
    ai = build_ai(response)
    ai.model = CapturingModel(response)

    asyncio.run(ai.handle_user_command("what pairs are monitored?", "operator", "telegram"))

    assert '"name": "get_system_overview"' in ai.model.prompt
    assert '"name": "get_watchlist"' in ai.model.prompt
    assert '"tool_call"' in ai.model.prompt
    assert '"watchlist": []' in ai._senders["telegram"].call_args.args[0]


def test_price_and_news_alerts_validate_against_platform_schema():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config" / "aether.yaml").read_text(encoding="utf-8"))
    config["alerts"] = {
        "price": [{
            "id": "alert-1", "symbol": "EURUSD", "condition": "above",
            "price": 1.095, "enabled": True,
        }],
        "news": {
            "enabled": True, "pairs": ["EURUSD"], "minimum_impact": "high",
            "source_url": "https://calendar.example/events.csv",
            "source_timezone": "America/New_York", "poll_interval_seconds": 300,
            "lead_minutes": 60,
        },
    }

    validated = AetherConfig(**config)
    assert validated.alerts.price[0].price == 1.095
    assert validated.alerts.news.enabled is True


def test_unsafe_calendar_source_is_rejected():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "config" / "aether.yaml").read_text(encoding="utf-8"))
    config["alerts"] = {"news": {"source_url": "http://localhost/private.csv"}}

    try:
        AetherConfig(**config)
    except ValueError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("non-HTTPS calendar source was accepted")


def test_unauthorized_conversational_alert_does_not_mutate_config():
    ai = build_ai('{"action":"add_price_alert","payload":{"symbol":"EURUSD","condition":"above","price":1.1}}')
    ai.permission.check.return_value = False

    asyncio.run(ai.handle_user_command("alert EURUSD above 1.1", "viewer", "telegram"))

    assert ai.config.set_calls == []
    assert "not authorized" in ai._senders["telegram"].call_args.args[0]
