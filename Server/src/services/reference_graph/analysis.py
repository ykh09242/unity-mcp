"""No-install, conservative C# call/constant analysis.

This is a token/call analyzer, not a C# compiler. It never executes source or
claims absence of references. Overloads, dynamic expressions, cycles and absent
keys retain explicit uncertainty. Summaries propagate literal/parameter string
composition through wrappers without rereading their source at every call site.
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
import re

import yaml
from yaml.tokens import (
    BlockMappingStartToken,
    BlockSequenceStartToken,
    BlockEndToken,
    FlowMappingStartToken,
    FlowMappingEndToken,
    FlowSequenceStartToken,
    FlowSequenceEndToken,
    KeyToken,
    ScalarToken,
    ValueToken,
)

from services.reference_graph.compilation import source_for_build
from services.reference_graph.store import Edge

Expr = tuple[tuple[str, str], ...]
UNKNOWN: Expr = (("unknown", ""),)
ANALYSIS_VERSION = 7
# Limit both the semantic value and nonliteral representation before allocating
# expanded storage. Source size alone cannot bound repeated constant expansion.
MAX_EXPRESSION_CHARS = 65536
MAX_EXPRESSION_ATOMS = 4096


def _compose(expressions: Iterable[Expr]) -> Expr:
    output, literals = [], []
    size, invalid = 0, False
    for expression in expressions:
        for kind, value in expression:
            if invalid:
                continue
            size += len(value)
            if kind == "unknown" or size > MAX_EXPRESSION_CHARS:
                invalid = True
                output.clear()
                literals.clear()
            elif kind == "literal":
                if value:
                    literals.append(value)
            else:
                if len(output) + bool(literals) + 1 > MAX_EXPRESSION_ATOMS:
                    invalid = True
                    output.clear()
                    literals.clear()
                    continue
                if literals:
                    output.append(("literal", "".join(literals)))
                    literals.clear()
                output.append((kind, value))
    if invalid or len(output) + bool(literals) > MAX_EXPRESSION_ATOMS:
        return UNKNOWN
    if literals or not output:
        output.append(("literal", "".join(literals)))
    return tuple(output)


# Registry version follows the fact/analysis version. Values describe the string
# key overload; object/location/AssetReference expressions stay unresolved unless
# a later semantic provider can prove their identity.
LOADERS = {
    "UnityEngine.Resources.Load": ("resources", "single"),
    "UnityEngine.Resources.LoadAsync": ("resources", "single"),
    "UnityEngine.Resources.LoadAll": ("resources", "all"),
    "UnityEngine.AddressableAssets.Addressables.LoadAssetAsync": ("addressables", "single"),
    "UnityEngine.AddressableAssets.Addressables.LoadAssetsAsync": ("addressables", "all"),
    "UnityEngine.AddressableAssets.Addressables.InstantiateAsync": ("addressables", "single"),
    "UnityEngine.AddressableAssets.Addressables.LoadSceneAsync": ("addressables", "single"),
}
_LOADER_METHODS = frozenset(name.rpartition(".")[2] for name in LOADERS)
_IDENT = re.compile(r"@?[^\W\d]\w*", re.UNICODE)
_GUID = re.compile(r"^[0-9a-fA-F]{32}$")
_CONTROL = {
    "if",
    "while",
    "for",
    "foreach",
    "switch",
    "catch",
    "using",
    "lock",
    "typeof",
    "sizeof",
    "nameof",
    "default",
    "checked",
    "unchecked",
}


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    line: int
    kind: str = "symbol"
    value: str | None = None


def _string_value(value: str) -> str | None:
    escapes = {
        "0": "\0",
        "a": "\a",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "v": "\v",
        "\\": "\\",
        '"': '"',
        "'": "'",
    }
    output = []
    offset = 0
    while offset < len(value):
        char = value[offset]
        offset += 1
        if char != "\\":
            output.append(char)
            continue
        if offset == len(value):
            return None
        char = value[offset]
        offset += 1
        if char in escapes:
            output.append(escapes[char])
        elif char in ("u", "U", "x"):
            count = 4 if char == "u" else 8 if char == "U" else 0
            match = re.match(r"[0-9a-fA-F]{1,4}", value[offset:]) if not count else None
            count = count or (len(match[0]) if match else 0)
            digits = value[offset : offset + count]
            if not count or len(digits) != count or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                return None
            try:
                output.append(chr(int(digits, 16)))
            except ValueError:
                return None
            offset += count
        else:
            return None
    try:
        # C# escapes denote UTF-16 code units; Python keys denote Unicode scalars.
        # Preserve supplementary characters without persisting lone surrogates.
        return "".join(output).encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeError:
        return None


def _tokens(source: str) -> list[Token]:
    result = []
    offset, line = 0, 1
    while offset < len(source):
        start, start_line = offset, line
        char = source[offset]
        if char.isspace():
            offset += 1
            line += char == "\n"
            continue
        if source.startswith("//", offset) or char == "#":
            end = source.find("\n", offset)
            offset = len(source) if end < 0 else end
            continue
        if source.startswith("/*", offset):
            end = source.find("*/", offset + 2)
            offset = len(source) if end < 0 else end + 2
            line += source[start:offset].count("\n")
            continue
        prefix = (
            re.match(r'(?:\$+@?|@\$?)?("{3,}|"|\')', source[offset:]) if char in "$@\"'" else None
        )
        if prefix:
            marker = prefix[1]
            head = prefix[0]
            dynamic, verbatim = "$" in head, "@" in head
            offset += len(head)
            content = offset
            if len(marker) >= 3:
                end = source.find(marker, offset)
                offset = len(source) if end < 0 else end + len(marker)
                value = source[content:end] if end >= 0 else None
                # Multiline raw strings have indentation semantics. Keep them
                # unresolved rather than guessing runtime whitespace.
                if value is not None and "\n" in value:
                    value = None
            else:
                end = -1
                while offset < len(source):
                    if source[offset] == marker:
                        if verbatim and source.startswith(marker * 2, offset):
                            offset += 2
                            continue
                        end = offset
                        offset += 1
                        break
                    if source[offset] == "\\" and not verbatim:
                        offset += 2
                    else:
                        offset += 1
                raw = source[content:end] if end >= 0 else ""
                value = (
                    (raw.replace('""', '"') if verbatim else _string_value(raw))
                    if end >= 0
                    else None
                )
            line += source[start:offset].count("\n")
            result.append(
                Token(
                    source[start:offset],
                    start_line,
                    "string",
                    None if dynamic or marker == "'" else value,
                )
            )
            continue
        match = _IDENT.match(source, offset)
        if match:
            offset = match.end()
            result.append(Token(match[0].lstrip("@"), line, "id"))
            continue
        pair = source[offset : offset + 2]
        if pair in ("=>", "::", "?.", "??"):
            offset += 2
            result.append(Token(pair, line))
        else:
            offset += 1
            result.append(Token(char, line))
    return result


def _pairs(tokens: list[Token]) -> dict[int, int]:
    stack, result = [], {}
    closes = {")": "(", "}": "{", "]": "["}
    for i, token in enumerate(tokens):
        if token.text in ("(", "{", "["):
            stack.append((token.text, i))
        elif token.text in closes and stack and stack[-1][0] == closes[token.text]:
            _, start = stack.pop()
            result[start], result[i] = i, start
    return result


def _split(tokens: list[Token], separator: str) -> list[list[Token]]:
    result, start, depth = [], 0, 0
    for i, token in enumerate(tokens):
        if token.text in ("(", "[", "{", "<"):
            depth += 1
        elif token.text in (")", "]", "}", ">"):
            depth -= 1
        elif token.text == separator and depth == 0:
            result.append(tokens[start:i])
            start = i + 1
    return result + [tokens[start:]] if tokens else []


def _expression(
    tokens: list[Token],
    scope: str,
    parameters: tuple[str, ...],
    locals_: frozenset[str] = frozenset(),
    import_scope: str = "",
) -> Expr:
    while tokens and tokens[0].text == "(" and _pairs(tokens).get(0) == len(tokens) - 1:
        tokens = tokens[1:-1]
    parts = _split(tokens, "+")
    if len(parts) > 1:
        return _compose(
            _expression(part, scope, parameters, locals_, import_scope) for part in parts
        )
    if len(tokens) == 1 and tokens[0].kind == "string":
        return (("literal", tokens[0].value),) if tokens[0].value is not None else UNKNOWN
    if (
        tokens
        and all(
            t.kind == "id" if i % 2 == 0 else t.text in (".", "::") for i, t in enumerate(tokens)
        )
        and len(tokens) % 2
    ):
        name = "".join(t.text for t in tokens)
        if name.partition(".")[0] in locals_:
            return UNKNOWN
        if name in parameters:
            return (("parameter", name),)
        return (("symbol", scope + "|" + name + "|" + import_scope),)
    return UNKNOWN


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    arguments: tuple[Expr, ...]
    line: int
    scope: str
    method: str
    shadowed_receiver: bool = False
    import_scope: str = ""
    argument_names: tuple[str, ...] = ()
    argument_types: tuple[str, ...] = ()
    target_type: str = ""


@dataclass(frozen=True, slots=True)
class Method:
    identity: str
    name: str
    parameters: tuple[str, ...]
    calls: tuple[int, ...]
    parameter_types: tuple[str, ...] = ()
    defaults: tuple[Expr | None, ...] = ()


@dataclass(frozen=True, slots=True)
class Facts:
    owner: str
    path: str
    usings: tuple[str, ...]
    aliases: dict[str, str]
    types: tuple[str, ...]
    constants: dict[str, Expr]
    methods: tuple[Method, ...]
    calls: tuple[Call, ...]
    imports: dict[str, dict] = field(default_factory=dict)
    version: int = ANALYSIS_VERSION
    build: dict = field(default_factory=dict)
    diagnostics: tuple[dict, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> Facts:
        return cls(
            data["owner"],
            data["path"],
            tuple(data["usings"]),
            data["aliases"],
            tuple(data["types"]),
            {key: tuple(tuple(atom) for atom in expr) for key, expr in data["constants"].items()},
            tuple(
                Method(
                    item["identity"],
                    item["name"],
                    tuple(item["parameters"]),
                    tuple(item["calls"]),
                    tuple(item.get("parameter_types", ())),
                    tuple(
                        None if expr is None else tuple(tuple(atom) for atom in expr)
                        for expr in item.get("defaults", ())
                    ),
                )
                for item in data["methods"]
            ),
            tuple(
                Call(
                    item["name"],
                    tuple(tuple(tuple(atom) for atom in expr) for expr in item["arguments"]),
                    item["line"],
                    item["scope"],
                    item["method"],
                    item.get("shadowed_receiver", False),
                    item.get("import_scope", ""),
                    tuple(item.get("argument_names", ())),
                    tuple(item.get("argument_types", ())),
                    item.get("target_type", ""),
                )
                for item in data["calls"]
            ),
            data.get("imports", {}),
            data.get("version", 1),
            data.get("build", {}),
            tuple(data.get("diagnostics", ())),
        )


def parse_csharp(owner: str, path: str, source: str, *, context: dict | None = None) -> Facts:
    source, build, diagnostics = source_for_build(source, context)
    tokens = _tokens(source)
    pairs = _pairs(tokens)
    scopes = [""] * len(tokens)
    import_scopes = [""] * len(tokens)
    imports = {"": {"usings": [], "statics": [], "aliases": {}, "alias_scopes": {}}}
    import_parents = {}
    active_imports = []
    types, usings, aliases, constants = [], [], {}, {}
    active: list[tuple[int, str, str]] = []
    for i, token in enumerate(tokens):
        while active and active[-1][0] <= i:
            active.pop()
        while active_imports and active_imports[-1][0] <= i:
            active_imports.pop()
        import_scope = active_imports[-1][1] if active_imports else ""
        import_scopes[i] = import_scope
        scope = active[-1][1] if active else ""
        scopes[i] = scope
        if token.text in ("namespace", "class", "struct", "record") and i + 1 < len(tokens):
            end = i + 1
            while end < len(tokens) and tokens[end].text not in ("{", ";", "(", ":", "<"):
                end += 1
            name = "".join(t.text for t in tokens[i + 1 : end])
            if not name:
                continue
            full = (scope + "." if scope else "") + name
            while end < len(tokens) and tokens[end].text not in ("{", ";"):
                end += 1
            if end < len(tokens):
                stop = pairs.get(end, len(tokens)) if tokens[end].text == "{" else len(tokens)
                active.append((stop, full, token.text))
                if token.text != "namespace":
                    types.append(full)
                else:
                    identity = str(i)
                    imports[identity] = {
                        "usings": [],
                        "statics": [],
                        "aliases": {},
                        "alias_scopes": {},
                    }
                    import_parents[identity] = import_scope
                    active_imports.append((stop, identity))
        elif token.text == "using" and (not active or active[-1][2] == "namespace"):
            end = i + 1
            while end < len(tokens) and tokens[end].text not in (";", "(", "{"):
                end += 1
            parts = tokens[i + 1 : end]
            if end < len(tokens) and tokens[end].text == ";" and parts:
                value = "".join(t.text for t in parts)
                if "=" in value:
                    name, target = value.split("=", 1)
                    imports[import_scope]["aliases"][name] = target
                    imports[import_scope]["alias_scopes"][name] = scope
                elif parts[0].text == "static":
                    imports[import_scope]["statics"].append("".join(t.text for t in parts[1:]))
                else:
                    imports[import_scope]["usings"].append(value)
                if i and tokens[i - 1].text == "global":
                    diagnostics += ({"reason": "global_using_not_propagated", "line": token.line},)
    # Each namespace occurrence has its own import scope, including reopened
    # namespaces with the same qualified name. Imports are inherited lexically.
    for identity, parent in import_parents.items():
        imports[identity] = {
            "usings": list(
                dict.fromkeys([*imports[parent]["usings"], *imports[identity]["usings"]])
            ),
            "aliases": {**imports[parent]["aliases"], **imports[identity]["aliases"]},
            "statics": list(
                dict.fromkeys([*imports[parent]["statics"], *imports[identity]["statics"]])
            ),
            "alias_scopes": {
                **imports[parent]["alias_scopes"],
                **imports[identity]["alias_scopes"],
            },
        }
    usings, aliases = imports[""]["usings"], imports[""]["aliases"]
    candidates = []
    declarations = {}
    for i, token in enumerate(tokens):
        if token.text != "(" or i not in pairs:
            continue
        name_end = i - 1
        if name_end >= 0 and tokens[name_end].text == ">":
            depth = 1
            name_end -= 1
            while name_end >= 0 and depth:
                depth += (tokens[name_end].text == ">") - (tokens[name_end].text == "<")
                name_end -= 1
        if name_end < 0 or tokens[name_end].kind != "id" or tokens[name_end].text in _CONTROL:
            continue
        start = name_end
        while (
            start >= 2
            and tokens[start - 1].text in (".", "::", "?.")
            and tokens[start - 2].kind == "id"
        ):
            start -= 2
        name = "".join(t.text for t in tokens[start : name_end + 1])
        end = pairs[i]
        after = end + 1
        if after < len(tokens) and tokens[after].text == "where":
            while after < len(tokens) and tokens[after].text not in ("{", "=>", ";"):
                after += 1
        is_definition = (
            after < len(tokens) and tokens[after].text in ("{", "=>") and "." not in name
        )
        if is_definition:
            parameters, parameter_types, defaults = [], [], []
            for part in _split(tokens[i + 1 : end], ","):
                pieces = _split(part, "=")
                before_default = pieces[0]
                names = [t.text for t in before_default if t.kind == "id"]
                parameters.append(names[-1] if names else "?")
                parameter_types.append("".join(t.text for t in before_default[:-1]))
                defaults.append(
                    _expression(pieces[1], scopes[i], (), import_scope=import_scopes[i])
                    if len(pieces) == 2
                    else None
                )
            stop = pairs.get(after, after)
            if tokens[after].text == "=>":
                stop = after + 1
                while stop < len(tokens) and tokens[stop].text != ";":
                    stop += 1
            identity = owner + ":" + str(i)
            declarations[i] = (
                identity,
                (scopes[i] + "." if scopes[i] else "") + name,
                tuple(parameters),
                after,
                stop,
                tuple(parameter_types),
                tuple(defaults),
            )
        else:
            candidates.append((i, name, start, end))
    method_ranges = sorted(declarations.values(), key=lambda item: item[3])
    local_names = {}
    inside_method = [False] * len(tokens)
    for method in method_ranges:
        for i in range(method[3] + 1, min(method[4], len(tokens))):
            inside_method[i] = True
        local_names[method[0]] = frozenset(
            tokens[i].text
            for i in range(method[3] + 1, min(method[4], len(tokens) - 1))
            if tokens[i].kind == "id"
            and (tokens[i - 1].kind == "id" or tokens[i - 1].text in (">", "]", "?", "*", ",", ")"))
            and tokens[i - 1].text not in ("return", "throw", "await", "new")
            and tokens[i + 1].text in ("=", ";", ",", ")", "=>")
        )
        tainted = set()
        for i in range(method[3] + 1, min(method[4], len(tokens) - 2)):
            if tokens[i].text in method[2] and (
                tokens[i - 1].text in ("ref", "out")
                or tokens[i + 1].text == "="
                and tokens[i + 2].text != "="
                or tokens[i + 1].text in ("+", "-", "?", "??")
                and tokens[i + 2].text == "="
            ):
                tainted.add(tokens[i].text)
        local_names[method[0]] |= frozenset(tainted)
        for i in range(method[3] + 1, min(method[4], len(tokens) - 1)):
            if tokens[i].text == "=" and tokens[i - 1].text == ")" and tokens[i + 1].text != "=":
                start = pairs.get(i - 1, i)
                tainted.update(
                    token.text for token in tokens[start + 1 : i - 1] if token.text in method[2]
                )
        local_names[method[0]] |= frozenset(tainted)
    fields = defaultdict(set)
    parameter_positions = {
        position for start in declarations for position in range(start, pairs[start] + 1)
    }
    for i in range(1, len(tokens) - 1):
        if (
            not inside_method[i]
            and i not in parameter_positions
            and tokens[i].kind == "id"
            and (tokens[i - 1].kind == "id" or tokens[i - 1].text in (">", "]", "?", "*", ",", ")"))
            and tokens[i - 1].text
            not in (
                "return",
                "throw",
                "await",
                "new",
                "using",
                "namespace",
                "class",
                "struct",
                "record",
            )
            and tokens[i + 1].text in ("=", ";", "{", "=>", ",")
            and not (i >= 2 and tokens[i - 2].text == "const")
        ):
            fields[scopes[i]].add(tokens[i].text)
    calls, method_calls = [], defaultdict(list)
    active_methods = []
    cursor = 0
    for i, name, start, end in candidates:
        while cursor < len(method_ranges) and method_ranges[cursor][3] < i:
            active_methods.append(method_ranges[cursor])
            cursor += 1
        while active_methods and active_methods[-1][4] < i:
            active_methods.pop()
        method = active_methods[-1] if active_methods else None
        parameters = method[2] if method else ()
        identity = method[0] if method else ""
        locals_ = local_names.get(identity, frozenset()) | fields[scopes[i]]
        receiver = name.partition(".")[0]
        argument_parts, argument_names = [], []
        for part in _split(tokens[i + 1 : end], ","):
            named = len(part) >= 2 and part[0].kind == "id" and part[1].text == ":"
            argument_names.append(part[0].text if named else "")
            argument_parts.append(part[2:] if named else part)
        parameter_types = dict(zip(parameters, method[5])) if method else {}
        call = Call(
            name,
            tuple(
                _expression(part, scopes[i], parameters, locals_, import_scopes[i])
                for part in argument_parts
            ),
            tokens[start].line,
            scopes[i],
            identity,
            "." in name
            and receiver in (locals_ | set(parameters))
            and tokens[start].text != "global",
            import_scopes[i],
            tuple(argument_names),
            tuple(_argument_type(part, parameter_types) for part in argument_parts),
            _generic_type(tokens, i),
        )
        method_calls[identity].append(len(calls))
        calls.append(call)
    for i, token in enumerate(tokens):
        if token.text != "const" or i + 3 >= len(tokens) or tokens[i + 1].text != "string":
            continue
        name, end = tokens[i + 2].text, i + 3
        if tokens[end].text != "=":
            continue
        end += 1
        stop = end
        while stop < len(tokens) and tokens[stop].text != ";":
            stop += 1
        # Local declarations do not become class-wide constants.
        if any(m[3] < i < m[4] for m in method_ranges):
            continue
        constants[(scopes[i] + "." if scopes[i] else "") + name] = _expression(
            tokens[end:stop], scopes[i], (), import_scope=import_scopes[i]
        )
    methods = tuple(
        Method(m[0], m[1], m[2], tuple(method_calls[m[0]]), m[5], m[6]) for m in method_ranges
    )
    return Facts(
        owner,
        path,
        tuple(usings),
        aliases,
        tuple(types),
        constants,
        methods,
        tuple(calls),
        imports,
        build=build,
        diagnostics=diagnostics,
    )


def _generic_type(tokens: list[Token], opening: int) -> str:
    if not opening or tokens[opening - 1].text != ">":
        return ""
    depth, start = 1, opening - 2
    while start >= 0:
        depth += (tokens[start].text == ">") - (tokens[start].text == "<")
        if not depth:
            parts = tokens[start + 1 : opening - 1]
            return "".join(token.text for token in parts) if len(_split(parts, ",")) == 1 else ""
        start -= 1
    return ""


def _argument_type(tokens: list[Token], parameters: dict[str, str]) -> str:
    if len(tokens) == 1:
        token = tokens[0]
        if token.kind == "string":
            return "char" if token.text.startswith("'") else "string"
        if token.text in {"true", "false"}:
            return "bool"
        if token.text in parameters:
            return parameters[token.text]
    literal = re.fullmatch(
        r"([+-]?)(0[xX][0-9a-fA-F_]+|0[bB][01_]+|[0-9][0-9_]*)([uU][lL]?|[lL][uU]?)?",
        "".join(token.text for token in tokens),
    )
    if literal:
        sign, digits, suffix = literal.groups()
        suffix = (suffix or "").lower()
        digits = digits.replace("_", "")
        base = (
            16 if digits.lower().startswith("0x") else 2 if digits.lower().startswith("0b") else 10
        )
        digits = digits[2:] if base != 10 else digits
        # A C# integral literal has at most 64 significant binary digits.
        # Avoid parsing an arbitrarily long invalid token into a Python bigint.
        digits = digits.lstrip("0") or "0"
        if len(digits) > {16: 16, 2: 64, 10: 20}[base]:
            return "invalid_numeric_literal"
        number = int(digits, base)
        candidates = (
            (("ulong", 64),)
            if "u" in suffix and "l" in suffix
            else (("uint", 32), ("ulong", 64))
            if suffix == "u"
            else (("long", 63), ("ulong", 64))
            if suffix == "l"
            else (("int", 31), ("uint", 32), ("long", 63), ("ulong", 64))
        )
        result = next(
            (name for name, bits in candidates if number < 1 << bits), "invalid_numeric_literal"
        )
        if sign == "-":
            if not suffix and number == 1 << 31:
                return "int"
            if suffix in ("", "l") and number == 1 << 63:
                return "long"
            return (
                "long"
                if result == "uint"
                else "invalid_numeric_literal"
                if result == "ulong"
                else result
            )
        return result
    return ""


class Catalog:
    @staticmethod
    def canonical_key(scheme: str, value: str) -> str:
        if scheme == "resources":
            # Avoid full Unicode folding that conflates different-length names
            # such as sharp-s and SS. Paths/Addressables retain exact spelling.
            return "".join(folded if len(folded := char.upper()) == 1 else char for char in value)
        return value

    def __init__(self, assets: Iterable[dict]):
        self.keys: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.guids: set[str] = set()
        self.blocked: set[tuple[str, str]] = set()
        self.addressables_unavailable = False
        for asset in assets:
            if asset.get("_excluded"):
                self.blocked.update(
                    (key["scheme"], self.canonical_key(key["scheme"], key["value"]))
                    for key in asset.get("keys", ())
                )
                self.blocked.add(("guid", asset["guid"].lower()))
                self.blocked.add(("path", asset["path"]))
                continue
            self.addressables_unavailable |= bool(asset.get("addressables_error")) or asset.get(
                "addressables"
            ) in {"unavailable", "unsupported", "failed"}
            guid = asset["guid"].lower()
            target = "asset:" + guid
            self.guids.add(guid)
            self.keys["guid", guid].add(target)
            self.keys["path", asset["path"]].add(target)
            for key in asset.get("keys", ()):
                self.keys[key["scheme"], self.canonical_key(key["scheme"], key["value"])].add(
                    target
                )

    def resolve(self, scheme: str, key: str, mode: str = "single") -> tuple[set[str], str | None]:
        key = self.canonical_key(scheme, key)
        targets = set(self.keys.get((scheme, key), ()))
        if scheme == "resources" and mode == "all":
            prefix = key.rstrip("/") + "/" if key else ""
            targets.update(
                target
                for (kind, value), ids in self.keys.items()
                if kind == scheme and (value.startswith(prefix) or value == key)
                for target in ids
            )
        if scheme == "addressables":
            targets.update(self.keys.get(("addressables_label", key), ()))
        if not targets:
            if (scheme, key) in self.blocked or (
                scheme == "addressables" and ("addressables_label", key) in self.blocked
            ):
                return set(), "excluded"
            if scheme in {"addressables", "addressables_label"} and self.addressables_unavailable:
                return set(), "provider_unavailable"
            return set(), "key_not_found"
        if len(targets) > 1 and mode == "single":
            return set(), "ambiguous_key"
        return targets, None

    def resolve_detail(
        self, scheme: str, key: str, mode: str = "single"
    ) -> tuple[set[str], str | None, str]:
        targets, reason = self.resolve(scheme, key, mode)
        if scheme == "addressables" and reason in {"key_not_found", "provider_unavailable"}:
            match = re.fullmatch(r"([^\[\]]+)\[([^\[\]]+)\]", key)
            if match:
                targets, reason = self.resolve(scheme, match[1], mode)
                return targets, reason, match[2]
        return targets, reason, ""


@dataclass(frozen=True, slots=True)
class Effect:
    scheme: str
    mode: str
    expression: Expr
    evidence: str
    target_type: str = ""


@dataclass(slots=True)
class Analysis:
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    dependencies: set[str] = field(default_factory=set)
    coverage: str = "partial_static"


def validate_rule(rule: dict) -> None:
    if not isinstance(rule, dict) or not isinstance(rule.get("call"), str) or not rule["call"]:
        raise ValueError("Wrapper rules require a qualified call name")
    if rule.get("scheme") not in ("resources", "addressables", "guid", "path") or rule.get(
        "mode", "single"
    ) not in ("single", "all"):
        raise ValueError("Invalid wrapper rule scheme or mode")
    if type(rule.get("argument", 0)) is not int or rule.get("argument", 0) < 0:
        raise ValueError("Wrapper argument must be a nonnegative index")
    if not all(isinstance(rule.get(key, ""), str) for key in ("prefix", "suffix")):
        raise ValueError("Wrapper prefix and suffix must be strings")


class AnalysisIndex:
    def __init__(
        self,
        facts: Iterable[Facts],
        catalog: Catalog,
        rules: Iterable[dict] = (),
        *,
        check: Callable[[], None] = lambda: None,
    ):
        self.check = check
        self.facts: dict[str, Facts] = {}
        self._contexts = {}
        self._visibility = {}
        self.catalog = catalog
        self.rules = {}
        for rule in rules:
            validate_rule(rule)
            if rule["call"] in self.rules:
                raise ValueError("Duplicate wrapper rule")
            self.rules[rule["call"]] = rule
        self.constants: dict[str, list[tuple[str, Expr]]] = defaultdict(list)
        self.methods: dict[str, list[tuple[str, Method]]] = defaultdict(list)
        self.method_ids = {}
        self.types = defaultdict(set)
        self.namespaces = defaultdict(set)
        self.dependencies: dict[str, set[str]] = defaultdict(set)
        self.reverse: dict[str, set[str]] = defaultdict(set)
        self.lookups: dict[str, set[str]] = defaultdict(set)
        self.symbol_users: dict[str, set[str]] = defaultdict(set)
        self.constant_cache: dict[str, dict[tuple, Expr]] = defaultdict(dict)
        self.resolutions: dict[str, tuple] = {}
        self.unknown_summaries: dict[str, tuple | None] = {}
        for item in facts:
            self.check()
            self._add(item)
        self._prepare(set(self.facts))

    @staticmethod
    def _prefixes(item: Facts) -> set[str]:
        names = set()
        for name in item.types:
            while name := name.rpartition(".")[0]:
                names.add(name)
        return names

    @staticmethod
    def _symbols(item: Facts) -> set[str]:
        return {
            *item.types,
            *item.constants,
            *(method.name for method in item.methods),
            *AnalysisIndex._prefixes(item),
        }

    def _add(self, item: Facts) -> None:
        context_key = self._context_key(item.build)
        if context_key not in self._contexts:
            self._contexts[context_key] = [
                item.build,
                0,
                frozenset(item.build.get("references", ())),
            ]
        context = self._contexts[context_key]
        context[1] += 1
        if item.build and item.build is not context[0]:
            item = replace(item, build=context[0])
        self._visibility[item.owner] = context[2]
        self.facts[item.owner] = item
        for name in item.types:
            self.types[name].add(item.owner)
        for name in self._prefixes(item):
            self.namespaces[name].add(item.owner)
        for name, expr in item.constants.items():
            self.constants[name].append((item.owner, expr))
        for method in item.methods:
            self.methods[method.name].append((item.owner, method))
            self.method_ids[method.identity] = (item, method)

    def _remove(self, owner: str) -> None:
        item = self.facts.pop(owner, None)
        if item is None:
            return
        context_key = self._context_key(item.build)
        self._contexts[context_key][1] -= 1
        if not self._contexts[context_key][1]:
            del self._contexts[context_key]
        self._visibility.pop(owner, None)
        for name in item.types:
            self.types[name].discard(owner)
            if not self.types[name]:
                del self.types[name]
        for name in self._prefixes(item):
            self.namespaces[name].discard(owner)
            if not self.namespaces[name]:
                del self.namespaces[name]
        for mapping, names in (
            (self.constants, item.constants),
            (self.methods, {m.name for m in item.methods}),
        ):
            for name in names:
                mapping[name] = [row for row in mapping[name] if row[0] != owner]
                if not mapping[name]:
                    del mapping[name]
        for method in item.methods:
            self.method_ids.pop(method.identity, None)
            self.unknown_summaries.pop(method.identity, None)

    def _depend(self, owner: str, dependencies: Iterable[str]) -> None:
        for dependency in dependencies:
            self.dependencies[owner].add(dependency)
            self.reverse[dependency].add(owner)

    def _prepare(self, owners: set[str]) -> None:
        # Clear before rebinding any owner: constant resolution can populate a
        # different owner's dependencies while walking a cross-file expression.
        for owner in owners:
            self.check()
            for dependency in self.dependencies.pop(owner, ()):
                self.reverse[dependency].discard(owner)
                if not self.reverse[dependency]:
                    del self.reverse[dependency]
            for name in self.lookups.pop(owner, ()):
                self.symbol_users[name].discard(owner)
                if not self.symbol_users[name]:
                    del self.symbol_users[name]
            self.constant_cache.pop(owner, None)
            self.resolutions.pop(owner, None)
        for owner in sorted(owners & self.facts.keys()):
            self.check()
            item = self.facts[owner]
            resolved = []
            for call in item.calls:
                self.check()
                resolved.append(self._resolve_call(item, call))
                for expression in call.arguments:
                    self._expand(item, expression, {})
            self.resolutions[owner] = tuple(resolved)
        self._prepare_unknown(owners)

    def _prepare_unknown(self, owners: set[str]) -> None:
        # Cache only tiny unknown-input outcomes. A large/concrete/recursive
        # branch stays lazy; no transitive expression or result list is expanded.
        identities = {
            method.identity
            for owner in owners & self.facts.keys()
            for method in self.facts[owner].methods
        }
        waiting, reverse = {}, defaultdict(set)
        for identity in identities:
            self.check()
            item, method = self.method_ids[identity]
            children = {self.resolutions[item.owner][i][1] for i in method.calls} & identities
            waiting[identity] = children
            self.unknown_summaries.pop(identity, None)
            for child in children:
                reverse[child].add(identity)
        ready = deque(identity for identity, children in waiting.items() if not children)
        while ready:
            self.check()
            identity = ready.popleft()
            item, method = self.method_ids[identity]
            arguments = dict.fromkeys(method.parameters, UNKNOWN)
            values, reusable = set(), True
            for index in method.calls:
                direct, child, issue, bindings = self.resolutions[item.owner][index]
                if issue:
                    values.add(issue)
                values.update(
                    Effect(
                        e.scheme,
                        e.mode,
                        self._bound(item, e.expression, arguments),
                        "",
                        e.target_type,
                    )
                    for e in direct
                )
                if child:
                    bound = [self._bound(item, expr, arguments) for expr in bindings]
                    summary = self.unknown_summaries.get(child)
                    if summary is None or any(value != UNKNOWN for value in bound):
                        reusable = False
                        break
                    values.update(summary)
                if len(values) > 4:
                    reusable = False
                    break
            self.unknown_summaries[identity] = tuple(sorted(values, key=repr)) if reusable else None
            for parent in reverse[identity]:
                waiting[parent].discard(identity)
                if not waiting[parent]:
                    ready.append(parent)
        for identity in identities - self.unknown_summaries.keys():
            self.unknown_summaries[identity] = None

    def update(self, facts: Iterable[Facts], *, removed: Iterable[str] = ()) -> set[str]:
        changes = {item.owner: item for item in facts}
        changed = set(changes) | set(removed)
        symbols = set()
        for owner in changed:
            self.check()
            for item in (self.facts.get(owner), changes.get(owner)):
                if item is not None:
                    symbols.update(self._symbols(item))
        # Missing and ambiguous lookups matter too: a newly added declaration
        # can change a caller that had no previously resolved dependency edge.
        affected = self.affected(
            changed | {owner for name in symbols for owner in self.symbol_users.get(name, ())}
        )
        for owner in changed:
            self._remove(owner)
        for item in changes.values():
            self._add(item)
        self._prepare(affected)
        return affected

    def _names(self, item: Facts, name: str, scope: str, import_scope: str = "") -> list[str]:
        imports = item.imports.get(import_scope, {"aliases": item.aliases, "usings": item.usings})
        if name.startswith("global::"):
            return self._lookup_names(item.owner, [name[len("global::") :]])
        first, _, rest = name.partition(".")
        names = []
        while scope:
            if first in imports["aliases"] and scope == imports.get("alias_scopes", {}).get(first):
                break
            full = scope + "." + name
            names.append(full)
            self._lookup_names(item.owner, [full, scope + "." + first])
            if self._declared(item, scope + "." + first) or self._declared(item, full):
                return [full]
            scope = scope.rpartition(".")[0]
        if first in imports["aliases"]:
            target = imports["aliases"][first]
            alias_scope = imports.get("alias_scopes", {}).get(first, "")
            if not target.startswith("global::"):
                while alias_scope:
                    candidate = alias_scope + "." + target
                    self._lookup_names(item.owner, [candidate])
                    if self._declared(item, candidate):
                        target = candidate
                        break
                    alias_scope = alias_scope.rpartition(".")[0]
            return self._lookup_names(
                item.owner, [target.removeprefix("global::") + ("." + rest if rest else "")]
            )
        # A new global type/namespace can shadow a previously imported name.
        # Track this candidate even before it exists so incremental and cold
        # binding agree after the new source is imported.
        self._lookup_names(item.owner, [first])
        if self._declared(item, first) or self._declared(item, name):
            return self._lookup_names(item.owner, [name, first])[:1]
        return self._lookup_names(
            item.owner,
            list(
                dict.fromkeys(
                    [
                        *names,
                        name,
                        *(
                            using.removeprefix("global::") + "." + name
                            for using in [*imports["usings"], *imports.get("statics", ())]
                        ),
                    ]
                )
            ),
        )

    def _visible(self, item: Facts, owner: str) -> bool:
        build = item.build
        target = self.facts[owner].build
        return (
            build.get("state") != "available"
            or target.get("state") != "available"
            or target.get("assembly") == build.get("assembly")
            or target.get("assembly") in self._visibility[item.owner]
        )

    @staticmethod
    def _context_key(build: dict) -> tuple:
        return tuple(
            (key, tuple(value) if key == "references" else value)
            for key, value in sorted(build.items())
        )

    def _declared(self, item: Facts, name: str) -> bool:
        return (
            any(self._visible(item, owner) for owner in self.types.get(name, ()))
            or any(self._visible(item, owner) for owner in self.namespaces.get(name, ()))
            or any(self._visible(item, owner) for owner, _ in self.methods.get(name, ()))
            or any(self._visible(item, owner) for owner, _ in self.constants.get(name, ()))
        )

    def _lookup_names(self, owner: str, names: list[str]) -> list[str]:
        for name in names:
            self.lookups[owner].add(name)
            self.symbol_users[name].add(owner)
        return names

    def _constant(
        self, item: Facts, scope: str, name: str, active: set[str], import_scope: str = ""
    ) -> Expr:
        self.check()
        key = (scope, name, import_scope)
        if key in self.constant_cache[item.owner]:
            return self.constant_cache[item.owner][key]
        matches = [
            (full, row)
            for full in self._names(item, name, scope, import_scope)
            for row in self.constants.get(full, ())
            if self._visible(item, row[0])
        ]
        self._depend(item.owner, (row[0] for _, row in matches))
        if len(matches) != 1 or matches[0][0] in active:
            return UNKNOWN
        full, (owner, expr) = matches[0]
        self._depend(item.owner, (owner,))
        result = self._expand(self.facts[owner], expr, {}, active | {full})
        self.constant_cache[item.owner][key] = result
        return result

    def _expand(
        self, item: Facts, expr: Expr, arguments: dict[str, Expr], active: set[str] | None = None
    ) -> Expr:
        def pieces():
            for kind, value in expr:
                self.check()
                if kind == "symbol":
                    scope, name, *imports = value.split("|", 2)
                    try:
                        yield self._constant(
                            item, scope, name, active or set(), imports[0] if imports else ""
                        )
                    except RecursionError:
                        yield UNKNOWN
                elif kind == "parameter":
                    yield arguments.get(value, ((kind, value),))
                else:
                    yield ((kind, value),)

        # Consume every piece even after a limit/unknown result, preserving the
        # symbol dependencies needed to invalidate cached uncertainty later.
        return _compose(pieces())

    def _bind(self, item: Facts, call: Call, owner: str, method: Method) -> tuple[Expr, ...] | None:
        if len(call.arguments) > len(method.parameters):
            return None
        supplied = {}
        for index, expression in enumerate(call.arguments):
            name = call.argument_names[index] if call.argument_names else ""
            if name:
                if name not in method.parameters:
                    return None
                target = method.parameters.index(name)
            else:
                target = index
            if target in supplied:
                return None
            argument_type = call.argument_types[index] if call.argument_types else ""
            if not argument_type and self._bound(item, expression, {}) != UNKNOWN:
                argument_type = "string"
            parameter_type = method.parameter_types[target] if method.parameter_types else ""
            canonical = {
                "System.String": "string",
                "System.Int32": "int",
                "System.Int64": "long",
                "System.UInt32": "uint",
                "System.UInt64": "ulong",
                "System.Boolean": "bool",
                "System.Char": "char",
            }
            parameter_type = canonical.get(parameter_type.removeprefix("global::"), parameter_type)
            argument_type = canonical.get(argument_type.removeprefix("global::"), argument_type)
            if argument_type and parameter_type and argument_type != parameter_type:
                return None
            supplied[target] = expression
        bound = []
        for index in range(len(method.parameters)):
            if index in supplied:
                bound.append(supplied[index])
            else:
                default = method.defaults[index] if method.defaults else None
                if default is None:
                    return None
                bound.append(self._expand(self.facts[owner], default, {}))
        return tuple(bound)

    def _resolve_call(
        self, item: Facts, call: Call
    ) -> tuple[list[Effect], str | None, str | None, tuple[Expr, ...]]:
        if call.shadowed_receiver:
            return [], None, "unresolved_receiver", ()
        names = self._names(item, call.name, call.scope, call.import_scope)
        rule_matches = [self.rules[name] for name in names if name in self.rules]
        if len(rule_matches) > 1:
            return [], None, "ambiguous_call", ()
        if rule_matches:
            rule = rule_matches[0]
            index = rule.get("argument", 0)
            expr = (
                call.arguments[index]
                if index < len(call.arguments) and not any(call.argument_names)
                else UNKNOWN
            )
            return (
                [
                    Effect(
                        rule["scheme"],
                        rule.get("mode", "single"),
                        (
                            ("literal", rule.get("prefix", "")),
                            *expr,
                            ("literal", rule.get("suffix", "")),
                        ),
                        "rule:" + rule["call"],
                    )
                ],
                None,
                None,
                (),
            )
        matches = [
            (owner, method, bindings)
            for name in names
            for owner, method in self.methods.get(name, ())
            if self._visible(item, owner)
            and (bindings := self._bind(item, call, owner, method)) is not None
        ]
        if matches:
            self._depend(item.owner, (owner for owner, _, _ in matches))
            if len(matches) != 1:
                return [], None, "ambiguous_call", ()
            owner, method, bindings = matches[0]
            self._depend(item.owner, (owner,))
            return [], method.identity, None, bindings
        if any(
            self._visible(item, owner) for name in names for owner, _ in self.methods.get(name, ())
        ):
            return [], None, "unsupported_call_binding", ()
        qualifier, _, method = call.name.rpartition(".")
        if method not in _LOADER_METHODS:
            return [], None, None, ()
        qualified = self._names(item, qualifier, call.scope, call.import_scope)
        # A project type with the same visible qualifier takes precedence.
        if any(
            self._visible(item, owner) for name in qualified for owner in self.types.get(name, ())
        ):
            self._depend(
                item.owner, (owner for name in qualified for owner in self.types.get(name, ()))
            )
            return [], None, "unresolved_loader", ()
        signatures = {LOADERS[name] for name in names if name in LOADERS}
        if len(signatures) != 1:
            return [], None, "unresolved_loader", ()
        scheme, mode = signatures.pop()
        expression = call.arguments[0] if call.arguments else UNKNOWN
        if any(call.argument_names):
            key_name = (
                "path"
                if scheme == "resources"
                else "keys"
                if method == "LoadAssetsAsync"
                else "key"
            )
            expression = next(
                (
                    expr
                    for index, expr in enumerate(call.arguments)
                    if call.argument_names[index] == key_name
                    or index == 0
                    and not call.argument_names[index]
                ),
                UNKNOWN,
            )
        return (
            [
                Effect(
                    scheme,
                    mode,
                    expression,
                    "call:" + call.name,
                    call.target_type,
                )
            ],
            None,
            None,
            (),
        )

    def affected(self, changed: set[str]) -> set[str]:
        result, pending = set(changed), list(changed)
        while pending:
            self.check()
            for owner in self.reverse.get(pending.pop(), set()) - result:
                result.add(owner)
                pending.append(owner)
        return result

    def _bound(self, item: Facts, expression: Expr, arguments: dict[str, Expr]) -> Expr:
        expression = self._expand(item, expression, arguments)
        if any(kind != "literal" for kind, _ in expression):
            return UNKNOWN
        return (("literal", "".join(value for _, value in expression)),)

    def _effects(self, owner: str, call_index: int) -> Iterator[Effect | str]:
        # Shared local call plans are traversed only with a concrete binding (or
        # one canonical unknown). Frames, not Python recursion, support deep code.
        frames = [("", self.facts[owner], iter((call_index,)), {}, None)]
        active = set()
        seen = OrderedDict()
        unknown_seen = set()
        while frames:
            self.check()
            identity, item, calls, arguments, evidence = frames[-1]
            index = next(calls, None)
            if index is None:
                frames.pop()
                active.discard(identity)
                continue
            call = item.calls[index]
            direct, child, issue, bindings = self.resolutions[item.owner][index]
            if issue:
                yield issue
            for effect in direct:
                yield Effect(
                    effect.scheme,
                    effect.mode,
                    self._bound(item, effect.expression, arguments),
                    evidence or effect.evidence,
                    effect.target_type,
                )
            if not child:
                continue
            if child in active:
                yield "recursive_wrapper"
                continue
            child_item, method = self.method_ids[child]
            values = tuple(self._bound(item, expr, arguments) for expr in bindings)
            state = (child, values)
            symbolic = all(value == UNKNOWN for value in values)
            summary = self.unknown_summaries.get(child) if symbolic else None
            if summary is not None:
                for value in summary:
                    if isinstance(value, str):
                        yield value
                    else:
                        yield Effect(
                            value.scheme,
                            value.mode,
                            value.expression,
                            evidence or "wrapper:" + call.name,
                            value.target_type,
                        )
                continue
            if symbolic and child in unknown_seen:
                continue
            if not symbolic and state in seen:
                seen.move_to_end(state)
                continue
            if symbolic:
                # At most one unknown state per method, bounded by program size.
                unknown_seen.add(child)
            else:
                seen[state] = None
            # This is an evictable memo, not a depth/work/result limit. Eviction
            # permits recomputation; the publication sink deduplicates evidence.
            if len(seen) > 4096:
                seen.popitem(last=False)
            active.add(child)
            frames.append(
                (
                    child,
                    child_item,
                    iter(method.calls),
                    dict(zip(method.parameters, values)),
                    evidence or "wrapper:" + call.name,
                )
            )

    def events(self, owner: str) -> Iterator[Edge | dict]:
        """Stream concrete evidence without retaining a wrapper's entire fanout."""
        self.check()
        yield from self.facts[owner].diagnostics
        for call_index in range(len(self.facts[owner].calls)):
            yield from self._call_events(owner, call_index)

    def _call_events(self, owner: str, call_index: int) -> Iterator[Edge | dict]:
        call = self.facts[owner].calls[call_index]
        issues = set()
        for effect in self._effects(owner, call_index):
            if isinstance(effect, str):
                issues.add(effect)
                continue
            expr = effect.expression
            if any(kind != "literal" for kind, _ in expr):
                issues.add("dynamic_key")
                continue
            key = "".join(value for _, value in expr)
            targets, reason, subobject = self.catalog.resolve_detail(
                effect.scheme, key, effect.mode
            )
            if reason and reason != "excluded":
                yield {"line": call.line, "call": call.name, "reason": reason, "key": key}
            for target in sorted(targets):
                self.check()
                yield Edge(
                    target,
                    effect.scheme + "_load",
                    key,
                    call.line,
                    "possible",
                    effect.evidence,
                    target_type=effect.target_type,
                    subobject=subobject,
                )
        for reason in sorted(issues):
            yield {"line": call.line, "call": call.name, "reason": reason}

    def transitive_dependencies(self, owner: str) -> set[str]:
        # Transitive dependencies ensure a changed constant inside a wrapper also
        # invalidates callers, even when the wrapper's own source is unchanged.
        result = set()
        pending = list(self.dependencies[owner])
        while pending:
            self.check()
            dependency = pending.pop()
            if dependency in result:
                continue
            result.add(dependency)
            pending.extend(self.dependencies[dependency] - result)
        return result

    def analyze(self, owner: str) -> Analysis:
        # Convenience API for bounded component callers. The reconciler consumes
        # events into a disk-backed staging sink for potentially large owners.
        result = Analysis()
        result.unresolved.extend(self.facts[owner].diagnostics)
        for call_index in range(len(self.facts[owner].calls)):
            edges, issues = set(), set()
            for event in self._call_events(owner, call_index):
                if isinstance(event, Edge):
                    if event not in edges:
                        edges.add(event)
                        result.edges.append(event)
                else:
                    key = tuple(sorted(event.items()))
                    if key not in issues:
                        issues.add(key)
                        result.unresolved.append(event)
        result.dependencies = self.transitive_dependencies(owner)
        return result


def serialized_references(source: str, catalog: Catalog) -> Analysis:
    result = Analysis(coverage="lexical_serialized_guid_fields_untyped")
    window = deque(maxlen=4)
    mappings = []
    selected = {"guid", "fileID", "m_AssetGUID", "m_SubObjectName", "m_SubObjectGUID"}

    def finish(fields):
        if not fields:
            return
        for field_name in ("guid", "m_AssetGUID"):
            value = fields.get(field_name)
            if value is None or not _GUID.fullmatch(value[0]) or set(value[0]) == {"0"}:
                continue
            file_id = fields.get("fileID", ("", 0))[0]
            if field_name == "guid" and not re.fullmatch(r"-?[0-9]+", file_id):
                continue
            guid = value[0].lower()
            sub_guid = fields.get("m_SubObjectGUID", ("", 0))[0]
            result.edges.append(
                Edge(
                    "asset:" + guid,
                    "serialized_guid_field"
                    if field_name == "m_AssetGUID"
                    else "serialized_object_reference",
                    guid,
                    value[1],
                    "possible",
                    field_name + "; type_unverified",
                    source_pointer=field_name,
                    target_file_id=file_id if field_name == "guid" else "",
                    subobject=fields.get("m_SubObjectName", ("", 0))[0]
                    if field_name == "m_AssetGUID"
                    else "",
                    subobject_guid=sub_guid.lower()
                    if field_name == "m_AssetGUID" and _GUID.fullmatch(sub_guid)
                    else "",
                )
            )

    # Scan tokens only: never construct tagged objects or expand YAML aliases.
    # Scalar contents, comments and quoted text cannot masquerade as mapping keys.
    try:
        for token in yaml.scan(source, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
            if isinstance(token, (BlockMappingStartToken, FlowMappingStartToken)):
                mappings.append({})
            elif isinstance(token, (BlockSequenceStartToken, FlowSequenceStartToken)):
                mappings.append(None)
            elif isinstance(token, (BlockEndToken, FlowMappingEndToken, FlowSequenceEndToken)):
                if mappings:
                    finish(mappings.pop())
            window.append(token)
            if len(window) != 4:
                continue
            key, field_name, separator, value = window
            if not (
                isinstance(key, KeyToken)
                and isinstance(field_name, ScalarToken)
                and field_name.value in selected
                and isinstance(separator, ValueToken)
                and isinstance(value, ScalarToken)
                and mappings
                and mappings[-1] is not None
            ):
                continue
            fields = mappings[-1]
            if field_name.value in fields:
                raise ValueError("Duplicate serialized identity field")
            fields[field_name.value] = (value.value, field_name.start_mark.line + 1)
    except yaml.YAMLError as error:
        raise ValueError("Serialized YAML could not be scanned") from error
    return result
