using System;
using System.Collections;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class SettingsSearchTests
    {
        private sealed class Host : EditorWindow { }

        private static Type SearchType()
        {
            var type = typeof(McpWindowNavigation).Assembly.GetType("MCPForUnity.Editor.Windows.McpSettingsSearch");
            Assert.IsNotNull(type, "Metadata-only settings search must be available.");
            return type;
        }

        [Test]
        public void SearchUsesOnlyAuthoredMetadata()
        {
            var find = SearchType().GetMethod("Find", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(find);
            var matches = (Array)find.Invoke(null, new object[] { "API key" });
            Assert.Greater(matches.Length, 0);
            foreach (var entry in matches)
                StringAssert.DoesNotContain("synthetic-secret-value", entry.ToString());
            Assert.Zero(((Array)find.Invoke(null, new object[] { "synthetic-secret-value" })).Length);
            Assert.Zero(((Array)find.Invoke(null, new object[] { "" })).Length);
        }

        [Test]
        public async System.Threading.Tasks.Task SourceAccessFailuresOfferExplicitConfigurationRoute()
        {
            var host = ScriptableObject.CreateInstance<Host>();
            try
            {
                host.ShowUtility();
                using var section = new MCPForUnity.Editor.Windows.Components.ReferenceGraph.McpReferenceGraphSection(
                    host.rootVisualElement,
                    (_, __, ___) =>
                        System.Threading.Tasks.Task.FromResult(
                            Newtonsoft.Json.Linq.JObject.Parse(
                                "{'graph_status':{'providers':{'external:fixture':{'state':'failed','error':'Access unavailable'}}}}"
                            )
                        ),
                    () => "fixture-account"
                );
                section.SetVisible(true);
                await section.Run("overview", new Newtonsoft.Json.Linq.JObject());
                var button = host.rootVisualElement.Q<Button>("graph-review-source-access");
                Assert.IsNotNull(button);
                typeof(Clickable).GetMethod("Invoke", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button.clickable, new object[] { null });
                Assert.IsTrue(host.rootVisualElement.Q<Foldout>("graph-transfer").value);
                Assert.AreEqual("External sources", host.rootVisualElement.Q<DropdownField>("graph-kind").value);
            }
            finally
            {
                host.Close();
            }
        }

        [UnityTest]
        public IEnumerator RoutesOpenPageFoldoutsAndFocusWithoutChangingValues()
        {
            var host = ScriptableObject.CreateInstance<Host>();
            try
            {
                host.ShowUtility();
                host.position = new Rect(30, 30, 500, 340);
                var root = host.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml").CloneTree(root);
                var group = new Foldout
                {
                    name = "provider-credentials",
                    text = "Credentials",
                    value = false,
                };
                var target = new TextField
                {
                    name = "provider-key-fal",
                    isPasswordField = true,
                    value = "synthetic-secret-value",
                };
                group.Add(target);
                root.Q("assetgen-container").Add(group);
                string selected = null;
                using var navigation = new McpWindowNavigation(
                    root,
                    id =>
                    {
                        selected = id;
                        root.Query<ScrollView>(className: "panel-scroll").ForEach(panel => panel.EnableInClassList("hidden", panel.name != id + "-panel"));
                    }
                );
                var open = typeof(McpWindowNavigation).GetMethod("OpenSetting", BindingFlags.Instance | BindingFlags.NonPublic);
                Assert.IsNotNull(open);
                Assert.IsTrue((bool)open.Invoke(navigation, new object[] { "provider-key-fal" }));
                for (int i = 0; i < 15; i++)
                {
                    host.Repaint();
                    yield return null;
                }
                Assert.AreEqual("assetgen", selected);
                Assert.IsTrue(group.value);
                var focused = root.focusController.focusedElement as VisualElement;
                Assert.IsTrue(focused == target || target.Contains(focused));
                Assert.AreEqual("synthetic-secret-value", target.value);
                Assert.IsFalse((bool)open.Invoke(navigation, new object[] { "not-an-indexed-setting" }));
                for (int i = 0; i < 3; i++)
                    yield return null;
                Assert.IsNotNull(root.Q<Label>("settings-search-message"));
                StringAssert.Contains("unavailable", root.Q<Label>("settings-search-message").text.ToLowerInvariant());
                Assert.Zero(
                    ((Array)SearchType().GetMethod("Find", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { target.value })).Length
                );
            }
            finally
            {
                host.Close();
            }
        }

        [UnityTest, Explicit("Actual settings search keyboard and responsive layout in native light/dark themes.")]
        public IEnumerator ActualSearchKeyboardAndLayout()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var skin = typeof(EditorGUIUtility).GetProperty("skinIndex", BindingFlags.Static | BindingFlags.NonPublic);
            var original = skin.GetValue(null);
            Host host = null;
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/search-" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            try
            {
                foreach (int theme in new[] { 1, 0 })
                {
                    skin.SetValue(null, theme);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(theme == 1, EditorGUIUtility.isProSkin);
                    host = ScriptableObject.CreateInstance<Host>();
                    host.ShowUtility();
                    var root = host.rootVisualElement;
                    root.AddToClassList("mcp-editor");
                    root.EnableInClassList("unity-theme-light", theme == 0);
                    root.EnableInClassList("unity-theme-dark", theme == 1);
                    AssetDatabase
                        .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml")
                        .CloneTree(root);
                    var group = new Foldout { text = "Provider credentials", value = false };
                    var field = new TextField("API key") { name = "provider-key-fal", isPasswordField = true };
                    group.Add(field);
                    root.Q("assetgen-container").Add(group);
                    using var navigation = new McpWindowNavigation(
                        root,
                        id => root.Query<ScrollView>(className: "panel-scroll").ForEach(panel => panel.EnableInClassList("hidden", panel.name != id + "-panel"))
                    );
                    foreach (var size in new[] { new Vector2(500, 340), new Vector2(500, 600), new Vector2(1100, 700) })
                    {
                        host.position = new Rect(30, 30, size.x, size.y);
                        using (var evt = KeyDownEvent.GetPooled('f', KeyCode.F, EventModifiers.Control))
                        {
                            evt.target = root;
                            root.SendEvent(evt);
                        }
                        for (int i = 0; i < 12; i++)
                        {
                            host.Repaint();
                            yield return null;
                        }
                        var query = root.Q<TextField>("settings-search-input");
                        query.value = "API key";
                        for (int i = 0; i < 12; i++)
                        {
                            host.Repaint();
                            yield return null;
                        }
                        Assert.Greater(root.Q<ScrollView>("settings-search-results").childCount, 0);
                        Assert.LessOrEqual(root.Q<ScrollView>("settings-search-results").horizontalScroller.highValue, 1);
                        var toggle = root.Q<Button>("settings-search-toggle");
                        Assert.LessOrEqual(toggle.worldBound.xMax, root.worldBound.xMax);
                        string path = Path.Combine(directory, "settings-" + size.x + "-" + size.y + "-" + theme + ".png");
                        typeof(OperationActivityTests)
                            .GetMethod("Capture", BindingFlags.Static | BindingFlags.NonPublic)
                            .Invoke(null, new object[] { host, path });
                        TestContext.Out.WriteLine("SETTINGS_SEARCH_CAPTURE " + path);
                        query.value = "fal";
                        using (var evt = KeyDownEvent.GetPooled('\n', KeyCode.Return, EventModifiers.None))
                        {
                            evt.target = query;
                            query.SendEvent(evt);
                        }
                        for (int i = 0; i < 12; i++)
                        {
                            host.Repaint();
                            yield return null;
                        }
                        Assert.AreEqual(DisplayStyle.None, root.Q("settings-search-panel").resolvedStyle.display);
                        Assert.IsTrue(group.value);
                        var focus = root.focusController.focusedElement as VisualElement;
                        Assert.IsTrue(focus == field || field.Contains(focus));
                    }
                    host.Close();
                    host = null;
                }
            }
            finally
            {
                if (host != null)
                    host.Close();
                skin.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }
    }
}
