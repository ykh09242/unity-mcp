"""Project the purpose and returned evidence of an observed shell call without storing its text."""

from __future__ import annotations

from hashlib import sha256
from pathlib import PurePosixPath
import re
import shlex


def _tokens(command):
    try:
        # Keep Windows path separators. Only literal arguments from a single command qualify.
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return []
    return [
        token[1:-1] if len(token) > 1 and token[0] == token[-1] and token[0] in "\"'" else token
        for token in tokens
    ]


def shell_purpose(command, asset="", project="", cwd=None):
    if not isinstance(command, str) or len(command) > 8192:
        return "unknown"
    tokens = _tokens(command)
    if len(tokens) == 3 and tokens[1].casefold() in {"-command", "-lc"}:
        executable = tokens[0].replace("\\", "/").split("/")[-1].casefold()
        if executable in {"powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash"}:
            command = tokens[-1]
            tokens = _tokens(command)
    if not tokens or re.search(r"[;&|`$<>\r\n()]", command):
        return "unknown"
    if len(tokens) == 1 and tokens[0].casefold() in {"pwd", "get-location"}:
        return "working_directory"
    name = tokens[0].casefold()
    if name not in {"cat", "get-content"}:
        return "unknown"
    options = {arg.casefold() for arg in tokens[1:] if arg.startswith("-")}
    allowed = {"-raw", "-path", "-literalpath"} if name == "get-content" else {"--"}
    if not options <= allowed or {"-path", "-literalpath"} <= options:
        return "unknown"
    paths = [arg for arg in tokens[1:] if not arg.startswith("-")]
    if len(paths) != 1 or (tokens[-1] != paths[0] and name == "cat"):
        return "unknown"
    literal = paths[0].replace("\\", "/")
    if any(c in literal for c in "*?[]{}") and "-literalpath" not in options:
        return "unknown"
    if not literal or any(c in literal for c in "\"'") or ".." in PurePosixPath(literal).parts:
        return "unknown"
    path = PurePosixPath(literal)
    if project:
        root = PurePosixPath(str(project).replace("\\", "/"))
        if not path.is_absolute() and not re.match(r"^[A-Za-z]:/", path.as_posix()):
            if cwd:
                path = PurePosixPath(str(cwd).replace("\\", "/")) / path
            elif path.as_posix() == asset:
                return "unknown"
        if path.is_relative_to(root):
            path = path.relative_to(root)
    if asset and path.as_posix() == asset:
        return "named_asset_read"
    if path.name in {"AGENTS.md", "SKILL.md"}:
        return "instruction_read"
    return "other_file_read"


def shell_receipt(command, output, exit_code, asset, project="", cwd=None):
    from tools.experiments.blind_tool_trace import saved_graph_facts

    purpose = shell_purpose(command, asset, project, cwd)
    receipt = {"purpose": purpose}
    if purpose == "named_asset_read":
        receipt["asset_digest"] = sha256(asset.encode()).hexdigest()
    if purpose == "named_asset_read" and exit_code == 0 and isinstance(output, str):
        facts = saved_graph_facts(output[:2_000_000], allow_partial=True)
        if facts:
            receipt["facts"] = facts
    return receipt
