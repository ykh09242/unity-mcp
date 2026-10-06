# Large result codec fixture

These tests compile the actual C# transport writer with installed Roslyn/.NET SDK reference assemblies and reconstruct its messages with the production Python assembler. They use synthetic JSON and ephemeral temporary files. `measure.py --wire` additionally creates an OS-assigned loopback WebSocket server and an owned C# ClientWebSocket, with compression at the WebSocket layer disabled and controlled per-frame pacing. It contacts no Unity Editor or application server.

Run from the repository root, using the repository's installed Python environment:

```powershell
$env:UNITY_MCP_RUN_HTTP_TESTS = '1'
uv run --directory Server --no-sync pytest tests/test_large_result_assembler.py tests/test_large_result_gzip.py tests/http/test_plugin_result_gzip.py -q
uv run --directory Server --no-sync python ../tools/tests/fixtures/large_result_codec/measure.py --baseline-ref f053700a --output ../reports/CS-20261006-mcp-usability/phase7/data/baseline-same-payload.json
uv run --directory Server --no-sync python ../tools/tests/fixtures/large_result_codec/measure.py --after --wire --output ../reports/CS-20261006-mcp-usability/phase7/data/final-wire.json
```

If the default uv cache is inaccessible, pass `uv --cache-dir "$env:TEMP/unity-mcp-uv-large-result"` as used in the recorded checks. The script uses the installed .NET SDK and existing Python dependencies; it does not install packages or download an SDK. Python tests requiring C# must run where a .NET SDK is available. Only the optional real-socket test needs the already-installed `websockets` package.

The baseline option reads the selected historical C# and Python source with `git show` into an ephemeral directory and compiles/imports those exact files. It never changes the Git checkout. Both measurements use the same deterministic payloads, including Unicode, large integers and literal escaped text. `bytes_json` measures the prior full `Encoding.UTF8.GetBytes` allocation; `string_json` measures incremental UTF-8; `gzip_json` additionally opts into negotiated gzip.

Reported C# allocations exclude the pre-existing response JSON string and file input fixture; they include the writer's full UTF-8 array on the old path, framing/staging buffers and compressed segments. Python peaks cover frame reading and assembly under tracemalloc, and aren't a claim of total OS process memory. `owned_wire_ms` measures the actual writer's encoding, pacing and frame sends after connection establishment. `whole_receive_ms` measures from the post-handshake receiver handler start to reconstructed bytes, strict UTF-8 decoding, bounded JSON validation/parsing and normal result-size accounting. It excludes connection establishment and Unity command execution. `normal_parse_and_charge_ms` separates that unchanged receiver CPU work in the repeated codec samples. OS timer quantization means reported paced rates are targets; compare measured wall times rather than deriving exact network bandwidth.
