using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Windows;
using MCPForUnity.Editor.Windows.Components.AssetGen;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using MCPForUnity.Editor.Windows.Components.Connection;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Windows
{
    [Parallelizable(ParallelScope.None)]
    public sealed class SetupAndProviderRecoveryTests
    {
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;
        private const string Base = "Packages/com.ykh09242.unity-mcp/Editor/Windows/";

        private sealed class EvidenceWindow : EditorWindow { }

        private static void Set(object target, string name, object value)
        {
            var field = target.GetType().GetField(name, Instance);
            Assert.IsNotNull(field, "Expected recovery state: " + name);
            field.SetValue(target, value);
        }

        private static object Invoke(object target, string name, params object[] args) => target.GetType().GetMethod(name, Instance).Invoke(target, args);

        private sealed class Client : IMcpClientConfigurator
        {
            public string Id { get; set; }
            public string DisplayName => Id;
            public McpStatus Status => default;
            public ConfiguredTransport ConfiguredTransport => default;
            public bool SupportsAutoConfigure => true;
            public bool IsInstalled => true;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports => Array.Empty<ConfiguredTransport>();

            public string GetConfigureActionLabel() => "Configure";

            public string GetConfigPath() => "synthetic";

            public McpStatus CheckStatus(bool attemptAutoRewrite = true) => default;

            public void Configure() => Assert.Fail("Real configuration is forbidden.");

            public void Unregister() => Assert.Fail("No unregister.");

            public string GetManualSnippet() => "";

            public IList<string> GetInstallationSteps() => Array.Empty<string>();

            public bool SupportsSkills => false;

            public string GetSkillInstallPath() => null;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedSetupRemainsOpenAndRetryDoesNotRepeatSuccess(bool allFail)
        {
            var probe = typeof(MCPSetupWindow).GetField("dependencyCheck", Static);
            object old = probe.GetValue(null);
            MCPSetupWindow window = null;
            try
            {
                probe.SetValue(null, (Func<bool, Task<DependencyCheckResult>>)(_ => Task.FromResult(new DependencyCheckResult())));
                window = ScriptableObject.CreateInstance<MCPSetupWindow>();
                window.CreateGUI();
                Assert.IsNotNull(window.rootVisualElement.Q<Label>("clients-result"), "Failures need a persistent inline result.");
                var clients = new IMcpClientConfigurator[]
                {
                    new Client { Id = "first" },
                    new Client { Id = "second" },
                };
                var calls = new Dictionary<string, int>();
                bool retry = false;
                int completed = 0;
                Set(window, "getClients", (Func<IReadOnlyList<IMcpClientConfigurator>>)(() => clients));
                Set(
                    window,
                    "configureClient",
                    (Action<IMcpClientConfigurator>)(
                        c =>
                        {
                            calls[c.Id] = calls.TryGetValue(c.Id, out int count) ? count + 1 : 1;
                            if (!retry && (allFail || c.Id == "second"))
                                throw new InvalidOperationException("synthetic-private-error");
                        }
                    )
                );
                Set(window, "finishSetup", (Action)(() => completed++));
                Invoke(window, "ShowClientsStep");
                Invoke(window, "OnConfigureSelectedClicked");
                Assert.AreEqual(0, completed);
                StringAssert.Contains("failed", window.rootVisualElement.Q<Label>("clients-result").text);
                StringAssert.DoesNotContain("synthetic-private-error", window.rootVisualElement.Q<Label>("clients-result").text);
                Invoke(window, "ShowClientsStep");
                retry = true;
                Invoke(window, "OnConfigureSelectedClicked");
                Assert.AreEqual(allFail ? 2 : 1, calls["first"]);
                Assert.AreEqual(2, calls["second"]);
                Assert.AreEqual(1, completed);
            }
            finally
            {
                if (window != null)
                    UnityEngine.Object.DestroyImmediate(window);
                probe.SetValue(null, old);
            }
        }

        [Test]
        public void CancelledPreviewClearsPreviousClientStatus()
        {
            var section = (McpClientConfigSection)FormatterServices.GetUninitializedObject(typeof(McpClientConfigSection));
            var status = new Label("previous client ready");
            Set(section, "configurationChangeStatusLabel", status);
            Set(section, "configurationPreviewText", new TextField { value = "previous" });
            var apply = new Button();
            Set(section, "applyConfigurationButton", apply);
            Invoke(section, "OnCancelConfigurationPreviewClicked");
            Assert.IsEmpty(status.text);
            Assert.IsFalse(apply.enabledSelf);
        }

        [TestCase("stale_file", "Preview again")]
        [TestCase("locked", "unlock")]
        [TestCase("applied", "Saved")]
        [TestCase("restored", "Restored")]
        public void ClientResultExplainsAffectedClientAndRecovery(string code, string expected)
        {
            var method = typeof(McpClientConfigSection).GetMethod("ConfigurationStatusMessage", Static);
            Assert.IsNotNull(method);
            string message = (string)method.Invoke(null, new object[] { code, "Synthetic Client" });
            StringAssert.Contains("Synthetic Client", message);
            StringAssert.Contains(expected, message);
            StringAssert.DoesNotContain(code + ":", message);
        }

        [UnityTest]
        public IEnumerator LocalConnectionBlockerFocusesItsFieldAndClearsAfterCorrection()
        {
            var fields = new[] { "_bridgeService", "_serverManagementService" };
            var previousServices = fields.ToDictionary(n => n, n => typeof(MCPServiceLocator).GetField(n, Static).GetValue(null));
            var keys = new[] { EditorPrefKeys.HttpBaseUrl, EditorPrefKeys.HttpTransportScope };
            var prefs = keys.ToDictionary(k => k, k => (exists: EditorPrefs.HasKey(k), value: EditorPrefs.GetString(k)));
            bool hadHttp = EditorPrefs.HasKey(EditorPrefKeys.UseHttpTransport);
            bool http = EditorPrefs.GetBool(EditorPrefKeys.UseHttpTransport);
            bool pinned = SessionState.GetBool("MCPForUnity.ForceStdioForSession", false);
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            try
            {
                var fixture = typeof(MCPForUnityTests.Editor.Windows.McpConnectionSectionInteractionTests);
                object bridge = Activator.CreateInstance(fixture.GetNestedType("FakeBridge", BindingFlags.NonPublic), true);
                var serverType = fixture.GetNestedType("FakeServer", BindingFlags.NonPublic);
                object server = Activator.CreateInstance(serverType, true);
                serverType.GetField("Reachable").SetValue(server, true);
                typeof(MCPServiceLocator).GetField("_bridgeService", Static).SetValue(null, bridge);
                typeof(MCPServiceLocator).GetField("_serverManagementService", Static).SetValue(null, server);
                EditorConfigurationCache.Instance.UnpinStdioForSession();
                EditorConfigurationCache.Instance.SetUseHttpTransport(true);
                EditorConfigurationCache.Instance.SetHttpTransportScope("local");
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://not-local.example:8080");
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/Connection/McpConnectionSection.uxml").CloneTree();
                window.rootVisualElement.Add(root);
                var section = (McpConnectionSection)FormatterServices.GetUninitializedObject(typeof(McpConnectionSection));
                Set(section, "<Root>k__BackingField", root);
                Invoke(section, "CacheUIElements");
                var protocol = typeof(McpConnectionSection).GetNestedType("TransportProtocol", BindingFlags.NonPublic);
                root.Q<EnumField>("transport-dropdown").Init((Enum)Enum.Parse(protocol, "HTTPLocal"));
                section.UpdateConnectionStatus();
                var blocker = root.Q<Label>("connection-blocker");
                Assert.IsNotEmpty(blocker.text);
                Assert.IsFalse(root.Q<Button>("connection-toggle").enabledSelf);
                var action = root.Q<Button>("connection-fix");
                Assert.IsFalse(action.ClassListContains("hidden"));
                action.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    action.SendEvent(submit);
                yield return null;
                var target = root.Q<TextField>("http-url");
                var focus = target.focusController.focusedElement as VisualElement;
                Assert.IsTrue(focus == target || focus != null && target.Contains(focus));
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:8080");
                section.UpdateConnectionStatus();
                Assert.IsEmpty(blocker.text);
                Assert.IsTrue(action.ClassListContains("hidden"));
                Assert.IsTrue(root.Q<Button>("connection-toggle").enabledSelf);
            }
            finally
            {
                window.Close();
                EditorConfigurationCache.Instance.SetUseHttpTransport(http);
                if (!hadHttp)
                    EditorPrefs.DeleteKey(EditorPrefKeys.UseHttpTransport);
                foreach (var pref in prefs)
                    if (pref.Value.exists)
                        EditorPrefs.SetString(pref.Key, pref.Value.value);
                    else
                        EditorPrefs.DeleteKey(pref.Key);
                if (pinned)
                    EditorConfigurationCache.Instance.PinStdioForSession();
                EditorConfigurationCache.Instance.Refresh();
                foreach (var previous in previousServices)
                    typeof(MCPServiceLocator).GetField(previous.Key, Static).SetValue(null, previous.Value);
            }
        }

        private sealed class Store : ISecureKeyStore
        {
            public int Writes;

            public bool Has(string id) => Writes > 0;

            public bool TryGet(string id, out string key)
            {
                key = null;
                throw new InvalidOperationException("No key read");
            }

            public void Set(string id, string key)
            {
                Assert.AreEqual("synthetic-key", key);
                Writes++;
            }

            public void Delete(string id) => Assert.Fail("No deletion");
        }

        [UnityTest]
        public IEnumerator ProviderCredentialsUseExplicitSaveAndSharedRoutes()
        {
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            var store = new Store();
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network") };
            try
            {
                SecureKeyStore.OverrideForTests(store);
                AssetGenModelCatalog.ResetForTests(true);
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                window.rootVisualElement.Add(root);
                var section = new McpAssetGenSection(root);
                var credentials = root.Q<Foldout>("provider-credentials");
                Assert.IsNotNull(credentials, "Credentials must have their own visible group.");
                var key = root.Q<TextField>("provider-key-tripo");
                Assert.AreEqual("API key", key.label);
                key.value = "synthetic-key";
                key.Focus();
                yield return null;
                var save = root.Q<Button>("provider-save-tripo");
                save.Focus();
                yield return null;
                Assert.AreEqual(0, store.Writes, "Leaving the field is not a Save action.");
                section.Refresh();
                Assert.AreSame(key, root.Q<TextField>("provider-key-tripo"));
                Assert.AreEqual("synthetic-key", key.value, "Tab refresh keeps the unsaved field.");
                using (var submit = NavigationSubmitEvent.GetPooled())
                    save.SendEvent(submit);
                yield return null;
                Assert.AreEqual(1, store.Writes);
                Assert.IsEmpty(key.value);
                credentials.value = false;
                var route = root.Q<Button>("provider-settings-audio-fal");
                Assert.IsNotNull(route);
                route.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    route.SendEvent(submit);
                yield return null;
                Assert.IsTrue(credentials.value);
                var falKey = root.Q<TextField>("provider-key-fal");
                var focused = falKey.focusController.focusedElement as VisualElement;
                Assert.IsTrue(focused == falKey || (focused != null && falKey.Contains(focused)), "The route must focus the shared key field.");
                key.value = "synthetic-key";
                falKey.value = "synthetic-key";
                var saveFal = root.Q<Button>("provider-save-fal");
                saveFal.Focus();
                yield return null;
                using (var submit = NavigationSubmitEvent.GetPooled())
                    saveFal.SendEvent(submit);
                yield return null;
                Assert.AreEqual("synthetic-key", key.value, "Saving the shared key must not destroy another provider draft.");
                Assert.IsEmpty(transport.RecordedRequests);
            }
            finally
            {
                window.Close();
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }

        [UnityTest, Explicit("Controller-owned provider and recovery layout evidence.")]
        public IEnumerator NarrowProviderCredentialsRemainVisible()
        {
            var skinIndex = typeof(EditorGUIUtility).GetProperty("skinIndex", Static);
            object originalSkin = skinIndex.GetValue(null);
            try
            {
                foreach (int skin in new[] { 1, 0 })
                {
                    skinIndex.SetValue(null, skin);
                    UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
                    for (int i = 0; i < 12; i++)
                        yield return null;
                    Assert.AreEqual(skin == 1, EditorGUIUtility.isProSkin);
                    yield return CaptureProviderCredentials();
                }
            }
            finally
            {
                skinIndex.SetValue(null, originalSkin);
                UnityEditorInternal.InternalEditorUtility.RepaintAllViews();
            }
        }

        private IEnumerator CaptureProviderCredentials()
        {
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            McpWindowNavigation navigation = null;
            try
            {
                SecureKeyStore.OverrideForTests(new Store());
                AssetGenModelCatalog.ResetForTests(true);
                var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network") };
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                window.ShowUtility();
                window.position = new Rect(30, 30, 500, 600);
                var root = window.rootVisualElement;
                root.AddToClassList("mcp-editor");
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "MCPForUnityEditorWindow.uxml").CloneTree(root);
                AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree(root.Q("assetgen-container"));
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                var section = new McpAssetGenSection(root.Q("assetgen-container"));
                root.Q("clients-panel").AddToClassList("hidden");
                root.Q("assetgen-panel").RemoveFromClassList("hidden");
                navigation = new McpWindowNavigation(root, _ => { });
                navigation.Select("assetgen");
                string directory = System.IO.Path.GetFullPath(
                    System.IO.Path.Combine(Application.dataPath, "../Library/McpTestEvidence/setup-recovery-" + Guid.NewGuid().ToString("N"))
                );
                System.IO.Directory.CreateDirectory(directory);
                foreach (string theme in new[] { EditorGUIUtility.isProSkin ? "dark" : "light" })
                {
                    root.EnableInClassList("unity-theme-dark", theme == "dark");
                    root.EnableInClassList("unity-theme-light", theme == "light");
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    section.FocusProviderCredentials("fal");
                    for (int i = 0; i < 12; i++)
                    {
                        window.Repaint();
                        yield return null;
                    }
                    var scroll = root.Q<ScrollView>("assetgen-panel");
                    var key = root.Q<TextField>("provider-key-fal");
                    Assert.IsTrue(key.worldBound.Overlaps(scroll.contentViewport.worldBound));
                    Assert.Greater(key.worldBound.width, 120);
                    Assert.LessOrEqual(root.Q<Button>("provider-save-fal").worldBound.yMax, scroll.contentViewport.worldBound.yMax);
                    Assert.LessOrEqual(scroll.horizontalScroller.highValue, 1);
                    string path = System.IO.Path.Combine(directory, "credentials-500-" + theme + ".png");
                    typeof(OperationActivityTests).GetMethod("Capture", Static).Invoke(null, new object[] { window, path });
                    TestContext.Out.WriteLine("SETUP_RECOVERY_CAPTURE " + path);
                }
            }
            finally
            {
                navigation?.Dispose();
                window.Close();
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }

        [Test]
        public void BundledModelRefreshFollowsSharedSelectionWithoutReplacingCredentials()
        {
            var slot = typeof(SecureKeyStore).GetField("_current", Static);
            var previous = (ISecureKeyStore)slot.GetValue(null);
            string originalModel = AssetGenPrefs.GetSelectedModel("model", "tripo");
            var window = ScriptableObject.CreateInstance<EvidenceWindow>();
            try
            {
                SecureKeyStore.OverrideForTests(new Store());
                AssetGenModelCatalog.ResetForTests(true);
                var transport = new FakeHttpTransport { Handler = _ => throw new InvalidOperationException("No network") };
                FalModelCatalog.TransportOverrideForTests = transport;
                OpenRouterModelCatalog.TransportOverrideForTests = transport;
                AssetGenPrefs.SetSelectedModel("model", "tripo", AssetGenModelCatalog.ForProvider("tripo", "model").First().Id);
                window.ShowUtility();
                var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
                root.Q("blender-bridge-panel").RemoveFromHierarchy();
                window.rootVisualElement.Add(root);
                var section = new McpAssetGenSection(root);
                var key = root.Q<TextField>("provider-key-tripo");
                key.value = "synthetic-key";
                AssetGenPrefs.SetSelectedModel("model", "tripo", "external-synthetic-model");
                section.Refresh();
                StringAssert.Contains("external-synthetic-model", root.Q("provider-model-model-tripo").Q<DropdownField>().value);
                Assert.AreSame(key, root.Q<TextField>("provider-key-tripo"));
                Assert.AreEqual("synthetic-key", key.value);
                Assert.IsEmpty(transport.RecordedRequests);
            }
            finally
            {
                window.Close();
                AssetGenPrefs.SetSelectedModel("model", "tripo", originalModel);
                AssetGenModelCatalog.ResetForTests(true);
                SecureKeyStore.OverrideForTests(previous);
            }
        }

        [Test]
        public void ScopeLabelsDistinguishUserDefaultsFromProjectDeployment()
        {
            var root = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/Advanced/McpAdvancedSection.uxml").CloneTree();
            Assert.That(root.Q<Label>("settings-user-scope")?.text, Does.Contain("User"));
            Assert.That(root.Q<Label>("settings-project-scope")?.text, Does.Contain("Project"));
            var gen = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(Base + "Components/AssetGen/McpAssetGenSection.uxml").CloneTree();
            Assert.That(gen.Q<Label>("generation-scope")?.text, Does.Contain("User"));
            Assert.AreEqual("Host", gen.Q<TextField>("blender-host").label);
            Assert.AreEqual("Port", gen.Q<TextField>("blender-port").label);
        }
    }
}
