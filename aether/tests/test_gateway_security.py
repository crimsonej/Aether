import pytest
from fastapi import HTTPException
from starlette.requests import Request

from aether.core.gateway.server import redact_config, require_admin, require_telegram_webhook_secret


def request_with_authorization(value=""):
    return request_with_header("authorization", value)


def request_with_header(name, value):
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/start",
        "headers": [(name.lower().encode("ascii"), value.encode("ascii"))],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
        "scheme": "http",
    })


def test_admin_routes_fail_closed_without_configured_token(monkeypatch):
    monkeypatch.delenv("AETHER_ADMIN_TOKEN", raising=False)

    with pytest.raises(HTTPException) as exc:
        require_admin(request_with_authorization("Bearer anything"))

    assert exc.value.status_code == 503


def test_admin_routes_require_correct_bearer_token(monkeypatch):
    monkeypatch.setenv("AETHER_ADMIN_TOKEN", "test-admin-token")

    with pytest.raises(HTTPException) as exc:
        require_admin(request_with_authorization("Bearer wrong"))
    assert exc.value.status_code == 401

    assert require_admin(request_with_authorization("Bearer test-admin-token")) is None


def test_telegram_webhook_requires_configured_secret():
    with pytest.raises(HTTPException) as exc:
        require_telegram_webhook_secret(request_with_authorization(), object())
    assert exc.value.status_code == 503


def test_telegram_webhook_compares_secret_header():
    class Adapter:
        webhook_secret = "configured-secret"

    request = request_with_header("x-telegram-bot-api-secret-token", "wrong")
    with pytest.raises(HTTPException) as exc:
        require_telegram_webhook_secret(request, Adapter())
    assert exc.value.status_code == 401

    valid_request = request_with_header("x-telegram-bot-api-secret-token", "configured-secret")
    assert require_telegram_webhook_secret(valid_request, Adapter()) is None


def test_public_config_redacts_sensitive_fields_recursively():
    redacted = redact_config({
        "data": {"api_key_env": "OANDA_API_KEY", "account_id": "private"},
        "delivery": [{"token": "secret", "enabled": True}],
    })

    assert redacted["data"]["api_key_env"] == "[REDACTED]"
    assert redacted["data"]["account_id"] == "[REDACTED]"
    assert redacted["delivery"][0]["token"] == "[REDACTED]"
    assert redacted["delivery"][0]["enabled"] is True
