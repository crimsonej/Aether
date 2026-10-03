"""Backward-compatible imports for the renamed AetherAgent runtime component."""

from .agent import AetherAgent, INTENT_SCHEMA

AIConfigManager = AetherAgent

__all__ = ["AetherAgent", "AIConfigManager", "INTENT_SCHEMA"]
