"""Materialize an independently pinned, self-contained fixture project on a clean host."""

from hashlib import sha256
import os

from cli.utils.play_scenario_analysis_io import open_artifact, read_bytes, safe_local_path
from services.verification.git_draft import _Git
from services.verification.input_chunks import decode_object


def bundle_digest(bundle, baseline, candidate):
    bundle = safe_local_path(bundle)
    with open_artifact(bundle, 256 * 1024**2) as (stream, _):
        digest = sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    refs = _Git(bundle.parent).read("bundle", "list-heads", str(bundle), limit=1024)
    expected = {
        f"{commit} refs/heads/{name}".encode()
        for name, commit in (("baseline", baseline), ("candidate", candidate))
    }
    if len(refs.splitlines()) != 2 or set(refs.splitlines()) != expected:
        raise ValueError("reproduction_project_bundle_refs_mismatch")
    return digest.hexdigest()


def materialize(directory, baseline, candidate, digest, destination):
    bundle = safe_local_path(directory / "project.bundle")
    destination = safe_local_path(destination)
    if bundle_digest(bundle, baseline, candidate) != digest:
        raise ValueError("reproduction_project_bundle_digest_mismatch")
    destination.mkdir(parents=True, exist_ok=False)
    # A complete local Git bundle contains both commits; the replay never resolves
    # remotes, submodules, package caches, filters or repository-provided hooks.
    git = _Git(
        destination,
        environment={
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        },
    )
    git.read("init", "--template=", "--quiet")
    git.read("config", "--local", "core.autocrlf", "false")
    git.read("bundle", "verify", str(bundle))
    git.read(
        "-c",
        "fetch.fsckObjects=true",
        "fetch",
        "--no-tags",
        "--no-recurse-submodules",
        str(bundle),
        "refs/heads/baseline:refs/heads/baseline",
        "refs/heads/candidate:refs/heads/candidate",
    )
    git.read("-c", "core.hooksPath=.git/unused-hooks", "checkout", "--quiet", "--detach", candidate)
    return destination


def reviewed_project(manifest, pin, fallback, destination):
    from services.verification.reproduce import PlayerPair

    manifest = safe_local_path(manifest)
    raw = read_bytes(manifest, 64 * 1024)
    if sha256(raw).hexdigest() != pin:
        raise ValueError("reproduction_manifest_digest_mismatch")
    pair = PlayerPair.model_validate(decode_object(raw))
    if pair.project_bundle_sha256 is None:
        return safe_local_path(fallback)
    return materialize(
        manifest.parent,
        pair.baseline.project_commit,
        pair.candidate.project_commit,
        pair.project_bundle_sha256,
        destination,
    )


def captured_project(folder, fallback, destination):
    from services.verification.reproduce import BuildCapture, SIDES

    folder = safe_local_path(folder)
    if not (folder / "project.bundle").exists():
        return safe_local_path(fallback)
    commits = [
        BuildCapture.model_validate(
            decode_object(read_bytes(folder / side / "capture.json", 65536))
        ).project_commit
        for side in SIDES
    ]
    digest = bundle_digest(folder / "project.bundle", *commits)
    return materialize(folder, *commits, digest, destination)


if __name__ == "__main__":
    from cli.commands.verification_pair_transfer import resolve_project_command

    resolve_project_command()
