"""Compare native descriptor contracts independently of server routing metadata."""

from copy import deepcopy


def native_input_schema(schema):
    result = deepcopy(schema)
    result["properties"].pop("unity_instance")
    return result
