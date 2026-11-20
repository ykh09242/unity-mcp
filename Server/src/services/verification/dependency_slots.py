"""Identify explicit Unity object-reference fields in bounded committed text assets."""

import json
import re

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

_HEADER = re.compile(rb"(?m)^--- !u!(\d+) &(-?\d+)(?: stripped)?\r?$")
_GUID = re.compile(r"[0-9a-fA-F]{32}")


def serialized_slots(data):
    """Return field paths scoped to Unity class/local file ID. Ambiguity yields no claim."""
    if len(data) > 512 * 1024 or b"guid:" not in data or not data.startswith(b"%YAML "):
        return []
    headers = list(_HEADER.finditer(data))
    seen_objects = set()
    slots = []
    for index, header in enumerate(headers):
        obj = tuple(group.decode("ascii") for group in header.groups())
        if obj in seen_objects:
            return []
        seen_objects.add(obj)
        end = headers[index + 1].start() if index + 1 < len(headers) else len(data)
        # Strip only the Unity tag/header. Compose scalar data without constructing
        # tagged objects. Reject aliases and duplicate keys instead of expanding them.
        try:
            tree = yaml.compose(
                data[header.end() : end], Loader=getattr(yaml, "CBaseLoader", yaml.BaseLoader)
            )
        except (yaml.YAMLError, UnicodeError, RecursionError):
            return []
        pending = [(tree, obj)]
        seen = set()
        while pending:
            node, path = pending.pop()
            if id(node) in seen or len(path) > 64:
                return []
            seen.add(id(node))
            if isinstance(node, MappingNode):
                fields = {k.value: v for k, v in node.value if isinstance(k, ScalarNode)}
                if len(fields) != len(node.value) or any(len(k) > 256 for k in fields):
                    return []
                guid, file_id = fields.get("guid"), fields.get("fileID")
                if (
                    isinstance(guid, ScalarNode)
                    and _GUID.fullmatch(guid.value)
                    and isinstance(file_id, ScalarNode)
                    and re.fullmatch(r"-?[0-9]+", file_id.value)
                ):
                    slots.append(
                        (
                            json.dumps(path, separators=(",", ":")),
                            "asset:" + guid.value.lower(),
                            file_id.value,
                        )
                    )
                pending.extend((v, (*path, k)) for k, v in fields.items())
            elif isinstance(node, SequenceNode):
                pending.extend((v, (*path, i)) for i, v in enumerate(node.value))
    return slots
