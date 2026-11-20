"""Build one disposable Windows Player and verify old, stale, updated and cached marker loads."""

import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import re
import shutil
from threading import Thread
from uuid import uuid4

from player_e2e_artifacts import digest, read_json, write_json
from player_e2e_process import Invocation, execute
from run_native_package_shapes import verify_lock
from unity_ci_packages import PROFILES, ROOT, prepare


class CatalogServer(BaseHTTPRequestHandler):
    """Expose only the fixture's generated catalog, hash and bundles on loopback."""

    def __init__(self, *args, source, **kwargs):
        self.source = source
        super().__init__(*args, **kwargs)

    def do_PUT(self):
        session = self.source["session"]
        destination = self.source.get("switch_to")
        if self.headers.get("Content-Length", "0") != "0":
            self.send_error(400)
            return
        if self.path == "/switch/" + session and destination is not None:
            self.source["before_switch"] = self.source["requests"].copy()
            self.source["directory"] = destination
            self.source["switch_to"] = None
        elif (
            self.path == "/observed/" + session
            and self.source.get("before_switch") is not None
            and self.source.get("retained_gets") is None
        ):
            self.source["retained_gets"] = self.source["requests"][
                len(self.source["before_switch"]) :
            ]
        else:
            self.send_error(404)
            return
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        name = self.path.partition("?")[0].removeprefix("/")
        if not re.fullmatch(r"[\w.-]{1,200}\.(?:json|hash|bundle)", name):
            self.send_error(404)
            return
        self.source["requests"].append(name)
        path = self.source["directory"] / name
        if not path.is_file() or (name.endswith(".bundle") and self.source["deny_bundles"]):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as stream:
            shutil.copyfileobj(stream, self.wfile)

    def log_message(self, *_):
        pass


def require_player_observation(receipt, session, version, marker, cold, updates, applied, requests):
    """Check independently observed catalog lookup, actual marker, bundle hash/cache and HTTP traffic."""
    item = receipt["observed"]
    before, after = item["bundles_before"], item["bundles_after"]
    expected_cached = not cold
    if (
        receipt["session"] != session
        or receipt["unity_version"] != version
        or not re.fullmatch(r"[0-9a-fA-F]{32}", receipt["build_guid"])
        or item["stage"] != "complete"
        or item.get("error")
        or item["location_count"] != 1
        or not item["catalog_ids"]
        or not item["location_id"]
        or item["loaded_marker"] != f"{marker}-{session}"
        or item["update_count"] not in updates
        or item["updated_catalog_count"] != (item["update_count"] if applied else 0)
        or item["download_bytes_before"] < 0
        or (item["download_bytes_before"] > 0) != cold
        or item["download_bytes_after"] != 0
        or len(before) != 1
        or len(after) != 1
        or not re.fullmatch(r"[0-9a-fA-F]{32}", before[0]["hash"])
        or before[0]["hash"] != after[0]["hash"]
        or before[0]["cached"] is not expected_cached
        or after[0]["cached"] is not True
        or any(name.endswith(".bundle") for name in requests) != cold
    ):
        raise ValueError(
            "Player observation did not match the controlled catalog/cache/content case"
        )
    return before[0]["hash"]


def checked(command, directory, env, timeout):
    result = execute(Invocation(command, directory, timeout=timeout, environment=env))
    if result["forced_termination"] or result["actual_exit_code"] != 0:
        raise ValueError(f"Native process failed. See {directory}")


def run(options):
    root = options.output.resolve()
    root.mkdir(parents=True)
    editor = options.editor.resolve(strict=True)
    profile = read_json(PROFILES)["playerPackageSets"]["addressables-content-update"]
    fixtures = ROOT / "tools/native_api_shapes"
    prepare(
        options.unity_version,
        editor.parent / "Data",
        root / "prepared",
        registry_cache=options.registry_cache,
        extra_packages=profile,
        fixture_sources={
            "Assets/CatalogMarkerPlayer.cs": fixtures / "CatalogMarkerPlayer.cs",
            "Assets/Editor/CatalogMarkerBuild.cs": fixtures / "CatalogMarkerBuild.cs",
        },
    )
    session = uuid4().hex
    project = root / "prepared/project"
    dependencies = read_json(project / "Packages/manifest.json")["dependencies"]
    for owned in (project, root):
        (owned / ".catalog-owner").write_text(session, encoding="ascii")
    content = project / "CatalogFixture"
    source = {
        "session": session,
        "directory": content / "old",
        "requests": [],
        "deny_bundles": False,
    }
    with HTTPServer(("127.0.0.1", 0), partial(CatalogServer, source=source)) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {
                "MCP_CATALOG_SESSION": session,
                "MCP_CATALOG_ORIGIN": f"http://127.0.0.1:{server.server_port}",
            }
            checked(
                [
                    str(editor),
                    "-batchmode",
                    "-nographics",
                    "-quit",
                    "-projectPath",
                    str(project),
                    "-buildTarget",
                    "Win64",
                    "-executeMethod",
                    "CatalogMarkerBuild.Build",
                    "-logFile",
                    str(root / "build.log"),
                ],
                root / "build",
                env,
                1800,
            )
            verify_lock(project, dependencies)
            catalogs = {}
            for generation in ("old", "new"):
                hashes = list((content / generation).glob("*.hash"))
                if len(hashes) != 1 or not hashes[0].with_suffix(".json").is_file():
                    raise ValueError(f"Expected one native {generation} catalog and hash")
                catalogs[generation] = {
                    "name": hashes[0].name,
                    "published_hash": hashes[0].read_text(encoding="ascii").strip(),
                    "catalog_sha256": digest(hashes[0].with_suffix(".json")),
                }
            old, new = catalogs["old"], catalogs["new"]
            if (
                old["name"] != new["name"]
                or old["published_hash"] == new["published_hash"]
                or old["catalog_sha256"] == new["catalog_sha256"]
            ):
                raise ValueError(
                    "Content update must publish changed catalog bytes and hash at the original URL"
                )
            player = content / "Player/Catalog.exe"
            expected_build = None
            bundle_hashes = {}
            observations = {}
            for name, generation, marker, cold, updates, apply, initialization in (
                ("old-cold", "old", "old", True, {0, 1}, True, "cold"),
                ("old-cached", "old", "old", False, {0}, False, "in-flight"),
                ("old-stale", "old", "old", False, {1}, False, "retained"),
                ("new-update", "old", "new", True, {1}, True, "retained"),
                ("new-cached", "new", "new", False, {0}, False, "auto-release"),
            ):
                directory = root / name
                retained = initialization == "retained"
                source.update(
                    directory=content / generation,
                    requests=[],
                    deny_bundles=not cold,
                    switch_to=content / "new" if retained else None,
                    before_switch=None,
                    retained_gets=None,
                )
                checked(
                    [
                        str(player),
                        "-batchmode",
                        "-nographics",
                        "-logFile",
                        str(directory / "player.log"),
                    ],
                    directory,
                    {
                        **env,
                        "MCP_CATALOG_RUN": str(directory),
                        "MCP_CATALOG_APPLY": str(int(apply)),
                        "MCP_CATALOG_INIT": initialization,
                    },
                    120,
                )
                receipt = read_json(directory / "observation.json", 16384)
                if receipt.get("initialization") != initialization:
                    raise ValueError("Missing host initialization ownership evidence")
                if (
                    receipt["unrelated_bytes_after"] <= 0
                    or receipt["unrelated_bytes_after"] != receipt["shared_label_bytes_after"]
                ):
                    raise ValueError(
                        "The shared label must still include the separately packed, unloaded texture"
                    )
                if retained:
                    before_switch = source["before_switch"]
                    retained_gets = source["retained_gets"]
                    if (
                        not before_switch
                        or not retained_gets
                        or source["switch_to"] is not None
                        or old["name"] not in before_switch
                        or new["name"] not in retained_gets
                    ):
                        raise ValueError(
                            "Expected independent old and new hash GETs across the in-process origin switch"
                        )
                    before_update = dict(receipt, observed=receipt["retained_observation"])
                    if (
                        require_player_observation(
                            before_update,
                            session,
                            options.unity_version,
                            "old",
                            False,
                            {1},
                            False,
                            retained_gets,
                        )
                        != bundle_hashes["old"]
                    ):
                        raise ValueError(
                            "Retained host check did not coexist with a pending update and the old bundle"
                        )
                observed_hash = require_player_observation(
                    receipt,
                    session,
                    options.unity_version,
                    marker,
                    cold,
                    updates,
                    apply,
                    source["requests"],
                )
                build_guid = receipt["build_guid"]
                expected_build = expected_build or build_guid
                expected_hash = bundle_hashes.setdefault(marker, observed_hash)
                if build_guid != expected_build or observed_hash != expected_hash:
                    raise ValueError(
                        "Player identity or catalog generation's marker bundle changed across processes"
                    )
                observations[name] = {
                    "player": receipt,
                    "http_gets": source["requests"],
                    "http_gets_before_switch": source["before_switch"],
                    "http_gets_with_retained_check": source["retained_gets"],
                }
            if bundle_hashes["old"] == bundle_hashes["new"]:
                raise ValueError(
                    "Loaded old and new marker bundles must have distinct native hashes"
                )
            write_json(
                root / "result.json",
                {
                    "session": session,
                    "player_sha256": digest(player),
                    "content_state_sha256": digest(content / "original-state.bin"),
                    "package_lock_sha256": digest(project / "Packages/packages-lock.json"),
                    "catalogs": catalogs,
                    "package_profile": profile,
                    "observations": observations,
                },
            )
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", required=True, type=Path)
    parser.add_argument("--unity-version", required=True)
    parser.add_argument("--registry-cache", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    run(parser.parse_args())
