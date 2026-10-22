using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Windows.Components.ReferenceGraph;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    public sealed class ReferenceGraphPanelTests
    {
        private const string Template = "Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml";

        private sealed class EvidenceWindow : EditorWindow { }

        private static JObject Sample() =>
            JObject.Parse(
                @"{
          'scope':'isolated-scope',
          'identity':{'mode':'local','principal':'isolated-test-account','instance_id':'isolated-editor','project_id':'isolated-project'},
          'storage':{'allocated_bytes':123456789,'reusable_bytes':12345},
          'memory':{'catalog_cached':true,'analysis_cached':false},
          'jobs':{'graph_queries':{'0':1,'1':3}},
          'graph_status':{'revision':120,'ready':50000,'pending':120,'errors':1,'native_current':false,'coverage':'partial_static',
           'progress':{'phase':'collecting'},'worker_error':null,
           'providers':{'addressables':{'state':'available','errors':0},'external:settings':{'state':'failed','error':'Read access unavailable','last_verified_at':1792627200}}}}"
            );

        [Test]
        public async Task HiddenAndChangedIdentityCannotPublishLateResults()
        {
            var root = new VisualElement();
            var completion = new TaskCompletionSource<JObject>();
            CancellationToken captured = default;
            string identity = "one";
            using var section = new McpReferenceGraphSection(
                root,
                (_, __, token) =>
                {
                    captured = token;
                    return completion.Task;
                },
                () => identity
            );
            section.SetVisible(true);
            Task request = section.Run("overview", new JObject());
            section.SetVisible(false);
            Assert.IsTrue(captured.IsCancellationRequested);
            completion.SetResult(Sample());
            await request;
            StringAssert.DoesNotContain("isolated-test-account", root.Q<Label>("graph-identity").text);
            section.SetVisible(true);
            completion = new TaskCompletionSource<JObject>();
            request = section.Run("overview", new JObject());
            identity = "two";
            completion.SetResult(Sample());
            await request;
            StringAssert.DoesNotContain("isolated-test-account", root.Q<Label>("graph-identity").text);
        }

        [Test]
        public async Task DisposeCancelsOwnedRequestAndReleasesView()
        {
            var root = new VisualElement();
            var completion = new TaskCompletionSource<JObject>();
            CancellationToken captured = default;
            var section = new McpReferenceGraphSection(
                root,
                (_, __, token) =>
                {
                    captured = token;
                    return completion.Task;
                },
                () => "one"
            );
            section.SetVisible(true);
            Task request = section.Run("overview", new JObject());
            section.Dispose();
            Assert.IsTrue(captured.IsCancellationRequested);
            completion.SetResult(Sample());
            await request;
            Assert.AreEqual(0, root.childCount);
        }

        [Test]
        public void MainTemplateExposesReferencePanel()
        {
            var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Template).CloneTree();
            Assert.IsNotNull(root.Q("graph-tab"));
            Assert.IsNotNull(root.Q("graph-container"));
        }

        [Test]
        public async Task PreviousAccountFormCannotDispatchToReplacementAccount()
        {
            var root = new VisualElement();
            string account = "first";
            int sends = 0;
            using var section = new McpReferenceGraphSection(
                root,
                (_, __, ___) =>
                {
                    sends++;
                    return Task.FromResult(Sample());
                },
                () => account
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            account = "second";
            await section.Run("import_config", new JObject());
            Assert.AreEqual(1, sends, "A stale form must never write into a newly selected account.");
            StringAssert.DoesNotContain("isolated-test-account", root.Q<Label>("graph-identity").text);
            Assert.IsTrue(string.IsNullOrEmpty(root.Q<Label>("graph-identity").tooltip));
            Assert.IsFalse(root.Q(className: "graph-summary").ClassListContains("graph-attention"));
        }

        [UnityTest, Explicit("Controller-owned rendered operations panel evidence.")]
        public IEnumerator ActualNarrowAndWidePanelLayout()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            McpReferenceGraphSection section = null;
            try
            {
                window.titleContent = new GUIContent("Unity MCP Reference Graph");
                window.minSize = new Vector2(400, 400);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Template).CloneTree(root);
                root.Q("clients-panel").AddToClassList("hidden");
                root.Q("graph-panel").RemoveFromClassList("hidden");
                root.Q<UnityEditor.UIElements.ToolbarToggle>("clients-tab").SetValueWithoutNotify(false);
                root.Q<UnityEditor.UIElements.ToolbarToggle>("graph-tab").SetValueWithoutNotify(true);
                section = new McpReferenceGraphSection(root.Q("graph-container"), (_, __, ___) => Task.FromResult(Sample()), () => "one");
                section.SetVisible(true);
                section.Render(Sample());
                string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/McpTestEvidence/graph-" + Guid.NewGuid().ToString("N")));
                Directory.CreateDirectory(directory);
                foreach (int width in new[] { 500, 1100 })
                {
                    window.position = new Rect(30, 30, width, 1000);
                    for (int i = 0; i < 15; i++)
                    {
                        EditorApplication.QueuePlayerLoopUpdate();
                        window.Repaint();
                        yield return null;
                    }
                    foreach (var button in root.Q("graph-container").Query<Button>().ToList())
                    {
                        if (button.worldBound.width == 0)
                            continue;
                        Assert.Greater(button.worldBound.width, 8);
                        Assert.GreaterOrEqual(button.worldBound.xMin, root.worldBound.xMin);
                        Assert.LessOrEqual(button.worldBound.xMax, root.worldBound.xMax);
                    }
                    var refresh = root.Q<Button>("graph-refresh");
                    refresh.Focus();
                    yield return null;
                    Assert.AreSame(refresh, root.focusController.focusedElement);
                    using (var submit = NavigationSubmitEvent.GetPooled())
                        refresh.SendEvent(submit);
                    yield return null;
                    StringAssert.Contains("isolated-test-account", root.Q<Label>("graph-identity").text);
                    string path = Path.Combine(directory, "overview-" + width + ".png");
                    typeof(OperationActivityTests)
                        .GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static)
                        .Invoke(null, new object[] { window, path });
                    TestContext.Out.WriteLine("REFERENCE_GRAPH_UI_CAPTURE " + path);
                    root.Q<Foldout>("graph-transfer").value = true;
                    root.Q<Foldout>("graph-item-editor").value = true;
                    var scroll = root.Q<ScrollView>("graph-panel");
                    for (int i = 0; i < 6; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    scroll.ScrollTo(root.Q("graph-item-editor"));
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    path = Path.Combine(directory, "transfer-" + width + ".png");
                    typeof(OperationActivityTests)
                        .GetMethod("Capture", BindingFlags.NonPublic | BindingFlags.Static)
                        .Invoke(null, new object[] { window, path });
                    TestContext.Out.WriteLine("REFERENCE_GRAPH_UI_CAPTURE " + path);
                    Assert.LessOrEqual(
                        scroll.contentContainer.worldBound.width,
                        scroll.contentViewport.worldBound.width + 1,
                        "The panel must not require horizontal scrolling."
                    );
                    Assert.LessOrEqual(scroll.horizontalScroller.highValue, 1, "No hidden horizontal content.");
                    root.Q<TextField>("graph-config").value = "{invalid";
                    root.Q<Label>("graph-message").AddToClassList("hidden");
                    var apply = root.Q<Button>("graph-import");
                    apply.Focus();
                    yield return null;
                    using (var submit = NavigationSubmitEvent.GetPooled())
                        apply.SendEvent(submit);
                    yield return null;
                    Assert.IsFalse(root.Q<Label>("graph-message").ClassListContains("hidden"));
                    StringAssert.Contains("configuration fields", root.Q<Label>("graph-message").text);
                    root.Q<TextField>("graph-config").value = "{}";
                    root.Q<Foldout>("graph-transfer").value = false;
                    scroll.scrollOffset = Vector2.zero;
                }
            }
            finally
            {
                section?.Dispose();
                window.Close();
            }
        }
    }
}
