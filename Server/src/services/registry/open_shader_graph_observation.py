"""Evidence available from one existing Shader Graph window."""

from typing import Final

from services.registry.action_guidance_types import Observation

OPEN_GRAPH_OBSERVATION: Final[Observation] = {
    "source": "editor_live",
    "values": ["nodeCount", "edgeCount"],
    "unavailable": ["nodes", "edges", "properties", "keywords", "property_values", "shader_source"],
    "required_parameters": ["path"],
    "predicates": {
        "all": [{"op": "equals", "path": "status", "value": "open_model"}],
        "by_value": {
            "nodeCount": [{"op": "type", "path": "nodeCount", "type": "nonnegative_integer"}],
            "edgeCount": [{"op": "type", "path": "edgeCount", "type": "nonnegative_integer"}],
        },
    },
    "conditions": (
        "Counts require one open Shader Graph window with a readable model matching the asset GUID. "
        "Only status=open_model supplies counts, including edits awaiting a save."
    ),
}
