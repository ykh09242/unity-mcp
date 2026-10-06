using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.Serialization;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Windows.Components.ClientConfig;
using NUnit.Framework;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
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
            Assert.AreEqual("Configuration steps not available for this client.",
                Get<Label>(section, "installationStepsLabel").text);
            Assert.IsTrue(Get<Button>(section, "copyJsonButton").enabledSelf);
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
            return section;
        }

        private static T Get<T>(McpClientConfigSection section, string field)
            => (T)typeof(McpClientConfigSection).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(section);

        private static void Set(McpClientConfigSection section, string field, object value)
            => typeof(McpClientConfigSection).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(section, value);

        private sealed class ManualClient : IMcpClientConfigurator
        {
            public string FailureStage;
            public IList<string> Steps = new[] { "Install", "Configure" };
            public string Id => "memory-client";
            public string DisplayName => "Memory Client";
            public McpStatus Status => McpStatus.NotConfigured;
            public ConfiguredTransport ConfiguredTransport => ConfiguredTransport.Unknown;
            public bool IsInstalled => true;
            public bool SupportsAutoConfigure => true;
            public bool SupportsSkills => false;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports => new[] { ConfiguredTransport.Http };
            public string GetConfigureActionLabel() => "Configure";
            public string GetSkillInstallPath() => null;
            public string GetConfigPath() { ThrowIf("path"); return "memory-config"; }
            public string GetManualSnippet() { ThrowIf("snippet"); return "memory-json"; }
            public IList<string> GetInstallationSteps() { ThrowIf("steps"); return Steps; }
            public McpStatus CheckStatus(bool attemptAutoRewrite = true) => Status;
            public void Configure() { throw new InvalidOperationException("Real configuration is forbidden in this test."); }
            public void Unregister() { throw new InvalidOperationException("Real configuration is forbidden in this test."); }
            private void ThrowIf(string stage)
            {
                if (FailureStage == stage) throw new InvalidOperationException("synthetic configuration error");
            }
        }
    }
}
