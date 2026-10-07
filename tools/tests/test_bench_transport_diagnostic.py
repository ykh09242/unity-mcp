"""Diagnostic observation must preserve delegation, ownership and natural budgets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from tools.bench_transport import Options
from tools.bench_transport_diagnostic import (
    DiagnosticError,
    Trace,
    patch,
    require_sdk,
    traced_tool,
    write_sidecar,
)
from tools.bench_transport_compare import Capture
from tools.bench_transport_compare import check_contract
from tools.bench_transport_process import native_python


def test_default_has_no_diagnostic_mode_or_wrappers(tmp_path: Path) -> None:
    options = Options(
        output=tmp_path / "x.json",
        samples=1,
        warmup=0,
        large_bytes=262144,
        work_ms=0,
        concurrency=1,
        order="stdio-first",
    )
    assert options.diagnostic is False

    async def original(correlation):
        return correlation

    assert traced_tool(None, "correlation")(original) is original


def test_tool_wrapper_preserves_value_schema_and_correlation() -> None:
    async def exercise():
        trace = Trace()
        result = {"owned": [1, 2]}
        calls = []

        async def original(correlation: str):
            calls.append(correlation)
            return result

        wrapped = traced_tool(trace, "correlation")(original)
        assert await wrapped("state:warm:0") is result
        assert calls == ["state:warm:0"]
        assert trace.export()["events"][0]["correlation"] == "state:warm:0"
        assert trace.correlation.get() == ""

    anyio.run(exercise)


def test_patch_restores_inherited_descriptor_not_bound_override() -> None:
    class Owner:
        def original(self, value):
            return value

    owner = Owner()
    function = Owner.original
    with patch(owner, "original", ("value",), Trace(), "sync", synchronous=True):
        assert owner.original(3) == 3
    assert "original" not in vars(owner)
    assert Owner.original is function


def test_partial_patch_install_failure_restores_previous_seams() -> None:
    from contextlib import ExitStack

    def original(value):
        return value

    owner = SimpleNamespace(first=original, second=original)
    with pytest.raises(DiagnosticError):
        with ExitStack() as stack:
            stack.enter_context(
                patch(owner, "first", ("value",), Trace(), "first", synchronous=True)
            )
            stack.enter_context(
                patch(owner, "second", ("changed",), Trace(), "second", synchronous=True)
            )
    assert owner.first is original
    assert owner.second is original


def test_tool_wrapper_keeps_fastmcp_context_and_output_schema() -> None:
    from fastmcp import Context
    from fastmcp.tools.function_tool import FunctionTool

    # The annotation is intentionally defined in a different module namespace.
    async def original(ctx: Context, correlation: str) -> dict[str, str]:
        return {"correlation": correlation}

    original.__annotations__ = {"ctx": Context, "correlation": str, "return": dict[str, str]}
    before = FunctionTool.from_function(original)
    after = FunctionTool.from_function(traced_tool(Trace(), "correlation")(original))
    assert before.parameters == after.parameters
    assert before.output_schema == after.output_schema


def test_sync_patch_delegates_once_and_restores_on_exception() -> None:
    calls = []
    result = []

    def original(value):
        calls.append(value)
        if value == "fail":
            raise LookupError("owned failure")
        return result

    owner = SimpleNamespace(work=original)
    trace = Trace()
    with pytest.raises(LookupError):
        with patch(owner, "work", ("value",), trace, "sync", synchronous=True):
            assert owner.work("ok") is result
            owner.work("fail")
    assert owner.work is original
    assert calls == ["ok", "fail"]
    events = trace.export()["events"]
    assert [e["completed"] for e in events] == [True, False]
    assert all(e["thread_cpu_ms"] >= 0 for e in events)


def test_async_patch_restores_after_cancellation_and_is_not_request_cpu() -> None:
    async def exercise():
        calls = []

        async def original(value):
            calls.append(value)
            await anyio.sleep_forever()

        owner = SimpleNamespace(work=original)
        trace = Trace()
        with patch(owner, "work", ("value",), trace, "async"):
            with anyio.CancelScope() as scope:
                scope.cancel()
                await owner.work("owned")
        assert owner.work is original
        assert calls == ["owned"]
        event = trace.export()["events"][0]
        assert event["completed"] is False
        assert "thread_cpu_ms" not in event

    anyio.run(exercise)


def test_mismatched_or_stacked_seam_never_replaces_owner() -> None:
    def original(value):
        return value

    owner = SimpleNamespace(work=original)
    trace = Trace()
    with pytest.raises(DiagnosticError, match="signature"):
        with patch(owner, "work", ("changed",), trace, "sync", synchronous=True):
            pytest.fail("entered incompatible seam")
    assert owner.work is original
    with patch(owner, "work", ("value",), trace, "sync", synchronous=True):
        with pytest.raises(DiagnosticError, match="stack"):
            with patch(owner, "work", ("value",), trace, "sync", synchronous=True):
                pytest.fail("stacked diagnostic wrapper")
    assert owner.work is original


def test_overflow_is_bounded_and_does_not_mask_delegate_failure() -> None:
    trace = Trace(max_events=1)
    with trace.span("first"):
        pass
    with pytest.raises(LookupError, match="original"):
        with trace.span("overflow"):
            raise LookupError("original")
    snapshot = trace.export()
    assert len(snapshot["events"]) == 1
    assert snapshot["dropped_events"] == 1
    assert snapshot["complete"] is False
    assert len(json.dumps(snapshot)) < 4096
    with pytest.raises(DiagnosticError, match="overflow"):
        trace.check()


def test_sdk_version_mismatch_fails_before_patching(monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.bench_transport_diagnostic.importlib.metadata.version", lambda _: "unsupported"
    )
    with pytest.raises(DiagnosticError, match="SDK"):
        require_sdk()


def test_sidecar_byte_overflow_preserves_bounded_explicit_failure(tmp_path: Path) -> None:
    path = tmp_path / "sidecar.json"
    assert write_sidecar(path, {"data_kind": "diagnostic", "events": ["x" * 2048]}, 1024) is False
    value = json.loads(path.read_text())
    assert value["complete"] is False
    assert value["error"] == "diagnostic_byte_capacity"
    assert path.stat().st_size <= 1024


@pytest.mark.parametrize("marker", ["kind", "flag"])
def test_natural_comparator_rejects_diagnostic_before_counts(marker: str) -> None:
    # A deliberately incomplete capture must reject the marker at parsing, not
    # silently discard it and later consider diagnostic rows natural evidence.
    raw = {"data_kind": "diagnostic"} if marker == "kind" else {"options": {"diagnostic": True}}
    with pytest.raises(ValueError) as error:
        Capture.model_validate(raw)
    assert (
        marker == "kind"
        and "data_kind" in str(error.value)
        or marker == "flag"
        and "diagnostic" in str(error.value)
    )


def test_owned_diagnostic_parity_and_cleanup_against_default(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    reports = []
    for enabled in (False, True):
        output = tmp_path / ("diagnostic.json" if enabled else "natural.json")
        arguments = [
            str(root / "tools/bench_transport.py"),
            "--output",
            str(output),
            "--samples",
            "2",
            "--warmup",
            "0",
            "--large-bytes",
            "262144",
            "--work-ms",
            "0",
            "--concurrency",
            "2",
            "--cohort-gate",
            "--resource-contract",
        ]
        if enabled:
            arguments.append("--diagnostic")
        completed = subprocess.run(
            native_python(arguments, (root,)),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        reports.append(json.loads(output.read_text(encoding="utf-8")))
    assert len(reports) == 2
    natural, diagnostic = reports[0], reports[1]
    assert "diagnostic" not in natural["options"]
    assert "diagnostic_sidecar" not in natural
    check_contract(Capture.model_validate(natural))
    with pytest.raises(ValueError):
        Capture.model_validate(diagnostic)
    pointer = diagnostic["diagnostic_sidecar"]
    sidecar_path = Path(pointer["path"])
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert sidecar["data_kind"] == "diagnostic"
    assert sidecar["source"] == diagnostic["source"]
    assert sidecar_path.stat().st_size < 4 * 1024 * 1024
    for before, after, mode in zip(
        natural["results"], diagnostic["results"], sidecar["modes"], strict=True
    ):
        for workload in ("small", "state", "large", "job"):
            assert (
                before["warmed"][workload]["output_sha256"]
                == after["warmed"][workload]["output_sha256"]
            )
        assert mode["diagnostic_control_rpc_count"] == 8
        assert mode["normal_tools_call_count"] == before["client_rpc_counts"]["tools/call"]
        assert (
            after["client_rpc_counts"]["public_tools/call"]
            == before["client_rpc_counts"]["public_tools/call"]
        )
        assert (
            after["lifecycle"]["after_reconnect"]["commands"]
            == before["lifecycle"]["after_reconnect"]["commands"]
        )
        public = {"read_console", "get_editor_state", "get_test_job"}
        assert [s for s in after["tool_schemas"] if s["name"] in public] == [
            s for s in before["tool_schemas"] if s["name"] in public
        ]
        for process in ("client", "child"):
            assert mode[process]["complete"] is True
            assert 0 < len(mode[process]["events"]) <= mode[process]["max_events"]
            assert all(s["source_sha256"] for s in mode[process]["seams"])
        assert {"client_sdk_request", "client_dispatch_wait"} <= {
            e["name"] for e in mode["client"]["events"]
        }
        expected_boundaries = {"fixture_tool", "mcp_envelope_validation"}
        expected_boundaries |= (
            {"hub_command_result", "hub_large_result"}
            if after["mode"] == "http"
            else {"stdio_handoff", "stdio_write_flush"}
        )
        assert expected_boundaries <= {e["name"] for e in mode["child"]["events"]}
        for event in mode["child"]["events"]:
            assert ("thread_cpu_ms" in event) == (
                event["name"] in {"mcp_envelope_validation", "stdio_write_flush"}
            )
        assert len(mode["cpu_batches"]) == 4
        assert all(b["child"]["peer_completed_and_accounting_drained"] for b in mode["cpu_batches"])
        assert mode["child"]["events"]
        if after["mode"] == "http":
            partial = after["lifecycle"]["partial_cancel"]
            assert partial["receiver_final_chunk_processed"] is True
            assert partial["held"]["assembler_buffer_bytes"] > 0
            assert all(
                partial["after_late_chunks"]["accounting"].get(k, 0) == 0
                for k in ("hub_reserved_bytes", "assembler_buffer_bytes", "pending_commands")
            )
        else:
            clean = after["lifecycle"]["after_reconnect"]["accounting"]
            assert clean["stdio_delivery_observer_available"] is True
            assert clean["charged_stdio_deliveries"] == 0
