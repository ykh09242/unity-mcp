"""Compare raw Git names with physical lookup on the runner's actual volume."""

from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import unicodedata

import pytest

from services.verification.checkout_diagnostics import CheckoutDivergence, CheckoutInputs
from services.verification.git_draft import _Git, _blob_ids
from services.verification.input_chunks import SourceSample
from services.verification.player_candidate import live_sources


def git(root: Path, *args: str, stdin: bytes | None = None):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=root,
        input=stdin,
        capture_output=True,
        check=True,
    ).stdout


def make_collision_commit(repo: Path, names: tuple[str, str], contents: dict[str, bytes]):
    git(repo, "init", "--initial-branch=fixture")
    entries = []
    for name in names:
        oid = git(repo, "hash-object", "-w", "--stdin", stdin=contents[name]).strip()
        entries.append(b"100644 blob " + oid + b"\t" + name.encode("utf-8") + b"\0")
    assets = git(repo, "mktree", "-z", stdin=b"".join(entries)).strip()
    tree = git(repo, "mktree", "-z", stdin=b"040000 tree " + assets + b"\tAssets\0").strip()
    commit = git(repo, "commit-tree", tree.decode("ascii"), "-m", "path collision fixture").strip()
    git(repo, "update-ref", "HEAD", commit.decode("ascii"))
    return commit.decode("ascii")


def physical_open(path: Path, entries: tuple[Path, ...]):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {"opened": False, "sha256": None, "entries": []}
    # samefile identifies which physical directory entries the host lookup used,
    # including cases where different Git names contain identical blob bytes.
    matches = [entry.name for entry in entries if entry.samefile(path)]
    assert len(matches) == 1, (path, matches)
    return {"opened": True, "sha256": sha256(data).hexdigest(), "entries": matches}


@pytest.mark.parametrize("spelling", ["case", "hangul_normalization"])
@pytest.mark.parametrize("equal_bytes", [False, True])
def test_git_collisions_use_exact_checkout_entries(tmp_path, request, spelling, equal_bytes):
    nfc = "\ud55c\uae00.asset"
    names = (
        ("Panel.asset", "panel.asset")
        if spelling == "case"
        else (nfc, unicodedata.normalize("NFD", nfc))
    )
    contents = {
        name: f"controlled asset {0 if equal_bytes else i}\n".encode()
        for i, name in enumerate(names)
    }
    source = tmp_path / "source"
    source.mkdir()
    commit = make_collision_commit(source, names, contents)
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--no-checkout", "--local", str(source), str(checkout))
    git(checkout, "config", "core.autocrlf", "false")
    # A host can decline checkout or materialize one of the colliding spellings.
    materialize = subprocess.run(
        ["git", "checkout", "--detach", commit], cwd=checkout, capture_output=True, check=False
    )

    raw_names = git(checkout, "ls-tree", "-r", "--name-only", "-z", commit).split(b"\0")[:-1]
    paths = [f"Assets/{name}" for name in names]
    assert set(raw_names) == {path.encode("utf-8") for path in paths}
    reader = _Git(checkout)
    blobs = _blob_ids(reader, commit, paths)
    with reader.blobs(blobs) as results:
        committed = dict(results)
    assert committed == {f"Assets/{name}": body for name, body in contents.items()}

    assets = checkout / "Assets"
    physical = tuple(sorted(assets.iterdir())) if assets.is_dir() else ()
    if materialize.returncode == 0:
        assert physical, "Successful fixture checkout materialized no asset"
    opened = {path: physical_open(checkout / path, physical) for path in paths}
    exact = {path for path, result in opened.items() if result["entries"] == [Path(path).name]}
    if platform.system() == "Linux" and materialize.returncode == 0:
        assert exact == set(paths), "Expected the hosted Linux volume to preserve both names"

    expected = {path: sha256(data).hexdigest() for path, data in committed.items()}
    assert live_sources(checkout, paths) == {path: opened[path]["sha256"] for path in exact}
    findings = []
    sample = SourceSample()
    comparison = CheckoutInputs(checkout, expected, sample)
    assert dict(comparison.compare(committed.items())) == committed
    expected_findings = {
        path: "path_alias"
        if result["opened"] and path not in exact
        else "missing"
        if not result["opened"]
        else "content_mismatch"
        for path, result in opened.items()
        if path not in exact or result["sha256"] != expected[path]
    }
    if expected_findings:
        with pytest.raises(CheckoutDivergence) as error:
            comparison.check()
        findings = error.value.findings
        assert {finding["path"]: finding["kind"] for finding in findings} == expected_findings
        assert all(
            row["checkout_sha256"] is None for row in findings if row["kind"] == "path_alias"
        )
    else:
        comparison.check()
    sample.finish()

    request.node.user_properties.append(
        (
            "checkout_path_observation",
            json.dumps(
                {
                    "platform": platform.platform(),
                    "git_version": git(checkout, "--version").decode().strip(),
                    "commit": commit,
                    "spelling": spelling,
                    "equal_blob_bytes": equal_bytes,
                    "git_names_utf8_hex": [name.hex() for name in raw_names],
                    "git_blob_oids": blobs,
                    "materialization_exit_code": materialize.returncode,
                    "materialization_stderr": materialize.stderr.decode("utf-8", "replace"),
                    "physical_names": [entry.name for entry in physical],
                    "host_lookups": opened,
                    "exact_paths": sorted(exact),
                    "findings": findings,
                },
                sort_keys=True,
            ),
        )
    )
