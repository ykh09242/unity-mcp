using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using NUnit.Framework;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class ClientConfigSectionTests
    {
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
            Assert.AreEqual(string.Empty, Get<TextField>(section, "configPathField").value);
            Assert.AreEqual(string.Empty, Get<TextField>(section, "configJsonField").value);
            StringAssert.Contains("synthetic configuration error", Get<Label>(section, "installationStepsLabel").text);
            Assert.IsFalse(Get<Button>(section, "copyPathButton").enabledSelf);
            Assert.IsFalse(Get<Button>(section, "openFileButton").enabledSelf);
            Assert.IsFalse(Get<Button>(section, "copyJsonButton").enabledSelf);

            client.FailureStage = null;
            section.UpdateManualConfiguration();
            Assert.AreEqual("memory-config", Get<TextField>(section, "configPathField").value);
            Assert.AreEqual("memory-json", Get<TextField>(section, "configJsonField").value);
            Assert.AreEqual("1. Install\n2. Configure", Get<Label>(section, "installationStepsLabel").text);
            Assert.IsTrue(Get<Button>(section, "copyPathButton").enabledSelf);
            Assert.IsTrue(Get<Button>(section, "openFileButton").enabledSelf);
            Assert.IsTrue(Get<Button>(section, "copyJsonButton").enabledSelf);
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
                var section = CreateSection(harness.Client);
                InitializeRetryUi(section);
                InitializePreviewUi(section, harness.Service);
                Invoke(section, "OnPreviewConfigurationClicked");
                System.IO.File.WriteAllText(path, "{\"user\":\"owned-later-edit\"}");
                Invoke(section, "OnApplyConfigurationPreviewClicked");
                Assert.AreEqual("{\"user\":\"owned-later-edit\"}", System.IO.File.ReadAllText(path));
                Assert.AreEqual(0, harness.Client.ForbiddenCalls);
                Assert.IsFalse(Get<Button>(section, "applyConfigurationButton").enabledSelf);
                StringAssert.Contains("stale_file", Get<Label>(section, "configurationChangeStatusLabel").text);
            }
            finally
            {
                slot.SetValue(null, original);
                Assert.IsTrue(System.IO.Path.GetFileName(root).StartsWith("unity-mcp-preview-ui-", StringComparison.Ordinal));
                System.IO.Directory.Delete(root, true);
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
            return section;
        }

        private static T Get<T>(McpClientConfigSection section, string field) =>
            (T)typeof(McpClientConfigSection).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(section);

        private static void Set(McpClientConfigSection section, string field, object value) =>
            typeof(McpClientConfigSection).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(section, value);

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

            public void ConfigureClient(IMcpClientConfigurator configurator) => configurator.Configure();

            public bool CheckClientStatus(IMcpClientConfigurator configurator, bool attemptAutoRewrite = true) => false;

            public IReadOnlyList<IMcpClientConfigurator> GetAllClients() => Array.Empty<IMcpClientConfigurator>();

            public ClientConfigurationSummary ConfigureAllDetectedClients() =>
                AllowBulk ? new ClientConfigurationSummary() : throw new InvalidOperationException("Bulk configuration is forbidden in this test.");
        }

        private sealed class ManualClient : IMcpClientConfigurator
        {
            public string FailureStage;
            public string ClientId = "memory-client";
            public bool AllowConfigure;
            public bool ConfigureFails;
            public int ConfigureCalls;
            public int StatusCalls;
            public IList<string> Steps = new[] { "Install", "Configure" };
            public string Id => ClientId;
            public string DisplayName => "Memory Client";
            public McpStatus Status => McpStatus.NotConfigured;
            public ConfiguredTransport ConfiguredTransport => ConfiguredTransport.Unknown;
            public bool IsInstalled => true;
            public bool SupportsAutoConfigure => true;
            public bool SupportsSkills => false;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports => new[] { ConfiguredTransport.Http };

            public string GetConfigureActionLabel() => "Configure";

            public string GetSkillInstallPath() => null;

            public string GetConfigPath()
            {
                ThrowIf("path");
                return "memory-config";
            }

            public string GetManualSnippet()
            {
                ThrowIf("snippet");
                return "memory-json";
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
