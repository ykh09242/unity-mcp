using System;
using System.Collections;
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

        [TestCase(false, false)]
        [TestCase(true, false)]
        [TestCase(false, true)]
        [TestCase(true, true)]
        public async Task Stop_ReturnsWhileCancellationCallbackAndHandlerRemainOwned(bool throws, bool callbackFirst)
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
            Task stopping = null;
            TcpClient peer = null;
            int callbacks = 0;
            handlers[name] = HandlerInfo.Cooperative(
                name,
                (_, cancellation) =>
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
                }
            );
            try
            {
                StdioBridgeHost.StartOwned(0, token);
                Assert.IsTrue(StdioBridgeHost.IsRunning);
                peer = Connect(StdioBridgeHost.GetCurrentPort(), token);
                Send(peer.GetStream(), new JObject { ["type"] = name, ["params"] = new JObject() }.ToString(Formatting.None));
                await PumpUntilAsync(() => entered.IsSet);
                Assert.AreEqual(1, StdioBridgeHost.QueuedCommandCount);
                Assert.Greater(StdioBridgeHost.ReservedInputBytes, 0);
                stopping = Task.Run(StdioBridgeHost.Stop);
                Assert.IsTrue(callbackEntered.Wait(TimeSpan.FromSeconds(5)), "Cancellation must reach the actual dispatched handler.");
                Assert.IsTrue(stopping.Wait(TimeSpan.FromSeconds(1)), "Stop must return while a cancellation callback is held.");
                Assert.IsFalse(StdioBridgeHost.IsRunning);
                StdioBridgeHost.StartOwned(0, token);
                Assert.IsTrue(StdioBridgeHost.IsRunning, "A replacement must be able to start while the old command drains.");
                object replacement = typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null);
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
                releaseCallback.Set();
                handler.TrySetResult(new { done = true });
                await PumpUntilAsync(() => StdioBridgeHost.QueuedCommandCount == 0 && StdioBridgeHost.ReservedInputBytes == 0);
                Assert.AreEqual(1, callbacks);
                Assert.IsTrue(StdioBridgeHost.IsRunning, "The old drain must not poison a valid replacement launch.");
                Assert.AreSame(replacement, typeof(StdioBridgeHost).GetField("authentication", Static).GetValue(null));
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

        private static TcpClient Connect(int port, string token)
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

        private static void Send(NetworkStream stream, string text)
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
