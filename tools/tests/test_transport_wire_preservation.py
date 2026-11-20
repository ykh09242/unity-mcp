import json
import unicodedata

import pytest

from tools.tests.fixtures.transport_wire import wire_preservation
from tools.tests.fixtures.transport_wire.wire_preservation import WireMismatch, observe


@pytest.mark.parametrize(
    "expected,raw_reference,field,expected_json,observed_json",
    [
        (
            {"fileID": -9223372036854775808},
            '{"fileID": -9223372036854775807}',
            "fileID",
            "-9223372036854775808",
            "-9223372036854775807",
        ),
        (
            {"fileID": 9007199254740992},
            '{"fileID": 9007199254740992.0}',
            "fileID",
            "9007199254740992",
            "9007199254740992.0",
        ),
        ({"optional": None}, "{}", "optional", "null", "<missing>"),
        ({}, '{"optional": null}', "optional", "<missing>", "null"),
        (
            {"asset/path~": "Assets/\ud55c.prefab"},
            r'{"asset/path~": "Assets/\u1112\u1161\u11ab.prefab"}',
            "asset~1path~0",
            r'"Assets/\ud55c.prefab"',
            r'"Assets/\u1112\u1161\u11ab.prefab"',
        ),
    ],
)
def test_wire_mismatch_keeps_the_first_exact_nested_value(
    expected, raw_reference, field, expected_json, observed_json
):
    # Given one mismatching reference and a later change.
    wires = {}
    raw = '{"references": [' + raw_reference + '], "later": "changed"}'

    # When the observed wire is checked against its contract.
    with pytest.raises(WireMismatch) as caught:
        observe(
            wires, "python_read_websocket", raw, {"references": [expected], "later": "original"}
        )

    # Then the receipt points to the first changed value and retains the original wire.
    assert caught.value.detail == {
        "stage": "python_read_websocket",
        "path": f"/references/0/{field}",
        "expected_json": expected_json,
        "observed_json": observed_json,
    }
    assert wires["python_read_websocket"] == raw


def test_python_roundtrip_rejects_an_in_place_codec_change(monkeypatch):
    # Given a codec that normalizes the caller's nested path before building the wire envelope.
    envelope = wire_preservation.command_envelope
    original_path = "Assets/한글/Café.prefab"
    changed_path = unicodedata.normalize("NFD", original_path)

    def normalize_in_place(command_id, name, params, timeout):
        params["references"][0]["assetPath"] = changed_path
        return envelope(command_id, name, params, timeout)

    monkeypatch.setattr(wire_preservation, "command_envelope", normalize_in_place)

    # When the Python-origin case runs, the outgoing frame must be rejected before contacting C#.
    with pytest.raises(WireMismatch) as caught:
        wire_preservation.python_roundtrip(peer=None, wires={})

    # Then the mismatch reports the original fixture value and the altered codec value.
    assert caught.value.detail == {
        "stage": "python_write_websocket",
        "path": "/params/references/0/assetPath",
        "expected_json": json.dumps(original_path),
        "observed_json": json.dumps(changed_path),
    }
