using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Windows.Components.ReferenceGraph;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class ReferenceExplorerTests
    {
        private sealed class Host : EditorWindow { }

        private Host host;
        private VisualElement Root => host.rootVisualElement;

        [SetUp]
        public void SetUp()
        {
            host = ScriptableObject.CreateInstance<Host>();
            host.ShowUtility();
        }

        [TearDown]
        public void TearDown()
        {
            if (host != null)
                host.Close();
        }

        private static JObject Status() => JObject.Parse("{'graph_status':{'native_current':false,'revision':7,'coverage':'partial_static','providers':{}}}");

        private static UnityEngine.Object Asset() =>
            AssetDatabase.LoadAssetAtPath<MonoScript>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/ReferenceGraph/McpReferenceGraphSection.cs");

        private void Click(string name)
        {
            var button = Root.Q<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledInHierarchy, name);
            typeof(Clickable).GetMethod("Invoke", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(button.clickable, new object[] { null });
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task MissingSelectionValidationIsVisibleAfterInitialRefresh(bool pathView)
        {
            int requests = 0;
            using var section = new McpReferenceGraphSection(
                Root,
                (action, _, __) =>
                {
                    requests++;
                    Assert.AreEqual("overview", action, "Invalid local input must not issue a graph request.");
                    return Task.FromResult(Status());
                },
                () => "one"
            );
            var status = Root.Q<Label>("graph-explorer-status");
            Assert.IsTrue(status.ClassListContains("hidden"), "The initial explorer should stay quiet.");
            if (pathView)
                Root.Q<DropdownField>("graph-explorer-view").value = "Path";
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Assert.IsTrue(status.ClassListContains("hidden"), "Refreshing account status alone should not add explorer guidance.");
            Click("graph-explorer-search");
            Assert.That(status.text, Does.Contain(pathView ? "and a path target" : "Choose a saved project asset"));
            Assert.IsFalse(status.ClassListContains("hidden"), "An explicit invalid Explore action must reveal its local validation.");
            Assert.AreEqual(1, requests);
            Assert.Zero(Root.Q("graph-explorer-rows").childCount);
        }

        [Test]
        public async Task AssetReferencesPageWithoutDroppingResultsOrEagerExplanations()
        {
            int pages = 0,
                explanations = 0;
            string expectedNode = "asset:" + AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(Asset()));
            using var section = new McpReferenceGraphSection(
                Root,
                (action, options, token) =>
                {
                    var data = Status();
                    if (action == "query")
                    {
                        pages++;
                        Assert.AreEqual(expectedNode, options.Value<string>("node"));
                        Assert.AreEqual("incoming", options.Value<string>("direction"));
                        Assert.AreEqual(25, options.Value<int>("limit"));
                        int first = int.Parse(options.Value<string>("cursor") ?? "0");
                        var edges = new JArray();
                        for (int i = first; i < Math.Min(first + 25, 1001); i++)
                            edges.Add(
                                new JObject
                                {
                                    ["id"] = "edge-" + i,
                                    ["owner"] = "asset:source-" + i,
                                    ["target"] = expectedNode,
                                    ["provider"] = "script",
                                    ["confidence"] = "possible",
                                    ["stale"] = true,
                                }
                            );
                        data["edges"] = edges;
                        data["next_cursor"] = first + 25 < 1001 ? (first + 25).ToString() : null;
                    }
                    if (action == "explain")
                    {
                        explanations++;
                        data["edges"] = new JArray(
                            new JObject
                            {
                                ["id"] = options["id"],
                                ["evidence"] = "Recorded wrapper call",
                                ["provider"] = "script",
                                ["owner"] = "asset:source-" + options.Value<string>("id").Replace("edge-", ""),
                                ["target"] = expectedNode,
                                ["source_path"] = "",
                                ["target_path"] = "  ",
                            }
                        );
                    }
                    return Task.FromResult(data);
                },
                () => "account-one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            var asset = Root.Q<ObjectField>("graph-explorer-asset");
            Assert.IsNotNull(asset);
            asset.value = Asset();
            Assert.AreEqual(0, pages);
            Click("graph-explorer-search");
            Click("graph-explorer-explain-edge-0");
            Assert.AreEqual(25, Root.Q("graph-explorer-rows").childCount);
            Assert.IsTrue(Root.Q<Button>("graph-explorer-next").enabledSelf);
            var seen = new HashSet<string>();
            do
            {
                var list = Root.Q("graph-explorer-rows");
                Assert.LessOrEqual(list.childCount, 25);
                foreach (var row in list.Children())
                    Assert.IsTrue(seen.Add(row.name));
                if (!Root.Q<Button>("graph-explorer-next").enabledSelf)
                    break;
                Click("graph-explorer-next");
            } while (true);
            Assert.AreEqual(1001, seen.Count);
            Assert.AreEqual(41, pages);
            Assert.AreEqual(1, explanations);
            StringAssert.Contains("partial", Root.Q<Label>("graph-explorer-status").text);
            Click("graph-explorer-explain-edge-1000");
            Assert.AreEqual(2, explanations);
            StringAssert.Contains("Recorded wrapper call", Root.Q<Label>("graph-explorer-explanation").text);
            StringAssert.Contains("asset:source-1000", Root.Q<Label>("graph-explorer-explanation").text);
            StringAssert.Contains(expectedNode, Root.Q<Label>("graph-explorer-explanation").text);
        }

        private static JObject Page(int first = 0)
        {
            var data = Status();
            var edges = new JArray();
            for (int i = first; i < first + 25; i++)
                edges.Add(
                    new JObject
                    {
                        ["id"] = "edge-" + i,
                        ["owner"] = "asset:source-" + i,
                        ["target"] = "asset:target",
                        ["provider"] = "script",
                    }
                );
            data["edges"] = edges;
            data["next_cursor"] = (first + 25).ToString();
            data["query_id"] = "retained-query";
            return data;
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task TransientExplainFailureRetainsAllRowsAndContinuation(bool unexpectedError)
        {
            int pages = 0,
                explanations = 0;
            using var section = new McpReferenceGraphSection(
                Root,
                (action, options, _) =>
                {
                    if (action == "explain")
                    {
                        explanations++;
                        if (unexpectedError)
                            throw new Exception("Synthetic transport failure");
                        throw new InvalidOperationException("Synthetic transient failure");
                    }
                    if (action == "query")
                    {
                        pages++;
                        if (pages == 2)
                            Assert.AreEqual("25", options.Value<string>("cursor"));
                        return Task.FromResult(Page(pages == 1 ? 0 : 25));
                    }
                    return Task.FromResult(Status());
                },
                () => "one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Root.Q<ObjectField>("graph-explorer-asset").value = Asset();
            Click("graph-explorer-search");
            string pageStatus = Root.Q<Label>("graph-explorer-status").text;
            Click("graph-explorer-explain-edge-0");
            Assert.AreEqual(25, Root.Q("graph-explorer-rows").childCount);
            for (int i = 0; i < 25; i++)
                Assert.IsNotNull(Root.Q<Button>("graph-explorer-explain-edge-" + i));
            Assert.AreEqual(pageStatus, Root.Q<Label>("graph-explorer-status").text);
            Assert.IsTrue(Root.Q<Button>("graph-explorer-next").enabledSelf);
            Assert.AreSame(Root.Q("graph-edge-edge-0"), Root.Q<Foldout>("graph-explorer-details").parent);
            Assert.That(Root.Q<Label>("graph-explorer-explanation").text, Does.Contain("retry"));
            Assert.AreEqual(1, explanations);
            Click("graph-explorer-next");
            Assert.AreEqual(2, pages);
            Assert.AreEqual(25, Root.Q("graph-explorer-rows").childCount);
            Assert.IsNotNull(Root.Q<Button>("graph-explorer-explain-edge-25"));
            Assert.IsTrue(Root.Q<Foldout>("graph-explorer-details").ClassListContains("hidden"));
        }

        [Test]
        public async Task StoppedContinuationPreservesPageAndIgnoresLateCompletion()
        {
            int pages = 0;
            var completion = new TaskCompletionSource<JObject>();
            using var section = new McpReferenceGraphSection(
                Root,
                (action, _, __) =>
                {
                    if (action == "query")
                        return ++pages == 2 ? completion.Task : Task.FromResult(Page(pages == 1 ? 0 : 25));
                    return Task.FromResult(Status());
                },
                () => "one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Root.Q<ObjectField>("graph-explorer-asset").value = Asset();
            Click("graph-explorer-search");
            Task pending = section.Run("query", new JObject { ["cursor"] = "25" });
            try
            {
                Click("graph-stop");
                Assert.AreEqual(25, Root.Q("graph-explorer-rows").childCount);
                Assert.IsTrue(Root.Q<Button>("graph-explorer-next").enabledSelf);
                Assert.That(Root.Q<Label>("graph-explorer-status").text, Does.Contain("Page 1"));
                Click("graph-explorer-next");
                Assert.IsNotNull(Root.Q("graph-edge-edge-25"));
                completion.SetResult(Page(999));
                await pending;
                Assert.IsNotNull(Root.Q("graph-edge-edge-25"));
                Assert.IsNull(Root.Q("graph-edge-edge-999"));
            }
            finally
            {
                completion.TrySetResult(Status());
                await pending;
            }
        }

        [TestCase(401)]
        [TestCase(403)]
        public async Task RevokedAuthorizationClearsRowsCursorAndAccount(int statusCode)
        {
            using var section = new McpReferenceGraphSection(
                Root,
                (action, _, __) =>
                {
                    if (action == "explain")
                        throw (UnauthorizedAccessException)
                            typeof(McpReferenceGraphClient)
                                .GetMethod("AuthorizationFailure", BindingFlags.NonPublic | BindingFlags.Static)
                                .Invoke(null, new object[] { statusCode });
                    return Task.FromResult(action == "query" ? Page() : Status());
                },
                () => "one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Root.Q<ObjectField>("graph-explorer-asset").value = Asset();
            Click("graph-explorer-search");
            Assert.IsTrue(Root.Q<Button>("graph-explorer-next").enabledSelf);
            Click("graph-explorer-explain-edge-0");
            Assert.Zero(Root.Q("graph-explorer-rows").childCount);
            Assert.IsFalse(Root.Q<Button>("graph-explorer-next").enabledSelf);
            Assert.IsFalse(Root.Q<Button>("graph-explorer-search").enabledInHierarchy);
            Assert.IsNull(Root.Q<ObjectField>("graph-explorer-asset").value);
            Assert.That(Root.Q<Label>("graph-explorer-explanation").text, Does.Not.Contain("asset:source"));
            Assert.That(Root.Q<Label>("graph-message").text, Does.Contain(statusCode == 401 ? "Authentication" : "permissions"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task ChangedIdentityDuringExplanationClearsRetainedPage(bool failure)
        {
            string identity = "one";
            var completion = new TaskCompletionSource<JObject>();
            using var section = new McpReferenceGraphSection(
                Root,
                (action, _, __) => action == "explain" ? completion.Task : Task.FromResult(action == "query" ? Page() : Status()),
                () => identity
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Root.Q<ObjectField>("graph-explorer-asset").value = Asset();
            Click("graph-explorer-search");
            Task explain = section.Run("explain", new JObject { ["id"] = "edge-0" });
            identity = "two";
            if (failure)
                completion.SetException(new InvalidOperationException("Synthetic transient failure"));
            else
                completion.SetResult(Page());
            await explain;
            Assert.Zero(Root.Q("graph-explorer-rows").childCount);
            Assert.IsFalse(Root.Q<Button>("graph-explorer-next").enabledSelf);
            Assert.IsNull(Root.Q<ObjectField>("graph-explorer-asset").value);
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task ExplicitExplainRevealsOnlySelectedRowEvidenceOrFallback(bool retained)
        {
            int explanations = 0;
            using var section = new McpReferenceGraphSection(
                Root,
                (action, options, _) =>
                {
                    if (action != "explain")
                        return Task.FromResult(action == "query" ? Page() : Status());
                    explanations++;
                    var data = Status();
                    data["edges"] = retained
                        ? new JArray(
                            new JObject
                            {
                                ["id"] = options["id"],
                                ["source_path"] = "Assets/SyntheticSource.cs",
                                ["target_path"] = "Assets/SyntheticTarget.prefab",
                                ["provider"] = "script",
                                ["kind"] = "wrapper_call",
                                ["confidence"] = "possible",
                                ["stale"] = true,
                                ["target_exists"] = false,
                                ["evidence"] = "Retained synthetic wrapper evidence",
                                ["line"] = 42,
                                ["source_pointer"] = "/synthetic/reference",
                                ["provenance"] = new JObject { ["path"] = "published/synthetic.json" },
                                ["subobject"] = "SyntheticChild",
                                ["target_file_id"] = "12345",
                            }
                        )
                        : new JArray();
                    return Task.FromResult(data);
                },
                () => "one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Root.Q<ObjectField>("graph-explorer-asset").value = Asset();
            Click("graph-explorer-search");
            Assert.Zero(explanations, "Query pages must not eagerly explain rows.");
            Click("graph-explorer-explain-edge-0");
            var details = Root.Q<Foldout>("graph-explorer-details");
            Assert.AreSame(Root.Q("graph-edge-edge-0"), details.parent);
            Assert.IsTrue(details.value);
            Assert.IsFalse(details.ClassListContains("hidden"));
            Assert.AreEqual(25, Root.Q("graph-explorer-rows").childCount);
            string text = Root.Q<Label>("graph-explorer-explanation").text;
            if (retained)
                foreach (
                    string expected in new[]
                    {
                        "Assets/SyntheticSource.cs",
                        "Assets/SyntheticTarget.prefab",
                        "script",
                        "wrapper_call",
                        "possible",
                        "Stale evidence",
                        "missing",
                        "Retained synthetic wrapper evidence",
                        "42",
                        "/synthetic/reference",
                        "published/synthetic.json",
                        "SyntheticChild",
                        "12345",
                    }
                )
                    Assert.That(text, Does.Contain(expected));
            else
                Assert.That(text, Does.Contain("No retained evidence"));
            Click("graph-explorer-explain-edge-24");
            Assert.AreSame(Root.Q("graph-edge-edge-24"), details.parent);
            Assert.AreEqual(1, Root.Query<Foldout>("graph-explorer-details").ToList().Count);
            Assert.AreEqual(2, explanations);
            await section.Run("overview", new JObject());
            Assert.AreSame(Root.Q("graph-edge-edge-24"), details.parent, "Background status refresh must not move selected detail.");
        }

        [Test]
        public async Task ImpactPathAndIdentityChangesKeepRequestsScoped()
        {
            var calls = new List<(string action, JObject options)>();
            string identity = "one";
            TaskCompletionSource<JObject> pending = null;
            using var section = new McpReferenceGraphSection(
                Root,
                (action, options, token) =>
                {
                    calls.Add((action, (JObject)options.DeepClone()));
                    if (pending != null)
                        return pending.Task;
                    var data = Status();
                    data["edges"] = new JArray();
                    if (action == "impact")
                        data["query_id"] = "retained-query";
                    return Task.FromResult(data);
                },
                () => identity
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            var asset = Root.Q<ObjectField>("graph-explorer-asset");
            Assert.IsNotNull(asset);
            asset.value = Asset();
            Root.Q<DropdownField>("graph-explorer-view").value = "Impact";
            Click("graph-explorer-search");
            Assert.AreEqual("impact", calls[calls.Count - 1].action);
            Root.Q<DropdownField>("graph-explorer-view").value = "Path";
            Root.Q<ObjectField>("graph-explorer-target").value = Asset();
            Click("graph-explorer-search");
            Assert.AreEqual("path", calls[calls.Count - 1].action);
            Assert.IsNotNull(calls[calls.Count - 1].options["target"]);
            var released = calls.FindAll(call => call.action == "release_query");
            Assert.AreEqual(1, released.Count);
            Assert.AreEqual("retained-query", released[0].options.Value<string>("id"));
            pending = new TaskCompletionSource<JObject>();
            var request = section.Run("query", new JObject { ["node"] = "asset:old" });
            identity = "two";
            var late = Status();
            late["edges"] = new JArray(new JObject { ["id"] = "private-old-edge" });
            pending.SetResult(late);
            await request;
            Assert.Zero(Root.Q("graph-explorer-rows").childCount);
            Assert.IsNull(asset.value);
            Assert.IsFalse(Root.Q<Button>("graph-explorer-search").enabledInHierarchy);
        }

        [UnityTest, Explicit("Selected explanation reveal with 25 synthetic rows in short and wide windows.")]
        public IEnumerator SelectedExplanationRevealAtShortAndWideSizes()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            string directory = Path.GetFullPath(
                Path.Combine(Application.dataPath, "../Library/McpTestEvidence/explorer-reveal-" + Guid.NewGuid().ToString("N"))
            );
            Directory.CreateDirectory(directory);
            Root.Clear();
            Root.AddToClassList("mcp-editor");
            AssetDatabase.LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml").CloneTree(Root);
            using var navigation = new MCPForUnity.Editor.Windows.McpWindowNavigation(Root, _ => { });
            navigation.Select("graph");
            foreach (var toggle in Root.Query<ToolbarToggle>(className: "navigation-tab").ToList())
                toggle.SetValueWithoutNotify(toggle.name == "graph-tab");
            Root.Q("clients-panel").AddToClassList("hidden");
            Root.Q("graph-panel").RemoveFromClassList("hidden");
            bool retained = true;
            using var section = new McpReferenceGraphSection(
                Root.Q("graph-container"),
                (action, _, __) =>
                {
                    var data = action == "query" ? Page() : Status();
                    if (action == "explain")
                        data["edges"] = retained
                            ? new JArray(
                                new JObject
                                {
                                    ["id"] = "edge-24",
                                    ["source_path"] = "Assets/SyntheticSource.cs",
                                    ["target_path"] = "Assets/SyntheticTarget.prefab",
                                    ["provider"] = "script",
                                    ["kind"] = "wrapper_call",
                                    ["confidence"] = "possible",
                                    ["target_exists"] = true,
                                    ["evidence"] = "Recorded synthetic wrapper evidence",
                                    ["line"] = 42,
                                    ["source_pointer"] = "/synthetic/reference",
                                    ["provenance"] = new JObject { ["path"] = "published/synthetic.json" },
                                    ["subobject"] = "SyntheticChild",
                                    ["target_file_id"] = "12345",
                                }
                            )
                            : new JArray();
                    return Task.FromResult(data);
                },
                () => "synthetic-account"
            );
            section.SetVisible(true);
            section.Run("overview", new JObject()).GetAwaiter().GetResult();
            Root.Q<ObjectField>("graph-explorer-asset").value = Asset();
            Click("graph-explorer-search");
            host.minSize = new Vector2(500, 180);
            var scroll = Root.Q<ScrollView>("graph-panel");
            foreach (var size in new[] { new Vector2(500, 340), new Vector2(1200, 180), new Vector2(1200, 800) })
            foreach (bool hasEvidence in new[] { true, false })
            {
                retained = hasEvidence;
                host.position = new Rect(30, 30, size.x, size.y);
                var button = Root.Q<Button>("graph-explorer-explain-edge-24");
                scroll.ScrollTo(button);
                for (int i = 0; i < 8; i++)
                {
                    host.Repaint();
                    yield return null;
                }
                host.Focus();
                yield return null;
                button.Focus();
                yield return null;
                Assert.AreSame(button, Root.panel.focusController.focusedElement, "The fixture must establish the explicit action's focus before Explain.");
                Click(button.name);
                var explanation = Root.Q<Label>("graph-explorer-explanation");
                Assert.AreSame(explanation, Root.panel.focusController.focusedElement, "Explicit Explain must focus the selected evidence before waiting.");
                for (int i = 0; i < 8; i++)
                {
                    host.Repaint();
                    yield return null;
                }
                Assert.AreSame(Root.Q("graph-edge-edge-24"), Root.Q<Foldout>("graph-explorer-details").parent);
                Assert.IsTrue(explanation.worldBound.Overlaps(scroll.contentViewport.worldBound), "Explicit Explain must reveal evidence or fallback.");
                Assert.AreSame(explanation, Root.panel.focusController.focusedElement, "The selected evidence must retain focus after completion.");
                Assert.LessOrEqual(scroll.horizontalScroller.highValue, 1);
                Vector2 previous = scroll.scrollOffset;
                section.Run("overview", new JObject()).GetAwaiter().GetResult();
                yield return null;
                Assert.AreEqual(previous, scroll.scrollOffset, "A background status refresh must not reveal or focus detail again.");
                Assert.AreSame(explanation, Root.panel.focusController.focusedElement, "Background status refresh must preserve the chosen detail focus.");
                string path = Path.Combine(directory, "explain-" + size.x + "x" + size.y + "-" + (retained ? "retained" : "fallback") + ".png");
                typeof(OperationActivityTests).GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { host, path });
                TestContext.Out.WriteLine("EXPLORER_REVEAL_CAPTURE " + path);
            }
        }

        [UnityTest, Explicit("Actual asset explorer controls and paged evidence in both Editor themes.")]
        public IEnumerator ActualExplorerLayoutAndExplicitQueries()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var skin = typeof(EditorGUIUtility).GetProperty("skinIndex", BindingFlags.Static | BindingFlags.NonPublic);
            var original = skin.GetValue(null);
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/explorer-" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            try
            {
                foreach (int theme in new[] { 1, 0 })
                {
                    host.Close();
                    host = null;
                    skin.SetValue(null, theme);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(theme == 1, EditorGUIUtility.isProSkin);
                    host = ScriptableObject.CreateInstance<Host>();
                    host.ShowUtility();
                    var root = Root;
                    root.Clear();
                    root.AddToClassList("mcp-editor");
                    root.EnableInClassList("unity-theme-dark", theme == 1);
                    root.EnableInClassList("unity-theme-light", theme == 0);
                    AssetDatabase
                        .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml")
                        .CloneTree(root);
                    using var navigation = new MCPForUnity.Editor.Windows.McpWindowNavigation(root, _ => { });
                    navigation.Select("graph");
                    root.Q("clients-panel").AddToClassList("hidden");
                    root.Q("graph-panel").RemoveFromClassList("hidden");
                    using var section = new McpReferenceGraphSection(
                        root.Q("graph-container"),
                        (action, options, token) =>
                        {
                            var data = Status();
                            data["edges"] = new JArray(
                                new JObject
                                {
                                    ["id"] = "rendered-edge",
                                    ["owner"] = "asset:11111111111111111111111111111111",
                                    ["target"] = "asset:22222222222222222222222222222222",
                                    ["provider"] = "external:approved-source",
                                    ["confidence"] = "possible",
                                    ["stale"] = true,
                                    ["kind"] = "addressables",
                                    ["evidence"] = "Recorded wrapper call at the last verified scan",
                                }
                            );
                            return Task.FromResult(data);
                        },
                        () => "rendered-account"
                    );
                    section.SetVisible(true);
                    var initial = section.Run("overview", new JObject());
                    while (!initial.IsCompleted)
                        yield return null;
                    root.Q<ObjectField>("graph-explorer-asset").value = Asset();
                    Click("graph-explorer-search");
                    foreach (var size in new[] { new Vector2(500, 340), new Vector2(500, 600), new Vector2(1100, 700) })
                    {
                        host.position = new Rect(30, 30, size.x, size.y);
                        for (int i = 0; i < 12; i++)
                        {
                            host.Repaint();
                            yield return null;
                        }
                        var scroll = root.Q<ScrollView>("graph-panel");
                        foreach (string control in new[] { "graph-explorer-asset", "graph-explorer-rows" })
                        {
                            scroll.ScrollTo(root.Q(control));
                            for (int i = 0; i < 12; i++)
                            {
                                host.Repaint();
                                yield return null;
                            }
                            Assert.LessOrEqual(scroll.horizontalScroller.highValue, 1);
                            Assert.IsTrue(root.Q(control).worldBound.Overlaps(scroll.contentViewport.worldBound), control);
                            string path = Path.Combine(directory, control + "-" + size.x + "-" + size.y + "-" + theme + ".png");
                            typeof(OperationActivityTests)
                                .GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static)
                                .Invoke(null, new object[] { host, path });
                            TestContext.Out.WriteLine("EXPLORER_CAPTURE " + path);
                        }
                    }
                }
            }
            finally
            {
                skin.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }
    }
}
