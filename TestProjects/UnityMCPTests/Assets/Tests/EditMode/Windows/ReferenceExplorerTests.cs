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
