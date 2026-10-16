using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class ConfigureDetectedClientsTests
    {
        private TemporaryStore store;
        private int preparations;
        private List<string> transportMessages;

        [SetUp]
        public void SetUp()
        {
            store = new TemporaryStore();
            preparations = 0;
            transportMessages = new List<string>();
        }

        [TearDown]
        public void TearDown() => store.Dispose();

        [Test]
        public void Summary_AggregatesFailuresAndContinuesWithSuccessfulPeers()
        {
            var first = new FakeClient("first", store);
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var last = new FakeClient("last", store);
            var skipped = new FakeClient("skipped", store) { IsInstalled = false };
            var service = CreateService(first, failed, last, skipped);

            var summary = service.ConfigureAllDetectedClients();

            Assert.AreEqual(2, summary.SuccessCount);
            Assert.AreEqual(1, summary.FailureCount);
            Assert.AreEqual(1, summary.SkippedCount);
            Assert.AreEqual(3, summary.Messages.Count);
            Assert.IsTrue(summary.Messages.Any(message => message.Contains("failed")));
            Assert.AreEqual(1, first.ConfigureCalls);
            Assert.AreEqual(1, last.ConfigureCalls);
            Assert.AreEqual(0, skipped.StatusCalls);
            Assert.AreEqual(0, skipped.ConfigureCalls);
            Assert.IsFalse(first.LastAutoRewrite);
            Assert.IsFalse(failed.LastAutoRewrite);
            Assert.AreEqual(1, preparations);
            CollectionAssert.AreEquivalent(new[] { "first.config", "last.config" }, Directory.GetFiles(store.Root).Select(Path.GetFileName));
        }

        [Test]
        public void StatusFailure_DoesNotConfigureFailedClientOrPreventPeers()
        {
            var failed = new FakeClient("failed", store) { FailStatus = true };
            var peer = new FakeClient("peer", store);
            var summary = CreateService(failed, peer).ConfigureAllDetectedClients();

            Assert.AreEqual(1, summary.FailureCount);
            Assert.AreEqual(1, summary.SuccessCount);
            Assert.AreEqual(0, failed.ConfigureCalls);
            Assert.AreEqual(1, peer.ConfigureCalls);
            Assert.IsTrue(store.UseHttp);
        }

        [Test]
        public void RepeatedCalls_RefreshFakeFilesAndRestoreChangedTransportPreference()
        {
            var flexible = new FakeClient("flexible", store);
            var stdio = new FakeClient("stdio", store) { SupportedTransports = new[] { ConfiguredTransport.Stdio } };
            var service = CreateService(stdio, flexible);

            Assert.AreEqual(2, service.ConfigureAllDetectedClients().SuccessCount);
            Assert.IsTrue(store.UseHttp);
            Assert.AreEqual("http", File.ReadAllText(flexible.GetConfigPath()));
            store.UseHttp = false;
            Assert.AreEqual(2, service.ConfigureAllDetectedClients().SuccessCount);

            Assert.IsFalse(store.UseHttp);
            Assert.AreEqual("stdio", File.ReadAllText(flexible.GetConfigPath()));
            CollectionAssert.AreEqual(new[] { true, false }, flexible.ObservedHttp);
            CollectionAssert.AreEqual(new[] { false, false }, stdio.ObservedHttp);
            Assert.AreEqual(2, flexible.StatusCalls);
            Assert.AreEqual(2, flexible.ConfigureCalls);
            Assert.AreEqual(2, preparations);
        }

        [TestCase(true, false)]
        [TestCase(true, true)]
        [TestCase(false, false)]
        [TestCase(false, true)]
        public void ConfigureClient_RestoresPreferenceAfterSuccessOrFailure(bool requestedHttp, bool fails)
        {
            store.UseHttp = requestedHttp;
            var client = new FakeClient("coerced", store)
            {
                FailConfigure = fails,
                SupportedTransports = new[] { requestedHttp ? ConfiguredTransport.Stdio : ConfiguredTransport.HttpRemote },
            };
            var service = CreateService(client);

            if (fails)
                Assert.Throws<IOException>(() => service.ConfigureClient(client));
            else
                service.ConfigureClient(client);

            CollectionAssert.AreEqual(new[] { !requestedHttp }, client.ObservedHttp);
            Assert.AreEqual(requestedHttp, store.UseHttp);
            Assert.AreEqual(1, preparations);
            Assert.AreEqual(1, transportMessages.Count);
        }

        [Test]
        public void InjectedBoundary_UsesOnlyOwnedClientsAndTemporaryStore()
        {
            var client = new FakeClient("owned", store);
            var supplied = new List<IMcpClientConfigurator> { client };
            var service = new ClientConfigurationService(
                supplied,
                () => store.UseHttp,
                value => store.UseHttp = value,
                () => preparations++,
                transportMessages.Add
            );
            supplied.Clear();

            CollectionAssert.AreEqual(new[] { client }, service.GetAllClients());
            Assert.AreEqual(1, service.ConfigureAllDetectedClients().SuccessCount);
            Assert.Greater(store.Reads, 0);
            Assert.Greater(store.Writes, 0);
            Assert.AreEqual(Path.Combine(store.Root, "owned.config"), client.GetConfigPath());
            Assert.AreEqual(0, client.ForbiddenCalls);
            Assert.AreEqual(1, preparations);
        }

        private ClientConfigurationService CreateService(params FakeClient[] clients) =>
            new ClientConfigurationService(clients, () => store.UseHttp, value => store.UseHttp = value, () => preparations++, transportMessages.Add);

        private sealed class TemporaryStore : IDisposable
        {
            private bool useHttp = true;
            public string Root { get; } = Path.Combine(Path.GetTempPath(), "unity-mcp-fake-clients-" + Guid.NewGuid().ToString("N"));
            public int Reads { get; private set; }
            public int Writes { get; private set; }
            public bool UseHttp
            {
                get
                {
                    Reads++;
                    return useHttp;
                }
                set
                {
                    Writes++;
                    useHttp = value;
                }
            }

            public TemporaryStore() => Directory.CreateDirectory(Root);

            public void Dispose() => Directory.Delete(Root, recursive: true);
        }

        private sealed class FakeClient : IMcpClientConfigurator
        {
            private readonly TemporaryStore store;
            public string Id { get; }
            public string DisplayName => Id;
            public McpStatus Status => McpStatus.NotConfigured;
            public ConfiguredTransport ConfiguredTransport => ConfiguredTransport.Unknown;
            public bool SupportsAutoConfigure => true;
            public bool SupportsSkills => false;
            public bool IsInstalled { get; set; } = true;
            public IReadOnlyList<ConfiguredTransport> SupportedTransports { get; set; } = new[] { ConfiguredTransport.Http, ConfiguredTransport.Stdio };
            public bool FailConfigure { get; set; }
            public bool FailStatus { get; set; }
            public int StatusCalls { get; private set; }
            public int ConfigureCalls { get; private set; }
            public int ForbiddenCalls { get; private set; }
            public bool LastAutoRewrite { get; private set; }
            public List<bool> ObservedHttp { get; } = new List<bool>();

            public FakeClient(string id, TemporaryStore store)
            {
                Id = id;
                this.store = store;
            }

            public McpStatus CheckStatus(bool attemptAutoRewrite = true)
            {
                StatusCalls++;
                LastAutoRewrite = attemptAutoRewrite;
                if (FailStatus)
                    throw new IOException("Synthetic status failure");
                return Status;
            }

            public void Configure()
            {
                ConfigureCalls++;
                bool http = store.UseHttp;
                ObservedHttp.Add(http);
                if (FailConfigure)
                    throw new IOException("Synthetic configuration failure");
                File.WriteAllText(GetConfigPath(), http ? "http" : "stdio");
            }

            public string GetConfigPath() => Path.Combine(store.Root, Id + ".config");

            public string GetConfigureActionLabel() => "Configure";

            public string GetManualSnippet() => throw Forbidden();

            public string GetSkillInstallPath() => throw Forbidden();

            public IList<string> GetInstallationSteps() => throw Forbidden();

            public void Unregister() => throw Forbidden();

            private InvalidOperationException Forbidden()
            {
                ForbiddenCalls++;
                return new InvalidOperationException("Only injected temporary configuration is permitted");
            }
        }
    }
}
