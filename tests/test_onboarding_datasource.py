import sys
import types

import yaml

import aether.cli.commands.onboard as onboard


class DummyResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def isolate_onboarding_files(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(onboard, "DRAFT_PATH", tmp_path / "config" / "aether_onboarding_draft.json")
    monkeypatch.setattr(onboard, "CONFIG_PATH", tmp_path / "config" / "aether.yaml")
    monkeypatch.setattr(onboard, "ENV_PATH", tmp_path / ".env")


def test_llm_provider_verification_uses_model_endpoint(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "requests",
        types.SimpleNamespace(get=lambda *args, **kwargs: DummyResponse({"data": [{"id": "gpt-4o"}]})),
    )

    assert onboard.verify_provider_key("OpenAI", "test-api-key") is True


def test_llm_provider_verification_fails_without_models(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "requests",
        types.SimpleNamespace(get=lambda *args, **kwargs: DummyResponse({"data": []})),
    )

    assert onboard.verify_provider_key("OpenAI", "test-api-key") is False


def test_alpha_vantage_metadata_persists_provider_config(tmp_path, monkeypatch):
    isolate_onboarding_files(tmp_path, monkeypatch)
    draft = onboard._empty_draft()
    draft["data_sources"] = [{
        "name": "AlphaVantage",
        "api_key": "alpha-test-key",
        "env_var": "ALPHAVANTAGE_API_KEY",
        "status": "configured",
    }]

    onboard._commit(draft)

    config = yaml.safe_load((tmp_path / "config" / "aether.yaml").read_text())
    assert config["data"]["provider_chain"] == ["AlphaVantage"]
    assert config["data"]["providers"]["AlphaVantage"]["api_key_env"] == "ALPHAVANTAGE_API_KEY"
    assert "ALPHAVANTAGE_API_KEY=alpha-test-key" in (tmp_path / ".env").read_text()


def test_oanda_account_id_is_collected_and_persisted(tmp_path, monkeypatch):
    isolate_onboarding_files(tmp_path, monkeypatch)
    answers = iter(["oanda-api-key", "account-123"])
    monkeypatch.setattr(onboard, "choose_simple_option", lambda *_args: "Oanda")
    monkeypatch.setattr(onboard, "safe_prompt", lambda *_args, **_kwargs: next(answers))
    draft = onboard._empty_draft()

    assert onboard._add_one_data_source(draft) is True
    onboard._commit(draft)

    config = yaml.safe_load((tmp_path / "config" / "aether.yaml").read_text())
    provider = config["data"]["providers"]["Oanda"]
    assert provider["api_key_env"] == "OANDA_API_KEY"
    assert provider["account_id_env"] == "OANDA_ACCOUNT_ID"
    env = (tmp_path / ".env").read_text()
    assert "OANDA_API_KEY=oanda-api-key" in env
    assert "OANDA_ACCOUNT_ID=account-123" in env
