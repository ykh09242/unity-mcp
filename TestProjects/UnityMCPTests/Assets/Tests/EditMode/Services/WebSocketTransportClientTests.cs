using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.WebSockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class WebSocketTransportClientTests
    {
        private sealed class TimingSerializationProbe
        {
            internal int Reads;
            internal string Value = new string('x', 100000);
            public string Payload
            {
                get
                {
                    Reads++;
                    return Value;
                }
            }
        }

        private static WebSocketTransportClient CreateTimingClient(bool compression) =>
            new WebSocketTransportClient(
                null,
                new WebSocketTransportClient.OwnedConnectionOptions(
                    new Uri("ws://127.0.0.1:1"),
                    Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N"),
                    "core13",
                    "core13",
                    "core13-memory-only",
                    "fixture",
                    compression
                )
            );

        [TestCase(32, false, false)]
        [TestCase(300000, true, false)]
        [TestCase(400000, true, true)]
        public async Task TimedSuccessfulWriterPreservesEveryFrameAndSerializesOnce(int characters, bool negotiated, bool compression)
        {
            using var client = CreateTimingClient(compression);
            using var current = new CancellationTokenSource();
            SetField(client, "_connectionCts", current);
            SetField(client, "_largeResultNegotiated", negotiated);
            SetField(client, "_compressionNegotiated", compression);
            var expected = new List<Tuple<WebSocketMessageType, byte[]>>();
            var actual = new List<Tuple<WebSocketMessageType, byte[]>>();
            Task Record(List<Tuple<WebSocketMessageType, byte[]>> frames, ArraySegment<byte> bytes, WebSocketMessageType type)
            {
                var copy = new byte[bytes.Count];
                Buffer.BlockCopy(bytes.Array, bytes.Offset, copy, 0, bytes.Count);
                frames.Add(Tuple.Create(type, copy));
                return Task.CompletedTask;
            }
            string id = "f88b3f2a-ef07-4ad4-a9a6-1b9bdb5b9cb0";
            string value = new string('\u6f22', characters) + "\ud83d\ude80";
            string baseline = Newtonsoft.Json.JsonConvert.SerializeObject(
                new
                {
                    type = "command_result",
                    id,
                    result = new { Payload = value },
                }
            );
            await LargeResultWriter.SendJsonAsync(
                id,
                baseline,
                negotiated,
                (bytes, type, _) => Record(expected, bytes, type),
                current.Token,
                compression,
                compression
            );
            JObject row = null;
            var timing = new CommandPhaseTiming(() => 10, 1000, item => row = item, CommandPhaseTiming.Transport.WebSocket);
            var probe = new TimingSerializationProbe { Value = value };
            Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> writer = (bytes, type, _) => Record(actual, bytes, type);
            try
            {
                var send = typeof(WebSocketTransportClient).GetMethod("SendCommandResultCoreAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                await (Task)send.Invoke(client, new object[] { id, probe, current.Token, timing, writer });
                timing.ExecutionDone();
                timing.DeliveryDone(CommandPhaseTiming.Delivery.Sent);
                Assert.AreEqual(1, probe.Reads);
                Assert.AreEqual(expected.Count, actual.Count);
                for (int i = 0; i < expected.Count; i++)
                {
                    Assert.AreEqual(expected[i].Item1, actual[i].Item1);
                    CollectionAssert.AreEqual(expected[i].Item2, actual[i].Item2);
                }
                if (compression)
                    Assert.AreEqual("gzip", JObject.Parse(Encoding.UTF8.GetString(actual[0].Item2)).Value<string>("encoding"));
                if (negotiated)
                    Assert.Greater(actual.Count, 1);
                else
                    Assert.AreEqual(1, actual.Count);
                Assert.AreEqual("sent", row.Value<string>("delivery"));
                Assert.IsNotNull(row["send_ms"]);
                StringAssert.DoesNotContain(id, row.ToString());
                StringAssert.DoesNotContain("Payload", row.ToString());
            }
            finally
            {
                client.ForceStop();
            }
        }

        [Test]
        public void TimedResponseSerializesOnceAndCannotWriteToReplacementConnection()
        {
            using var client = CreateTimingClient(false);
            using var obsolete = new CancellationTokenSource();
            using var current = new CancellationTokenSource();
            using var replacement = new ClientWebSocket();
            SetField(client, "_connectionCts", current);
            SetField(client, "_socket", replacement);
            Newtonsoft.Json.Linq.JObject row = null;
            var timing = new MCPForUnity.Editor.Services.Transport.CommandPhaseTiming(
                () => 0,
                1000,
                value => row = value,
                MCPForUnity.Editor.Services.Transport.CommandPhaseTiming.Transport.WebSocket
            );
            var probe = new TimingSerializationProbe();
            try
            {
                var send = typeof(WebSocketTransportClient).GetMethod("SendTimedCommandResultAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                var error = Assert.Throws<TargetInvocationException>(() =>
                    send.Invoke(client, new object[] { "private-opaque-request", probe, obsolete.Token, timing })
                );
                Assert.IsInstanceOf<OperationCanceledException>(error.InnerException);
                timing.ExecutionDone();
                timing.DeliveryDone(MCPForUnity.Editor.Services.Transport.CommandPhaseTiming.Delivery.Canceled);
                Assert.AreEqual(1, probe.Reads, "Timing must not add a second result serialization.");
                Assert.AreEqual(WebSocketState.None, replacement.State);
                StringAssert.DoesNotContain("private-opaque-request", row.ToString());
                StringAssert.DoesNotContain("Payload", row.ToString());
                Assert.IsNull(row["send_ms"]);
            }
            finally
            {
                client.ForceStop();
            }
        }

        private const string CandidateBuilderMethodName = "BuildConnectionCandidateUris";
        private const string WebSocketTransportClientTypeName = "MCPForUnity.Editor.Services.Transport.Transports.WebSocketTransportClient";
        private static readonly MethodInfo BuildConnectionCandidateUrisMethod = ResolveCandidateBuilderMethod();

        [Test]
        public void SendFrameAsync_ObsoleteConnection_DoesNotUseReplacementSocket()
        {
            using var client = new WebSocketTransportClient();
            using var obsolete = new CancellationTokenSource();
            using var current = new CancellationTokenSource();
            using var replacement = new ClientWebSocket();
            SetField(client, "_connectionCts", current);
            SetField(client, "_socket", replacement);
            try
            {
                var send = typeof(WebSocketTransportClient).GetMethod("SendFrameAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                var result = (Task)send.Invoke(client, new object[] { new ArraySegment<byte>(new byte[] { 1 }), WebSocketMessageType.Text, obsolete.Token });
                Assert.IsTrue(result.IsCanceled, "A stale sender must be canceled before touching the replacement socket.");
                Assert.AreEqual(WebSocketState.None, replacement.State);
                Assert.IsFalse(current.IsCancellationRequested);
            }
            finally
            {
                client.ForceStop();
            }
        }

        [Test]
        public void AttemptReconnectAsync_CanceledOldLifecycle_DoesNotStopNewConnection()
        {
            using var client = new WebSocketTransportClient();
            using var oldLifecycle = new CancellationTokenSource();
            using var newLifecycle = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            oldLifecycle.Cancel();
            SetField(client, "_lifecycleCts", newLifecycle);
            SetField(client, "_connectionCts", newConnection);
            SetField(client, "_isReconnectingFlag", 1);
            try
            {
                var reconnect = typeof(WebSocketTransportClient).GetMethod("AttemptReconnectAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                var args = reconnect.GetParameters().Length == 1 ? new object[] { oldLifecycle.Token } : new object[] { oldLifecycle.Token, oldLifecycle };
                ((Task)reconnect.Invoke(client, args)).GetAwaiter().GetResult();
                Assert.IsFalse(newConnection.IsCancellationRequested, "A stale reconnect worker must not stop the replacement connection.");
                Assert.AreSame(newConnection, GetField(client, "_connectionCts"));
                Assert.AreEqual(1, GetField(client, "_isReconnectingFlag"), "An old reconnect must not clear the new session's guard.");
            }
            finally
            {
                client.ForceStop();
            }
        }

        [Test]
        public void HandleSocketClosureAsync_CanceledDuringLoopStop_ReportsDisconnected()
        {
            using var client = new WebSocketTransportClient();
            using var lifecycle = new CancellationTokenSource();
            using var connection = new CancellationTokenSource();
            using var registration = connection.Token.Register(() => lifecycle.Cancel());
            SetField(client, "_lifecycleCts", lifecycle);
            SetField(client, "_connectionCts", connection);
            SetField(client, "_isConnected", true);
            SetField(client, "_state", TransportState.Connected("websocket", sessionId: "old-session"));
            try
            {
                var closure = typeof(WebSocketTransportClient).GetMethod("HandleSocketClosureAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                ((Task)closure.Invoke(client, new object[] { "Server closed connection", connection.Token })).GetAwaiter().GetResult();
                Assert.IsFalse(client.IsConnected);
                Assert.IsFalse(client.State.IsConnected, "A reconnecting transport must not publish a connected state snapshot.");
                Assert.AreEqual("Server closed connection", client.State.Error);
                Assert.AreEqual(0, GetField(client, "_isReconnectingFlag"), "Canceled reconnect scheduling must release its guard.");
            }
            finally
            {
                client.ForceStop();
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void HandleSocketClosureAsync_OldConnectionError_PreservesReplacementSession(bool cancelOldConnection)
        {
            using var client = new WebSocketTransportClient();
            using var oldConnection = new CancellationTokenSource();
            using var newLifecycle = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            using var registration = newConnection.Token.Register(() => newLifecycle.Cancel());
            if (cancelOldConnection)
                oldConnection.Cancel();
            SetField(client, "_lifecycleCts", newLifecycle);
            SetField(client, "_connectionCts", newConnection);
            SetField(client, "_isConnected", true);
            SetField(client, "_state", TransportState.Connected("websocket", sessionId: "new-session"));
            try
            {
                var closure = typeof(WebSocketTransportClient).GetMethod("HandleSocketClosureAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                ((Task)closure.Invoke(client, new object[] { "Old socket was disposed", oldConnection.Token })).GetAwaiter().GetResult();
                Assert.AreSame(newConnection, GetField(client, "_connectionCts"));
                Assert.IsFalse(newConnection.IsCancellationRequested, "An old socket error must not cancel the replacement connection.");
                Assert.IsFalse(newLifecycle.IsCancellationRequested);
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("new-session", client.State.SessionId);
                Assert.AreEqual(0, GetField(client, "_isReconnectingFlag"));
            }
            finally
            {
                client.ForceStop();
            }
        }

        [Test]
        public void HandleSocketClosureAsync_ForceStopAndRestartAfterValidation_PreservesReplacementSession()
        {
            using var client = new WebSocketTransportClient();
            using var oldLifecycle = new CancellationTokenSource();
            using var oldConnection = new CancellationTokenSource();
            using var newLifecycle = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            using var reachedLog = new ManualResetEventSlim();
            using var resumeClosure = new ManualResetEventSlim();
            using var registration = newConnection.Token.Register(() => newLifecycle.Cancel());
            var logField = typeof(MCPForUnity.Editor.Helpers.McpLog).GetField("_debugEnabled", BindingFlags.Static | BindingFlags.NonPublic);
            bool previousDebug = (bool)logField.GetValue(null);
            logField.SetValue(null, true);
            SetField(client, "_lifecycleCts", oldLifecycle);
            SetField(client, "_connectionCts", oldConnection);
            SetField(client, "_isConnected", true);
            SetField(client, "_state", TransportState.Connected("websocket", sessionId: "old-session"));
            UnityEngine.Application.LogCallback pauseClosure = (message, trace, type) =>
            {
                if (!message.Contains("HandleSocketClosureAsync called. Reason: overlap-old"))
                    return;
                reachedLog.Set();
                resumeClosure.Wait(TimeSpan.FromSeconds(5));
            };
            UnityEngine.Application.logMessageReceivedThreaded += pauseClosure;
            Task closureTask = null;
            try
            {
                var closure = typeof(WebSocketTransportClient).GetMethod("HandleSocketClosureAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                var oldToken = oldConnection.Token;
                closureTask = Task.Run(async () => await (Task)closure.Invoke(client, new object[] { "overlap-old", oldToken }));
                Assert.IsTrue(reachedLog.Wait(TimeSpan.FromSeconds(5)), "Pause the actual background callback after its ownership validation.");
                client.ForceStop();
                var restartLock =
                    typeof(WebSocketTransportClient).GetField("_ownershipLock", BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(client)
                    ?? new object();
                lock (restartLock)
                {
                    SetField(client, "_lifecycleCts", newLifecycle);
                    SetField(client, "_connectionCts", newConnection);
                    SetField(client, "_isConnected", true);
                    SetField(client, "_state", TransportState.Connected("websocket", sessionId: "new-session"));
                }
                resumeClosure.Set();
                Assert.IsTrue(SpinWait.SpinUntil(() => closureTask.IsCompleted, TimeSpan.FromSeconds(5)));
                closureTask.GetAwaiter().GetResult();
                Assert.AreSame(newConnection, GetField(client, "_connectionCts"));
                Assert.AreSame(newLifecycle, GetField(client, "_lifecycleCts"));
                Assert.IsFalse(newConnection.IsCancellationRequested);
                Assert.IsFalse(newLifecycle.IsCancellationRequested);
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("new-session", client.State.SessionId);
            }
            finally
            {
                resumeClosure.Set();
                if (closureTask != null)
                    SpinWait.SpinUntil(() => closureTask.IsCompleted, TimeSpan.FromSeconds(5));
                UnityEngine.Application.logMessageReceivedThreaded -= pauseClosure;
                logField.SetValue(null, previousDebug);
                client.ForceStop();
            }
        }

        [TestCase("welcome")]
        [TestCase("registered")]
        public void HandleMessageAsync_ObsoleteConnection_PreservesCurrentSessionAndNegotiatedIntervals(string messageType)
        {
            using var client = new WebSocketTransportClient();
            using var oldConnection = new CancellationTokenSource();
            using var newLifecycle = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            var previousCallbacks = EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>();
            SetField(client, "_lifecycleCts", newLifecycle);
            SetField(client, "_connectionCts", newConnection);
            SetField(client, "_endpointUri", new Uri("ws://127.0.0.1:9100/hub/plugin"));
            SetField(client, "_sessionId", "new-session");
            SetField(client, "_state", TransportState.Connected("websocket", sessionId: "new-session"));
            try
            {
                var payload = new JObject
                {
                    ["type"] = messageType,
                    ["session_id"] = "old-session",
                    ["keepAliveInterval"] = 90,
                    ["serverTimeout"] = 120,
                };
                var handler = typeof(WebSocketTransportClient).GetMethod("HandleMessageAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                ((Task)handler.Invoke(client, new object[] { payload.ToString(), oldConnection.Token })).GetAwaiter().GetResult();
                Assert.AreEqual("new-session", GetField(client, "_sessionId"));
                Assert.AreEqual("new-session", client.State.SessionId);
                Assert.AreEqual(TimeSpan.FromSeconds(15), GetField(client, "_keepAliveInterval"));
                Assert.AreEqual(TimeSpan.FromSeconds(15), GetField(client, "_socketKeepAliveInterval"));
                CollectionAssert.AreEqual(
                    previousCallbacks,
                    EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>(),
                    "An obsolete registration must not enqueue a session preference write."
                );
            }
            finally
            {
                RemoveAddedDelayCallbacks(previousCallbacks);
                client.ForceStop();
            }
        }

        [Test]
        public void RegisteredSession_RestartBeforeDeferredPersistence_PreservesReplacementPreference()
        {
            using var client = new WebSocketTransportClient();
            using var oldLifecycle = new CancellationTokenSource();
            using var oldConnection = new CancellationTokenSource();
            using var newLifecycle = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            string preferenceKey = $"{EditorPrefKeys.SessionId}_{ProjectIdentityUtility.GetProjectHash()}";
            bool hadPreference = EditorPrefs.HasKey(preferenceKey);
            string previousPreference = EditorPrefs.GetString(preferenceKey, string.Empty);
            var previousCallbacks = EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>();
            SetField(client, "_lifecycleCts", oldLifecycle);
            SetField(client, "_connectionCts", oldConnection);
            SetField(client, "_endpointUri", new Uri("ws://127.0.0.1:9000/hub/plugin"));
            try
            {
                var registered = typeof(WebSocketTransportClient).GetMethod("HandleRegisteredAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                (
                    (Task)
                        registered.Invoke(
                            client,
                            new object[]
                            {
                                new JObject { ["session_id"] = "old-session" },
                                oldConnection.Token,
                            }
                        )
                ).GetAwaiter().GetResult();
                var callbacks = (EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>()).Except(previousCallbacks).ToArray();
                Assert.AreEqual(1, callbacks.Length);
                Assert.AreEqual("old-session", client.State.SessionId, "The current owner must still publish registration.");
                client.ForceStop();
                lock (GetField(client, "_ownershipLock"))
                {
                    SetField(client, "_lifecycleCts", newLifecycle);
                    SetField(client, "_connectionCts", newConnection);
                    SetField(client, "_sessionId", "new-session");
                }
                EditorPrefs.SetString(preferenceKey, "new-session");
                foreach (var callback in callbacks)
                    ((EditorApplication.CallbackFunction)callback)();
                Assert.AreEqual("new-session", EditorPrefs.GetString(preferenceKey));
            }
            finally
            {
                RemoveAddedDelayCallbacks(previousCallbacks);
                if (hadPreference)
                    EditorPrefs.SetString(preferenceKey, previousPreference);
                else
                    EditorPrefs.DeleteKey(preferenceKey);
                client.ForceStop();
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TryPublishConnectedEndpoint_RequiresExactCurrentConnection(bool currentOwner)
        {
            using var client = new WebSocketTransportClient();
            using var oldConnection = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            var currentEndpoint = new Uri("ws://localhost:9100/hub/plugin");
            var oldFallback = new Uri("ws://127.0.0.1:9000/hub/plugin");
            SetField(client, "_connectionCts", newConnection);
            SetField(client, "_endpointUri", currentEndpoint);
            var publish = typeof(WebSocketTransportClient).GetMethod("TryPublishConnectedEndpoint", BindingFlags.Instance | BindingFlags.NonPublic);
            bool published = (bool)publish.Invoke(client, new object[] { currentOwner ? newConnection.Token : oldConnection.Token, oldFallback });
            Assert.AreEqual(currentOwner, published);
            Assert.AreEqual(currentOwner ? oldFallback : currentEndpoint, GetField(client, "_endpointUri"));
        }

        private static void RemoveAddedDelayCallbacks(Delegate[] previousCallbacks)
        {
            var callbacks = EditorApplication.delayCall?.GetInvocationList() ?? Array.Empty<Delegate>();
            foreach (var callback in callbacks.Except(previousCallbacks))
                EditorApplication.delayCall -= (EditorApplication.CallbackFunction)callback;
        }

        [Test]
        public void StopAsync_ForceStopWhileReceivePending_CompletesWithoutFault()
        {
            using var client = new WebSocketTransportClient();
            using var lifecycle = new CancellationTokenSource();
            var receive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            SetField(client, "_lifecycleCts", lifecycle);
            SetField(client, "_receiveTask", receive.Task);
            Task stop = client.StopAsync();
            Assert.IsFalse(stop.IsCompleted, "Shutdown should wait for the pending receive loop.");
            client.ForceStop();
            receive.SetResult(true);

            Assert.IsTrue(SpinWait.SpinUntil(() => stop.IsCompleted, TimeSpan.FromSeconds(5)));
            Assert.IsFalse(stop.IsFaulted, stop.Exception?.GetBaseException().Message);
            Assert.IsFalse(client.IsConnected);
        }

        [Test]
        public void StopAsync_NewLifecycleWhileOldReceivePending_PreservesNewSession()
        {
            using var client = new WebSocketTransportClient();
            using var oldLifecycle = new CancellationTokenSource();
            using var newLifecycle = new CancellationTokenSource();
            using var oldConnection = new CancellationTokenSource();
            using var newConnection = new CancellationTokenSource();
            using var newSocket = new ClientWebSocket();
            var oldReceive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var newReceive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var newKeepAlive = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            SetField(client, "_lifecycleCts", oldLifecycle);
            SetField(client, "_connectionCts", oldConnection);
            SetField(client, "_receiveTask", oldReceive.Task);
            Task stop = client.StopAsync();
            SetField(client, "_lifecycleCts", newLifecycle);
            SetField(client, "_connectionCts", newConnection);
            SetField(client, "_receiveTask", newReceive.Task);
            SetField(client, "_keepAliveTask", newKeepAlive.Task);
            SetField(client, "_socket", newSocket);
            SetField(client, "_isConnected", true);
            SetField(client, "_state", TransportState.Connected("websocket", sessionId: "new-session"));
            try
            {
                oldReceive.SetResult(true);
                Assert.IsTrue(
                    SpinWait.SpinUntil(() => stop.IsCompleted, TimeSpan.FromSeconds(5)),
                    "Old shutdown must not wait for loops from a replacement session."
                );
                Assert.IsFalse(stop.IsFaulted, stop.Exception?.GetBaseException().Message);
                Assert.AreSame(newLifecycle, GetField(client, "_lifecycleCts"));
                Assert.AreSame(newConnection, GetField(client, "_connectionCts"));
                Assert.AreSame(newReceive.Task, GetField(client, "_receiveTask"));
                Assert.AreSame(newKeepAlive.Task, GetField(client, "_keepAliveTask"));
                Assert.AreSame(newSocket, GetField(client, "_socket"));
                Assert.IsFalse(newLifecycle.IsCancellationRequested);
                Assert.IsFalse(newConnection.IsCancellationRequested);
                Assert.IsTrue(client.IsConnected);
                Assert.AreEqual("new-session", client.State.SessionId);
            }
            finally
            {
                newReceive.TrySetResult(true);
                newKeepAlive.TrySetResult(true);
                SpinWait.SpinUntil(() => stop.IsCompleted, TimeSpan.FromSeconds(5));
                client.ForceStop();
            }
        }

        private static void SetField(WebSocketTransportClient client, string name, object value) =>
            typeof(WebSocketTransportClient).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(client, value);

        private static object GetField(WebSocketTransportClient client, string name) =>
            typeof(WebSocketTransportClient).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(client);

        [Test]
        public void BuildConnectionCandidateUris_NullEndpoint_ReturnsEmptyList()
        {
            // Act
            List<Uri> candidates = InvokeBuildConnectionCandidateUris(null);

            // Assert
            Assert.IsNotNull(candidates);
            Assert.AreEqual(0, candidates.Count);
        }

        [Test]
        public void BuildConnectionCandidateUris_NonLocalhost_ReturnsOriginalOnly()
        {
            // Arrange
            var endpoint = new Uri("ws://127.0.0.1:8080/hub/plugin");

            // Act
            List<Uri> candidates = InvokeBuildConnectionCandidateUris(endpoint);

            // Assert
            Assert.AreEqual(1, candidates.Count);
            Assert.AreEqual(endpoint, candidates[0]);
        }

        [Test]
        public void BuildConnectionCandidateUris_Localhost_AddsIPv4AndIPv6Fallbacks()
        {
            // Arrange
            var endpoint = new Uri("ws://localhost:8080/hub/plugin");

            // Act
            List<Uri> candidates = InvokeBuildConnectionCandidateUris(endpoint);

            // Assert
            Assert.AreEqual(3, candidates.Count);
            CollectionAssert.AreEqual(new[] { "localhost", "127.0.0.1", "::1" }, candidates.Select(uri => NormalizeHostForComparison(uri.Host)).ToArray());

            int uniqueCount = candidates.Select(uri => uri.AbsoluteUri).Distinct(StringComparer.OrdinalIgnoreCase).Count();
            Assert.AreEqual(candidates.Count, uniqueCount, "Fallback list should not contain duplicate endpoints.");
        }

        [Test]
        public void BuildConnectionCandidateUris_LocalhostFallbacks_PreserveSchemePortPathAndQuery()
        {
            // Arrange
            var endpoint = new Uri("wss://localhost:9443/custom/path?mode=test");

            // Act
            List<Uri> candidates = InvokeBuildConnectionCandidateUris(endpoint);

            // Assert
            Assert.AreEqual(3, candidates.Count);
            foreach (Uri candidate in candidates)
            {
                Assert.AreEqual("wss", candidate.Scheme);
                Assert.AreEqual(9443, candidate.Port);
                Assert.AreEqual("/custom/path", candidate.AbsolutePath);
                Assert.AreEqual("?mode=test", candidate.Query);
            }
        }

        private static List<Uri> InvokeBuildConnectionCandidateUris(Uri endpoint)
        {
            if (BuildConnectionCandidateUrisMethod == null)
            {
                Assert.Fail(BuildMissingMethodDiagnostic());
            }
            var result = BuildConnectionCandidateUrisMethod.Invoke(null, new object[] { endpoint });
            Assert.IsNotNull(result);
            Assert.IsInstanceOf<List<Uri>>(result);
            return (List<Uri>)result;
        }

        private static MethodInfo ResolveCandidateBuilderMethod()
        {
            MethodInfo direct = GetCandidateBuilderMethod(typeof(WebSocketTransportClient));
            if (direct != null)
            {
                return direct;
            }

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type candidateType = assembly.GetType(WebSocketTransportClientTypeName);
                if (candidateType == null)
                {
                    continue;
                }

                MethodInfo method = GetCandidateBuilderMethod(candidateType);
                if (method != null)
                {
                    return method;
                }
            }

            return null;
        }

        private static MethodInfo GetCandidateBuilderMethod(Type type)
        {
            const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static;
            MethodInfo direct = type.GetMethod(CandidateBuilderMethodName, flags, binder: null, types: new[] { typeof(Uri) }, modifiers: null);
            if (direct != null)
            {
                return direct;
            }

            // Fallback for environments where signature binding can differ between loaded copies.
            return type.GetMethods(flags)
                .FirstOrDefault(method =>
                {
                    if (!string.Equals(method.Name, CandidateBuilderMethodName, StringComparison.Ordinal))
                    {
                        return false;
                    }

                    ParameterInfo[] parameters = method.GetParameters();
                    return parameters.Length == 1 && parameters[0].ParameterType == typeof(Uri);
                });
        }

        private static string BuildMissingMethodDiagnostic()
        {
            var sb = new StringBuilder();
            sb.Append("Expected private candidate builder method to exist. Searched loaded assemblies for ")
                .Append(WebSocketTransportClientTypeName)
                .Append('.')
                .Append(CandidateBuilderMethodName)
                .Append(". Loaded candidate types:");

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type candidateType = assembly.GetType(WebSocketTransportClientTypeName);
                if (candidateType == null)
                {
                    continue;
                }

                sb.Append("\n- ").Append(assembly.FullName).Append(" @ ").Append(string.IsNullOrEmpty(assembly.Location) ? "<dynamic>" : assembly.Location);
            }

            return sb.ToString();
        }

        [Test]
        public async Task CommandProgress_NegotiatedBatchReportsBeforeGatedSecondAndKeepsFifo()
        {
            RequireOwnedProgressEditor();
            const string first = "__core06_ws_first",
                second = "__core06_ws_second",
                queued = "__core06_ws_queued";
            using var handlers = new ProgressHandlerScope();
            using var settings = NativeProgressProbe.EnableTools("batch_execute");
            var gate = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int firstCalls = 0,
                secondCalls = 0,
                queuedCalls = 0;
            handlers.Set(
                first,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    first,
                    _ =>
                    {
                        firstCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                second,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    second,
                    null,
                    async _ =>
                    {
                        secondCalls++;
                        entered.TrySetResult(true);
                        return await gate.Task.ConfigureAwait(true);
                    }
                )
            );
            handlers.Set(
                queued,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    queued,
                    _ =>
                    {
                        queuedCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            using var peer = new ProgressWirePeer();
            using var client = peer.CreateClient();
            string id = Guid.NewGuid().ToString("D"),
                queuedId = Guid.NewGuid().ToString("D");
            try
            {
                Assert.IsTrue(await client.StartAsync());
                JObject registration = await peer.Negotiate(true);
                peer.Execute(
                    id,
                    "batch_execute",
                    new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = second }) },
                    true
                );
                await NativeProgressProbe.AwaitOwned(entered.Task);
                peer.Execute(queuedId, queued, new JObject(), false);
                var update = await peer.Message(message =>
                    message.Value<string>("id") == id
                    && (message.Value<string>("type") == "command_progress" || message.Value<string>("type") == "command_result")
                );
                Assert.AreEqual("command_progress", update.Value<string>("type"), "The real settled1/2 frame must precede the held final result.");
                CollectionAssert.AreEquivalent(new[] { "type", "id", "completed", "total", "stage" }, update.Properties().Select(property => property.Name));
                Assert.AreEqual(id, update.Value<string>("id"));
                Assert.AreEqual(1, update.Value<int>("completed"));
                Assert.AreEqual(2, update.Value<int>("total"));
                Assert.AreEqual("batch_items", update.Value<string>("stage"));
                Assert.LessOrEqual(Encoding.UTF8.GetByteCount(update.ToString(Newtonsoft.Json.Formatting.None)), 512);
                Assert.AreEqual(1, firstCalls);
                Assert.AreEqual(1, secondCalls);
                Assert.AreEqual(0, queuedCalls, "Progress does not split the admitted batch or release its FIFO owner.");
                CollectionAssert.Contains(registration["capabilities"].Values<string>().ToArray(), "command_progress_v1");
                gate.TrySetResult(new { done = true });
                int lastCompleted = 1;
                JObject result;
                while (true)
                {
                    result = await peer.Message(message => message.Value<string>("id") == id);
                    if (result.Value<string>("type") == "command_result")
                        break;
                    Assert.AreEqual("command_progress", result.Value<string>("type"));
                    Assert.Greater(result.Value<int>("completed"), lastCompleted);
                    lastCompleted = result.Value<int>("completed");
                    Assert.LessOrEqual(lastCompleted, 2);
                    Assert.AreEqual(2, result.Value<int>("total"));
                }
                Assert.AreEqual(2, result.SelectToken("result.result.data.attemptedCount").Value<int>());
                Assert.AreEqual(0, result.SelectToken("result.result.data.notRunCount").Value<int>());
                Assert.IsNotNull(await peer.Result(queuedId));
                Assert.AreEqual(1, queuedCalls);
                await Task.Delay(200);
                await peer.Barrier();
                Assert.IsFalse(
                    peer.Remaining().Any(message => message.Value<string>("id") == id),
                    "No optional frame or second final response may follow the terminal response."
                );
                peer.AssertHealthy();
            }
            finally
            {
                gate.TrySetResult(new { done = true });
                await StopOwnedProgressClient(client);
            }
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        public async Task CommandProgress_NoOptInOrOldPeerPreservesOrdinaryResult(bool supported, bool requested)
        {
            RequireOwnedProgressEditor();
            const string child = "__core06_ws_compat";
            using var handlers = new ProgressHandlerScope();
            using var settings = NativeProgressProbe.EnableTools("batch_execute");
            int calls = 0;
            handlers.Set(
                child,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    child,
                    _ =>
                    {
                        calls++;
                        return new { stable = "unchanged" };
                    },
                    null
                )
            );
            using var peer = new ProgressWirePeer();
            using var client = peer.CreateClient();
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.Negotiate(supported);
                var parameters = new JObject { ["commands"] = new JArray(new JObject { ["tool"] = child }) };
                string baselineId = Guid.NewGuid().ToString("D"),
                    id = Guid.NewGuid().ToString("D");
                peer.Execute(baselineId, "batch_execute", parameters, false);
                var baseline = await peer.Result(baselineId);
                peer.Execute(id, "batch_execute", parameters, requested);
                var result = await peer.Result(id);
                Assert.IsTrue(JToken.DeepEquals(baseline["result"], result["result"]), "Optional progress cannot change any ordinary business result field.");
                CollectionAssert.AreEquivalent(new[] { "type", "id", "result" }, result.Properties().Select(property => property.Name));
                Assert.AreEqual(2, calls);
                await peer.Barrier();
                Assert.IsFalse(peer.Remaining().Any(message => message.Value<string>("type") == "command_progress"));
                peer.AssertHealthy();
            }
            finally
            {
                await StopOwnedProgressClient(client);
            }
        }

        [Test]
        public async Task CommandProgress_CancelClosesForegroundBeforeLegacyChildSettles()
        {
            RequireOwnedProgressEditor();
            const string first = "__core06_ws_cancel_first",
                held = "__core06_ws_cancel_held",
                next = "__core06_ws_cancel_next";
            using var handlers = new ProgressHandlerScope();
            using var settings = NativeProgressProbe.EnableTools("batch_execute");
            var gate = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int activeCalls = 0,
                nextCalls = 0;
            handlers.Set(first, new MCPForUnity.Editor.Tools.HandlerInfo(first, _ => new { done = true }, null));
            handlers.Set(
                held,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    held,
                    null,
                    async _ =>
                    {
                        activeCalls++;
                        entered.TrySetResult(true);
                        return await gate.Task.ConfigureAwait(true);
                    }
                )
            );
            handlers.Set(
                next,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    next,
                    _ =>
                    {
                        nextCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            using var peer = new ProgressWirePeer();
            using var client = peer.CreateClient();
            string id = Guid.NewGuid().ToString("D"),
                nextId = Guid.NewGuid().ToString("D");
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.Negotiate(true);
                peer.Execute(
                    id,
                    "batch_execute",
                    new JObject { ["commands"] = new JArray(new JObject { ["tool"] = first }, new JObject { ["tool"] = held }) },
                    true
                );
                await NativeProgressProbe.AwaitOwned(entered.Task);
                var update = await peer.Message(message => message.Value<string>("type") == "command_progress" && message.Value<string>("id") == id);
                Assert.AreEqual(1, update.Value<int>("completed"));
                peer.Send(new JObject { ["type"] = "cancel", ["id"] = id });
                var cancelled = await peer.Result(id);
                Assert.AreEqual("error", cancelled["result"].Value<string>("status"));
                Assert.That(cancelled["result"].Value<string>("error"), Does.Contain("canceled"));
                Assert.IsFalse(gate.Task.IsCompleted, "Foreground cancellation cannot pretend the legacy mutation settled.");
                peer.Execute(nextId, next, new JObject(), false);
                await peer.Barrier();
                Assert.AreEqual(0, nextCalls);
                gate.TrySetResult(new { done = true });
                await peer.Result(nextId);
                await peer.Barrier();
                Assert.AreEqual(1, activeCalls);
                Assert.AreEqual(1, nextCalls);
                Assert.IsFalse(
                    peer.Remaining().Any(message => message.Value<string>("id") == id),
                    "Neither late progress nor a second final response may follow foreground cancellation."
                );
                peer.AssertHealthy();
            }
            finally
            {
                gate.TrySetResult(new { done = true });
                await StopOwnedProgressClient(client);
            }
        }

        [Test]
        public async Task CommandProgress_BusyOptionalWriterDoesNotBlockItemsOrChangeFinalResult()
        {
            RequireOwnedProgressEditor();
            const string child = "__core06_ws_busy_writer";
            using var handlers = new ProgressHandlerScope();
            using var settings = NativeProgressProbe.EnableTools("batch_execute");
            var gate = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var settled = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int calls = 0;
            handlers.Set(
                child,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    child,
                    null,
                    async _ =>
                    {
                        if (++calls < 20)
                            return new { done = true };
                        entered.TrySetResult(true);
                        var result = await gate.Task.ConfigureAwait(true);
                        settled.TrySetResult(true);
                        return result;
                    }
                )
            );
            using var peer = new ProgressWirePeer();
            using var client = peer.CreateClient();
            SemaphoreSlim writer = null;
            bool ownsWriter = false;
            string id = Guid.NewGuid().ToString("D");
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.Negotiate(true);
                writer = (SemaphoreSlim)GetField(client, "_sendLock");
                await NativeProgressProbe.AwaitOwned(writer.WaitAsync());
                ownsWriter = true;
                var commands = new JArray();
                for (int i = 0; i < 20; i++)
                    commands.Add(new JObject { ["tool"] = child });
                peer.Execute(id, "batch_execute", new JObject { ["commands"] = commands }, true);
                await NativeProgressProbe.AwaitOwned(entered.Task);
                Assert.AreEqual(20, calls, "Optional writer backpressure must not block the native batch loop.");
                gate.TrySetResult(new { done = true });
                await NativeProgressProbe.AwaitOwned(settled.Task);
                writer.Release();
                ownsWriter = false;
                int previous = 0,
                    optionalFrames = 0;
                JObject final;
                while (true)
                {
                    final = await peer.Message(message => message.Value<string>("id") == id);
                    if (final.Value<string>("type") == "command_result")
                        break;
                    Assert.AreEqual("command_progress", final.Value<string>("type"));
                    Assert.Greater(final.Value<int>("completed"), previous);
                    Assert.AreEqual(20, final.Value<int>("total"));
                    previous = final.Value<int>("completed");
                    Assert.LessOrEqual(previous, 20);
                    Assert.LessOrEqual(++optionalFrames, 2, "A blocked burst must coalesce instead of queuing one send per item.");
                }
                Assert.AreEqual(20, final.SelectToken("result.result.data.attemptedCount").Value<int>());
                Assert.AreEqual(20, calls);
                await Task.Delay(200);
                await peer.Barrier();
                Assert.IsFalse(peer.Remaining().Any(message => message.Value<string>("id") == id));
                peer.AssertHealthy();
            }
            finally
            {
                gate.TrySetResult(new { done = true });
                if (ownsWriter)
                    writer.Release();
                await StopOwnedProgressClient(client);
            }
        }

        [Test]
        public async Task CommandProgress_ObsoleteExecuteCannotRunOrSubscribeToCurrentOwner()
        {
            RequireOwnedProgressEditor();
            const string name = "__core06_ws_obsolete",
                barrier = "__core06_ws_current";
            using var handlers = new ProgressHandlerScope();
            int obsoleteCalls = 0,
                currentCalls = 0;
            handlers.Set(
                name,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    name,
                    _ =>
                    {
                        obsoleteCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            handlers.Set(
                barrier,
                new MCPForUnity.Editor.Tools.HandlerInfo(
                    barrier,
                    _ =>
                    {
                        currentCalls++;
                        return new { done = true };
                    },
                    null
                )
            );
            using var peer = new ProgressWirePeer();
            using var client = peer.CreateClient();
            using var obsolete = new CancellationTokenSource();
            string id = Guid.NewGuid().ToString("D"),
                barrierId = Guid.NewGuid().ToString("D");
            try
            {
                Assert.IsTrue(await client.StartAsync());
                await peer.Negotiate(true);
                Assert.IsNotNull(GetField(client, "_commandWork"), "A missing command owner would make the obsolete-execute oracle vacuous.");
                var execute = typeof(WebSocketTransportClient).GetMethod("HandleExecuteAsync", BindingFlags.Instance | BindingFlags.NonPublic);
                var payload = new JObject
                {
                    ["type"] = "execute",
                    ["id"] = id,
                    ["name"] = name,
                    ["params"] = new JObject(),
                    ["progress"] = new JObject { ["version"] = 1 },
                };
                await NativeProgressProbe.AwaitOwned((Task)execute.Invoke(client, new object[] { payload, obsolete.Token }));
                peer.Execute(barrierId, barrier, new JObject(), false);
                await peer.Result(barrierId);
                Assert.AreEqual(1, currentCalls, "The live connection and its FIFO owner must still execute real work.");
                Assert.AreEqual(0, obsoleteCalls, "A stale token cannot enter the live owner's FIFO or acquire a reporter.");
                Assert.IsFalse(peer.Remaining().Any(message => message.Value<string>("id") == id));
                peer.AssertHealthy();
            }
            finally
            {
                await StopOwnedProgressClient(client);
            }
        }

        private static void RequireOwnedProgressEditor()
        {
            if (Environment.GetEnvironmentVariable("UNITY_MCP_OWNED_TRANSPORT_TESTS") != "1")
                Assert.Ignore("CORE06 loopback tests require the existing isolated owned-transport Editor lane.");
            Assert.IsTrue(UnityEngine.Application.isBatchMode);
            Assert.IsTrue(string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH")));
            Assert.IsFalse(StdioBridgeHost.IsRunning, "These tests must not share a global bridge.");
        }

        private static async Task StopOwnedProgressClient(WebSocketTransportClient client)
        {
            Task stop = client.StopAsync();
            if (await Task.WhenAny(stop, Task.Delay(5000)) != stop)
            {
                client.ForceStop();
                await NativeProgressProbe.AwaitOwned(stop);
                Assert.Fail("Owned WebSocket client required forced cleanup after its released child.");
            }
            await stop;
        }

        // Reuse the existing owned RFC6455 peer without changing its non-owned fixture.
        // This adapter contains no socket implementation and owns only that peer instance.
        private sealed class ProgressWirePeer : IDisposable
        {
            private const string Token = "owned-core06-progress-synthetic-token-only";
            private readonly Type _type = typeof(TransportEditorIntegrationTests).GetNestedType("OwnedWebSocketPeer", BindingFlags.NonPublic);
            private readonly object _peer;

            internal ProgressWirePeer() => _peer = Activator.CreateInstance(_type, new object[] { Token, false, false });

            private object Call(string name, params object[] arguments) =>
                _type.GetMethod(name, BindingFlags.Public | BindingFlags.Instance).Invoke(_peer, arguments);

            internal WebSocketTransportClient CreateClient() =>
                new WebSocketTransportClient(
                    null,
                    new WebSocketTransportClient.OwnedConnectionOptions(
                        (Uri)_type.GetProperty("Uri").GetValue(_peer),
                        Token,
                        "Core06Progress",
                        new string('6', 40),
                        "/owned/core06",
                        "6000.0"
                    )
                );

            internal void Send(JObject message) => Call("Send", message);

            internal Task<JObject> Message(Func<JObject, bool> predicate) => (Task<JObject>)Call("Message", predicate);

            internal Task<JObject> Result(string id) => (Task<JObject>)Call("Result", id);

            internal async Task<JObject> Negotiate(bool supported)
            {
                await (Task)Call("WaitRegistered", 1);
                JObject registration = await Message(message => message.Value<string>("type") == "register");
                var capabilities = new JArray("large_result_v1", "command_cancel_v1");
                if (supported)
                    capabilities.Add("command_progress_v1");
                Send(new JObject { ["type"] = "welcome", ["capabilities"] = capabilities.DeepClone() });
                Send(
                    new JObject
                    {
                        ["type"] = "registered",
                        ["session_id"] = "owned-editor-session-1",
                        ["capabilities"] = capabilities,
                    }
                );
                await Barrier();
                return registration;
            }

            internal void Execute(string id, string name, JObject parameters, bool requested)
            {
                if (name == "batch_execute")
                    NativeProgressProbe.RequireBatchCapacity(parameters);
                var message = new JObject
                {
                    ["type"] = "execute",
                    ["id"] = id,
                    ["name"] = name,
                    ["params"] = parameters,
                    ["timeout"] = 30,
                };
                if (requested)
                    message["progress"] = new JObject { ["version"] = 1 };
                Send(message);
            }

            internal async Task Barrier()
            {
                Send(new JObject { ["type"] = "ping" });
                await Message(message => message.Value<string>("type") == "pong");
            }

            internal JObject[] Remaining()
            {
                var queued = (IEnumerable<JObject>)_type.GetField("_messages", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(_peer);
                var collected = (IEnumerable<JObject>)_type.GetField("_collected", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(_peer);
                return collected.Concat(queued).ToArray();
            }

            internal void AssertHealthy()
            {
                var error = _type.GetField("_error", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(_peer);
                Assert.IsNull(error, "Owned loopback peer must not encounter malformed/interleaved framing.");
                Assert.IsTrue((bool)_type.GetField("AllAuthHeadersMatch").GetValue(_peer));
            }

            public void Dispose() => ((IDisposable)_peer).Dispose();
        }

        private static string NormalizeHostForComparison(string host)
        {
            if (string.IsNullOrEmpty(host))
            {
                return host;
            }

            return host.Trim('[', ']');
        }
    }
}
