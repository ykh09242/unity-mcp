"""Plan bounded bridge component pairs and record their actual execution outcome."""

import argparse
from dataclasses import asdict, dataclass
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
from typing import Final

from check_release_evidence import BASE, EvidenceError, git, load_json, require
from check_source_pin_drift import resolve_commit, source_pin


PIN_PATHS: Final = ("MCPForUnity/package.json", "manifest.json", "release-source-inputs.json")


@dataclass(frozen=True, slots=True)
class BridgePair:
    mode: str
    package_commit: str
    selected_server_commit: str
    tested_server_commit: str
    server_source: str


def plan(repo: Path, selection: str, baseline: str | None = None) -> list[BridgePair]:
    """Always identify committed pins; add selected coverage only when requested or refreshed."""
    package = resolve_commit(repo, "HEAD")
    selected = source_pin(repo, package)
    metadata_changed = False
    if baseline:
        base = resolve_commit(repo, baseline)
        metadata_changed = bool(git(repo, "diff", "--name-only", base, package, "--", *PIN_PATHS))
    modes = {
        "auto": ("candidate", "selected") if metadata_changed else ("candidate",),
        "candidate": ("candidate",),
        "selected": ("selected",),
        "both": ("candidate", "selected"),
    }
    require(selection in modes, "Unknown bridge pair selection")
    return [
        BridgePair(
            mode,
            package,
            selected,
            selected if mode == "selected" else package,
            BASE + f"/archive/{selected}.zip#subdirectory=Server"
            if mode == "selected"
            else "Server",
        )
        for mode in modes[selection]
    ]


@dataclass(frozen=True, slots=True)
class InstalledServer:
    server_source: str
    distribution_version: str
    module_path: str


def verify_installed(pair: BridgePair) -> InstalledServer:
    """Reject editable installs, mismatched archive provenance and checkout import shadows."""
    distribution = importlib.metadata.distribution("ykh09242-unity-mcp-server")
    direct = distribution.read_text("direct_url.json")
    require(direct is not None, "Installed server lacks archive provenance")
    provenance = load_json(direct.encode("utf-8"))
    require(
        provenance.get("url") == pair.server_source.split("#", 1)[0]
        and provenance.get("subdirectory") == "Server"
        and isinstance(provenance.get("archive_info"), dict)
        and "dir_info" not in provenance,
        "Installed server differs from the selected immutable archive",
    )
    spec = importlib.util.find_spec("transport.legacy.unity_connection")
    require(spec is not None and spec.origin is not None, "Installed bridge module is missing")
    module = Path(spec.origin).resolve()
    installed_root = Path(distribution.locate_file("")).resolve()
    require(module.is_relative_to(installed_root), "Bridge import shadows the installed server")
    return InstalledServer(pair.server_source, distribution.version, str(module))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "report", "verify-installed"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--selection", choices=("auto", "candidate", "selected", "both"), default="auto"
    )
    parser.add_argument("--baseline")
    parser.add_argument("--mode", choices=("candidate", "selected"), default="candidate")
    parser.add_argument(
        "--outcome", choices=("success", "failure", "cancelled", "skipped"), default="skipped"
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--installation", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            pairs = plan(args.repo, args.selection, args.baseline)
            report = {
                "schema_version": 1,
                "execution_status": "planned",
                "include": [asdict(pair) for pair in pairs],
            }
            output = os.environ.get("GITHUB_OUTPUT")
            if output:
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write("matrix=" + json.dumps({"include": report["include"]}) + "\n")
        else:
            pair = plan(args.repo, args.mode)[0]
            if args.command == "verify-installed":
                report = asdict(verify_installed(pair))
            else:
                installation = None
                if args.installation and args.installation.is_file():
                    installation = load_json(args.installation.read_bytes())
                if pair.mode == "selected" and args.outcome == "success":
                    require(
                        installation is not None
                        and installation.get("server_source") == pair.server_source,
                        "Successful selected coverage requires verified installed provenance",
                    )
                report = {
                    "schema_version": 1,
                    **asdict(pair),
                    "execution_status": args.outcome,
                    "live_bridge_passed": args.outcome == "success",
                    "installation": installation,
                }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as stream:
                stream.write("## Bridge component pair evidence\n\n```json\n")
                stream.write(json.dumps(report, indent=2) + "\n```\n\n")
                stream.write(
                    "Only a successful live harness is bridge execution evidence. Planned or skipped pairs did not pass.\n"
                )
    except (EvidenceError, OSError, ValueError, importlib.metadata.PackageNotFoundError):
        print("Bridge pair evidence unavailable: invalid metadata, installation or local history.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
