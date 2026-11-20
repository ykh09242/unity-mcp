"""Review-only Git selection for a Unity project. Never stages files or registers a Player."""

from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryFile
from threading import Timer
from time import monotonic

from cli.utils.play_scenario_analysis_io import safe_local_path
from services.verification.checkout_paths import CheckoutPaths
from services.verification.git_draft_artifacts import DRAFT_PATH_LIMIT, pack_inputs
from services.verification.input_chunks import (
    CHUNK_BYTES,
    CHUNK_PATHS,
    MAX_INPUT_PATHS,
    SourceSample,
    publish_input_chunk,
    source_revision,
    validate_input_paths,
)
from services.verification.project_configuration import CONFIG_PATHS, ConfigurationEvidence


_BLOB_LIMIT = 4 * 1024 * 1024
_SOURCE_BYTES = 32 * 1024 * 1024
_LIST_LIMIT = 8 * 1024 * 1024
_INLINE_BUILD_FIELDS_BYTES = 1024
_OID = re.compile(rb"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_GUID = re.compile(rb"^guid:[ \t]*([a-f0-9]{32})[ \t]*\r?$", re.MULTILINE)
_DEVICE = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|(?:com|lpt)[1-9¹²³])", re.IGNORECASE)
_SCOPE = (
    "Assets",
    "Packages",
    *sorted(CONFIG_PATHS),
    ":(exclude,icase,glob)Assets/Resources/GameData/**",
    ":(exclude,icase,glob)**/.env*",
)
_DIFF = (
    "--relative",
    "--no-ext-diff",
    "--no-textconv",
    "--ignore-submodules=all",
    "--no-renames",
    "--name-only",
    "-z",
)


class _GitFailed(ValueError):
    """A bounded Git command exited unsuccessfully."""


class _Git:
    def __init__(self, project, *, environment=None):
        self.project = project
        self.environment = environment or {}
        self.deadline = monotonic() + 60

    def remaining(self):
        seconds = self.deadline - monotonic()
        if seconds <= 0:
            raise ValueError("candidate_git_deadline")
        return seconds

    @contextmanager
    def _process(self, *args, stdin=subprocess.DEVNULL, accepted=(0,)):
        # A repository can sit inside a larger checkout. Keep pathnames relative to
        # the selected project and never invoke a diff driver, hook, pager or smudge filter.
        remaining = self.remaining()
        env = {key: val for key, val in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")
        env.update(self.environment)
        with subprocess.Popen(
            [
                "git",
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.precomposeUnicode=false",
                *args,
            ],
            cwd=self.project,
            env=env,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            timer = Timer(remaining, process.kill)
            timer.start()
            try:
                yield process.stdout
                returncode = process.wait()
                if monotonic() >= self.deadline:
                    raise ValueError("candidate_git_deadline")
                if returncode not in accepted:
                    raise _GitFailed(f"candidate_git_{args[0]}_failed")
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()

    def read(
        self,
        *args,
        limit=_LIST_LIMIT,
        accepted=(0,),
        output_error="candidate_git_output_limit",
        stdin=subprocess.DEVNULL,
    ):
        with self._process(*args, accepted=accepted, stdin=stdin) as stream:
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError(output_error)
            return data

    @contextmanager
    def blobs(self, blobs):
        if not blobs:
            yield iter(())
            return
        ordered = sorted(blobs.items())
        with TemporaryFile() as requests:
            for _, oid in ordered:
                requests.write(oid.encode("ascii") + b"\n")
            requests.seek(0)
            with self._process("cat-file", "--batch", stdin=requests) as stream:
                yield _read_batch(stream, ordered)


def _read_batch(stream, ordered):
    total = 0
    for path, oid in ordered:
        header = stream.readline(128).split()
        if (
            len(header) != 3
            or header[:2] != [oid.encode("ascii"), b"blob"]
            or not header[2].isdigit()
        ):
            raise ValueError("candidate_git_blob_unavailable")
        size = int(header[2])
        if size > _BLOB_LIMIT or total + size > _SOURCE_BYTES:
            raise ValueError(
                "candidate_draft_source_byte_limit: Up to 4 MiB per file and 32 MiB per side. "
                "Split the selection or exclude the oversized source and retry."
            )
        data = stream.read(size)
        if len(data) != size or stream.read(1) != b"\n":
            raise ValueError("candidate_git_blob_unavailable")
        total += size
        yield path, data
    if stream.read(1):
        raise ValueError("candidate_git_unexpected_blob_output")


def _records(data):
    return data.rstrip(b"\0").split(b"\0") if data else []


def _eligible(path):
    if (
        len(path) > 512
        or path.casefold().startswith("assets/resources/gamedata/")
        or any(_DEVICE.fullmatch(part.partition(".")[0].rstrip(" ")) for part in path.split("/"))
    ):
        return False
    try:
        validate_input_paths([path])
        return True
    except ValueError:
        return False


def _blob_ids(git, commit, paths):
    if not paths:
        return {}
    # --literal-pathspecs prevents brackets and other valid filenames from
    # broadening the exact path selection. ls-tree and ls-files preserve cwd.
    args = ["ls-tree", "-z", commit] if commit else ["ls-files", "--stage", "-z"]
    result = {}
    records = []
    path_set = set(paths)
    # Budget for quoting and non-BMP characters represented as two UTF-16 units
    # on Windows. Shorter paths can share a batch without extra Git processes.
    batch_size = min(256, 24_000 // (2 * max(map(len, paths)) + 3))
    for start in range(0, len(paths), batch_size):
        batch = paths[start : start + batch_size]
        records.extend(_records(git.read("--literal-pathspecs", *args, "--", *batch)))
    for record in records:
        header, name = record.split(b"\t", 1)
        mode, middle, last = header.split(b" ")
        path = name.decode("utf-8")
        oid = last if commit else middle
        if (
            path not in path_set
            or mode not in (b"100644", b"100755")
            or not _OID.fullmatch(oid)
            or (not commit and last != b"0")
        ):
            raise ValueError("candidate_git_nonregular_or_unmerged_input")
        result[path] = oid.decode("ascii")
    return result


def _working_bytes(project, paths):
    sample = SourceSample()
    checkout = CheckoutPaths(project, paths)
    total = 0
    for path in paths:
        try:
            data = sample.read(checkout.resolve(path), min(_BLOB_LIMIT, _SOURCE_BYTES - total))
        except FileNotFoundError:
            continue
        total += len(data)
        yield path, data
    sample.finish()
    checkout.finish()


def _snapshot(files):
    inputs, assets = {}, {}
    configuration = ConfigurationEvidence()
    for path, data in files:
        inputs[path] = sha256(data).hexdigest()
        if path in CONFIG_PATHS:
            configuration.observe(path, data)
        if path.endswith(".meta"):
            guids = _GUID.findall(data)
            if len(guids) > 1:
                raise ValueError("candidate_draft_ambiguous_metadata")
            if guids:
                assets[path.removesuffix(".meta")] = guids[0].decode("ascii")
    assets = {path: guid for path, guid in assets.items() if path in inputs}
    validate_input_paths(inputs)
    if len(set(assets.values())) != len(assets):
        raise ValueError("candidate_draft_ambiguous_asset_identities")
    return inputs, assets, configuration


def _resolve_commit(git, revision, side):
    if (
        not revision
        or len(revision) > 256
        or revision.startswith("-")
        or ":" in revision
        or any(ord(char) < 32 for char in revision)
    ):
        raise ValueError(f"candidate_git_invalid_{side}")
    try:
        resolved = git.read("rev-parse", "--verify", "--end-of-options", revision + "^{commit}")
    except _GitFailed:
        raise ValueError(
            f"candidate_git_unresolved_{side}: Provide a local Git commit or ref."
        ) from None
    commit = resolved.strip()
    if not _OID.fullmatch(commit):
        raise ValueError(f"candidate_git_invalid_{side}")
    return commit.decode("ascii")


def _merge_base(git, base, candidate):
    if git.read("rev-parse", "--is-shallow-repository").strip() == b"true":
        raise ValueError(
            "candidate_git_shallow_history: Use a full checkout containing the two commits."
        )
    bases = git.read("merge-base", "--all", base, candidate, accepted=(0, 1)).splitlines()
    if not bases:
        raise ValueError("candidate_git_no_merge_base: Select commits with shared history.")
    if len(bases) != 1:
        raise ValueError(
            "candidate_git_multiple_merge_bases: Select refs with one unambiguous merge base."
        )
    if not _OID.fullmatch(bases[0]):
        raise ValueError("candidate_git_invalid_merge_base")
    return bases[0].decode("ascii")


def _selection_paths(changed):
    paths = set()
    skipped = 0
    for raw in set(changed):
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped += 1
            continue
        if not _eligible(path):
            skipped += 1
            continue
        if path in CONFIG_PATHS:
            paths.add(path)
        else:
            base = path.removesuffix(".meta")
            paths.update((base, base + ".meta"))
    # Two disjoint sides can each contribute MAX_INPUT_PATHS changed names.
    # Each such name has at most one prospective metadata/source sibling.
    if len(paths) > 4 * MAX_INPUT_PATHS:
        raise ValueError(DRAFT_PATH_LIMIT)
    if any(len(path) > 512 for path in paths):
        raise ValueError("candidate_draft_source_path_length_limit")
    return sorted(paths), skipped


def git_draft(
    project: Path,
    baseline: str,
    selection: str,
    *,
    candidate_ref: str | None = None,
    chunk_directory: Path | None = None,
):
    project = safe_local_path(project)
    if selection not in {"staged", "working-tree", "committed"}:
        raise ValueError("candidate_git_unknown_selection")
    if (selection == "committed") != (candidate_ref is not None):
        raise ValueError(
            "candidate_git_ref_required: Use --candidate-ref only with --selection committed."
        )

    git = _Git(project)
    base = _resolve_commit(git, baseline, "baseline")
    candidate = (
        _resolve_commit(git, candidate_ref, "candidate") if candidate_ref is not None else None
    )
    if candidate is not None:
        commit = _merge_base(git, base, candidate)
        revisions = (commit, candidate)
    else:
        commit = base
        revisions = ("--cached", commit)
    changed = _records(
        git.read("diff", *_DIFF, *revisions, "--", *_SCOPE, output_error=DRAFT_PATH_LIMIT)
    )
    if selection == "working-tree":
        changed.extend(
            _records(
                git.read(
                    "ls-files",
                    "-z",
                    "-m",
                    "-o",
                    "--exclude-standard",
                    "--",
                    *_SCOPE,
                    output_error=DRAFT_PATH_LIMIT,
                )
            )
        )
    paths, skipped = _selection_paths(changed)
    if selection == "working-tree" and len({path.casefold() for path in paths}) != len(paths):
        raise ValueError("candidate_draft_working_path_collision")

    with git.blobs(_blob_ids(git, commit, paths)) as files:
        before, old_assets, old_configuration = _snapshot(files)
    selected_blobs = _blob_ids(git, candidate, paths)
    if selection == "working-tree":
        after, new_assets, new_configuration = _snapshot(_working_bytes(project, paths))
    else:
        with git.blobs(selected_blobs) as files:
            after, new_assets, new_configuration = _snapshot(files)

    changes = []
    for path in paths:
        if before.get(path) == after.get(path):
            continue
        kind = (
            "modified"
            if path in before and path in after
            else "added"
            if path in after
            else "deleted"
        )
        changes.append({"path": path, "kind": kind})
    affected = {row["path"].removesuffix(".meta") for row in changes}
    chunks = {}

    def evidence(inputs, assets, configuration):
        selected = {p: d for p, d in inputs.items() if p.removesuffix(".meta") in affected}
        fields = {
            "source_revision": source_revision(selected),
            "assets": {p: guid for p, guid in assets.items() if p in affected},
            **configuration.registration(selected),
        }
        inline = {"inputs": selected, **fields}
        if len(selected) <= CHUNK_PATHS:
            # Account for a pretty-printed registration with escaped Unicode. Reserve
            # space for the scenario and build identity added by the operator.
            size = len(
                json.dumps({"schema_version": 1, "project": str(project), **inline}, indent=2)
            )
            if size + _INLINE_BUILD_FIELDS_BYTES <= CHUNK_BYTES:
                return inline
        packed = pack_inputs(selected)
        chunks.update({ref.sha256: (ref, data) for ref, data in packed})
        return {"input_chunks": [ref.model_dump() for ref, _ in packed], **fields}

    baseline_evidence = evidence(before, old_assets, old_configuration)
    candidate_evidence = evidence(after, new_assets, new_configuration)
    configuration_changes = old_configuration.compare(new_configuration)
    previous = {guid: path for path, guid in old_assets.items()}
    result = {
        "schema_version": 2 if chunks else 1,
        "kind": "git_candidate_draft",
        "baseline_commit": commit,
        "selection": selection,
        **({"base_commit": base, "candidate_commit": candidate} if candidate is not None else {}),
        "baseline": baseline_evidence,
        "candidate": candidate_evidence,
        "changed_paths": changes,
        "asset_moves": [
            {"guid": guid, "from": previous[guid], "to": path}
            for path, guid in new_assets.items()
            if guid in previous and previous[guid] != path
        ],
        "asset_replacements": [
            {"path": path, "old_guid": old_assets[path], "new_guid": new_assets[path]}
            for path in sorted(old_assets.keys() & new_assets.keys())
            if old_assets[path] != new_assets[path]
        ],
        "asset_deletions": [
            {"path": path, "guid": guid}
            for path, guid in old_assets.items()
            if path not in after and guid not in new_assets.values()
        ],
        "skipped_unsupported_path_count": skipped,
        **({"configuration_changes": configuration_changes} if configuration_changes else {}),
    }
    if chunks:
        if chunk_directory is None:
            raise ValueError(
                "candidate_draft_chunk_destination_required: Provide --output or --chunk-directory "
                "to save the content-addressed input chunks."
            )
        for ref, content in chunks.values():
            publish_input_chunk(chunk_directory, ref, content)
    return result
