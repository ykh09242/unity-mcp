"""Selection, safe defaults and pinned-tool contracts for the formatting runner."""

from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import format_scripts as formatting  # noqa: E402


@pytest.mark.parametrize(
    "path",
    [
        "mcp_source.py",
        ".github/scripts/check.py",
        "CustomTools/RoslynRuntimeCompilation/Tool.cs",
        "MCPForUnity/Editor/Tools/Build/ManageBuild.cs",
        "MCPForUnity/Runtime/Transport.cs",
        "Server/tests/e2e/test_connection.py",
        "TestProjects/UnityMCPTests/Assets/Tests/Editor/ToolTests.cs",
        "TestProjects/UnityMCPTests/Assets/Scripts/Player.cs",
        "tools/experiments/transports/Transport.cs",
        "tools/tests/fixtures/Script.cs",
        "website/src/pages/index.js",
        "website/scripts/check.mjs",
    ],
)
def test_authored_scope_includes_scripts_tests_and_fixtures(path):
    assert formatting.is_authored_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/outside.py",
        ".tmp/format-first/tools/script.py",
        "tools/.env.secret.py",
        "tools/vendor/dependency.py",
        "tools/external/dependency.cs",
        "tools/generated/output.py",
        "Server/.venv/module.py",
        "tools/cache_pb2.py",
        "MCPForUnity/Editor/Generated.g.cs",
        "TestProjects/UnityMCPTests/Library/Cache.cs",
        "TestProjects/UnityMCPTests/Assets/Resources/GameData/Secret.cs",
        "TestProjects/AssetStoreUploads/Assets/External.cs",
        "website/package-lock.json",
        "website/docs/reference/tools/example.py",
        "MCPForUnity/Editor/Tool.cs.meta",
    ],
)
def test_non_source_or_prohibited_paths_are_rejected(path):
    assert not formatting.is_authored_path(path)


def make_file(root, relative, content="value = 1\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    return path


def test_default_selection_combines_changed_and_new_without_reading_excluded(tmp_path, monkeypatch):
    changed = make_file(tmp_path, "tools/changed.py")
    new = make_file(tmp_path, "tools/new.py")
    make_file(tmp_path, "tools/unchanged.py")
    candidates = {"tools/changed.py", "tools/new.py", "tools/unchanged.py", "tools/.env.py"}

    def git_paths(root, arguments):
        assert root == tmp_path
        if arguments[0] == "diff":
            assert "HEAD" in arguments
            return {"tools/changed.py"}
        return set(candidates) if "--cached" in arguments else {"tools/new.py", "tools/.env.py"}

    monkeypatch.setattr(formatting, "git_paths", git_paths)
    assert formatting.discover_files(tmp_path, False, ()) == (changed, new)
    assert formatting.discover_files(tmp_path, True, ()) == (
        changed,
        new,
        tmp_path / "tools/unchanged.py",
    )


def test_explicit_selection_rejects_ignored_and_outside_paths(tmp_path, monkeypatch):
    target = make_file(tmp_path, "tools/with spaces.py")
    monkeypatch.setattr(formatting, "git_paths", lambda *_: {"tools/with spaces.py"})
    assert formatting.discover_files(tmp_path, False, ["tools/with spaces.py"]) == (target,)
    with pytest.raises(ValueError, match="eligible"):
        formatting.discover_files(tmp_path, False, ["tools/ignored.py"])
    with pytest.raises(ValueError, match="inside"):
        formatting.discover_files(tmp_path, False, [str(tmp_path.parent / "outside.py")])


@pytest.mark.parametrize("write", [False, True])
def test_check_is_default_and_write_is_explicit(tmp_path, monkeypatch, write):
    python_file = make_file(tmp_path, "tools/file.py")
    csharp_file = make_file(tmp_path, "tools/File.cs")
    shell_content = "cat <<'EOF'\n    intentional payload  \nEOF\n"
    shell_file = make_file(tmp_path, "tools/file.sh", shell_content)
    monkeypatch.setattr(formatting, "ROOT", tmp_path)
    monkeypatch.setattr(
        formatting, "discover_files", lambda *_: (python_file, csharp_file, shell_file)
    )
    monkeypatch.setattr(formatting, "ruff_command", lambda _: ("ruff",))
    monkeypatch.setattr(formatting, "csharpier_command", lambda _: ("csharpier",))
    calls = []
    monkeypatch.setattr(
        formatting, "run_batches", lambda root, command, files: calls.append((command, files)) or 0
    )
    csharp_calls = []
    monkeypatch.setattr(
        formatting,
        "format_csharp_files",
        lambda root, files, write, command: csharp_calls.append((files, write, command)) or 0,
    )
    assert formatting.main(["--write"] if write else []) == 0
    assert ("--check" in calls[0][0]) is not write
    assert calls[0][1] == (python_file,)
    assert csharp_calls == [((csharp_file,), write, ("csharpier",))]
    assert shell_file.read_text(encoding="utf-8") == shell_content


def test_setup_failure_precedes_any_write(tmp_path, monkeypatch):
    files = (make_file(tmp_path, "tools/file.py"), make_file(tmp_path, "tools/File.cs"))
    monkeypatch.setattr(formatting, "ROOT", tmp_path)
    monkeypatch.setattr(formatting, "discover_files", lambda *_: files)
    monkeypatch.setattr(formatting, "ruff_command", lambda _: ("ruff",))

    def unavailable(_):
        raise RuntimeError("CSharpier unavailable")

    monkeypatch.setattr(formatting, "csharpier_command", unavailable)
    monkeypatch.setattr(formatting, "run_batches", lambda *_: pytest.fail("unexpected write"))
    assert formatting.main(["--write"]) == 2


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("ruff 0.16.10\n", True),
        ("0.16.10\n", True),
        ("ruff 0.16.9\n", False),
        ("10.16.10\n", False),
    ],
)
def test_version_verification_is_exact(tmp_path, monkeypatch, output, expected):
    monkeypatch.setattr(
        formatting.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, output, ""),
    )
    assert (formatting.verified_command(tmp_path, ("tool",), "0.16.10") is not None) is expected


def test_batched_execution_preserves_file_arguments_and_signal_failure(tmp_path, monkeypatch):
    files = tuple(tmp_path / (str(index) + " " + "x" * 5000 + ".py") for index in range(8))
    calls = []

    def run(arguments, **kwargs):
        assert kwargs["cwd"] == tmp_path
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, -15 if len(calls) == 1 else 0)

    monkeypatch.setattr(formatting.subprocess, "run", run)
    assert formatting.run_batches(tmp_path, ("ruff", "format"), files) == 1
    assert len(calls) > 1
    assert [argument for call in calls for argument in call[2:]] == [str(path) for path in files]


def test_list_mode_invokes_no_formatter(tmp_path, monkeypatch, capsys):
    file = make_file(tmp_path, "tools/file.py")
    monkeypatch.setattr(formatting, "ROOT", tmp_path)
    monkeypatch.setattr(formatting, "discover_files", lambda *_: (file,))
    monkeypatch.setattr(formatting, "ruff_command", lambda *_: pytest.fail("unexpected formatter"))
    assert formatting.main(["--list"]) == 0
    assert capsys.readouterr().out == "tools/file.py\n"


PLAIN_EXPANDED = b"catch\n{\n    /* User assembly not always needed */\n}\n"
PLAIN_COMPACT = b"catch { /* User assembly not always needed */ }\n"
TYPED_EXPANDED = b"catch (Exception error)\n{\n    /* Expected optional assembly */\n}\n"
TYPED_COMPACT = b"catch (Exception error) { /* Expected optional assembly */ }\n"


def fake_csharpier(monkeypatch):
    calls = []

    def run(root, command, files):
        assert "format" in command
        assert "--include-generated" in command
        assert "--no-cache" in command
        assert "--ignore-path" in command
        assert command[command.index("--config-path") + 1] == str(root / ".editorconfig")
        calls.append(tuple(files))
        for file in files:
            # Model CSharpier's syntax formatting while keeping raw-string content literal.
            parts = file.read_bytes().split(b'"""')
            for index in range(0, len(parts), 2):
                parts[index] = (
                    parts[index]
                    .replace(PLAIN_COMPACT, PLAIN_EXPANDED)
                    .replace(TYPED_COMPACT, TYPED_EXPANDED)
                )
            file.write_bytes(b'"""'.join(parts))
        return 0

    monkeypatch.setattr(formatting, "run_batches", run)
    return calls


@pytest.mark.parametrize(
    ("expanded", "compact"),
    [(PLAIN_EXPANDED, PLAIN_COMPACT), (TYPED_EXPANDED, TYPED_COMPACT)],
)
def test_csharp_catch_roundtrip_and_idempotent_check(tmp_path, monkeypatch, expanded, compact):
    file = make_file(tmp_path, "tools/File.cs", expanded.decode())
    calls = fake_csharpier(monkeypatch)
    assert formatting.format_csharp_files(tmp_path, (file,), command=("fake",)) == 1
    assert file.read_bytes() == expanded
    assert formatting.format_csharp_files(tmp_path, (file,), True, ("fake",)) == 0
    assert file.read_bytes() == compact
    assert formatting.format_csharp_files(tmp_path, (file,), command=("fake",)) == 0
    assert file.read_bytes() == compact
    assert len(calls) == 6


@pytest.mark.parametrize(
    "source",
    [
        b"catch\n{\n    Work();\n    OtherWork();\n}\n",
        b"catch\n{\n    /* First */\n    /* Second */\n}\n",
        b"catch\n{\n    /* First\n       second line */\n}\n",
        b"catch (Exception error) when (error != null)\n{\n    /* Filter */\n}\n",
        b"catch\n{\n    /* " + b"x" * 160 + b" */\n}\n",
    ],
)
def test_noncompact_csharp_blocks_remain_unchanged(source):
    assert formatting.compact_catch_candidates(source) == source


def test_raw_string_false_match_is_rejected_by_roundtrip(tmp_path, monkeypatch, capsys):
    source = b'const string Value = """\n' + PLAIN_EXPANDED + b'""";\n'
    file = make_file(tmp_path, "tools/File.cs", source.decode())
    fake_csharpier(monkeypatch)
    assert formatting.compact_catch_candidates(source) != source
    assert formatting.format_csharp_files(tmp_path, (file,), True, ("fake",)) == 0
    assert file.read_bytes() == source
    assert "unsafe catch candidate" in capsys.readouterr().out


def test_csharp_concurrent_edit_is_not_overwritten(tmp_path, monkeypatch):
    file = make_file(tmp_path, "tools/File.cs", PLAIN_EXPANDED.decode())
    fake_run = fake_csharpier(monkeypatch)
    original_run = formatting.run_batches

    def concurrent_run(root, command, files):
        result = original_run(root, command, files)
        if len(fake_run) == 2:
            file.write_bytes(b"// Another contributor's edit\n")
        return result

    monkeypatch.setattr(formatting, "run_batches", concurrent_run)
    with pytest.raises(RuntimeError, match="changed while formatting"):
        formatting.format_csharp_files(tmp_path, (file,), True, ("fake",))
    assert file.read_bytes() == b"// Another contributor's edit\n"
