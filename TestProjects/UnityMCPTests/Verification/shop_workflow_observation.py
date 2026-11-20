"""Independent observations from the maintained Resources prefab move fixture."""

import json
from pathlib import Path
import re


def read_player_observation(path: Path) -> dict:
    marker = "SHOP_WORKFLOW_OBSERVATION "
    with path.open("rb") as stream:
        body = stream.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise ValueError("Player observation log exceeds 1 MiB")
    rows = [
        json.loads(line.removeprefix(marker))
        for line in body.decode("utf-8", errors="replace").splitlines()
        if line.startswith(marker)
    ]
    if (
        not rows
        or not isinstance(rows[0], dict)
        or any(
            json.dumps(row, sort_keys=True) != json.dumps(rows[0], sort_keys=True)
            for row in rows[1:]
        )
    ):
        raise ValueError("shop_player_observation_missing_or_ambiguous")
    return rows[0]


def check_observation(phase, source_revision, guid, editor, player):
    if phase not in {"original", "moved", "repaired"}:
        raise ValueError("unknown_shop_phase")
    loaded = phase != "moved"
    key = "VerifiedShop/Store/ShopBanner" if phase == "repaired" else "VerifiedShop/UI/ShopBanner"
    if (
        not isinstance(guid, str)
        or not re.fullmatch(r"[0-9a-f]{32}", guid)
        or not source_revision
        or editor.get("phase") != phase
        or editor.get("source_revision") != source_revision
        or editor.get("asset_guid") != guid
        or editor.get("serialized_guid") != guid
        or editor.get("dependency_retained") is not True
        or editor.get("key") != key
        or editor.get("loader_guid") != (guid if loaded else "")
    ):
        raise ValueError("shop_editor_observation_mismatch")
    if (
        player.get("key") != key
        or player.get("expected") != "ShopBanner"
        or player.get("loaded") != ("ShopBanner" if loaded else "")
        or player.get("same_asset") is not loaded
        or type(player.get("visible_banners")) is not int
        or player["visible_banners"] != int(loaded)
    ):
        raise ValueError("shop_player_observation_mismatch")
