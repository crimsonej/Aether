"""Unit tests for the multi-entry onboarding draft.

Covers:
  * Default draft shape: providers is a list, data_sources is a list.
  * Saving then loading round-trips.
  * `_append_no_key_fallbacks` always puts Frankfurter + CoinGecko at the end
    and never duplicates them.
  * `_commit` writes the new `data.providers` map + `data.provider_chain` list
    to aether.yaml and the corresponding keys to .env.
"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from aether.cli.commands import onboard  # noqa: E402


@pytest.fixture
def tmp_project(tmp_path, monkeypatch):
    """Pretend `tmp_path` is the project root for the onboard module."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(onboard, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(onboard, "DRAFT_PATH", tmp_path / "config" / "aether_onboarding_draft.json")
    monkeypatch.setattr(onboard, "CONFIG_PATH", tmp_path / "config" / "aether.yaml")
    monkeypatch.setattr(onboard, "ENV_PATH", tmp_path / ".env")
    return tmp_path


def test_empty_draft_shape():
    draft = onboard._empty_draft()
    assert isinstance(draft["providers"], list)
    assert isinstance(draft["data_sources"], list)
    assert draft["providers"] == []
    assert draft["data_sources"] == []


def test_save_load_roundtrip(tmp_project):
    draft = onboard._empty_draft()
    draft["providers"] = [
        {"name": "NVIDIA", "api_key": "nv-abc", "env_var": "NVIDIA_API_KEY", "status": "verified"},
    ]
    draft["data_sources"] = [
        {"name": "TwelveData", "api_key": "td-1", "env_var": "TWELVEDATA_API_KEY",
         "requires_key": True, "status": "configured"},
    ]
    onboard.save_draft(draft)

    loaded = onboard.load_draft()
    assert len(loaded["providers"]) == 1
    assert loaded["providers"][0]["name"] == "NVIDIA"
    assert len(loaded["data_sources"]) == 1


def test_append_no_key_fallbacks_dedupes(tmp_project):
    draft = onboard._empty_draft()
    draft["data_sources"] = [
        {"name": "TwelveData", "api_key": "k", "env_var": "TWELVEDATA_API_KEY",
         "requires_key": True, "status": "configured"},
    ]
    onboard._append_no_key_fallbacks(draft)
    names = [d["name"] for d in draft["data_sources"]]
    assert names[-2:] == ["Frankfurter", "CoinGecko"]
    # Calling again must not duplicate.
    onboard._append_no_key_fallbacks(draft)
    names2 = [d["name"] for d in draft["data_sources"]]
    assert names2.count("Frankfurter") == 1
    assert names2.count("CoinGecko") == 1


def test_commit_writes_yaml_and_env(tmp_project):
    draft = onboard._empty_draft()
    draft["providers"] = [
        {"name": "NVIDIA", "api_key": "nv-abc", "env_var": "NVIDIA_API_KEY", "status": "verified"},
    ]
    draft["data_sources"] = [
        {"name": "TwelveData", "api_key": "td-1", "env_var": "TWELVEDATA_API_KEY",
         "requires_key": True, "status": "configured"},
        {"name": "AlphaVantage", "api_key": "av-1", "env_var": "ALPHAVANTAGE_API_KEY",
         "requires_key": True, "status": "configured"},
    ]
    # The wizard always appends the no-key fallbacks at the end; commit
    # assumes the caller did the same.
    onboard._append_no_key_fallbacks(draft)
    draft["messaging"] = {
        "platform": "telegram",
        "token": "123:abc-secrethere",
        "allowed_users": ["6938668462"],
        "extras": {"chat_id": "6938668462"},
        "status": "configured",
    }
    onboard._commit(draft)

    # .env must contain the LLM key + data keys + telegram creds.
    env_text = (tmp_project / ".env").read_text()
    assert "NVIDIA_API_KEY=nv-abc" in env_text
    assert "TWELVEDATA_API_KEY=td-1" in env_text
    assert "ALPHAVANTAGE_API_KEY=av-1" in env_text
    assert "TELEGRAM_BOT_TOKEN=123:abc-secrethere" in env_text
    assert "TELEGRAM_CHAT_ID=6938668462" in env_text

    # aether.yaml must contain the new chain and the providers map.
    cfg = yaml.safe_load((tmp_project / "config" / "aether.yaml").read_text())
    chain = cfg["data"]["provider_chain"]
    assert chain[0] == "TwelveData"
    assert chain[1] == "AlphaVantage"
    assert chain[-2:] == ["Frankfurter", "CoinGecko"]
    assert "sources" not in cfg["data"]  # legacy field dropped
    assert "TwelveData" in cfg["data"]["providers"]
    assert cfg["data"]["providers"]["TwelveData"]["api_key_env"] == "TWELVEDATA_API_KEY"
    assert cfg["delivery"]["telegram"]["allowed_users"] == ["6938668462"]


def test_validation_rejects_short_token():
    assert onboard.validate_telegram_token("") is not None
    assert onboard.validate_telegram_token("abc") is not None
    assert onboard.validate_telegram_token("123:short") is not None
    assert onboard.validate_telegram_token("123:abcdefghijklmnopqrstuvwxyz") is None


def test_validation_rejects_short_api_key():
    err = onboard.validate_api_key("TwelveData", "abc")
    assert err is not None
    assert "TwelveData" in err
    err2 = onboard.validate_api_key("TwelveData", "a-very-long-key")
    assert err2 is None
