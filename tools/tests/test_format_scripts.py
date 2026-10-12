"""Selection, safe defaults and pinned-tool contracts for the formatting runner."""

import json
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


@pytest.mark.parametrize("relative", ["tools/file.py", "tools/feature_program_web/src/App.tsx"])
def test_list_mode_invokes_no_formatter(tmp_path, monkeypatch, capsys, relative):
    file = make_file(tmp_path, relative)
    monkeypatch.setattr(formatting, "ROOT", tmp_path)
    monkeypatch.setattr(formatting, "discover_files", lambda *_: (file,))
    monkeypatch.setattr(formatting, "ruff_command", lambda *_: pytest.fail("unexpected formatter"))
    assert formatting.main(["--list"]) == 0
    assert capsys.readouterr().out == relative + "\n"


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


@pytest.fixture
def frontend_package(tmp_path, monkeypatch):
    package = tmp_path / formatting.FRONTEND_ROOT
    version = formatting.PRETTIER_VERSION
    make_file(package, "package.json", json.dumps({"devDependencies": {"prettier": version}}))
    make_file(
        package,
        "package-lock.json",
        json.dumps(
            {
                "packages": {
                    "": {"devDependencies": {"prettier": version}},
                    "node_modules/prettier": {"version": version},
                }
            }
        ),
    )
    make_file(package, ".prettierrc.json", '{"singleQuote": true, "semi": true}\n')
    make_file(package, ".prettierignore", "generated/\nnode_modules/\npackage-lock.json\n")
    make_file(package, "node_modules/prettier/bin/prettier.cjs", "// Synthetic CLI fixture\n")
    monkeypatch.setattr(formatting.shutil, "which", lambda _: "node executable with spaces")
    return package


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("exit_code", [0, 1, 2, -15])
def test_frontend_selection_paths_modes_and_failure_propagation(
    tmp_path, monkeypatch, frontend_package, write, exit_code
):
    target = make_file(frontend_package, "src/component with spaces.tsx", "const value=1;\n")
    manual = make_file(tmp_path, "website/src/manual.js", "const manual=1;\n")
    package_shell = make_file(frontend_package, "scripts/manual.ps1", "$value = 1\n")
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if "--version" in arguments:
            assert kwargs["cwd"] == frontend_package
            return subprocess.CompletedProcess(arguments, 0, formatting.PRETTIER_VERSION + "\n", "")
        assert kwargs["cwd"] == tmp_path
        assert arguments[0] == "node executable with spaces"
        assert arguments[1] == str(frontend_package / "node_modules/prettier/bin/prettier.cjs")
        assert arguments[arguments.index("--config") + 1] == str(
            frontend_package / ".prettierrc.json"
        )
        assert arguments[arguments.index("--ignore-path") + 1] == str(
            frontend_package / ".prettierignore"
        )
        assert arguments[-3:] == ["--write" if write else "--check", "--", str(target)]
        if write and exit_code == 0:
            target.write_text("const value = 1;\n", encoding="utf-8")
        return subprocess.CompletedProcess(arguments, exit_code)

    monkeypatch.setattr(formatting, "ROOT", tmp_path)
    monkeypatch.setattr(formatting, "discover_files", lambda *_: (target, manual, package_shell))
    monkeypatch.setattr(formatting.subprocess, "run", run)
    assert formatting.main(["--write"] if write else ["--check"]) == max(
        exit_code, 1 if exit_code < 0 else 0
    )
    assert len(calls) == 2
    assert target.read_text(encoding="utf-8") == (
        "const value = 1;\n" if write and exit_code == 0 else "const value=1;\n"
    )
    assert manual.read_text(encoding="utf-8") == "const manual=1;\n"
    assert package_shell.read_text(encoding="utf-8") == "$value = 1\n"


@pytest.mark.parametrize(
    "fault",
    ["node", "cli", "config", "ignore", "manifest", "lock", "lock-root", "version", "timeout"],
)
def test_frontend_setup_failure_precedes_all_selected_writes(
    tmp_path, monkeypatch, frontend_package, fault, capsys
):
    target = make_file(frontend_package, "src/App.tsx", "const value=1;\n")
    python = make_file(tmp_path, "tools/file.py", "value=1\n")
    csharp = make_file(tmp_path, "tools/File.cs", "class File{}\n")
    originals = {path: path.read_bytes() for path in (target, python, csharp)}
    if fault == "node":
        monkeypatch.setattr(formatting.shutil, "which", lambda _: None)
    elif fault == "cli":
        (frontend_package / "node_modules/prettier/bin/prettier.cjs").unlink()
    elif fault == "config":
        (frontend_package / ".prettierrc.json").write_text("invalid json", encoding="utf-8")
    elif fault == "ignore":
        (frontend_package / ".prettierignore").unlink()
    elif fault == "manifest":
        (frontend_package / "package.json").write_text(
            '{"devDependencies": {"prettier": "^3.9.10"}}', encoding="utf-8"
        )
    elif fault in {"lock", "lock-root"}:
        lock_path = frontend_package / "package-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if fault == "lock":
            lock["packages"]["node_modules/prettier"]["version"] = "3.9.9"
        else:
            lock["packages"][""]["devDependencies"]["prettier"] = "3.9.9"
        lock_path.write_text(json.dumps(lock), encoding="utf-8")

    def run(arguments, **_kwargs):
        assert "--version" in arguments
        if fault == "timeout":
            raise subprocess.TimeoutExpired(arguments, 30)
        output = "3.9.9" if fault == "version" else formatting.PRETTIER_VERSION
        return subprocess.CompletedProcess(arguments, 0, output + "\n", "")

    monkeypatch.setattr(formatting, "ROOT", tmp_path)
    monkeypatch.setattr(formatting, "discover_files", lambda *_: (python, csharp, target))
    monkeypatch.setattr(formatting, "ruff_command", lambda _: ("ruff",))
    monkeypatch.setattr(formatting, "csharpier_command", lambda _: ("csharpier",))
    monkeypatch.setattr(formatting.subprocess, "run", run)
    monkeypatch.setattr(
        formatting, "run_batches", lambda *_: pytest.fail("unexpected format write")
    )
    monkeypatch.setattr(
        formatting, "format_csharp_files", lambda *_: pytest.fail("unexpected C# write")
    )
    assert formatting.main(["--write"]) == 2
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert "npm ci in tools/feature_program_web/" in capsys.readouterr().err


@pytest.mark.parametrize(
    "relative",
    [
        "generated/reference.ts",
        "node_modules/module/index.js",
        "package-lock.json",
        "captures/snapshot.json",
        "evidence/report.json",
        "test-results/report.json",
        "playwright-report/report.json",
        "coverage/coverage.json",
        ".env.secret.json",
    ],
)
def test_frontend_exclusions_precede_source_reads(tmp_path, monkeypatch, relative):
    source = make_file(tmp_path, "tools/feature_program_web/src/App.tsx", "export {};\n")
    excluded = formatting.FRONTEND_ROOT + relative
    monkeypatch.setattr(
        formatting, "git_paths", lambda *_: {excluded, source.relative_to(tmp_path).as_posix()}
    )
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("unexpected source read"))
    monkeypatch.setattr(
        Path, "read_text", lambda *_args, **_kwargs: pytest.fail("unexpected source read")
    )
    assert formatting.discover_files(tmp_path, True, ()) == (source,)
    with pytest.raises(ValueError, match="eligible"):
        formatting.discover_files(tmp_path, False, [excluded])


def test_frontend_linked_ancestor_is_rejected(tmp_path, monkeypatch):
    source = make_file(tmp_path, "tools/feature_program_web/src/App.tsx", "export {};\n")
    monkeypatch.setattr(
        formatting, "git_paths", lambda *_: {source.relative_to(tmp_path).as_posix()}
    )
    resolve = Path.resolve
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path: tmp_path.parent / "external.tsx" if path == source else resolve(path),
    )
    assert formatting.discover_files(tmp_path, True, ()) == ()
    with pytest.raises(ValueError, match="without links"):
        formatting.discover_files(tmp_path, False, [str(source)])


@pytest.mark.parametrize(
    ("returncode", "output"),
    [(0, "3.9.10+modified\n"), (0, "prettier 3.9.10\n"), (1, "3.9.10\n")],
)
def test_prettier_installed_version_requires_exact_success(
    tmp_path, monkeypatch, frontend_package, returncode, output
):
    monkeypatch.setattr(
        formatting.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode, output, ""),
    )
    with pytest.raises(RuntimeError, match="npm ci"):
        formatting.prettier_command(tmp_path)


def test_prettier_setup_rejects_linked_configuration_before_reading(
    tmp_path, monkeypatch, frontend_package
):
    config = frontend_package / ".prettierrc.json"
    is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == config or is_symlink(path))
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("unexpected read"))
    with pytest.raises(RuntimeError, match="npm ci"):
        formatting.prettier_command(tmp_path)


@pytest.mark.parametrize(
    "private",
    [
        ".work/programs/private.py",
        ".work/handoffs/private.json",
        "tools/.work/programs/private.py",
        "tools/feature_program_web/.WORK/handoffs/private.tsx",
        ".omo/programs/legacy.py",
    ],
)
def test_private_work_paths_are_rejected_before_reads_or_link_resolution(
    tmp_path, monkeypatch, private
):
    source = make_file(tmp_path, "tools/public.py")
    private_path = tmp_path / private
    monkeypatch.setattr(formatting, "git_paths", lambda *_: {"tools/public.py", private})
    is_file = Path.is_file
    resolve = Path.resolve

    def public_only_is_file(path):
        assert path != private_path, "Excluded private paths must precede filesystem access"
        return is_file(path)

    def public_only_resolve(path):
        assert path != private_path, "Excluded private paths must precede link resolution"
        return resolve(path)

    monkeypatch.setattr(Path, "is_file", public_only_is_file)
    monkeypatch.setattr(Path, "resolve", public_only_resolve)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("unexpected content read"))
    monkeypatch.setattr(
        Path, "read_text", lambda *_args, **_kwargs: pytest.fail("unexpected content read")
    )
    assert not formatting.is_authored_path(private)
    assert formatting.discover_files(tmp_path, True, ()) == (source,)
    assert formatting.discover_files(tmp_path, False, ()) == (source,)
    for explicit in (private, str(private_path)):
        with pytest.raises(ValueError, match="eligible"):
            formatting.discover_files(tmp_path, False, [explicit])


def test_public_link_into_private_work_is_rejected_before_read(tmp_path, monkeypatch):
    source = make_file(tmp_path, "tools/linked.py")
    private_target = tmp_path / ".work/programs/private.py"
    monkeypatch.setattr(formatting, "git_paths", lambda *_: {"tools/linked.py"})
    resolve = Path.resolve
    monkeypatch.setattr(
        Path, "resolve", lambda path: private_target if path == source else resolve(path)
    )
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("unexpected content read"))
    monkeypatch.setattr(
        Path, "read_text", lambda *_args, **_kwargs: pytest.fail("unexpected content read")
    )
    assert formatting.discover_files(tmp_path, True, ()) == ()
    with pytest.raises(ValueError, match="without links"):
        formatting.discover_files(tmp_path, False, [str(source)])
