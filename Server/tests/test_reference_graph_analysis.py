"""Conservative source analysis must preserve both evidence and uncertainty."""

from services.reference_graph.analysis import (
    AnalysisIndex,
    Catalog,
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


def analyze(source, *others, rules=()):
    facts = parse_csharp("script", "Assets/Use.cs", source)
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
        "%YAML 1.1\n  m_AssetGUID: " + "d" * 32 + '\n  m_AssetGUID: "' + "b" * 32 + '"\n', catalog()
    )
    assert {edge.target for edge in result.edges} == {"asset:" + char * 32 for char in "bd"}
    assert {edge.line for edge in result.edges} == {2, 3}


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
