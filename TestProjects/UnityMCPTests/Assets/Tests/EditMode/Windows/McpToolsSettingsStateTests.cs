using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Windows.Components.Tools;
using MCPForUnityTests.Editor.Services;
using NUnit.Framework;
using UnityEditor;
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

        [TestCase(false)]
        [TestCase(true)]
        public void StartupProfile_SelectionIsExplicitAndRemoteHttpCannotChangeIt(bool remote)
        {
            // Given: a project has no startup override; native tool selection has a read-only fake.
            string key = "MCPForUnity.StartupToolProfile." + ProjectIdentityUtility.GetProjectHash();
            bool hadProfile = EditorPrefs.HasKey(key);
            string savedProfile = EditorPrefs.GetString(key, "");
            const string scopeKey = "MCPForUnity.HttpTransportScope";
            bool hadScope = EditorPrefs.HasKey(scopeKey);
            string savedScope = EditorPrefs.GetString(scopeKey, "local");
            try
            {
                EditorPrefs.DeleteKey(key);
                EditorPrefs.SetString(scopeKey, remote ? "remote" : "local");
                EditorConfigurationCache.Instance.Refresh();
                var root = Root();
                root.Add(new DropdownField { name = "startup-tool-profile" });
                root.Add(new Label { name = "startup-tool-profile-help" });
                var section = new McpToolsSection(root, null, true);
                var dropdown = root.Q<DropdownField>("startup-tool-profile");
                // When: the panel reads startup settings, without enabling or granting tools.
                section.Refresh();
                // Then: default emits no persisted choice; remote HTTP cannot change server startup.
                Assert.AreEqual("Server default", dropdown.value);
                Assert.IsFalse(EditorPrefs.HasKey(key));
                Assert.AreEqual(!remote, dropdown.enabledSelf);
                if (remote)
                {
                    StringAssert.Contains("Remote HTTP", dropdown.tooltip);
                    dropdown.value = "Full (static clients)";
                    Assert.IsFalse(EditorPrefs.HasKey(key));
                }
                else
                {
                    dropdown.value = "Full (static clients)";
                    Assert.AreEqual("full", EditorPrefs.GetString(key));
                    StringAssert.Contains("Restart local HTTP", root.Q<Label>("startup-tool-profile-help").text);
                    Assert.IsFalse(root.Q<Label>("startup-tool-profile-help").ClassListContains("catalog-hidden"));
                    dropdown.value = "Core (compact)";
                    Assert.AreEqual("core", EditorPrefs.GetString(key));
                    dropdown.value = "Server default";
                    Assert.IsFalse(EditorPrefs.HasKey(key));
                    Assert.IsTrue(root.Q<Label>("startup-tool-profile-help").ClassListContains("catalog-hidden"));
                }
            }
            finally
            {
                if (hadProfile)
                    EditorPrefs.SetString(key, savedProfile);
                else
                    EditorPrefs.DeleteKey(key);
                if (hadScope)
                    EditorPrefs.SetString(scopeKey, savedScope);
                else
                    EditorPrefs.DeleteKey(scopeKey);
                EditorConfigurationCache.Instance.Refresh();
            }
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

        [TestCase(false)]
        [TestCase(true)]
        public void ConsentRow_LoadAndCancelledEnableNeverWriteOrNotify(bool legacyGrant)
        {
            using var consent = new ScopedConsentTestState();
            string key = MCPForUnity.Editor.Constants.EditorPrefKeys.ToolEnabledPrefix + "ExplicitConsent.execute_code";
            bool? before = UnityEditor.EditorPrefs.HasKey(key) ? UnityEditor.EditorPrefs.GetBool(key) : (bool?)null;
            try
            {
                UnityEditor.EditorPrefs.SetBool(key, legacyGrant);
                var status = new ConsentNotifications();
                var root = ConsentRoot();
                int prompts = 0;
                var section = ConsentSection(
                    root,
                    consent,
                    status,
                    _ =>
                    {
                        prompts++;
                        return null;
                    }
                );
                using var panel = new OwnedConsentPanel(root);
                section.Refresh();
                Assert.AreEqual(0, consent.StorageWrites);
                var toggle = root.Q<Toggle>(className: "tool-item-toggle");
                Assert.IsNotNull(toggle.panel, "The value setter must dispatch through a real attached panel.");
                toggle.value = true;
                Assert.IsFalse(toggle.value);
                Assert.AreEqual(1, prompts);
                Assert.AreEqual(0, consent.StorageWrites);
                Assert.AreEqual(0, status.Notifications);
                if (legacyGrant)
                    StringAssert.Contains("Previous global consent is inactive", root.Q<Label>("consent-scope-execute_code").text);
            }
            finally
            {
                if (before.HasValue)
                    UnityEditor.EditorPrefs.SetBool(key, before.Value);
                else
                    UnityEditor.EditorPrefs.DeleteKey(key);
            }
        }

        [TestCase(ToolConsentScope.EditorSession)]
        [TestCase(ToolConsentScope.Project)]
        public void ConsentRow_ExplicitScopePublishesOnceAndRevokeBlocksBulkRecovery(ToolConsentScope scope)
        {
            using var consent = new ScopedConsentTestState();
            var status = new ConsentNotifications();
            var root = ConsentRoot();
            var section = ConsentSection(root, consent, status, _ => scope);
            using var panel = new OwnedConsentPanel(root);
            section.Refresh();
            var toggle = root.Q<Toggle>(className: "tool-item-toggle");
            Assert.IsNotNull(toggle.panel, "The value setter must dispatch through a real attached panel.");
            toggle.value = true;
            Assert.IsTrue(toggle.value);
            Assert.AreEqual(scope, consent.Service.GetStatus("execute_code").Scope);
            Assert.AreEqual(1, status.Notifications);
            Click(root.Q<Button>("consent-revoke-execute_code"));
            Assert.IsFalse(toggle.value);
            Assert.IsTrue(consent.Service.GetStatus("execute_code").RequiresExplicitGrant);
            Assert.AreEqual(2, status.Notifications);
            int writes = consent.StorageWrites;
            Click(root.Q<Button>("enable-all-button"));
            Assert.IsFalse(toggle.value);
            Assert.AreEqual(writes, consent.StorageWrites, "Bulk enable cannot mint or retry a dangerous grant.");
            Assert.AreEqual(2, status.Notifications);
        }

        [Test]
        public void ConsentRow_ScopeReplacementRemovesRememberedAlternative()
        {
            using var consent = new ScopedConsentTestState("execute_code");
            Assert.IsTrue(consent.Service.Grant("execute_code", ToolConsentScope.Project).Succeeded);
            var status = new ConsentNotifications();
            var root = ConsentRoot();
            var section = ConsentSection(root, consent, status, _ => ToolConsentScope.EditorSession);
            section.Refresh();
            Click(root.Q<Button>("consent-change-execute_code"));
            Assert.AreEqual(ToolConsentScope.EditorSession, consent.Service.GetStatus("execute_code").Scope);
            var keys = ToolConsentService.KeysFor(ToolConsentService.NormalizeProjectRoot(consent.Root), "execute_code");
            Assert.IsFalse(consent.GetBool(ToolConsentStorageScope.Project, keys.ProjectGrant));
            Assert.AreEqual(1, status.Notifications);
        }

        [Test]
        public void ConsentRow_CommittedThenThrowCannotBeRetriedBySetterOrPassiveRefresh()
        {
            using var consent = new ScopedConsentTestState();
            var status = new ConsentNotifications();
            var root = ConsentRoot();
            var section = ConsentSection(root, consent, status, _ => ToolConsentScope.Project);
            using var panel = new OwnedConsentPanel(root);
            section.Refresh();
            consent.FailAfterNextWrite = true;
            Assert.IsNotNull(root.Q<Toggle>(className: "tool-item-toggle").panel, "The value setter must dispatch through a real attached panel.");
            root.Q<Toggle>(className: "tool-item-toggle").value = true;
            Assert.IsFalse(consent.Service.GetStatus("execute_code").Enabled);
            Assert.IsFalse(root.Q<Toggle>(className: "tool-item-toggle").value);
            StringAssert.Contains("Blocked", root.Q<Label>("tools-note").text);
            int writes = consent.StorageWrites;
            MCPServiceLocator.ToolDiscovery.SetToolEnabled("execute_code", true);
            section.Refresh();
            Assert.AreEqual(writes, consent.StorageWrites);
            Assert.IsFalse(consent.Service.GetStatus("execute_code").Enabled);
            Click(root.Q<Button>("consent-change-execute_code"));
            Assert.IsTrue(consent.Service.GetStatus("execute_code").Enabled, "A new explicit scope action can recover.");
            Assert.AreEqual(ToolConsentScope.Project, consent.Service.GetStatus("execute_code").Scope);
        }

        [Test]
        public void ConsentRow_AllStorageFailureShowsCurrentDomainOnlyWarning()
        {
            using var consent = new ScopedConsentTestState("execute_code");
            var root = ConsentRoot();
            var section = ConsentSection(root, consent, new ConsentNotifications(), _ => ToolConsentScope.EditorSession);
            section.Refresh();
            consent.FailAll = true;
            Click(root.Q<Button>("consent-revoke-execute_code"));
            Assert.IsFalse(root.Q<Toggle>(className: "tool-item-toggle").value);
            StringAssert.Contains("could not be confirmed across reload/restart", root.Q<Label>("tools-note").text);
            section.Refresh();
            StringAssert.Contains("could not be confirmed across reload/restart", root.Q<Label>("tools-note").text);
            Assert.IsFalse(consent.Service.GetStatus("execute_code").Enabled);
        }

        [Test]
        public void ConsentRows_BulkRevokeNotifiesOnceForTwoActualPolicyChanges()
        {
            using var consent = new ScopedConsentTestState("execute_code", "manage_packages");
            var status = new ConsentNotifications();
            var root = ConsentRoot();
            var section = ConsentSection(root, consent, status, _ => ToolConsentScope.EditorSession);
            section.Refresh();
            Assert.AreEqual(0, status.Notifications);
            Click(root.Q<Button>("disable-all-button"));
            foreach (string name in new[] { "execute_code", "manage_packages" })
            {
                var state = consent.Service.GetStatus(name);
                Assert.IsFalse(state.Enabled);
                Assert.IsTrue(state.RequiresExplicitGrant);
            }
            Assert.AreEqual(1, status.Notifications, "One bulk action must publish one settings change, rather than one per tool.");
            Click(root.Q<Button>("disable-all-button"));
            Assert.AreEqual(1, status.Notifications, "Repeating an unchanged policy action must not publish another change.");
        }

        [Test, Explicit("Real-store individual actions only in a fresh admitted CORE12 disposable project; platform dialog interaction is separate.")]
        public void NativeConsent_RealPanelCancelSessionProjectAndRevoke()
        {
            using var consent = new NativeConsentProjectScope();
            var asset = UnityEditor.AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
                "Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/Tools/McpToolsSection.uxml"
            );
            Assert.IsNotNull(asset, "Load the actual installed Tools UXML, not a synthetic row tree.");
            var root = asset.CloneTree();
            Type panelType = typeof(VisualElement).Assembly.GetType("UnityEngine.UIElements.Panel");
            Assert.IsNotNull(panelType);
            MethodInfo createPanel = panelType.GetMethod(
                "CreateEditorPanel",
                BindingFlags.Static | BindingFlags.NonPublic,
                null,
                new[] { typeof(UnityEngine.ScriptableObject) },
                null
            );
            Assert.IsNotNull(createPanel);
            var owner = UnityEngine.ScriptableObject.CreateInstance<OwnedPanelOwner>();
            owner.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
            IPanel panel = null;
            ToolConsentScope? choice = null;
            int prompts = 0;
            try
            {
                panel = createPanel.Invoke(null, new object[] { owner }) as IPanel;
                Assert.IsNotNull(panel);
                var section = new McpToolsSection(
                    root,
                    new ConsentNotifications(),
                    false,
                    tool =>
                    {
                        Assert.AreEqual("execute_code", tool.Name);
                        prompts++;
                        return choice;
                    }
                );
                panel.visualTree.Add(root);
                Assert.AreSame(panel, root.panel);
                section.Refresh();
                var toggle = root.Query<Toggle>(className: "tool-item-toggle").ToList().Single(item => item.name == "tool-toggle-execute_code");
                int writes = consent.Store.Writes;
                consent.DuringOwnedMutation("execute_code", () => toggle.value = true); // Actual callback; deterministic modal cancellation.
                consent.AssertOwnedState();
                Assert.IsFalse(toggle.value);
                Assert.AreEqual(1, prompts);
                Assert.AreEqual(writes, consent.Store.Writes);

                choice = ToolConsentScope.EditorSession;
                consent.DuringOwnedMutation("execute_code", () => toggle.value = true);
                consent.RecordGrant("execute_code", ToolConsentScope.EditorSession);
                Assert.IsTrue(toggle.value);
                Assert.AreEqual(ToolConsentScope.EditorSession, consent.Service.GetStatus("execute_code").Scope);
                var label = root.Q<Label>("consent-scope-execute_code");
                StringAssert.Contains("this Editor session", label.text);
                StringAssert.Contains(MCPForUnity.Editor.Helpers.ProjectIdentityUtility.GetProjectName(), label.text);

                choice = ToolConsentScope.Project;
                consent.DuringOwnedMutation("execute_code", () => Click(root.Q<Button>("consent-change-execute_code")));
                consent.RecordGrant("execute_code", ToolConsentScope.Project);
                Assert.AreEqual(ToolConsentScope.Project, consent.Service.GetStatus("execute_code").Scope);
                StringAssert.Contains("remembered for this project", label.text);
                var keys = ToolConsentService.KeysFor(consent.Root, "execute_code");
                Assert.IsTrue(UnityEditor.EditorPrefs.GetBool(keys.ProjectGrant, false));
                Assert.IsFalse(UnityEditor.SessionState.GetBool(keys.SessionGrant(consent.Run), false));

                consent.DuringOwnedMutation("execute_code", () => Click(root.Q<Button>("consent-revoke-execute_code")));
                consent.RecordRevoke("execute_code");
                Assert.IsFalse(toggle.value);
                Assert.IsFalse(consent.Service.GetStatus("execute_code").Enabled);
                Assert.IsFalse(UnityEditor.EditorPrefs.GetBool(keys.ProjectGrant, false));
                Assert.IsFalse(consent.Discovery.IsToolEnabled("execute_code"));
                Assert.AreEqual(3, prompts, "Only cancel, session and project choices invoke the modal seam.");
                CollectionAssert.AreEquivalent(new[] { consent.MainThread }, consent.Store.AccessThreads);
                // No bulk button is clicked: full-default bulk would change global
                // ordinary enablement preferences. Existing isolated49 covers it.
                root.RemoveFromHierarchy();
                Assert.IsNull(root.panel);
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

        private static VisualElement ConsentRoot()
        {
            var root = Root();
            root.Add(new Label { name = "tools-note" });
            root.Add(new Button { name = "enable-all-button" });
            root.Add(new Button { name = "disable-all-button" });
            return root;
        }

        private static McpToolsSection ConsentSection(
            VisualElement root,
            ScopedConsentTestState consent,
            ConsentNotifications source,
            Func<ToolMetadata, ToolConsentScope?> prompt
        )
        {
            MCPServiceLocator.Register<IToolDiscoveryService>(new ConsentDiscovery(consent.Service));
            return new McpToolsSection(root, source, true, prompt);
        }

        private static void Click(Button button)
        {
            Assert.IsNotNull(button);
            typeof(Clickable).GetMethod("Invoke", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(button.clickable, new object[] { null });
        }

        private sealed class ConsentNotifications : IToolSettingsStatusSource
        {
            internal int Notifications;
            public ToolSettingsStatus Status => new("owned", 1, null, null, "owned", "saved", null, null, false, false);
            public event Action StatusChanged
            {
                add { }
                remove { }
            }

            public void NotifyToolSettingsChanged() => Notifications++;

            public Task RetryToolSettingsAsync() => Task.CompletedTask;
        }

        private sealed class ConsentDiscovery : IToolDiscoveryService
        {
            private readonly IToolConsentService consent;
            private readonly List<ToolMetadata> tools = new()
            {
                new()
                {
                    Name = "execute_code",
                    IsBuiltIn = true,
                    Group = "core",
                    RequiresExplicitConsent = true,
                },
                new()
                {
                    Name = "manage_packages",
                    IsBuiltIn = true,
                    Group = "core",
                    RequiresExplicitConsent = true,
                },
            };

            internal ConsentDiscovery(IToolConsentService value)
            {
                consent = value;
            }

            public List<ToolMetadata> DiscoverAllTools() => new(tools);

            public ToolMetadata GetToolMetadata(string name) => tools.Find(tool => tool.Name == name);

            public List<ToolMetadata> GetEnabledTools() => tools.FindAll(tool => consent.GetStatus(tool.Name).Enabled);

            public bool IsToolEnabled(string name) => GetToolMetadata(name) != null && consent.GetStatus(name).Enabled;

            public void SetToolEnabled(string name, bool enabled)
            {
                consent.SetEnabled(name, enabled);
            }

            public void InvalidateCache() { }
        }

        private sealed class OwnedConsentPanel : IDisposable
        {
            private readonly VisualElement root;
            private readonly OwnedPanelOwner owner;
            private readonly IPanel panel;

            internal OwnedConsentPanel(VisualElement root)
            {
                this.root = root;
                Type panelType = typeof(VisualElement).Assembly.GetType("UnityEngine.UIElements.Panel");
                Assert.IsNotNull(panelType, "Consent toggle tests require the installed UI Toolkit Panel type.");
                MethodInfo createPanel = panelType.GetMethod(
                    "CreateEditorPanel",
                    BindingFlags.Static | BindingFlags.NonPublic,
                    null,
                    new[] { typeof(UnityEngine.ScriptableObject) },
                    null
                );
                Assert.IsNotNull(createPanel, "Consent toggle tests require the real in-memory Editor panel factory.");
                owner = UnityEngine.ScriptableObject.CreateInstance<OwnedPanelOwner>();
                owner.hideFlags = UnityEngine.HideFlags.HideAndDontSave;
                try
                {
                    panel = createPanel.Invoke(null, new object[] { owner }) as IPanel;
                    Assert.IsNotNull(panel);
                    panel.visualTree.Add(root);
                    Assert.AreSame(panel, root.panel);
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            public void Dispose()
            {
                try
                {
                    root.RemoveFromHierarchy();
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
