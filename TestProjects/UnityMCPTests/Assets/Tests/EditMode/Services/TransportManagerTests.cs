using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// Pins TransportManager.StartAsync's coalescing contract: concurrent starts for the
    /// same mode share one in-flight attempt instead of racing — a second StartAsync would
    /// otherwise tear down the first connection mid-handshake (manual Connect vs the
    /// reload-resume/auto-start loops).
    /// </summary>
    public class TransportManagerTests
    {
        private sealed class ControlledClient : IMcpTransportClient
        {
            public readonly List<TaskCompletionSource<bool>> Starts = new List<TaskCompletionSource<bool>>();
            public int StopCalls;
            public TaskCompletionSource<bool> PendingStop;
            public TransportState State { get; set; } = TransportState.Disconnected("controlled");
            public bool IsConnected => State.IsConnected;
            public string TransportName => "controlled";

            public Task<bool> StartAsync()
            {
                var completion = new TaskCompletionSource<bool>();
                Starts.Add(completion);
                return completion.Task;
            }

            public Task StopAsync()
            {
                StopCalls++;
                return PendingStop?.Task ?? Task.CompletedTask;
            }

            public Task<bool> VerifyAsync() => Task.FromResult(IsConnected);

            public Task ReregisterToolsAsync() => Task.CompletedTask;
        }

        [TestCase(TransportMode.Http, false)]
        [TestCase(TransportMode.Http, true)]
        [TestCase(TransportMode.Stdio, false)]
        [TestCase(TransportMode.Stdio, true)]
        public void StopThenStart_RetiredCompletionDoesNotStopReplacement(TransportMode mode, bool oldSuccess)
        {
            var client = new ControlledClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            Task<bool> oldStart = manager.StartAsync(mode);
            manager.StopAsync(mode).GetAwaiter().GetResult();
            Task<bool> replacement = manager.StartAsync(mode);
            Assert.AreNotSame(oldStart, replacement);
            Assert.AreEqual(2, client.Starts.Count);
            Assert.AreSame(replacement, manager.StartAsync(mode), "the replacement intent still coalesces");

            client.State = TransportState.Connected("controlled", sessionId: "replacement");
            client.Starts[1].SetResult(true);
            client.Starts[0].SetResult(oldSuccess);

            Assert.IsFalse(oldStart.Result);
            Assert.IsTrue(replacement.Result);
            Assert.AreEqual(1, client.StopCalls, "a retired failure must not stop the replacement");
            Assert.IsTrue(manager.HasConnectionIntent(mode));
            Assert.IsTrue(manager.IsRunning(mode));
        }

        [Test]
        public void StopAll_RetiresBothPendingStartsBeforeAwaitingStop()
        {
            var http = new ControlledClient();
            var stdio = new ControlledClient();
            http.PendingStop = new TaskCompletionSource<bool>();
            var manager = new TransportManager();
            manager.Configure(() => http, () => stdio);
            Task<bool> oldHttp = manager.StartAsync(TransportMode.Http);
            Task<bool> oldStdio = manager.StartAsync(TransportMode.Stdio);
            Task stop = manager.StopAsync();
            Assert.IsFalse(stop.IsCompleted);
            Assert.AreEqual(1, stdio.StopCalls, "both modes must be stopped before awaiting either one");
            Task<bool> nextHttp = manager.StartAsync(TransportMode.Http);
            Task<bool> nextStdio = manager.StartAsync(TransportMode.Stdio);
            Assert.AreNotSame(oldHttp, nextHttp);
            Assert.AreNotSame(oldStdio, nextStdio);
            http.Starts[0].SetResult(false);
            stdio.Starts[0].SetResult(false);
            http.Starts[1].SetResult(true);
            stdio.Starts[1].SetResult(true);
            http.PendingStop.SetResult(true);
            Assert.IsTrue(stop.IsCompleted);
            Assert.AreEqual(1, http.StopCalls);
            Assert.AreEqual(1, stdio.StopCalls);
        }

        [Test]
        public void FailedStartException_CleansUpIntent()
        {
            var client = new ControlledClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            Task<bool> start = manager.StartAsync(TransportMode.Http);
            client.Starts[0].SetException(new InvalidOperationException("start failed"));
            Assert.Throws<InvalidOperationException>(() => start.GetAwaiter().GetResult());
            Assert.AreEqual(1, client.StopCalls);
            Assert.IsFalse(manager.HasConnectionIntent(TransportMode.Http));
        }

        [Test]
        public void RetiredStartException_DoesNotCleanUpReplacement()
        {
            var client = new ControlledClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            Task<bool> oldStart = manager.StartAsync(TransportMode.Http);
            manager.StopAsync(TransportMode.Http).GetAwaiter().GetResult();
            Task<bool> replacement = manager.StartAsync(TransportMode.Http);
            client.Starts[0].SetException(new InvalidOperationException("old failure"));
            Assert.Throws<InvalidOperationException>(() => oldStart.GetAwaiter().GetResult());
            Assert.AreEqual(1, client.StopCalls);
            Assert.IsTrue(manager.HasConnectionIntent(TransportMode.Http));
            client.Starts[1].SetResult(true);
            Assert.IsTrue(replacement.Result);
        }

        [Test]
        public void ForceStop_RetiresPendingStartAndAllowsReplacement()
        {
            var client = new ControlledClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            Task<bool> oldStart = manager.StartAsync(TransportMode.Http);
            manager.ForceStop(TransportMode.Http);
            Assert.IsFalse(manager.HasConnectionIntent(TransportMode.Http));
            Task<bool> replacement = manager.StartAsync(TransportMode.Http);
            Assert.AreNotSame(oldStart, replacement);
            client.Starts[0].SetResult(false);
            client.Starts[1].SetResult(true);
            Assert.IsFalse(oldStart.Result);
            Assert.IsTrue(replacement.Result);
            Assert.AreEqual(1, client.StopCalls);
        }

        [Test]
        public void FailedClientFactory_ClearsIntent()
        {
            var manager = new TransportManager();
            manager.Configure(() => throw new InvalidOperationException("factory failed"), () => new ControlledClient());
            Task<bool> start = manager.StartAsync(TransportMode.Http);
            Assert.Throws<InvalidOperationException>(() => start.GetAwaiter().GetResult());
            Assert.IsFalse(manager.HasConnectionIntent(TransportMode.Http));
        }

        private sealed class PendingTransportClient : IMcpTransportClient
        {
            public readonly TaskCompletionSource<bool> Pending = new TaskCompletionSource<bool>();
            public int StartCalls;

            public bool IsConnected => false;
            public string TransportName => "http";
            public TransportState State { get; } = TransportState.Disconnected("http");

            public Task<bool> StartAsync()
            {
                StartCalls++;
                return Pending.Task;
            }

            public Task StopAsync() => Task.CompletedTask;

            public Task<bool> VerifyAsync() => Task.FromResult(false);

            public Task ReregisterToolsAsync() => Task.CompletedTask;
        }

        [Test]
        public void StartAsync_ConcurrentCallsSameMode_CoalesceIntoOneAttempt()
        {
            var client = new PendingTransportClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);

            Task<bool> first = manager.StartAsync(TransportMode.Http);
            Task<bool> second = manager.StartAsync(TransportMode.Http);

            Assert.AreEqual(1, client.StartCalls, "concurrent starts must share one client attempt");
            Assert.AreSame(first, second, "the in-flight task is returned to concurrent callers");

            client.Pending.SetResult(true); // let the shared attempt finish
        }

        /// <summary>
        /// Stdio fake whose connectivity is flipped by the test without going through
        /// StartAsync/StopAsync — mirrors StdioBridgeHost binding via its editor-idle
        /// retry after a busy-port domain reload, which bypasses TransportManager.
        /// </summary>
        private sealed class ExternallyControlledStdioClient : IMcpTransportClient
        {
            public bool Connected;
            public int Port = 6400;

            public bool IsConnected => Connected;
            public string TransportName => "stdio";
            public TransportState State =>
                Connected ? TransportState.Connected("stdio", port: Port) : TransportState.Disconnected("stdio", "Bridge not running");

            public Task<bool> StartAsync()
            {
                Connected = true;
                return Task.FromResult(true);
            }

            public Task StopAsync()
            {
                Connected = false;
                return Task.CompletedTask;
            }

            public Task<bool> VerifyAsync() => Task.FromResult(Connected);

            public Task ReregisterToolsAsync() => Task.CompletedTask;
        }

        private sealed class ExternallyControlledHttpClient : IMcpTransportClient
        {
            public TransportState State { get; set; } = TransportState.Connected("websocket", sessionId: "pending");
            public bool IsConnected => State.IsConnected;
            public string TransportName => "websocket";

            public Task<bool> StartAsync() => Task.FromResult(true);

            public Task StopAsync() => Task.CompletedTask;

            public Task<bool> VerifyAsync() => Task.FromResult(IsConnected);

            public Task ReregisterToolsAsync() => Task.CompletedTask;
        }

        [Test]
        public void GetState_Http_ReflectsClientDisconnectWithoutVerify()
        {
            var client = new ExternallyControlledHttpClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            Assert.IsTrue(manager.StartAsync(TransportMode.Http).Result);
            client.State = TransportState.Disconnected("websocket", "Server closed connection");

            Assert.IsFalse(manager.IsRunning(TransportMode.Http));
            Assert.AreEqual("Server closed connection", manager.GetState(TransportMode.Http).Error);
        }

        [Test]
        public void GetState_Http_ReflectsRegisteredAndReconnectedSession()
        {
            var client = new ExternallyControlledHttpClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);
            Assert.IsTrue(manager.StartAsync(TransportMode.Http).Result);
            client.State = TransportState.Connected("websocket", sessionId: "registered-session");
            Assert.AreEqual("registered-session", manager.GetState(TransportMode.Http).SessionId);
            client.State = TransportState.Connected("websocket", sessionId: "reconnected-session");
            Assert.AreEqual("reconnected-session", manager.GetState(TransportMode.Http).SessionId);
        }

        /// <summary>
        /// The bridge binding via its editor-idle retry (no StartAsync) must surface as
        /// connected through GetState/IsRunning, port included.
        /// </summary>
        [Test]
        public void GetState_Stdio_ReconcilesWhenBridgeStartsOutsideManager()
        {
            var client = new ExternallyControlledStdioClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);

            Assert.IsFalse(manager.IsRunning(TransportMode.Stdio), "sanity: starts disconnected");

            client.Connected = true; // bridge bound via editor-idle retry, no StartAsync involved

            Assert.IsTrue(manager.IsRunning(TransportMode.Stdio), "manager must report the live bridge even when it started outside StartAsync");
            TransportState state = manager.GetState(TransportMode.Stdio);
            Assert.IsTrue(state.IsConnected);
            Assert.AreEqual(6400, state.Port, "reconciled state comes from the client, port included");
        }

        /// <summary>
        /// A listener that died without StopAsync (e.g. socket teardown on reload) must stop
        /// being reported as connected.
        /// </summary>
        [Test]
        public void GetState_Stdio_ReconcilesWhenBridgeStopsOutsideManager()
        {
            var client = new ExternallyControlledStdioClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);

            Task<bool> started = manager.StartAsync(TransportMode.Stdio);
            Assert.IsTrue(started.IsCompleted && started.Result, "fake start completes synchronously");
            Assert.IsTrue(manager.IsRunning(TransportMode.Stdio));

            client.Connected = false; // listener died without StopAsync (e.g. socket teardown on reload)

            Assert.IsFalse(manager.IsRunning(TransportMode.Stdio), "manager must not report a bridge that is no longer listening");
        }

        /// <summary>
        /// A stop/start cycle between two reads can rebind to a different port while both
        /// snapshots stay "connected" (the 6400↔6401 busy-port fallback); GetState must
        /// surface the new port, not the stale one.
        /// </summary>
        [Test]
        public void GetState_Stdio_ReconcilesWhenBridgePortChangesWhileConnected()
        {
            var client = new ExternallyControlledStdioClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);

            client.Connected = true;
            Assert.AreEqual(6400, manager.GetState(TransportMode.Stdio).Port, "sanity: initial port");

            client.Port = 6401; // bridge restarted onto the fallback port between reads

            TransportState state = manager.GetState(TransportMode.Stdio);
            Assert.IsTrue(state.IsConnected);
            Assert.AreEqual(6401, state.Port, "a rebind while connected must refresh the reported port");
        }

        [Test]
        public void StartAsync_AfterCompletedStart_StartsFresh()
        {
            var client = new FakeTransportClient();
            var manager = new TransportManager();
            manager.Configure(() => client, () => client);

            Task<bool> first = manager.StartAsync(TransportMode.Http);
            Assert.IsTrue(first.IsCompleted && first.Result, "fake start should complete synchronously");

            Task<bool> second = manager.StartAsync(TransportMode.Http);
            Assert.AreEqual(2, client.StartCalls, "a completed start must not block later restarts");
            Assert.IsTrue(second.IsCompleted && second.Result);
        }
    }
}
