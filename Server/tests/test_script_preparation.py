"""Read-only proposals have complete, verifiable Unity target identity."""

import asyncio  # The production deadline/cancellation boundary uses asyncio.
import copy
import hashlib
import importlib
from unittest.mock import AsyncMock

import pytest

tools = importlib.import_module("services.tools.script_apply_edits")


def prepared(original="class Foo {}\n", candidate="class Foo { void M() {} }\n"):
    sha = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    return {
        "success": True,
        "data": {
            "preview": True,
            "complete": True,
            "truncated": False,
            "path": "Assets/Foo.cs",
            "uri": "mcpforunity://path/Assets/Foo.cs",
            "project_root": "C:/UnityProject",
            "absolute_path": "C:/UnityProject/Assets/Foo.cs",
            "original_contents": original,
            "new_contents": candidate,
            "original_sha256": sha(original),
            "candidate_sha256": sha(candidate),
            "candidate_bytes_sha256": sha(candidate),
            "sha256": sha(original),
            "encoding": "utf-8",
            "bom": False,
            "no_op": original == candidate,
            "editsApplied": 0,
            "editsPrepared": 0 if original == candidate else 1,
            "scheduledRefresh": False,
        },
    }


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(
        tools, "get_unity_instance_from_context", AsyncMock(return_value="Unity@abc")
    )
    reader = AsyncMock(return_value=prepared())
    writer = AsyncMock()
    monkeypatch.setattr(tools, "send_with_unity_instance", reader)
    monkeypatch.setattr(tools, "send_mutation", writer)
    return reader, writer


@pytest.mark.asyncio
async def test_structured_preview_is_read_only_complete_handoff(wire):
    reader, writer = wire
    response = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert response["success"] is True
    writer.assert_not_awaited()
    params = reader.await_args.args[3]
    assert params["action"] == "preview_edit" and params["options"]["preview"] is True
    data = response["data"]
    assert data["absolute_path"] == "C:/UnityProject/Assets/Foo.cs"
    assert data["unity_instance"] == "Unity@abc"
    assert data["sha256"] == data["original_sha256"]
    assert data["native_apply"]["status"] == "verification_required"
    assert data["diff"] and data["new_contents"] == prepared()["data"]["new_contents"]


class OrderedWire:
    """Mutable controlled native boundary; this is not a C# engine or filesystem proof.

    Only synthetic method snippets are recognized. Text spans are applied independently
    with Python codepoint coordinates so production range conversion is observable.
    """

    def __init__(self):
        self.original = "class Foo {\n    void Existing() {}\n}\n"
        self.bom = False
        self.calls = []
        self.commits = []
        self.legacy_writes = []
        self.corrupt = None
        self.fail_action = None
        self.commit_delay = 0
        self.stage_delay = 0
        self.refresh_failure = False
        self.selected = []
        self.final_refusal = False
        self.after_stage = None

    @staticmethod
    def digest(text, bom=False):
        return hashlib.sha256((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")).hexdigest()

    def response(self, candidate, final=False):
        response = prepared(self.original, candidate)
        response["data"].update(
            original_bom=self.bom,
            bom=self.bom,
            original_bytes_sha256=self.digest(self.original, self.bom),
            candidate_bytes_sha256=self.digest(candidate, self.bom),
            validation_status="passed" if final else "pending",
        )
        return response

    @staticmethod
    def span(source, edit):
        lines = source.splitlines(keepends=True)
        if source.endswith(("\r", "\n")):
            lines.append("")
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        start = offsets[edit["startLine"] - 1] + edit["startCol"] - 1
        end = offsets[edit["endLine"] - 1] + edit["endCol"] - 1
        return source[:start] + edit["newText"] + source[end:]

    async def select(self, ctx):
        self.selected.append(ctx)
        return "Unity@abc"

    async def legacy(self, ctx, instance, command, params, **kwargs):
        self.legacy_writes.append(copy.deepcopy(params))
        return {"success": True}

    async def send(self, sender, instance, command, params, **kwargs):
        assert instance == "Unity@abc" and command == "manage_script"
        self.calls.append((copy.deepcopy(params), dict(kwargs)))
        action = params["action"]
        if action == self.fail_action:
            return {"success": False, "error": f"Unknown action: {action}"}
        if action == "read":
            return {"success": True, "data": {"contents": self.original}}
        if action == "validate_edit":
            return {"success": True}
        if action == "get_sha":
            return {"success": True, "data": {"sha256": self.digest(self.original)}}
        if action in {"preview_edit", "preview_text_edits"}:
            return prepared(self.original, self.original)
        assert action in {"preview_script_buffer", "apply_prepared_script"}, action
        if action == "apply_prepared_script":
            assert kwargs.get("retry_on_reload") is False
            self.commits.append(copy.deepcopy(params))
            if self.commit_delay:
                await asyncio.sleep(self.commit_delay)
            candidate = params["candidate_contents"]
            assert params["original_bytes_sha256"] == self.digest(self.original, self.bom)
            assert params["candidate_bytes_sha256"] == self.digest(candidate, self.bom)
            return {
                "success": True,
                "data": {
                    "applied": True,
                    "sha256": self.digest(candidate),
                    "bytes_sha256": self.digest(candidate, self.bom),
                    "bom": self.bom,
                    "scheduledRefresh": not self.refresh_failure,
                    "refresh_status": "failed" if self.refresh_failure else "scheduled",
                },
            }
        if self.stage_delay:
            await asyncio.sleep(self.stage_delay)
        source = params.get("source_contents")
        if source is None:
            assert "edit" not in params
            return self.response(self.original)
        assert params["original_sha256"] == self.digest(self.original)
        assert params["original_bytes_sha256"] == self.digest(self.original, self.bom)
        assert params["source_sha256"] == self.digest(source)
        assert params["source_bytes_sha256"] == self.digest(source, self.bom)
        assert params["original_bom"] is self.bom and params["bom"] is self.bom
        edit = params.get("edit")
        candidate = source
        if edit and "startLine" in edit:
            candidate = self.span(source, edit)
        elif edit:
            assert edit["op"] == "replace_method"
            old = "void " + edit["methodName"] + "() {}"
            if old not in source:
                return {"success": False, "code": "missing_method", "message": old}
            candidate = source.replace(old, edit["replacement"], 1)
        response = self.response(candidate, params.get("validate_final") is True)
        if params.get("validate_final") is True and self.final_refusal:
            return {
                "success": False,
                "code": "validation_failed",
                "message": "Synthetic final native refusal",
            }
        if self.corrupt:
            key, value = self.corrupt
            response["data"][key] = value
        if self.after_stage:
            self.after_stage()
        return response


@pytest.fixture
def ordered_wire(monkeypatch):
    native = OrderedWire()
    monkeypatch.setattr(tools, "get_unity_instance_from_context", native.select)
    monkeypatch.setattr(tools, "send_with_unity_instance", native.send)
    monkeypatch.setattr(tools, "send_mutation", native.legacy)
    return native


def ordered_edits():
    return [
        {"op": "regex_replace", "pattern": r"(?m)^}", "replacement": "    void Added() {}\n}"},
        {
            "op": "replace_method",
            "methodName": "Added",
            "replacement": "void Added() { int value = 1; }",
        },
    ]


async def ordered_preview(native, edits=None):
    return await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits() if edits is None else edits,
        {"mixed_order": "request", "preview": True},
    )


@pytest.mark.asyncio
async def test_ordered_text_creates_native_structured_target_without_writes(ordered_wire):
    native = ordered_wire
    result = await ordered_preview(native)
    assert result["success"] is True, result
    data = result["data"]
    assert (
        data["new_contents"]
        == "class Foo {\n    void Existing() {}\n    void Added() { int value = 1; }\n}\n"
    )
    assert data["ordering"] == "request" and data["coordinate_space"] == "current"
    assert data["steps_processed"] == 2 and data["validation_status"] == "passed"
    assert data["editsApplied"] == 0 and data["scheduledRefresh"] is False
    assert len(native.selected) == 1 and len(native.calls) == 3
    assert not native.commits and not native.legacy_writes
    assert "void Added() {}" in native.calls[-1][0]["source_contents"]
    assert native.calls[1][0]["validate_final"] is False
    assert native.calls[-1][0]["validate_final"] is True


@pytest.mark.asyncio
async def test_ordered_structured_then_regex_uses_native_replacement_buffer(ordered_wire):
    edits = [
        {
            "op": "replace_method",
            "methodName": "Existing",
            "replacement": "void Existing() { int value = 1; }",
        },
        {"op": "regex_replace", "pattern": "value = 1", "replacement": "value = 2"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is True, result
    assert "value = 2" in result["data"]["new_contents"]
    assert "value = 1" not in result["data"]["new_contents"]
    reversed_result = await ordered_preview(ordered_wire, list(reversed(edits)))
    assert reversed_result["success"] is True, reversed_result
    assert "value = 1" in reversed_result["data"]["new_contents"]
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_reversed_created_target_refuses_before_any_write(ordered_wire):
    result = await ordered_preview(ordered_wire, list(reversed(ordered_edits())))
    assert result["success"] is False and result["code"] == "missing_method"
    assert not ordered_wire.commits and not ordered_wire.legacy_writes
    assert [p["action"] for p, _ in ordered_wire.calls] == ["preview_script_buffer"] * 2


@pytest.mark.asyncio
async def test_ordered_current_utf16_coordinates_and_later_overlap(ordered_wire):
    ordered_wire.original = "class Foo {\n    // 😀x\n    void Existing() {}\n}\r\n"
    edits = [
        {"op": "prepend", "text": "// head\r\n"},
        {
            "range": {"start": {"line": 2, "character": 9}, "end": {"line": 2, "character": 10}},
            "newText": "한",
        },
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is True, result
    assert (
        result["data"]["new_contents"]
        == "// head\r\nclass Foo {\n    // 😀한\n    void Existing() {}\n}\r\n"
    )
    span = ordered_wire.calls[2][0]["edit"]
    assert (span["startLine"], span["startCol"], span["endCol"]) == (3, 9, 10)


@pytest.mark.asyncio
async def test_ordered_same_position_insertions_use_current_buffer(ordered_wire):
    edits = [
        {"op": "prepend", "text": "// A\n"},
        {"op": "prepend", "text": "// B\n"},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is True, result
    assert result["data"]["new_contents"].startswith("// B\n// A\nclass Foo")


@pytest.mark.asyncio
async def test_ordered_regex_miss_is_noop_with_final_validation(ordered_wire):
    edits = [
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
        {"op": "regex_replace", "pattern": "absent", "replacement": "unused"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is True, result
    assert result["data"]["new_contents"] == ordered_wire.original
    assert result["data"]["no_op"] is True
    assert result["data"]["validation_status"] == "passed"
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize("bom", [False, True])
async def test_ordered_bom_raw_digest_and_literal_bytes_are_preserved(ordered_wire, bom):
    native = ordered_wire
    native.bom = bom
    native.original = 'class Foo {\r\n    void Existing() {}\n    string s = @"한😀\nraw";\r\n}'
    edits = [
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
        {"op": "append", "text": "// tail"},
    ]
    result = await ordered_preview(native, edits)
    assert result["success"] is True, result
    data = result["data"]
    assert data["new_contents"] == native.original + "// tail"
    assert data["original_bom"] is bom and data["bom"] is bom
    assert data["candidate_bytes_sha256"] == native.digest(data["new_contents"], bom)
    assert data["original_bytes_sha256"] == native.digest(native.original, bom)
    assert data["candidate_sha256"] == native.digest(data["new_contents"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_bytes_sha256", "0" * 64),
        ("original_bytes_sha256", "0" * 64),
        ("original_bom", True),
        ("bom", True),
        ("validation_status", "pending"),
        ("absolute_path", "C:/Other/Assets/Foo.cs"),
        ("original_sha256", "0" * 64),
    ],
)
async def test_ordered_corrupt_stage_refuses_without_commit(ordered_wire, field, value):
    ordered_wire.corrupt = (field, value)
    result = await ordered_preview(ordered_wire)
    assert result["success"] is False, result
    assert len(ordered_wire.calls) >= 2, result
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_old_peer_refuses_new_snapshot_without_fallback(ordered_wire):
    ordered_wire.fail_action = "preview_script_buffer"
    result = await ordered_preview(ordered_wire)
    assert result["success"] is False and "Unknown action:" in result.get("error", "")
    assert [p["action"] for p, _ in ordered_wire.calls] == ["preview_script_buffer"]
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"mixed_order": None},
        {"mixed_order": True},
        {"mixed_order": "unknown"},
        {"mixed_order": "request", "applyMode": "atomic"},
        {"mixed_order": "request", "applyMode": "sequential", "apply_mode": "atomic"},
    ],
)
async def test_ordered_invalid_option_refuses_before_instance_or_wire(ordered_wire, options):
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits(),
        {**options, "preview": True},
    )
    assert result["success"] is False
    assert result["code"] == "invalid_options", result
    assert not ordered_wire.selected and not ordered_wire.calls and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_pure_batch_refuses_before_instance_selection(ordered_wire):
    result = await ordered_preview(ordered_wire, [ordered_edits()[0]])
    assert result["success"] is False
    assert not ordered_wire.selected and not ordered_wire.calls and not ordered_wire.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "digests",
    [
        {},
        {"precondition_bytes_sha256": "0" * 64},
        {"precondition_bytes_sha256": True, "candidate_bytes_sha256": "0" * 64},
        {"precondition_bytes_sha256": "z" * 64, "candidate_bytes_sha256": "0" * 64},
    ],
)
async def test_ordered_apply_requires_both_raw_digests_before_io(ordered_wire, digests):
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits(),
        {"mixed_order": "request", **digests},
    )
    assert result["success"] is False
    assert not ordered_wire.selected and not ordered_wire.calls and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_candidate_digest_mismatch_never_dispatches_commit(ordered_wire):
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits(),
        {
            "mixed_order": "request",
            "precondition_bytes_sha256": ordered_wire.digest(ordered_wire.original),
            "candidate_bytes_sha256": "0" * 64,
        },
    )
    assert result["success"] is False
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_apply_one_commit_preserves_known_refresh_failure(ordered_wire):
    native = ordered_wire
    proposal = await ordered_preview(native)
    assert proposal["success"] is True, proposal
    native.calls.clear()
    native.refresh_failure = True
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits(),
        {
            "mixed_order": "request",
            "precondition_bytes_sha256": proposal["data"]["original_bytes_sha256"],
            "candidate_bytes_sha256": proposal["data"]["candidate_bytes_sha256"],
        },
    )
    assert result["data"]["applied"] is True, result
    assert result["data"]["refresh_status"] == "failed"
    assert result["data"]["bytes_sha256"] == proposal["data"]["candidate_bytes_sha256"]
    assert len(native.commits) == 1 and not native.legacy_writes


@pytest.mark.asyncio
async def test_ordered_deadline_includes_instance_selection(ordered_wire, monkeypatch):
    async def slow_selection(ctx):
        await asyncio.sleep(0.1)
        return "Unity@abc"

    monkeypatch.setattr(tools, "get_unity_instance_from_context", slow_selection)
    monkeypatch.setattr(tools.config, "command_total_timeout", 0.01)
    result = await ordered_preview(ordered_wire)
    assert result["success"] is False and result["code"] == "deadline_exceeded", result
    assert not ordered_wire.calls and not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_post_dispatch_timeout_reports_unknown_without_replay(
    ordered_wire, monkeypatch
):
    native = ordered_wire
    proposal = await ordered_preview(native)
    assert proposal["success"] is True, proposal
    native.commit_delay = 0.1
    monkeypatch.setattr(tools.config, "command_total_timeout", 0.02)
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits(),
        {
            "mixed_order": "request",
            "precondition_bytes_sha256": proposal["data"]["original_bytes_sha256"],
            "candidate_bytes_sha256": proposal["data"]["candidate_bytes_sha256"],
        },
    )
    assert result["success"] is False
    assert result["data"]["outcome"] == "unknown", result
    assert result["data"].get("applied") is not False
    assert result["data"]["expected_bytes_sha256"] == proposal["data"]["candidate_bytes_sha256"]
    assert len(native.commits) == 1 and not native.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize("option", [None, "text_first"])
async def test_ordered_legacy_mixed_preview_remains_unsupported(ordered_wire, option):
    options = {"preview": True}
    if option is not None:
        options["mixed_order"] = option
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", ordered_edits(), options)
    assert result["success"] is False and result["code"] == "unsupported_preview"
    assert not ordered_wire.calls and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_final_only_validation_allows_balanced_later_buffer(ordered_wire):
    edits = [
        {"op": "prepend", "text": "namespace Temporary {\n"},
        {"op": "append", "text": "}\n"},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is True, result
    stages = [p for p, _ in ordered_wire.calls if "source_contents" in p]
    assert [p["validate_final"] for p in stages] == [False, False, True]
    assert (
        result["data"]["new_contents"] == "namespace Temporary {\n" + ordered_wire.original + "}\n"
    )


@pytest.mark.asyncio
async def test_ordered_final_native_validation_refusal_never_commits(ordered_wire):
    ordered_wire.final_refusal = True
    result = await ordered_preview(ordered_wire)
    assert result["success"] is False and result["code"] == "validation_failed"
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [32, 33])
async def test_ordered_operation_bound_is_finite_before_io(ordered_wire, count):
    edits = [{"op": "prepend", "text": "// marker\n"}] * (count - 1) + [
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    if count == 33:
        assert result["success"] is False
        assert not ordered_wire.selected and not ordered_wire.calls
    else:
        assert result["success"] is True, result
        assert result["data"]["steps_processed"] == 32
        assert len(ordered_wire.calls) == 33
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_aggregate_payload_bound_rejects_before_io(ordered_wire):
    edits = [
        {"op": "prepend", "text": "x" * 32768},
        {"op": "append", "text": "y" * 32768},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is False and result["code"] == "payload_too_large", result
    assert not ordered_wire.selected and not ordered_wire.calls and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_active_regex_budget_excludes_native_io_gaps(ordered_wire, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(tools.bounded_regex, "monotonic", lambda: clock[0])

    def advance_native_clock():
        clock[0] += 2.0

    ordered_wire.after_stage = advance_native_clock
    edits = [
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is True, result
    assert result["data"]["new_contents"] == ordered_wire.original
    assert clock[0] >= 104.0


@pytest.mark.asyncio
async def test_ordered_cumulative_regex_character_allowance_cannot_renew(ordered_wire, monkeypatch):
    monkeypatch.setattr(tools.bounded_regex, "MAX_WORK_CHARS", 250)
    edits = [
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is False
    # Reaching two text stages separates aggregate exhaustion from per-stage refusal.
    assert len(ordered_wire.calls) >= 3, result
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize("bom", [False, True])
async def test_ordered_apply_noop_never_dispatches_commit_or_refresh(ordered_wire, bom):
    native = ordered_wire
    native.bom = bom
    edits = [
        {"op": "prepend", "text": "// temporary\n"},
        {"op": "regex_replace", "pattern": "// temporary\\n", "replacement": ""},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    raw_sha = native.digest(native.original, bom)
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        edits,
        {
            "mixed_order": "request",
            "precondition_bytes_sha256": raw_sha,
            "candidate_bytes_sha256": raw_sha,
        },
    )
    assert result["success"] is True, result
    assert result["data"]["no_op"] is True and result["data"]["editsApplied"] == 0
    assert result["data"]["scheduledRefresh"] is False
    assert not native.commits and not native.legacy_writes


@pytest.mark.asyncio
async def test_ordered_original_raw_mismatch_refuses_after_snapshot(ordered_wire):
    native = ordered_wire
    native.bom = True
    # The logical digest intentionally omits the real BOM; it cannot authorize apply.
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        ordered_edits(),
        {
            "mixed_order": "request",
            "precondition_bytes_sha256": native.digest(native.original),
            "candidate_bytes_sha256": "0" * 64,
        },
    )
    assert result["success"] is False
    assert [p["action"] for p, _ in native.calls] == ["preview_script_buffer"]
    assert not native.commits and not native.legacy_writes


@pytest.mark.asyncio
async def test_ordered_pre_dispatch_cancellation_propagates_without_commit(
    ordered_wire, monkeypatch
):
    async def cancelled_selection(ctx):
        raise asyncio.CancelledError()

    monkeypatch.setattr(tools, "get_unity_instance_from_context", cancelled_selection)
    with pytest.raises(asyncio.CancelledError):
        await ordered_preview(ordered_wire)
    assert not ordered_wire.calls and not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_invalid_current_range_refuses_without_commit(ordered_wire):
    edits = [
        {"op": "prepend", "text": "// shift\n"},
        {
            "op": "replace_range",
            "startLine": 99,
            "startCol": 1,
            "endLine": 99,
            "endCol": 1,
            "text": "unreachable",
        },
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is False and result["code"] == "invalid_range", result
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
async def test_ordered_active_processing_allowance_cannot_renew(ordered_wire, monkeypatch):
    original_runner = tools._run_regex_work

    async def queued_processing(budget, work):
        # A deterministic queue interval is part of active preprocessing accounting.
        # Each 0.4 second phase fits the individual one-second WorkBudget; three do not.
        await asyncio.sleep(0.4)
        return await original_runner(budget, work)

    monkeypatch.setattr(tools, "_run_regex_work", queued_processing)
    edits = [
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
        {"op": "replace_method", "methodName": "Existing", "replacement": "void Existing() {}"},
        {"op": "regex_replace", "pattern": "Existing", "replacement": "Existing"},
    ]
    result = await ordered_preview(ordered_wire, edits)
    assert result["success"] is False
    assert len(ordered_wire.calls) >= 3, result
    assert not ordered_wire.commits and not ordered_wire.legacy_writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("complete", False),
        ("truncated", True),
        ("new_contents", "truncated"),
        ("candidate_sha256", "0" * 64),
        ("candidate_bytes_sha256", "0" * 64),
        ("absolute_path", "C:/WrongProject/Assets/Foo.cs"),
        ("path", "Assets/../Foo.cs"),
        ("encoding", "utf-16"),
        ("bom", True),
        ("scheduledRefresh", True),
        ("editsApplied", 1),
        ("editsPrepared", 0),
        ("editsPrepared", -1),
        ("editsPrepared", True),
    ],
)
async def test_incomplete_or_inconsistent_preparation_cannot_be_used(wire, field, value):
    reader, writer = wire
    response = prepared()
    response["data"][field] = value
    reader.return_value = response
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert result["success"] is False
    assert result["code"] == "invalid_preview"
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_combined_source_candidate_limit_and_noop_count(wire):
    reader, _ = wire
    reader.return_value = prepared("x" * (512 * 1024), "y" * (512 * 1024 + 1))
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert result["success"] is False and result["code"] == "invalid_preview"
    response = prepared("same", "same")
    response["data"]["editsPrepared"] = 1
    reader.return_value = response
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert result["success"] is False and result["code"] == "invalid_preview"


@pytest.mark.asyncio
async def test_public_text_preview_uses_readonly_alias(wire, monkeypatch):
    module = importlib.import_module("services.tools.manage_script")
    reader, _ = wire
    writer = AsyncMock()
    monkeypatch.setattr(
        module, "get_unity_instance_from_context", AsyncMock(return_value="Unity@abc")
    )
    monkeypatch.setattr(module, "send_with_unity_instance", reader)
    monkeypatch.setattr(module, "send_mutation", writer)
    response = await module.apply_text_edits(
        AsyncMock(),
        "Assets/Foo.cs",
        [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// next\n"}],
        options={"preview": True},
    )
    assert response["success"] is True
    assert reader.await_args.args[3]["action"] == "preview_text_edits"
    writer.assert_not_awaited()
    reader.return_value = {"success": False, "error": "Unknown action: preview_text_edits"}
    response = await module.apply_text_edits(
        AsyncMock(),
        "Assets/Foo.cs",
        [{"startLine": 1, "startCol": 1, "endLine": 1, "endCol": 1, "newText": "// next\n"}],
        options={"preview": True},
    )
    assert response["success"] is False and response["error"].startswith("Unknown action:")
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_and_noop_proposals_do_not_offer_local_apply(wire, monkeypatch):
    from core.config import config

    reader, _ = wire
    monkeypatch.setattr(config, "http_remote_hosted", True)
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert result["data"]["native_apply"]["status"] == "unavailable_remote"
    reader.return_value = prepared("class Foo {}\n", "class Foo {}\n")
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert result["data"]["native_apply"]["status"] == "not_needed"


@pytest.mark.asyncio
async def test_preview_disconnect_is_not_recovered_as_mutation_success(wire):
    reader, writer = wire
    reader.return_value = {"success": False, "error": "connection closed"}
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [{"op": "insert_method", "replacement": "void M() {}"}],
        {"preview": True},
    )
    assert result == {"success": False, "error": "connection closed"}
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [True, False])
async def test_older_unity_cannot_treat_preview_as_a_write(wire, structured):
    reader, writer = wire
    seen = []

    async def old_unity(*args, **kwargs):
        action = args[3]["action"]
        seen.append(action)
        if action == "read":
            return {"success": True, "data": {"contents": "class Foo {}\n"}}
        assert action not in ("edit", "apply_text_edits", "update")
        return {"success": False, "error": f"Unknown action: {action}"}

    reader.side_effect = old_unity
    edits = (
        [{"op": "insert_method", "replacement": "void M() {}"}]
        if structured
        else [{"op": "append", "text": "// next\n"}]
    )
    result = await tools.script_apply_edits(AsyncMock(), "Foo", "Assets", edits, {"preview": True})
    assert result["success"] is False and "Unknown action:" in result["error"]
    assert seen == (["preview_edit"] if structured else ["read", "preview_text_edits"])
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["anchor_insert", "anchor_replace"])
@pytest.mark.parametrize("shadow", ["insert", "content"])
@pytest.mark.parametrize("shadow_value", ["", "ignored"])
async def test_ordered_anchor_shadow_alias_cannot_hide_overlimit_native_payload(
    wire, op, shadow, shadow_value
):
    reader, writer = wire
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [
            {
                "op": op,
                "anchor": "}",
                shadow: shadow_value,
                "replacement": "/*" + "x" * 70000 + "*/",
            },
            {"op": "regex_replace", "pattern": "absent", "replacement": ""},
        ],
        {"mixed_order": "request", "preview": True},
    )
    assert result["success"] is False and result["code"] == "payload_too_large", result
    tools.get_unity_instance_from_context.assert_not_awaited()
    reader.assert_not_awaited()
    writer.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["anchor_insert", "anchor_replace"])
async def test_ordered_anchor_empty_replacement_respects_native_base64_precedence(wire, op):
    import base64

    reader, writer = wire
    reader.return_value = {"success": False, "error": "Unknown action: preview_script_buffer"}
    payload = "/*" + "x" * 39996 + "*/"
    result = await tools.script_apply_edits(
        AsyncMock(),
        "Foo",
        "Assets",
        [
            {
                "op": op,
                "anchor": "}",
                "replacement": "",
                "replacementBase64": base64.b64encode(payload.encode()).decode("ascii"),
            },
            {"op": "prepend", "text": "//" + "y" * 39998},
        ],
        {"mixed_order": "request", "preview": True},
    )
    if op == "anchor_insert":
        assert result["success"] is False and result["code"] == "payload_too_large", result
        reader.assert_not_awaited()
        tools.get_unity_instance_from_context.assert_not_awaited()
    else:
        # Native anchor_replace selects the explicit empty replacement, so only
        # the 40 KiB prepend is charged and the read-only old-peer probe is reached.
        assert result == {"success": False, "error": "Unknown action: preview_script_buffer"}
        assert reader.await_count == 1
        assert reader.await_args.args[3]["action"] == "preview_script_buffer"
    writer.assert_not_awaited()
