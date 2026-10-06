"""Compare one synthetic credential using the actual production Windows reader."""
import hmac
import importlib.util
import os
import sys


def main() -> int:
    if os.name != "nt" or len(sys.argv) != 2:
        return 1
    # Mono's redirected StreamWriter may emit a UTF-8 BOM. Decode the owned pipe
    # explicitly instead of relying on the redirected Windows stdio code page.
    generation = sys.stdin.buffer.readline().decode("utf-8-sig").rstrip("\n\r")
    expected = sys.stdin.buffer.readline().decode("utf-8-sig").rstrip("\n\r")
    if len(generation) != 32 or len(expected) != 64:
        print("FAIL: owned channel framing")
        return 1
    spec = importlib.util.spec_from_file_location("owned_production_stdio_credentials", sys.argv[1])
    if spec is None or spec.loader is None:
        return 1
    production = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(production)
    actual_native_reader = production._read_windows_token
    native_calls = 0

    def only_owned_generation(candidate: str) -> str | None:
        nonlocal native_calls
        # Invalid greetings must be rejected by the production public entry point
        # before this native boundary, which forwards unchanged for the owned entry.
        if candidate != generation:
            raise RuntimeError("Unexpected native target")
        native_calls += 1
        return actual_native_reader(candidate)

    production._read_windows_token = only_owned_generation
    malformed = ("", generation[:-1], generation + "0", "A" * 32, "G" * 32,
                 "../" + generation, "MCPForUnity.Stdio:" + generation,
                 "MCPForUnity.AssetGen:" + generation)
    for candidate in malformed:
        if production.read_stdio_token(candidate) is not None or native_calls != 0:
            print("FAIL: malformed generation rejection")
            return 1
    actual = production.read_stdio_token(generation)
    matched = isinstance(actual, str) and hmac.compare_digest(actual, expected) and native_calls == 1
    actual = expected = None
    if not matched:
        print("FAIL: production native credential match")
        return 1
    print("PASS: production Python reader matched; malformed generations rejected before native access")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Never report exception locals or credential content to the parent/logs.
        print("FAIL: owned reader exception " + type(error).__name__)
        sys.exit(1)
