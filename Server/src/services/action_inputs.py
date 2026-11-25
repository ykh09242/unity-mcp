"""Unconditionally required action inputs shared by domain validators and discovery.

Tool-wide required parameters are declared on the tool signature.
"""

from typing import Final


_EXISTING_ASSET = ("path", "expected_revision")
INPUT_ACTION_REQUIRED: Final = {
    "capabilities": (),
    "read": ("path",),
    "create_asset": ("path",),
    "edit": ("path", "commands"),
    "add_map": (*_EXISTING_ASSET, "name"),
    "add_action": (*_EXISTING_ASSET, "map_id", "name", "action_type"),
    "add_binding": (*_EXISTING_ASSET, "map_id", "action_id", "binding_path"),
    "set_binding": (*_EXISTING_ASSET, "map_id", "binding_id", "binding_path"),
    "remove_binding": (*_EXISTING_ASSET, "map_id", "binding_id"),
}

_TERRAIN_WRITE = ("expected_revision", "expected_data_guid")
TERRAIN_REQUIRED: Final = {
    "get_info": (),
    "get_region": ("channel", "region"),
    "set_region": (*_TERRAIN_WRITE, "channel", "region", "values"),
    "get_trees": ("indices",),
    "set_trees": (*_TERRAIN_WRITE, "indices", "values"),
    "get_layer": ("index",),
    "set_layer": (*_TERRAIN_WRITE, "index", "layer_path", "layer_guid"),
}

REFLECTION_REQUIRED: Final = {
    "get_type": ("class_name",),
    "get_member": ("class_name", "member_name"),
    "search": ("query",),
}
