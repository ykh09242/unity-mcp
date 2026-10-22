"""Build-context filtering using compiler-provided defines, without executing code."""

from __future__ import annotations

import re

_WORD = re.compile(r"[^\W\d]\w*", re.UNICODE)
_CONDITION = re.compile(r"\s*(&&|\|\||==|!=|[!()]|[^\W\d]\w*)", re.UNICODE)
_PRECEDENCE = {"||": 1, "&&": 2, "==": 3, "!=": 3, "!": 4}


def condition(text: str, defines: set[str]) -> bool:
    """Evaluate C# conditional symbols with an iterative boolean parser."""
    values, operators = [], []
    offset, operand = 0, True

    def apply():
        operator = operators.pop()
        if operator == "!":
            values.append(not values.pop())
        else:
            right, left = values.pop(), values.pop()
            values.append(
                left and right
                if operator == "&&"
                else left or right
                if operator == "||"
                else left == right
                if operator == "=="
                else left != right
            )

    text = text.split("//", 1)[0].strip()
    try:
        while offset < len(text):
            match = _CONDITION.match(text, offset)
            if match is None:
                raise ValueError("Unsupported conditional expression")
            token, offset = match[1], match.end()
            if operand:
                if token in {"!", "("}:
                    operators.append(token)
                    continue
                if _WORD.fullmatch(token) is None:
                    raise ValueError("A conditional symbol is required")
                values.append(token == "true" or token != "false" and token in defines)
                operand = False
            elif token == ")":
                while operators and operators[-1] != "(":
                    apply()
                if not operators:
                    raise ValueError("Unmatched conditional parenthesis")
                operators.pop()
            elif token in _PRECEDENCE and token != "!":
                while (
                    operators
                    and operators[-1] != "("
                    and _PRECEDENCE[operators[-1]] >= _PRECEDENCE[token]
                ):
                    apply()
                operators.append(token)
                operand = True
            else:
                raise ValueError("A conditional operator is required")
        if operand:
            raise ValueError("Incomplete conditional expression")
        while operators:
            if operators[-1] == "(":
                raise ValueError("Unmatched conditional parenthesis")
            apply()
        if len(values) != 1:
            raise ValueError("Invalid conditional expression")
        return values[0]
    except IndexError as error:
        raise ValueError("Invalid conditional expression") from error


def _lexical_state(line: str, state: str) -> str:
    """Only active source can start a multi-line literal or comment."""
    offset = 0
    while offset < len(line):
        if state == "/*":
            end = line.find("*/", offset)
            if end < 0:
                return state
            offset, state = end + 2, ""
        elif state.startswith('"""'):
            end = line.find(state, offset)
            if end < 0:
                return state
            offset, state = end + len(state), ""
        elif state in {'"', "'", '@"'}:
            quote = state[-1]
            if line[offset] == quote:
                if state == '@"' and line.startswith('""', offset):
                    offset += 2
                    continue
                state = ""
            elif line[offset] == "\\" and state != '@"':
                offset += 1
            offset += 1
        elif line.startswith("//", offset):
            return ""
        elif line.startswith("/*", offset):
            state, offset = "/*", offset + 2
        elif line[offset] in "$@\"'" and (
            match := re.match(r'(?:\$+@?|@\$?)?("{3,}|"|\')', line[offset:])
        ):
            marker = match[1]
            state = '@"' if marker == '"' and "@" in match[0] else marker
            offset += len(match[0])
        else:
            offset += 1
    return state


def source_for_build(source: str, context: dict | None) -> tuple[str, dict, tuple[dict, ...]]:
    """Preserve source lines and disclose fallback/inactive coverage explicitly."""
    if context is not None and not isinstance(context, dict):
        raise ValueError("Invalid native compilation context")
    context = context or {}
    build = {
        key: value
        for key, value in context.items()
        if key
        in {
            "state",
            "assembly",
            "references",
            "revision",
            "binding_revision",
            "target",
            "assemblies_type",
            "error",
        }
    }
    if any(
        not isinstance(value, str) and value is not None
        for key, value in build.items()
        if key != "references"
    ):
        raise ValueError("Invalid native compilation context metadata")
    if "references" in build and (
        not isinstance(build["references"], list)
        or any(not isinstance(value, str) for value in build["references"])
    ):
        raise ValueError("Invalid native assembly references")
    if build.get("binding_revision"):
        # Global membership revisions fence transport pages, not this assembly's
        # semantics. Unchanged assemblies share metadata across membership edits.
        build["revision"] = build["binding_revision"]
    if context.get("state") == "not_in_compilation":
        return "", build, ({"reason": "inactive_build_context", "line": 0},)
    known = context.get("state") == "available"
    supplied = context.get("defines", [])
    if known and (
        not isinstance(context.get("assembly"), str)
        or not context["assembly"]
        or not isinstance(supplied, list)
        or any(not isinstance(value, str) or not _WORD.fullmatch(value) for value in supplied)
        or not isinstance(context.get("references", []), list)
        or any(not isinstance(value, str) for value in context.get("references", []))
    ):
        raise ValueError("Invalid native compilation context")
    if "#" not in source:
        return (
            source,
            build,
            ({"reason": "build_context_unavailable", "line": 0},)
            if context.get("state") == "unavailable"
            else (),
        )
    defines, stack, output = set(supplied) if known else set(), [], []
    active, state, conditional = True, "", False
    for number, line in enumerate(source.splitlines(keepends=True), 1):
        directive = re.match(r"\s*#\s*(\w+)\b(.*)", line) if not state or not active else None
        if directive:
            kind, text = directive[1], directive[2].strip()
            conditional |= kind in {"if", "elif", "else", "endif"}
            if not known:
                output.append(line)
                continue
            if kind == "if":
                selected = condition(text, defines)
                stack.append([active, selected, False])
                active = active and selected
            elif kind in {"elif", "else"}:
                if not stack or stack[-1][2]:
                    raise ValueError(f"Invalid conditional branch at line {number}")
                parent, taken, _ = stack[-1]
                selected = condition(text, defines) if kind == "elif" else True
                active = parent and not taken and selected
                stack[-1][1] |= selected
                stack[-1][2] = kind == "else"
            elif kind == "endif":
                if not stack:
                    raise ValueError(f"Unmatched endif at line {number}")
                active = stack.pop()[0]
            elif kind in {"define", "undef"} and active:
                symbol = text.split("//", 1)[0].strip()
                if not _WORD.fullmatch(symbol) or symbol in {"true", "false"}:
                    raise ValueError(f"Invalid conditional symbol at line {number}")
                if kind == "define":
                    defines.add(symbol)
                else:
                    defines.discard(symbol)
            output.append("".join(char if char in "\r\n" else " " for char in line))
        elif active:
            output.append(line)
            state = _lexical_state(line, state)
        else:
            output.append("".join(char if char in "\r\n" else " " for char in line))
    if known and stack:
        raise ValueError("Unclosed conditional compilation block")
    issues = (
        ({"reason": "build_context_unavailable", "line": 0},)
        if not known and (conditional or context.get("state") == "unavailable")
        else ()
    )
    return "".join(output), build, issues
