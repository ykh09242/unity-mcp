using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport.Transports;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    [Parallelizable(ParallelScope.None)]
    public class WebSocketLifecycleIntentTests
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

        [Test]
        public void Stop_ThrowingDisconnectedObserverPreservesSettlementAndAllowsRestart()
        {
            const string timeoutKey = "UNITY_MCP_WEBSOCKET_CONTROL_TIMEOUT_SECONDS";
            string previousTimeout = Environment.GetEnvironmentVariable(timeoutKey);
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            var context = new HeldContext();
            var heldReceive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Task<bool> start = context.Start(client);
            int disconnected = 0;
            Action throwing = () =>
            {
                if (!client.Status.IsConnected)
                    throw new InvalidOperationException("owned-sensitive-observer-marker");
            };
            Action remaining = () =>
            {
                if (!client.Status.IsConnected)
                    Interlocked.Increment(ref disconnected);
            };
            try
            {
                FinishStart(context, start);
                Assert.IsTrue(start.GetAwaiter().GetResult());
                Wait(() => Get(client, "_toolSettingsRegistration") != null, "owned tool settings registration");
                object lifecycle = Get(client, "_lifecycleCts");
                lock (Get(client, "_ownershipLock"))
                    Set(client, "_receiveTask", Task.WhenAll((Task)Get(client, "_receiveTask"), heldReceive.Task));
                client.StatusChanged += throwing;
                client.StatusChanged += remaining;
                Environment.SetEnvironmentVariable(timeoutKey, "0.1");
                UnityEngine.TestTools.LogAssert.Expect(
                    UnityEngine.LogType.Warning,
                    new System.Text.RegularExpressions.Regex("Tool settings status observer failed: InvalidOperationException")
                );

                Task stop = client.StopAsync();
                Wait(() => stop.IsCompleted, "bounded stop observation");
                Assert.DoesNotThrow(() => stop.GetAwaiter().GetResult());
                Assert.AreEqual(1, Volatile.Read(ref disconnected), "A throwing observer must not skip remaining subscribers.");
                Assert.IsFalse(((Task)Get(client, "_stopSettlement")).IsCompleted, "The actual held receive loop must continue to own cleanup.");
                start = context.Start(client);
                FinishStart(context, start);
                Assert.IsFalse(start.GetAwaiter().GetResult(), "Observer isolation cannot permit replacement while actual work is unsettled.");
                Assert.AreSame(lifecycle, Get(client, "_lifecycleCts"));

                heldReceive.SetResult(true);
                Wait(() => ((Task)Get(client, "_stopSettlement")).IsCompletedSuccessfully, "actual stopped lifecycle settlement");
                Assert.IsNull(Get(client, "_socket"));
                Assert.IsNull(Get(client, "_lifecycleCts"));
                Assert.IsNull(Get(client, "_connectionCts"));
                Assert.IsNull(Get(client, "_toolSettingsRegistration"));
                Assert.IsNull(Get(client, "_receiveTask"));
                Assert.IsNull(Get(client, "_keepAliveTask"));
                Assert.IsNull(Get(client, "_commandWork"));

                start = context.Start(client);
                FinishStart(context, start);
                Assert.IsTrue(start.GetAwaiter().GetResult(), "A diagnostic-only observer failure must not poison the next start.");
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("owned-editor-session-2", client.State.SessionId);
            }
            finally
            {
                heldReceive.TrySetResult(true);
                client.StatusChanged -= throwing;
                client.StatusChanged -= remaining;
                try
                {
                    Cleanup(client, context, start);
                }
                finally
                {
                    Environment.SetEnvironmentVariable(timeoutKey, previousTimeout);
                }
            }
        }

        [Test]
        public void Dispose_RetainsQueuedSendResourcesUntilActualSettlement()
        {
            using var client = CreateClient(new Uri("ws://127.0.0.1:1"));
            var connection = new CancellationTokenSource();
            using var canceled = new CancellationTokenSource();
            var receiver = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Set(client, "_connectionCts", connection);
            Set(client, "_socket", new System.Net.WebSockets.ClientWebSocket());
            Set(client, "_receiveTask", receiver.Task);
            var sendLock = (SemaphoreSlim)Get(client, "_sendLock");
            Assert.IsTrue(sendLock.Wait(0));
            var send = typeof(WebSocketTransportClient).GetMethod("SendOwnedFrameAsync", Private);
            var queued = (Task)
                send.Invoke(
                    client,
                    new object[]
                    {
                        new ArraySegment<byte>(new byte[1]),
                        System.Net.WebSockets.WebSocketMessageType.Text,
                        connection.Token,
                        CancellationToken.None,
                    }
                );
            var canceling = (Task)
                send.Invoke(
                    client,
                    new object[] { new ArraySegment<byte>(new byte[1]), System.Net.WebSockets.WebSocketMessageType.Text, connection.Token, canceled.Token }
                );
            try
            {
                client.Dispose();
                client.Dispose();
                Assert.IsFalse(receiver.Task.IsCompleted, "Dispose must return without waiting for Editor-owned cleanup.");
                Assert.IsNull(Get(client, "_connectionCts"));
                Assert.Throws<ObjectDisposedException>(() => client.StartAsync().GetAwaiter().GetResult());
                canceled.Cancel();
                Wait(() => canceling.IsCompleted, "canceled queued send settles after disposal");
                Assert.IsTrue(canceling.IsCanceled);
                Assert.DoesNotThrow(() => sendLock.Release(), "Admitted send waiters retain the shared semaphore.");
                Wait(() => queued.IsCompleted, "retired queued send settles after disposal");
                Assert.IsTrue(queued.IsCanceled, "Retirement must cancel the send before socket access.");
                Assert.IsTrue(sendLock.Wait(0));
                sendLock.Release();
                receiver.SetResult(true);
                Wait(() => ((Task)Get(client, "_disposeSettlement")).IsCompleted, "deferred resource disposal");
                ((Task)Get(client, "_disposeSettlement")).GetAwaiter().GetResult();
                Assert.Throws<ObjectDisposedException>(() => sendLock.Wait(0));
            }
            finally
            {
                receiver.TrySetResult(true);
                canceled.Cancel();
                client.ForceStop();
            }
        }

        [Test]
        public void SilentUpgradeDeadlineReleasesCandidateAndAllowsRetry()
        {
            const string timeoutKey = "UNITY_MCP_WEBSOCKET_CONTROL_TIMEOUT_SECONDS";
            string previous = Environment.GetEnvironmentVariable(timeoutKey);
            using var healthy = CreatePeer(out Uri endpoint);
            using var peer = new SilentUpgradePeer(endpoint);
            using var client = CreateClient(peer.Endpoint);
            var context = new HeldContext();
            Task<bool> start = null;
            try
            {
                Environment.SetEnvironmentVariable(timeoutKey, "0.1");
                UnityEngine.TestTools.LogAssert.Expect(
                    UnityEngine.LogType.Error,
                    new System.Text.RegularExpressions.Regex("\\[WebSocket\\] Connection failed\\.")
                );
                start = context.Start(client);
                Wait(() => peer.Accepted.Task.IsCompleted, "silent upgrade accepted");
                Wait(() => context.HasContinuation, "product connection deadline");
                while (context.HasContinuation)
                    context.RunNext();
                Wait(() => start.IsCompleted || context.HasContinuation, "bounded startup cleanup");
                while (context.HasContinuation)
                    context.RunNext();
                Assert.IsTrue(start.IsCompleted, "A silent upgrade must not strand initial startup.");
                Assert.IsFalse(start.GetAwaiter().GetResult());
                Assert.IsFalse(client.IsConnected);
                Assert.IsNull(Get(client, "_socket"), "The failed candidate must release its socket.");
                Assert.AreEqual(1, PendingAttempts(client), "A disposed public socket is not proof that Mono's physical attempt finished.");
                for (int retry = 0; retry < 3; retry++)
                {
                    UnityEngine.TestTools.LogAssert.Expect(
                        UnityEngine.LogType.Error,
                        new System.Text.RegularExpressions.Regex("connection_establishment_unsettled")
                    );
                    start = context.Start(client);
                    FinishStart(context, start);
                    Assert.IsFalse(start.GetAwaiter().GetResult());
                    Assert.AreEqual(1, PendingAttempts(client), "Repeated starts cannot create additional physical attempts to a still-pending candidate.");
                }
                // Mono can retain its internal upgrade read after ClientWebSocket is
                // disposed. Release the owned peer so that actual operation also settles.
                peer.Release();
                Assert.IsTrue(peer.Closed.Task.Wait(5000), "The fixture must release its actual silent upgrade read.");
                Wait(() => PendingAttempts(client) == 0, "actual retired connection completion");
                Assert.IsFalse(client.IsConnected, "Late candidate completion cannot publish a connection.");

                start = context.Start(client);
                Wait(() => context.HasContinuation && client.State.SessionId == "owned-editor-session-1", "retry registration");
                context.RunNext();
                Assert.IsTrue(start.IsCompleted && start.GetAwaiter().GetResult(), "A failed upgrade must not prevent the next owned attempt.");
            }
            finally
            {
                try
                {
                    if (start != null)
                        Cleanup(client, context, start);
                }
                finally
                {
                    Environment.SetEnvironmentVariable(timeoutKey, previous);
                }
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void StopDuringSilentUpgradeCannotPublishAStaleConnection(bool force)
        {
            using var peer = new SilentUpgradePeer();
            using var client = CreateClient(peer.Endpoint);
            var context = new HeldContext();
            Task<bool> start = context.Start(client);
            try
            {
                Wait(() => peer.Accepted.Task.IsCompleted, "silent upgrade accepted");
                if (force)
                    client.ForceStop();
                else
                    client.StopAsync().GetAwaiter().GetResult();
                Wait(() => context.HasContinuation || start.IsCompleted, "stopped connection continuation");
                while (context.HasContinuation)
                    context.RunNext();
                Wait(() => start.IsCompleted, "stopped startup completion");
                Assert.IsFalse(client.IsConnected);
                Assert.IsFalse(client.State.IsConnected);
                Assert.IsNull(Get(client, "_socket"));
                peer.Release();
                Assert.IsTrue(peer.Closed.Task.Wait(5000));
                Wait(() => PendingAttempts(client) == 0, "stopped physical connection completion");
                Assert.IsTrue(start.IsCanceled || start.IsFaulted || !start.GetAwaiter().GetResult());
            }
            finally
            {
                Cleanup(client, context, start);
            }
        }

        [Test]
        public void DifferentSilentCandidatesShareABoundedPhysicalAttemptBudget()
        {
            const string timeoutKey = "UNITY_MCP_WEBSOCKET_CONTROL_TIMEOUT_SECONDS";
            string previous = Environment.GetEnvironmentVariable(timeoutKey);
            var peers = new[] { new SilentUpgradePeer(), new SilentUpgradePeer(), new SilentUpgradePeer(), new SilentUpgradePeer() };
            using var client = CreateClient(peers[0].Endpoint);
            using var lifecycle = new CancellationTokenSource();
            var establish = typeof(WebSocketTransportClient).GetMethod("EstablishConnectionAsync", Private);
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_useLocalAuth", true);
            try
            {
                Environment.SetEnvironmentVariable(timeoutKey, "0.1");
                for (int index = 0; index < peers.Length; index++)
                {
                    Set(client, "_endpointUri", peers[index].Endpoint);
                    UnityEngine.TestTools.LogAssert.Expect(
                        UnityEngine.LogType.Error,
                        new System.Text.RegularExpressions.Regex(index < 3 ? "\\[WebSocket\\] Connection failed\\." : "connection_establishment_unsettled")
                    );
                    var attempt = (Task<CancellationToken?>)establish.Invoke(client, new object[] { lifecycle.Token });
                    Wait(() => attempt.IsCompleted, "bounded candidate establishment");
                    Assert.IsNull(attempt.GetAwaiter().GetResult());
                    Assert.AreEqual(Math.Min(index + 1, 3), PendingAttempts(client));
                    if (index < 3)
                        Assert.IsTrue(peers[index].Accepted.Task.IsCompleted);
                    else
                        Assert.IsFalse(
                            peers[index].Accepted.Task.IsCompleted,
                            "A full physical-attempt budget must refuse before initiating another connection."
                        );
                }
                foreach (var peer in peers)
                    peer.Release();
                Wait(() => PendingAttempts(client) == 0, "actual bounded candidate completion");
            }
            finally
            {
                try
                {
                    client.ForceStop();
                    foreach (var peer in peers)
                        peer.Dispose();
                    Wait(() => ((Task)Get(client, "_forcedCommandSettlement")).IsCompleted, "bounded attempt cleanup");
                }
                finally
                {
                    Environment.SetEnvironmentVariable(timeoutKey, previous);
                }
            }
        }

        private sealed class SilentUpgradePeer : IDisposable
        {
            private readonly TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
            private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
            private readonly Task operation;
            private TcpClient socket;
            internal readonly TaskCompletionSource<bool> Accepted = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly TaskCompletionSource<bool> Closed = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal Uri Endpoint { get; }

            internal SilentUpgradePeer(Uri retryUpstream = null)
            {
                listener.Start();
                Endpoint = new Uri("ws://127.0.0.1:" + ((IPEndPoint)listener.LocalEndpoint).Port + "/owned-upgrade");
                operation = Task.Run(async () =>
                {
                    try
                    {
                        using var accepted = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
                        socket = accepted;
                        var bytes = new byte[4096];
                        string request = "";
                        int count;
                        while ((count = await accepted.GetStream().ReadAsync(bytes, 0, bytes.Length, lifetime.Token).ConfigureAwait(false)) != 0)
                        {
                            request += Encoding.ASCII.GetString(bytes, 0, count);
                            if (request.Contains("\r\n\r\n"))
                                Accepted.TrySetResult(true);
                        }
                        Closed.TrySetResult(true);
                    }
                    catch (Exception) when (lifetime.IsCancellationRequested) { }
                    catch (Exception exception) when (exception is IOException || exception is SocketException || exception is ObjectDisposedException)
                    {
                        Closed.TrySetResult(true);
                    }
                    if (retryUpstream != null && !lifetime.IsCancellationRequested)
                    {
                        // After releasing the first silent connection, forward a retry
                        // from the same endpoint to the existing owned RFC6455 peer.
                        try
                        {
                            using var retry = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
                            socket = retry;
                            using var upstream = new TcpClient();
                            await upstream.ConnectAsync(retryUpstream.Host, retryUpstream.Port).ConfigureAwait(false);
                            Task outbound = retry.GetStream().CopyToAsync(upstream.GetStream(), 8192, lifetime.Token);
                            Task inbound = upstream.GetStream().CopyToAsync(retry.GetStream(), 8192, lifetime.Token);
                            await Task.WhenAny(outbound, inbound).ConfigureAwait(false);
                            retry.Dispose();
                            upstream.Dispose();
                            try
                            {
                                await Task.WhenAll(outbound, inbound).ConfigureAwait(false);
                            }
                            catch (Exception exception)
                                when (exception is IOException || exception is ObjectDisposedException || exception is OperationCanceledException) { }
                        }
                        catch (Exception) when (lifetime.IsCancellationRequested) { }
                    }
                });
            }

            public void Dispose()
            {
                lifetime.Cancel();
                listener.Stop();
                socket?.Dispose();
                Assert.IsTrue(operation.Wait(5000), "Owned silent peer cleanup");
                lifetime.Dispose();
            }

            internal void Release() => socket?.Dispose();
        }

        private static int PendingAttempts(WebSocketTransportClient client)
        {
            lock (Get(client, "_ownershipLock"))
                return ((System.Collections.IDictionary)Get(client, "_pendingConnectionAttempts")).Count;
        }

        private static void FinishStart(HeldContext context, Task start) =>
            Wait(
                () =>
                {
                    while (context.HasContinuation)
                        context.RunNext();
                    return start.IsCompleted;
                },
                "bounded startup completion"
            );

        [TestCase(false)]
        [TestCase(true)]
        public void RemoteSettingsChangedDuringCleanupCannotRetargetTheCapturedCredential(bool changeScope)
        {
            using var peerA = new TransportEditorIntegrationTests.OwnedWebSocketPeer("unused-remote-test");
            using var peerB = new TransportEditorIntegrationTests.OwnedWebSocketPeer("unused-remote-test");
            string a = "http://127.0.0.1:" + peerA.Uri.Port + "/proxy?ticket=synthetic-private-query";
            string b = "http://127.0.0.1:" + peerB.Uri.Port;
            var saved = new Dictionary<string, string>();
            foreach (
                string key in new[]
                {
                    EditorPrefKeys.HttpTransportScope,
                    EditorPrefKeys.HttpRemoteBaseUrl,
                    EditorPrefKeys.HttpBaseUrl,
                    EditorPrefKeys.SessionId + "_" + ProjectIdentityUtility.GetProjectHash(),
                    EditorPrefKeys.RemoteApiKeyPrefix + HttpEndpointUtility.GetRemoteOrigin(a),
                }
            )
                saved[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;
            bool hadInsecure = EditorPrefs.HasKey(EditorPrefKeys.AllowInsecureRemoteHttp);
            bool insecure = EditorPrefs.GetBool(EditorPrefKeys.AllowInsecureRemoteHttp);
            using var client = new WebSocketTransportClient();
            using var lifecycle = new CancellationTokenSource();
            var receive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var context = new HeldContext();
            Task<bool> start = null;
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, true);
                EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "remote");
                EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, a);
                HttpEndpointUtility.SaveRemoteApiKey(a, "synthetic-origin-a-key");
                EditorConfigurationCache.Instance.Refresh();
                Set(client, "_lifecycleCts", lifecycle);
                Set(client, "_receiveTask", receive.Task);
                start = context.Start(client);
                Assert.IsFalse(start.IsCompleted, "Startup must be waiting for the old drain.");
                EditorPrefs.SetString(EditorPrefKeys.HttpRemoteBaseUrl, b);
                EditorPrefs.SetString(EditorPrefKeys.HttpBaseUrl, b);
                if (changeScope)
                    EditorPrefs.SetString(EditorPrefKeys.HttpTransportScope, "local");
                EditorConfigurationCache.Instance.Refresh();
                receive.SetResult(true);
                Wait(() => context.HasContinuation, "captured startup continuation");
                context.RunNext();
                Wait(() => context.HasContinuation && client.State.SessionId == "owned-editor-session-1", "captured origin registration");
                context.RunNext();
                Assert.IsTrue(start.IsCompleted && start.GetAwaiter().GetResult());
                Assert.AreEqual("synthetic-origin-a-key", peerA.AuthRequests[0][AuthConstants.ApiKeyHeader]);
                Assert.IsFalse(peerA.AuthRequests[0].ContainsKey(AuthConstants.LocalTokenHeader));
                Assert.IsEmpty(peerB.AuthRequests, "No request may be redirected to B after cleanup.");
                StringAssert.DoesNotContain("synthetic-private-query", client.State.Details);

                peerA.DropConnection();
                Wait(() => peerA.AuthRequests.Length >= 2 && client.State.SessionId == "owned-editor-session-2", "reconnect to captured origin");
                Assert.AreEqual("synthetic-origin-a-key", peerA.AuthRequests[1][AuthConstants.ApiKeyHeader]);
                Assert.IsEmpty(peerB.AuthRequests, "Reconnect must preserve the same approved endpoint and credential.");
            }
            finally
            {
                receive.TrySetResult(true);
                if (start != null)
                    Cleanup(client, context, start);
                foreach (var pref in saved)
                    if (pref.Value == null)
                        EditorPrefs.DeleteKey(pref.Key);
                    else
                        EditorPrefs.SetString(pref.Key, pref.Value);
                if (hadInsecure)
                    EditorPrefs.SetBool(EditorPrefKeys.AllowInsecureRemoteHttp, insecure);
                else
                    EditorPrefs.DeleteKey(EditorPrefKeys.AllowInsecureRemoteHttp);
                EditorConfigurationCache.Instance.Refresh();
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void Stop_WhileStartWaitsForOldDrain_PreventsConnectionAfterContinuation(bool force)
        {
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            using var lifecycle = new CancellationTokenSource();
            var receive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var context = new HeldContext();
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_receiveTask", receive.Task);
            Task<bool> start = context.Start(client);
            try
            {
                Assert.IsFalse(start.IsCompleted, "The old receive loop must hold startup before connection creation.");
                Task stop;
                if (force)
                {
                    client.ForceStop();
                    stop = (Task)Get(client, "_forcedCommandSettlement");
                }
                else
                    stop = client.StopAsync();
                receive.SetResult(true);
                Wait(() => stop.IsCompleted && context.HasContinuation, "old drain and captured startup continuation");
                stop.GetAwaiter().GetResult();

                context.RunNext();

                Assert.IsNull(Get(client, "_lifecycleCts"), "A stopped startup must not create a replacement lifecycle.");
                Assert.IsNull(Get(client, "_socket"), "A stopped startup must not create a replacement socket.");
                Assert.IsTrue(start.IsCompleted, "A stale startup must finish without starting network work.");
                Assert.IsFalse(start.GetAwaiter().GetResult());
                Assert.IsFalse(client.IsConnected);
                Assert.IsFalse(client.State.IsConnected);
            }
            finally
            {
                receive.TrySetResult(true);
                Cleanup(client, context, start);
            }
        }

        [Test]
        public void NewerStart_WhileOldDrainPending_SurvivesOlderContinuation()
        {
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            using var lifecycle = new CancellationTokenSource();
            var receive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var firstContext = new HeldContext();
            var secondContext = new HeldContext();
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_receiveTask", receive.Task);
            Task<bool> first = firstContext.Start(client);
            Task<bool> second = secondContext.Start(client);
            try
            {
                receive.SetResult(true);
                Wait(() => firstContext.HasContinuation && secondContext.HasContinuation, "both pending starts after old drain");
                secondContext.RunNext();
                Wait(() => secondContext.HasContinuation && client.State.SessionId == "owned-editor-session-1", "newer startup registration");
                secondContext.RunNext();
                Assert.IsTrue(second.IsCompleted && second.GetAwaiter().GetResult());
                object current = Get(client, "_lifecycleCts");

                firstContext.RunNext();

                Assert.IsTrue(first.IsCompleted && !first.GetAwaiter().GetResult(), "The superseded start must finish without replacing the newer connection.");
                Assert.AreSame(current, Get(client, "_lifecycleCts"));
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("owned-editor-session-1", client.State.SessionId);
            }
            finally
            {
                receive.TrySetResult(true);
                Cleanup(client, firstContext, first);
                Cleanup(client, secondContext, second);
            }
        }

        [Test]
        public void Registered_BeforeStartContinuation_PreservesServerSessionId()
        {
            using var peer = CreatePeer(out Uri endpoint);
            using var client = CreateClient(endpoint);
            var context = new HeldContext();
            Task<bool> start = context.Start(client);
            try
            {
                Wait(() => context.HasContinuation && client.State.SessionId == "owned-editor-session-1", "server registration before startup publication");
                Assert.IsFalse(start.IsCompleted, "The startup continuation must still be held after registration.");

                context.RunNext();

                Assert.IsTrue(start.IsCompleted);
                Assert.IsTrue(start.GetAwaiter().GetResult());
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("owned-editor-session-1", client.State.SessionId);
            }
            finally
            {
                Cleanup(client, context, start);
            }
        }

        private static IDisposable CreatePeer(out Uri endpoint)
        {
            // Reuse the existing owned wire peer, with synthetic identity and no saved settings.
            Type peerType = typeof(TransportEditorIntegrationTests).GetNestedType("OwnedWebSocketPeer", BindingFlags.NonPublic);
            var peer = (IDisposable)Activator.CreateInstance(peerType, new object[] { "owned-lifecycle-token", false, false });
            endpoint = (Uri)peerType.GetProperty("Uri").GetValue(peer);
            return peer;
        }

        private static WebSocketTransportClient CreateClient(Uri endpoint) =>
            new WebSocketTransportClient(
                null,
                new WebSocketTransportClient.OwnedConnectionOptions(
                    endpoint,
                    "owned-lifecycle-token",
                    "LifecycleIntent",
                    new string('a', 40),
                    "/owned/lifecycle-intent",
                    "6000.0"
                )
            );

        private static void Cleanup(WebSocketTransportClient client, HeldContext context, Task start)
        {
            client.ForceStop();
            Wait(
                () =>
                {
                    while (context.HasContinuation)
                        context.RunNext();
                    return start.IsCompleted
                        && ((Task)Get(client, "_stopSettlement")).IsCompleted
                        && ((Task)Get(client, "_forcedCommandSettlement")).IsCompleted;
                },
                "owned lifecycle cleanup"
            );
            _ = start.Exception;
        }

        private static void Wait(Func<bool> condition, string description) =>
            Assert.IsTrue(SpinWait.SpinUntil(condition, TimeSpan.FromSeconds(5)), description);

        private static void Set(WebSocketTransportClient client, string field, object value) =>
            typeof(WebSocketTransportClient).GetField(field, Private).SetValue(client, value);

        private static object Get(WebSocketTransportClient client, string field) => typeof(WebSocketTransportClient).GetField(field, Private).GetValue(client);

        private sealed class HeldContext : SynchronizationContext
        {
            private readonly ConcurrentQueue<Action> _continuations = new ConcurrentQueue<Action>();
            internal bool HasContinuation => !_continuations.IsEmpty;

            public override void Post(SendOrPostCallback callback, object state) => _continuations.Enqueue(() => callback(state));

            internal Task<bool> Start(WebSocketTransportClient client)
            {
                SynchronizationContext previous = Current;
                SetSynchronizationContext(this);
                try
                {
                    return client.StartAsync();
                }
                finally
                {
                    SetSynchronizationContext(previous);
                }
            }

            internal void RunNext()
            {
                Assert.IsTrue(_continuations.TryDequeue(out Action continuation));
                SynchronizationContext previous = Current;
                SetSynchronizationContext(this);
                try
                {
                    continuation();
                }
                finally
                {
                    SetSynchronizationContext(previous);
                }
            }
        }
    }
}
