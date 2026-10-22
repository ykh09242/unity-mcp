using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.Activity;
using MCPForUnity.Editor.Windows.Components.AssetGen;
using MCPForUnity.Editor.Windows.Components.ReferenceGraph;
using MCPForUnity.Editor.Windows.Components.Resources;
using MCPForUnity.Editor.Windows.Components.Tools;
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
    public sealed class EditorCatalogLayoutTests
    {
        private const string BasePath = "Packages/com.ykh09242.unity-mcp/Editor/Windows/";
        private const BindingFlags PrivateStatic = BindingFlags.NonPublic | BindingFlags.Static;
        private static readonly string[] Pages = { "tools", "resources", "assetgen", "graph", "activity", "deps" };

        private sealed class EvidenceWindow : EditorWindow { }

        private sealed class NoKeys : ISecureKeyStore
        {
            public bool Has(string id) => false;

            public bool TryGet(string id, out string key)
            {
                key = null;
                return false;
            }

            public void Set(string id, string key) => Assert.Fail("Layout inspection must not store keys.");

            public void Delete(string id) => Assert.Fail("Layout inspection must not delete keys.");
        }

        private sealed class ResourceCatalog : IResourceDiscoveryService
        {
            private readonly List<ResourceMetadata> resources = new()
            {
                new ResourceMetadata
                {
                    Name = "editor_state",
                    Description = "Editor readiness, active scene and current play mode.",
                    IsBuiltIn = true,
                },
                new ResourceMetadata
                {
                    Name = "project_info",
                    Description = "Project settings, loaded assemblies and installed capabilities.",
                    IsBuiltIn = true,
                },
                new ResourceMetadata
                {
                    Name = "custom_build_configuration",
                    Description = "Project-defined build variants and content preparation status.",
                    IsBuiltIn = false,
                },
            };

            public List<ResourceMetadata> DiscoverAllResources() => resources.ToList();

            public ResourceMetadata GetResourceMetadata(string name) => resources.Find(r => r.Name == name);

            public List<ResourceMetadata> GetEnabledResources() => resources.FindAll(r => IsResourceEnabled(r.Name));

            public bool IsResourceEnabled(string name) => name == "editor_state";

            public void SetResourceEnabled(string name, bool value) => Assert.Fail("Rendering and searching must not change resource enablement.");

            public void InvalidateCache() => Assert.Fail("Rendering must not rescan resources.");
        }

        [UnityTest, Explicit("Controller-owned populated catalog and operation layout evidence.")]
        public IEnumerator PopulatedPagesAndEmptyStatesUseRealViewsInBothThemes()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            Assert.AreNotEqual(UnityEngine.Rendering.GraphicsDeviceType.Null, SystemInfo.graphicsDeviceType);
            var skinIndex = typeof(EditorGUIUtility).GetProperty("skinIndex", PrivateStatic);
            object originalSkin = skinIndex.GetValue(null);
            var storeField = typeof(SecureKeyStore).GetField("_current", PrivateStatic);
            var originalStore = storeField.GetValue(null);
            string[] serviceFields = { "_toolDiscoveryService", "_resourceDiscoveryService", "_transportManager" };
            var services = serviceFields.ToDictionary(name => name, name => typeof(MCPServiceLocator).GetField(name, PrivateStatic).GetValue(null));
            string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/catalog-" + Guid.NewGuid().ToString("N")));
            Directory.CreateDirectory(directory);
            var fal = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No catalog request is expected during layout inspection.") };
            var router = new FakeHttpTransport
            {
                Handler = _ => throw new InvalidOperationException("No catalog request is expected during layout inspection."),
            };
            try
            {
                SecureKeyStore.OverrideForTests(new NoKeys());
                AssetGenModelCatalog.ResetForTests(true);
                FalModelCatalog.CachePathOverrideForTests = Path.Combine(directory, "fal.json");
                OpenRouterModelCatalog.CachePathOverrideForTests = Path.Combine(directory, "router.json");
                FalModelCatalog.TransportOverrideForTests = fal;
                OpenRouterModelCatalog.TransportOverrideForTests = router;
                MCPServiceLocator.Register<IToolDiscoveryService>(
                    (IToolDiscoveryService)Activator.CreateInstance(typeof(McpToolsLayoutTests).GetNestedType("LayoutDiscovery", BindingFlags.NonPublic), true)
                );
                MCPServiceLocator.Register<IResourceDiscoveryService>(new ResourceCatalog());
                MCPServiceLocator.Register(new TransportManager());
                foreach (int skin in new[] { 1, 0 })
                {
                    skinIndex.SetValue(null, skin);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(skin == 1, EditorGUIUtility.isProSkin);
                    yield return CaptureTheme(directory, skin == 1 ? "dark" : "light");
                }
                Assert.IsEmpty(fal.RecordedRequests);
                Assert.IsEmpty(router.RecordedRequests);
            }
            finally
            {
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests((ISecureKeyStore)originalStore);
                foreach (var pair in services)
                    typeof(MCPServiceLocator).GetField(pair.Key, PrivateStatic).SetValue(null, pair.Value);
                skinIndex.SetValue(null, originalSkin);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }

        private static IEnumerator CaptureTheme(string directory, string theme)
        {
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            var dependencies = ScriptableObject.CreateInstance<MCPForUnityEditorWindow>();
            McpWindowNavigation navigation = null;
            McpActivitySection activity = null;
            McpReferenceGraphSection graph = null;
            try
            {
                window.titleContent = new GUIContent("Unity MCP · populated layout evidence");
                window.minSize = new Vector2(500, 340);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                root.AddToClassList("unity-theme-" + theme);
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(BasePath + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                navigation = new McpWindowNavigation(root, _ => { });
                VisualElement AddTemplate(string page, string component)
                {
                    var content = AssetDatabase
                        .LoadAssetAtPath<VisualTreeAsset>(BasePath + "Components/" + component + "/Mcp" + component + "Section.uxml")
                        .CloneTree();
                    root.Q(page + "-container").Add(content);
                    return content;
                }
                var tools = AddTemplate("tools", "Tools");
                new McpToolsSection(tools, null, false).Refresh();
                tools.Q<TextField>("tools-search").value = "User Interface";
                var resources = AddTemplate("resources", "Resources");
                new McpResourcesSection(resources).Refresh();
                foreach (var group in resources.Query<Foldout>().ToList())
                    group.SetValueWithoutNotify(true);
                var generation = AddTemplate("assetgen", "AssetGen");
                // Exercise the real provider view without probing an external Blender installation.
                var blender = generation.Q("blender-bridge-panel");
                var blenderParent = blender.parent;
                int blenderIndex = blenderParent.IndexOf(blender);
                blender.RemoveFromHierarchy();
                new McpAssetGenSection(generation);
                blenderParent.Insert(blenderIndex, blender);
                var rows = (List<OperationActivityRow>)typeof(OperationActivityTests).GetMethod("SampleRows", PrivateStatic).Invoke(null, null);
                long revision = 0;
                activity = new McpActivitySection(
                    root.Q("activity-container"),
                    () => rows,
                    () => revision,
                    _ => Assert.Fail("No cancellation during rendering."),
                    _ => Assert.Fail("No evidence action during rendering.")
                );
                graph = new McpReferenceGraphSection(
                    root.Q("graph-container"),
                    (_, __, ___) => Task.FromResult((JObject)typeof(ReferenceGraphPanelTests).GetMethod("Sample", PrivateStatic).Invoke(null, null)),
                    () => "layout-only"
                );
                graph.SetVisible(true);
                graph.Run("overview", new JObject()).GetAwaiter().GetResult();
                typeof(MCPForUnityEditorWindow)
                    .GetMethod("BuildDependenciesSection", BindingFlags.NonPublic | BindingFlags.Instance)
                    .Invoke(dependencies, new object[] { root.Q("deps-container") });
                foreach (int width in new[] { 500, 800, 1100 })
                {
                    window.position = new Rect(30, 30, width, 1000);
                    foreach (string page in Pages)
                    {
                        foreach (string item in new[] { "clients", "tools", "resources", "assetgen", "graph", "activity", "deps", "advanced" })
                        {
                            root.Q(item + "-panel").EnableInClassList("hidden", item != page);
                            root.Q<ToolbarToggle>(item + "-tab").SetValueWithoutNotify(item == page);
                        }
                        navigation.Select(page);
                        activity.SetVisible(page == "activity");
                        yield return Settle(window);
                        var scroll = root.Q<ScrollView>(page + "-panel");
                        Assert.LessOrEqual(scroll.contentContainer.worldBound.width, scroll.contentViewport.worldBound.width + 1, page);
                        foreach (var button in root.Q(page + "-container").Query<Button>().ToList())
                        {
                            if (button.worldBound.width < 1)
                                continue;
                            Assert.GreaterOrEqual(button.worldBound.xMin, root.worldBound.xMin - 1, page + ": " + button.text);
                            Assert.LessOrEqual(button.worldBound.xMax, root.worldBound.xMax + 1, page + ": " + button.text);
                        }
                        Capture(window, Path.Combine(directory, page + "-" + width + "-" + theme + ".png"));
                        if (page == "graph")
                        {
                            var transfer = root.Q<Foldout>("graph-transfer");
                            var editor = root.Q<Foldout>("graph-item-editor");
                            transfer.value = editor.value = true;
                            transfer.Q<DropdownField>().value = "External sources";
                            yield return Settle(window);
                            scroll.ScrollTo(editor);
                            yield return Settle(window);
                            Capture(window, Path.Combine(directory, "graph-permissions-" + width + "-" + theme + ".png"));
                            transfer.value = editor.value = false;
                            scroll.scrollOffset = Vector2.zero;
                        }
                        if (page == "assetgen")
                        {
                            foreach (var category in generation.Query<Foldout>(className: "provider-category").ToList())
                                category.value = false;
                            var setup = generation.Q<Foldout>("blender-setup-foldout");
                            var imports = generation.Q<Foldout>("assetgen-import-foldout");
                            setup.value = imports.value = true;
                            yield return Settle(window);
                            scroll.ScrollTo(setup);
                            yield return Settle(window);
                            Capture(window, Path.Combine(directory, "assetgen-settings-" + width + "-" + theme + ".png"));
                            setup.value = imports.value = false;
                            foreach (var category in generation.Query<Foldout>(className: "provider-category").ToList())
                                category.value = true;
                            scroll.scrollOffset = Vector2.zero;
                        }
                        if (page == "deps")
                        {
                            var bulk = root.Q<Foldout>("dependency-bulk-actions");
                            bulk.value = true;
                            yield return Settle(window);
                            Capture(window, Path.Combine(directory, "deps-actions-" + width + "-" + theme + ".png"));
                            bulk.value = false;
                        }
                        if (page == "resources")
                        {
                            var search = resources.Q<TextField>("resources-search");
                            search.value = "no-matching-resource";
                            yield return Settle(window);
                            Assert.IsFalse(resources.Q("resources-empty-state").ClassListContains("catalog-hidden"));
                            Capture(window, Path.Combine(directory, "resources-empty-" + width + "-" + theme + ".png"));
                            search.value = "";
                        }
                        if (page == "activity")
                        {
                            var populated = rows;
                            rows = new List<OperationActivityRow>();
                            revision++;
                            activity.Tick();
                            yield return Settle(window);
                            Capture(window, Path.Combine(directory, "activity-empty-" + width + "-" + theme + ".png"));
                            rows = populated;
                            revision++;
                        }
                    }
                }
            }
            finally
            {
                navigation?.Dispose();
                activity?.Dispose();
                graph?.Dispose();
                window.Close();
                UnityEngine.Object.DestroyImmediate(dependencies);
            }
        }

        private static IEnumerator Settle(EditorWindow window)
        {
            for (int i = 0; i < 12; i++)
            {
                window.Repaint();
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
        }

        private static void Capture(EditorWindow window, string path)
        {
            typeof(OperationActivityTests).GetMethod("Capture", PrivateStatic).Invoke(null, new object[] { window, path });
            TestContext.Out.WriteLine("EDITOR_CATALOG_CAPTURE " + path);
        }
    }
}
