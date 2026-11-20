"""Require exact reference values at each managed wire boundary."""

import json
import unicodedata
from collections.abc import Callable
from copy import deepcopy
from itertools import zip_longest
from typing import TypedDict, TypeVar

from transport.legacy.unity_connection import _decode_unity_response
from transport.models import CommandResultMessage
from transport.payload_processing import command_envelope, encode_command
from transport.plugin_hub import PluginHub

MISSING = object()
Decoded = TypeVar("Decoded")


class FieldDifference(TypedDict):
    path: str
    expected_json: str
    observed_json: str


def first_difference(expected: object, observed: object, path: str = "") -> FieldDifference | None:
    """Return the first differing JSON Pointer, preserving scalar type and key presence."""
    if isinstance(expected, dict) and isinstance(observed, dict):
        for key in dict.fromkeys((*expected, *observed)):
            escaped = key.replace("~", "~0").replace("/", "~1")
            difference = first_difference(
                expected.get(key, MISSING), observed.get(key, MISSING), f"{path}/{escaped}"
            )
            if difference is not None:
                return difference
    elif isinstance(expected, list) and isinstance(observed, list):
        for index, (wanted, actual) in enumerate(
            zip_longest(expected, observed, fillvalue=MISSING)
        ):
            difference = first_difference(wanted, actual, f"{path}/{index}")
            if difference is not None:
                return difference
    elif type(expected) is not type(observed) or expected != observed:
        return {
            "path": path,
            "expected_json": "<missing>" if expected is MISSING else json.dumps(expected),
            "observed_json": "<missing>" if observed is MISSING else json.dumps(observed),
        }
    return None


class WireMismatch(ValueError):
    def __init__(self, stage: str, difference: FieldDifference):
        super().__init__(stage, difference)
        self.detail = {"stage": stage, **difference}


def observe(
    wires: dict[str, str],
    stage: str,
    wire: str,
    expected: object,
    *,
    decode: Callable[[str], Decoded] = json.loads,
    path: str = "",
) -> Decoded:
    wires[stage] = wire
    observed = decode(wire)
    difference = first_difference(expected, observed, path)
    if difference is not None:
        raise WireMismatch(stage, difference)
    return observed


def decode_websocket(wire: str) -> dict:
    data = PluginHub._decode_json_message(wire, PluginHub.MAX_RESULT_NODES)
    message = CommandResultMessage(**data)
    return {"type": message.type, "id": message.id, "result": message.result}


def decode_stdio(wire: str) -> object:
    return _decode_unity_response(wire.encode("utf-8"))


def receive_result(wires, response, command_id, data):
    stdio = {"status": "success", "result": data}
    websocket = {"type": "command_result", "id": command_id, "result": stdio}
    observe(wires, "csharp_write_stdio", response["stdio"], stdio)
    observe(wires, "python_read_stdio", response["stdio"], stdio, decode=decode_stdio)
    observe(wires, "csharp_write_websocket", response["websocket"], websocket)
    decoded = observe(
        wires, "python_read_websocket", response["websocket"], websocket, decode=decode_websocket
    )
    return decoded["result"]["result"]


def send_command(peer, wires, data, expected, command_id):
    # Evaluate the outgoing frame against the origin's contract before passing it to C#.
    payload = command_envelope(command_id, "manage_asset", data, 30.0)
    command = {
        "type": "execute",
        "id": command_id,
        "name": "manage_asset",
        "params": expected,
        "timeout": 30.0,
    }
    wire = encode_command(payload)
    observe(wires, "python_write_websocket", wire, command)
    result = peer.call("observe_command", wire=wire)
    observe(wires, "csharp_read_websocket", result["received"], expected, path="/params")
    return result["response"]


def python_roundtrip(peer, wires):
    path = "Assets/한글/Café.prefab"
    expected = {
        "references": [
            {"fileID": 9007199254740993, "assetPath": path, "optional": None},
            {"fileID": -9007199254740993, "assetPath": unicodedata.normalize("NFD", path)},
        ]
    }
    # The selected command codecs can mutate nested values they receive.
    response = send_command(peer, wires, deepcopy(expected), expected, "python-reference")
    receive_result(wires, response, "python-reference", expected)


def csharp_roundtrip(peer, wires):
    # Independent expectation for the C#-owned result. No Python command gives it these values.
    path = "Assets/장면/카페.unity"
    expected = {
        "references": [
            {"fileID": 9223372036854775807, "assetPath": path},
            {
                "fileID": -9223372036854775808,
                "assetPath": unicodedata.normalize("NFD", path),
                "optional": None,
            },
        ]
    }
    response = peer.call("native_result", id="native-reference")
    received = receive_result(wires, response, "native-reference", expected)
    send_command(peer, wires, received, expected, "native-reference")


def preservation(peer) -> dict:
    results = {}
    for direction, check in (
        ("python_csharp_python", python_roundtrip),
        ("csharp_python_csharp", csharp_roundtrip),
    ):
        wires = {}
        result = {"status": "passed", "wire_by_stage": wires}
        try:
            check(peer, wires)
        except WireMismatch as error:
            result.update(status="mismatch", failure=error.detail)
        except (KeyError, RuntimeError, TypeError, ValueError) as error:
            result.update(status="error", failure={"error": f"{type(error).__name__}: {error}"})
        results[direction] = result
    return results
