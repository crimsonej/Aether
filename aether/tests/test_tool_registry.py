import asyncio

from aether.core.tools.registry import ToolRegistry
from aether.core.tools.web import ApprovedWebDataTool


class Permissions:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.checked = []

    def check(self, user, action):
        self.checked.append((user, action))
        return self.allowed


def make_registry(permission):
    registry = ToolRegistry(permission)
    registry.register(
        "add_alert",
        "Create a test alert",
        {
            "properties": {"symbol": {"type": "string"}, "price": {"type": "number", "exclusiveMinimum": 0}},
            "required": ["symbol", "price"],
        },
        "add_alert",
        lambda arguments: {"created": arguments["symbol"], "price": arguments["price"]},
    )
    return registry


def test_tool_registry_validates_dispatches_and_reports_specs():
    permissions = Permissions()
    registry = make_registry(permissions)
    result = asyncio.run(registry.execute("add_alert", {"symbol": "EURUSD", "price": 1.1}, "operator"))

    assert result == {"ok": True, "result": {"created": "EURUSD", "price": 1.1}}
    assert permissions.checked == [("operator", "add_alert")]
    assert registry.specifications()[0]["name"] == "add_alert"


def test_tool_registry_rejects_invalid_unknown_and_unauthorized_calls():
    permissions = Permissions()
    registry = make_registry(permissions)

    invalid = asyncio.run(registry.execute("add_alert", {"symbol": "EURUSD", "price": -1}, "operator"))
    unknown = asyncio.run(registry.execute("shell", {}, "operator"))
    assert invalid["error"] == "invalid_arguments"
    assert unknown["error"] == "unknown_tool"
    assert permissions.checked == []

    denied_registry = make_registry(Permissions(allowed=False))
    denied = asyncio.run(denied_registry.execute("add_alert", {"symbol": "EURUSD", "price": 1.1}, "viewer"))
    assert denied["error"] == "permission_denied"


def test_tool_registry_supports_async_handlers():
    registry = ToolRegistry(Permissions())

    async def handler(arguments):
        return {"echo": arguments["value"]}

    registry.register(
        "echo", "Echo a value", {"properties": {"value": {"type": "string"}}, "required": ["value"]},
        "echo", handler,
    )
    result = asyncio.run(registry.execute("echo", {"value": "ok"}, "operator"))
    assert result == {"ok": True, "result": {"echo": "ok"}}


def test_approved_web_tool_blocks_private_and_non_allowlisted_domains():
    tool = ApprovedWebDataTool()
    assert tool.is_allowed_url("https://www.yahoo.com") is True
    assert tool.is_allowed_url("http://localhost:8000") is False
    assert tool.is_allowed_url("https://example.com") is False


def test_approved_web_tool_extracts_search_results_from_public_html():
    html = '''
    <html><body>
      <a rel="nofollow" class="result-link" href="https://www.yahoo.com/finance">Yahoo Finance</a>
      <a rel="nofollow" class="result-link" href="https://www.reuters.com/world/">Reuters World</a>
    </body></html>
    '''
    results = ApprovedWebDataTool.extract_search_results(html)
    assert len(results) >= 2
    assert results[0]["title"] == "Yahoo Finance"
    assert results[1]["url"].startswith("https://www.reuters.com")
