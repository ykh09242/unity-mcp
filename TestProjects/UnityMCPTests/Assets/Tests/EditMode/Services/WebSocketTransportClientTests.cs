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
