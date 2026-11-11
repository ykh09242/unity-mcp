"""Offline contracts for the distributed skills, without importing the MCP server."""

import ast
import json
from pathlib import Path
import re
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
SKILLS = (
    ROOT / "unity-mcp-skill",
    ROOT / ".claude/skills/unity-mcp-skill",
    ROOT / ".claude/skills/blender-to-unity",
    ROOT / ".claude/skills/mcp-source",
)
FENCES = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
LINKS = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def action_sequence(expression, tree, path, resolving=frozenset()):
    """Resolve only static string sequences; never execute product code or imports."""
    if isinstance(expression, (ast.List, ast.Tuple)):
        values = [ast.literal_eval(value) for value in expression.elts]
        if all(isinstance(value, str) for value in values):
            return values
    elif isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        return action_sequence(expression.left, tree, path, resolving) + action_sequence(
            expression.right, tree, path, resolving
        )
    elif isinstance(expression, ast.Name):
        key = (path.resolve(), expression.id)
        if key in resolving:
            raise ValueError(f"Cyclic action declaration: {expression.id}")
        resolving = resolving | {key}
        for statement in tree.body:
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
                if isinstance(statement, ast.AnnAssign)
                else []
            )
            if any(
                isinstance(target, ast.Name) and target.id == expression.id for target in targets
            ):
                return action_sequence(statement.value, tree, path, resolving)
            if not isinstance(statement, ast.ImportFrom) or statement.level or not statement.module:
                continue
            for alias in statement.names:
                if (alias.asname or alias.name) != expression.id:
                    continue
                # Resolve source AST only for repository-local tool helper modules.
                parts = statement.module.split(".")
                if parts[:2] != ["services", "tools"] or len(parts) != 3:
                    raise ValueError(f"Unsupported action import: {statement.module}")
                folder = (ROOT / "Server/src/services/tools").resolve()
                imported = (folder / (parts[2] + ".py")).resolve()
                if not imported.is_relative_to(folder) or not imported.is_file():
                    raise ValueError(f"Missing contained action declaration: {statement.module}")
                imported_tree = ast.parse(imported.read_text(encoding="utf-8"))
                return action_sequence(ast.Name(id=alias.name), imported_tree, imported, resolving)
    raise ValueError(f"Unsupported static action expression: {ast.dump(expression)}")


def literal_values(annotation, tree, path):
    """Read literal members and explicitly starred static action declarations."""
    members = (
        annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else [annotation.slice]
    )
    values = set()
    for member in members:
        if isinstance(member, ast.Starred):
            values.update(action_sequence(member.value, tree, path))
        else:
            values.add(ast.literal_eval(member))
    return values


def registered_contracts():
    tools, resources = {}, []
    for folder in ("tools", "resources"):
        for path in (ROOT / "Server/src/services" / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call) or not isinstance(
                        decorator.func, ast.Name
                    ):
                        continue
                    metadata = {kw.arg: kw.value for kw in decorator.keywords}
                    if decorator.func.id == "mcp_for_unity_resource":
                        resources.append(ast.literal_eval(metadata["uri"]))
                    if decorator.func.id != "mcp_for_unity_tool":
                        continue
                    name = ast.literal_eval(metadata["name"]) if "name" in metadata else node.name
                    args = node.args.posonlyargs + node.args.args
                    required_count = len(args) - len(node.args.defaults)
                    required = {arg.arg for arg in args[:required_count]} - {"ctx"}
                    allowed = {arg.arg for arg in args + node.args.kwonlyargs} - {"ctx"}
                    literals = {}
                    for arg in args + node.args.kwonlyargs:
                        if arg.annotation is None:
                            continue
                        for annotation in ast.walk(arg.annotation):
                            if (
                                isinstance(annotation, ast.Subscript)
                                and isinstance(annotation.value, ast.Name)
                                and annotation.value.id == "Literal"
                            ):
                                literals[arg.arg] = literal_values(annotation, tree, path)
                    tools[name] = (allowed, required, literals)
    # Focused tools are adapters declared in the catalog, not decorated functions.
    catalog = ROOT / "Server/src/services/registry/capability_catalog.py"
    for node in ast.walk(ast.parse(catalog.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_feature":
            continue
        name, source, _, _, actions, parameters = map(ast.literal_eval, node.args[:6])
        allowed, required, literals = tools[source]
        admitted = {"action", *parameters.split()}
        tools[name] = (
            allowed & admitted,
            (required & admitted) | {"action"},
            {**literals, "action": set(actions.split())},
        )
    return tools, resources


def validate_request(request, tools):
    name, params = request["tool"], request["params"]
    if name not in tools:
        raise ValueError(f"Unknown registered tool: {name}")
    if not isinstance(params, dict):
        raise ValueError("Tool parameters must be an object")
    allowed, required, literals = tools[name]
    # Routing is injected into advertised schemas by middleware, not the Python function.
    unexpected = params.keys() - allowed - {"unity_instance"}
    if unexpected or required - params.keys():
        raise ValueError(f"{name}: unexpected={unexpected}, missing={required - params.keys()}")
    for parameter, values in literals.items():
        if parameter in params and params[parameter] is not None:
            selected = (
                params[parameter] if isinstance(params[parameter], list) else [params[parameter]]
            )
            if any(value not in tuple(values) for value in selected):
                raise ValueError(f"{name}.{parameter}: invalid value {params[parameter]!r}")
    if name == "import_model_file":
        source = params["source_path"].replace("\\", "/")
        if not source.startswith("Assets/") or ".." in source.split("/"):
            raise ValueError("Portable model examples must use contained Assets-relative sources")
    if name == "batch_execute":
        for command in params["commands"]:
            if "unity_instance" in command.get("params", {}):
                raise ValueError("Nested commands cannot select another Editor")
            # Batch dispatches native handlers; all examples here use their shared public keys.
            validate_request(command, tools)


class LiteralMetadataParserTests(unittest.TestCase):
    def test_starred_metadata_resolves_lists_concatenation_and_local_import_without_execution(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            tools = root / "Server/src/services/tools"
            tools.mkdir(parents=True)
            (tools / "shared.py").write_text(
                'raise RuntimeError("must not execute")\nEXTRA = ["inspect"]\n', encoding="utf-8"
            )
            source = (
                "from services.tools.shared import EXTRA as IMPORTED\n"
                'BASE: list[str] = ["ping"]\nALL_ACTIONS = BASE + IMPORTED\n'
                'action: Annotated[str, Literal["status", *ALL_ACTIONS]]\n'
            )
            tree = ast.parse(source)
            annotation = tree.body[-1].annotation.slice.elts[1]
            with patch(__name__ + ".ROOT", root):
                values = literal_values(annotation, tree, tools / "fixture.py")

        self.assertEqual(values, {"status", "ping", "inspect"})
        contracts = {"fixture": ({"action"}, {"action"}, {"action": values})}
        validate_request({"tool": "fixture", "params": {"action": "inspect"}}, contracts)
        with self.assertRaises(ValueError):
            validate_request({"tool": "fixture", "params": {"action": "invented"}}, contracts)

    def test_starred_metadata_rejects_unsupported_or_cyclic_expressions(self):
        for expression in (
            'list(["ping"])',
            '__import__("os").system("must not run")',
            "UNKNOWN",
            "ALL_ACTIONS",
            '"ping"',
            '[1, "ping"]',
        ):
            tree = ast.parse(f"ALL_ACTIONS = {expression}\naction: Literal[*ALL_ACTIONS]\n")
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                literal_values(tree.body[-1].annotation, tree, Path(__file__))

    def test_starred_metadata_rejects_nonlocal_imports(self):
        tree = ast.parse("from external import ACTIONS\naction: Literal[*ACTIONS]\n")
        with self.assertRaisesRegex(ValueError, "Unsupported action import"):
            literal_values(tree.body[-1].annotation, tree, Path(__file__))


class SkillDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools, cls.resources = registered_contracts()

    def test_local_links_are_contained_and_resolve(self):
        for skill in SKILLS:
            for path in skill.rglob("*.md"):
                for link in LINKS.findall(path.read_text(encoding="utf-8")):
                    if urlsplit(link).scheme:
                        continue
                    relative, _, anchor = unquote(link).partition("#")
                    target = (path.parent / relative).resolve() if relative else path.resolve()
                    with self.subTest(path=path.relative_to(ROOT), link=link):
                        self.assertTrue(
                            target.is_relative_to(skill.resolve()),
                            "Reference escapes distributed skill",
                        )
                        self.assertTrue(
                            target.is_file(), "Reference missing from distributed skill"
                        )
                        if anchor:
                            headings = re.findall(
                                r"^#+\s+(.+)$", target.read_text(encoding="utf-8"), re.MULTILINE
                            )
                            anchors = {
                                re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
                                for heading in headings
                            }
                            self.assertIn(anchor, anchors)

    def test_json_tool_examples_match_registered_schemas(self):
        count = 0
        for skill in SKILLS:
            for path in skill.rglob("*.md"):
                for language, body in FENCES.findall(path.read_text(encoding="utf-8")):
                    if language != "json":
                        continue
                    with self.subTest(path=path.relative_to(ROOT), example=body[:80]):
                        data = json.loads(body)
                        if isinstance(data, dict) and {"tool", "params"} <= data.keys():
                            validate_request(data, self.tools)
                            count += 1
        self.assertGreater(count, 0, "No tool examples were checked")

    def test_routing_tables_name_registered_tools(self):
        for skill in SKILLS[:2]:
            path = skill / "references/tools-reference.md"
            for line in path.read_text(encoding="utf-8").splitlines():
                columns = line.split("|")
                if len(columns) != 5:
                    continue
                for name in re.findall(r"`([a-z][a-z_]+)`", columns[2]):
                    with self.subTest(path=path.relative_to(ROOT), tool=name):
                        self.assertIn(name, self.tools)

    def test_resource_uris_match_registered_resources_or_script_locators(self):
        # Component templates advertise an optional RFC6570 query expansion.
        paths = [re.sub(r"\{\?[^}]+\}", "", uri) for uri in self.resources]
        patterns = [
            re.compile("^" + re.sub(r"\\\{.*?\\\}", "[^/?` ]+", re.escape(uri)) + "$")
            for uri in paths
        ]
        for skill in SKILLS:
            for path in skill.rglob("*.md"):
                uris = re.findall(r"mcpforunity://[^`\s\"<>]+", path.read_text(encoding="utf-8"))
                for uri in uris:
                    with self.subTest(path=path.relative_to(ROOT), uri=uri):
                        if uri.startswith("mcpforunity://path/Assets/"):
                            continue
                        self.assertTrue(
                            any(pattern.fullmatch(uri.split("?", 1)[0]) for pattern in patterns),
                            "Unregistered resource URI",
                        )

    def test_source_skill_manifest_examples_use_current_package_identity(self):
        metadata = json.loads((ROOT / "MCPForUnity/package.json").read_text(encoding="utf-8"))
        examples = []
        for language, body in FENCES.findall((SKILLS[3] / "SKILL.md").read_text(encoding="utf-8")):
            if language == "json":
                examples.append(json.loads(body))
        self.assertTrue(examples)
        for example in examples:
            manifest = example.get("after", example)
            dependency = manifest["dependencies"]
            self.assertIn(metadata["name"], dependency)
            self.assertNotIn("com.coplaydev.unity-mcp", dependency)
            url = urlsplit(dependency[metadata["name"]])
            self.assertEqual(url.hostname, "github.com")
            self.assertEqual(url.path, "/ykh09242/unity-mcp.git")
            self.assertEqual(url.query, "path=/MCPForUnity")
            self.assertTrue(url.fragment)

    def test_source_migration_fixture_preserves_unrelated_manifest_entries(self):
        examples = [
            json.loads(body)
            for language, body in FENCES.findall(
                (SKILLS[3] / "SKILL.md").read_text(encoding="utf-8")
            )
            if language == "json"
        ]
        migrations = [example for example in examples if {"before", "after"} <= example.keys()]
        self.assertTrue(migrations, "No manifest migration case was checked")
        old, new = "com.coplaydev.unity-mcp", "com.ykh09242.unity-mcp"
        for example in migrations:
            before, after = example["before"], example["after"]
            self.assertIn(old, before["dependencies"])
            self.assertNotIn(old, after["dependencies"])
            unrelated = {
                name: value
                for name, value in before["dependencies"].items()
                if name not in (old, new)
            }
            self.assertTrue(unrelated, "Migration fixture needs an unrelated dependency")
            self.assertEqual(
                unrelated,
                {
                    name: value
                    for name, value in after["dependencies"].items()
                    if name not in (old, new)
                },
            )
            self.assertEqual(
                [name for name in before["testables"] if name not in (old, new)],
                [name for name in after["testables"] if name not in (old, new)],
            )
            self.assertTrue(any(name not in (old, new) for name in before["testables"]))
            self.assertIn(old, before["testables"])
            self.assertNotIn(old, after["testables"])
            self.assertEqual(after["testables"].count(new), 1)

    def test_repository_mirror_contains_the_same_distributable_references(self):
        canonical, mirror = SKILLS[:2]
        expected = {path.relative_to(canonical) for path in canonical.rglob("*.md")}
        self.assertEqual(expected, {path.relative_to(mirror) for path in mirror.rglob("*.md")})
        for relative in expected:
            with self.subTest(reference=relative):
                self.assertEqual(
                    (canonical / relative).read_text(encoding="utf-8"),
                    (mirror / relative).read_text(encoding="utf-8"),
                )

    def test_missing_tool_recovery_covers_group_and_editor_catalog_actions(self):
        for skill in SKILLS[:2]:
            examples = [
                json.loads(body)
                for language, body in FENCES.findall(
                    (skill / "references/connection.md").read_text(encoding="utf-8")
                )
                if language == "json"
            ]
            actions = {
                example["params"]["action"]
                for example in examples
                if example["tool"] == "manage_tools"
            }
            self.assertTrue({"list_groups", "activate", "sync"} <= actions)
            for example in examples:
                validate_request(example, self.tools)

    def test_integrated_blender_examples_cover_inspection_and_scoped_import(self):
        examples = []
        for path in SKILLS[2].rglob("*.md"):
            for language, body in FENCES.findall(path.read_text(encoding="utf-8")):
                if language == "json":
                    example = json.loads(body)
                    if example.get("tool") == "blender_bridge":
                        examples.append(example)
        self.assertTrue(
            {"status", "scene_info", "object_info", "import_model"}
            <= {example["params"]["action"] for example in examples}
        )
        imports = []
        for example in examples:
            validate_request(example, self.tools)
            if example["params"]["action"] == "import_model":
                params = example["params"]
                imports.append(params)
                self.assertTrue(params.get("object_names") or params.get("selection_only"))
                self.assertIs(params.get("auto_animate"), False)
                self.assertIsInstance(params.get("save_prefab"), bool)
                self.assertIs(params.get("ensure_bloom"), False)
                if params["save_prefab"]:
                    self.assertIs(params.get("place_in_scene"), True)
        self.assertTrue(
            any(params["save_prefab"] is False for params in imports),
            "Plain import scope is missing",
        )
        self.assertTrue(
            any(params["save_prefab"] is True for params in imports),
            "Requested prefab creation is missing",
        )

    def test_separate_blender_prefab_example_creates_without_overwrite_and_reads_asset(self):
        examples = [
            json.loads(body)
            for language, body in FENCES.findall(
                (SKILLS[2] / "SKILL.md").read_text(encoding="utf-8")
            )
            if language == "json"
        ]
        prefabs = [example for example in examples if example.get("tool") == "manage_prefabs"]
        creates = [
            example["params"]
            for example in prefabs
            if example["params"]["action"] == "create_from_gameobject"
        ]
        reads = [
            example["params"] for example in prefabs if example["params"]["action"] == "get_info"
        ]
        self.assertTrue(creates, "Model import alone cannot fulfill a new prefab request")
        for example in prefabs:
            validate_request(example, self.tools)
        for params in creates:
            self.assertTrue(params.get("target"))
            self.assertTrue(params["prefab_path"].startswith("Assets/"))
            self.assertTrue(params["prefab_path"].endswith(".prefab"))
            self.assertIs(params.get("allow_overwrite"), False)
            self.assertTrue(any(read["prefab_path"] == params["prefab_path"] for read in reads))

    def test_validator_rejects_contract_regressions(self):
        invalid = (
            {"tool": "invented_tool", "params": {}},
            {"tool": "manage_tools", "params": {"action": "enable", "group": "asset_gen"}},
            {"tool": "read_console", "params": {"types": ["invented-level"]}},
            {"tool": "blender_bridge", "params": {"action": "save_prefab"}},
            {"tool": "script_apply_edits", "params": {"uri": "Assets/A.cs", "edits": []}},
            {"tool": "import_model_file", "params": {"source_path": "/tmp/model.fbx"}},
            {
                "tool": "batch_execute",
                "params": {
                    "commands": [
                        {
                            "tool": "manage_scene",
                            "params": {"action": "get_active", "unity_instance": "Other@123"},
                        }
                    ]
                },
            },
        )
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(ValueError):
                validate_request(request, self.tools)


if __name__ == "__main__":
    unittest.main()
