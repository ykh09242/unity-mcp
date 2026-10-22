"""Conservative source analysis must preserve both evidence and uncertainty."""

from dataclasses import asdict
from itertools import product
import tracemalloc

import pytest

from services.reference_graph.analysis import (
    AnalysisIndex,
    Catalog,
    Facts,
    parse_csharp,
    serialized_references,
)


def test_isolated_surrogate_escape_remains_unknown_and_facts_are_utf8_safe():
    from dataclasses import asdict
    from services.reference_graph.store import _json

    facts = parse_csharp(
        "script",
        "Assets/Use.cs",
        r'using UnityEngine; class Use { void Go() { Resources.Load("\uD800"); } }',
    )
    _json(asdict(facts)).encode("utf-8")
    result = AnalysisIndex([facts], catalog()).analyze("script")
    assert not result.edges
    assert result.unresolved


def catalog():
    return Catalog(
        [
            {
                "guid": "a" * 32,
                "path": "Assets/Resources/UI/Menu.prefab",
                "keys": [{"scheme": "resources", "value": "UI/Menu"}],
            },
            {
                "guid": "b" * 32,
                "path": "Assets/UI/Hud.prefab",
                "keys": [
                    {"scheme": "addressables", "value": "hud"},
                    {"scheme": "addressables_label", "value": "screens"},
                ],
            },
            {
                "guid": "c" * 32,
                "path": "Assets/UI/Map.prefab",
                "keys": [
                    {"scheme": "addressables", "value": "map"},
                    {"scheme": "addressables_label", "value": "screens"},
                ],
            },
        ]
    )


def test_unused_branching_wrappers_do_not_materialize_exponential_summaries():
    source = "using UnityEngine; class Loader { static void W0(string x) { Resources.Load(x); }"
    for level in range(1, 14):
        source += f'static void W{level}(string x) {{ W{level - 1}(x+"a"); W{level - 1}(x+"b"); }}'
    facts = parse_csharp("wrapper", "Assets/Wrapper.cs", source + "}")
    tracemalloc.start()
    try:
        index = AnalysisIndex([facts], catalog())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024
    result = index.analyze("wrapper")
    assert not result.edges
    assert {issue["reason"] for issue in result.unresolved} == {"dynamic_key"}


def test_incremental_constants_missing_symbols_and_duplicates_invalidate_callers():
    caller = parse_csharp(
        "caller",
        "Assets/Caller.cs",
        "using UnityEngine; class Use { void Go() { Resources.Load(Keys.Value); } }",
    )
    unrelated = parse_csharp(
        "other", "Assets/Other.cs", 'class Other { const string Key = "unused"; }'
    )
    key = parse_csharp(
        "keys", "Assets/Keys.cs", 'class Keys { public const string Value = "UI/Menu"; }'
    )
    index = AnalysisIndex([caller, unrelated], catalog())
    assert not index.analyze("caller").edges
    assert "caller" in index.update([key])
    assert index.facts["other"] is unrelated
    assert {edge.key for edge in index.analyze("caller").edges} == {"UI/Menu"}
    duplicate = parse_csharp(
        "duplicate", "Assets/Duplicate.cs", 'class Keys { public const string Value = "Missing"; }'
    )
    assert "caller" in index.update([duplicate])
    assert not index.analyze("caller").edges
    assert "caller" in index.update([], removed={"duplicate"})
    assert index.analyze("caller").edges
    assert "caller" in index.update([], removed={"keys"})
    assert not index.analyze("caller").edges


def test_incremental_type_shadowing_and_wrapper_change_preserve_reverse_dependencies():
    caller = parse_csharp(
        "caller",
        "Assets/Caller.cs",
        'using UnityEngine; class Use { void Go() { Loader.Load("Menu"); } }',
    )
    wrapper = parse_csharp(
        "wrapper",
        "Assets/Wrapper.cs",
        'using UnityEngine; class Loader { public static object Load(string x) => Resources.Load("UI/" + x); }',
    )
    index = AnalysisIndex([caller, wrapper], catalog())
    assert index.analyze("caller").edges
    shadow = parse_csharp(
        "shadow",
        "Assets/Shadow.cs",
        "class Resources { public static object Load(string x) => null; }",
    )
    assert {"caller", "wrapper"} <= index.update([shadow])
    assert not index.analyze("caller").edges
    assert {"caller", "wrapper"} <= index.update([], removed={"shadow"})
    assert index.analyze("caller").edges


def test_lazy_wrapper_walk_handles_deep_chains_and_checks_cancellation():
    source = "using UnityEngine; class Loader { static void W0(string x) { Resources.Load(x); }"
    for level in range(1, 1101):
        source += f"public static void W{level}(string x) {{ W{level - 1}(x); }}"
    wrapper = parse_csharp("wrapper", "Assets/Wrapper.cs", source + "}")
    caller = parse_csharp(
        "caller", "Assets/Caller.cs", 'class Use { void Go() { Loader.W1100("UI/Menu"); } }'
    )
    cancelled = False

    def check():
        if cancelled:
            raise InterruptedError("cancelled analysis")

    index = AnalysisIndex([wrapper, caller], catalog(), check=check)
    assert {edge.key for edge in index.analyze("caller").edges} == {"UI/Menu"}
    cancelled = True
    with pytest.raises(InterruptedError, match="cancelled analysis"):
        index.analyze("caller")
    with pytest.raises(InterruptedError, match="cancelled analysis"):
        AnalysisIndex([caller], catalog(), check=check)


def test_concrete_branch_fanout_streams_every_key_without_dropping_dynamic_constant_loads():
    source = "using UnityEngine; class Loader { static void W0(string x) { Resources.Load(x); }"
    for level in range(1, 10):
        source += f'static void W{level}(string x) {{ W{level - 1}(x+"a"); W{level - 1}(x+"b"); }}'
    wrapper = parse_csharp("wrapper", "Assets/Wrapper.cs", source + "}")
    caller = parse_csharp(
        "caller", "Assets/Caller.cs", 'class Use { void Run() { Loader.W9(""); } }'
    )
    keys = {"".join(value) for value in product("ab", repeat=9)}
    assets = Catalog(
        [
            {
                "guid": f"{n:032x}",
                "path": f"Assets/{n}.prefab",
                "keys": [{"scheme": "resources", "value": key}],
            }
            for n, key in enumerate(sorted(keys), 1)
        ]
    )
    index = AnalysisIndex([wrapper, caller], assets)
    events = list(index.events("caller"))
    assert len(events) == 512
    assert {edge.key for edge in events} == keys
    dynamic = parse_csharp(
        "dynamic",
        "Assets/Dynamic.cs",
        """using UnityEngine;
class Dynamic {
static void Inner(string x) { Resources.Load(x); Resources.Load("UI/Menu"); }
static void Outer(string x) { Inner(x+"a"); Inner(x+"b"); }
void Run(string input) { Outer(input); }
}""",
    )
    result = AnalysisIndex([dynamic], catalog()).analyze("dynamic")
    assert {edge.key for edge in result.edges} == {"UI/Menu"}
    assert any(issue["reason"] == "dynamic_key" for issue in result.unresolved)


def analyze(source, *others, rules=()):
    facts = parse_csharp("script", "Assets/Use.cs", source)
    facts = Facts.from_dict(asdict(facts))
    index = AnalysisIndex([facts, *others], catalog(), rules)
    return index.analyze("script")


def test_direct_loaders_ignore_comments_and_string_contents():
    result = analyze("""using UnityEngine;
using UnityEngine.AddressableAssets;
class Use { void Go() {
// Resources.Load("fake");
string text = "Resources.Load(\\\"fake\\\")";
Resources.Load<GameObject>("UI/" + "Menu");
Addressables.LoadAssetAsync<GameObject>(@"hud");
} }""")
    assert {(edge.target, edge.line) for edge in result.edges} == {
        ("asset:" + "a" * 32, 6),
        ("asset:" + "b" * 32, 7),
    }
    assert all(edge.confidence == "possible" for edge in result.edges)


def test_nested_cross_file_wrappers_constants_and_changed_dependents():
    wrapper = parse_csharp(
        "wrapper",
        "Assets/Loader.cs",
        """using UnityEngine;
namespace Game { static class Loader {
public static Object Inner(string key) => Resources.Load("UI/" + key);
public static Object Outer(string name) { return Inner(name); }
} }""",
    )
    constants = parse_csharp(
        "constants",
        "Assets/Keys.cs",
        'namespace Game { static class Keys { public const string Menu = "Menu"; } }',
    )
    source = "using Game; class Use { void Go() { Loader.Outer(Keys.Menu); } }"
    facts = parse_csharp("script", "Assets/Use.cs", source)
    index = AnalysisIndex([facts, wrapper, constants], catalog())
    result = index.analyze("script")
    assert {edge.target for edge in result.edges} == {"asset:" + "a" * 32}
    assert {"wrapper", "constants"} <= result.dependencies
    assert "script" in index.affected({"wrapper"})
    changed = parse_csharp(
        "wrapper",
        "Assets/Loader.cs",
        'using UnityEngine; namespace Game { static class Loader { public static Object Outer(string name) => Resources.Load("Missing/" + name); } }',
    )
    after = AnalysisIndex([facts, changed, constants], catalog()).analyze("script")
    assert not after.edges
    assert any(item["reason"] == "key_not_found" for item in after.unresolved)


def test_ambiguous_wrappers_and_dynamic_keys_do_not_invent_exact_edges():
    first = parse_csharp(
        "one",
        "Assets/One.cs",
        "using UnityEngine; class Loader { public static Object Load(string key) => Resources.Load(key); }",
    )
    second = parse_csharp(
        "two",
        "Assets/Two.cs",
        "using UnityEngine; class Loader { public static Object Load(string key) => Resources.Load(key); }",
    )
    result = analyze(
        'using UnityEngine; class Use { void Go(string key) { Resources.Load(key); Loader.Load("UI/Menu"); } }',
        first,
        second,
    )
    assert not result.edges
    assert {item["reason"] for item in result.unresolved} >= {"dynamic_key", "ambiguous_call"}


def test_aliases_global_names_and_shadowed_resource_class():
    result = analyze("""using R = UnityEngine.Resources;
using A = UnityEngine.AddressableAssets.Addressables;
class Resources { public static object Load(string s) => null; }
class Use { void Go() { Resources.Load("UI/Menu"); R.Load("UI/Menu"); A.LoadAssetAsync<object>("hud"); } }""")
    assert len(result.edges) == 2
    assert {edge.key for edge in result.edges} == {"UI/Menu", "hud"}


def test_labels_and_resources_loadall_are_fanout_not_single_target_guesses():
    result = analyze(
        'using UnityEngine; using UnityEngine.AddressableAssets; class Use { void Go() { Resources.LoadAll(""); Addressables.LoadAssetsAsync<object>("screens", null); Addressables.LoadAssetAsync<object>("screens"); } }'
    )
    assert {edge.target for edge in result.edges} == {"asset:" + char * 32 for char in "abc"}
    assert any(item["reason"] == "ambiguous_key" for item in result.unresolved)


def test_recursive_wrappers_are_reported_without_hanging():
    result = analyze(
        'class Loader { public static object A(string s) => B(s); public static object B(string s) => A(s); void Go() { A("x"); } }'
    )
    assert not result.edges
    assert any(item["reason"] == "recursive_wrapper" for item in result.unresolved)


def test_raw_unicode_verbatim_and_interpolated_strings_are_distinct():
    result = analyze(
        'using UnityEngine; class Use { void Go(string x) { Resources.Load("""UI/Menu"""); Resources.Load("UI/\\u004denu"); Resources.Load($"UI/{x}"); } }'
    )
    assert len(result.edges) == 2
    assert any(item["reason"] == "dynamic_key" for item in result.unresolved)


def test_explicit_wrapper_rule_and_invalid_argument_index():
    rule = {
        "call": "Custom.Assets.Fetch",
        "argument": 1,
        "scheme": "resources",
        "prefix": "UI/",
        "mode": "single",
    }
    result = analyze('class Use { void Go() { Custom.Assets.Fetch(42, "Menu"); } }', rules=[rule])
    assert {edge.target for edge in result.edges} == {"asset:" + "a" * 32}
    assert result.edges[0].evidence.startswith("rule:")


def test_changed_missing_symbol_can_become_a_wrapper():
    caller = parse_csharp(
        "caller", "Assets/Caller.cs", 'class Use { void Go() { Loader.Get("UI/Menu"); } }'
    )
    wrapper = parse_csharp(
        "wrapper",
        "Assets/Loader.cs",
        "using UnityEngine; class Loader { public static object Get(string key) => Resources.Load(key); }",
    )
    index = AnalysisIndex([caller, wrapper], catalog())
    assert "caller" in index.affected({"wrapper"})
    assert index.analyze("caller").edges


def test_local_string_is_not_treated_as_a_type_constant():
    result = analyze(
        'using UnityEngine; class Use { const string Key="UI/Menu"; void Go(string Key) { Resources.Load(Key); } }'
    )
    assert not result.edges
    assert any(item["reason"] == "dynamic_key" for item in result.unresolved)


def test_serialized_asset_reference_keeps_missing_guid_evidence():
    result = serialized_references(
        "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!114 &1\nMonoBehaviour:\n  first:\n    m_AssetGUID: "
        + "d" * 32
        + '\n  second:\n    m_AssetGUID: "'
        + "b" * 32
        + '"\n',
        catalog(),
    )
    assert {edge.target for edge in result.edges} == {"asset:" + char * 32 for char in "bd"}
    assert {edge.line for edge in result.edges} == {6, 8}


def test_preprocessor_and_nonliteral_expressions_remain_explicitly_partial():
    result = analyze(
        'using UnityEngine; class Use { void Go() {\n#if ANY\nResources.Load("UI/Menu");\n#endif\nResources.Load(Path.Combine("UI", "Menu")); } }'
    )
    assert result.coverage == "partial_static"
    assert any(item["reason"] == "dynamic_key" for item in result.unresolved)


def test_local_variable_shadow_and_unaddressable_guid_do_not_invent_edges():
    result = analyze(
        'using UnityEngine; using UnityEngine.AddressableAssets; class Use { const string Key="UI/Menu"; void Go() { string Key=GetKey(); Resources.Load(Key); Addressables.LoadAssetAsync<object>("'
        + "a" * 32
        + '"); } }'
    )
    assert not result.edges
    assert {item["reason"] for item in result.unresolved} >= {"dynamic_key", "key_not_found"}


def test_facts_roundtrip_and_long_wrapper_chain():
    import dataclasses
    import json
    from services.reference_graph.analysis import Facts

    source = (
        "using UnityEngine; class Loader { public static object W0(string key) => Resources.Load(key);"
        + "".join(
            f"public static object W{i}(string key) => W{i - 1}(key);" for i in range(1, 1200)
        )
        + 'void Go() { W1199("UI/Menu"); } }'
    )
    facts = parse_csharp("script", "Assets/Long.cs", source)
    restored = Facts.from_dict(json.loads(json.dumps(dataclasses.asdict(facts))))
    assert restored == facts
    result = AnalysisIndex([restored], catalog()).analyze("script")
    assert {edge.target for edge in result.edges} == {"asset:" + "a" * 32}


def test_reassigned_wrapper_argument_is_not_substituted_with_old_caller_value():
    result = analyze(
        'using UnityEngine; class Loader { static object Get(string key) { key="elsewhere"; return Resources.Load(key); } void Go() { Get("UI/Menu"); } }'
    )
    assert not result.edges
    assert any(item["reason"] == "dynamic_key" for item in result.unresolved)


def test_shadowed_receivers_never_resolve_to_unity_or_static_wrapper_types():
    sources = [
        'class Use { void Go(object Resources) { Resources.Load("UI/Menu"); } }',
        'class Use { object Resources; void Go() { Resources.Load("UI/Menu"); } }',
        'class Use { void Go() { var Resources = Get(); Resources.Load("UI/Menu"); } }',
        'class Use { object Resources { get; set; } void Go() { Resources.Load("UI/Menu"); } }',
        'class Use { object Resources => Get(); void Go() { Resources.Load("UI/Menu"); } }',
        'class Use { Custom<int> Resources = Get(); void Go() { Resources.Load("UI/Menu"); } }',
        'class Use { void Go() { Custom<int> Resources = Get(); Resources.Load("UI/Menu"); } }',
        'class Use { Custom first, Resources; void Go() { Resources.Load("UI/Menu"); } }',
    ]
    for source in sources:
        result = analyze("using UnityEngine; " + source)
        assert not result.edges, source
        assert any(item["reason"] == "unresolved_receiver" for item in result.unresolved)


def test_added_shadow_type_or_ambiguous_wrapper_invalidates_existing_callers():
    caller = parse_csharp(
        "caller",
        "Assets/Use.cs",
        'using UnityEngine; class Use { void Go() { Resources.Load("UI/Menu"); } }',
    )
    shadow = parse_csharp(
        "shadow",
        "Assets/Shadow.cs",
        "class Resources : Custom {} class Custom { public static object Load(string key) => null; }",
    )
    index = AnalysisIndex([caller, shadow], catalog())
    assert not index.analyze("caller").edges
    assert "caller" in index.affected({"shadow"})
    wrapper_source = "using UnityEngine; class Loader { public static object Get(string key) => Resources.Load(key); }"
    first = parse_csharp("first", "Assets/First.cs", wrapper_source)
    second = parse_csharp("second", "Assets/Second.cs", wrapper_source)
    use = parse_csharp("use", "Assets/Use.cs", 'class Use { void Go() { Loader.Get("UI/Menu"); } }')
    ambiguous = AnalysisIndex([first, second, use], catalog())
    assert not ambiguous.analyze("use").edges
    assert "use" in ambiguous.affected({"second"})


def test_deconstruction_and_ref_mutation_taint_formal_arguments():
    for assignment in ('(key, _) = ("elsewhere", 1);', "Rewrite(ref key);", 'key += "Changed";'):
        result = analyze(
            "using UnityEngine; class Loader { static object Get(string key) { "
            + assignment
            + ' return Resources.Load(key); } void Go() { Get("UI/Menu"); } }'
        )
        assert not result.edges, assignment
        assert any(item["reason"] == "dynamic_key" for item in result.unresolved)


def test_resources_case_normalization_is_scheme_specific_and_preserves_key():
    result = analyze(
        'using UnityEngine; class C { void M() { Resources.Load("ui/menu"); Resources.LoadAll("ui"); } }'
    )
    assert len(result.edges) == 2
    assert result.edges[0].key == "ui/menu"
    assert catalog().resolve("addressables", "HUD")[1] == "key_not_found"
    collision = Catalog(
        [
            {
                "guid": "a" * 32,
                "path": "Assets/A",
                "keys": [{"scheme": "resources", "value": "UI/Menu"}],
            },
            {
                "guid": "b" * 32,
                "path": "Assets/B",
                "keys": [{"scheme": "resources", "value": "ui/menu"}],
            },
        ]
    )
    assert collision.resolve("resources", "Ui/Menu")[1] == "ambiguous_key"
    assert len(collision.resolve("resources", "UI", "all")[0]) == 2


def test_resources_unicode_case_does_not_expand_distinct_key_spelling():
    values = ["UI/Écran", "Straße", "STRASSE"]
    assets = Catalog(
        [
            {
                "guid": f"{i:032x}",
                "path": f"Assets/{i}",
                "keys": [{"scheme": "resources", "value": value}],
            }
            for i, value in enumerate(values, 1)
        ]
    )
    assert assets.resolve("resources", "ui/écran")[0] == {"asset:" + f"{1:032x}"}
    assert assets.resolve("resources", "Straße")[0] == {"asset:" + f"{2:032x}"}
    assert assets.resolve("resources", "strasse")[0] == {"asset:" + f"{3:032x}"}


def test_instantiation_scene_loaders_and_wrappers_produce_evidence_or_diagnostics():
    result = analyze("""using UnityEngine.AddressableAssets;
class Loader { public static object Spawn(string key) => Addressables.InstantiateAsync(key); }
class C { void M(object location) {
Addressables.InstantiateAsync("hud"); Addressables.LoadSceneAsync("map");
Loader.Spawn("hud"); Addressables.LoadSceneAsync(location);
} }""")
    assert {(edge.key, edge.line) for edge in result.edges} == {("hud", 4), ("map", 4), ("hud", 5)}
    assert any(
        issue["reason"] == "dynamic_key" and issue["line"] == 5 for issue in result.unresolved
    )


def test_sibling_namespace_aliases_and_imports_do_not_bleed():
    source = """namespace A { using R = Other.Loader; class C { void M() { R.Load("UI/Menu"); } } }
namespace B { using R = UnityEngine.Resources; class C { void M() { R.Load("UI/Menu"); } } }
namespace Other { class Loader { public static object Load(string key) => null; } }
namespace C { using UnityEngine; class C { void M() { Resources.Load("UI/Menu"); } } }
namespace D { class C { void M() { Resources.Load("UI/Menu"); } } }"""
    result = analyze(source)
    assert {edge.line for edge in result.edges} == {2, 4}


def test_nested_and_reopened_namespaces_keep_their_lexical_aliases():
    result = analyze("""using R = UnityEngine.Resources;
namespace A { class C { void M() { R.Load("UI/Menu"); } }
namespace Inner { using R = Other.Loader; class C { void M() { R.Load("UI/Menu"); } } } }
namespace A { using R = Other.Loader; class D { void M() { R.Load("UI/Menu"); } } }
namespace Other { class Loader { public static object Load(string key) => null; } }""")
    assert {edge.line for edge in result.edges} == {2}
    scoped = analyze(
        'using R = UnityEngine.Resources; namespace Game; class C { void M() { R.Load("UI/Menu"); } }'
    )
    assert len(scoped.edges) == 1


def test_constant_symbols_keep_lexical_alias_context():
    result = analyze("""using UnityEngine;
namespace One { class Keys { public const string Value = "UI/Menu"; } }
namespace Two { class Keys { public const string Value = "Missing"; } }
namespace A { using K = One.Keys; class C { public const string Key = K.Value; void M() { Resources.Load(Key); } } }
namespace B { using K = Two.Keys; class C { public const string Key = K.Value; void M() { Resources.Load(Key); } } }""")
    assert {edge.line for edge in result.edges} == {4}


@pytest.mark.parametrize("scalar", ["|", ">-", "|2-"])
def test_yaml_text_scalars_cannot_invent_asset_references(scalar):
    source = f"notes: {scalar}\n  m_AssetGUID: {'a' * 32}\nreal:\n  m_AssetGUID: {'b' * 32}\n"
    result = serialized_references(source, catalog())
    assert {edge.target for edge in result.edges} == {"asset:" + "b" * 32}
    assert all(edge.confidence == "possible" for edge in result.edges)


def test_yaml_quotes_comments_flow_and_custom_fields_keep_conservative_evidence():
    source = f"""notes: "multiline
  m_AssetGUID: {"a" * 32}
  text"
# m_AssetGUID: {"a" * 32}
custom: {{m_AssetGUID: {"b" * 32}}}
"""
    result = serialized_references(source, catalog())
    assert len(result.edges) == 1
    assert result.edges[0].target == "asset:" + "b" * 32
    assert result.edges[0].confidence == "possible"
    assert result.edges[0].kind == "serialized_guid_field"


def test_unscannable_yaml_remains_an_explicit_collection_failure():
    with pytest.raises(ValueError, match="YAML"):
        serialized_references('notes: "unterminated', catalog())
