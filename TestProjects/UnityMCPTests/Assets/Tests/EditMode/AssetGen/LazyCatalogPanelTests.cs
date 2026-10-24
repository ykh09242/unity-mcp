using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Windows.Components.AssetGen;
using MCPForUnity.Editor.Windows.Components.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class LazyCatalogPanelTests
    {
        private sealed class NoKeys : ISecureKeyStore
        {
            public bool Has(string id) => false;

            public bool TryGet(string id, out string key)
            {
                key = null;
                return false;
            }

            public void Set(string id, string key) { }

            public void Delete(string id) { }
        }

        private FakeHttpTransport fal,
            router;
        private string directory;
        private object priorStore;

        private sealed class MemorySettings : McpAssetGenSection.ISettings
        {
            public string DefaultFormat { get; set; } = "glb";
            public string OutputRoot { get; set; } = "Assets/Generated";
            public bool AutoNormalize { get; set; } = true;
            private readonly Dictionary<string, string> models = new();
            private readonly Dictionary<string, bool> enabled = new();

            public bool IsProviderEnabled(string id) => enabled.TryGetValue(id, out bool value) && value;

            public void SetProviderEnabled(string id, bool value) => enabled[id] = value;

            public string GetSelectedModel(string kind, string provider) => models.TryGetValue(kind + "/" + provider, out string value) ? value : "";

            public void SetSelectedModel(string kind, string provider, string value) => models[kind + "/" + provider] = value;
        }

        private static VisualElement ProviderRoot()
        {
            var root = new VisualElement();
            root.Add(new VisualElement { name = "assetgen-providers-container" });
            root.Add(new TextField { name = "assetgen-output-root" });
            root.Add(new Label { name = "assetgen-output-validation" });
            root.Add(new Label { name = "assetgen-output-effective" });
            return root;
        }

        private sealed class AttachedProviderRoot : IDisposable
        {
            private readonly CategoryWindow window;

            internal AttachedProviderRoot(VisualElement root)
            {
                window = ScriptableObject.CreateInstance<CategoryWindow>();
                window.ShowUtility();
                window.rootVisualElement.Add(root);
                Assert.IsNotNull(root.panel, "Field edits must dispatch through a real attached UI Toolkit panel.");
            }

            public void Dispose()
            {
                window.rootVisualElement.Clear();
                window.Close();
            }
        }

        [TestCase("Generated")]
        [TestCase("../outside")]
        [TestCase("Assets/../outside")]
        public void InvalidOutputDraftIsRetainedWithoutChangingSavedDestination(string draft)
        {
            var settings = new MemorySettings { OutputRoot = "Assets/SyntheticOutput" };
            var root = ProviderRoot();
            using var attached = new AttachedProviderRoot(root);
            var section = new McpAssetGenSection(root, settings);
            var field = root.Q<TextField>("assetgen-output-root");
            field.value = draft;
            // Invoke the exact commit method used by blur/Enter; a manually pooled FocusOutEvent has no focus-controller state.
            typeof(McpAssetGenSection).GetMethod("CommitOutputRoot", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(section, null);
            section.Refresh();
            Assert.AreEqual(draft, field.value);
            Assert.AreEqual("Assets/SyntheticOutput", settings.OutputRoot);
            StringAssert.Contains("under Assets", root.Q<Label>("assetgen-output-validation").text);
            StringAssert.Contains(settings.OutputRoot, root.Q<Label>("assetgen-output-effective").text);
        }

        [Test]
        public void ValidOutputDraftNormalizesAtCommitAndShowsEffectiveDestination()
        {
            var settings = new MemorySettings();
            var root = ProviderRoot();
            using var attached = new AttachedProviderRoot(root);
            var section = new McpAssetGenSection(root, settings);
            var field = root.Q<TextField>("assetgen-output-root");
            field.value = " Assets\\SyntheticOutput/Models/.. ";
            Assert.AreEqual("Assets/Generated", settings.OutputRoot, "Typing is only a draft.");
            // Invoke the exact commit method used by blur/Enter; a manually pooled FocusOutEvent has no focus-controller state.
            typeof(McpAssetGenSection).GetMethod("CommitOutputRoot", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(section, null);
            Assert.AreEqual("Assets/SyntheticOutput", settings.OutputRoot);
            Assert.AreEqual(settings.OutputRoot, field.value);
            StringAssert.Contains(settings.OutputRoot, root.Q<Label>("assetgen-output-effective").text);
            Assert.IsEmpty(root.Q<Label>("assetgen-output-validation").text);
        }

        [Test]
        public void ZeroMatchesKeepsCurrentSelectionSeparateFromSearchResults()
        {
            var settings = new MemorySettings();
            string id = AssetGenModelCatalog.DefaultModelId("fal", "image");
            settings.SetSelectedModel("image", "fal", id);
            var root = ProviderRoot();
            using var attached = new AttachedProviderRoot(root);
            new McpAssetGenSection(root, settings);
            root.Q<TextField>("model-search-image-fal").value = "no synthetic model matches this query";
            var row = root.Q("provider-model-image-fal");
            StringAssert.Contains(id, row.Q<Label>("model-current-image-fal").text);
            StringAssert.Contains("No models found", row.Q<Label>("model-empty-image-fal").text);
            Assert.IsNull(row.Q<DropdownField>());
            Assert.AreEqual(id, settings.GetSelectedModel("image", "fal"));
            Assert.IsEmpty(fal.RecordedRequests);
            Assert.IsEmpty(router.RecordedRequests);
        }

        [Test]
        public void LargeCatalogSearchReachesLastModelWithoutExtraLoadsOrUnboundedControls()
        {
            var models = new JArray(
                Enumerable
                    .Range(0, 8192)
                    .Select(index => new JObject
                    {
                        ["id"] = "synthetic/image-" + index.ToString("D4"),
                        ["name"] = "Synthetic image " + index,
                        ["architecture"] = new JObject { ["input_modalities"] = new JArray("text"), ["output_modalities"] = new JArray("image") },
                        ["supported_parameters"] = new JObject
                        {
                            ["output_format"] = new JObject { ["type"] = "enum", ["values"] = new JArray("png") },
                        },
                    })
            );
            router.Handler = _ => new HttpResult { Status = 200, Text = new JObject { ["data"] = models }.ToString() };
            Assert.IsTrue(OpenRouterModelCatalog.RefreshAsync().GetAwaiter().GetResult());
            var settings = new MemorySettings();
            settings.SetSelectedModel("image", "openrouter", "synthetic/image-8191");
            var root = ProviderRoot();
            using var attached = new AttachedProviderRoot(root);
            var section = new McpAssetGenSection(root, settings);
            var row = root.Q("provider-model-image-openrouter");
            Assert.AreEqual(McpAssetGenSection.MenuLimit + 1, row.Q<DropdownField>().choices.Count);
            root.Q<TextField>("model-search-image-openrouter").value = "image-8191";
            Assert.AreEqual(1, row.Q<DropdownField>().choices.Count);
            StringAssert.Contains("image-8191", row.Q<DropdownField>().value);
            int controls = row.Query<VisualElement>().ToList().Count;
            section.Refresh();
            Assert.AreEqual(controls, row.Query<VisualElement>().ToList().Count);
            Assert.AreEqual(1, router.RecordedRequests.Count, "Rendering, search and refresh use the cached catalog.");
            Assert.IsEmpty(fal.RecordedRequests);
        }

        [UnityTest]
        public IEnumerator DelayedVerificationSurvivesOtherProviderFocusAndSameSearchRebuild()
        {
            var delayed = new DelayedCatalog();
            OpenRouterModelCatalog.TransportOverrideForTests = delayed;
            var settings = new MemorySettings();
            var picked = AssetGenModelCatalog.ForProvider("openrouter", "image").First();
            settings.SetSelectedModel(picked.Kind, picked.Provider, picked.Id);
            var root = ProviderRoot();
            var section = new McpAssetGenSection(root, settings);
            var oldLabel = root.Q<Label>("model-meta-image-openrouter");
            var verify = (Task)
                typeof(McpAssetGenSection)
                    .GetMethod("VerifySelection", BindingFlags.NonPublic | BindingFlags.Instance)
                    .Invoke(section, new object[] { picked });
            (
                (Task)
                    typeof(McpAssetGenSection)
                        .GetMethod("SelectCatalogAsync", BindingFlags.NonPublic | BindingFlags.Instance)
                        .Invoke(section, new object[] { "model", "tripo" })
            )
                .GetAwaiter()
                .GetResult();
            root.Q<TextField>("model-search-image-openrouter").value = "Gemini";
            var currentLabel = root.Q<Label>("model-meta-image-openrouter");
            Assert.AreNotSame(oldLabel, currentLabel);
            StringAssert.Contains("Checking compatibility", currentLabel.text);
            delayed.Response.SetResult(new HttpResult { Status = 500, Text = "synthetic failure" });
            while (!verify.IsCompleted)
                yield return null;
            verify.GetAwaiter().GetResult();
            StringAssert.Contains("Compatibility check failed", root.Q<Label>("model-meta-image-openrouter").text);
            section.Refresh();
            StringAssert.Contains("Compatibility check failed", root.Q<Label>("model-meta-image-openrouter").text);
        }

        [UnityTest]
        public IEnumerator LateVerificationCannotOverwriteNewModelSelection()
        {
            var delayed = new DelayedCatalog();
            FalModelCatalog.TransportOverrideForTests = delayed;
            var settings = new MemorySettings();
            var models = AssetGenModelCatalog.ForProvider("fal", "image");
            var picked = models[0];
            settings.SetSelectedModel("image", "fal", picked.Id);
            var root = ProviderRoot();
            var section = new McpAssetGenSection(root, settings);
            var verify = (Task)
                typeof(McpAssetGenSection)
                    .GetMethod("VerifySelection", BindingFlags.NonPublic | BindingFlags.Instance)
                    .Invoke(section, new object[] { picked });
            settings.SetSelectedModel("image", "fal", models[1].Id);
            section.Refresh();
            string current = root.Q<Label>("model-meta-image-fal").text;
            delayed.Response.SetResult(new HttpResult { Status = 500, Text = "synthetic failure" });
            while (!verify.IsCompleted)
                yield return null;
            verify.GetAwaiter().GetResult();
            Assert.AreEqual(current, root.Q<Label>("model-meta-image-fal").text);
        }

        [UnityTest]
        public IEnumerator DetachedVerificationCannotRestoreFailureAfterReattach()
        {
            var window = ScriptableObject.CreateInstance<CategoryWindow>();
            try
            {
                window.ShowUtility();
                var delayed = new DelayedCatalog();
                OpenRouterModelCatalog.TransportOverrideForTests = delayed;
                var settings = new MemorySettings();
                var picked = AssetGenModelCatalog.ForProvider("openrouter", "image").First();
                settings.SetSelectedModel(picked.Kind, picked.Provider, picked.Id);
                var root = ProviderRoot();
                window.rootVisualElement.Add(root);
                var section = new McpAssetGenSection(root, settings);
                var verify = (Task)
                    typeof(McpAssetGenSection)
                        .GetMethod("VerifySelection", BindingFlags.NonPublic | BindingFlags.Instance)
                        .Invoke(section, new object[] { picked });
                Assert.IsNotNull(root.panel);
                root.RemoveFromHierarchy();
                Assert.IsNull(root.panel);
                delayed.Response.SetResult(new HttpResult { Status = 500, Text = "synthetic failure" });
                while (!verify.IsCompleted)
                    yield return null;
                verify.GetAwaiter().GetResult();
                window.rootVisualElement.Add(root);
                string current = root.Q<Label>("model-meta-image-openrouter").text;
                StringAssert.DoesNotContain("Compatibility check failed", current);
                StringAssert.DoesNotContain("Checking compatibility", current);
            }
            finally
            {
                window.Close();
            }
        }

        [Test]
        public void BlenderConsentFailureShowsHumanRecoveryAndOnlyRequestsSettingsNavigation()
        {
            var root = new VisualElement();
            root.Add(new Label { name = "blender-action-status" });
            root.Add(new Label { name = "blender-action-details" });
            root.Add(new Button { name = "blender-consent-settings" });
            var panel = new McpBlenderBridgePanel(root, false);
            int requests = 0;
            panel.OnConsentSettingsRequested += () => requests++;
            string explanation = "Enable blender_bridge explicitly before importing or synchronizing addons.";
            var response = JObject.FromObject(new ErrorResponse("blender_consent_required", explanation));
            panel.PresentActionResult(response);
            StringAssert.Contains(explanation, root.Q<Label>("blender-action-status").text);
            StringAssert.Contains("Open Tools", root.Q<Label>("blender-action-status").text);
            Assert.IsFalse(root.Q<Button>("blender-consent-settings").ClassListContains("catalog-hidden"));
            typeof(Clickable)
                .GetMethod("Invoke", BindingFlags.Instance | BindingFlags.NonPublic)
                .Invoke(root.Q<Button>("blender-consent-settings").clickable, new object[] { null });
            Assert.AreEqual(1, requests);
            Assert.AreEqual("blender_consent_required", response.Value<string>("code"));
            panel.PresentActionResult(JObject.FromObject(new SuccessResponse("Synthetic success.")));
            Assert.IsTrue(root.Q<Button>("blender-consent-settings").ClassListContains("catalog-hidden"));
            Assert.IsEmpty(root.Q<Label>("blender-action-details").text);
        }

        [Test]
        public void BlenderConsentNavigationRevealsRowWithoutChangingFiltersOrGrantingConsent()
        {
            var root = new VisualElement();
            var search = new TextField { name = "tools-search", value = "unrelated query" };
            root.Add(search);
            var category = new Foldout { text = "Asset Generation", value = false };
            category.AddToClassList("catalog-hidden");
            root.Add(category);
            var row = new VisualElement();
            row.AddToClassList("catalog-hidden");
            category.Add(row);
            int grants = 0;
            row.Add(new Button(() => grants++) { name = "consent-change-blender_bridge" });
            var section = new McpToolsSection(root, null, false);
            var rows =
                (Dictionary<string, VisualElement>)
                    typeof(McpToolsSection).GetField("toolRowMap", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(section);
            rows.Add("blender_bridge", row);
            section.RevealToolConsent("blender_bridge");
            Assert.AreEqual("unrelated query", search.value);
            Assert.IsFalse(row.ClassListContains("catalog-hidden"));
            Assert.IsFalse(category.ClassListContains("catalog-hidden"));
            Assert.IsTrue(category.value);
            Assert.AreEqual(0, grants);
            Assert.IsNotNull(row.Q<Label>("consent-navigation-hint"));
        }

        [SetUp]
        public void SetUp()
        {
            var field = typeof(SecureKeyStore).GetField("_current", BindingFlags.NonPublic | BindingFlags.Static);
            priorStore = field.GetValue(null);
            SecureKeyStore.OverrideForTests(new NoKeys());
            AssetGenModelCatalog.ResetForTests(true);
            directory = Path.Combine(Path.GetTempPath(), "lazy-catalog-" + Guid.NewGuid().ToString("N"));
            FalModelCatalog.CachePathOverrideForTests = Path.Combine(directory, "fal.json");
            OpenRouterModelCatalog.CachePathOverrideForTests = Path.Combine(directory, "router.json");
            fal = new FakeHttpTransport { Handler = _ => new HttpResult { Status = 200, Text = "{\"models\":[],\"has_more\":false}" } };
            router = new FakeHttpTransport { Handler = _ => new HttpResult { Status = 200, Text = "{\"data\":[]}" } };
            FalModelCatalog.TransportOverrideForTests = fal;
            OpenRouterModelCatalog.TransportOverrideForTests = router;
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenModelCatalog.ResetForTests(true);
            SecureKeyStore.OverrideForTests((ISecureKeyStore)priorStore);
            if (Directory.Exists(directory))
                Directory.Delete(directory, true);
        }

        private sealed class CategoryWindow : EditorWindow { }

        [Test]
        public void ProviderCategoriesPreserveUserStateAcrossRefreshAndChildChanges()
        {
            var window = ScriptableObject.CreateInstance<CategoryWindow>();
            try
            {
                window.ShowUtility();
                var root = AssetDatabase
                    .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/AssetGen/McpAssetGenSection.uxml")
                    .CloneTree();
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                window.rootVisualElement.Add(root);
                var section = new McpAssetGenSection(root, new MemorySettings());
                var category = root.Q<Foldout>(className: "provider-category");
                category.value = false;
                section.Refresh();
                category = root.Q<Foldout>(className: "provider-category");
                Assert.IsFalse(category.value, "Refreshing model choices must preserve a collapsed category.");
                category.value = true;
                var child = new Toggle();
                category.Add(child);
                child.value = true;
                child.value = false;
                section.Refresh();
                Assert.IsTrue(root.Q<Foldout>(className: "provider-category").value, "A child control cannot overwrite its category state.");
                Assert.IsEmpty(fal.RecordedRequests);
                Assert.IsEmpty(router.RecordedRequests);
            }
            finally
            {
                window.Close();
            }
        }

        private static McpAssetGenSection Section()
        {
            var root = new VisualElement();
            root.Add(new Button { name = "assetgen-refresh" });
            root.Add(new Label { name = "assetgen-refresh-status" });
            return new McpAssetGenSection(root, new MemorySettings());
        }

        [Test]
        public void OpeningOrRefreshingBlenderOnlyPanelDoesNotFetchCatalogs()
        {
            var section = Section();
            section.Refresh();
            Assert.IsEmpty(fal.RecordedRequests);
            Assert.IsEmpty(router.RecordedRequests);
        }

        [UnityTest]
        public IEnumerator SelectingOneProviderAndExplicitRefreshAllHaveSeparateScope()
        {
            var section = Section();
            Assert.IsEmpty(fal.RecordedRequests);
            Assert.IsEmpty(router.RecordedRequests);
            var method = typeof(McpAssetGenSection).GetMethod("SelectCatalogAsync", BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.IsNotNull(method);
            var selected = (Task)method.Invoke(section, new object[] { "image", "openrouter" });
            while (!selected.IsCompleted)
                yield return null;
            selected.GetAwaiter().GetResult();
            Assert.AreEqual(1, router.RecordedRequests.Count);
            Assert.IsEmpty(fal.RecordedRequests);
            var all = (Task)
                typeof(McpAssetGenSection).GetMethod("RefreshCatalog", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(section, new object[] { true });
            while (!all.IsCompleted)
                yield return null;
            all.GetAwaiter().GetResult();
            Assert.AreEqual(2, router.RecordedRequests.Count);
            Assert.IsTrue(fal.RecordedRequests.Any(r => r.Url.Contains("category=text-to-image")));
            Assert.IsTrue(fal.RecordedRequests.Any(r => r.Url.Contains("category=text-to-audio")));
            Assert.IsTrue(fal.RecordedRequests.Any(r => r.Url.Contains("category=text-to-3d")));
        }

        private sealed class DelayedCatalog : IHttpTransport
        {
            internal readonly TaskCompletionSource<HttpResult> Response = new();

            public Task<HttpResult> SendAsync(HttpRequestSpec request, CancellationToken ct) => Response.Task;
        }

        [UnityTest]
        public IEnumerator LateSelectionCompletionDoesNotOverwriteCurrentStatus()
        {
            var delayed = new DelayedCatalog();
            OpenRouterModelCatalog.TransportOverrideForTests = delayed;
            var section = Section();
            var method = typeof(McpAssetGenSection).GetMethod("SelectCatalogAsync", BindingFlags.NonPublic | BindingFlags.Instance);
            var old = (Task)method.Invoke(section, new object[] { "image", "openrouter" });
            ((Task)method.Invoke(section, new object[] { "model", "tripo" })).GetAwaiter().GetResult();
            string current = section.Root.Q<Label>("assetgen-refresh-status").text;
            delayed.Response.SetResult(new HttpResult { Status = 200, Text = "{\"data\":[]}" });
            while (!old.IsCompleted)
                yield return null;
            old.GetAwaiter().GetResult();
            Assert.AreEqual(current, section.Root.Q<Label>("assetgen-refresh-status").text);
            Assert.IsTrue(section.Root.Q<Button>("assetgen-refresh").enabledSelf);
        }

        [UnityTest]
        public IEnumerator CachedPanelRendersRefreshAllAndStatusWithoutNetwork()
        {
            var panel = ScriptableObject.CreateInstance<PanelSettings>();
            panel.scaleMode = PanelScaleMode.ConstantPixelSize;
            panel.scale = 1;
            panel.clearColor = true;
            panel.colorClearValue = new Color(.18f, .18f, .18f);
            var gameObject = new GameObject("LazyCatalogEvidence-" + Guid.NewGuid().ToString("N"));
            try
            {
                var document = gameObject.AddComponent<UIDocument>();
                document.panelSettings = panel;
                var tree = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                    "Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/AssetGen/McpAssetGenSection.uxml"
                );
                Assert.IsNotNull(tree);
                var content = tree.CloneTree();
                content.Q<VisualElement>("blender-bridge-panel").RemoveFromHierarchy();
                var root = document.rootVisualElement;
                root.style.width = 840;
                root.style.height = 720;
                root.style.unityFont = UnityEngine.Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                root.style.fontSize = 14;
                root.style.color = Color.white;
                var scroll = new ScrollView();
                scroll.style.height = 720;
                scroll.Add(content);
                root.Add(scroll);
                new McpAssetGenSection(content, new MemorySettings());
                var request = new JObject
                {
                    ["action"] = "render_ui",
                    ["target"] = gameObject.GetInstanceIDCompat().ToString(),
                    ["component_type"] = "ui_document",
                    ["width"] = 840,
                    ["height"] = 720,
                    ["warmup_updates"] = 4,
                    ["file_name"] = "lazy-catalog.png",
                    ["output_folder"] = "Temp/LazyCatalogEvidence-" + Guid.NewGuid().ToString("N"),
                };
                var response = JObject.FromObject(ManageUI.HandleCommand(request));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var poll = new JObject { ["action"] = "render_ui", ["capture_id"] = response["data"]["capture_id"].DeepClone() };
                for (int i = 0; i < 300 && response["data"].Value<bool?>("pending") == true; i++)
                {
                    yield return null;
                    response = JObject.FromObject(ManageUI.HandleCommand(poll));
                }
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.IsFalse(response["data"].Value<bool?>("pending") == true);
                string receipt = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/" + Guid.NewGuid().ToString("N")));
                Directory.CreateDirectory(receipt);
                string screenshot = Path.Combine(receipt, "lazy-catalog.png");
                File.Copy(response["data"].Value<string>("fullPath"), screenshot);
                TestContext.WriteLine("B21 screenshot: " + screenshot);
                Assert.AreEqual("Refresh All", content.Q<Button>("assetgen-refresh").text);
                Assert.IsEmpty(fal.RecordedRequests);
                Assert.IsEmpty(router.RecordedRequests);
            }
            finally
            {
                typeof(ManageUI).GetMethod("CleanupPendingCapture", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
                var cache =
                    (Dictionary<int, RenderTexture>)typeof(ManageUI).GetField("s_panelRTs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                int id = panel.GetInstanceIDCompat();
                if (cache.TryGetValue(id, out var target))
                    typeof(ManageUI)
                        .GetMethod("FinishPanelRender", BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Public)
                        .Invoke(null, new object[] { id, panel, target, false, true });
                UnityEngine.Object.DestroyImmediate(gameObject);
                UnityEngine.Object.DestroyImmediate(panel);
            }
        }
    }
}
