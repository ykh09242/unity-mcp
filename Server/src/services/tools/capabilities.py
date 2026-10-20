"""Register focused feature schemas without duplicating tool execution logic."""

from importlib import import_module

from services.registry.capability_adapter import register_capability
from services.registry.capability_catalog import CAPABILITIES

CAPABILITY_TOOLS = {}

for _capability in CAPABILITIES:
    _module = import_module(f"services.tools.{_capability.source}")
    CAPABILITY_TOOLS[_capability.name] = register_capability(
        _capability, getattr(_module, _capability.source)
    )
