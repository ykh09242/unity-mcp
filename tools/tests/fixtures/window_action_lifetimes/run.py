"""Compile exact window action bodies with instrumented boundaries; never start Unity."""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[4]
FILES = {
    "client": "MCPForUnity/Editor/Windows/Components/ClientConfig/McpClientConfigSection.cs",
    "advanced": "MCPForUnity/Editor/Windows/Components/Advanced/McpAdvancedSection.cs",
}


def block(text, marker):
    start = text.index(marker)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    # These selected bodies contain no unmatched braces in literals. Reject drift explicitly.
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", help="Read-only Git ref, e.g. 43ee728d")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", choices=("all", "process", "clear", "browse"), default="all")
    args = parser.parse_args()
    output = args.output.resolve()
    allowed = (
        ROOT / ".tmp/CS-20261009-memory-query-resources",
        ROOT / ".tmp/CS-20261009-memory-query-remaining",
    )
    if not any(output.is_relative_to(path) for path in allowed):
        parser.error("--output must be inside an authorized window-action audit .tmp directory")
    output.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, path in FILES.items():
        sources[name] = (
            subprocess.check_output(["git", "show", f"{args.baseline}:{path}"], cwd=ROOT).decode(
                "utf-8"
            )
            if args.baseline
            else (ROOT / path).read_text(encoding="utf-8")
        )
    method = block(sources["client"], "private void OnOpenFileClicked()")
    value = block(sources["advanced"], "gitUrlOverride.RegisterValueChangedCallback(evt =>") + ");"
    clear = block(sources["advanced"], "clearGitUrlButton.clicked += () =>") + ";"
    browse = block(sources["advanced"], "private void OnBrowseGitUrlClicked()")
    resolve = block(sources["advanced"], "private static string ResolveServerPath(string path)")
    print(
        json.dumps(
            {
                "source": args.baseline or "working-tree",
                "sha256": {
                    FILES[name]: hashlib.sha256(source.encode()).hexdigest()
                    for name, source in sources.items()
                },
            },
            sort_keys=True,
        ),
        flush=True,
    )
    scaffold = Template("""using System;
using System.IO;
namespace WindowActions;
public class ClientAction
{
    public TextField configPathField = new();
    public void Open() => OnOpenFileClicked();
$method
}
public class AdvancedAction
{
    public TextField gitUrlOverride = new();
    public Button clearGitUrlButton = new();
    public event Action OnGitUrlChanged;
    public event Action OnHttpServerCommandUpdateRequested;
    public void Browse() => OnBrowseGitUrlClicked();
$browse
$resolve
    public void Register()
    {
$value
$clear
    }
}
""").substitute(method=method, value=value, clear=clear, browse=browse, resolve=resolve)
    (output / "SelectedSource.cs").write_text(scaffold, encoding="utf-8")
    here = Path(__file__).parent
    for name in ("Program.cs", "Boundary.cs"):
        (output / name).write_bytes((here / name).read_bytes())
    (output / "WindowActions.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        "<TargetFramework>net9.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings>"
        "<Nullable>disable</Nullable></PropertyGroup></Project>",
        encoding="utf-8",
    )
    return subprocess.call(
        [
            "dotnet",
            "run",
            "--project",
            str(output / "WindowActions.csproj"),
            "--",
            str(output),
            args.group,
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    sys.exit(main())
