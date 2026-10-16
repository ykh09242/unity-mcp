"""Public texture normalization preserves platform requests and rejects hidden conflicts."""

from copy import deepcopy
from importlib import import_module

import pytest


textures = import_module("services.tools.manage_texture")


@pytest.mark.parametrize("alias", ["platform_settings", "platformSettings"])
def test_platform_settings_forward_native_fields_without_mutating_input(alias):
    # Given supported and future native settings whose outcomes belong to the Editor.
    platforms = {
        "Standalone": {"overridden": False, "maxTextureSize": 64},
        "FutureConsole": {"futureField": None},
    }
    request = {"readable": False, alias: platforms}
    original = deepcopy(request)

    # When public normalization constructs the native request.
    normalized, error = textures._normalize_import_settings(request)

    # Then every nested field survives, including false/null, and caller data is isolated.
    assert error is None
    assert normalized == {"isReadable": False, "platformSettings": platforms}
    normalized["platformSettings"]["Standalone"]["overridden"] = True
    assert request == original


@pytest.mark.parametrize("key", ["unrecognized_setting", "futureSetting", "maxTextureSize"])
def test_unknown_global_importer_fields_reach_native_outcome_reporting(key):
    # Given an unknown public field alongside an accepted known field.
    request = {"readable": True, key: 0}

    # When it crosses public normalization.
    normalized, error = textures._normalize_import_settings(request)

    # Then native can preserve valid partial writes and report unsupported requests.
    assert error is None
    assert normalized == {"isReadable": True, key: 0}


@pytest.mark.parametrize(
    "known,native,first,second",
    [
        ("readable", "isReadable", True, False),
        ("readable", "isReadable", True, 1),
        ("max_texture_size", "maxTextureSize", 64, 128),
    ],
)
def test_normalized_global_alias_collision_is_explicit(known, native, first, second):
    # Given two inputs that would overwrite one canonical native field.
    normalized, error = textures._normalize_import_settings({known: first, native: second})

    # Then neither differing value is silently discarded.
    assert normalized is None
    assert "conflict" in error.lower() and native in error


@pytest.mark.parametrize("first,second", [(True, False), (True, 1), (64, 64.0)])
def test_conflicting_platform_aliases_reject_type_sensitive_differences(first, second):
    # Given dual aliases carrying different JSON values or scalar types.
    request = {
        "platform_settings": {"Standalone": {"overridden": first}},
        "platformSettings": {"Standalone": {"overridden": second}},
    }

    # When canonicalization would otherwise choose one alias.
    normalized, error = textures._normalize_import_settings(request)

    # Then the conflict is visible instead of losing one request.
    assert normalized is None
    assert "conflict" in error.lower()


def test_identical_platform_aliases_deduplicate_without_losing_fields():
    # Given identical aliases with different dictionary insertion order.
    request = {
        "platform_settings": {"Standalone": {"overridden": True, "format": "RGBA32"}},
        "platformSettings": {"Standalone": {"format": "RGBA32", "overridden": True}},
    }

    # When public normalization merges the equivalent representation.
    normalized, error = textures._normalize_import_settings(request)

    # Then exactly one intact native platform block is forwarded.
    assert error is None
    assert normalized == {"platformSettings": request["platform_settings"]}


@pytest.mark.parametrize("value", [None, [], "not-an-object", {"Standalone": []}])
def test_malformed_platform_container_is_rejected_locally(value):
    # Given a malformed documented platform container shape.
    normalized, error = textures._normalize_import_settings({"platform_settings": value})

    # Then normalization reports it rather than silently omitting the request.
    assert normalized is None
    assert "platform_settings" in error and "object" in error


@pytest.mark.parametrize(
    "field",
    [
        "srgb",
        "sRGBTexture",
        "aniso_level",
        "anisoLevel",
        "sprite_pixels_per_unit",
        "spritePixelsPerUnit",
        "sprite_pivot",
        "spritePivot",
    ],
)
def test_explicit_importer_null_cannot_disappear_behind_a_valid_change(field):
    # Given an explicitly requested null importer value beside a valid field.
    normalized, error = textures._normalize_import_settings({"readable": True, field: None})

    # Then the public boundary refuses the invalid value before any native partial write.
    assert normalized is None
    assert field in error and "null" in error
