from tools.experiments.verify_compact_edit_followup import disk_snapshot, disk_valid


def test_material_requires_exact_saved_queue_and_unchanged_neighbors():
    spec = {"task": "n02", "before": {"queue": 2011}, "after": {"queue": 2457}}
    original = b"  m_CustomRenderQueue: 2011\r\n  m_EnableInstancingVariants: 0\r\n"
    before = disk_snapshot(spec, original, b"guid: same")
    for source, meta, expected in (
        (original, b"guid: same", False),
        (original.replace(b"2011", b"2457"), b"guid: same", True),
        (original.replace(b"2011", b"2457").replace(b": 0", b": 1"), b"guid: same", False),
        (original.replace(b"2011", b"2457"), b"guid: other", False),
    ):
        assert (
            disk_valid(spec, {"before": before, "after": disk_snapshot(spec, source, meta)})
            is expected
        )
    assert disk_valid(spec, {"before": before, "after": None}) is None


def test_audio_only_allows_default_preload_change():
    spec = {"task": "n05", "before": {"preload": True}, "after": {"preload": False}}
    meta = b"  defaultSettings:\n    preloadAudioData: 1\n    quality: 1\n  Standalone:\n    preloadAudioData: 1\n"
    original = disk_snapshot(spec, b"wave", meta)
    edited = meta.replace(b"preloadAudioData: 1", b"preloadAudioData: 0", 1)
    for wave, metadata, expected in (
        (b"wave", edited, True),
        (b"wave", meta, False),
        (b"wave", edited.replace(b"quality: 1", b"quality: 0"), False),
        (b"wave", edited.replace(b"preloadAudioData: 1", b"preloadAudioData: 0"), False),
        (b"changed", edited, False),
    ):
        assert (
            disk_valid(spec, {"before": original, "after": disk_snapshot(spec, wave, metadata)})
            is expected
        )


def test_uxml_preserves_neighbors_and_grades_exact_punctuation():
    spec = {
        "task": "r03",
        "before": {"element": "owned", "tooltip": "old"},
        "after": {"tooltip": "new"},
    }
    source = b'<ui:UXML xmlns:ui="UnityEngine.UIElements"><ui:Label name="neighbor" tooltip="old"/><ui:Label name="owned" tooltip="old"/></ui:UXML>'
    original = disk_snapshot(spec, source, b"meta")
    for changed, expected in (
        (source.replace(b'owned" tooltip="old', b'owned" tooltip="new'), True),
        (source.replace(b'owned" tooltip="old', b'owned" tooltip="new.'), False),
        (source.replace(b'tooltip="old"', b'tooltip="new"'), False),
    ):
        assert (
            disk_valid(spec, {"before": original, "after": disk_snapshot(spec, changed, b"meta")})
            is expected
        )
