"""Generate exact package handler identities for audited native operation routes.

Only package Editor source is scanned. Project .asmref source and loaded type
metadata never contribute owners. This does not replace registry method/delegate
sealing or turn discovery attributes into runtime concurrency authority.
"""

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server/src"))

from services.operation_permissions import _ACTIONS, _NATIVE_COMMANDS  # noqa: E402
from services.operation_policy import (  # noqa: E402
    CAPABILITY_ALIASES,
    OperationMode,
    resolve_operation_policy,
)

TARGET = ROOT / "MCPForUnity/Editor/Services/ToolOperationIdentity.generated.cs"
_TOKEN = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*.*?\*/)|"
    r'(?P<string>\$?@"(?:""|[^"])*"|\$?"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|'
    r"(?P<word>[A-Za-z_][A-Za-z_0-9]*)|(?P<space>\s+)|(?P<symbol>.)",
    re.DOTALL,
)
_MODIFIERS = frozenset(
    "public internal private protected static sealed abstract partial unsafe".split()
)


def declarations(source: str) -> dict[str, str]:
    """Read the constrained top-level handler declaration shape, ignoring lookalikes."""
    tokens = [
        (match.lastgroup, match.group())
        for match in _TOKEN.finditer(source)
        if match.lastgroup not in {"comment", "space"}
    ]
    namespaces = []
    for index, (_, value) in enumerate(tokens):
        if value != "namespace" or (index > 0 and tokens[index - 1][1] == "@"):
            continue
        cursor, parts = index + 1, []
        while cursor < len(tokens) and tokens[cursor][1] not in {"{", ";"}:
            parts.append(tokens[cursor][1])
            cursor += 1
        namespaces.append(
            ("".join(parts), 0 if cursor < len(tokens) and tokens[cursor][1] == ";" else 1)
        )
    owners = {}
    depth = 0
    for index, (kind, value) in enumerate(tokens):
        if kind == "symbol" and value == "{":
            depth += 1
        elif kind == "symbol" and value == "}":
            depth -= 1
        if (
            value != "["
            or index + 3 >= len(tokens)
            or tokens[index + 1][1]
            not in {
                "McpForUnityTool",
                "McpForUnityToolAttribute",
                "McpForUnityResource",
                "McpForUnityResourceAttribute",
            }
            or tokens[index + 2][1] != "("
            or tokens[index + 3][0] != "string"
        ):
            continue
        literal = tokens[index + 3][1]
        if not literal.startswith('"') or re.fullmatch(r'"[a-z][a-z0-9_]*"', literal) is None:
            continue
        command = literal[1:-1]
        cursor, brackets = index, 0
        while cursor < len(tokens):
            token = tokens[cursor][1]
            if token == "[":
                brackets += 1
            elif token == "]":
                brackets -= 1
                if brackets == 0:
                    cursor += 1
                    break
            cursor += 1
        while cursor < len(tokens) and tokens[cursor][1] in _MODIFIERS:
            cursor += 1
        if cursor + 1 >= len(tokens) or tokens[cursor][1] != "class":
            continue
        if len(namespaces) != 1 or depth != namespaces[0][1]:
            raise ValueError("Audited handlers require one namespace and a top-level declaration")
        owner = namespaces[0][0] + "." + tokens[cursor + 1][1]
        if command in owners:
            raise ValueError(f"Duplicate command declaration: {command}")
        owners[command] = owner
    return owners


def reviewed_native_names() -> frozenset[str]:
    """Enumerate existing action inventory; effects come only from the policy resolver."""
    inherited = {}
    for feature in CAPABILITY_ALIASES.values():
        inherited.setdefault(feature.source, set()).update(feature.native_actions)
    names = {"batch_execute", "batch_validate", "batch_apply_preview"}
    for name in _NATIVE_COMMANDS:
        feature = CAPABILITY_ALIASES.get(name)
        actions = (
            set(feature.native_actions)
            if feature is not None
            else {action for values in _ACTIONS.get(name, {}).values() for action in values}
            | inherited.get(name, set())
        )
        for action in actions or (None,):
            params = {} if action is None else {"action": action}
            if resolve_operation_policy(name, params, native=True).mode != OperationMode.EXCLUSIVE:
                names.add(name)
                break
    return frozenset(names)


def canonical_owners(repo: Path) -> dict[str, str]:
    """Bind reviewed operations only to declarations shipped under the package Editor."""
    reviewed = reviewed_native_names()
    owners = {}
    for path in sorted((repo / "MCPForUnity/Editor").rglob("*.cs")):
        # Separate optional assemblies are intentionally excluded from this assembly identity.
        current = path.parent
        while current != repo / "MCPForUnity/Editor" and not any(current.glob("*.asmdef")):
            current = current.parent
        if current != repo / "MCPForUnity/Editor":
            continue
        source = path.read_text(encoding="utf-8")
        for command, owner in declarations(source).items():
            if command not in reviewed:
                continue
            if command in owners and owners[command] != owner:
                raise ValueError(f"Ambiguous package owner for {command}")
            owners[command] = owner
    return dict(sorted(owners.items()))


def render() -> str:
    owners = canonical_owners(ROOT)
    rows = "\n".join(
        f"            {{ {json.dumps(name)}, {json.dumps(owner)} }},"
        for name, owner in owners.items()
    )
    return TEMPLATE.replace("@@OWNERS@@", rows)


TEMPLATE = r"""// <auto-generated />
// Run: python tools/generate_operation_identity.py
using System;
using System.Collections.Generic;

namespace MCPForUnity.Editor.Services
{
    /// <summary>Canonical package owners only; registry method/delegate identity must also be sealed.</summary>
    internal static class ToolOperationIdentity
    {
        private static readonly Dictionary<string, string> Owners = new Dictionary<string, string>(StringComparer.Ordinal)
        {
@@OWNERS@@
        };

        internal static bool IsCanonicalHandler(string commandName, Type registeredSource)
        {
            if (commandName == null || registeredSource == null || !Owners.TryGetValue(commandName, out string owner))
                return false;
            // Resolve the Type from this exact package assembly, not an assembly name
            // or the caller's SourceType/namespace/annotations. Optional owners are absent.
            Type canonical = typeof(ToolOperationIdentity).Assembly.GetType(owner, false, false);
            return canonical != null && ReferenceEquals(canonical, registeredSource);
        }
    }
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != expected:
            print("Native operation identities are stale. Run tools/generate_operation_identity.py")
            return 1
    else:
        TARGET.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Verified {len(canonical_owners(ROOT))} canonical native operation owners.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
