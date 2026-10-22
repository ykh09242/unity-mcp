using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Windows;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class EditorWindowUxTests
    {
        private const string BasePath = "Packages/com.ykh09242.unity-mcp/Editor/Windows/";
        private const BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;
        private static readonly string[] Pages = { "clients", "tools", "resources", "assetgen", "graph", "activity", "deps", "advanced" };

        private sealed class EvidenceWindow : EditorWindow { }

        [Test]
        public void PageSelectorPreservesSavedTabAndUpdatesContext()
        {
            bool had = EditorPrefs.HasKey(EditorPrefKeys.EditorWindowActivePanel);
            string saved = EditorPrefs.GetString(EditorPrefKeys.EditorWindowActivePanel);
            var window = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            try
            {
                EditorPrefs.SetString(EditorPrefKeys.EditorWindowActivePanel, "Advanced");
                var root = window.rootVisualElement;
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(BasePath + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                string[] fields =
                {
                    "clientsPanel",
                    "toolsPanel",
                    "resourcesPanel",
                    "assetGenPanel",
                    "graphPanel",
                    "activityPanel",
                    "depsPanel",
                    "advancedPanel",
                };
                for (int i = 0; i < Pages.Length; i++)
                    typeof(MCPForUnityEditorWindow).GetField(fields[i], PrivateInstance).SetValue(window, root.Q(Pages[i] + "-panel"));
                typeof(MCPForUnityEditorWindow).GetMethod("SetupTabs", PrivateInstance).Invoke(window, null);
                typeof(MCPForUnityEditorWindow).GetField("guiCreated", PrivateInstance).SetValue(window, true);
                window.ShowUtility();
                Assert.IsNotNull(root.panel);
                var selector = root.Q<DropdownField>("page-selector");
                Assert.IsNotNull(selector, "Narrow windows need an accessible page selector.");
                Assert.AreEqual("Advanced", selector.value);
                Assert.AreEqual(8, selector.choices.Count);
                selector.value = "Connect";
                Assert.AreEqual("Clients", EditorPrefs.GetString(EditorPrefKeys.EditorWindowActivePanel));
                Assert.IsFalse(root.Q("clients-panel").ClassListContains("hidden"));
                Assert.IsTrue(root.Q("advanced-panel").ClassListContains("hidden"));
                Assert.AreEqual("Connect", root.Q<Label>("page-title").text);
                Assert.IsNotEmpty(root.Q<Label>("page-description").text);
                root.Q<ToolbarToggle>("advanced-tab").value = true;
                Assert.AreEqual("Advanced", selector.value);
                root.Q<ToolbarToggle>("advanced-tab").value = false;
                Assert.IsTrue(root.Q<ToolbarToggle>("advanced-tab").value);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(window);
                if (had)
                    EditorPrefs.SetString(EditorPrefKeys.EditorWindowActivePanel, saved);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.EditorWindowActivePanel);
            }
        }

        [UnityTest, Explicit("Controller-owned rendered navigation and focus checks.")]
        public IEnumerator KeyboardAndResizeKeepNavigationReachable()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            McpWindowNavigation navigation = null;
            try
            {
                window.minSize = new Vector2(500, 340);
                window.position = new Rect(30, 30, 1100, 340);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(BasePath + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                string selected = "clients";
                navigation = new McpWindowNavigation(
                    root,
                    id =>
                    {
                        selected = id;
                        navigation.Select(id);
                    }
                );
                for (int i = 0; i < 12; i++)
                {
                    window.Repaint();
                    yield return null;
                }
                var first = root.Q<ToolbarToggle>("clients-tab");
                window.Focus();
                first.Focus();
                yield return null;
                Assert.AreSame(first, root.focusController.focusedElement);
                using (var key = KeyDownEvent.GetPooled(new Event { type = EventType.KeyDown, keyCode = KeyCode.End }))
                    first.SendEvent(key);
                Assert.AreEqual("advanced", selected);
                yield return null;
                var last = root.Q<ToolbarToggle>("advanced-tab");
                Assert.AreSame(last, root.focusController.focusedElement);
                var sidebar = root.Q<ScrollView>("navigation-scroll");
                Assert.LessOrEqual(last.worldBound.yMax, sidebar.contentViewport.worldBound.yMax + 1);
                window.position = new Rect(30, 30, 500, 340);
                for (int i = 0; i < 12; i++)
                {
                    window.Repaint();
                    yield return null;
                }
                var selector = root.Q<DropdownField>("page-selector");
                Assert.AreEqual("Advanced", selector.value);
                Assert.AreSame(selector, root.focusController.focusedElement);
                selector.value = "Resources";
                Assert.AreEqual("resources", selected);
                window.position = new Rect(30, 30, 800, 340);
                for (int i = 0; i < 12; i++)
                {
                    window.Repaint();
                    yield return null;
                }
                Assert.AreSame(root.Q<ToolbarToggle>("resources-tab"), root.focusController.focusedElement);
                Assert.Greater(root.Q<ScrollView>("clients-panel").contentViewport.worldBound.height, 80);
                navigation.Dispose();
                selector.value = "Connect";
                Assert.AreEqual("resources", selected, "Disposed navigation must release callbacks.");
            }
            finally
            {
                navigation?.Dispose();
                window.Close();
            }
        }

        [UnityTest, Explicit("Controller-owned, secret-free rendered Editor window audit.")]
        public IEnumerator CaptureAllPagesAtNarrowAndWideWidths() => CaptureThemes(false);

        [UnityTest, Explicit("Controller-owned, secret-free expanded settings layout audit.")]
        public IEnumerator CaptureSettingsAtNarrowAndWideWidths() => CaptureThemes(true);

        private IEnumerator CaptureThemes(bool settings)
        {
            var skinIndex = typeof(EditorGUIUtility).GetProperty("skinIndex", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(skinIndex);
            object original = skinIndex.GetValue(null);
            try
            {
                foreach (int skin in new[] { 1, 0 })
                {
                    skinIndex.SetValue(null, skin);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(skin == 1, EditorGUIUtility.isProSkin);
                    yield return CaptureThemePages(skin == 1 ? "dark" : "light", settings);
                }
            }
            finally
            {
                skinIndex.SetValue(null, original);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }

        private static void PopulateSettings(VisualElement root)
        {
            var protocol = typeof(MCPForUnity.Editor.Windows.Components.Connection.McpConnectionSection).GetNestedType(
                "TransportProtocol",
                BindingFlags.NonPublic
            );
            root.Q<EnumField>("transport-dropdown").Init((Enum)Enum.Parse(protocol, "HTTPLocal"));
            root.Q<TextField>("http-url").SetValueWithoutNotify("http://localhost:8080/mcp");
            root.Q("api-key-row").style.display = DisplayStyle.None;
            root.Q("unity-socket-port-row").style.display = DisplayStyle.None;
            root.Q<Label>("connection-status").text = "Disconnected";
            var clients = root.Q<DropdownField>("client-dropdown");
            clients.choices = new System.Collections.Generic.List<string> { "Example AI client" };
            clients.SetValueWithoutNotify("Example AI client");
            root.Q<Foldout>("client-review-foldout").SetValueWithoutNotify(true);
            root.Q<Foldout>("manual-config-foldout").SetValueWithoutNotify(true);
            root.Q<Foldout>("client-bulk-setup").SetValueWithoutNotify(true);
            root.Q<TextField>("configuration-preview")
                .SetValueWithoutNotify(
                    string.Join("\n", Enumerable.Repeat("Example redacted Unity MCP entry: existing unrelated settings are preserved.", 40))
                );
            root.Q<Label>("configuration-change-status").text = "Preview ready. Review the change before applying it.";
            root.Q<Button>("apply-configuration").SetEnabled(true);
            root.Q<TextField>("config-path").SetValueWithoutNotify("/example/workspaces/large-project/client/configuration/example-client-settings.json");
            root.Q<TextField>("config-json")
                .SetValueWithoutNotify("{\n  \"mcpServers\": {\n    \"unityMCP\": { \"url\": \"http://localhost:8080/mcp\" }\n  }\n}");
            root.Q<Label>("installation-steps").text = "1. Review the configuration.\n2. Reconnect your AI client.";
            root.Q<Foldout>("settings-development").SetValueWithoutNotify(true);
            root.Q<TextField>("git-url-override").SetValueWithoutNotify("/example/workspaces/large-project/tools/unity-mcp/Server");
            root.Q<Label>("deploy-status-label").text = "Example failed deployment: choose a valid package folder and try again.";
        }

        private static IEnumerator CaptureThemePages(string theme, bool settings)
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            Assert.AreNotEqual(UnityEngine.Rendering.GraphicsDeviceType.Null, SystemInfo.graphicsDeviceType);
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            IDisposable navigation = null;
            try
            {
                window.titleContent = new GUIContent("Unity MCP · layout evidence");
                window.minSize = new Vector2(500, 340);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(BasePath + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                foreach (
                    var pair in new[]
                    {
                        ("clients", "Connection"),
                        ("clients", "ClientConfig"),
                        ("tools", "Tools"),
                        ("resources", "Resources"),
                        ("assetgen", "AssetGen"),
                        ("advanced", "Advanced"),
                        ("advanced", "Validation"),
                    }
                )
                    AssetDatabase
                        .LoadAssetAtPath<VisualTreeAsset>(BasePath + "Components/" + pair.Item2 + "/Mcp" + pair.Item2 + "Section.uxml")
                        .CloneTree(root.Q(pair.Item1 + "-container"));
                if (settings)
                    PopulateSettings(root);
                Type navigationType = typeof(MCPForUnityEditorWindow).Assembly.GetType("MCPForUnity.Editor.Windows.McpWindowNavigation");
                Action<string> select = id =>
                {
                    foreach (string page in Pages)
                    {
                        root.Q(page + "-panel").EnableInClassList("hidden", page != id);
                        root.Q<ToolbarToggle>(page + "-tab").SetValueWithoutNotify(page == id);
                    }
                };
                if (navigationType != null)
                    navigation = (IDisposable)Activator.CreateInstance(navigationType, PrivateInstance, null, new object[] { root, select }, null);
                string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/window-" + Guid.NewGuid().ToString("N")));
                Directory.CreateDirectory(directory);
                foreach (int width in settings ? new[] { 500, 800, 1100 } : new[] { 500, 1100 })
                {
                    root.EnableInClassList("unity-theme-dark", theme == "dark");
                    root.EnableInClassList("unity-theme-light", theme == "light");
                    window.position = new Rect(30, 30, width, 900);
                    foreach (string page in settings ? new[] { "clients", "advanced" } : Pages)
                    {
                        select(page);
                        navigationType?.GetMethod("Select", PrivateInstance).Invoke(navigation, new object[] { page });
                        for (int i = 0; i < 12; i++)
                        {
                            window.Repaint();
                            EditorApplication.QueuePlayerLoopUpdate();
                            yield return null;
                        }
                        var scroll = root.Q<ScrollView>(page + "-panel");
                        Assert.LessOrEqual(scroll.contentContainer.worldBound.width, scroll.contentViewport.worldBound.width + 1, page);
                        string path = Path.Combine(directory, page + "-" + width + "-" + theme + ".png");
                        typeof(OperationActivityTests)
                            .GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static)
                            .Invoke(null, new object[] { window, path });
                        TestContext.Out.WriteLine("EDITOR_UX_CAPTURE " + path);
                        if (settings)
                        {
                            var target = root.Q(page == "clients" ? "client-review-foldout" : "settings-development");
                            scroll.ScrollTo(target);
                            for (int i = 0; i < 12; i++)
                            {
                                window.Repaint();
                                yield return null;
                            }
                            Assert.Less(target.worldBound.yMin, scroll.contentViewport.worldBound.yMax, "Expanded controls remain reachable");
                            path = Path.Combine(directory, page + "-expanded-" + width + "-" + theme + ".png");
                            typeof(OperationActivityTests)
                                .GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static)
                                .Invoke(null, new object[] { window, path });
                            TestContext.Out.WriteLine("EDITOR_UX_CAPTURE " + path);
                            scroll.scrollOffset = Vector2.zero;
                        }
                    }
                }
            }
            finally
            {
                navigation?.Dispose();
                window.Close();
            }
        }
    }
}
