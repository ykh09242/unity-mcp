"""Replay an installed-package registry lookup from integrity-checked local Unity cache content."""

import argparse
import base64
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.experiments.live_editor_fixture.lane import (
    CACHE,
    PRODUCT_PACKAGE,
    RECORDED_REVISION,
    REVISION,
    ROOT,
    WORK,
    check_work_root,
    dump,
    launch,
    real_child,
    require_file,
    shared,
)

REGISTRY = "https://packages.unity.com"
VERSIONS = {"com.unity.cinemachine": "2.6.11", "com.unity.inputsystem": "1.14.2"}


def bucket(root, digest):
    return root / digest[:2] / digest[2:4] / digest[4:]


def read_cached(source, key):
    index = require_file(
        bucket(source / "index-v5", hashlib.sha256(key.encode()).hexdigest()),
        "the Unity official registry metadata cache",
    )
    rows = [json.loads(line.split(b"\t", 1)[1]) for line in index.read_bytes().splitlines() if line]
    record = next(row for row in reversed(rows) if row["key"] == key)
    algo, checksum = record["integrity"].split("-", 1)
    digest = base64.b64decode(checksum, validate=True).hex()
    content = require_file(
        bucket(source / "content-v2" / algo, digest), "the cached package content"
    ).read_bytes()
    if hashlib.new(algo, content).hexdigest() != digest:
        raise ValueError(f"Local public cache content integrity mismatch for {key}")
    return content


def official_record(name, version):
    # Unity's public registry cache uses a content-addressed npm cache index.
    key = f"package-metadata|{name}|packages.unity.com"
    content = read_cached(CACHE / "upm/db", key)
    entry = json.loads(content)["versions"][version]
    tarball = require_file(
        CACHE / "npm/packages.unity.com" / name / version / "package.tgz",
        "the cached official package tarball",
    )
    payload = tarball.read_bytes()
    if (
        entry["name"] != name
        or entry["version"] != version
        or hashlib.sha1(payload).hexdigest() != entry["dist"]["shasum"]
    ):
        raise ValueError(f"Registry version or tarball mismatch for {name}@{version}")
    evidence = {k: entry[k] for k in ("name", "version", "displayName", "description")}
    evidence.update(
        author=entry.get("author"),
        registry=REGISTRY,
        registryMetadataKey=key,
        tarballUrl=entry["dist"]["tarball"],
        metadataSha256=hashlib.sha256(content).hexdigest(),
        tarballSha256=hashlib.sha256(payload).hexdigest(),
        publishedTarballSha1=entry["dist"]["shasum"],
    )
    return evidence


def write_cache_content(db, key, algo, payload):
    digest = hashlib.new(algo, payload)
    blob = bucket(db / "content-v2" / algo, digest.hexdigest())
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(payload)
    entry = {
        "key": key,
        "integrity": algo + "-" + base64.b64encode(digest.digest()).decode(),
        "time": int(time.time() * 1000),
        "size": len(payload),
    }
    encoded = json.dumps(entry, separators=(",", ":")).encode()
    index = bucket(db / "index-v5", hashlib.sha256(key.encode()).hexdigest())
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_bytes(b"\n" + hashlib.sha1(encoded).hexdigest().encode() + b"\t" + encoded + b"\n")


def restore_cache():
    check_work_root()
    db = real_child(WORK / "upm-cache/db")
    for name, version in VERSIONS.items():
        official_record(name, version)
        key = f"package-metadata|{name}|packages.unity.com"
        write_cache_content(db, key, "sha512", read_cached(CACHE / "upm/db", key))
        payload = (CACHE / "npm/packages.unity.com" / name / version / "package.tgz").read_bytes()
        suffix = f"|{name}|{version}|packages.unity.com"
        write_cache_content(db, "package-tarball" + suffix, "sha1", payload)
        write_cache_content(db, "package-size" + suffix, "sha512", str(len(payload)).encode())
        try:
            signature = read_cached(CACHE / "upm/db", "package-signature" + suffix)
        except FileNotFoundError:
            continue
        write_cache_content(db, "package-signature" + suffix, "sha512", signature)


def configure_registry(baseline: Path, output: Path, candidate_product: str | None) -> None:
    old_lock = baseline / "lane-0/project/Packages/packages-lock.json"
    locked = json.loads(require_file(old_lock, "the frozen baseline lane").read_bytes())
    manifest = json.loads((baseline / "dependencies/manifest.json").read_bytes())
    if candidate_product is not None:
        manifest["dependencies"][PRODUCT_PACKAGE] = candidate_product
        locked["dependencies"][PRODUCT_PACKAGE]["version"] = candidate_product
    for name, version in VERSIONS.items():
        manifest["dependencies"][name] = version
        old = locked["dependencies"][name]
        locked["dependencies"][name] = {
            "version": version,
            "depth": old["depth"],
            "source": "registry",
            "dependencies": old["dependencies"],
            "url": REGISTRY,
        }
    dump(output / "manifest.json", manifest)
    dump(output / "packages-lock.json", locked)


def prepare(baseline):
    if baseline == WORK or baseline.is_relative_to(WORK) or WORK.is_relative_to(baseline):
        raise ValueError("Use separate baseline and registry trial roots")
    if (WORK / "lane-0").exists():
        raise ValueError("Registry preparation requires a fresh lane")
    entries = {name: official_record(name, version) for name, version in VERSIONS.items()}
    shared()
    output = real_child(WORK / "dependencies")
    candidate_product = None
    if REVISION != RECORDED_REVISION:
        candidate_product = json.loads((output / "manifest.json").read_bytes())["dependencies"][
            PRODUCT_PACKAGE
        ]
    real_child(output / "packages-lock.json")
    configure_registry(baseline, output, candidate_product)
    shutil.copy2(baseline / "bin/LaneHost.dll", real_child(WORK / "bin/LaneHost.dll"))
    shutil.copy2(baseline / "bin/World.dll", real_child(WORK / "bin/World.dll"))
    dump(
        WORK / "registry-evidence.json",
        {"registry": REGISTRY, "installedRegistry": REGISTRY, "packages": entries},
    )
    restore_cache()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "restore-cache", "launch"])
    parser.add_argument("--baseline", type=Path, default=ROOT / ".work/live-agent-20261125")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.baseline.resolve())
    elif args.command == "restore-cache":
        restore_cache()
    else:
        check_work_root()
        registered = json.loads(
            require_file(WORK / "registry-evidence.json", "registry.py prepare").read_bytes()
        )
        launch(0, registry=registered["installedRegistry"])
        shutil.copy2(WORK / "bin/World.dll", real_child(WORK / "lane-0/control/World.dll"))


if __name__ == "__main__":
    main()
