using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.WebSockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

// Production transport/dispatcher/registry/writer, Unity API stubs, ephemeral loopback peers.
internal static class Phase12WebSocketHarness
{
    private const BindingFlags Private = BindingFlags.NonPublic | BindingFlags.Instance;
    private static readonly PumpContext Context = new PumpContext();
    private static int _passes,
        _failures;

    public static int Main(string[] args)
    {
        SynchronizationContext.SetSynchronizationContext(Context);
        TransportCommandDispatcher.ExecuteCommandJsonAsync("ping", CancellationToken.None).GetAwaiter().GetResult();
        string selection = args.Length == 0 ? "all" : args[0];
        if (selection == "architecture")
            Run("existing_architecture_real_socket", RunArchitecture);
        foreach (string mode in new[] { "reconnect", "force", "dispose" })
        foreach (bool cooperative in new[] { false, true })
            if (selection == "all" || selection == mode)
                Run(mode + (cooperative ? "_cooperative_cleanup" : "_legacy_handler"), () => HeldHandler(mode, cooperative));
        if (selection == "all" || selection == "bytes")
        {
            Run("UTF8_exact_bytes_and_framing", ExactBytes);
            Run("UTF8_caps_and_transport_fallback", CapsAndFallback);
        }
        if (selection == "all" || selection == "bytes" || selection == "inputs")
            Run("PreparedJson_rejects_default_null_and_preserves_empty", PreparedInputs);
        Console.WriteLine("PASS_TOTAL: " + _passes + " FAIL_TOTAL: " + _failures);
        return _failures == 0 ? 0 : 1;
    }

    private static void Run(string name, Action test)
    {
        try
        {
            test();
            _passes++;
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception ex)
        {
            _failures++;
            Console.WriteLine("FAIL: " + name + " " + ex);
        }
    }

    private static void RunArchitecture()
    {
        Type harness = typeof(TransportArchitectureHarness);
        var context = (SynchronizationContext)harness.GetField("Context", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
        SynchronizationContext.SetSynchronizationContext(context);
        typeof(TransportCommandDispatcher).GetField("_mainThreadContext", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, context);
        harness.GetMethod("ExerciseRealSocket", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
    }

    private static void HeldHandler(string mode, bool cooperative)
    {
        // Given: A has started on an old real socket; its final mutation is held by a gate.
        var handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
        var gate = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var events = new List<string>();
        int starts = 0,
            cleanup = 0,
            mutations = 0;
        Func<Task<object>> finish = async () =>
        {
            await gate.Task;
            events.Add("A settled");
            return new { done = true };
        };
        handlers["phase12_A"] = cooperative
            ? HandlerInfo.Cooperative(
                "phase12_A",
                async (_, token) =>
                {
                    starts++;
                    try
                    {
                        await Task.Delay(Timeout.Infinite, token);
                        return null;
                    }
                    finally
                    {
                        cleanup++;
                        await finish();
                    }
                }
            )
            : new HandlerInfo(
                "phase12_A",
                null,
                _ =>
                {
                    starts++;
                    return finish();
                }
            );
        handlers["phase12_B"] = new HandlerInfo(
            "phase12_B",
            _ =>
            {
                mutations++;
                events.Add("B started");
                return new { done = true };
            },
            null
        );
        using var original = new LocalWebSocketPeer();
        using var replacement = new LocalWebSocketPeer();
        var client = new WebSocketTransportClient(
            null,
            new WebSocketTransportClient.OwnedConnectionOptions(replacement.Uri, "fixture-only", "Phase12", "phase12", "Fixture", "6000.0.69f1")
        );
        using var socket = new ClientWebSocket();
        using var lifecycle = new CancellationTokenSource();
        using var connection = CancellationTokenSource.CreateLinkedTokenSource(lifecycle.Token);
        var work = new ConnectionCommandWork(connection.Token);
        Task restart = null;
        try
        {
            Complete(socket.ConnectAsync(original.Uri, connection.Token));
            Complete(original.Ready);
            Set(client, "_socket", socket);
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_connectionCts", connection);
            Set(client, "_commandWork", work);
            Set(client, "_endpointUri", replacement.Uri);
            Set(client, "_keepAliveInterval", TimeSpan.FromMinutes(1));
            typeof(WebSocketTransportClient).GetMethod("StartBackgroundLoops", Private).Invoke(client, new object[] { connection.Token });
            original.Send(Execute("A", "phase12_A"));
            PumpUntil(() => starts == 1, "A invocation");

            // When: the old connection is canceled by automatic closure, forced restart, or Dispose.
            if (mode == "reconnect")
                Complete(Invoke(client, "HandleSocketClosureAsync", "fixture closure", connection.Token));
            else if (mode == "force")
            {
                client.ForceStop();
                client.ForceStop();
                restart = client.StartAsync();
            }
            else
            {
                // Release on a watchdog only to avoid hanging the fixture if synchronous Dispose regresses.
                var watchdog = new CancellationTokenSource();
                Task.Delay(1500, watchdog.Token)
                    .ContinueWith(
                        t =>
                        {
                            if (!t.IsCanceled)
                                gate.TrySetResult(true);
                        },
                        TaskScheduler.Default
                    );
                client.Dispose();
                watchdog.Cancel();
                watchdog.Dispose();
                Assert.IsFalse(gate.Task.IsCompleted, "Dispose blocked until the watchdog released main-thread cleanup");
            }
            Task drain = work.DrainAsync();
            PumpWindow(150, () => drain.IsCompleted || replacement.Ready.IsCompleted);
            if (mode != "dispose" && drain.IsCompleted)
            {
                Complete(replacement.Ready);
                replacement.Send(Execute("B", "phase12_B"));
                PumpUntil(() => mutations == 1, "overtaking replacement B");
            }
            Console.WriteLine(
                "HELD: mode="
                    + mode
                    + " cooperative="
                    + cooperative
                    + " drain="
                    + drain.IsCompleted
                    + " replacement="
                    + replacement.Ready.IsCompleted
                    + " mutations="
                    + mutations
                    + " cleanup="
                    + cleanup
            );

            // Then: actual settlement stays outstanding and no replacement handler can overtake A.
            Assert.IsFalse(drain.IsCompleted, "Owned work drained while A could still mutate");
            Assert.IsFalse(replacement.Ready.IsCompleted, "Replacement connection was admitted before A settled");
            Assert.AreEqual(0, mutations);
            if (cooperative)
                Assert.AreEqual(1, cleanup, "Cancellation must enter cooperative cleanup");
            gate.SetResult(true);
            Complete(drain);
            if (mode != "dispose")
            {
                Complete(replacement.Ready);
                if (restart != null)
                    Complete(restart);
                replacement.Send(Execute("B", "phase12_B"));
                PumpUntil(() => mutations == 1, "replacement B mutation");
                CollectionAssert.AreEqual(new[] { "A settled", "B started" }, events);
                if (mode == "force")
                    Assert.IsTrue(((Task)Get(client, "_forcedCommandSettlement")).IsCompleted);
            }
        }
        finally
        {
            gate.TrySetResult(true);
            client.ForceStop();
            Complete(work.DrainAsync());
            client.Dispose();
            handlers.Remove("phase12_A");
            handlers.Remove("phase12_B");
        }
    }

    private static void ExactBytes()
    {
        // Given: Unicode pairs at staging boundaries plus isolated surrogates.
        string id = "01234567-89ab-cdef-0123-456789abcdef";
        string text = new string('x', 16383) + "😀" + "\ud800x\udc00한글" + new string('a', 400000);
        foreach (bool negotiated in new[] { false, true })
        foreach (bool gzip in new[] { false, true })
        {
            string payload = gzip ? text + new string('a', 1024 * 1024) : text;
            var frames = new List<(byte[] bytes, WebSocketMessageType kind)>();
            // When: transmit through the existing public string API and the safe measured path when present.
            Complete(SendJson(id, payload, negotiated, gzip, frames));
            byte[] expected = Encoding.UTF8.GetBytes(payload);
            if (frames.Count == 1)
                CollectionAssert.AreEqual(expected, frames[0].bytes);
            else
            {
                JObject start = JObject.Parse(Encoding.UTF8.GetString(frames[0].bytes));
                byte[] wire = frames.Skip(1).SelectMany(f => f.bytes.Skip(LargeResultWriter.HeaderBytes)).ToArray();
                Assert.AreEqual(wire.Length, start.Value<int>("total_bytes"));
                Assert.AreEqual(frames.Count - 1, start.Value<int>("chunk_count"));
                foreach (var frame in frames.Skip(1))
                    Assert.LessOrEqual(frame.bytes.Length, LargeResultWriter.MaxFrameBytes);
                if (start.Value<string>("encoding") == "gzip")
                {
                    Assert.AreEqual(expected.Length, start.Value<int>("decoded_bytes"));
                    using var source = new MemoryStream(wire);
                    using var unzip = new GZipStream(source, CompressionMode.Decompress);
                    using var decoded = new MemoryStream();
                    unzip.CopyTo(decoded);
                    wire = decoded.ToArray();
                }
                // Then: reconstructed final bytes equal Encoding.UTF8, including fallback semantics.
                CollectionAssert.AreEqual(expected, wire);
            }
            Console.WriteLine("BYTES: negotiated=" + negotiated + " gzip=" + gzip + " expected=" + expected.Length + " frames=" + frames.Count);
        }
    }

    private static Task SendJson(string id, string text, bool negotiated, bool gzip, List<(byte[] bytes, WebSocketMessageType kind)> frames)
    {
        Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> send = (segment, kind, token) =>
        {
            frames.Add((segment.ToArray(), kind));
            return Task.CompletedTask;
        };
        Type preparedType = typeof(LargeResultWriter).GetNestedType("PreparedJson", BindingFlags.NonPublic);
        if (preparedType == null)
            return LargeResultWriter.SendJsonAsync(id, text, negotiated, send, CancellationToken.None, gzip, gzip);
        object prepared = Activator.CreateInstance(preparedType, BindingFlags.Instance | BindingFlags.NonPublic, null, new object[] { text }, null);
        return (Task)
            typeof(LargeResultWriter)
                .GetMethod("SendPreparedJsonAsync", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, new[] { (object)id, prepared, negotiated, send, CancellationToken.None, gzip, gzip });
    }

    private static void CapsAndFallback()
    {
        // Given: the byte cap applies even when the string character count is below it.
        string oversized = new string('한', LargeResultWriter.MaxResultBytes / 3 + 1);
        int frames = 0;
        Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> rejectSend = (_, __, ___) =>
        {
            frames++;
            return Task.CompletedTask;
        };
        // When: an oversized result reaches the writer's public path.
        Assert.ThrowsAsync<InvalidOperationException>(async () =>
            await LargeResultWriter.SendJsonAsync("legacy", oversized, false, rejectSend, CancellationToken.None)
        );
        // Then: no oversized frame is emitted; the transport substitutes a bounded error envelope.
        Assert.AreEqual(0, frames);
        var captured = new List<(byte[] bytes, WebSocketMessageType kind)>();
        Assert.ThrowsAsync<InvalidOperationException>(async () => await SendJson("legacy", oversized, false, false, captured));
        Assert.AreEqual(0, captured.Count);
        using var peer = new LocalWebSocketPeer();
        using var socket = new ClientWebSocket();
        using var lifecycle = new CancellationTokenSource();
        using var connection = new CancellationTokenSource();
        var client = new WebSocketTransportClient();
        try
        {
            Complete(socket.ConnectAsync(peer.Uri, connection.Token));
            Complete(peer.Ready);
            Set(client, "_socket", socket);
            Set(client, "_lifecycleCts", lifecycle);
            Set(client, "_connectionCts", connection);
            Complete(Invoke(client, "SendCommandResultAsync", "fallback", new { data = oversized }, connection.Token));
            PumpUntil(() => peer.Received.Count == 1, "fallback envelope");
            peer.Received.TryDequeue(out JObject response);
            string expected = JsonConvert.SerializeObject(
                new
                {
                    type = "command_result",
                    id = "fallback",
                    result = new { status = "error", error = "Command result exceeds the transport size limit" },
                }
            );
            Assert.AreEqual(expected, response.ToString(Formatting.None));
            Console.WriteLine("FALLBACK_UTF8_BYTES: " + Encoding.UTF8.GetByteCount(expected));
        }
        finally
        {
            client.ForceStop();
            client.Dispose();
        }
    }

    private static void PreparedInputs()
    {
        // Given: default has no initialized text; null cannot acquire a trusted byte count.
        var frames = new List<(int bytes, WebSocketMessageType kind)>();
        Func<ArraySegment<byte>, WebSocketMessageType, CancellationToken, Task> send = (segment, kind, token) =>
        {
            frames.Add((segment.Count, kind));
            return Task.CompletedTask;
        };
        // When: these invalid inputs cross the writer boundary.
        Assert.Throws<ArgumentNullException>(() => LargeResultWriter.SendPreparedJsonAsync("legacy", default, false, send, CancellationToken.None));
        Assert.Throws<ArgumentNullException>(() => new LargeResultWriter.PreparedJson(null));
        Assert.Throws<ArgumentNullException>(() => LargeResultWriter.SendJsonAsync("legacy", null, false, send, CancellationToken.None));
        // Then: rejection happens synchronously with no frame, while valid empty text emits one empty text frame.
        Assert.AreEqual(0, frames.Count);
        Complete(LargeResultWriter.SendPreparedJsonAsync("legacy", new LargeResultWriter.PreparedJson(string.Empty), false, send, CancellationToken.None));
        Assert.AreEqual(1, frames.Count);
        Assert.AreEqual(0, frames[0].bytes);
        Assert.AreEqual(WebSocketMessageType.Text, frames[0].kind);
    }

    private static JObject Execute(string id, string name) =>
        new JObject
        {
            ["type"] = "execute",
            ["id"] = id,
            ["name"] = name,
            ["timeout"] = 30,
        };

    private static void Set(object target, string name, object value) => target.GetType().GetField(name, Private).SetValue(target, value);

    private static object Get(object target, string name) => target.GetType().GetField(name, Private).GetValue(target);

    private static Task Invoke(object target, string name, params object[] args) => (Task)target.GetType().GetMethod(name, Private).Invoke(target, args);

    private static void Complete(Task task)
    {
        PumpUntil(() => task.IsCompleted, "task completion");
        task.GetAwaiter().GetResult();
    }

    private static void PumpUntil(Func<bool> done, string label)
    {
        var clock = Stopwatch.StartNew();
        while (!done())
        {
            Context.Drain();
            UnityEditor.EditorApplication.Pump();
            if (clock.ElapsedMilliseconds > 5000)
                throw new TimeoutException(label);
            Thread.Sleep(1);
        }
        Context.Drain();
    }

    private static void PumpWindow(int milliseconds, Func<bool> early)
    {
        var clock = Stopwatch.StartNew();
        while (clock.ElapsedMilliseconds < milliseconds && !early())
        {
            Context.Drain();
            UnityEditor.EditorApplication.Pump();
            Thread.Sleep(1);
        }
        Context.Drain();
    }

    private sealed class PumpContext : SynchronizationContext
    {
        private readonly Queue<Action> _callbacks = new Queue<Action>();

        public override void Post(SendOrPostCallback callback, object state)
        {
            lock (_callbacks)
                _callbacks.Enqueue(() => callback(state));
        }

        public void Drain()
        {
            while (true)
            {
                Action callback;
                lock (_callbacks)
                {
                    if (_callbacks.Count == 0)
                        return;
                    callback = _callbacks.Dequeue();
                }
                callback();
            }
        }
    }
}
