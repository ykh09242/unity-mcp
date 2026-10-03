"""Registered SDK page validation with isolated inert editor boundaries."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import subprocess
import sys


def cases():
    rows = []
    for tool, action in (("manage_asset", "search"), ("manage_ui", "list"), ("manage_physics", "validate")):
        base = {"action": action, **({"path": "Assets"} if tool == "manage_asset" else {})}
        for size in (0, 1, 1000, 1001, 2147483647):
            rows.append((tool, {**base, "page_size": size}, 1 <= size <= 1000))
        position = "cursor" if tool == "manage_physics" else "page_number"
        for value in (-1, 0, 1, 2147483647, 2147483648):
            rows.append((tool, {**base, position: value}, (0 if position == "cursor" else 1) <= value <= 2147483647))
        rows.append((tool, base, True))
    for size in (0, 1, 32, 33, 2147483647):
        rows.append(("manage_asset", {"action": "search", "path": "Assets", "generate_preview": True, "page_size": size}, 1 <= size <= 32))
    rows.append(("manage_asset", {"action": "search", "path": "Assets", "generate_preview": True}, True))
    rows.append(("manage_asset", {"action": "search", "path": "Assets", "page_size": "1000", "page_number": "1"}, True))
    return rows


async def exercise(owned: Path):
    from fastmcp import Client, FastMCP
    from services.tools import register_all_tools

    modules = {name: importlib.import_module("services.tools." + name) for name in ("manage_asset", "manage_ui", "manage_physics")}
    events = []
    sent = []

    async def resolve(ctx):
        events.append("resolve")
        return None

    async def preflight(*args, **kwargs):
        events.append("preflight")
        return None

    async def send(fn, instance, command, params, **kwargs):
        events.append("send")
        sent.append(params.copy())
        return {"success": True, "data": {"assets": [], "warnings": []}}

    for module in modules.values():
        module.get_unity_instance_from_context = resolve
        module.send_with_unity_instance = send
    modules["manage_asset"].preflight = preflight
    server = FastMCP("pagination-security-fixture")
    register_all_tools(server)
    results = []
    for mode in ("auto", "legacy"):
        async with Client(server, mode=mode) as client:
            for tool, params, allowed in cases():
                events.clear()
                sent.clear()
                result = await client.call_tool(tool, params)
                content = result.structured_content
                passed = content.get("success") is allowed and ("send" in events) is allowed
                if not allowed:
                    passed = passed and events == []
                if allowed:
                    maximum = 32 if params.get("generate_preview") else 1000
                    actual = sent[0].get("pageSize", sent[0].get("page_size"))
                    passed = passed and isinstance(actual, int) and 1 <= actual <= maximum
                results.append({"mode": mode, "tool": tool, "params": params, "allowed": allowed, "passed": passed, "events": events.copy()})

    # Direct Python callers bypass the SDK's Pydantic input coercion.
    for tool, action in (("manage_asset", "search"), ("manage_ui", "list"), ("manage_physics", "validate")):
        for value in (True, "bad", 1.5, "NaN", "1e500", 2147483648):
            events.clear()
            params = {"action": action, "page_size": value}
            if tool == "manage_asset": params["path"] = "Assets"
            result = await getattr(modules[tool], tool)(None, **params)
            results.append({"mode": "direct", "tool": tool, "value": value, "passed": result.get("success") is False and events == [], "events": events.copy()})
    report = {"checks": len(results), "results": results, "failures": [row for row in results if not row["passed"]]}
    (owned / "sdk-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"checks": len(results), "failures": len(report["failures"])}))
    assert not report["failures"], json.dumps(report["failures"][:5])


def test_pages_at_registered_sdk(tmp_path: Path):
    result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--sdk-child", str(tmp_path / "paging")],
                            capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    import runpy
    owned = Path(sys.argv[2]).resolve()
    # Reuse the audited Windows stdlib socketpair exception and ownership setup.
    safety = runpy.run_path(str(Path(__file__).with_name("test_render_capture_limits.py")))
    safety["_own_child_environment"](owned)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    import anyio
    anyio.run(exercise, owned)
