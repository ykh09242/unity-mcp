using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class BridgeConnectionIntentTests : TransportPreferenceTestBase
    {
        private readonly Dictionary<string, (bool exists, string value)> _preferences = new();
        private TransportManager _manager;
        private ControlledClient _http;
        private ControlledClient _stdio;
        private bool _resume;
        private string _resumeStatus;
        private bool _connectPending;

        [SetUp]
        public void SetUp()
        {
            foreach (string key in new[] { EditorPrefKeys.HttpTransportScope, EditorPrefKeys.HttpBaseUrl, EditorPrefKeys.HttpRemoteBaseUrl })
                _preferences[key] = (EditorPrefs.HasKey(key), EditorPrefs.GetString(key));
            _resume = HttpBridgeReloadHandler.IsResumePending;
            _resumeStatus = HttpBridgeReloadHandler.ResumeStatus;
            _connectPending = SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, false);
            _http = new ControlledClient();
            _stdio = new ControlledClient();
            _manager = new TransportManager();
            _manager.Configure(() => _http, () => _stdio);
            MCPServiceLocator.Register(_manager);
            EditorPrefs.SetBool(EditorPrefKeys.UseHttpTransport, true);
            EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
            EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, "http://127.0.0.1:8080");
            EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, "https://intent-b.example");
            EditorConfigurationCache.Instance.Refresh();
            HttpBridgeReloadHandler.CancelPendingResume();
        }

        [TearDown]
        public void TearDown()
        {
            HttpBridgeReloadHandler.CancelPendingResume();
            _http.PendingStart?.TrySetResult(false);
            _http.PendingStop?.TrySetResult(true);
            _stdio.PendingStart?.TrySetResult(false);
            _stdio.PendingStop?.TrySetResult(true);
            _manager.StopAsync().GetAwaiter().GetResult();
            // Register retires the previous owner; a saved instance cannot be restored.
            MCPServiceLocator.Register(new TransportManager());
            foreach (var pref in _preferences)
                if (pref.Value.exists)
                    EditorPrefs.SetString(pref.Key, pref.Value.value);
                else
                    EditorPrefs.DeleteKey(pref.Key);
            _preferences.Clear();
            EditorConfigurationCache.Instance.Refresh();
            SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, _resume);
            SessionState.SetString(HttpBridgeReloadHandler.ResumeStatusSessionKey, _resumeStatus);
            SessionState.SetBool(HttpAutoStartHandler.ConnectPendingKey, _connectPending);
        }

        [Test]
        public void CachedDefaultBridge_UsesReplacementOwnerAndRejectsRetiredRestoration()
        {
            // Given the cached default bridge while its original owner is active.
            IBridgeControlService previousBridge = MCPServiceLocator.Bridge;
            var bridge = new BridgeControlService();
            MCPServiceLocator.Register<IBridgeControlService>(bridge);
            var client = new ControlledClient();
            var replacement = new TransportManager();
            replacement.Configure(() => client, () => new ControlledClient());
            try
            {
                // When the locator transfers ownership to a replacement manager.
                MCPServiceLocator.Register(replacement);

                // Then the same bridge reaches that owner, and an invalid restoration leaves it usable.
                Assert.AreSame(bridge, MCPServiceLocator.Bridge);
                Assert.IsTrue(bridge.StartAsync().Result);
                Assert.IsTrue(bridge.IsRunning);
                Assert.AreEqual(1, client.StartCalls);
                Assert.Throws<ObjectDisposedException>(() => MCPServiceLocator.Register(_manager));
                Assert.AreSame(replacement, MCPServiceLocator.TransportManager);
                Assert.IsTrue(bridge.VerifyAsync().Result.Success);
                Assert.IsTrue(bridge.StopAsync().IsCompleted);
                Assert.IsFalse(bridge.IsRunning);
            }
            finally
            {
                MCPServiceLocator.Register<IBridgeControlService>(previousBridge);
            }
        }

        [Test]
        public void AsyncVerification_IncompleteProbeLeavesCallingContextAvailable()
        {
            // Given a connected owner and a probe waiting on this thread's context.
            Assert.IsTrue(_manager.StartAsync(TransportMode.Http).Result);
            _http.PendingVerify = new TaskCompletionSource<bool>();
            var context = new QueuedContext();
            SynchronizationContext saved = SynchronizationContext.Current;
            SynchronizationContext.SetSynchronizationContext(context);
            try
            {
                // When asynchronous verification starts before the probe completes.
                Task<BridgeVerificationResult> verification = new BridgeControlService().VerifyAsync();

                // Then control returns to the caller and pumping its context completes the result.
                Assert.IsFalse(verification.IsCompleted);
                _http.PendingVerify.SetResult(true);
                context.Drain();
                Assert.IsTrue(verification.IsCompleted && verification.Result.Success);
                Assert.IsTrue(new BridgeControlService().Verify(0).Success, "Completed probes retain synchronous compatibility.");
            }
            finally
            {
                _http.PendingVerify.TrySetResult(false);
                context.Drain();
                SynchronizationContext.SetSynchronizationContext(saved);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SuspendedBridgeStart_StopOrSelectionChangeDoesNotStartDeselectedTransport(bool switchBack)
        {
            Assert.IsTrue(_manager.StartAsync(TransportMode.Http).Result);
            EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            _http.PendingStop = new TaskCompletionSource<bool>();
            var bridge = new BridgeControlService();
            Task<bool> pending = bridge.StartAsync();
            Assert.IsFalse(pending.IsCompleted, "The opposing HTTP stop must hold the bridge before stdio start.");
            if (switchBack)
                EditorConfigurationCache.Instance.SetUseHttpTransport(true);
            else
                Assert.IsTrue(bridge.StopAsync().IsCompleted);
            _http.PendingStop.SetResult(true);
            Assert.IsTrue(pending.IsCompleted);
            Assert.IsFalse(pending.Result);
            Assert.AreEqual(0, _stdio.StartCalls, "Retired bridge intent must never reach the target transport.");
            Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Stdio));
        }

        [Test]
        public void SuspendedBridgeStart_SelectionRoundTripDoesNotReviveIntent()
        {
            Assert.IsTrue(_manager.StartAsync(TransportMode.Http).Result);
            EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            _http.PendingStop = new TaskCompletionSource<bool>();
            Task<bool> pending = new BridgeControlService().StartAsync();
            Assert.IsFalse(pending.IsCompleted);
            EditorConfigurationCache.Instance.SetUseHttpTransport(true);
            EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            _http.PendingStop.SetResult(true);
            Assert.IsFalse(pending.Result);
            Assert.AreEqual(0, _stdio.StartCalls, "Returning to the same selection must not revive an older request.");
        }

        [Test]
        public void SuspendedHttpBridgeStart_EndpointChangeDoesNotStartWithOldIntent()
        {
            EditorConfigurationCache.Instance.SetUseHttpTransport(false);
            Assert.IsTrue(_manager.StartAsync(TransportMode.Stdio).Result);
            EditorConfigurationCache.Instance.SetUseHttpTransport(true);
            _stdio.PendingStop = new TaskCompletionSource<bool>();
            Task<bool> pending = new BridgeControlService().StartAsync();
            Assert.IsFalse(pending.IsCompleted);
            EditorConfigurationCache.Instance.SetHttpBaseUrl("http://127.0.0.1:8081");
            _stdio.PendingStop.SetResult(true);
            Assert.IsFalse(pending.Result);
            Assert.AreEqual(0, _http.StartCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EndpointChange_RetiresActiveOrPendingHttpWithoutReconnect(bool pendingStart)
        {
            if (pendingStart)
                _http.PendingStart = new TaskCompletionSource<bool>();
            Task<bool> start = _manager.StartAsync(TransportMode.Http);
            EditorConfigurationCache.Instance.SetHttpBaseUrl("http://127.0.0.1:8081");
            if (pendingStart)
                _http.PendingStart.SetResult(true);
            Assert.IsTrue(start.IsCompleted);
            Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Http));
            Assert.IsFalse(_manager.IsRunning(TransportMode.Http));
            Assert.AreEqual(1, _http.StopCalls);
            Assert.AreEqual(1, _http.StartCalls, "Applying settings requires an explicit reconnect.");
        }

        [Test]
        public void ScopeChange_RetiresHttpAndCancelsOldResumeIntent()
        {
            Assert.IsTrue(_manager.StartAsync(TransportMode.Http).Result);
            SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, true);
            SessionState.SetBool(HttpAutoStartHandler.ConnectPendingKey, true);
            EditorConfigurationCache.Instance.SetHttpTransportScope("remote");
            Assert.IsFalse(_manager.IsRunning(TransportMode.Http));
            Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Http));
            Assert.IsFalse(HttpBridgeReloadHandler.IsResumePending);
            Assert.IsFalse(SessionState.GetBool(HttpAutoStartHandler.ConnectPendingKey, false));
            Assert.AreEqual(1, _http.StartCalls);
        }

        [Test]
        public void InactiveEndpointChange_PreservesActiveLocalConnectionAndReloadIntent()
        {
            Assert.IsTrue(_manager.StartAsync(TransportMode.Http).Result);
            SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, true);
            EditorConfigurationCache.Instance.SetHttpRemoteBaseUrl("https://intent-c.example");
            Assert.IsTrue(_manager.IsRunning(TransportMode.Http));
            Assert.IsTrue(HttpBridgeReloadHandler.IsResumePending);
            Assert.AreEqual(0, _http.StopCalls);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void EndpointHelperChange_RetiresConnectionButUnchangedSavePreservesIt(bool remote)
        {
            if (remote)
                EditorConfigurationCache.Instance.SetHttpTransportScope("remote");
            Assert.IsTrue(_manager.StartAsync(TransportMode.Http).Result);
            string initial = remote ? "https://intent-b.example" : "http://127.0.0.1:8080";
            HttpEndpointUtility.SaveBaseUrl(initial);
            Assert.IsTrue(_manager.IsRunning(TransportMode.Http), "Saving unchanged settings must not disconnect.");
            HttpEndpointUtility.SaveBaseUrl(remote ? "https://intent-c.example" : "http://127.0.0.1:8081");
            Assert.IsFalse(_manager.IsRunning(TransportMode.Http));
            Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Http));
            Assert.AreEqual(1, _http.StopCalls);
            Assert.AreEqual(1, _http.StartCalls);
        }

        [Test]
        public async Task BoundedStop_PreservesUnsettledBridgeStatusUntilActualSettlement()
        {
            const string budgetKey = "UNITY_MCP_WEBSOCKET_CONTROL_TIMEOUT_SECONDS";
            string previousBudget = Environment.GetEnvironmentVariable(budgetKey);
            var release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var lifecycle = new CancellationTokenSource();
            using var connection = CancellationTokenSource.CreateLinkedTokenSource(lifecycle.Token);
            var work = new ConnectionCommandWork(connection.Token);
            var endpoint = new Uri("ws://127.0.0.1:1/hub/plugin");
            var options = new WebSocketTransportClient.OwnedConnectionOptions(endpoint, "owned-synthetic-token", "owned", "owned", "/owned", "6000.0");
            using var client = new WebSocketTransportClient(null, options);
            void Set(object target, string field, object value) =>
                target.GetType().GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(target, value);
            try
            {
                Environment.SetEnvironmentVariable(budgetKey, "0.05");
                int mutations = 0;
                Assert.IsNull(
                    work.TryStart(
                        "owned-held-command",
                        async _ =>
                        {
                            Interlocked.Increment(ref mutations);
                            await release.Task;
                        }
                    )
                );
                Assert.IsTrue(await _manager.StartAsync(TransportMode.Http));
                Set(client, "_lifecycleCts", lifecycle);
                Set(client, "_connectionCts", connection);
                Set(client, "_commandWork", work);
                Set(client, "_isConnected", true);
                Set(client, "_state", TransportState.Connected("websocket", sessionId: "owned-stopping"));
                Set(_manager, "_httpClient", client);
                var bridge = new BridgeControlService();
                Task stop = bridge.StopAsync();
                Assert.AreSame(stop, await Task.WhenAny(stop, Task.Delay(3000)), "Bridge Stop must return within the observation budget.");
                await stop;
                Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Http));
                Assert.IsFalse(bridge.IsRunning);
                StringAssert.Contains("execution_unsettled", _manager.GetState(TransportMode.Http).Error);
                StringAssert.Contains("execution_unsettled", (await bridge.VerifyAsync()).Message);
                StringAssert.Contains("execution_unsettled", bridge.Verify(0).Message);
                Assert.IsFalse(work.DrainAsync().IsCompleted);

                Assert.IsFalse(await _manager.StartAsync(TransportMode.Http), "Unfinished command ownership must still refuse a replacement connection.");
                Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Http));
                Assert.IsNotNull(
                    work.TryStart(
                        "owned-late-command",
                        _ =>
                        {
                            Interlocked.Increment(ref mutations);
                            return Task.CompletedTask;
                        }
                    )
                );
                Assert.AreEqual(1, mutations);
                StringAssert.Contains("execution_unsettled", (await bridge.VerifyAsync()).Message);

                release.TrySetResult(true);
                Task settlement = (Task)
                    typeof(WebSocketTransportClient).GetField("_stopSettlement", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(client);
                Assert.AreSame(settlement, await Task.WhenAny(settlement, Task.Delay(3000)));
                await settlement;
                Assert.IsNull(_manager.GetState(TransportMode.Http).Error, "Status reads must observe actual settlement without another Stop or Start.");
                StringAssert.DoesNotContain("execution_unsettled", (await bridge.VerifyAsync()).Message);
                Assert.IsFalse(_manager.HasConnectionIntent(TransportMode.Http));
                Assert.IsFalse(bridge.IsRunning);
                Assert.AreEqual(1, mutations);
            }
            finally
            {
                release.TrySetResult(true);
                try
                {
                    await client.StopAsync();
                }
                finally
                {
                    Environment.SetEnvironmentVariable(budgetKey, previousBudget);
                    Set(_manager, "_httpClient", _http);
                }
            }
        }

        [Test]
        public void FailedStart_ManualVerificationAndReloadStatusPreserveReasonAfterCleanup()
        {
            _http.StartFailure = "Registration acknowledgement timed out";
            Task<bool> start = _manager.StartAsync(TransportMode.Http);
            Assert.IsFalse(start.Result);
            Assert.IsNull(_http.State.Error, "The fake models cleanup that erases the client's error.");
            var bridge = new BridgeControlService();
            Assert.AreEqual(_http.StartFailure, bridge.VerifyAsync().Result.Message);
            SessionState.SetBool(HttpBridgeReloadHandler.ResumeSessionKey, true);
            HttpBridgeReloadHandler.ResumeHttpWithRetriesAsync(new[] { TimeSpan.Zero }, () => false).GetAwaiter().GetResult();
            StringAssert.Contains(_http.StartFailure, HttpBridgeReloadHandler.ResumeStatus);
        }

        private sealed class ControlledClient : IMcpTransportClient
        {
            public TaskCompletionSource<bool> PendingStart;
            public TaskCompletionSource<bool> PendingStop;
            public TaskCompletionSource<bool> PendingVerify;
            public string StartFailure;
            public int StartCalls;
            public int StopCalls;
            public bool IsConnected => State.IsConnected;
            public string TransportName => "controlled";
            public TransportState State { get; private set; } = TransportState.Disconnected("controlled");

            public Task<bool> StartAsync()
            {
                StartCalls++;
                State = StartFailure == null ? TransportState.Connected(TransportName) : TransportState.Disconnected(TransportName, StartFailure);
                return PendingStart?.Task ?? Task.FromResult(StartFailure == null);
            }

            public Task StopAsync()
            {
                StopCalls++;
                State = TransportState.Disconnected(TransportName);
                return PendingStop?.Task ?? Task.CompletedTask;
            }

            public Task<bool> VerifyAsync() => PendingVerify?.Task ?? Task.FromResult(IsConnected);

            public Task ReregisterToolsAsync() => Task.CompletedTask;
        }

        private sealed class QueuedContext : SynchronizationContext
        {
            private readonly Queue<Action> _callbacks = new();

            public override void Post(SendOrPostCallback callback, object state) => _callbacks.Enqueue(() => callback(state));

            internal void Drain()
            {
                while (_callbacks.Count != 0)
                    _callbacks.Dequeue()();
            }
        }
    }
}
