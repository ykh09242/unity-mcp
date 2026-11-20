"""Unity Personal QA qualification uses the actual producing Editor and only discloses its edition."""

from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.check_unity_personal_evidence import classify_inventory, qualify_editor_log


def inventory(products):
    return {
        "success": True,
        "data": {
            "items": [{"product": p, "organization": "private"} for p in products],
            "total": len(products),
            "partial": False,
            "nextPage": None,
        },
    }


@pytest.mark.parametrize(
    ("products", "expected"),
    [
        (["Unity Personal", "Asset Store"], True),
        (["Unity Pro", "Asset Store"], False),
        (["Unity Personal", "Unity Pro"], False),
        (["Asset Store"], False),
        (["unknown"], False),
    ],
)
def test_complete_active_inventory(products, expected):
    observation = classify_inventory(inventory(products))
    assert observation["personal_only_editor"] is expected
    assert "private" not in str(observation)


def test_incomplete_inventory_is_unverified():
    response = inventory(["Unity Personal"])
    response["data"]["partial"] = True
    with pytest.raises(ValueError, match="incomplete"):
        classify_inventory(response)


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (b"  Product: Unity Personal\r\n", True),
        (b"  Product: Unity Pro\r\n", False),
        (b"Log mentions Product: Unity Personal\n", False),
        (b"Product: Unity Personal\nProduct: Unity Pro\n", False),
        (b"Licensing client handshake succeeded\n", False),
    ],
)
def test_tier_is_bound_to_producing_log(tmp_path, record, expected):
    data = b"organization: private\n" + record
    path = tmp_path / "editor.log"
    path.write_bytes(data)
    receipt = qualify_editor_log(path)
    assert receipt["personal"] is expected
    assert receipt["sha256"] == sha256(data).hexdigest()
    assert "private" not in str(receipt)
