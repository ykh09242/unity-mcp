"""Execute the production fallback template with doubles only at Blender and I/O boundaries."""

import builtins
from contextlib import redirect_stdout
from dataclasses import dataclass, field
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Final

import pytest


SOURCE: Final = (
    Path(__file__).resolve().parents[2] / "MCPForUnity/Editor/Tools/Blender/BlenderBridgeTool.cs"
)


@dataclass
class BlenderObject:
    """Mutable Blender object boundary, including invalidated RNA references."""

    label: str
    selected: bool = False
    mode: str = "OBJECT"
    removed: bool = False
    children_recursive: list["BlenderObject"] = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.removed:
            raise ReferenceError("StructRNA of type Object has been removed")
        return self.label

    def select_set(self, value: bool) -> None:
        assert self.mode == "OBJECT", "Selection must be restored before entering Edit mode"
        self.selected = value


class BlenderBoundary:
    """Mutable scene/context boundary; production code owns every export/restoration decision."""

    def __init__(self) -> None:
        self.child = BlenderObject("Child")
        self.target = BlenderObject("A", children_recursive=[self.child])
        self.original = BlenderObject("B", selected=True, mode="EDIT")
        self.objects = {obj.name: obj for obj in (self.target, self.original, self.child)}
        self.layer_objects = SimpleNamespace(active=self.original, get=self.objects.get)
        self.export_error: RuntimeError | None = None
        self.restore_error: RuntimeError | None = None
        self.remove_original = False
        self.export_enters_edit = False
        self.export_calls: list[tuple[str, dict[str, str | bool | list[str]]]] = []
        self.mutations: list[str] = []
        self.bpy = SimpleNamespace(
            data=SimpleNamespace(objects=self.objects),
            context=self,
            ops=SimpleNamespace(
                object=SimpleNamespace(mode_set=self.mode_set, select_all=self.select_all),
                export_scene=SimpleNamespace(gltf=self.export_glb, fbx=self.export_fbx),
            ),
        )
        self.fake_os = SimpleNamespace(
            makedirs=lambda *args, **kwargs: self.mutations.append("mkdir"),
            path=SimpleNamespace(dirname=lambda path: "owned-memory-only", getsize=lambda path: 17),
        )

    @property
    def object(self) -> BlenderObject | None:
        return self.layer_objects.active

    @property
    def view_layer(self) -> SimpleNamespace:
        return SimpleNamespace(objects=self.layer_objects)

    @property
    def scene(self) -> SimpleNamespace:
        return SimpleNamespace(objects=list(self.objects.values()))

    @property
    def selected_objects(self) -> list[BlenderObject]:
        return [obj for obj in self.objects.values() if obj.selected]

    def mode_set(self, *, mode: str) -> None:
        assert self.object is not None, "Mode requires an active object"
        if mode != "OBJECT":
            assert self.object is self.original, "Restore the original active object before mode"
            assert self.original.selected, "Restore selection before mode"
            if self.restore_error is not None:
                raise self.restore_error
        self.mutations.append(f"mode:{mode}")
        for obj in self.selected_objects:
            obj.mode = mode
        self.object.mode = mode

    def select_all(self, *, action: str) -> None:
        assert action == "DESELECT"
        assert all(obj.mode == "OBJECT" for obj in self.selected_objects)
        self.mutations.append("deselect")
        for obj in self.objects.values():
            obj.select_set(False)

    def export_glb(self, **options) -> None:
        self.export("glb", options)

    def export_fbx(self, **options) -> None:
        self.export("fbx", options)

    def export(self, fmt: str, options: dict[str, str | bool]) -> None:
        assert self.object is None or self.object.mode == "OBJECT"
        self.export_calls.append(
            (fmt, {**options, "selected": [obj.name for obj in self.selected_objects]})
        )
        if self.remove_original:
            self.objects.pop(self.original.name)
            self.original.removed = True
        if self.export_enters_edit:
            for obj in self.selected_objects:
                obj.mode = "EDIT"
        if self.export_error is not None:
            raise self.export_error

    def run(self, **overrides) -> str:
        source = SOURCE.read_text(encoding="utf-8")
        match = re.search(r'const string template\s*=\s*@"((?:""|[^"])*)";', source, re.S)
        assert match is not None, "Production fallback template was not found"
        template = match.group(1).replace('""', '"')
        config = {
            "out": "owned-memory-only/model.fbx",
            "names": ["A"],
            "selection_only": False,
            "apply_modifiers": True,
            "format": "fbx",
            **overrides,
        }
        script = template.replace("__CFG__", json.dumps(json.dumps(config)), 1)

        def importer(name, *args, **kwargs):
            if name == "bpy":
                return self.bpy
            if name == "os":
                return self.fake_os
            return builtins.__import__(name, *args, **kwargs)

        output = io.StringIO()
        with redirect_stdout(output):
            exec(script, {"__builtins__": {**vars(builtins), "__import__": importer}})
        return output.getvalue()

    def assert_restored(self) -> None:
        assert self.selected_objects == [self.original]
        assert self.object is self.original
        assert self.original.mode == "EDIT"


@pytest.mark.parametrize("fmt", ["glb", "fbx"])
def test_named_export_restores_state_and_reports_actual_exported_objects(fmt: str) -> None:
    boundary = BlenderBoundary()
    result = json.loads(boundary.run(format=fmt))
    boundary.assert_restored()
    assert boundary.export_calls[0][0] == fmt
    assert boundary.export_calls[0][1]["selected"] == ["A", "Child"]
    assert boundary.export_calls[0][1]["use_selection"] is True
    assert result["exported"] == ["A", "Child"]
    assert result["selection_only"] is True
    assert result["bytes"] == 17


@pytest.mark.parametrize("fmt", ["glb", "fbx"])
def test_export_failure_restores_state_and_preserves_original_exception(fmt: str) -> None:
    boundary = BlenderBoundary()
    boundary.export_error = RuntimeError("controlled exporter failure")
    with pytest.raises(RuntimeError, match="controlled exporter failure") as failure:
        boundary.run(format=fmt)
    assert failure.value is boundary.export_error
    boundary.assert_restored()


def test_missing_names_reject_before_any_state_or_filesystem_mutation() -> None:
    boundary = BlenderBoundary()
    with pytest.raises(Exception, match="Objects not found in Blender: Missing"):
        boundary.run(names=["A", "Missing"])
    boundary.assert_restored()
    assert boundary.mutations == []
    assert boundary.export_calls == []


@pytest.mark.parametrize("selection_only", [False, True])
def test_existing_selection_and_whole_scene_exports_keep_inclusion(selection_only: bool) -> None:
    boundary = BlenderBoundary()
    result = json.loads(boundary.run(names=[], selection_only=selection_only))
    boundary.assert_restored()
    assert boundary.export_calls[0][1]["use_selection"] is selection_only
    assert result["exported"] == (["B"] if selection_only else ["A", "B", "Child"])


def test_empty_selection_rejects_without_mutation() -> None:
    boundary = BlenderBoundary()
    boundary.original.mode = "OBJECT"
    boundary.original.selected = False
    with pytest.raises(Exception, match="Nothing is selected"):
        boundary.run(names=[], selection_only=True)
    assert boundary.mutations == []
    assert boundary.object is boundary.original
    assert boundary.selected_objects == []


@pytest.mark.parametrize("mode", ["OBJECT", "SCULPT", "POSE"])
def test_restores_original_object_mode(mode: str) -> None:
    boundary = BlenderBoundary()
    boundary.original.mode = mode
    boundary.run()
    assert boundary.object is boundary.original
    assert boundary.selected_objects == [boundary.original]
    assert boundary.original.mode == mode


def test_restores_selection_before_reentering_multi_object_edit_mode() -> None:
    boundary = BlenderBoundary()
    boundary.child.selected = True
    boundary.child.mode = "EDIT"
    boundary.run()
    assert boundary.selected_objects == [boundary.original, boundary.child]
    assert boundary.object is boundary.original
    assert boundary.original.mode == boundary.child.mode == "EDIT"


def test_leaves_exporter_edit_mode_before_restoring_selection() -> None:
    boundary = BlenderBoundary()
    boundary.export_enters_edit = True
    boundary.export_error = RuntimeError("controlled exporter failure")
    with pytest.raises(RuntimeError) as failure:
        boundary.run()
    assert failure.value is boundary.export_error
    boundary.assert_restored()


def test_original_null_active_object_is_restored() -> None:
    boundary = BlenderBoundary()
    boundary.original.mode = "OBJECT"
    boundary.layer_objects.active = None
    boundary.run()
    assert boundary.object is None
    assert boundary.selected_objects == [boundary.original]


def test_removed_active_object_does_not_prevent_surviving_selection_restore() -> None:
    boundary = BlenderBoundary()
    boundary.child.selected = True
    boundary.child.mode = "EDIT"
    boundary.remove_original = True
    boundary.export_error = RuntimeError("controlled exporter failure")
    with pytest.raises(RuntimeError) as failure:
        boundary.run()
    assert failure.value is boundary.export_error
    assert boundary.object is None
    assert boundary.selected_objects == [boundary.child]
    assert boundary.child.mode == "OBJECT"


def test_restoration_error_does_not_reraise_an_unrelated_callers_exception() -> None:
    boundary = BlenderBoundary()
    boundary.restore_error = RuntimeError("controlled mode restoration failure")
    try:
        raise LookupError("unrelated caller failure")
    except LookupError:
        with pytest.raises(RuntimeError, match="state restoration failed"):
            boundary.run()


@pytest.mark.parametrize("export_fails", [False, True])
def test_restoration_failure_is_actionable_and_does_not_mask_export_failure(
    export_fails: bool,
) -> None:
    boundary = BlenderBoundary()
    boundary.restore_error = RuntimeError("controlled mode restoration failure")
    if export_fails:
        boundary.export_error = RuntimeError("controlled exporter failure")
    with pytest.raises(RuntimeError) as failure:
        boundary.run()
    assert boundary.object is boundary.original
    assert boundary.selected_objects == [boundary.original]
    if export_fails:
        assert failure.value is boundary.export_error
        assert "restoration" in str(failure.value.__cause__)
    else:
        assert "restoration" in str(failure.value)
    assert "controlled mode restoration failure" in str(
        failure.value.__cause__ if export_fails else failure.value
    )


@pytest.mark.parametrize("export_fails", [False, True])
def test_disappeared_original_object_is_skipped_without_masking_export_failure(
    export_fails: bool,
) -> None:
    boundary = BlenderBoundary()
    boundary.remove_original = True
    if export_fails:
        boundary.export_error = RuntimeError("controlled exporter failure")
        with pytest.raises(RuntimeError) as failure:
            boundary.run()
        assert failure.value is boundary.export_error
    else:
        assert json.loads(boundary.run())["exported"] == ["A", "Child"]
    assert boundary.object is None
    assert boundary.selected_objects == []
