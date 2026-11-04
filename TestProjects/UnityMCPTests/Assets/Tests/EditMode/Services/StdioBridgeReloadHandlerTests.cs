using System;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class StdioBridgeReloadHandlerTests : TransportPreferenceTestBase
    {
        private static readonly TimeSpan[] ZeroSchedule = { TimeSpan.Zero, TimeSpan.Zero, TimeSpan.Zero };
        private const string MigrationKey = StdioBridgeReloadHandler.ResumeSessionKey + ".Migrated";
        private TransportManager savedManager;
        private bool hadLegacy;
        private bool legacy;
        private bool migrated;
        private bool hadMigration;
        private bool ownsState;
        private bool savedExplicitStop;
        private HeldClient client;

        [SetUp]
        public void SetUp()
        {
            if (SessionState.GetBool(StdioBridgeReloadHandler.ResumeSessionKey, false))
                Assert.Ignore("An existing stdio recovery is pending; do not replace its services.");
            hadLegacy = EditorPrefs.HasKey(StdioBridgeReloadHandler.ResumeSessionKey);
            legacy = EditorPrefs.GetBool(StdioBridgeReloadHandler.ResumeSessionKey, false);
            migrated = SessionState.GetBool(MigrationKey, false);
            hadMigration = migrated == SessionState.GetBool(MigrationKey, true);
            savedManager = MCPServiceLocator.TransportManager;
            ownsState = true;
            // This fixture substitutes a transport with automatic-start intent. Its fake
            // start never clears the real host's intent, so isolate that static state too.
            var stopField = typeof(MCPForUnity.Editor.Services.Transport.Transports.StdioBridgeHost).GetField(
                "explicitlyStopped",
                BindingFlags.NonPublic | BindingFlags.Static
            );
            savedExplicitStop = (bool)stopField.GetValue(null);
            stopField.SetValue(null, false);
            StdioBridgeReloadHandler.CancelPendingResume();
            client = new HeldClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            MCPServiceLocator.Register(manager);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, false);
            EditorConfigurationCache.Instance.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            if (!ownsState)
                return;
            StdioBridgeReloadHandler.CancelPendingResume();
            client.Completion.TrySetResult(false);
            MCPServiceLocator.Register(savedManager);
            typeof(MCPForUnity.Editor.Services.Transport.Transports.StdioBridgeHost)
                .GetField("explicitlyStopped", BindingFlags.NonPublic | BindingFlags.Static)
                .SetValue(null, savedExplicitStop);
            if (hadLegacy)
                EditorPrefs.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, legacy);
            else
                EditorPrefs.DeleteKey(StdioBridgeReloadHandler.ResumeSessionKey);
            if (hadMigration)
                SessionState.SetBool(MigrationKey, migrated);
            else
                SessionState.EraseBool(MigrationKey);
            ownsState = false;
        }

        [Test]
        public void StopPendingStartPreventsNextRetry()
        {
            WithoutContext(() =>
            {
                // Given a real resume loop suspended in the transport's initial start.
                SessionState.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
                Task resume = StdioBridgeReloadHandler.ResumeStdioWithRetriesAsync(ZeroSchedule);
                Assert.AreEqual(1, client.Starts);
                Assert.IsFalse(resume.IsCompleted);
                // When the same bridge service used by End Session stops it before completion.
                Task stop = new BridgeControlService().StopAsync();
                Assert.IsTrue(stop.IsCompleted);
                Assert.GreaterOrEqual(client.Stops, 1);
                client.Completion.SetResult(false);
                // Then even zero retry delays cannot start the bridge again.
                Assert.IsTrue(resume.IsCompleted);
                resume.GetAwaiter().GetResult();
                Assert.AreEqual(1, client.Starts);
                Assert.IsFalse(StdioBridgeReloadHandler.IsResumePending);
            });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void RetiredCompletionPreservesReplacementIntent(bool lateSuccess)
        {
            WithoutContext(() =>
            {
                // Given an old start whose completion is still outstanding after cancellation.
                SessionState.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
                Task resume = StdioBridgeReloadHandler.ResumeStdioWithRetriesAsync(ZeroSchedule);
                StdioBridgeReloadHandler.CancelPendingResume();
                // When another reload records new intent before the old start completes.
                SessionState.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
                client.Completion.SetResult(lateSuccess);
                // Then the retired generation cannot consume or retry the new intent.
                Assert.IsTrue(resume.IsCompleted);
                resume.GetAwaiter().GetResult();
                Assert.IsTrue(StdioBridgeReloadHandler.IsResumePending);
                Assert.AreEqual(1, client.Starts);
            });
        }

        [Test]
        public void CancelledDeferredResumeDoesNotRestart()
        {
            // Given a compile-deferred resume callback already queued on the Editor loop.
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Static;
            SessionState.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
            var schedule = typeof(StdioBridgeReloadHandler).GetMethod("ScheduleResumeAfterDelay", flags);
            var callbackField = typeof(StdioBridgeReloadHandler).GetField("_resumeCallback", flags);
            schedule.Invoke(null, null);
            var callback = (EditorApplication.CallbackFunction)callbackField.GetValue(null);
            // When a manual lifecycle action cancels before the callback can run.
            StdioBridgeReloadHandler.CancelPendingResume();
            Assert.IsFalse(Array.Exists(EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>(), entry => entry.Equals(callback)));
            // A further reload can schedule new intent before an already-copied old callback runs.
            SessionState.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
            schedule.Invoke(null, null);
            var replacement = (EditorApplication.CallbackFunction)callbackField.GetValue(null);
            callback();
            // Then the retired callback cannot start or remove the replacement's work.
            Assert.AreEqual(0, client.Starts);
            Assert.IsTrue(StdioBridgeReloadHandler.IsResumePending);
            Assert.IsTrue(Array.Exists(EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>(), entry => entry.Equals(replacement)));
        }

        [Test]
        public void MultiPassReloadRetainsSessionIntentWithoutWritingGlobalPreference()
        {
            // Given stdio running at the first reload boundary.
            EditorPrefs.DeleteKey(StdioBridgeReloadHandler.ResumeSessionKey);
            Assert.IsTrue(StdioBridgeReloadHandler.PersistResumeIntent(true));
            // When a second boundary arrives before the first resume has connected.
            Assert.IsTrue(StdioBridgeReloadHandler.PersistResumeIntent(false));
            // Then this Editor's intent survives and no machine-wide intent is created.
            Assert.IsTrue(StdioBridgeReloadHandler.IsResumePending);
            Assert.IsFalse(EditorPrefs.HasKey(StdioBridgeReloadHandler.ResumeSessionKey));
        }

        [Test]
        public void LegacyMigrationNeverAdoptsAnotherEditorsIntentOrCleansItTwice()
        {
            // Given an old machine-wide flag that cannot be attributed to this Editor.
            SessionState.EraseBool(MigrationKey);
            EditorPrefs.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
            // When this session performs the one-time legacy cleanup.
            StdioBridgeReloadHandler.MigrateLegacyResumeIntent();
            Assert.IsFalse(StdioBridgeReloadHandler.IsResumePending);
            Assert.IsFalse(EditorPrefs.HasKey(StdioBridgeReloadHandler.ResumeSessionKey));
            // Then later reloads and cancellation leave an older Editor's new flag alone.
            EditorPrefs.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
            StdioBridgeReloadHandler.MigrateLegacyResumeIntent();
            StdioBridgeReloadHandler.CancelPendingResume();
            Assert.IsTrue(EditorPrefs.GetBool(StdioBridgeReloadHandler.ResumeSessionKey, false));
        }

        [Test]
        public void NoPendingIntentDoesNotStartTransport()
        {
            Task resume = StdioBridgeReloadHandler.ResumeStdioWithRetriesAsync(ZeroSchedule);
            Assert.IsTrue(resume.IsCompleted);
            Assert.AreEqual(0, client.Starts);
        }

        [Test]
        public void TransportSwitchClearsThisEditorsIntent()
        {
            SessionState.SetBool(StdioBridgeReloadHandler.ResumeSessionKey, true);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorConfigurationCache.Instance.Refresh();
            Assert.IsFalse(StdioBridgeReloadHandler.PersistResumeIntent(false));
            Assert.IsFalse(StdioBridgeReloadHandler.IsResumePending);
        }

        private void WithoutContext(Action test)
        {
            // All fake completions are controlled on this thread; no editor tick or real
            // transport auto-start can run while preferences/services are temporarily replaced.
            SynchronizationContext saved = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(null);
            try
            {
                test();
            }
            finally
            {
                StdioBridgeReloadHandler.CancelPendingResume();
                client.Completion.TrySetResult(false);
                SynchronizationContext.SetSynchronizationContext(saved);
            }
        }

        private sealed class HeldClient : IMcpTransportClient
        {
            internal readonly TaskCompletionSource<bool> Completion = new TaskCompletionSource<bool>();
            internal int Starts;
            internal int Stops;
            public bool IsConnected => false;
            public string TransportName => "stdio-test";
            public TransportState State => TransportState.Disconnected(TransportName);

            public Task<bool> StartAsync()
            {
                Starts++;
                return Completion.Task;
            }

            public Task StopAsync()
            {
                Stops++;
                return Task.CompletedTask;
            }

            public Task<bool> VerifyAsync() => Task.FromResult(false);

            public Task ReregisterToolsAsync() => Task.CompletedTask;
        }
    }
}
