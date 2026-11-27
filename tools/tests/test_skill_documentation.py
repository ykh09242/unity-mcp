"""Check portable skill examples against frozen SDK contracts using only the standard library."""

import ast
import json
from pathlib import Path
import re
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
CLAUDE_SKILLS = ROOT / ".claude/skills"
ORCHESTRATOR_COPIES = (ROOT / "unity-mcp-skill", CLAUDE_SKILLS / "unity-mcp-skill")
BLENDER_SKILL = CLAUDE_SKILLS / "blender-to-unity"
SOURCE_SKILL = CLAUDE_SKILLS / "mcp-source"
SKILLS = (
    ORCHESTRATOR_COPIES[0],
    *(entry.parent for entry in sorted(CLAUDE_SKILLS.glob("*/SKILL.md"))),
)
FENCES = re.compile(r"^```([^\n]*)\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
LINKS = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
JSON_TYPES = {
    dict: "object",
    list: "array",
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    type(None): "null",
}


def distributed_tool_examples():
    for skill in SKILLS:
        for path in sorted(skill.rglob("*.md")):
            for language, body in FENCES.findall(path.read_text(encoding="utf-8")):
                if language == "json":
                    example = json.loads(body)
                    if isinstance(example, dict) and {"tool", "params"} <= example.keys():
                        yield path, example


def static_resource_uri(expression, tree, path, resolving=frozenset()):
    """Read published resource URI declarations without importing a provider."""
    if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
        return expression.value
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
        return static_resource_uri(expression.left, tree, path, resolving) + static_resource_uri(
            expression.right, tree, path, resolving
        )
    if isinstance(expression, ast.Name):
        key = (path.resolve(), expression.id)
        if key in resolving:
            raise ValueError(f"Cyclic resource URI declaration: {expression.id}")
        resolving = resolving | {key}
        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                )
                if any(
                    isinstance(target, ast.Name) and target.id == expression.id
                    for target in targets
                ):
                    return static_resource_uri(statement.value, tree, path, resolving)
            if not isinstance(statement, ast.ImportFrom) or statement.level or not statement.module:
                continue
            for alias in statement.names:
                if (alias.asname or alias.name) != expression.id:
                    continue
                parts = statement.module.split(".")
                if (
                    len(parts) != 3
                    or parts[0] != "services"
                    or parts[1] not in ("tools", "registry", "resources")
                ):
                    raise ValueError(f"Unsupported resource URI import: {statement.module}")
                folder = (ROOT / "Server/src/services" / parts[1]).resolve()
                imported = (folder / (parts[2] + ".py")).resolve()
                if not imported.is_relative_to(folder) or not imported.is_file():
                    raise ValueError(
                        f"Missing contained resource URI declaration: {statement.module}"
                    )
                imported_tree = ast.parse(imported.read_text(encoding="utf-8"))
                return static_resource_uri(
                    ast.Name(id=alias.name), imported_tree, imported, resolving
                )
    raise ValueError(f"Unsupported static resource URI expression: {ast.dump(expression)}")


def registered_resources():
    resources = []
    for folder in ("tools", "resources"):
        for path in (ROOT / "Server/src/services" / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if (
                        isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Name)
                        and decorator.func.id == "mcp_for_unity_resource"
                    ):
                        metadata = {kw.arg: kw.value for kw in decorator.keywords}
                        resources.append(static_resource_uri(metadata["uri"], tree, path))
    return resources


def published_tools():
    path = ROOT / "Server/tests/fixtures/public_mcp_contracts.json"
    protocols = json.loads(path.read_bytes())["protocols"]
    if not {"legacy", "modern"} <= protocols.keys():
        raise ValueError("The public contract snapshot is missing a protocol")
    return {mode: contract["tools"] for mode, contract in protocols.items()}


def accepts_documented_choice(field, value):
    """Check wire types and finite choices, including nullable or list-valued parameters.

    The companion SDK tests validate the complete JSON Schema against the live catalogs.
    """
    if isinstance(field, bool):
        return field
    kinds = {JSON_TYPES[type(value)]}
    if type(value) is int or (type(value) is float and value.is_integer()):
        kinds.update(("number", "integer"))
    if "type" in field:
        declared = field["type"]
        if kinds.isdisjoint([declared] if isinstance(declared, str) else declared):
            return False
    for choices in ([field["const"]] if "const" in field else None, field.get("enum")):
        if choices is not None and not any(
            value == choice and isinstance(value, bool) == isinstance(choice, bool)
            for choice in choices
        ):
            return False
    for keyword in ("anyOf", "oneOf"):
        if keyword in field and not any(
            accepts_documented_choice(branch, value) for branch in field[keyword]
        ):
            return False
    if isinstance(value, list) and "items" in field:
        return all(accepts_documented_choice(field["items"], item) for item in value)
    return True


def validate_request(request, tools):
    name, params = request["tool"], request["params"]
    if not isinstance(params, dict):
        raise ValueError("Tool parameters must be an object")
    for mode, catalog in tools.items():
        if name not in catalog:
            raise ValueError(f"{mode}: unknown registered tool {name}")
        schema = catalog[name]["inputSchema"]
        fields = schema["properties"]
        unexpected = params.keys() - fields.keys()
        missing = set(schema.get("required", ())) - params.keys()
        if unexpected or missing:
            raise ValueError(f"{mode} {name}: unexpected={unexpected}, missing={missing}")
        for parameter, value in params.items():
            if not accepts_documented_choice(fields[parameter], value):
                raise ValueError(f"{mode} {name}.{parameter}: invalid value {value!r}")
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


class ResourceMetadataParserTests(unittest.TestCase):
    def test_resource_uri_follows_a_registry_alias_without_executing_the_module(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "Server/src/services/registry"
            registry.mkdir(parents=True)
            (registry / "catalog.py").write_text(
                'raise RuntimeError("must not execute")\nPREFIX = "mcpforunity://"\n'
                'URI: str = PREFIX + "scene/inventory"\n',
                encoding="utf-8",
            )
            tree = ast.parse("from services.registry.catalog import URI as INVENTORY\nINVENTORY")
            with patch(__name__ + ".ROOT", root):
                self.assertEqual(
                    static_resource_uri(tree.body[-1].value, tree, root / "resource.py"),
                    "mcpforunity://scene/inventory",
                )
        for source in ("get_uri()", '["unexpected"]', "from external import URI\nURI"):
            tree = ast.parse(source)
            with self.subTest(source=source), self.assertRaises(ValueError):
                static_resource_uri(tree.body[-1].value, tree, Path(__file__))


class SkillDocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = published_tools()
        cls.resources = registered_resources()

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
        for path, example in distributed_tool_examples():
            with self.subTest(path=path.relative_to(ROOT), tool=example["tool"]):
                validate_request(example, self.tools)
                count += 1
        self.assertGreater(count, 0, "No tool examples were checked")

    def test_routing_tables_name_registered_tools(self):
        for skill in ORCHESTRATOR_COPIES:
            path = skill / "references/tools-reference.md"
            for line in path.read_text(encoding="utf-8").splitlines():
                columns = line.split("|")
                if len(columns) != 5:
                    continue
                for name in re.findall(r"`([a-z][a-z_]+)`", columns[2]):
                    with self.subTest(path=path.relative_to(ROOT), tool=name):
                        for catalog in self.tools.values():
                            self.assertIn(name, catalog)

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
        for language, body in FENCES.findall(
            (SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
        ):
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
                (SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
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
        canonical, mirror = ORCHESTRATOR_COPIES
        expected = {path.relative_to(canonical) for path in canonical.rglob("*.md")}
        self.assertEqual(expected, {path.relative_to(mirror) for path in mirror.rglob("*.md")})
        for relative in expected:
            with self.subTest(reference=relative):
                self.assertEqual(
                    (canonical / relative).read_text(encoding="utf-8"),
                    (mirror / relative).read_text(encoding="utf-8"),
                )

    def test_missing_tool_recovery_covers_group_and_editor_catalog_actions(self):
        for skill in ORCHESTRATOR_COPIES:
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
        for path in BLENDER_SKILL.rglob("*.md"):
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
                (BLENDER_SKILL / "SKILL.md").read_text(encoding="utf-8")
            )
            if language == "json"
        ]
        creates = [
            example["params"]
            for example in examples
            if example.get("tool") == "prefab_create"
            and example["params"]["action"] == "create_from_gameobject"
        ]
        reads = [
            example["params"]
            for example in examples
            if example.get("tool") == "prefab_inspect" and example["params"]["action"] == "get_info"
        ]
        self.assertTrue(creates, "Model import alone cannot fulfill a new prefab request")
        for params in creates:
            self.assertTrue(params.get("target"))
            self.assertTrue(params["prefab_path"].startswith("Assets/"))
            self.assertTrue(params["prefab_path"].endswith(".prefab"))
            self.assertIs(params.get("allow_overwrite"), False)
            self.assertTrue(any(read["prefab_path"] == params["prefab_path"] for read in reads))

    def test_examples_use_the_same_action_and_preflight_fields_as_clients(self):
        for request in (
            {
                "tool": "scene_inspect",
                "params": {"action": "get_active", "refresh_if_dirty": False},
            },
            {
                "tool": "manage_asset",
                "params": {
                    "action": "get_info",
                    "path": "Assets/Scenes/Level.unity",
                    "refresh_if_dirty": False,
                },
            },
            {"tool": "read_console", "params": {"types": ["warning", "log"]}},
        ):
            with self.subTest(request=request):
                validate_request(request, self.tools)

    def test_validator_rejects_contract_regressions(self):
        invalid = (
            {"tool": "invented_tool", "params": {}},
            {"tool": "manage_scene", "params": {"action": "invented"}},
            {"tool": "asset_labels", "params": {"action": "get_labels", "refresh_if_dirty": False}},
            {
                "tool": "scene_inspect",
                "params": {"action": "get_active", "refresh_if_dirty": "false"},
            },
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
