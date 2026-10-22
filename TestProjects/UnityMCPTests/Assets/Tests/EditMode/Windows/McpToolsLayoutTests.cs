using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows.Components.Tools;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class McpToolsLayoutTests
    {
        private sealed class EvidenceWindow : EditorWindow { }

        [UnityTest]
        public IEnumerator ToolsPage_RendersFrameworkSelectionAtNarrowAndWideWidths()
        {
            if (UnityEngine.SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
                Assert.Ignore("A graphics-enabled Editor is required for real window evidence.");
            var discoveryField = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", BindingFlags.Static | BindingFlags.NonPublic);
            var transportField = typeof(MCPServiceLocator).GetField("_transportManager", BindingFlags.Static | BindingFlags.NonPublic);
            object oldDiscovery = discoveryField.GetValue(null),
                oldTransport = transportField.GetValue(null);
            EvidenceWindow window = null;
            try
            {
                var tools = new LayoutDiscovery();
                MCPServiceLocator.Register<IToolDiscoveryService>(tools);
                MCPServiceLocator.Register(new TransportManager());
                window = ScriptableObject.CreateInstance<EvidenceWindow>();
                window.titleContent = new GUIContent("Unity MCP Tools");
                window.minSize = new Vector2(360, 420);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                var frame = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml");
                Assert.IsNotNull(frame);
                frame.CloneTree(root);
                using var navigation = new MCPForUnity.Editor.Windows.McpWindowNavigation(root, _ => { });
                navigation.Select("tools");
                root.Q<VisualElement>("clients-panel").AddToClassList("hidden");
                root.Q<VisualElement>("tools-panel").RemoveFromClassList("hidden");
                root.Q<UnityEditor.UIElements.ToolbarToggle>("clients-tab").SetValueWithoutNotify(false);
                root.Q<UnityEditor.UIElements.ToolbarToggle>("tools-tab").SetValueWithoutNotify(true);
                var tree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                    "Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/Tools/McpToolsSection.uxml"
                );
                var content = tree.CloneTree();
                root.Q<VisualElement>("tools-container").Add(content);
                new McpToolsSection(content, null, false).Refresh();
                foreach (var foldout in content.Query<Foldout>(className: "catalog-category").ToList())
                    foldout.SetValueWithoutNotify(foldout.text.StartsWith("User Interface", StringComparison.Ordinal));
                string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/" + Guid.NewGuid().ToString("N")));
                Directory.CreateDirectory(directory);

                foreach (int width in new[] { 420, 1000 })
                {
                    window.position = new Rect(40, 40, width, 860);
                    window.Repaint();
                    for (int i = 0; i < 12; i++)
                    {
                        EditorApplication.QueuePlayerLoopUpdate();
                        window.Repaint();
                        yield return null;
                    }
                    Assert.That(root.resolvedStyle.width, Is.EqualTo(width).Within(2));
                    foreach (
                        string name in new[]
                        {
                            "recommended-tools-button",
                            "enable-all-button",
                            "disable-all-button",
                            "tools-search",
                            "tools-state-filter",
                            "tool-toggle-ui_source",
                            "tool-toggle-manage_ugui",
                        }
                    )
                    {
                        var control = root.Q<VisualElement>(name);
                        Assert.IsNotNull(control, name);
                        Assert.That(control.worldBound.width, Is.GreaterThan(8), name);
                        Assert.That(control.worldBound.xMin, Is.GreaterThanOrEqualTo(root.worldBound.xMin - 1), name);
                        Assert.That(control.worldBound.xMax, Is.LessThanOrEqualTo(root.worldBound.xMax + 1), name);
                    }
                    foreach (var tab in root.Query<UnityEditor.UIElements.ToolbarToggle>(className: "navigation-tab").ToList())
                    {
                        if (tab.resolvedStyle.display == DisplayStyle.None || root.Q("navigation-scroll").resolvedStyle.display == DisplayStyle.None)
                            continue;
                        var label = tab.Q<TextElement>(className: "unity-toggle__text");
                        Assert.IsNotNull(label, tab.name);
                        var measured = label.MeasureTextSize(label.text, 0, VisualElement.MeasureMode.Undefined, 0, VisualElement.MeasureMode.Undefined);
                        Assert.That(label.contentRect.width, Is.GreaterThanOrEqualTo(measured.x - 1), tab.name + " text must fit");
                    }
                    var visibleToggles = content.Query<Toggle>(className: "tool-item-toggle").ToList().Where(toggle => toggle.worldBound.width > 0).ToList();
                    Assert.That(visibleToggles.Count, Is.EqualTo(2), "Both UI framework choices must be rendered.");
                    foreach (var toggle in visibleToggles)
                    {
                        var check = toggle.Q<VisualElement>(className: "unity-toggle__checkmark");
                        var label = toggle.Q<TextElement>(className: "unity-toggle__text");
                        Assert.That(label.worldBound.xMin - check.worldBound.xMax, Is.GreaterThanOrEqualTo(5), toggle.name + " checkbox spacing");
                    }
                    string path = Path.Combine(directory, "tools-" + width + ".png");
                    Capture(window, path);
                    TestContext.WriteLine("TOOLS_LAYOUT_CAPTURE " + path);
                }
                content.Q<DropdownField>("tools-state-filter").value = "Enabled";
                content.Q<TextField>("tools-search").value = "Canvas";
                var ugui = content.Q<Toggle>("tool-toggle-manage_ugui");
                ugui.parent.parent.Q<Foldout>(className: "tool-details").value = true;
                for (int i = 0; i < 12; i++)
                {
                    EditorApplication.QueuePlayerLoopUpdate();
                    window.Repaint();
                    yield return null;
                }
                Assert.That(content.Q<Label>("tools-visible-count").text, Is.EqualTo("1 / 8 shown"));
                string filtered = Path.Combine(directory, "tools-filtered-details.png");
                Capture(window, filtered);
                TestContext.WriteLine("TOOLS_LAYOUT_CAPTURE " + filtered);
                window.position = new Rect(40, 40, 420, 860);
                content.Q<DropdownField>("tools-state-filter").value = "All";
                foreach (string query in new[] { "Physics Queries", "Compatibility" })
                {
                    content.Q<TextField>("tools-search").value = query;
                    for (int i = 0; i < 12; i++)
                    {
                        EditorApplication.QueuePlayerLoopUpdate();
                        window.Repaint();
                        yield return null;
                    }
                    Assert.That(content.Q<Label>("tools-visible-count").text, Is.EqualTo("1 / 8 shown"));
                    string featurePath = Path.Combine(directory, query == "Compatibility" ? "tools-compatibility.png" : "tools-physics-query.png");
                    Capture(window, featurePath);
                    TestContext.WriteLine("TOOLS_LAYOUT_CAPTURE " + featurePath);
                }
            }
            finally
            {
                if (window != null)
                    window.Close();
                discoveryField.SetValue(null, oldDiscovery);
                transportField.SetValue(null, oldTransport);
            }
        }

        private static void Capture(EditorWindow window, string path)
        {
            object host = typeof(EditorWindow).GetField("m_Parent", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(window);
            MethodInfo grab = host.GetType()
                .GetMethod(
                    "GrabPixels",
                    BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(RenderTexture), typeof(Rect) },
                    null
                );
            Assert.IsNotNull(grab, "Use actual Editor view pixels.");
            int width = Mathf.RoundToInt(window.rootVisualElement.resolvedStyle.width * EditorGUIUtility.pixelsPerPoint);
            int height = Mathf.RoundToInt(window.rootVisualElement.resolvedStyle.height * EditorGUIUtility.pixelsPerPoint);
            var rt = new RenderTexture(width, height, 0, RenderTextureFormat.ARGB32);
            var old = RenderTexture.active;
            Texture2D texture = null;
            try
            {
                rt.Create();
                grab.Invoke(host, new object[] { rt, new Rect(0, 0, width, height) });
                RenderTexture.active = rt;
                texture = new Texture2D(width, height, TextureFormat.RGBA32, false);
                texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                var pixels = texture.GetPixels32();
                var flipped = new Color32[pixels.Length];
                for (int y = 0; y < height; y++)
                    Array.Copy(pixels, y * width, flipped, (height - y - 1) * width, width);
                texture.SetPixels32(flipped);
                texture.Apply();
                File.WriteAllBytes(path, texture.EncodeToPNG());
            }
            finally
            {
                RenderTexture.active = old;
                if (texture != null)
                    UnityEngine.Object.DestroyImmediate(texture);
                rt.Release();
                UnityEngine.Object.DestroyImmediate(rt);
            }
        }

        private sealed class LayoutDiscovery : IToolDiscoveryService
        {
            private readonly List<ToolMetadata> tools = CreateRepresentativeCatalog();

            private static List<ToolMetadata> CreateRepresentativeCatalog()
            {
                var discovery = new ToolDiscoveryService();
                return new[]
                {
                    "scene_inspect",
                    "gameobject_create",
                    "ui_source",
                    "manage_ugui",
                    "capture_camera",
                    "manage_render_volumes",
                    "query_physics",
                    "manage_ui",
                }
                    .Select(discovery.GetToolMetadata)
                    .ToList();
            }

            public List<ToolMetadata> DiscoverAllTools() => tools.ToList();

            public ToolMetadata GetToolMetadata(string name) => tools.Find(tool => tool.Name == name);

            public List<ToolMetadata> GetEnabledTools() => tools.FindAll(tool => IsToolEnabled(tool.Name));

            public bool IsToolEnabled(string name) => name == "manage_ugui" || name == "scene_inspect";

            public void SetToolEnabled(string name, bool enabled) => Assert.Fail("Render inspection cannot change user selection.");

            public void InvalidateCache() => Assert.Fail("Render inspection cannot rescan.");
        }
    }
}
