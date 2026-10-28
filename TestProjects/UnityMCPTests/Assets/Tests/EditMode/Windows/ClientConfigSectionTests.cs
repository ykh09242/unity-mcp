using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using System.Threading.Tasks;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Clients.Configurators;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class ClientConfigSectionTests
    {
        private sealed class FoldoutWindow : EditorWindow { }

        [UnityTest]
        public IEnumerator ConfigurationReviewCollapsesEmptyContentAndRestoresItForAPreview()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = slot.GetValue(null);
            bool hadPreference = EditorPrefs.HasKey(EditorPrefKeys.ClientDetailsFoldoutOpen);
            bool savedPreference = EditorPrefs.GetBool(EditorPrefKeys.ClientDetailsFoldoutOpen);
            string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-review-layout-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(directory);
            var window = ScriptableObject.CreateInstance<FoldoutWindow>();
            try
            {
                slot.SetValue(null, new MemoryClientService());
                string path = System.IO.Path.Combine(directory, "synthetic-config");
                var client = new ManualClient { ConfigPath = path };
                System.IO.File.WriteAllText(path, MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Document("json", false));
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, "json");
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, "json");
                var root = window.rootVisualElement;
                AssetDatabase
                    .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/ClientConfig/McpClientConfigSection.uxml")
                    .CloneTree(root);
                var section = CreateSection(client);
                Set(section, "<Root>k__BackingField", root);
                Set(section, "configurationChanges", harness.Service);
                Invoke(section, "CacheUIElements");
                Invoke(section, "InitializeUI");
                root.Q<Foldout>("client-details-foldout").value = true;
                root.Q<Foldout>("client-review-foldout").value = true;
                window.ShowUtility();
                var scroll = root.Q<ScrollView>(className: "config-preview-scroll");
                foreach (int width in new[] { 380, 1144 })
                {
                    window.position = new Rect(40, 40, width, 700);
                    yield return null;
                    yield return null;
                    Assert.That(root.worldBound.width, Is.GreaterThan(0), "The actual Editor panel must have layout.");
                    Assert.AreEqual(DisplayStyle.None, scroll.resolvedStyle.display, "An empty preview must not reserve its 160px viewport.");
                    Assert.AreEqual(DisplayStyle.None, root.Q<Button>("apply-configuration").resolvedStyle.display);
                    Assert.AreEqual(DisplayStyle.None, root.Q<Button>("cancel-configuration-preview").resolvedStyle.display);
                    Assert.IsNotEmpty(root.Q<Label>("configuration-change-status").text);

                    Invoke(section, "OnPreviewConfigurationClicked");
                    yield return null;
                    yield return null;
                    Assert.Greater(scroll.worldBound.height, 0);
                    Assert.IsNotEmpty(root.Q<TextField>("configuration-preview").value);
                    Assert.IsTrue(root.Q<Button>("apply-configuration").enabledSelf);
                    Assert.AreEqual(DisplayStyle.Flex, root.Q<Button>("cancel-configuration-preview").resolvedStyle.display);

                    Invoke(section, "OnCancelConfigurationPreviewClicked");
                    yield return null;
                    yield return null;
                    Assert.AreEqual(DisplayStyle.None, scroll.resolvedStyle.display);
                    Assert.IsEmpty(root.Q<TextField>("configuration-preview").value);
                    Assert.IsNotEmpty(root.Q<Label>("configuration-change-status").text);
                    Assert.IsFalse(root.Q<Button>("apply-configuration").enabledSelf);
                }
                Assert.Zero(client.ConfigureCalls, "Preparing and cancelling a preview must not configure the client.");
                Assert.IsFalse(System.IO.File.ReadAllText(path).Contains("unityMCP"));
            }
            finally
            {
                window.Close();
                slot.SetValue(null, previous);
                if (hadPreference)
                    EditorPrefs.SetBool(EditorPrefKeys.ClientDetailsFoldoutOpen, savedPreference);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.ClientDetailsFoldoutOpen);
                System.IO.Directory.Delete(directory, true);
            }
        }

        [Test]
        public void NestedConfigurationFoldoutsPreserveSelectedClientPreference()
        {
            bool had = EditorPrefs.HasKey(EditorPrefKeys.ClientDetailsFoldoutOpen);
            bool saved = EditorPrefs.GetBool(EditorPrefKeys.ClientDetailsFoldoutOpen);
            var window = ScriptableObject.CreateInstance<FoldoutWindow>();
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.ClientDetailsFoldoutOpen, true);
                window.ShowUtility();
                var root = window.rootVisualElement;
                AssetDatabase
                    .LoadAssetAtPath<VisualTreeAsset>("Packages/com.ykh09242.unity-mcp/Editor/Windows/Components/ClientConfig/McpClientConfigSection.uxml")
                    .CloneTree(root);
                var section = (McpClientConfigSection)FormatterServices.GetUninitializedObject(typeof(McpClientConfigSection));
                Set(section, "<Root>k__BackingField", root);
                Set(section, "configurators", new List<IMcpClientConfigurator>());
                Invoke(section, "CacheUIElements");
                Invoke(section, "InitializeUI");
                var details = root.Q<Foldout>("client-details-foldout");
                foreach (string name in new[] { "client-review-foldout", "manual-config-foldout" })
                {
                    var nested = root.Q<Foldout>(name);
                    nested.value = true;
                    nested.value = false;
                    Assert.IsTrue(details.value);
                    Assert.IsTrue(EditorPrefs.GetBool(EditorPrefKeys.ClientDetailsFoldoutOpen), name);
                }
                details.value = false;
                Assert.IsFalse(EditorPrefs.GetBool(EditorPrefKeys.ClientDetailsFoldoutOpen));
                details.value = true;
                Assert.IsTrue(EditorPrefs.GetBool(EditorPrefKeys.ClientDetailsFoldoutOpen));
            }
            finally
            {
                window.Close();
                if (had)
                    EditorPrefs.SetBool(EditorPrefKeys.ClientDetailsFoldoutOpen, saved);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.ClientDetailsFoldoutOpen);
            }
        }

        [TestCase("path")]
        [TestCase("snippet")]
        [TestCase("steps")]
        public void ManualConfigurationFailureClearsStaleValuesAndRecovers(string failureStage)
        {
            var client = new ManualClient();
            var section = CreateSection(client);
            section.UpdateManualConfiguration();
            Assert.AreEqual("memory-config", Get<TextField>(section, "configPathField").value);
            Assert.AreEqual("memory-json", Get<TextField>(section, "configJsonField").value);

            client.FailureStage = failureStage;
            Assert.DoesNotThrow(section.UpdateManualConfiguration);
            // A generation failure does not invalidate an independently resolved file destination.
            bool retainsPath = failureStage == "snippet";
            Assert.AreEqual(retainsPath ? "memory-config" : string.Empty, Get<TextField>(section, "configPathField").value);
            Assert.IsTrue(string.IsNullOrEmpty(Get<TextField>(section, "configJsonField").value), "A failed generation must clear the stale snippet.");
            StringAssert.Contains("synthetic configuration error", Get<Label>(section, "installationStepsLabel").text);
            Assert.AreEqual(retainsPath, Get<Button>(section, "copyPathButton").enabledSelf);
            Assert.IsFalse(Get<Button>(section, "openFileButton").enabledSelf);
            Assert.IsFalse(Get<Button>(section, "copyJsonButton").enabledSelf);

            client.FailureStage = null;
            section.UpdateManualConfiguration();
            Assert.AreEqual("memory-config", Get<TextField>(section, "configPathField").value);
            Assert.AreEqual("memory-json", Get<TextField>(section, "configJsonField").value);
            Assert.AreEqual("1. Install\n2. Configure", Get<Label>(section, "installationStepsLabel").text);
            Assert.IsTrue(Get<Button>(section, "copyPathButton").enabledSelf);
            Assert.IsFalse(Get<Button>(section, "openFileButton").enabledSelf, "A missing file must not offer Open.");
            Assert.IsTrue(Get<Button>(section, "copyJsonButton").enabledSelf);
        }

        [Test]
        public void SnippetFailureRetainsExistingFileActions()
        {
            string path = System.IO.Path.GetTempFileName();
            try
            {
                var section = CreateSection(new ManualClient { ConfigPath = path, FailureStage = "snippet" });
                section.UpdateManualConfiguration();
                Assert.AreEqual(path, Get<TextField>(section, "configPathField").value);
                Assert.IsTrue(Get<Button>(section, "copyPathButton").enabledSelf);
                Assert.IsTrue(Get<Button>(section, "openFileButton").enabledSelf);
                Assert.IsFalse(Get<Button>(section, "copyJsonButton").enabledSelf);
                Assert.IsTrue(string.IsNullOrEmpty(Get<TextField>(section, "configJsonField").value));
            }
            finally
            {
                System.IO.File.Delete(path);
            }
        }

        [Test]
        public void ManualConfigurationActionsFollowAvailableContent()
        {
            string path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-manual-ui-" + Guid.NewGuid().ToString("N"));
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = slot.GetValue(null);
            try
            {
                var service = new MemoryClientService();
                slot.SetValue(null, service);
                var client = new ManualClient { ConfigPath = path };
                var section = CreateSection(client);
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, "json");
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, "json");
                InitializePreviewUi(section, harness.Service);
                Set(section, "configPathStatusLabel", new Label());
                section.RefreshSelectedClient(forceImmediate: true);
                Assert.IsTrue(Get<Button>(section, "copyPathButton").enabledSelf);
                Assert.IsFalse(Get<Button>(section, "openFileButton").enabledSelf);
                StringAssert.Contains("not been created", Get<Label>(section, "configPathStatusLabel").text);

                Invoke(section, "OnPreviewConfigurationClicked");
                object preview = Get<object>(section, "configurationPreview");
                System.IO.File.WriteAllText(path, "{}");
                section.RefreshSelectedClient(forceImmediate: true);
                Assert.IsTrue(Get<Button>(section, "openFileButton").enabledSelf);
                Assert.IsTrue(Get<Label>(section, "configPathStatusLabel").ClassListContains("hidden"));
                Assert.AreSame(preview, Get<object>(section, "configurationPreview"));
                Assert.IsFalse(Get<Button>(section, "applyConfigurationButton").enabledSelf);
                StringAssert.Contains("configuration file changed", Get<Label>(section, "configurationChangeStatusLabel").text);

                client.ConfigPath = "";
                client.Snippet = "";
                section.UpdateManualConfiguration();
                Assert.IsFalse(Get<Button>(section, "copyPathButton").enabledSelf);
                Assert.IsFalse(Get<Button>(section, "openFileButton").enabledSelf);
                Assert.IsFalse(Get<Button>(section, "copyJsonButton").enabledSelf);
                StringAssert.Contains("manual instructions", Get<Label>(section, "configPathStatusLabel").text);
                Assert.IsFalse(service.RequestedRewrite);
                Assert.Zero(client.ConfigureCalls);
            }
            finally
            {
                slot.SetValue(null, previous);
                System.IO.File.Delete(path);
            }
        }

        [TestCase("codex", false)]
        [TestCase("claude", false)]
        [TestCase("codex", true)]
        [TestCase("claude", true)]
        [TestCase("json", true)]
        public void ActualConfiguratorGenerationFailureDisablesCopyUntilRecovery(string kind, bool missingUvx)
        {
            var cache = EditorConfigurationCache.Instance;
            var flags = BindingFlags.Instance | BindingFlags.NonPublic;
            var values = new Dictionary<FieldInfo, object>();
            var pathsSlot = typeof(MCPServiceLocator).GetField("_pathService", BindingFlags.Static | BindingFlags.NonPublic);
            object originalPaths = pathsSlot.GetValue(null);
            var codexProbe = CodexHttpAuth.SupportsHeadersHelper;
            var claudeProbe = ClaudeHttpAuth.SupportsHeadersHelper;
            var preferences = new Dictionary<string, string>();
            foreach (string key in new[] { EditorPrefKeys.HttpBaseUrl, EditorPrefKeys.GitUrlOverride })
                preferences[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            try
            {
                foreach (string name in new[] { "_useHttpTransport", "_forceStdioForSession", "_httpTransportScope", "_httpBaseUrl" })
                {
                    var field = typeof(EditorConfigurationCache).GetField(name, flags);
                    values[field] = field.GetValue(cache);
                }
                typeof(EditorConfigurationCache).GetField("_useHttpTransport", flags).SetValue(cache, !missingUvx);
                typeof(EditorConfigurationCache).GetField("_forceStdioForSession", flags).SetValue(cache, false);
                typeof(EditorConfigurationCache).GetField("_httpTransportScope", flags).SetValue(cache, "local");
                typeof(EditorConfigurationCache).GetField("_httpBaseUrl", flags).SetValue(cache, "http://127.0.0.1:19040");
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:19040");
                EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, "synthetic-package-source");
                var paths = new ManualPaths();
                pathsSlot.SetValue(null, paths);
                CodexHttpAuth.SupportsHeadersHelper = () => false;
                ClaudeHttpAuth.SupportsHeadersHelper = _ => false;
                McpClientConfiguratorBase client = kind switch
                {
                    "codex" => new CodexConfigurator(),
                    "claude" => new ClaudeCodeConfigurator(),
                    _ => new CursorConfigurator(),
                };
                var section = CreateSection(client);
                var copied = new List<string>();
                Set(section, "copyToClipboard", new Action<string>(copied.Add));
                section.UpdateManualConfiguration();
                var failed = client.GenerateManualConfiguration();
                Assert.IsFalse(failed.Success);
                Assert.IsNull(failed.Snippet);
                Assert.IsNotEmpty(failed.Error);
                Assert.IsFalse(Get<Button>(section, "copyJsonButton").enabledSelf);
                Assert.IsTrue(string.IsNullOrEmpty(Get<TextField>(section, "configJsonField").value));
                StringAssert.Contains(failed.Error, Get<Label>(section, "installationStepsLabel").text);
                // Even stale nonempty display content cannot bypass the disabled action.
                Get<TextField>(section, "configJsonField").SetValueWithoutNotify("generation-failed-sentinel");
                Invoke(section, "OnCopyJsonClicked");
                Assert.IsEmpty(copied, "A generation error must not reach the clipboard write boundary.");

                paths.Uvx = "synthetic-uvx";
                CodexHttpAuth.SupportsHeadersHelper = () => true;
                ClaudeHttpAuth.SupportsHeadersHelper = _ => true;
                section.UpdateManualConfiguration();
                Assert.IsTrue(client.GenerateManualConfiguration().Success);
                Assert.IsTrue(Get<Button>(section, "copyJsonButton").enabledSelf);
                Assert.IsNotEmpty(Get<TextField>(section, "configJsonField").value);
                Assert.IsFalse(Get<Label>(section, "installationStepsLabel").text.Contains(failed.Error));
                StringAssert.Contains(client.GetInstallationSteps()[0], Get<Label>(section, "installationStepsLabel").text);
                Invoke(section, "OnCopyJsonClicked");
                CollectionAssert.AreEqual(new[] { Get<TextField>(section, "configJsonField").value }, copied);
            }
            finally
            {
                pathsSlot.SetValue(null, originalPaths);
                CodexHttpAuth.SupportsHeadersHelper = codexProbe;
                ClaudeHttpAuth.SupportsHeadersHelper = claudeProbe;
                foreach (var pair in values)
                    pair.Key.SetValue(cache, pair.Value);
                foreach (var preference in preferences)
                    if (preference.Value == null)
                        EditorPrefs.DeleteKey(preference.Key);
                    else
                        EditorPrefs.SetString(preference.Key, preference.Value);
            }
        }

        [Test]
        public void ManualConfigurationWithoutStepsDisplaysFallback()
        {
            var client = new ManualClient { Steps = null };
            var section = CreateSection(client);
            section.UpdateManualConfiguration();
            Assert.AreEqual("Configuration steps not available for this client.", Get<Label>(section, "installationStepsLabel").text);
            Assert.IsTrue(Get<Button>(section, "copyJsonButton").enabledSelf);
        }

        [TestCase(McpStatus.Configured)]
        [TestCase(McpStatus.IncorrectPath)]
        public void CodexConfigureButtonAlwaysWritesAndCanBeRepeated(McpStatus initialStatus)
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var originalService = slot.GetValue(null);
            try
            {
                MCPServiceLocator.Register<IClientConfigurationService>(new MemoryClientService());
                var client = new MemoryCodexClient(initialStatus);
                var section = CreateSection(client);
                var click = typeof(McpClientConfigSection).GetMethod("OnConfigureClicked", BindingFlags.Instance | BindingFlags.NonPublic);
                click.Invoke(section, null);
                click.Invoke(section, null);
                Assert.AreEqual(2, client.ConfigureCount, "A configured Codex client must not take the no-op Unregister path");
                Assert.AreEqual("Configure", Get<Button>(section, "configureButton").text);
            }
            finally
            {
                slot.SetValue(null, originalService);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RetryUi_OnlyRetriesFailureAndNeverRefreshesSuccessfulClient(bool startsWithNullSlot)
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            try
            {
                slot.SetValue(null, startsWithNullSlot ? null : new MemoryClientService());
                var peer = new ManualClient { AllowConfigure = true };
                var failed = new ManualClient
                {
                    AllowConfigure = true,
                    ConfigureFails = true,
                    ClientId = "failed",
                };
                bool http = true;
                var service = new ClientConfigurationService(new IMcpClientConfigurator[] { peer, failed }, () => http, v => http = v, () => { }, _ => { });
                slot.SetValue(null, service);
                var section = CreateSection(peer);
                Set(section, "configurators", new List<IMcpClientConfigurator> { peer, failed });
                InitializeRetryUi(section);
                Invoke(section, "OnConfigureAllClientsClicked");
                var retryButton = Get<Button>(section, "retryFailedButton");
                Assert.IsTrue(retryButton.enabledSelf);
                int peerStatuses = peer.StatusCalls;
                int peerConfigures = peer.ConfigureCalls;
                failed.ConfigureFails = false;
                Invoke(section, "OnRetryFailedClientsClicked");
                Assert.AreEqual(peerStatuses, peer.StatusCalls, "Retry must not use the default status refresh for a successful client");
                Assert.AreEqual(peerConfigures, peer.ConfigureCalls);
                Assert.AreEqual(2, failed.ConfigureCalls);
                Assert.IsFalse(retryButton.enabledSelf);
                Assert.AreEqual(0, Get<int>(section, "selectedClientIndex"));
                Assert.IsTrue(http);
                StringAssert.DoesNotContain("owned-sensitive-marker", Get<Label>(section, "retryStatusLabel").text);
            }
            finally
            {
                slot.SetValue(null, original);
            }
        }

        [Test]
        public void RetryUi_ServiceReplacementRefusesWithoutFallingBackToBulk()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            try
            {
                var failed = new ManualClient { AllowConfigure = true, ConfigureFails = true };
                bool http = true;
                var service = new ClientConfigurationService(new[] { failed }, () => http, v => http = v, () => { }, _ => { });
                slot.SetValue(null, service);
                var section = CreateSection(failed);
                InitializeRetryUi(section);
                Invoke(section, "OnConfigureAllClientsClicked");
                slot.SetValue(null, new MemoryClientService());
                Invoke(section, "OnRetryFailedClientsClicked");
                Assert.AreEqual(1, failed.ConfigureCalls);
                Assert.IsFalse(Get<Button>(section, "retryFailedButton").enabledSelf);
                StringAssert.Contains("unavailable", Get<Label>(section, "retryStatusLabel").text.ToLowerInvariant());
            }
            finally
            {
                slot.SetValue(null, original);
            }
        }

        [Test]
        public void RetryUi_PartialFailureKeepsRetryAvailableAndIndividualActionDiscardsIt()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            try
            {
                var failed = new ManualClient { AllowConfigure = true, ConfigureFails = true };
                bool http = true;
                var service = new ClientConfigurationService(new[] { failed }, () => http, v => http = v, () => { }, _ => { });
                slot.SetValue(null, service);
                var section = CreateSection(failed);
                InitializeRetryUi(section);
                Invoke(section, "OnConfigureAllClientsClicked");
                Invoke(section, "OnRetryFailedClientsClicked");
                Assert.IsTrue(Get<Button>(section, "retryFailedButton").enabledSelf);
                StringAssert.Contains("1 failed", Get<Label>(section, "retryStatusLabel").text);
                StringAssert.DoesNotContain("owned-sensitive-marker", Get<Label>(section, "retryStatusLabel").text);
                failed.ConfigureFails = false;
                Invoke(section, "OnConfigureClicked");
                Assert.IsFalse(Get<Button>(section, "retryFailedButton").enabledSelf);
                Invoke(section, "OnRetryFailedClientsClicked");
                Assert.AreEqual(3, failed.ConfigureCalls);
            }
            finally
            {
                slot.SetValue(null, original);
            }
        }

        [Test]
        public void RetryUi_LegacyServiceExplicitlyReportsSelectiveRetryUnavailable()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            try
            {
                slot.SetValue(null, new MemoryClientService { AllowBulk = true });
                var section = CreateSection(new ManualClient());
                InitializeRetryUi(section);
                Invoke(section, "OnConfigureAllClientsClicked");
                Assert.IsFalse(Get<Button>(section, "retryFailedButton").enabledSelf);
                StringAssert.Contains("unavailable for this client service", Get<Label>(section, "retryStatusLabel").text.ToLowerInvariant());
            }
            finally
            {
                slot.SetValue(null, original);
            }
        }

        private static void InitializeRetryUi(McpClientConfigSection section)
        {
            var presenter = typeof(McpClientConfigSection).GetField("showConfigurationResult", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(presenter, "Bulk/retry flow requires an owned result presenter before any modal-producing handler is invoked");
            presenter.SetValue(section, new Action<string, string>((_, __) => { }));
            Set(section, "configureAllButton", new Button());
            Set(section, "retryFailedButton", new Button());
            Set(section, "retryStatusLabel", new Label());
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void ReviewedApplyAndRestoreRefreshSelectedClientWithoutRewrite(string format)
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = slot.GetValue(null);
            string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-reviewed-status-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(directory);
            try
            {
                var service = new MemoryClientService();
                slot.SetValue(null, service);
                var client = new ManualClient();
                string path = System.IO.Path.Combine(directory, "synthetic-config");
                client.ConfigPath = path;
                System.IO.File.WriteAllText(path, MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Document(format, false));
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, format);
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, format);
                service.OnStatusCheck = _ =>
                    client.CurrentStatus = System.IO.File.ReadAllText(path).Contains("unityMCP") ? McpStatus.Configured : McpStatus.NotConfigured;
                var section = CreateSection(client);
                InitializeRetryUi(section);
                InitializePreviewUi(section, harness.Service);
                section.RefreshSelectedClient();
                Assert.AreEqual("Not Configured", Get<Label>(section, "clientStatusLabel").text);
                Invoke(section, "OnPreviewConfigurationClicked");
                Invoke(section, "OnApplyConfigurationPreviewClicked");
                Assert.AreEqual(2, service.StatusChecks, "Apply must refresh even within the status TTL.");
                Assert.AreEqual("Configured", Get<Label>(section, "clientStatusLabel").text);
                Assert.AreEqual("Unregister", Get<Button>(section, "configureButton").text);
                Assert.IsTrue(Get<VisualElement>(section, "clientStatusIndicator").ClassListContains("configured"));
                Invoke(section, "OnRestoreConfigurationClicked");
                Assert.AreEqual(3, service.StatusChecks);
                Assert.AreEqual("Not Configured", Get<Label>(section, "clientStatusLabel").text);
                Assert.AreEqual("Configure", Get<Button>(section, "configureButton").text);
                Assert.IsFalse(service.RequestedRewrite);
                Assert.Zero(client.ConfigureCalls);
            }
            finally
            {
                slot.SetValue(null, previous);
                System.IO.Directory.Delete(directory, true);
            }
        }

        [Test]
        public void RejectedRestoreKeepsReviewReceiptAndCurrentStatus()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = slot.GetValue(null);
            string directory = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-reviewed-status-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(directory);
            try
            {
                var service = new MemoryClientService();
                slot.SetValue(null, service);
                string path = System.IO.Path.Combine(directory, "synthetic-config");
                var client = new ManualClient { ConfigPath = path };
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, "json");
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, "json");
                var section = CreateSection(client);
                InitializeRetryUi(section);
                InitializePreviewUi(section, harness.Service);
                Invoke(section, "OnPreviewConfigurationClicked");
                Invoke(section, "OnApplyConfigurationPreviewClicked");
                object receipt = Get<object>(section, "configurationReceipt");
                System.IO.File.WriteAllText(path, MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Document("json"));
                Invoke(section, "OnPreviewConfigurationClicked");
                object preview = Get<object>(section, "configurationPreview");
                string review = Get<TextField>(section, "configurationPreviewText").value;
                int checks = service.StatusChecks;
                Invoke(section, "OnRestoreConfigurationClicked");
                Assert.AreSame(receipt, Get<object>(section, "configurationReceipt"));
                Assert.AreSame(preview, Get<object>(section, "configurationPreview"));
                Assert.AreEqual(review, Get<TextField>(section, "configurationPreviewText").value);
                Assert.AreEqual(checks, service.StatusChecks, "Rejected Restore must not refresh or rewrite a client.");
                Assert.IsTrue(Get<Button>(section, "restoreConfigurationButton").enabledSelf);
                StringAssert.Contains("changed after saving", Get<Label>(section, "configurationChangeStatusLabel").text);
            }
            finally
            {
                slot.SetValue(null, previous);
                System.IO.Directory.Delete(directory, true);
            }
        }

        [TestCase("json")]
        [TestCase("toml")]
        public void PreviewFlow_CancelPreservesRetryWhileApplyAndRestoreInvalidateIt(string format)
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            string root = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-preview-ui-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(root);
            try
            {
                var client = new ManualClient { AllowConfigure = true, ConfigureFails = true };
                bool http = true;
                var clientService = new ClientConfigurationService(new[] { client }, () => http, v => http = v, () => { }, _ => { });
                slot.SetValue(null, clientService);
                var retry = clientService.ConfigureAllDetectedClients().Attempt;
                var path = System.IO.Path.Combine(root, "config");
                System.IO.File.WriteAllText(path, MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Document(format));
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, format);
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, format);
                var section = CreateSection(client);
                InitializeRetryUi(section);
                Set(section, "retryAttempt", retry);
                Get<Button>(section, "retryFailedButton").SetEnabled(true);
                InitializePreviewUi(section, harness.Service);
                Invoke(section, "OnPreviewConfigurationClicked");
                Assert.AreSame(retry, Get<object>(section, "retryAttempt"));
                Assert.IsTrue(Get<Button>(section, "applyConfigurationButton").enabledSelf);
                StringAssert.DoesNotContain("synthetic-sensitive", Get<TextField>(section, "configurationPreviewText").value);
                Invoke(section, "OnCancelConfigurationPreviewClicked");
                Assert.AreSame(retry, Get<object>(section, "retryAttempt"));
                Assert.IsFalse(Get<Button>(section, "applyConfigurationButton").enabledSelf);
                Invoke(section, "OnPreviewConfigurationClicked");
                var prepared = Get<object>(section, "configurationPreview");
                section.RefreshSelectedClient();
                Assert.AreSame(prepared, Get<object>(section, "configurationPreview"), "Focus must preserve a prepared review");
                Assert.IsTrue(Get<Button>(section, "applyConfigurationButton").enabledSelf);
                Invoke(section, "OnApplyConfigurationPreviewClicked");
                Assert.IsFalse(Get<Button>(section, "retryFailedButton").enabledSelf);
                Assert.IsTrue(Get<Button>(section, "restoreConfigurationButton").enabledSelf);
                Assert.AreEqual(1, client.ConfigureCalls, "Apply must commit the reviewed file rather than call Configure again");
                var nextRetry = clientService.ConfigureAllDetectedClients().Attempt;
                Set(section, "retryAttempt", nextRetry);
                Get<Button>(section, "retryFailedButton").SetEnabled(true);
                MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.AddOtherServerEdit(path, format);
                Invoke(section, "OnRestoreConfigurationClicked");
                Assert.IsFalse(Get<Button>(section, "retryFailedButton").enabledSelf);
                StringAssert.Contains("original-owned-endpoint", System.IO.File.ReadAllText(path));
                StringAssert.Contains("later-user-edit", System.IO.File.ReadAllText(path));
                Assert.AreEqual(2, client.ConfigureCalls);
                Assert.IsTrue(http);
            }
            finally
            {
                slot.SetValue(null, original);
                Assert.IsTrue(System.IO.Path.GetFileName(root).StartsWith("unity-mcp-preview-ui-", StringComparison.Ordinal));
                System.IO.Directory.Delete(root, true);
            }
        }

        [Test]
        public void PreviewFlow_StaleApplyNeverFallsBackToConfigure()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            string root = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-preview-ui-" + Guid.NewGuid().ToString("N"));
            System.IO.Directory.CreateDirectory(root);
            try
            {
                slot.SetValue(null, new MemoryClientService());
                var path = System.IO.Path.Combine(root, "config");
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, "json");
                var client = new ManualClient();
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, "json");
                var section = CreateSection(client);
                InitializeRetryUi(section);
                InitializePreviewUi(section, harness.Service);
                Invoke(section, "OnPreviewConfigurationClicked");
                System.IO.File.WriteAllText(path, "{\"user\":\"owned-later-edit\"}");
                section.RefreshSelectedClient();
                string reviewed = Get<TextField>(section, "configurationPreviewText").value;
                object preview = Get<object>(section, "configurationPreview");
                Invoke(section, "OnApplyConfigurationPreviewClicked");
                Assert.AreEqual("{\"user\":\"owned-later-edit\"}", System.IO.File.ReadAllText(path));
                Assert.AreEqual(0, client.ConfigureCalls);
                Assert.IsFalse(Get<Button>(section, "applyConfigurationButton").enabledSelf);
                Assert.AreEqual(reviewed, Get<TextField>(section, "configurationPreviewText").value, "A rejected change must keep its review visible.");
                Assert.AreSame(preview, Get<object>(section, "configurationPreview"));
                StringAssert.Contains("configuration file changed", Get<Label>(section, "configurationChangeStatusLabel").text);
                StringAssert.Contains("Preview again", Get<Label>(section, "configurationChangeStatusLabel").text);
            }
            finally
            {
                slot.SetValue(null, original);
                Assert.IsTrue(System.IO.Path.GetFileName(root).StartsWith("unity-mcp-preview-ui-", StringComparison.Ordinal));
                System.IO.Directory.Delete(root, true);
            }
        }

        [Test]
        public void FocusRefreshReusesStatusAndNeverRequestsConfigurationWrites()
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            try
            {
                var service = new MemoryClientService();
                slot.SetValue(null, service);
                var section = CreateSection(new ManualClient());
                section.RefreshSelectedClient();
                section.RefreshSelectedClient();
                Assert.AreEqual(1, service.StatusChecks);
                Assert.IsFalse(service.RequestedRewrite);
                section.RefreshSelectedClient(forceImmediate: true);
                Assert.AreEqual(2, service.StatusChecks);
            }
            finally
            {
                slot.SetValue(null, original);
            }
        }

        [TestCase("json", "file", "configuration file changed")]
        [TestCase("toml", "file", "configuration file changed")]
        [TestCase("json", "context", "Connection settings changed")]
        [TestCase("toml", "context", "Connection settings changed")]
        [TestCase("json", "target", "destination changed")]
        [TestCase("toml", "target", "destination changed")]
        public void PreviewRefreshExplainsChangedInputsBeforeApply(string format, string changed, string expected)
        {
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            object previous = slot.GetValue(null);
            string path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "unity-mcp-preview-refresh-" + Guid.NewGuid().ToString("N"));
            try
            {
                var service = new MemoryClientService();
                slot.SetValue(null, service);
                System.IO.File.WriteAllText(path, MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Document(format));
                var client = new ManualClient { ConfigPath = path };
                var harness = new MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Harness(path, format);
                harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path, format);
                var section = CreateSection(client);
                InitializePreviewUi(section, harness.Service);
                Invoke(section, "OnPreviewConfigurationClicked");
                object preview = Get<object>(section, "configurationPreview");
                string review = Get<TextField>(section, "configurationPreviewText").value;
                section.RefreshSelectedClient(forceImmediate: true);
                Assert.IsTrue(Get<Button>(section, "applyConfigurationButton").enabledSelf, "Unchanged inputs must retain the prepared review.");

                if (changed == "file")
                    System.IO.File.AppendAllText(path, "\n ");
                else if (changed == "context")
                    harness.ChangeContext("https://example.invalid/changed");
                else
                    harness.Target.Value = MCPForUnityTests.Editor.Services.ClientConfigurationChangeTests.Target(client, path + ".next", format);
                string bytes = System.IO.File.ReadAllText(path);
                section.RefreshSelectedClient(forceImmediate: true);
                Assert.IsFalse(Get<Button>(section, "applyConfigurationButton").enabledSelf, "A stale review must explain its state before Apply is clicked.");
                Assert.AreSame(preview, Get<object>(section, "configurationPreview"));
                Assert.AreEqual(review, Get<TextField>(section, "configurationPreviewText").value);
                StringAssert.Contains(expected, Get<Label>(section, "configurationChangeStatusLabel").text);
                StringAssert.Contains("Preview again", Get<Label>(section, "configurationChangeStatusLabel").text);
                Assert.AreEqual(bytes, System.IO.File.ReadAllText(path));
                Assert.IsFalse(System.IO.File.Exists(path + ".next"));
                Assert.IsFalse(service.RequestedRewrite);
                Assert.Zero(client.ConfigureCalls);
            }
            finally
            {
                slot.SetValue(null, previous);
                System.IO.File.Delete(path);
            }
        }

        [Test]
        public void ChangedConfigFileInvalidatesStatusBeforeTtlExpires()
        {
            string path = System.IO.Path.GetTempFileName();
            var slot = typeof(MCPServiceLocator).GetField("_clientService", BindingFlags.Static | BindingFlags.NonPublic);
            var original = slot.GetValue(null);
            try
            {
                var service = new MemoryClientService();
                slot.SetValue(null, service);
                var section = CreateSection(new ManualClient { ConfigPath = path });
                section.RefreshSelectedClient();
                section.RefreshSelectedClient();
                Assert.AreEqual(1, service.StatusChecks);
                System.IO.File.WriteAllText(path, "changed");
                section.RefreshSelectedClient();
                Assert.AreEqual(2, service.StatusChecks);
                Assert.IsFalse(service.RequestedRewrite);
            }
            finally
            {
                slot.SetValue(null, original);
                System.IO.File.Delete(path);
            }
        }

        private static void InitializePreviewUi(McpClientConfigSection section, object service)
        {
            var field = typeof(McpClientConfigSection).GetField("configurationChanges", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(field, "Zero-write preview must have its own owned service; no Configure fallback is allowed");
            field.SetValue(section, service);
            Set(section, "previewConfigurationButton", new Button());
            Set(section, "applyConfigurationButton", new Button());
            Set(section, "restoreConfigurationButton", new Button());
            Set(section, "configurationPreviewText", new TextField());
            Set(section, "configurationChangeStatusLabel", new Label());
        }

        private static void Invoke(McpClientConfigSection section, string method)
        {
            var target = typeof(McpClientConfigSection).GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(target, "Selective retry must have its own action, without replaying configure-all");
            target.Invoke(section, null);
        }

        private static McpClientConfigSection CreateSection(IMcpClientConfigurator client)
        {
            // Bypass window initialization so these tests never resolve real clients or preferences.
            var section = (McpClientConfigSection)FormatterServices.GetUninitializedObject(typeof(McpClientConfigSection));
            Set(section, "configurators", new List<IMcpClientConfigurator> { client });
            Set(section, "claudeCliPathRow", new VisualElement());
            Set(section, "configPathField", new TextField());
            Set(section, "configJsonField", new TextField());
            Set(section, "installationStepsLabel", new Label());
            Set(section, "copyPathButton", new Button());
            Set(section, "openFileButton", new Button());
            Set(section, "copyJsonButton", new Button());
            Set(section, "configureButton", new Button());
            Set(section, "clientStatusIndicator", new VisualElement());
            Set(section, "clientStatusLabel", new Label());
            Set(section, "lastStatusChecks", new Dictionary<IMcpClientConfigurator, DateTime>());
            Set(section, "copyToClipboard", new Action<string>(_ => throw new InvalidOperationException("Unexpected clipboard write.")));
            return section;
        }

        private static T Get<T>(McpClientConfigSection section, string field) =>
            (T)typeof(McpClientConfigSection).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(section);

        private static void Set(McpClientConfigSection section, string field, object value) =>
            typeof(McpClientConfigSection).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(section, value);

        private sealed class ManualPaths : IPathResolverService
        {
            internal string Uvx;

            public string GetUvxPath() => Uvx;

            public string GetClaudeCliPath() => "synthetic-claude";

            public bool IsPythonDetected() => false;

            public bool IsClaudeCliDetected() => true;

            public bool HasUvxPathOverride => true;
            public bool HasClaudeCliPathOverride => true;
            public bool HasUvxPathFallback => false;

            public void SetUvxPathOverride(string path) => Uvx = path;

            public void SetClaudeCliPathOverride(string path) { }

            public void ClearUvxPathOverride() => Uvx = null;

            public void ClearClaudeCliPathOverride() { }

            public bool TryValidateUvxExecutable(string path, out string version)
            {
                version = null;
                return false;
            }

            public Task<string> ValidateUvxExecutableAsync(string path) => Task.FromResult<string>(null);
        }

        private sealed class MemoryCodexClient : CodexMcpConfigurator
        {
            public int ConfigureCount;

            public MemoryCodexClient(McpStatus status)
                : base(new McpClient { name = "Memory Codex" }) => client.SetStatus(status);

            public override string GetConfigPath() => "memory-config";

            public override string GetManualSnippet() => "memory-toml";

            public override McpStatus CheckStatus(bool attemptAutoRewrite = true) => Status;

            public override void Configure()
            {
                ConfigureCount++;
                client.SetStatus(McpStatus.Configured);
            }
        }

        private sealed class MemoryClientService : IClientConfigurationService
        {
            public bool AllowBulk;
            public int StatusChecks;
            public bool RequestedRewrite;
            public Action<IMcpClientConfigurator> OnStatusCheck;

            public void ConfigureClient(IMcpClientConfigurator configurator) => configurator.Configure();

            public bool CheckClientStatus(IMcpClientConfigurator configurator, bool attemptAutoRewrite = true)
            {
                StatusChecks++;
                RequestedRewrite |= attemptAutoRewrite;
                OnStatusCheck?.Invoke(configurator);
                return false;
            }

            public IReadOnlyList<IMcpClientConfigurator> GetAllClients() => Array.Empty<IMcpClientConfigurator>();

            public ClientConfigurationSummary ConfigureAllDetectedClients() =>
                AllowBulk ? new ClientConfigurationSummary() : throw new InvalidOperationException("Bulk configuration is forbidden in this test.");
        }

        private sealed class ManualClient : IMcpClientConfigurator
        {
            public string FailureStage;
            public string ConfigPath = "memory-config";
            public string Snippet = "memory-json";
            public string ClientId = "memory-client";
            public bool AllowConfigure;
            public bool ConfigureFails;
            public int ConfigureCalls;
            public int StatusCalls;
            public McpStatus CurrentStatus = McpStatus.NotConfigured;
            public IList<string> Steps = new[] { "Install", "Configure" };
            public string Id => ClientId;
            public string DisplayName => "Memory Client";
            public McpStatus Status => CurrentStatus;
            public ConfiguredTransport ConfiguredTransport => ConfiguredTransport.Unknown;
            public bool IsInstalled => true;
            public bool SupportsAutoConfigure => true;
            public bool SupportsSkills => false;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports => new[] { ConfiguredTransport.Http };

            public string GetConfigureActionLabel() => Status == McpStatus.Configured ? "Unregister" : "Configure";

            public string GetSkillInstallPath() => null;

            public string GetConfigPath()
            {
                ThrowIf("path");
                return ConfigPath;
            }

            public string GetManualSnippet()
            {
                ThrowIf("snippet");
                return Snippet;
            }

            public IList<string> GetInstallationSteps()
            {
                ThrowIf("steps");
                return Steps;
            }

            public McpStatus CheckStatus(bool attemptAutoRewrite = true)
            {
                StatusCalls++;
                return Status;
            }

            public void Configure()
            {
                ConfigureCalls++;
                if (AllowConfigure && !ConfigureFails)
                    return;
                if (ConfigureFails)
                    throw new InvalidOperationException("owned-sensitive-marker");
                throw new InvalidOperationException("Real configuration is forbidden in this test.");
            }

            public void Unregister()
            {
                throw new InvalidOperationException("Real configuration is forbidden in this test.");
            }

            private void ThrowIf(string stage)
            {
                if (FailureStage == stage)
                    throw new InvalidOperationException("synthetic configuration error");
            }
        }
    }
}
