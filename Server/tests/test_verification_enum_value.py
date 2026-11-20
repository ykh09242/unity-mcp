import json
from hashlib import sha256
from uuid import uuid4

import pytest
from cli.commands.verification_enum_value import compare_enum_values, enum_value_diff
from click.testing import CliRunner

PREFAB = "Assets/Encounter.prefab"
SCRIPT = "Assets/State.cs"


def capture(root, mapping):
    prefab_guid, script_guid = "1" * 32, "2" * 32
    raw = f"--- !u!114 &781\nMonoBehaviour:\n  m_Script: {{fileID: 11500000, guid: {script_guid}, type: 3}}\n  State: 1\n"
    inputs = {
        PREFAB: raw.encode(),
        PREFAB + ".meta": f"guid: {prefab_guid}\n".encode(),
        SCRIPT: b"class State {}\n",
        SCRIPT + ".meta": f"guid: {script_guid}\n".encode(),
    }
    for name, data in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    document = {
        "schema": "prefab_enum_read@1",
        "capture_id": uuid4().hex,
        "unity_version": "test",
        "prefab": PREFAB,
        "prefab_guid": prefab_guid,
        "file_id": 781,
        "script": SCRIPT,
        "script_guid": script_guid,
        "component": "State",
        "field": "State",
        "enum_type": "Mode",
        "assembly": "Assembly-CSharp",
        "value": 1,
        "mapping": mapping,
        "inputs": {name: sha256(data).hexdigest() for name, data in inputs.items()},
    }
    (root / "enum.json").write_text(json.dumps(document))


def test_original_prefab_preserved_across_reordered_and_pinned_members(tmp_path):
    old, new, fixed = [tmp_path / n for n in ("old", "new", "fixed")]
    for root, names in (
        (old, ["Idle", "Combat", "Reward"]),
        (new, ["Idle", "Reward", "Combat"]),
        (fixed, ["Idle", "Combat", "Reward"]),
    ):
        capture(root, {name: i for i, name in enumerate(names)})
    raw = CliRunner().invoke(enum_value_diff, [str(old), str(new), "--repaired", str(fixed)])
    assert raw.exit_code == 0, raw.output
    result = json.loads(raw.output)
    assert (
        result["value"] == 1 and result["meaning_changed"] and result["original_meaning_restored"]
    )
    assert result["reads"]["after"]["members_for_saved_integer"] == ["Reward"]
    with pytest.raises(ValueError, match="fresh native prefab read"):
        compare_enum_values([old, new, old])
    path = fixed / PREFAB
    path.write_bytes(path.read_bytes().replace(b"State: 1", b"State: 2"))
    with pytest.raises(ValueError, match="Archived bytes differ"):
        compare_enum_values([old, new, fixed])


def test_aliases_do_not_establish_unique_semantics(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    capture(old, {"Idle": 0, "Combat": 1})
    capture(new, {"Idle": 0, "Combat": 1, "Reward": 1})
    result = compare_enum_values([old, new])
    assert result["meaning_changed"] is None
    assert result["reads"]["after"]["members_for_saved_integer"] == ["Combat", "Reward"]
