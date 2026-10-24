using System;
using System.Collections;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.ReferenceGraph;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class ReferenceGraphRecoveryTests
    {
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;

        private sealed class EvidenceWindow : EditorWindow { }

        private EvidenceWindow eventWindow;

        [SetUp]
        public void AttachEventPanel()
        {
            eventWindow = ScriptableObject.CreateInstance<EvidenceWindow>();
            eventWindow.ShowUtility();
        }

        [TearDown]
        public void CloseEventPanel() => eventWindow.Close();

        private VisualElement NewRoot()
        {
            var root = new VisualElement();
            eventWindow.rootVisualElement.Add(root);
            Assert.IsNotNull(root.panel, "Value-change callbacks require a live panel.");
            return root;
        }

        private static JObject Sample() => (JObject)typeof(ReferenceGraphPanelTests).GetMethod("Sample", Static).Invoke(null, null);

        [Test]
        public async Task SameIdentityAndKindRoundtripsKeepIndependentDrafts()
        {
            var root = NewRoot();
            string account = "one";
            using var section = new McpReferenceGraphSection(root, (_, __, ___) => Task.FromResult(Sample()), () => account);
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            root.Q<TextField>("graph-config").value = "{\"call\":\"Draft.Load\"}";
            section.SetVisible(false);
            section.SetVisible(true);
            Assert.AreEqual("{\"call\":\"Draft.Load\"}", root.Q<TextField>("graph-config").value);
            var kind = root.Q<DropdownField>("graph-kind");
            kind.index = 1;
            root.Q<TextField>("graph-config").value = "{\"path\":\"draft/source\"}";
            kind.index = 0;
            Assert.AreEqual("{\"call\":\"Draft.Load\"}", root.Q<TextField>("graph-config").value);
            kind.index = 1;
            Assert.AreEqual("{\"path\":\"draft/source\"}", root.Q<TextField>("graph-config").value);
            section.SetVisible(false);
            account = "two";
            section.SetVisible(true);
            Assert.AreEqual("{}", root.Q<TextField>("graph-config").value);
            kind.index = 0;
            Assert.AreEqual("{}", root.Q<TextField>("graph-config").value);
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task UnverifiedDraftCannotBeAdoptedByAnotherAccount(bool afterInvalidation)
        {
            var root = NewRoot();
            string account = "one";
            using var section = new McpReferenceGraphSection(root, (_, __, ___) => Task.FromResult(Sample()), () => account);
            section.SetVisible(true);
            if (afterInvalidation)
            {
                await section.Run("overview", new JObject());
                section.SetVisible(false);
                account = "two";
                section.SetVisible(true);
            }
            var payload = root.Q<TextField>("graph-config");
            Assert.IsFalse(payload.enabledSelf, "Unverified forms must not accept drafts.");
            var kind = root.Q<DropdownField>("graph-kind");
            kind.index = 1;
            payload.SetValueWithoutNotify("{'path':'unverified/source'}");
            var approval = (Toggle)typeof(McpReferenceGraphSection).GetField("reauthorize", Instance).GetValue(section);
            var enabled = (Toggle)typeof(McpReferenceGraphSection).GetField("sourceEnabled", Instance).GetValue(section);
            approval.SetValueWithoutNotify(true);
            enabled.SetValueWithoutNotify(true);
            section.SetVisible(false);
            account = "three";
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            Assert.AreEqual("{}", payload.value, "No ownerless form may be adopted by a verified account.");
            Assert.IsFalse(approval.value);
            Assert.IsFalse(enabled.value);
            Assert.IsTrue(payload.enabledSelf);
            kind.index = 0;
            Assert.AreEqual("{}", payload.value);
        }

        [Test]
        public async Task StopWaitingAllowsAnotherRequestAndIgnoresLateCompletion()
        {
            var root = NewRoot();
            var completion = new TaskCompletionSource<JObject>();
            CancellationToken token = default;
            int calls = 0;
            using var section = new McpReferenceGraphSection(
                root,
                (_, __, ct) =>
                {
                    token = ct;
                    return ++calls == 1 ? completion.Task : Task.FromResult(Sample());
                },
                () => "one"
            );
            section.SetVisible(true);
            Task first = section.Run("overview", new JObject());
            try
            {
                var stop = root.Q<Button>("graph-stop");
                Assert.IsNotNull(stop);
                Assert.IsTrue(stop.enabledSelf);
                typeof(McpReferenceGraphSection).GetMethod("StopWaiting", Instance).Invoke(section, null);
                Assert.IsTrue(token.IsCancellationRequested);
                Assert.That(root.Q<Label>("graph-message").text, Does.Contain("server work may continue"));
                await section.Run("overview", new JObject());
                Assert.AreEqual(2, calls);
                string current = root.Q<Label>("graph-status").text;
                var late = Sample();
                late["graph_status"]["ready"] = 999999;
                completion.SetResult(late);
                await first;
                Assert.AreEqual(current, root.Q<Label>("graph-status").text);
            }
            finally
            {
                completion.TrySetResult(Sample());
                await first;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task StoppedApplyKeepsDraftAndLateCompletionCannotReleaseNewApply(bool leaveTab)
        {
            var root = NewRoot();
            var firstCompletion = new TaskCompletionSource<JObject>();
            var secondCompletion = new TaskCompletionSource<JObject>();
            int applies = 0;
            CancellationToken firstToken = default;
            using var section = new McpReferenceGraphSection(
                root,
                (action, _, token) =>
                {
                    if (action != "import_config")
                        return Task.FromResult(Sample());
                    if (++applies == 1)
                    {
                        firstToken = token;
                        return firstCompletion.Task;
                    }
                    return secondCompletion.Task;
                },
                () => "one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            var payload = root.Q<TextField>("graph-config");
            var item = root.Q<TextField>("graph-item-id");
            item.value = "draft-rule";
            payload.value = "{\"call\":\"Draft.Load\"}";
            var import = typeof(McpReferenceGraphSection).GetMethod("Import", Instance);
            Task first = (Task)import.Invoke(section, null);
            Task second = null;
            try
            {
                Assert.That(root.Q<Label>("graph-form-message").text, Does.Contain("Working"));
                Assert.AreSame(root.Q<Foldout>("graph-item-editor"), root.Q<Button>("graph-stop").GetFirstAncestorOfType<Foldout>());
                if (leaveTab)
                {
                    section.SetVisible(false);
                    section.SetVisible(true);
                }
                else
                    typeof(Clickable).GetMethod("Invoke", Instance).Invoke(root.Q<Button>("graph-stop").clickable, new object[] { null });
                Assert.IsTrue(firstToken.IsCancellationRequested);
                Assert.That(root.Q<Label>("graph-form-message").text, Does.Not.Contain("Working"));
                Assert.That(root.Q<Label>("graph-form-message").text, Does.Contain("remote outcome is unknown"));
                Assert.AreEqual("draft-rule", item.value);
                Assert.AreEqual("{\"call\":\"Draft.Load\"}", payload.value);
                Assert.IsTrue(root.Q<Button>("graph-import").enabledSelf);
                payload.value = "{\"call\":\"Newer.Load\"}";
                second = (Task)import.Invoke(section, null);
                var late = Sample();
                late["version"] = 99;
                firstCompletion.SetResult(late);
                await first;
                Assert.That(payload.value, Does.Contain("Newer.Load"));
                Assert.That(root.Q<Label>("graph-form-message").text, Does.Contain("Working"));
                Assert.IsTrue(root.Q<Button>("graph-stop").enabledSelf, "The old completion must not release the newer wait.");
                Assert.IsFalse(root.Q<Button>("graph-import").enabledSelf);
                var current = Sample();
                current["version"] = 2;
                secondCompletion.SetResult(current);
                await second;
                Assert.That(root.Q<Label>("graph-form-message").text, Does.Contain("Item updated"));
                Assert.IsTrue(root.Q<Button>("graph-import").enabledSelf);
            }
            finally
            {
                firstCompletion.TrySetResult(Sample());
                secondCompletion.TrySetResult(Sample());
                await first;
                if (second != null)
                    await second;
            }
        }

        [Test]
        public async Task DisposeDuringApplyIgnoresLateSuccess()
        {
            var root = NewRoot();
            var completion = new TaskCompletionSource<JObject>();
            var section = new McpReferenceGraphSection(
                root,
                (action, _, __) => action == "import_config" ? completion.Task : Task.FromResult(Sample()),
                () => "one"
            );
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            root.Q<TextField>("graph-item-id").value = "draft-rule";
            Task apply = (Task)typeof(McpReferenceGraphSection).GetMethod("Import", Instance).Invoke(section, null);
            section.Dispose();
            completion.SetResult(new JObject { ["version"] = 99 });
            await apply;
            Assert.Zero(root.childCount);
        }

        [Test]
        public async Task DirtyItemReplacementRequiresDiscardAndRefreshKeepsDraft()
        {
            var root = NewRoot();
            using var section = new McpReferenceGraphSection(root, (_, __, ___) => Task.FromResult(Sample()), () => "one");
            section.SetVisible(true);
            await section.Run("overview", new JObject());
            root.Q<TextField>("graph-config").value = "{\"call\":\"Draft.Load\"}";
            var guard = typeof(McpReferenceGraphSection).GetField("confirmDiscard", Instance);
            Assert.IsNotNull(guard);
            guard.SetValue(section, (Func<bool>)(() => false));
            var load = typeof(McpReferenceGraphSection).GetMethod("LoadItem", Instance);
            load.Invoke(section, new object[] { JObject.Parse("{'id':'other','version':2,'config':{'call':'Other.Load'}}") });
            Assert.That(root.Q<TextField>("graph-config").value, Does.Contain("Draft.Load"));
            await section.Run("overview", new JObject());
            Assert.That(root.Q<TextField>("graph-config").value, Does.Contain("Draft.Load"));
            guard.SetValue(section, (Func<bool>)(() => true));
            load.Invoke(section, new object[] { JObject.Parse("{'id':'other','version':2,'config':{'call':'Other.Load'}}") });
            Assert.That(root.Q<TextField>("graph-config").value, Does.Contain("Other.Load"));
        }

        [UnityTest, Explicit("Controller-owned narrow form feedback evidence.")]
        public IEnumerator InvalidJsonFeedbackIsVisibleAtBothSupportedHeights()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            McpReferenceGraphSection section = null;
            McpWindowNavigation navigation = null;
            try
            {
                window.minSize = new Vector2(500, 340);
                window.ShowUtility();
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/MCPForUnityEditorWindow.uxml").CloneTree(root);
                root.Q("clients-panel").AddToClassList("hidden");
                root.Q("graph-panel").RemoveFromClassList("hidden");
                navigation = new McpWindowNavigation(root, _ => { });
                navigation.Select("graph");
                section = new McpReferenceGraphSection(root.Q("graph-container"), (_, __, ___) => Task.FromResult(Sample()), () => "one");
                section.SetVisible(true);
                section.Run("overview", new JObject()).GetAwaiter().GetResult();
                root.Q<Foldout>("graph-transfer").value = true;
                root.Q<Foldout>("graph-item-editor").value = true;
                string directory = System.IO.Path.GetFullPath(
                    System.IO.Path.Combine(Application.dataPath, "../Library/McpTestEvidence/graph-recovery-" + Guid.NewGuid().ToString("N"))
                );
                System.IO.Directory.CreateDirectory(directory);
                foreach (int height in new[] { 340, 600 })
                {
                    window.position = new Rect(30, 30, 500, height);
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        EditorApplication.QueuePlayerLoopUpdate();
                        yield return null;
                    }
                    var scroll = root.Q<ScrollView>("graph-panel");
                    scroll.ScrollTo(root.Q<Button>("graph-import"));
                    root.Q<TextField>("graph-config").value = "{invalid";
                    root.Q<Button>("graph-import").Focus();
                    yield return null;
                    using (var submit = NavigationSubmitEvent.GetPooled())
                        root.Q<Button>("graph-import").SendEvent(submit);
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        EditorApplication.QueuePlayerLoopUpdate();
                        yield return null;
                    }
                    var error = root.Q<Label>("graph-form-message");
                    Assert.IsNotNull(error);
                    Assert.That(error.text, Does.Contain("line"));
                    Assert.IsTrue(error.worldBound.Overlaps(scroll.contentViewport.worldBound), "The submitted form must reveal its error.");
                    string path = System.IO.Path.Combine(directory, "invalid-json-500x" + height + ".png");
                    typeof(OperationActivityTests).GetMethod("Capture", Static).Invoke(null, new object[] { window, path });
                    TestContext.Out.WriteLine("GRAPH_RECOVERY_CAPTURE " + path);
                }
            }
            finally
            {
                section?.Dispose();
                navigation?.Dispose();
                window.Close();
            }
        }
    }
}
