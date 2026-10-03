"""Provider-independent, permission-checked tool registry for conversational control."""

import inspect
from typing import Any, Awaitable, Callable, Dict, List, Optional

from jsonschema import Draft7Validator


ToolHandler = Callable[[Dict[str, Any]], Any | Awaitable[Any]]


class ToolRegistry:
    def __init__(self, permission_engine):
        self.permission_engine = permission_engine
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        permission: str,
        handler: ToolHandler,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            **parameters,
        }
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": schema,
            "permission": permission,
            "handler": handler,
            "validator": Draft7Validator(schema),
        }

    def specifications(self) -> List[Dict[str, Any]]:
        return [
            {"name": tool["name"], "description": tool["description"], "parameters": tool["parameters"]}
            for tool in self._tools.values()
        ]

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    async def execute(self, name: str, arguments: Any, user: str) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": "unknown_tool"}
        if not isinstance(arguments, dict):
            return {"ok": False, "error": "arguments_must_be_an_object"}
        errors = sorted(tool["validator"].iter_errors(arguments), key=lambda error: list(error.path))
        if errors:
            return {"ok": False, "error": "invalid_arguments", "details": errors[0].message}
        if not self.permission_engine.check(user, tool["permission"]):
            return {"ok": False, "error": "permission_denied"}
        try:
            result = tool["handler"](arguments)
            if inspect.isawaitable(result):
                result = await result
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": "tool_execution_failed", "details": str(exc)}
