using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
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

        [Test]
        public void RetryFailed_OnlyRetriesFailureAndPreservesSuccessfulEdits()
        {
            var first = new FakeClient("first", store);
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var last = new FakeClient("last", store);
            var service = CreateService(first, failed, last);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            File.WriteAllText(first.GetConfigPath(), "user-edited-first");
            File.WriteAllText(last.GetConfigPath(), "user-edited-last");
            failed.FailConfigure = false;

            var result = Retry(service, attempt);

            Assert.AreEqual("completed", Read<string>(result, "Disposition"));
            Assert.AreEqual(1, Read<ClientConfigurationSummary>(result, "Summary").SuccessCount);
            Assert.IsFalse(Read<bool>(Read<object>(result, "Attempt"), "CanRetry"));
            Assert.AreEqual(1, first.ConfigureCalls);
            Assert.AreEqual(1, first.StatusCalls);
            Assert.AreEqual(1, last.ConfigureCalls);
            Assert.AreEqual(1, last.StatusCalls);
            Assert.AreEqual(2, failed.ConfigureCalls);
            Assert.AreEqual("user-edited-first", File.ReadAllText(first.GetConfigPath()));
            Assert.AreEqual("user-edited-last", File.ReadAllText(last.GetConfigPath()));
            Assert.AreEqual(2, preparations);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RetryFailed_UsesAndRestoresCurrentTransportEvenOnFailure(bool failsAgain)
        {
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var service = CreateService(failed);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            store.UseHttp = false;
            failed.FailConfigure = failsAgain;
            var result = Retry(service, attempt);
            Assert.IsFalse(store.UseHttp);
            CollectionAssert.AreEqual(new[] { true, false }, failed.ObservedHttp);
            Assert.AreEqual(failsAgain ? 1 : 0, Read<int>(Read<object>(result, "Attempt"), "RemainingFailureCount"));
        }

        [Test]
        public void RetryFailed_UsesCurrentContextAndPreservesCoercedPreference()
        {
            var failed = new FakeClient("failed", store) { FailConfigure = true, SupportedTransports = new[] { ConfiguredTransport.Stdio } };
            var service = CreateService(failed);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            store.Context = "new-owned-context";
            failed.FailConfigure = false;
            Retry(service, attempt);
            Assert.IsTrue(store.UseHttp);
            CollectionAssert.AreEqual(new[] { false, false }, failed.ObservedHttp);
            CollectionAssert.AreEqual(new[] { "initial-owned-context", "new-owned-context" }, failed.ObservedContexts);
        }

        [Test]
        public void RetryFailed_PartialWriteAndUnrelatedUserFieldSurviveExplicitRetry()
        {
            var failed = new FakeClient("failed", store) { FailConfigure = true, MergeOwnedFields = true };
            var service = CreateService(failed);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            Assert.IsTrue(File.Exists(failed.GetConfigPath()), "A failed configure call does not imply rollback");
            File.AppendAllText(failed.GetConfigPath(), "\nuser=preserved");
            failed.FailConfigure = false;
            Retry(service, attempt);
            StringAssert.Contains("user=preserved", File.ReadAllText(failed.GetConfigPath()));
            Assert.AreEqual(2, failed.ConfigureCalls);
        }

        [Test]
        public void RetryFailed_EmptySetDoesNotPrepareOrConfigureAgain()
        {
            var client = new FakeClient("success", store);
            var service = CreateService(client);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            Assert.AreEqual("no_failures", Read<string>(Retry(service, attempt), "Disposition"));
            Assert.AreEqual(1, preparations);
            Assert.AreEqual(1, client.ConfigureCalls);
            Assert.AreEqual(1, client.StatusCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ReportingFailure_AfterNormalConfigureNeverRetainsSuccessfulTarget(bool duringRetry)
        {
            var client = new FakeClient("display-failure", store) { FailConfigure = duringRetry };
            var service = CreateService(client);
            object previous = duringRetry ? Attempt(service.ConfigureAllDetectedClients()) : null;
            client.FailConfigure = false;
            client.FailDisplay = true;
            ClientConfigurationSummary summary = null;
            Assert.DoesNotThrow(
                () => summary = duringRetry ? Read<ClientConfigurationSummary>(Retry(service, previous), "Summary") : service.ConfigureAllDetectedClients(),
                "Reporting must not turn a normally returned Configure into a new failure"
            );
            Assert.AreEqual(1, summary.SuccessCount);
            Assert.AreEqual(0, summary.FailureCount);
            var attempt = Attempt(summary);
            Assert.AreEqual(0, Read<int>(attempt, "RemainingFailureCount"));
            int calls = client.ConfigureCalls;
            Assert.AreEqual("no_failures", Read<string>(Retry(service, attempt), "Disposition"));
            Assert.AreEqual(calls, client.ConfigureCalls);
        }

        [Test]
        public void RetryFailed_StatusFailureAndUnavailableTargetRemainIsolated()
        {
            var unavailable = new FakeClient("unavailable", store) { FailConfigure = true };
            var status = new FakeClient("status", store) { FailStatus = true };
            var service = CreateService(unavailable, status);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            unavailable.IsInstalled = false;
            status.FailStatus = false;
            var result = Retry(service, attempt);
            var summary = Read<ClientConfigurationSummary>(result, "Summary");
            Assert.AreEqual(1, summary.SuccessCount);
            Assert.AreEqual(1, summary.SkippedCount);
            Assert.AreEqual(1, Read<int>(Read<object>(result, "Attempt"), "RemainingFailureCount"));
            Assert.AreEqual(1, unavailable.ConfigureCalls);
            Assert.AreEqual(1, unavailable.StatusCalls);
            Assert.IsTrue(Read<bool>(Read<object>(result, "Attempt"), "CanRetry"));
        }

        [Test]
        public void DetectionFailure_DoesNotDiscardEarlierResultsOrPreventPeers()
        {
            var first = new FakeClient("first", store);
            var failed = new FakeClient("failed", store) { FailDetection = true };
            var last = new FakeClient("last", store);
            var summary = CreateService(first, failed, last).ConfigureAllDetectedClients();
            Assert.AreEqual(2, summary.SuccessCount);
            Assert.AreEqual(1, summary.FailureCount);
            Assert.AreEqual(0, failed.StatusCalls);
            Assert.AreEqual(1, last.ConfigureCalls);
        }

        [Test]
        public void RetryFailed_RejectsOldOwnerGenerationAndDiscardWithoutWork()
        {
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var service = CreateService(failed);
            var old = Attempt(service.ConfigureAllDetectedClients());
            var other = CreateService(failed);
            Assert.AreEqual("stale_attempt", Read<string>(Retry(other, old), "Disposition"));
            var current = Attempt(service.ConfigureAllDetectedClients());
            int before = preparations;
            Assert.AreEqual("stale_attempt", Read<string>(Retry(service, old), "Disposition"));
            typeof(ClientConfigurationService).GetMethod("DiscardConfigurationAttempt").Invoke(service, null);
            Assert.AreEqual("stale_attempt", Read<string>(Retry(service, current), "Disposition"));
            Assert.AreEqual(before, preparations);
            Assert.AreEqual(2, failed.ConfigureCalls);
        }

        [Test]
        public void RetryFailed_RejectsChangedCatalogWithoutRebindingSameId()
        {
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var service = CreateService(failed);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            var catalog = (List<IMcpClientConfigurator>)service.GetAllClients();
            catalog[0] = new FakeClient("failed", store);
            Assert.AreEqual("catalog_changed", Read<string>(Retry(service, attempt), "Disposition"));
            Assert.AreEqual(1, preparations);
            Assert.AreEqual(1, failed.ConfigureCalls);
        }

        [TestCase("duplicate")]
        [TestCase("empty")]
        [TestCase("long")]
        [TestCase("control")]
        [TestCase("throws")]
        public void RetryMetadata_InvalidIdsDoNotAbortLegacyBulk(string invalid)
        {
            var first = new FakeClient("first", store) { FailConfigure = true };
            var second = new FakeClient("second", store) { FailConfigure = true };
            switch (invalid)
            {
                case "duplicate":
                    second.Identity = "first";
                    break;
                case "empty":
                    second.Identity = "";
                    break;
                case "long":
                    second.Identity = new string('x', 129);
                    break;
                case "control":
                    second.Identity = "bad\nidentity";
                    break;
                case "throws":
                    second.FailIdentity = true;
                    break;
            }
            var summary = CreateService(first, second).ConfigureAllDetectedClients();
            Assert.AreEqual(2, summary.FailureCount);
            Assert.AreEqual(1, second.ConfigureCalls);
            Assert.IsFalse(Read<bool>(Attempt(summary), "CanRetry"));
        }

        [TestCase(128, true)]
        [TestCase(129, false)]
        public void RetryMetadata_FailureLimitDoesNotChangeBulkProcessing(int count, bool retryable)
        {
            var clients = Enumerable.Range(0, count).Select(i => new FakeClient("failed" + i, store) { FailConfigure = true }).ToArray();
            var summary = CreateService(clients).ConfigureAllDetectedClients();
            Assert.AreEqual(count, summary.FailureCount);
            Assert.AreEqual(retryable, Read<bool>(Attempt(summary), "CanRetry"));
            Assert.IsTrue(clients.All(c => c.ConfigureCalls == 1));
        }

        [TestCase(512, true)]
        [TestCase(513, false)]
        public void RetryMetadata_CatalogLimitIncludesUninstalledClients(int count, bool retryable)
        {
            var clients = Enumerable.Range(0, count).Select(i => new FakeClient("client" + i, store) { IsInstalled = i == 0, FailConfigure = true }).ToArray();
            var summary = CreateService(clients).ConfigureAllDetectedClients();
            Assert.AreEqual(1, summary.FailureCount);
            Assert.AreEqual(count - 1, summary.SkippedCount);
            Assert.AreEqual(retryable, Read<bool>(Attempt(summary), "CanRetry"));
        }

        [Test]
        public void RetryMetadata_Exact128CharacterIdIsAdmittedAndOldRetryTokenStaysStale()
        {
            var failed = new FakeClient(new string('x', 128), store) { FailConfigure = true };
            var service = CreateService(failed);
            var attempt = Attempt(service.ConfigureAllDetectedClients());
            Assert.IsTrue(Read<bool>(attempt, "CanRetry"));
            var next = Read<object>(Retry(service, attempt), "Attempt");
            Assert.IsTrue(Read<bool>(next, "CanRetry"));
            int before = preparations;
            Assert.AreEqual("stale_attempt", Read<string>(Retry(service, attempt), "Disposition"));
            Assert.AreEqual(before, preparations);
        }

        [Test]
        public void RetryPreparationFailure_ReissuesTokenButFreshBulkFailureClearsIt()
        {
            bool preparationFails = false;
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var service = new ClientConfigurationService(
                new[] { failed },
                () => store.UseHttp,
                v => store.UseHttp = v,
                () =>
                {
                    preparations++;
                    if (preparationFails)
                        throw new IOException("owned-marker");
                },
                transportMessages.Add
            );
            var old = Attempt(service.ConfigureAllDetectedClients());
            preparationFails = true;
            var result = Retry(service, old);
            Assert.AreEqual("preparation_failed", Read<string>(result, "Disposition"));
            var current = Read<object>(result, "Attempt");
            Assert.AreNotSame(old, current);
            Assert.IsTrue(Read<bool>(current, "CanRetry"));
            Assert.AreEqual("stale_attempt", Read<string>(Retry(service, old), "Disposition"));
            Assert.Throws<IOException>(() => service.ConfigureAllDetectedClients());
            Assert.AreEqual("stale_attempt", Read<string>(Retry(service, current), "Disposition"));
            Assert.AreEqual(1, failed.ConfigureCalls);
        }

        [Test]
        public void RetrySnapshot_IsImmutableAndDoesNotExposeExceptionMessage()
        {
            var failed = new FakeClient("failed", store) { FailConfigure = true };
            var service = CreateService(failed);
            var summary = service.ConfigureAllDetectedClients();
            var attempt = Attempt(summary);
            summary.Messages.Clear();
            Assert.AreEqual(1, Read<int>(attempt, "RemainingFailureCount"));
            var rows = (System.Collections.IEnumerable)attempt.GetType().GetProperty("Failures").GetValue(attempt);
            foreach (var row in rows)
            {
                StringAssert.DoesNotContain("owned-sensitive-marker", Read<string>(row, "FailureCode"));
                Assert.LessOrEqual(Read<string>(row, "FailureCode").Length, 80);
            }
        }

        private static object Attempt(ClientConfigurationSummary summary)
        {
            var property = summary.GetType().GetProperty("Attempt");
            Assert.IsNotNull(property, "Bulk result must carry its failed-target snapshot; rerunning all clients cannot preserve successful edits");
            var attempt = property.GetValue(summary);
            Assert.IsNotNull(attempt);
            return attempt;
        }

        private static object Retry(ClientConfigurationService service, object attempt)
        {
            var method = typeof(ClientConfigurationService).GetMethod("RetryFailedClients");
            Assert.IsNotNull(method, "Selective retry must not fall back to ConfigureAllDetectedClients");
            return method.Invoke(service, new[] { attempt });
        }

        private static T Read<T>(object target, string property) => (T)target.GetType().GetProperty(property).GetValue(target);

        private ClientConfigurationService CreateService(params FakeClient[] clients) =>
            new ClientConfigurationService(clients, () => store.UseHttp, value => store.UseHttp = value, () => preparations++, transportMessages.Add);

        private sealed class TemporaryStore : IDisposable
        {
            private bool useHttp = true;
            public string Root { get; } = Path.Combine(Path.GetTempPath(), "unity-mcp-fake-clients-" + Guid.NewGuid().ToString("N"));
            public string Context = "initial-owned-context";
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
            public string Identity { get; set; }
            public bool FailIdentity { get; set; }
            public bool FailDisplay { get; set; }
            public string Id => FailIdentity ? throw new IOException("owned-sensitive-marker") : Identity;
            public string DisplayName => FailDisplay ? throw new IOException("owned-sensitive-display-marker") : Identity;
            public McpStatus Status => McpStatus.NotConfigured;
            public ConfiguredTransport ConfiguredTransport => ConfiguredTransport.Unknown;
            public bool SupportsAutoConfigure => true;
            public bool SupportsSkills => false;
            private bool installed = true;
            public bool FailDetection { get; set; }
            public bool IsInstalled
            {
                get => FailDetection ? throw new IOException("owned detection failure") : installed;
                set => installed = value;
            }
            public IReadOnlyList<ConfiguredTransport> SupportedTransports { get; set; } = new[] { ConfiguredTransport.Http, ConfiguredTransport.Stdio };
            public bool FailConfigure { get; set; }
            public bool FailStatus { get; set; }
            public bool MergeOwnedFields { get; set; }
            public int StatusCalls { get; private set; }
            public int ConfigureCalls { get; private set; }
            public int ForbiddenCalls { get; private set; }
            public bool LastAutoRewrite { get; private set; }
            public List<bool> ObservedHttp { get; } = new List<bool>();
            public List<string> ObservedContexts { get; } = new List<string>();

            public FakeClient(string id, TemporaryStore store)
            {
                Identity = id;
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
                ObservedContexts.Add(store.Context);
                if (MergeOwnedFields)
                {
                    var fields = File.Exists(GetConfigPath())
                        ? File.ReadAllLines(GetConfigPath()).Where(line => !line.StartsWith("owned="))
                        : Enumerable.Empty<string>();
                    File.WriteAllLines(GetConfigPath(), fields.Concat(new[] { "owned=" + (http ? "http" : "stdio") }));
                }
                if (FailConfigure)
                    throw new IOException("owned-sensitive-marker");
                if (!MergeOwnedFields)
                    File.WriteAllText(GetConfigPath(), http ? "http" : "stdio");
            }

            public string GetConfigPath() => Path.Combine(store.Root, Identity + ".config");

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
