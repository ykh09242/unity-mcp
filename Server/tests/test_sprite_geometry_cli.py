"""Real root CLI parsing for guarded Sprite geometry, with transport captured."""

import importlib
import json
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from cli.main import cli


PATH = "Assets/Sprites/영웅.png"
GUID = "aB" * 16
SPRITE_ID = "Cd" * 16
FILE_ID = "-9223372036854775808"
REVISION = "revision:原본"
GEOMETRY = {
    "rect": [0, 0, 16, 32],
    "pivot": [0, 0.75],
    "border": [0, 1, 2, 3],
    "outlines": [[[-8, -16], [8.5, -16], [0, 16]]],
}


@pytest.fixture
def dispatch(monkeypatch):
    command = Mock(return_value={"success": True, "file_id": FILE_ID, "revision": REVISION})
    monkeypatch.setattr(importlib.import_module("cli.commands.sprite"), "run_command", command)
    return command


def invoke(arguments, **kwargs):
    return CliRunner().invoke(cli, ["--format", "json", "sprite", *arguments], **kwargs)


def set_arguments(**changes):
    options = {
        "guid": GUID,
        "sprite-id": SPRITE_ID,
        "file-id": FILE_ID,
        "expected-revision": REVISION,
        "geometry": json.dumps(GEOMETRY),
        **changes,
    }
    return [
        "set-geometry",
        PATH,
        *(part for key, value in options.items() for part in ("--" + key, value)),
    ]


@pytest.mark.parametrize(
    "options, expected",
    [
        ([], {}),
        (["--page-size", "1", "--cursor", "0"], {"page_size": 1, "cursor": 0}),
        (
            ["--page-size", "100", "--cursor", "4096", "--expected-revision", REVISION],
            {"page_size": 100, "cursor": 4096, "expected_revision": REVISION},
        ),
    ],
)
def test_get_geometry_reaches_root_cli_and_preserves_paging(dispatch, options, expected):
    # Given exact paging options for an imported sprite sheet.
    # When the real root CLI parses the new read command.
    result = invoke(["get-geometry", PATH, *options])
    # Then only supplied values reach the existing command transport.
    assert result.exit_code == 0, result.output
    assert dispatch.call_args.args[:2] == (
        "manage_sprite",
        {"action": "get_geometry", "path": PATH, **expected},
    )
    assert json.loads(result.output)["file_id"] == FILE_ID


@pytest.mark.parametrize("source", ["inline", "file", "stdin"])
def test_set_geometry_preserves_exact_ids_and_json_values(dispatch, tmp_path, source):
    # Given guarded identifiers and a geometry object containing zero/int/float values.
    payload = json.dumps(GEOMETRY)
    kwargs = {}
    if source == "file":
        path = tmp_path / "geometry.json"
        path.write_text(payload, encoding="utf-8-sig")
        payload = "@" + str(path)
    elif source == "stdin":
        kwargs["input"] = payload
        payload = "-"
    # When the existing CLI JSON-source convention supplies the new mutation.
    result = invoke(set_arguments(geometry=payload), **kwargs)
    # Then no ID is rounded, normalized or parsed as a number; JSON remains structured.
    assert result.exit_code == 0, result.output
    assert dispatch.call_args.args[:2] == (
        "manage_sprite",
        {
            "action": "set_geometry",
            "path": PATH,
            "guid": GUID,
            "sprite_id": SPRITE_ID,
            "file_id": FILE_ID,
            "expected_revision": REVISION,
            "geometry": GEOMETRY,
        },
    )
    assert type(dispatch.call_args.args[1]["geometry"]["pivot"][0]) is int
    assert json.loads(result.output)["revision"] == REVISION


@pytest.mark.parametrize(
    "options",
    [
        ["--page-size", "0"],
        ["--page-size", "101"],
        ["--page-size", "1.5"],
        ["--page-size", "true"],
        ["--cursor", "-1"],
        ["--cursor", "4097"],
        ["--cursor", "1"],
        ["--cursor", "1", "--expected-revision", ""],
    ],
)
def test_get_geometry_invalid_paging_stops_before_dispatch(dispatch, options):
    # Given malformed paging or an unguarded continuation.
    # When the root CLI validates its options.
    result = invoke(["get-geometry", PATH, *options])
    # Then no command is sent.
    assert result.exit_code != 0
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"guid": " " + GUID},
        {"sprite-id": "0"},
        {"file-id": "0"},
        {"file-id": "01"},
        {"file-id": "21300000.0"},
        {"expected-revision": ""},
        {"expected-revision": "x" * 129},
        {"geometry": "{}"},
        {"geometry": "null"},
        {"geometry": "[]"},
        {"geometry": "{'pivot': [0, 1]}"},
        {"geometry": '{"pivot":[true,0]}'},
        {"geometry": '{"pivot":["0",1]}'},
        {"geometry": '{"pivot":[NaN,0]}'},
        {"geometry": '{"pivot":[1e400,0]}'},
        {"geometry": '{"pivot":[2,0]}'},
        {"geometry": '{"rect":[0,0,0,10]}'},
        {"geometry": '{"name":"replacement"}'},
        {"geometry": '{"outlines":[[[0,0],[1,1]]]}'},
        {"geometry": '{"rect":[0,0,' + "9" * 400 + ",10]}"},
    ],
)
def test_set_geometry_invalid_guard_or_shape_stops_before_dispatch(dispatch, changes):
    # Given invalid exact guards or malformed structured geometry.
    # When the real command parses and validates its boundary.
    result = invoke(set_arguments(**changes))
    # Then errors remain CLI usage failures, with no transport call.
    assert result.exit_code == 2, result.output
    dispatch.assert_not_called()
