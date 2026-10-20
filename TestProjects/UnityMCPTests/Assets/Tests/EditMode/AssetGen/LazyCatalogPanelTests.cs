using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Windows.Components.AssetGen;
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

        private static McpAssetGenSection Section()
        {
            var root = new VisualElement();
            root.Add(new Button { name = "assetgen-refresh" });
            root.Add(new Label { name = "assetgen-refresh-status" });
            return new McpAssetGenSection(root);
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
                root.style.unityFont = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
                root.style.fontSize = 14;
                root.style.color = Color.white;
                var scroll = new ScrollView();
                scroll.style.height = 720;
                scroll.Add(content);
                root.Add(scroll);
                new McpAssetGenSection(content);
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
