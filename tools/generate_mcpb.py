#!/usr/bin/env python3
"""Generate MCPB bundle for Unity MCP.

This script creates a Model Context Protocol Bundle (.mcpb) file
for distribution as a GitHub release artifact.

Usage:
    python3 tools/generate_mcpb.py VERSION --icon PATH [--output FILE]

Examples:
    python3 tools/generate_mcpb.py 9.0.8 --icon /path/to/your-icon.png
    python3 tools/generate_mcpb.py 9.0.8 --icon /path/to/your-icon.png --output unity-mcp-9.0.8.mcpb
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_TEMPLATE = REPO_ROOT / "manifest.json"


def create_manifest(version: str, icon_filename: str) -> dict:
    """Create manifest.json content with the specified version."""
    if not MANIFEST_TEMPLATE.exists():
        raise FileNotFoundError(f"Manifest template not found: {MANIFEST_TEMPLATE}")

    manifest = json.loads(MANIFEST_TEMPLATE.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest["icon"] = icon_filename
    return manifest


def generate_mcpb(
    version: str,
    output_path: Path,
    icon_path: Path,
) -> Path:
    """Generate MCPB bundle file.

    Args:
        version: Semantic version string (e.g., "9.0.8")
        output_path: Output path for the .mcpb file
        icon_path: Path to the icon file

    Returns:
        Path to the generated .mcpb file
    """
    if not icon_path.is_file():
        raise FileNotFoundError(f"Icon not found: {icon_path}")
    if icon_path.name.casefold() in {"manifest.json", "license", "readme.md"}:
        raise ValueError(f"Icon filename conflicts with a bundle file: {icon_path.name}")

    with tempfile.TemporaryDirectory(dir=output_path.absolute().parent) as tmpdir:
        build_dir = Path(tmpdir) / "mcpb-build"
        build_dir.mkdir()
        staged_output = Path(tmpdir) / "bundle.mcpb"

        # Copy icon
        icon_filename = icon_path.name
        shutil.copy2(icon_path, build_dir / icon_filename)

        # Create manifest with version
        manifest = create_manifest(version, icon_filename)
        manifest_path = build_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Copy LICENSE and README if they exist
        for filename in ["LICENSE", "README.md"]:
            src = REPO_ROOT / filename
            if src.exists():
                shutil.copy2(src, build_dir / filename)

        # Pack using mcpb CLI
        # Syntax: mcpb pack [directory] [output]
        try:
            result = subprocess.run(
                ["npx", "@anthropic-ai/mcpb", "pack", ".", str(staged_output)],
                cwd=build_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"MCPB pack failed:\n{e.stderr}", file=sys.stderr)
            raise
        except FileNotFoundError:
            print(
                "Error: npx not found. Please install Node.js and npm.",
                file=sys.stderr,
            )
            raise

        if not staged_output.is_file() or staged_output.stat().st_size == 0:
            raise RuntimeError(f"MCPB file was not created: {output_path}")
        staged_output.replace(output_path)

    print(f"Generated: {output_path} ({output_path.stat().st_size:,} bytes)")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate MCPB bundle for Unity MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "version",
        help="Version string for the bundle (e.g., 9.0.8)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output path for the .mcpb file (default: unity-mcp-VERSION.mcpb)",
    )
    parser.add_argument(
        "--icon",
        type=Path,
        required=True,
        help="Path to an icon you are authorized to use for this distribution",
    )

    args = parser.parse_args()

    # Default output name
    if args.output is None:
        args.output = Path(f"unity-mcp-{args.version}.mcpb")

    try:
        generate_mcpb(args.version, args.output, args.icon)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
