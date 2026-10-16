using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows.Components.Tools;
using MCPForUnityTests.Editor.Services;
using NUnit.Framework;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class McpToolsSettingsStateTests
    {
        private object previousDiscovery;
        private object previousTransport;

        [SetUp]
        public void SetUp()
        {
            previousDiscovery = ServiceField("_toolDiscoveryService").GetValue(null);
            previousTransport = ServiceField("_transportManager").GetValue(null);
            MCPServiceLocator.Register<IToolDiscoveryService>(new EmptyDiscovery());
            MCPServiceLocator.Register(new TransportManager());
        }

        [TearDown]
        public void TearDown()
        {
            ServiceField("_toolDiscoveryService").SetValue(null, previousDiscovery);
            ServiceField("_transportManager").SetValue(null, previousTransport);
        }

        [Test]
        public void NoStatusSource_CannotDisplayRegistrationAsApplied()
        {
            var root = Root();
            var section = new McpToolsSection(root);
            section.Refresh();
            StringAssert.Contains("Not confirmed", root.Q<Label>("tools-apply-status").text);
            Assert.IsFalse(root.Q<Button>("retry-tools-button").enabledSelf);
        }

        [Test]
        public void SentFrame_StaysWaitingUntilMatchingReceipt()
        {
            var state = new ToolSettingsTestDriver();
            object snapshot = state.Capture();
            state.Connect();
            state.Call("BeginAttempt", state.Owner);
            state.Call("MarkSent", state.Owner, snapshot);
            var root = Root();
            object section = Create(root, state, true);
            Refresh(section);
            StringAssert.Contains("Waiting for server", root.Q<Label>("tools-apply-status").text);
            state.Call("AcceptReceipt", state.Owner, state.Receipt(snapshot));
            Refresh(section);
            StringAssert.Contains("Server catalog applied", root.Q<Label>("tools-apply-status").text);
        }

        [Test]
        public void RejectedChange_ShowsFailureAndRetryDoesNotGrantConsentOrReportSuccess()
        {
            var state = new ToolSettingsTestDriver();
            object snapshot = state.Capture();
            int retries = 0;
            var pending = new TaskCompletionSource<bool>();
            state.Call(
                "SetConnection",
                state.Owner,
                "session",
                true,
                (Func<Task>)(
                    () =>
                    {
                        retries++;
                        return pending.Task;
                    }
                )
            );
            state.Call("BeginAttempt", state.Owner);
            state.Call("AcceptReceipt", state.Owner, state.Receipt(snapshot, "rejected"));
            var root = Root();
            object section = Create(root, state, true);
            Refresh(section);
            StringAssert.Contains("Rejected", root.Q<Label>("tools-apply-status").text);
            var button = root.Q<Button>("retry-tools-button");
            Assert.IsTrue(button.enabledSelf);
            // Invoke the actual registered Clickable callbacks, without a real pointer or modal UI.
            typeof(Clickable).GetMethod("Invoke", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button.clickable, new object[] { null });
            Assert.AreEqual(1, retries);
            StringAssert.DoesNotContain("Server catalog applied", root.Q<Label>("tools-apply-status").text);
            pending.SetResult(true);
        }

        [Test]
        public void Stdio_DoesNotReuseHttpAppliedReceipt()
        {
            var state = new ToolSettingsTestDriver();
            object snapshot = state.Capture();
            state.Connect();
            state.Call("BeginAttempt", state.Owner);
            state.Call("AcceptReceipt", state.Owner, state.Receipt(snapshot));
            var root = Root();
            object section = Create(root, state, false);
            Refresh(section);
            StringAssert.Contains("Not confirmed", root.Q<Label>("tools-apply-status").text);
            StringAssert.Contains("sync", root.Q<Label>("tools-apply-status").text);
            Assert.IsFalse(root.Q<Button>("retry-tools-button").enabledSelf);
        }

        [Test]
        public void DetachedPanel_ReleasesStatusSubscription()
        {
            Type panelType = typeof(VisualElement).Assembly.GetType("UnityEngine.UIElements.Panel");
            Assert.IsNotNull(panelType, "This lifecycle test requires the installed Unity UI Toolkit Panel type.");
            MethodInfo createPanel = panelType.GetMethod(
                "CreateEditorPanel",
                BindingFlags.Static | BindingFlags.NonPublic,
                null,
                new[] { typeof(UnityEngine.ScriptableObject) },
                null
            );
            Assert.IsNotNull(createPanel, "This Unity version must expose its in-memory Editor test-panel factory; a synthetic detach event is insufficient.");
            var owner = UnityEngine.ScriptableObject.CreateInstance<OwnedPanelOwner>();
            owner.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
            IPanel panel = null;
            try
            {
                panel = createPanel.Invoke(null, new object[] { owner }) as IPanel;
                Assert.IsNotNull(panel);
                var state = new ToolSettingsTestDriver();
                state.Capture();
                var root = Root();
                int attached = 0,
                    detached = 0;
                root.RegisterCallback<AttachToPanelEvent>(evt =>
                {
                    if (ReferenceEquals(evt.target, root))
                        attached++;
                });
                root.RegisterCallback<DetachFromPanelEvent>(evt =>
                {
                    if (ReferenceEquals(evt.target, root))
                        detached++;
                });
                object section = Create(root, state, true);
                FieldInfo callbacks = state.Value.GetType().GetField("StatusChanged", BindingFlags.Instance | BindingFlags.NonPublic);
                Assert.IsNotNull(callbacks);

                panel.visualTree.Add(root);
                Assert.AreSame(panel, root.panel);
                Assert.AreEqual(1, attached);
                Assert.AreEqual(1, ((Delegate)callbacks.GetValue(state.Value)).GetInvocationList().Length);
                root.RemoveFromHierarchy();
                Assert.IsNull(root.panel);
                Assert.AreEqual(1, detached);
                Assert.IsNull(callbacks.GetValue(state.Value));
                Refresh(section);
                Assert.IsNull(callbacks.GetValue(state.Value), "Refreshing a detached section must not subscribe again.");

                panel.visualTree.Add(root);
                Assert.AreEqual(2, attached);
                Assert.AreEqual(1, ((Delegate)callbacks.GetValue(state.Value)).GetInvocationList().Length);
                root.RemoveFromHierarchy();
                Assert.AreEqual(2, detached);
                Assert.IsNull(root.panel);
                Assert.IsNull(callbacks.GetValue(state.Value));
                TestContext.WriteLine("DEV21_PANEL_LIFECYCLE real_panel=true attach=2 detach=2 subscriptions_after_each_detach=0");
            }
            finally
            {
                try
                {
                    panel?.Dispose();
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(owner);
                }
            }
        }

        private sealed class OwnedPanelOwner : UnityEngine.ScriptableObject { }

        private static VisualElement Root()
        {
            var root = new VisualElement();
            root.Add(new Label("uninitialized") { name = "tools-apply-status" });
            root.Add(new Button { name = "retry-tools-button" });
            root.Add(new Label { name = "tools-summary" });
            root.Add(new VisualElement { name = "tool-category-container" });
            return root;
        }

        private static object Create(VisualElement root, ToolSettingsTestDriver state, bool http)
        {
            foreach (ConstructorInfo constructor in typeof(McpToolsSection).GetConstructors())
            {
                var parameters = constructor.GetParameters();
                if (parameters.Length == 3 && parameters[1].ParameterType.IsInstanceOfType(state.Value))
                    return constructor.Invoke(new[] { (object)root, state.Value, http });
            }
            Assert.Fail("The Tools section must accept an in-memory status source and transport mode.");
            return null;
        }

        private static void Refresh(object section) => section.GetType().GetMethod("Refresh").Invoke(section, null);

        private static FieldInfo ServiceField(string name) => typeof(MCPServiceLocator).GetField(name, BindingFlags.Static | BindingFlags.NonPublic);

        private sealed class EmptyDiscovery : IToolDiscoveryService
        {
            public List<ToolMetadata> DiscoverAllTools() => new();

            public ToolMetadata GetToolMetadata(string name) => null;

            public List<ToolMetadata> GetEnabledTools() => new();

            public bool IsToolEnabled(string name) => false;

            public void SetToolEnabled(string name, bool enabled) => Assert.Fail("No test may write a real preference or grant consent.");

            public void InvalidateCache() { }
        }
    }
}
