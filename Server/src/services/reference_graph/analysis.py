"""No-install, conservative C# call/constant analysis.

This is a token/call analyzer, not a C# compiler. It never executes source or
claims absence of references. Overloads, dynamic expressions, cycles and absent
keys retain explicit uncertainty. Summaries propagate literal/parameter string
composition through wrappers without rereading their source at every call site.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
import re

from services.reference_graph.store import Edge

Expr = tuple[tuple[str, str], ...]
UNKNOWN: Expr = (("unknown", ""),)
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
    return "".join(output)


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
) -> Expr:
    while tokens and tokens[0].text == "(" and _pairs(tokens).get(0) == len(tokens) - 1:
        tokens = tokens[1:-1]
    parts = _split(tokens, "+")
    if len(parts) > 1:
        atoms = tuple(
            atom for part in parts for atom in _expression(part, scope, parameters, locals_)
        )
        return UNKNOWN if any(kind == "unknown" for kind, _ in atoms) else atoms
    if len(tokens) == 1 and tokens[0].kind == "string":
        return (("literal", tokens[0].value),) if tokens[0].value is not None else UNKNOWN
    if (
        tokens
        and all(
            t.kind == "id" if i % 2 == 0 else t.text in (".", "::") for i, t in enumerate(tokens)
        )
        and len(tokens) % 2
    ):
        name = "".join(t.text for t in tokens).removeprefix("global::")
        if name.partition(".")[0] in locals_:
            return UNKNOWN
        if name in parameters:
            return (("parameter", name),)
        return (("symbol", scope + "|" + name),)
    return UNKNOWN


@dataclass(frozen=True, slots=True)
class Call:
    name: str
    arguments: tuple[Expr, ...]
    line: int
    scope: str
    method: str
    shadowed_receiver: bool = False


@dataclass(frozen=True, slots=True)
class Method:
    identity: str
    name: str
    parameters: tuple[str, ...]
    calls: tuple[int, ...]


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
                    item["identity"], item["name"], tuple(item["parameters"]), tuple(item["calls"])
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
                )
                for item in data["calls"]
            ),
        )


def parse_csharp(owner: str, path: str, source: str) -> Facts:
    tokens = _tokens(source)
    pairs = _pairs(tokens)
    scopes = [""] * len(tokens)
    types, usings, aliases, constants = [], [], {}, {}
    active: list[tuple[int, str]] = []
    for i, token in enumerate(tokens):
        while active and active[-1][0] <= i:
            active.pop()
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
                active.append((stop, full))
                if token.text != "namespace":
                    types.append(full)
        elif token.text == "using":
            end = i + 1
            while end < len(tokens) and tokens[end].text not in (";", "(", "{"):
                end += 1
            parts = tokens[i + 1 : end]
            if end < len(tokens) and tokens[end].text == ";" and parts:
                value = "".join(t.text for t in parts).removeprefix("global::")
                if "=" in value:
                    name, target = value.split("=", 1)
                    aliases[name] = target.removeprefix("global::")
                else:
                    usings.append(value)
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
        name = "".join(t.text for t in tokens[start : name_end + 1]).removeprefix("global::")
        end = pairs[i]
        after = end + 1
        if after < len(tokens) and tokens[after].text == "where":
            while after < len(tokens) and tokens[after].text not in ("{", "=>", ";"):
                after += 1
        is_definition = (
            after < len(tokens) and tokens[after].text in ("{", "=>") and "." not in name
        )
        if is_definition:
            parameters = []
            for part in _split(tokens[i + 1 : end], ","):
                before_default = _split(part, "=")[0]
                names = [t.text for t in before_default if t.kind == "id"]
                parameters.append(names[-1] if names else "?")
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
            and tokens[i - 1].kind == "id"
            and tokens[i - 1].text not in ("return", "throw", "await", "new")
            and tokens[i + 1].text in ("=", ";", ",", ")")
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
    fields = defaultdict(set)
    for i in range(1, len(tokens) - 1):
        if (
            not inside_method[i]
            and tokens[i].kind == "id"
            and tokens[i - 1].kind == "id"
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
            and tokens[i + 1].text in ("=", ";", "{")
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
        call = Call(
            name,
            tuple(
                _expression(part, scopes[i], parameters, locals_)
                for part in _split(tokens[i + 1 : end], ",")
            ),
            tokens[start].line,
            scopes[i],
            identity,
            "." in name
            and receiver in (locals_ | set(parameters))
            and tokens[start].text != "global",
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
            tokens[end:stop], scopes[i], ()
        )
    methods = tuple(Method(m[0], m[1], m[2], tuple(method_calls[m[0]])) for m in method_ranges)
    return Facts(
        owner, path, tuple(usings), aliases, tuple(types), constants, methods, tuple(calls)
    )


class Catalog:
    def __init__(self, assets: Iterable[dict]):
        self.keys: dict[tuple[str, str], set[str]] = defaultdict(set)
        self.guids: set[str] = set()
        for asset in assets:
            guid = asset["guid"].lower()
            target = "asset:" + guid
            self.guids.add(guid)
            self.keys["guid", guid].add(target)
            self.keys["path", asset["path"]].add(target)
            for key in asset.get("keys", ()):
                self.keys[key["scheme"], key["value"]].add(target)

    def resolve(self, scheme: str, key: str, mode: str = "single") -> tuple[set[str], str | None]:
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
            return set(), "key_not_found"
        if len(targets) > 1 and mode == "single":
            return set(), "ambiguous_key"
        return targets, None


@dataclass(frozen=True, slots=True)
class Effect:
    scheme: str
    mode: str
    expression: Expr
    evidence: str


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
    def __init__(self, facts: Iterable[Facts], catalog: Catalog, rules: Iterable[dict] = ()):
        self.facts = {item.owner: item for item in facts}
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
        for item in self.facts.values():
            for name in item.types:
                self.types[name].add(item.owner)
        self.dependencies: dict[str, set[str]] = defaultdict(set)
        for item in self.facts.values():
            for name, expr in item.constants.items():
                self.constants[name].append((item.owner, expr))
            for method in item.methods:
                self.methods[method.name].append((item.owner, method))
                self.method_ids[method.identity] = (item, method)
        self.constant_cache: dict[tuple[str, str], Expr] = {}
        self.summaries: dict[str, tuple[tuple[Effect, ...], tuple[str, ...]]] = {}
        self._build_summaries()
        for item in self.facts.values():
            for call in item.calls:
                if not call.method:
                    self._resolve_call(item, call)
                    for expr in call.arguments:
                        self._expand(item, expr, {})

    def _names(self, item: Facts, name: str, scope: str) -> list[str]:
        first, _, rest = name.partition(".")
        if first in item.aliases:
            return [item.aliases[first] + ("." + rest if rest else "")]
        names = []
        while scope:
            names.append(scope + "." + name)
            scope = scope.rpartition(".")[0]
        return list(
            dict.fromkeys(
                [
                    *names,
                    name,
                    *(
                        using + "." + name
                        for using in item.usings
                        if not using.startswith("static")
                    ),
                ]
            )
        )

    def _constant(self, item: Facts, scope: str, name: str, active: set[str]) -> Expr:
        key = (item.owner, scope + "|" + name)
        if key in self.constant_cache:
            return self.constant_cache[key]
        matches = [
            (full, row)
            for full in self._names(item, name, scope)
            for row in self.constants.get(full, ())
        ]
        self.dependencies[item.owner].update(row[0] for _, row in matches)
        if len(matches) != 1 or matches[0][0] in active:
            return UNKNOWN
        full, (owner, expr) = matches[0]
        self.dependencies[item.owner].add(owner)
        result = self._expand(self.facts[owner], expr, {}, active | {full})
        self.constant_cache[key] = result
        return result

    def _expand(
        self, item: Facts, expr: Expr, arguments: dict[str, Expr], active: set[str] | None = None
    ) -> Expr:
        output = []
        for kind, value in expr:
            if kind == "symbol":
                scope, name = value.split("|", 1)
                try:
                    output.extend(self._constant(item, scope, name, active or set()))
                except RecursionError:
                    output.extend(UNKNOWN)
            elif kind == "parameter":
                output.extend(arguments.get(value, ((kind, value),)))
            else:
                output.append((kind, value))
        return UNKNOWN if any(kind == "unknown" for kind, _ in output) else tuple(output)

    def _resolve_call(self, item: Facts, call: Call) -> tuple[list[Effect], str | None, str | None]:
        if call.shadowed_receiver:
            return [], None, "unresolved_receiver"
        names = self._names(item, call.name, call.scope)
        rule_matches = [self.rules[name] for name in names if name in self.rules]
        if len(rule_matches) > 1:
            return [], None, "ambiguous_call"
        if rule_matches:
            rule = rule_matches[0]
            index = rule.get("argument", 0)
            expr = call.arguments[index] if index < len(call.arguments) else UNKNOWN
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
            )
        matches = [
            (owner, method)
            for name in names
            for owner, method in self.methods.get(name, ())
            if len(method.parameters) == len(call.arguments)
        ]
        if matches:
            self.dependencies[item.owner].update(owner for owner, _ in matches)
            if len(matches) != 1:
                return [], None, "ambiguous_call"
            owner, method = matches[0]
            self.dependencies[item.owner].add(owner)
            return [], method.identity, None
        qualifier, _, method = call.name.rpartition(".")
        if method not in ("Load", "LoadAsync", "LoadAll", "LoadAssetAsync", "LoadAssetsAsync"):
            return [], None, None
        qualified = self._names(item, qualifier, call.scope)
        # A project type with the same visible qualifier takes precedence.
        if any(name in self.types for name in qualified):
            self.dependencies[item.owner].update(
                owner for name in qualified for owner in self.types.get(name, ())
            )
            return [], None, "unresolved_loader"
        scheme = None
        if "UnityEngine.Resources" in qualified and method in ("Load", "LoadAsync", "LoadAll"):
            scheme = "resources"
        if "UnityEngine.AddressableAssets.Addressables" in qualified and method in (
            "LoadAssetAsync",
            "LoadAssetsAsync",
        ):
            scheme = "addressables"
        if scheme is None:
            return [], None, "unresolved_loader"
        return (
            [
                Effect(
                    scheme,
                    "all" if method in ("LoadAll", "LoadAssetsAsync") else "single",
                    call.arguments[0] if call.arguments else UNKNOWN,
                    "call:" + call.name,
                )
            ],
            None,
            None,
        )

    def _build_summaries(self) -> None:
        waiting, reverse, resolutions = {}, defaultdict(set), {}
        for identity, (item, method) in self.method_ids.items():
            children = set()
            for index in method.calls:
                resolved = self._resolve_call(item, item.calls[index])
                resolutions[identity, index] = resolved
                if resolved[1]:
                    children.add(resolved[1])
                    reverse[resolved[1]].add(identity)
            waiting[identity] = children
        ready = deque(identity for identity, children in waiting.items() if not children)
        while ready:
            identity = ready.popleft()
            item, method = self.method_ids[identity]
            effects, issues = set(), set()
            for index in method.calls:
                call = item.calls[index]
                direct, child, issue = resolutions[identity, index]
                if issue:
                    issues.add(issue)
                if child:
                    child_effects, child_issues = self.summaries[child]
                    issues.update(child_issues)
                    parameters = self.method_ids[child][1].parameters
                    arguments = {
                        name: self._expand(item, expr, {})
                        for name, expr in zip(parameters, call.arguments)
                    }
                    direct = [
                        Effect(
                            e.scheme,
                            e.mode,
                            self._expand(item, e.expression, arguments),
                            "wrapper:" + call.name,
                        )
                        for e in child_effects
                    ]
                effects.update(
                    Effect(e.scheme, e.mode, self._expand(item, e.expression, {}), e.evidence)
                    for e in direct
                )
            self.summaries[identity] = (tuple(sorted(effects, key=repr)), tuple(sorted(issues)))
            for parent in reverse[identity]:
                waiting[parent].discard(identity)
                if not waiting[parent]:
                    ready.append(parent)
        for identity in waiting.keys() - self.summaries.keys():
            self.summaries[identity] = ((), ("recursive_wrapper",))

    def affected(self, changed: set[str]) -> set[str]:
        reverse = defaultdict(set)
        for owner, dependencies in self.dependencies.items():
            for dependency in dependencies:
                reverse[dependency].add(owner)
        result, pending = set(changed), list(changed)
        while pending:
            for owner in reverse[pending.pop()] - result:
                result.add(owner)
                pending.append(owner)
        return result

    def analyze(self, owner: str) -> Analysis:
        item = self.facts[owner]
        result = Analysis()
        for call in item.calls:
            effects, child, issue = self._resolve_call(item, call)
            issues = [issue] if issue else []
            if child:
                effects, child_issues = self.summaries[child]
                issues.extend(child_issues)
                arguments = {
                    name: self._expand(item, expr, {})
                    for name, expr in zip(self.method_ids[child][1].parameters, call.arguments)
                }
                effects = [
                    Effect(
                        e.scheme,
                        e.mode,
                        self._expand(item, e.expression, arguments),
                        "wrapper:" + call.name,
                    )
                    for e in effects
                ]
            for effect in effects:
                expr = self._expand(item, effect.expression, {})
                if any(kind != "literal" for kind, _ in expr):
                    issues.append("dynamic_key")
                    continue
                key = "".join(value for _, value in expr)
                targets, reason = self.catalog.resolve(effect.scheme, key, effect.mode)
                if reason:
                    result.unresolved.append(
                        {"line": call.line, "call": call.name, "reason": reason, "key": key}
                    )
                for target in sorted(targets):
                    result.edges.append(
                        Edge(
                            target,
                            effect.scheme + "_load",
                            key,
                            call.line,
                            "possible",
                            effect.evidence,
                        )
                    )
            result.unresolved.extend(
                {"line": call.line, "call": call.name, "reason": reason}
                for reason in sorted(set(issues))
            )
        # Transitive dependencies ensure a changed constant inside a wrapper also
        # invalidates callers, even when the wrapper's own source is unchanged.
        pending = list(self.dependencies[owner])
        while pending:
            dependency = pending.pop()
            if dependency in result.dependencies:
                continue
            result.dependencies.add(dependency)
            pending.extend(self.dependencies[dependency] - result.dependencies)
        return result


def serialized_references(source: str, catalog: Catalog) -> Analysis:
    result = Analysis(coverage="serialized_asset_reference_guids")
    for line, text in enumerate(source.splitlines(), 1):
        match = re.fullmatch(r'\s*m_AssetGUID:\s*["\']?([0-9a-fA-F]{32})["\']?\s*', text)
        if match and set(match[1]) != {"0"}:
            guid = match[1].lower()
            result.edges.append(
                Edge(
                    "asset:" + guid,
                    "serialized_asset_reference",
                    guid,
                    line,
                    "observed",
                    "m_AssetGUID",
                )
            )
    return result
