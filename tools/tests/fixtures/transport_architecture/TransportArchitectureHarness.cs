using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Net.WebSockets;
using System.Reflection;
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

internal static class TransportArchitectureHarness
{
    private static readonly BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly List<JObject> Messages = new List<JObject>();
    private static readonly PumpContext Context = new PumpContext();
    private static int _passes;

    public static int Main()
    {
        try
        {
            TransportCommandDispatcher.ExecuteCommandJsonAsync("ping", CancellationToken.None).GetAwaiter().GetResult();
            RunNUnit(typeof(MCPForUnityTests.Editor.Services.TransportArchitectureTests));
            RunNUnit(typeof(MCPForUnityTests.Editor.Services.CooperativeCancellationTests));
            RunNUnit(typeof(MCPForUnityTests.Editor.Services.CooperativeHandlerCancellationTests));
            RunNUnit(typeof(MCPForUnityTests.Editor.Tools.BatchExecuteCancellationTests));
            RunNUnit(typeof(MCPForUnityTests.Editor.Services.TransportCommandDispatcherTests));
            RunNUnit(typeof(MCPForUnityTests.Editor.Services.WebSocketTransportClientTests));
            SynchronizationContext.SetSynchronizationContext(Context);
            typeof(TransportCommandDispatcher).GetField("_mainThreadContext", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, Context);
            ExerciseRealSocket();
            CancellationSocketHarness.Run(PumpUntil, Pass);
            BenchmarkProjection();
            Console.WriteLine("PASS_TOTAL: " + _passes);
            return 0;
        }
        catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }

    private static void RunNUnit(Type type)
    {
        foreach (var method in type.GetMethods())
        {
            var cases = method.GetCustomAttributes<TestCaseAttribute>().ToArray();
            bool test = method.GetCustomAttribute<TestAttribute>() != null;
            if (!test && cases.Length == 0) continue;
            var arguments = cases.Length == 0 ? new[] { Array.Empty<object>() } : cases.Select(c => c.Arguments).ToArray();
            foreach (var args in arguments)
            {
                object fixture = Activator.CreateInstance(type);
                try
                {
                    foreach (var setup in type.GetMethods().Where(m => m.GetCustomAttribute<SetUpAttribute>() != null)) setup.Invoke(fixture, null);
                    var returned = method.Invoke(fixture, args);
                    if (returned is Task task) { PumpUntil(() => task.IsCompleted, "NUnit task"); task.GetAwaiter().GetResult(); }
                    Pass(type.Name + "." + method.Name + "(" + string.Join(",", args) + ")");
                }
                finally { foreach (var teardown in type.GetMethods().Where(m => m.GetCustomAttribute<TearDownAttribute>() != null)) teardown.Invoke(fixture, null); }
            }
        }
    }

    private static void Set(WebSocketTransportClient client, string name, object value)
        => typeof(WebSocketTransportClient).GetField(name, PrivateInstance).SetValue(client, value);

    private static Task Invoke(WebSocketTransportClient client, string name, params object[] args)
        => (Task)typeof(WebSocketTransportClient).GetMethod(name, PrivateInstance).Invoke(client, args);

    private static void PumpUntil(Func<bool> done, string label, int milliseconds = 5000)
    {
        var clock = Stopwatch.StartNew();
        while (!done())
        {
            Context.Drain();
            UnityEditor.EditorApplication.Pump();
            if (clock.ElapsedMilliseconds > milliseconds) throw new Exception("Timeout: " + label);
            Thread.Sleep(2);
        }
        Context.Drain();
    }

    private static void Collect(LocalWebSocketPeer peer)
    {
        while (peer.Received.TryDequeue(out var message)) Messages.Add(message);
    }

    private static JObject Result(LocalWebSocketPeer peer, string id)
    {
        Collect(peer);
        return Messages.FirstOrDefault(m => m.Value<string>("id") == id && m.Value<string>("type") == "command_result");
    }

    private static JObject Execute(string id, string name, int timeout = 30)
        => new JObject { ["type"] = "execute", ["id"] = id, ["name"] = name, ["timeout"] = timeout };

    private static void ExerciseRealSocket()
    {
        var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
        var slow = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
        var invoked = new List<string>();
        int mainThread = Thread.CurrentThread.ManagedThreadId;
        handlers["fixture_slow"] = new HandlerInfo("fixture_slow", null, _ =>
        {
            Assert.AreEqual(mainThread, Thread.CurrentThread.ManagedThreadId);
            invoked.Add("slow");
            return slow.Task;
        });
        handlers["fixture_next"] = new HandlerInfo("fixture_next", _ =>
        {
            Assert.AreEqual(mainThread, Thread.CurrentThread.ManagedThreadId);
            invoked.Add("next");
            return new { completed = true };
        }, null);
        handlers["fixture_expired"] = new HandlerInfo("fixture_expired", _ => { invoked.Add("expired"); return new { completed = true }; }, null);
        using var peer = new LocalWebSocketPeer();
        using var socket = new ClientWebSocket();
        using var lifecycle = new CancellationTokenSource();
        using var connection = CancellationTokenSource.CreateLinkedTokenSource(lifecycle.Token);
        using var client = new WebSocketTransportClient();
        var connect = socket.ConnectAsync(peer.Uri, connection.Token);
        PumpUntil(() => connect.IsCompleted, "socket handshake"); connect.GetAwaiter().GetResult();
        peer.Ready.GetAwaiter().GetResult();
        Set(client, "_socket", socket);
        Set(client, "_lifecycleCts", lifecycle);
        Set(client, "_connectionCts", connection);
        var commands = new ConnectionCommandWork(connection.Token);
        Set(client, "_commandWork", commands);
        Set(client, "_endpointUri", peer.Uri);
        Set(client, "_keepAliveInterval", TimeSpan.FromMinutes(1));
        typeof(WebSocketTransportClient).GetMethod("StartBackgroundLoops", PrivateInstance).Invoke(client, new object[] { connection.Token });
        peer.Send(new JObject { ["type"] = "registered", ["session_id"] = "fixture", ["capabilities"] = new JArray("editor_state_v1", "large_result_v1") });
        PumpUntil(() => EditorStatePublisher.Starts == 1, "state publisher negotiated");
        Pass("Registered ACK activates negotiated state publisher");
        peer.Send(Execute("active", "fixture_slow", 1));
        PumpUntil(() => invoked.Contains("slow"), "slow handler starts");
        peer.Send(Execute("next", "fixture_next"));
        peer.Send(Execute("expired", "fixture_expired", 1));
        var pingClock = Stopwatch.StartNew();
        peer.Send(new JObject { ["type"] = "ping" });
        PumpUntil(() => { Collect(peer); return Messages.Any(m => m.Value<string>("type") == "pong"); }, "early pong", 1000);
        Console.WriteLine("RECEIVE_CONTROL_PONG_MS: " + pingClock.ElapsedMilliseconds);
        Assert.IsFalse(slow.Task.IsCompleted);
        Pass("Real receive loop accepts ping during active async command");
        peer.Send(Execute("active", "fixture_next"));
        PumpUntil(() => Result(peer, "active")?.SelectToken("result.error")?.Value<string>() == "Duplicate command id", "duplicate error");
        Pass("Duplicate active ID rejected without a second execution");
        for (int i = 0; i < 31; i++) peer.Send(Execute("queued-" + i, "fixture_next"));
        PumpUntil(() => { Collect(peer); return Messages.Any(m => m.SelectToken("result.error")?.Value<string>() == "Command queue is full"); }, "overload");
        Pass("Real receive path rejects work above capacity32");
        PumpUntil(() => Result(peer, "expired") != null, "queued receipt deadline", 2500);
        Assert.IsTrue(Result(peer, "expired").SelectToken("result.error").Value<string>().Contains("timed out"));
        Assert.IsFalse(invoked.Contains("expired"));
        Assert.IsFalse(invoked.Contains("next"));
        Pass("Queued deadline responds before predecessor completes; expired handler unexecuted");
        PumpUntil(() => { Collect(peer); return Messages.Any(m => m.Value<string>("id") == "active" && (m.SelectToken("result.error")?.Value<string>() ?? "").Contains("timed out")); }, "active receipt deadline");
        Assert.IsFalse(invoked.Contains("next"));
        slow.SetResult(new { completed = true });
        PumpUntil(() => Result(peer, "next") != null, "FIFO resumes after actual completion");
        Assert.AreEqual("slow", invoked[0]);
        Assert.AreEqual("next", invoked[1]);
        Assert.IsFalse(invoked.Contains("expired"));
        Pass("Actual async settlement preserves per-connection mutation order after response timeout");
        PumpUntil(() => commands.DrainAsync().IsCompleted, "FIFO commands drained");
        string largeText = new string('x', 4 * 1024 * 1024) + "한글😀";
        handlers["fixture_large"] = new HandlerInfo("fixture_large", _ => new { data = largeText }, null);
        peer.Send(Execute("opaque-large", "fixture_large"));
        PumpUntil(() => Result(peer, "opaque-large") != null, "legacy opaque large result");
        Assert.AreEqual(largeText, Result(peer, "opaque-large").SelectToken("result.result.data").Value<string>());
        Assert.AreEqual(0, peer.Binary.Count);
        Pass("Negotiated connection preserves opaque legacy IDs with complete text envelope");
        string transferId = Guid.NewGuid().ToString("D");
        peer.OnStart = _ => peer.Send(new JObject { ["type"] = "ping" });
        peer.Send(Execute(transferId, "fixture_large"));
        JObject start = null;
        PumpUntil(() =>
        {
            Collect(peer);
            start = Messages.FirstOrDefault(m => m.Value<string>("id") == transferId && m.Value<string>("type") == "result_start");
            return start != null && peer.Binary.Count == start.Value<int>("chunk_count");
        }, "negotiated binary result transfer");
        var chunks = peer.Binary.ToArray();
        byte[] reassembled = new byte[start.Value<int>("total_bytes")];
        foreach (var chunk in chunks)
        {
            Assert.LessOrEqual(chunk.Length, 65536);
            Assert.AreEqual("ULR1" + transferId, System.Text.Encoding.ASCII.GetString(chunk, 0, 40));
            int offset = chunk[40] * 16777216 + chunk[41] * 65536 + chunk[42] * 256 + chunk[43];
            Buffer.BlockCopy(chunk, 44, reassembled, offset, chunk.Length - 44);
        }
        var rebuilt = JObject.Parse(System.Text.Encoding.UTF8.GetString(reassembled));
        Assert.AreEqual(largeText, rebuilt.SelectToken("result.result.data").Value<string>());
        var order = peer.Order.ToArray();
        int startAt = Array.IndexOf(order, "result_start");
        int pongAt = Array.IndexOf(order, "pong", startAt);
        Assert.Greater(pongAt, startAt);
        Assert.Less(pongAt, Array.LastIndexOf(order, "binary"));
        Console.WriteLine("LARGE_RESULT_BYTES: " + reassembled.Length + " CHUNKS: " + chunks.Length + " MAX_FRAME: " + chunks.Max(c => c.Length));
        Pass("Production frame sender reassembles negotiated result and interleaves pong between <=64KiB messages");
        var disconnectActive = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
        bool disconnectStarted = false;
        handlers["fixture_disconnect"] = new HandlerInfo("fixture_disconnect", null, _ => { disconnectStarted = true; return disconnectActive.Task; });
        peer.Send(Execute("disconnect-active", "fixture_disconnect"));
        PumpUntil(() => disconnectStarted, "active operation before disconnect");
        var shutdown = Invoke(client, "StopCapturedConnectionLoopsAsync",
            typeof(WebSocketTransportClient).GetMethod("CaptureConnectionLoops", PrivateInstance).Invoke(client, null), true);
        try
        {
            PumpUntil(() => EditorStatePublisher.Disposals == 1, "publisher disposed before handler settlement");
            Assert.IsFalse(shutdown.IsCompleted, "Teardown must retain the active handler's settlement barrier.");
            Assert.IsFalse(commands.DrainAsync().IsCompleted, "Disconnect cannot report a drain while legacy work can still mutate.");
            Assert.IsFalse(disconnectActive.Task.IsCompleted);
        }
        finally { disconnectActive.TrySetResult(new { completed = true }); }
        PumpUntil(() => shutdown.IsCompleted, "deterministic teardown after handler settlement");
        shutdown.GetAwaiter().GetResult();
        Assert.IsTrue(commands.DrainAsync().IsCompleted);
        Pass("Connection teardown disposes publisher promptly and drains only after legacy handler settlement");
        client.ForceStop();
    }

    private static void BenchmarkProjection()
    {
        var payload = new { status = "success", result = new { data = new string('x', 256 * 1024) + "한글😀" } };
        Func<string> before = () => JsonConvert.SerializeObject(new { type = "command_result", id = "bench", result = JToken.Parse(JsonConvert.SerializeObject(payload)) });
        Func<string> after = () => JsonConvert.SerializeObject(new { type = "command_result", id = "bench", result = TransportCommandResponse.FromObject(payload).Payload });
        Assert.AreEqual(before(), after());
        Measure("BEFORE_SYNC_ROUNDTRIP", before);
        Measure("AFTER_SYNC_STRUCTURED", after);
        Pass("Structured projection matches exact legacy bytes including Unicode");
    }

    private static void Measure(string label, Func<string> action)
    {
        for (int i = 0; i < 3; i++) action();
        GC.Collect(); GC.WaitForPendingFinalizers(); GC.Collect();
        long allocated = GC.GetAllocatedBytesForCurrentThread();
        var watch = Stopwatch.StartNew();
        for (int i = 0; i < 30; i++) action();
        watch.Stop();
        Console.WriteLine(label + "_30_RUNS_MS: " + watch.ElapsedMilliseconds + " ALLOCATED_BYTES: " + (GC.GetAllocatedBytesForCurrentThread() - allocated));
    }

    private static void Pass(string label) { _passes++; Console.WriteLine("PASS: " + label); }

    private sealed class PumpContext : SynchronizationContext
    {
        private readonly Queue<Action> _queue = new Queue<Action>();
        public override void Post(SendOrPostCallback callback, object state) { lock (_queue) _queue.Enqueue(() => callback(state)); }
        public void Drain()
        {
            while (true)
            {
                Action action;
                lock (_queue) { if (_queue.Count == 0) return; action = _queue.Dequeue(); }
                action();
            }
        }
    }
}
