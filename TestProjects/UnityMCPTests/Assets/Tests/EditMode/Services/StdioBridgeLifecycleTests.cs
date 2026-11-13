using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class StdioBridgeLifecycleTests
    {
        private const BindingFlags Static = BindingFlags.NonPublic | BindingFlags.Static;

        private static readonly string ReloadDomain = Guid.NewGuid().ToString("N");
        private const string StopSessionKey = "MCPForUnity.StdioExplicitlyStopped";
        private bool savedStop;
        private bool hadStop;

        [SetUp]
        public void SaveStopIntent()
        {
            savedStop = SessionState.GetBool(StopSessionKey, false);
            hadStop = savedStop == SessionState.GetBool(StopSessionKey, true);
        }

        [TearDown]
        public void RestoreStopIntent()
        {
            typeof(StdioBridgeHost).GetField("explicitlyStopped", Static).SetValue(null, savedStop);
            if (hadStop)
                SessionState.SetBool(StopSessionKey, savedStop);
            else
                SessionState.EraseBool(StopSessionKey);
        }

        [UnityTest, Explicit("Requires an isolated batch Editor; this test replaces the scripting domain.")]
        public IEnumerator ExplicitStop_SurvivesActualDomainReloadUntilExplicitStart()
        {
            Assert.IsTrue(UnityEngine.Application.isBatchMode, "Actual reload requires an isolated batch Editor.");
            AssertIdle();
            const string domainKey = "MCPForUnity.Tests.StdioStoppedDomain";
            SessionState.SetString(domainKey, ReloadDomain);
            try
            {
                StdioBridgeHost.StartOwned(0, new string('b', 64));
                StdioBridgeHost.Stop();
                EditorUtility.RequestScriptReload();
                yield return new WaitForDomainReload();
                Assert.AreNotEqual(SessionState.GetString(domainKey, ""), ReloadDomain, "This must witness an actual replacement domain.");
                // Batch startup is intentionally disabled. Exercise the same scheduling gate
                // used by the interactive domain initializer without publishing an endpoint.
                typeof(StdioBridgeHost).GetMethod("ScheduleInitRetry", Static, null, Type.EmptyTypes, null).Invoke(null, null);
                Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("initScheduled", Static).GetValue(null), "End Session must survive domain replacement.");
                Assert.IsFalse(StdioBridgeHost.IsRunning);
                StdioBridgeHost.StartOwned(0, new string('b', 64));
                Assert.IsTrue(StdioBridgeHost.IsRunning, "An explicit Start must resume the stopped session.");
            }
            finally
            {
                StdioBridgeHost.Stop();
                SessionState.EraseString(domainKey);
            }
        }

        [Test]
        public void ReloadTeardown_DoesNotRecordAUserStop()
        {
            AssertIdle();
            var manager = new TransportManager();
            var client = new StdioTransportClient();
            manager.Configure(() => client, () => client);
            // Materialize the real client without starting the preference-backed endpoint.
            typeof(TransportManager).GetField("_stdioClient", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(manager, client);
            try
            {
                StdioBridgeHost.StartOwned(0, new string('c', 64));
                manager.StopStdioForReloadAsync().GetAwaiter().GetResult();
                Assert.IsFalse(StdioBridgeHost.IsRunning);
                typeof(StdioBridgeHost).GetMethod("ScheduleInitRetry", Static, null, Type.EmptyTypes, null).Invoke(null, null);
                Assert.IsTrue(
                    (bool)typeof(StdioBridgeHost).GetField("initScheduled", Static).GetValue(null),
                    "Reload teardown must preserve automatic startup eligibility."
                );
            }
            finally
            {
                StdioBridgeHost.Stop();
            }
        }

        [Test]
        public async Task TerminalListenerFailureCompletesStartupAndTransportImmediately()
        {
            AssertIdle();
            var factory = typeof(StdioBridgeHost).GetField("listenerFactory", Static);
            object previous = factory.GetValue(null);
            try
            {
                factory.SetValue(null, new Func<int, TcpListener>(_ => throw new InvalidOperationException("injected terminal failure")));
                LogAssert.Expect(
                    UnityEngine.LogType.Error,
                    new System.Text.RegularExpressions.Regex("Failed to start TCP listener \\(InvalidOperationException\\)")
                );
                StdioBridgeHost.StartOwned(0, new string('a', 64));
                var startup = StdioBridgeHost.StartupCompletion;
                Assert.IsTrue(startup.IsCompleted, "Terminal failure must complete this attempt without consuming the 300-second readiness budget.");
                var client = new StdioTransportClient(token => StdioTransportClient.WaitForStartupAsync(startup, 300, token), () => { });
                var start = client.StartAsync();
                Assert.IsTrue(start.IsCompleted, "Transport must consume an already-terminal host outcome immediately.");
                Assert.IsFalse(await start);
                Assert.That(client.State.Error, Does.Contain("InvalidOperationException"));
                Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("startRequested", Static).GetValue(null));
                Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("ensureUpdateHooked", Static).GetValue(null));
            }
            finally
            {
                factory.SetValue(null, previous);
                StdioBridgeHost.Stop();
            }
        }

        [Test]
        public async Task CompilationDeferralKeepsStartupPendingUntilReady()
        {
            AssertIdle();
            var check = typeof(StdioBridgeHost).GetField("compilationCheck", Static);
            object previous = check.GetValue(null);
            string token = new string('d', 64);
            try
            {
                check.SetValue(null, new Func<bool>(() => true));
                StdioBridgeHost.StartOwned(0, token);
                var startup = StdioBridgeHost.StartupCompletion;
                Assert.IsFalse(startup.IsCompleted, "Compilation is recoverable, not terminal.");
                check.SetValue(null, new Func<bool>(() => false));
                ResumeOwnedStartup(token);
                Assert.IsTrue((await startup).Ready);
                Assert.IsTrue(StdioBridgeHost.IsRunning);
            }
            finally
            {
                check.SetValue(null, previous);
                StdioBridgeHost.Stop();
            }
        }

        [Test]
        public async Task RecoverablePortConflictRetryKeepsSameStartupPendingAndRecovers()
        {
            AssertIdle();
            var check = typeof(StdioBridgeHost).GetField("compilationCheck", Static);
            object previous = check.GetValue(null);
            string token = new string('e', 64);
            try
            {
                check.SetValue(null, new Func<bool>(() => true));
                StdioBridgeHost.StartOwned(0, token);
                var startup = StdioBridgeHost.StartupCompletion;
                long intent = (long)typeof(StdioBridgeHost).GetField("startIntentEpoch", Static).GetValue(null);
                // Enter the same recovery branch as an ordinary listener's transient
                // AddressAlreadyInUse, using an owned endpoint to avoid discovery writes.
                typeof(StdioBridgeHost).GetMethod("DeferBusyPortStartup", Static).Invoke(null, new object[] { intent, EditorApplication.timeSinceStartup });
                Assert.IsFalse(startup.IsCompleted, "A temporary port conflict must leave the same attempt pending.");
                Assert.IsTrue((bool)typeof(StdioBridgeHost).GetField("ensureUpdateHooked", Static).GetValue(null));
                check.SetValue(null, new Func<bool>(() => false));
                ResumeOwnedStartup(token);
                Assert.IsTrue((await startup).Ready);
            }
            finally
            {
                check.SetValue(null, previous);
                StdioBridgeHost.Stop();
            }
        }

        [Test]
        public async Task OwnedPortConflictFailsWithoutRetryOrRetargeting()
        {
            AssertIdle();
            var occupant = new TcpListener(System.Net.IPAddress.Loopback, 0);
            occupant.Start();
            int port = ((System.Net.IPEndPoint)occupant.LocalEndpoint).Port;
            try
            {
                LogAssert.Expect(UnityEngine.LogType.Error, new System.Text.RegularExpressions.Regex("Failed to start TCP listener \\(SocketException\\)"));
                StdioBridgeHost.StartOwned(port, new string('e', 64));
                var result = await StdioBridgeHost.StartupCompletion;
                Assert.IsFalse(result.Ready);
                Assert.That(result.Error, Does.Contain("SocketException"));
                Assert.AreEqual(port, StdioBridgeHost.GetCurrentPort());
                Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("ensureUpdateHooked", Static).GetValue(null));
            }
            finally
            {
                occupant.Stop();
                StdioBridgeHost.Stop();
            }
        }

        [Test]
        public async Task RetiredStartupFailureCannotFinishItsReplacement()
        {
            AssertIdle();
            var check = typeof(StdioBridgeHost).GetField("compilationCheck", Static);
            object previous = check.GetValue(null);
            string token = new string('f', 64);
            try
            {
                check.SetValue(null, new Func<bool>(() => true));
                StdioBridgeHost.StartOwned(0, token);
                var retired = StdioBridgeHost.StartupCompletion;
                long oldIntent = (long)typeof(StdioBridgeHost).GetField("startIntentEpoch", Static).GetValue(null);
                StdioBridgeHost.StartOwned(0, token);
                var replacement = StdioBridgeHost.StartupCompletion;
                Assert.IsFalse((await retired).Ready);
                typeof(StdioBridgeHost).GetMethod("CompleteStartup", Static).Invoke(null, new object[] { oldIntent, false, "late terminal failure" });
                typeof(StdioBridgeHost).GetMethod("StartBridge", Static).Invoke(null, new object[] { (int?)0, token, oldIntent });
                Assert.IsFalse(replacement.IsCompleted, "A late old result or callback must not finish the replacement.");
                check.SetValue(null, new Func<bool>(() => false));
                ResumeOwnedStartup(token);
                Assert.IsTrue((await replacement).Ready);
                Assert.IsTrue(StdioBridgeHost.IsRunning);
            }
            finally
            {
                check.SetValue(null, previous);
                StdioBridgeHost.Stop();
            }
        }

        private static void ResumeOwnedStartup(string token, int port = 0)
        {
            long intent = (long)typeof(StdioBridgeHost).GetField("startIntentEpoch", Static).GetValue(null);
            typeof(StdioBridgeHost).GetMethod("StartBridge", Static).Invoke(null, new object[] { (int?)port, token, intent });
        }

        [Test]
        public void Dispatcher_RejectsCanceledOwnerWhileExecutionTokenIsStillLive()
        {
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", Static).GetValue(null);
            var processing = typeof(TransportCommandDispatcher).GetField("_processingFlag", Static);
            string name = "stdio_owner_retirement_" + Guid.NewGuid().ToString("N");
            using var execution = new CancellationTokenSource();
            using var owner = new CancellationTokenSource();
            int invocations = 0;
            handlers[name] = new HandlerInfo(name, _ => new { calls = ++invocations }, null);
            try
            {
                processing.SetValue(null, 1);
                var operation = TransportCommandDispatcher.ExecuteTimedCommandAsync(
                    new Command { type = name },
                    execution.Token,
                    TransportMode.Stdio,
                    null,
                    null,
                    CancellationToken.None,
                    owner.Token
                );
                processing.SetValue(null, 0);
                Assert.IsFalse(operation.Response.IsCompleted);
                owner.Cancel();
                Assert.IsFalse(execution.IsCancellationRequested, "Execution callback propagation is deliberately still pending.");
                typeof(TransportCommandDispatcher).GetMethod("ProcessQueue", Static).Invoke(null, null);
                Assert.AreEqual(0, invocations, "A canceled request must not start merely because its execution token has not caught up.");
                Assert.IsTrue(operation.Response.IsCanceled);
                Assert.IsTrue(operation.Completion.IsCompleted);
                Assert.IsFalse(execution.IsCancellationRequested, "The observation token must not be linked into user callbacks.");
            }
            finally
            {
                processing.SetValue(null, 0);
                execution.Cancel();
                handlers.Remove(name);
            }
        }

        [Test]
        public async Task CompletedSlotReuse_PreservesLegacyAdmissionOrder()
        {
            AssertIdle();
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", Static).GetValue(null);
            var processing = typeof(StdioBridgeHost).GetField("processingCommands", Static);
            string name = "stdio_fifo_" + Guid.NewGuid().ToString("N");
            string token = new string('f', 64);
            var first = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            var calls = new List<string>();
            var peers = new List<TcpClient>();
            handlers[name] = new HandlerInfo(name, null, parameters =>
            {
                string label = parameters.Value<string>("label");
                calls.Add(label);
                return label == "A" ? first.Task : Task.FromResult<object>(new { label });
            });
            void Enqueue(string label, int count)
            {
                var peer = Connect(StdioBridgeHost.GetCurrentPort(), token);
                peers.Add(peer);
                Send(
                    peer.GetStream(),
                    new JObject
                    {
                        ["type"] = name,
                        ["params"] = new JObject { ["label"] = label },
                    }.ToString(Formatting.None)
                );
                Assert.IsTrue(
                    SpinWait.SpinUntil(() => StdioBridgeHost.QueuedCommandCount == count, 5000),
                    "Each peer must be admitted before the next peer sends."
                );
            }
            try
            {
                StdioBridgeHost.StartOwned(0, token);
                processing.SetValue(null, 1);
                Enqueue("A", 1);
                processing.SetValue(null, 0);
                Pump();
                processing.SetValue(null, 1);
                CollectionAssert.AreEqual(new[] { "A" }, calls);
                Enqueue("B", 2);
                Enqueue("C", 3);
                first.TrySetResult(new { done = true });
                await PumpUntilAsync(() => StdioBridgeHost.QueuedCommandCount == 2);
                Enqueue("D", 3);
                processing.SetValue(null, 0);
                await PumpUntilAsync(() => StdioBridgeHost.QueuedCommandCount == 0);
                CollectionAssert.AreEqual(new[] { "A", "B", "C", "D" }, calls, "New work must not inherit a completed dictionary slot's priority.");
            }
            finally
            {
                first.TrySetResult(new { cleanup = true });
                processing.SetValue(null, 0);
                foreach (var peer in peers)
                    peer.Dispose();
                StdioBridgeHost.Stop();
                await PumpUntilAsync(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
                handlers.Remove(name);
            }
        }

        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void Stop_RejectsQueuedWorkBeforeCancellationPropagation(bool dispatcherQueued, bool restart)
        {
            AssertIdle();
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", Static).GetValue(null);
            var queue = (IDictionary)typeof(StdioBridgeHost).GetField("commandQueue", Static).GetValue(null);
            object queueLock = typeof(StdioBridgeHost).GetField("lockObj", Static).GetValue(null);
            var processing = typeof(TransportCommandDispatcher).GetField("_processingFlag", Static);
            string name = "stdio_retirement_" + Guid.NewGuid().ToString("N");
            string token = new string('e', 64);
            using var delayedCancellation = new CancellationTokenSource();
            TcpClient peer = null;
            int invocations = 0;
            handlers[name] = new HandlerInfo(name, _ => new { calls = ++invocations }, null);
            try
            {
                StdioBridgeHost.StartOwned(0, token);
                peer = Connect(StdioBridgeHost.GetCurrentPort(), token);
                Send(peer.GetStream(), new JObject { ["type"] = name }.ToString(Formatting.None));
                Assert.IsTrue(SpinWait.SpinUntil(() => StdioBridgeHost.QueuedCommandCount == 1, 5000));
                // Keep connection cleanup out of this deterministic admission interval.
                // The independent token models cancellation propagation still queued on a worker.
                lock (queueLock)
                {
                    var queued = queue.Values.Cast<QueuedCommand>().Single();
                    queued.OwnerCancellation = delayedCancellation.Token;
                    Task response = queued.Tcs.Task;
                    if (dispatcherQueued)
                    {
                        processing.SetValue(null, 1);
                        try
                        {
                            typeof(StdioBridgeHost).GetMethod("ProcessCommands", Static).Invoke(null, null);
                        }
                        finally
                        {
                            processing.SetValue(null, 0);
                        }
                        var pending = (IDictionary)typeof(TransportCommandDispatcher).GetField("Pending", Static).GetValue(null);
                        Assert.AreEqual(1, pending.Count);
                        object entry = pending.Values.Cast<object>().Single();
                        response = ((TaskCompletionSource<TransportCommandResponse>)entry.GetType().GetProperty("ResponseSource").GetValue(entry)).Task;
                    }
                    Assert.AreEqual(0, invocations);
                    StdioBridgeHost.Stop();
                    Assert.IsFalse(delayedCancellation.IsCancellationRequested);
                    if (restart)
                        StdioBridgeHost.StartOwned(0, token);
                    Pump();
                    Assert.AreEqual(0, invocations, "Retired launch work must be rejected before asynchronous cancellation reaches its normal token.");
                    Assert.IsTrue(response.IsCanceled, "Retired unstarted work must complete its response as canceled.");
                }
            }
            finally
            {
                processing.SetValue(null, 0);
                delayedCancellation.Cancel();
                peer?.Dispose();
                StdioBridgeHost.Stop();
                PumpUntil(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
                handlers.Remove(name);
            }
        }

        [Test]
        public void Stop_RetiresDeferredInitializationEvenWithoutListener()
        {
            AssertIdle();
            // Reconstruct initial domain-load intent without touching Editor preferences.
            typeof(StdioBridgeHost).GetField("explicitlyStopped", Static)?.SetValue(null, false);
            typeof(StdioBridgeHost).GetMethod("ScheduleInitRetry", Static, null, Type.EmptyTypes, null).Invoke(null, null);
            var delayed = EditorApplication.delayCall?.GetInvocationList().Where(item => item.Method.DeclaringType == typeof(StdioBridgeHost)).ToArray();
            // Lambdas capture the generation in a compiler-generated nested type.
            if (delayed == null || delayed.Length == 0)
                delayed = EditorApplication
                    .delayCall?.GetInvocationList()
                    .Where(item => item.Method.DeclaringType?.DeclaringType == typeof(StdioBridgeHost))
                    .ToArray();
            var updates = EditorApplication
                .update?.GetInvocationList()
                .Where(item =>
                    item.Method.DeclaringType == typeof(StdioBridgeHost) && item.Method.Name == "EnsureStartedOnEditorIdle"
                    || item.Method.DeclaringType?.DeclaringType == typeof(StdioBridgeHost)
                )
                .ToArray();
            Assert.IsNotEmpty(delayed, "The actual host delay subscription must be armed.");
            Assert.IsNotEmpty(updates, "The actual host idle retry subscription must be armed.");
            Assert.IsTrue((bool)typeof(StdioBridgeHost).GetField("initScheduled", Static).GetValue(null));
            int heartbeat = (int)typeof(StdioBridgeHost).GetField("heartbeatSeq", Static).GetValue(null);
            try
            {
                StdioBridgeHost.Stop();
                Assert.IsFalse(
                    (bool)typeof(StdioBridgeHost).GetField("initScheduled", Static).GetValue(null),
                    "Stop must retire startup while no listener exists."
                );
                Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("ensureUpdateHooked", Static).GetValue(null));
                foreach (Delegate callback in delayed.Concat(updates))
                    callback.DynamicInvoke();
                Assert.IsFalse(StdioBridgeHost.IsRunning);
                Assert.IsNull(typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null));
                Assert.AreEqual(heartbeat, (int)typeof(StdioBridgeHost).GetField("heartbeatSeq", Static).GetValue(null));

                StdioBridgeHost.StartOwned(0, new string('b', 64));
                Assert.IsTrue(StdioBridgeHost.IsRunning, "Internal startup cleanup must preserve a newer explicit start.");
                object launch = typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null);
                foreach (Delegate callback in delayed.Concat(updates))
                    callback.DynamicInvoke();
                Assert.AreSame(launch, typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null));
            }
            finally
            {
                StdioBridgeHost.Stop();
                foreach (Delegate callback in delayed)
                    EditorApplication.delayCall -= (EditorApplication.CallbackFunction)callback;
                foreach (Delegate callback in updates)
                    EditorApplication.update -= (EditorApplication.CallbackFunction)callback;
            }
        }

        [TestCase(false, false, "stop")]
        [TestCase(true, false, "stop")]
        [TestCase(false, true, "stop")]
        [TestCase(true, true, "stop")]
        [TestCase(false, false, "reload")]
        [TestCase(false, true, "reload")]
        [TestCase(false, false, "restart")]
        [TestCase(false, true, "restart")]
        public async Task Stop_ReturnsWhileCancellationCallbackAndHandlerRemainOwned(bool throws, bool callbackFirst, string transition)
        {
            AssertIdle();
            CommandRegistry.Initialize();
            var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", Static).GetValue(null);
            string name = "stdio_stop_" + Guid.NewGuid().ToString("N");
            string token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            using var entered = new ManualResetEventSlim();
            using var callbackEntered = new ManualResetEventSlim();
            using var releaseCallback = new ManualResetEventSlim();
            var handler = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            CancellationTokenRegistration registration = default;
            using var transport = new TransportManager();
            var client = new StdioTransportClient();
            transport.Configure(() => null, () => client);
            Task stopping = null;
            TcpClient peer = null;
            int callbacks = 0;
            handlers[name] = HandlerInfo.Cooperative(name, (_, cancellation) =>
            {
                registration = cancellation.Register(() =>
                {
                    Interlocked.Increment(ref callbacks);
                    callbackEntered.Set();
                    if (!releaseCallback.Wait(TimeSpan.FromSeconds(15)))
                        throw new TimeoutException("Owned callback was not released.");
                    if (throws)
                        throw new InvalidOperationException("Owned callback failure.");
                });
                entered.Set();
                return handler.Task;
            });
            try
            {
                StdioBridgeHost.StartOwned(0, token);
                Assert.IsTrue(StdioBridgeHost.IsRunning);
                peer = Connect(StdioBridgeHost.GetCurrentPort(), token);
                Send(peer.GetStream(), new JObject { ["type"] = name, ["params"] = new JObject() }.ToString(Formatting.None));
                await PumpUntilAsync(() => entered.IsSet);
                Assert.AreEqual(1, StdioBridgeHost.QueuedCommandCount);
                Assert.Greater(StdioBridgeHost.ReservedInputBytes, 0);
                stopping = Task.Run(() => transition == "reload" ? client.StopForReloadAsync() : client.StopAsync());
                Assert.IsTrue(callbackEntered.Wait(TimeSpan.FromSeconds(5)), "Cancellation must reach the actual dispatched handler.");
                Assert.IsTrue(stopping.Wait(TimeSpan.FromSeconds(1)), "Stop must return while a cancellation callback is held.");
                Assert.IsFalse(StdioBridgeHost.IsRunning);
                Assert.IsFalse(client.State.IsConnected);
                StringAssert.StartsWith("execution_unsettled:", client.State.Error);
                StringAssert.StartsWith("execution_unsettled:", transport.GetState(TransportMode.Stdio).Error);
                if (callbackFirst)
                    releaseCallback.Set();
                else
                    handler.TrySetResult(new { done = true });
                await Task.Delay(100);
                Assert.AreEqual(
                    1,
                    StdioBridgeHost.QueuedCommandCount,
                    "Both the handler and cancellation callback must settle before retiring the old command."
                );
                Assert.Greater(StdioBridgeHost.ReservedInputBytes, 0, "The old command must retain input ownership through complete settlement.");
                StringAssert.StartsWith("execution_unsettled:", transport.GetState(TransportMode.Stdio).Error);
                await AssertIndependentAdmissionAsync(true, TransportMode.Http);
                object replacement = null;
                if (transition == "restart")
                {
                    StdioBridgeHost.StartOwned(0, token);
                    Assert.IsTrue(StdioBridgeHost.IsRunning, "A replacement must be able to start while the old command drains.");
                    replacement = typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null);
                    await AssertIndependentAdmissionAsync(true, TransportMode.Http);
                }
                releaseCallback.Set();
                handler.TrySetResult(new { done = true });
                await PumpUntilAsync(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
                Assert.AreEqual(1, callbacks);
                Assert.IsNull(client.State.Error);
                Assert.IsNull(transport.GetState(TransportMode.Stdio).Error);
                await AssertIndependentAdmissionAsync(false, TransportMode.Http);
                if (transition == "restart")
                {
                    Assert.IsTrue(StdioBridgeHost.IsRunning, "The old drain must not poison a valid replacement launch.");
                    Assert.AreSame(replacement, typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null));
                }
                else
                    Assert.IsFalse(client.State.IsConnected);
            }
            finally
            {
                releaseCallback.Set();
                handler.TrySetResult(new { cleanup = true });
                if (stopping != null)
                    Assert.IsTrue(stopping.Wait(TimeSpan.FromSeconds(5)));
                peer?.Dispose();
                StdioBridgeHost.Stop();
                await PumpUntilAsync(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
                registration.Dispose();
                handlers.Remove(name);
            }
        }

        internal static async Task AssertIndependentAdmissionAsync(bool blocked, TransportMode origin)
        {
            using var settings = NativeProgressProbe.EnableTools("batch_execute", "get_selection");
            foreach (string tool in new[] { "batch_execute", "get_selection", "ping" })
            {
                var command = new Command
                {
                    type = tool,
                    @params = tool == "batch_execute" ? new JObject { ["commands"] = new JArray() } : new JObject(),
                    arbitration = new JObject
                    {
                        ["version"] = 1,
                        ["request_nonce"] = Guid.NewGuid().ToString("N"),
                        ["instance_generation"] = NativeCommandArbitrator.Shared.Generation,
                    },
                };
                var operation = TransportCommandDispatcher.ExecuteCommandAsync(command, CancellationToken.None, origin);
                await PumpUntilAsync(() => operation.Response.IsCompleted && operation.Completion.IsCompleted);
                var response = JObject.Parse((await operation.Response).ToJson());
                var proof = response["native_arbitration"];
                Assert.AreEqual(blocked && tool != "ping" ? "not_started" : "started", (string)proof?["disposition"], tool);
                if (blocked && tool != "ping")
                    Assert.AreEqual("operation_busy", (string)proof?["reason"], tool);
            }
        }

        private static async Task PumpUntilAsync(Func<bool> done)
        {
            var watch = Stopwatch.StartNew();
            while (!done())
            {
                Pump();
                Assert.Less(watch.ElapsedMilliseconds, 5000, "Owned work did not settle.");
                // Settlement includes handlers whose continuations belong to Unity's context.
                // A synchronous spin here prevents the real owner from ever completing.
                await Task.Yield();
            }
        }

        private static void Pump()
        {
            typeof(StdioBridgeHost).GetMethod("ProcessCommands", Static).Invoke(null, null);
            typeof(TransportCommandDispatcher).GetMethod("ProcessQueue", Static).Invoke(null, null);
        }

        private static void AssertIdle()
        {
            Assert.IsFalse(StdioBridgeHost.IsRunning, "Use an idle owned fixture; never replace an existing host.");
            Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("initScheduled", Static).GetValue(null), "Do not replace an existing pending startup.");
            Assert.IsFalse((bool)typeof(StdioBridgeHost).GetField("ensureUpdateHooked", Static).GetValue(null), "Do not replace an existing pending retry.");
        }

        private static void PumpUntil(Func<bool> done)
        {
            var watch = Stopwatch.StartNew();
            while (!done())
            {
                Pump();
                Assert.Less(watch.ElapsedMilliseconds, 5000, "Owned work did not settle.");
                Thread.Sleep(1);
            }
        }

        private static void PumpFor(int milliseconds)
        {
            var watch = Stopwatch.StartNew();
            while (watch.ElapsedMilliseconds < milliseconds)
            {
                Pump();
                Thread.Sleep(1);
            }
        }

        internal static TcpClient Connect(int port, string token)
        {
            var client = new TcpClient();
            try
            {
                Assert.IsTrue(client.ConnectAsync("127.0.0.1", port).Wait(5000));
                client.ReceiveTimeout = 5000;
                var stream = client.GetStream();
                var line = new StringBuilder();
                int value;
                while ((value = stream.ReadByte()) != '\n')
                {
                    Assert.GreaterOrEqual(value, 0);
                    line.Append((char)value);
                }
                string[] fields = line.ToString().Split(' ');
                string generation = fields.Single(field => field.StartsWith("SERVER=", StringComparison.Ordinal)).Substring(7);
                string challenge = fields.Single(field => field.StartsWith("CHALLENGE=", StringComparison.Ordinal)).Substring(10);
                string nonce = new string('a', 64);
                Send(
                    stream,
                    new JObject
                    {
                        ["type"] = "authenticate",
                        ["version"] = 2,
                        ["client_nonce"] = nonce,
                        ["proof"] = StdioBridgeAuthentication.Proof(token, "client", generation, challenge, nonce),
                    }.ToString(Formatting.None)
                );
                ulong length = 0;
                for (int i = 0; i < 8; i++)
                    length = (length << 8) | (uint)stream.ReadByte();
                Assert.Less(length, 1024);
                var bytes = new byte[(int)length];
                int read = 0;
                while (read < bytes.Length)
                {
                    int count = stream.Read(bytes, read, bytes.Length - read);
                    Assert.Greater(count, 0);
                    read += count;
                }
                Assert.AreEqual("authenticated", (string)JObject.Parse(Encoding.UTF8.GetString(bytes))["type"]);
                return client;
            }
            catch
            {
                client.Dispose();
                throw;
            }
        }

        internal static void Send(NetworkStream stream, string text)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(text);
            byte[] header = new byte[8];
            ulong length = (ulong)bytes.Length;
            for (int i = 7; i >= 0; i--)
            {
                header[i] = (byte)length;
                length >>= 8;
            }
            stream.Write(header, 0, header.Length);
            stream.Write(bytes, 0, bytes.Length);
        }
    }
}
