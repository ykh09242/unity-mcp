# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# Run: uv run --directory Server --no-sync python ../tools/tests/fixtures/large_result_codec/measure.py --output ../reports/CS-20261006-mcp-usability/phase7/data/baseline.json
"""Measure actual C# writer frames and Python assembly using synthetic payloads."""

import argparse
import asyncio
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "Server/src"))
from transport.large_result_assembler import LargeResultAssembler
from models.response_limits import bounded_json_text, response_size

ASSEMBLER = LargeResultAssembler
BASELINE = False


def build(work, baseline_ref=None):
    dotnet = shutil.which("dotnet")
    listing = subprocess.check_output([dotnet, "--list-sdks"], text=True).splitlines()
    version, parent = listing[-1].split(" [", 1)
    compiler = Path(parent.rstrip("]")) / version / "Roslyn/bincore/csc.dll"
    refs = Path(dotnet).resolve().parent / "packs/Microsoft.NETCore.App.Ref"
    latest = max(refs.iterdir(), key=lambda p: tuple(map(int, p.name.split("."))))
    target = next((latest / "ref").iterdir())
    dll = work / "CodecHarness.dll"
    sources = [
        ROOT / "MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs",
        Path(__file__).with_name("CodecHarness.cs"),
    ]
    if baseline_ref:
        saved = work / "BaselineLargeResultWriter.cs"
        saved.write_bytes(
            subprocess.check_output(
                [
                    "git",
                    "show",
                    f"{baseline_ref}:MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs",
                ],
                cwd=ROOT,
            )
        )
        sources[0] = saved
    args = [
        dotnet,
        str(compiler),
        "/nologo",
        "/noconfig",
        "/nostdlib+",
        "/target:exe",
        "/langversion:latest",
        f"/out:{dll}",
    ]
    args.extend(f"/reference:{ref}" for ref in target.glob("*.dll"))
    if not baseline_ref:
        args.append("/define:CURRENT_CODEC")
    subprocess.run([*args, *map(str, sources)], check=True, capture_output=True, text=True)
    dll.with_suffix(".runtimeconfig.json").write_text(
        json.dumps(
            {
                "runtimeOptions": {
                    "tfm": target.name,
                    "framework": {"name": "Microsoft.NETCore.App", "version": latest.name},
                }
            }
        ),
        encoding="utf-8",
    )
    return dotnet, dll


def read_frames(path):
    with path.open("rb") as stream:
        while header := stream.read(5):
            size = int.from_bytes(header[1:], "big")
            payload = stream.read(size)
            assert len(payload) == size
            yield header[0], payload


def run_case(work, harness, name, payload, mode="bytes"):
    source = work / f"{name}.json"
    trace = work / f"{name}.frames"
    source.write_bytes(payload)
    measured = json.loads(
        subprocess.check_output(
            [harness[0], str(harness[1]), str(source), str(trace), mode], text=True
        )
    )
    times, peaks, parse_times = [], [], []
    for _ in range(7):
        tracemalloc.start()
        callbacks = {} if BASELINE else {"reserve_compressed": lambda *_: True}
        assembler = ASSEMBLER(lambda *_: True, lambda *_: True, lambda *_: None, **callbacks)
        before = time.perf_counter()
        complete = None
        for kind, frame in read_frames(trace):
            if kind == 0:
                metadata = json.loads(frame)
                attributes = (
                    {}
                    if metadata.get("encoding", "identity") == "identity"
                    else {
                        "encoding": metadata["encoding"],
                        "decoded_bytes": metadata["decoded_bytes"],
                    }
                )
                assembler.begin(
                    "owned",
                    metadata["id"],
                    metadata["total_bytes"],
                    metadata["chunk_count"],
                    **attributes,
                )
            else:
                complete = assembler.feed("owned", frame)
        elapsed = (time.perf_counter() - before) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert complete.payload == payload
        parse_before = time.perf_counter()
        text = bounded_json_text(
            complete.payload.decode("utf-8"),
            max_bytes=32 * 1024 * 1024,
            max_depth=64,
            max_nodes=100000,
        )
        assert text is not None
        decoded = json.loads(text)
        assert response_size(decoded["result"]) is not None
        parse_times.append((time.perf_counter() - parse_before) * 1000)
        times.append(elapsed)
        peaks.append(peak)
    measured.update(
        assemble_ms=times,
        python_peak_bytes=peaks,
        normal_parse_and_charge_ms=parse_times,
        encode_median_ms=statistics.median(measured["encode_ms"]),
        assemble_median_ms=statistics.median(times),
    )
    return measured


async def wire_case(work, harness, name, payload, mode, rate):
    from websockets.asyncio.server import serve

    source = work / f"{name}-wire.json"
    trace = work / f"{name}-wire.frames"
    source.write_bytes(payload)
    done = asyncio.get_running_loop().create_future()
    assembler = LargeResultAssembler(
        lambda *_: True, lambda *_: True, lambda *_: None, reserve_compressed=lambda *_: True
    )

    async def receive(socket):
        receiver_begin = time.perf_counter()
        async for frame in socket:
            if isinstance(frame, str):
                start = json.loads(frame)
                assembler.begin(
                    "owned",
                    start["id"],
                    start["total_bytes"],
                    start["chunk_count"],
                    encoding=start.get("encoding", "identity"),
                    decoded_bytes=start.get("decoded_bytes"),
                )
            else:
                complete = assembler.feed("owned", frame)
                if complete is not None:
                    assert complete.payload == payload
                    text = bounded_json_text(
                        complete.payload.decode("utf-8"),
                        max_bytes=32 * 1024 * 1024,
                        max_depth=64,
                        max_nodes=100000,
                    )
                    assert text is not None
                    decoded = json.loads(text)
                    assert decoded["type"] == "command_result"
                    assert response_size(decoded["result"]) is not None
                    done.set_result((time.perf_counter() - receiver_begin) * 1000)
                    return

    async with serve(receive, "127.0.0.1", 0, compression=None, max_size=65536) as server:
        port = server.sockets[0].getsockname()[1]
        process = await asyncio.create_subprocess_exec(
            harness[0],
            str(harness[1]),
            str(source),
            str(trace),
            mode,
            f"ws://127.0.0.1:{port}",
            str(rate),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            receiver_ms = await asyncio.wait_for(done, 30)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 0, stderr.decode()
            measured = json.loads(stdout)
            measured["whole_receive_ms"] = receiver_ms
            return measured
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


def main():
    global ASSEMBLER, BASELINE
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--after", action="store_true")
    parser.add_argument("--wire", action="store_true")
    parser.add_argument("--baseline-ref")
    args = parser.parse_args()
    rng = random.Random(7008)
    base = {
        "type": "command_result",
        "id": "01234567-89ab-cdef-0123-456789abcdef",
        "result": {
            "success": True,
            "unicode": "\ud55c\uae00😀",
            "integer": 9007199254740993,
            "escaped_surrogate": "\\ud800",
        },
    }
    dense = json.loads(json.dumps(base))
    dense["result"]["rows"] = [
        {"id": i, "name": f"Synthetic-{i}", "value": "same-json-value" * 20} for i in range(14000)
    ]
    preview = json.loads(json.dumps(base))
    preview["result"]["preview"] = base64.b64encode(rng.randbytes(3 * 1024 * 1024)).decode("ascii")
    with tempfile.TemporaryDirectory(prefix="large-result-codec-") as temporary:
        work = Path(temporary)
        harness = build(work, args.baseline_ref)
        if args.baseline_ref:
            BASELINE = True
            saved = work / "baseline_assembler.py"
            saved.write_bytes(
                subprocess.check_output(
                    [
                        "git",
                        "show",
                        f"{args.baseline_ref}:Server/src/transport/large_result_assembler.py",
                    ],
                    cwd=ROOT,
                )
            )
            spec = importlib.util.spec_from_file_location("_codec_baseline_assembler", saved)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            ASSEMBLER = module.LargeResultAssembler
        modes = ["bytes_json", "string_json", "gzip_json"] if args.after else ["bytes_json"]
        cases = {}
        wire_results = {}
        for name, value in [("dense_json", dense), ("base64_preview", preview)]:
            payload = json.dumps(value, separators=(",", ":")).encode()
            for mode in modes:
                cases[f"{name}:{mode}"] = run_case(work, harness, f"{name}-{mode}", payload, mode)
            if args.wire:
                for rate in [512, 5120]:
                    for mode in ["string_json", "gzip_json"]:
                        wire_results[f"{name}:{mode}:{rate}KiB_s"] = asyncio.run(
                            wire_case(work, harness, name, payload, mode, rate)
                        )
    report = {
        "python": platform.python_version(),
        "baseline_ref": args.baseline_ref,
        "cases": cases,
        "owned_wire": wire_results,
        "source_sha256": {
            name: hashlib.sha256(
                subprocess.check_output(["git", "show", f"{args.baseline_ref}:{name}"], cwd=ROOT)
                if args.baseline_ref
                else (ROOT / name).read_bytes()
            ).hexdigest()
            for name in [
                "MCPForUnity/Editor/Services/Transport/LargeResultWriter.cs",
                "Server/src/transport/large_result_assembler.py",
            ]
        },
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                name: {
                    "bytes": case["bytes"],
                    "encode_median_ms": case["encode_median_ms"],
                    "assemble_median_ms": case["assemble_median_ms"],
                    "python_peak_bytes": statistics.median(case["python_peak_bytes"]),
                    "allocated_bytes": statistics.median(case["allocated_bytes"]),
                }
                for name, case in cases.items()
            }
        )
    )


if __name__ == "__main__":
    main()
