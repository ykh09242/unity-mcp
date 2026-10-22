"""Build context and conservative C# binding without an external compiler."""

from dataclasses import asdict
import pytest

from services.reference_graph.analysis import AnalysisIndex, Catalog, Facts, parse_csharp


def context(assembly="Game", references=(), defines=()):
    return {
        "state": "available",
        "assembly": assembly,
        "references": list(references),
        "defines": list(defines),
        "revision": "build-one",
        "target": "StandaloneWindows64",
        "assemblies_type": "Editor",
    }


def catalog():
    return Catalog(
        [
            {
                "guid": str(index) * 32,
                "path": f"Assets/Resources/{key}.prefab",
                "keys": [{"scheme": "resources", "value": key}],
            }
            for index, key in enumerate(("one", "two", "three"), 1)
        ]
    )


def facts(owner, source, build=None):
    return parse_csharp(owner, f"Assets/{owner}.cs", source, context=build or context())


def keys(index, owner="caller"):
    return {edge.key for edge in index.analyze(owner).edges}


def test_preprocessor_uses_actual_defines_preserves_lines_and_ignores_literal_directives():
    source = """using UnityEngine;
class Use {
const string Text = @"
#if FALSE
";
void Run() {
#if UNITY_EDITOR && (!SERVER || CLIENT == true)
Resources.Load("one");
#elif SERVER
Resources.Load("two");
#else
Resources.Load("three");
#endif
}
}"""
    item = facts("caller", source, context(defines=("UNITY_EDITOR", "CLIENT")))
    result = AnalysisIndex([item], catalog()).analyze("caller")
    assert {edge.key for edge in result.edges} == {"one"}
    assert result.edges[0].line == 8
    assert Facts.from_dict(asdict(item)) == item
    assert keys(
        AnalysisIndex([facts("caller", source, context(defines=("SERVER",)))], catalog())
    ) == {"two"}


def test_named_default_arguments_and_using_static_propagate_across_wrappers():
    loader = facts(
        "loader",
        """namespace Api { public static class Loader {
public static void Load(string suffix="one", string prefix="") { UnityEngine.Resources.Load(prefix+suffix); }
} }""",
    )
    caller = facts(
        "caller",
        """using static Api.Loader; class Use { void Run() { Load(); Load(prefix:"", suffix:"two"); } }""",
    )
    assert keys(AnalysisIndex([loader, caller], catalog())) == {"one", "two"}


def test_typed_overload_only_resolves_when_argument_type_is_supported_and_unique():
    item = facts(
        "caller",
        """class Use {
static void Load(string key) { UnityEngine.Resources.Load(key); }
static void Load(int value) { UnityEngine.Resources.Load("two"); }
void Run() { Load("one"); }
}""",
    )
    result = AnalysisIndex([item], catalog()).analyze("caller")
    # The overload body remains its own possible evidence; the call itself binds string.
    assert any(edge.key == "one" and edge.evidence.startswith("wrapper:") for edge in result.edges)
    assert not any(issue["reason"] == "ambiguous_call" for issue in result.unresolved)


def test_same_type_name_in_unreferenced_assembly_cannot_shadow_visible_symbol():
    good = facts(
        "good", 'public class Keys { public const string Value="one"; }', context("Visible")
    )
    other = facts(
        "other", 'public class Keys { public const string Value="two"; }', context("Hidden")
    )
    caller = facts(
        "caller",
        "class Use { void Run() { UnityEngine.Resources.Load(Keys.Value); } }",
        context("Game", references=("Visible",)),
    )
    index = AnalysisIndex([good, other, caller], catalog())
    assert keys(index) == {"one"}
    assert "good" in index.analyze("caller").dependencies
    updated = facts(
        "caller",
        "class Use { void Run() { UnityEngine.Resources.Load(Keys.Value); } }",
        context("Game", references=("Hidden",)),
    )
    assert "caller" in index.update([updated])
    assert keys(index) == {"two"}


def test_compilation_metadata_is_shared_per_index_and_released_after_last_owner():
    import json

    build = context("Game", references=tuple(f"Dependency{i}" for i in range(100)))
    items = [
        Facts.from_dict(json.loads(json.dumps(asdict(facts(str(i), f"class C{i} {{ }}", build)))))
        for i in range(32)
    ]
    index = AnalysisIndex(items, catalog())
    assert len({id(item.build) for item in index.facts.values()}) == 1
    assert len(index._contexts) == 1
    index.update([], removed=[str(i) for i in range(32)])
    assert not index._contexts and not index._visibility


def test_global_qualification_and_nearer_type_take_precedence_over_alias():
    item = facts(
        "caller",
        """using Loader = UnityEngine.Resources;
class Use {
class Loader { public static void Load(string value) {} }
void Run() { Loader.Load("two"); global::UnityEngine.Resources.Load("one"); }
}""",
    )
    assert keys(AnalysisIndex([item], catalog())) == {"one"}


def test_inactive_assembly_and_unknown_preprocessor_context_remain_explicit():
    inactive = facts(
        "caller",
        'class Use { void Run() { UnityEngine.Resources.Load("one"); } }',
        {
            "state": "not_in_compilation",
            "revision": "build-one",
            "target": "Linux",
            "assemblies_type": "Editor",
        },
    )
    result = AnalysisIndex([inactive], catalog()).analyze("caller")
    assert not result.edges
    assert any(issue["reason"] == "inactive_build_context" for issue in result.unresolved)
    unknown = parse_csharp(
        "caller",
        "Assets/Caller.cs",
        '#if PLATFORM\nclass Use { void Run() { UnityEngine.Resources.Load("one"); } }\n#endif',
    )
    result = AnalysisIndex([unknown], catalog()).analyze("caller")
    assert any(issue["reason"] == "build_context_unavailable" for issue in result.unresolved)


def test_relative_alias_and_global_alias_bypass_resolve_different_symbols():
    item = facts(
        "caller",
        """
class Keys { public const string Value="one"; }
namespace A {
using Selected = Keys;
class Keys { public const string Value="two"; }
class Use { void Run() {
UnityEngine.Resources.Load(Selected.Value);
UnityEngine.Resources.Load(global::Keys.Value);
} } }""",
    )
    assert keys(AnalysisIndex([item], catalog())) == {"one", "two"}


def test_relative_namespace_alias_and_static_prefix_namespace_are_not_conflated():
    item = facts(
        "caller",
        """
namespace staticApi { class Keys { public const string Value="one"; } }
namespace A.Inner { class Keys { public const string Value="two"; } }
namespace A { using Selected = Inner; using staticApi;
class Use { void Run() { UnityEngine.Resources.Load(Selected.Keys.Value); UnityEngine.Resources.Load(Keys.Value); } }
}""",
    )
    assert keys(AnalysisIndex([item], catalog())) == {"one", "two"}


def test_defaults_resolve_in_declaring_assembly_and_invalidate_callers():
    loader = facts(
        "loader",
        """class Loader {
public const string Key="one";
public static void Load(string value=Key) { UnityEngine.Resources.Load(value); }
}""",
        context("Library"),
    )
    caller = facts(
        "caller", "class Use { void Run() { Loader.Load(); } }", context("Game", ("Library",))
    )
    index = AnalysisIndex([loader, caller], catalog())
    assert keys(index) == {"one"}
    changed = facts(
        "loader",
        """class Loader {
public const string Key="two";
public static void Load(string value=Key) { UnityEngine.Resources.Load(value); }
}""",
        context("Library"),
    )
    assert "caller" in index.update([changed])
    assert keys(index) == {"two"}


def test_known_and_unknown_parameter_types_do_not_guess_overloads():
    item = facts(
        "caller",
        """class Use {
static void Load(string key) { UnityEngine.Resources.Load(key); }
static void Load(int value) { }
void Run(object value) { Load(value); Load(missing:"one"); }
}""",
    )
    result = AnalysisIndex([item], catalog()).analyze("caller")
    assert not result.edges
    assert any(issue["reason"] == "unsupported_call_binding" for issue in result.unresolved)


def test_named_native_loader_key_can_follow_other_arguments():
    item = facts(
        "caller",
        """class Use { void Run() {
UnityEngine.Resources.Load(systemTypeInstance: typeof(object), path:"one");
UnityEngine.AddressableAssets.Addressables.LoadAssetAsync<object>(key:"missing");
} }""",
    )
    result = AnalysisIndex([item], catalog()).analyze("caller")
    assert {edge.key for edge in result.edges} == {"one"}
    assert any(issue.get("key") == "missing" for issue in result.unresolved)


@pytest.mark.parametrize("expression", ["A &&", "(A", "A B", "A | B", "A ==", "!", "A)"])
def test_invalid_preprocessor_conditions_are_rejected(expression):
    with pytest.raises(ValueError):
        facts("caller", f"#if {expression}\nclass C {{}}\n#endif")


def test_nested_inactive_source_and_file_defines_do_not_leak_references():
    item = facts(
        "caller",
        """#define LOCAL
#if ABSENT
"unclosed ignored text
#if LOCAL
class Invalid { }
#endif
#else
class Use { void Run() {
#if LOCAL && (!ABSENT == true) // trailing comment
UnityEngine.Resources.Load("one");
#endif
} }
#endif""",
    )
    assert keys(AnalysisIndex([item], catalog())) == {"one"}
