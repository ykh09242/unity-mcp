using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;

internal static class StdioRegressionHarness
{
    private const BindingFlags PrivateStatic = BindingFlags.Static | BindingFlags.NonPublic;
    private static readonly Type Host = typeof(StdioBridgeHost);
    private static readonly MethodInfo Write = Host.GetMethod("WriteFrameAsync", PrivateStatic, null, new[] { typeof(NetworkStream), typeof(byte[]) }, null);
    private static readonly MethodInfo Pump = Host.GetMethod("ProcessCommands", PrivateStatic);
    private static readonly MethodInfo Read = Host.GetMethod("ReadFrameAsUtf8Async", PrivateStatic);
    private static int passed,
        failed;

    private static void Check(bool value, string message)
    {
        if (!value)
            throw new Exception(message);
    }

    private static void Test(string name, Action test)
    {
        try
        {
            test();
            passed++;
            Console.WriteLine("PASS: " + name);
        }
        catch (Exception ex)
        {
            failed++;
            Console.WriteLine("FAIL: " + name + " => " + ex.GetType().Name + ": " + ex.Message);
        }
    }

    private static Task WriteFrame(NetworkStream stream, byte[] payload) => (Task)Write.Invoke(null, new object[] { stream, payload });

    private static Task<string> ReadFrame(NetworkStream stream, CancellationToken token = default) =>
        (Task<string>)Read.Invoke(null, new object[] { stream, 80, token });

    private static async Task SettleWithin(Task task, string message)
    {
        Check(await Task.WhenAny(task, Task.Delay(2000)).ConfigureAwait(false) == task, message);
        await task.ConfigureAwait(false);
    }

    private static bool SourceDisposed(CancellationToken token)
    {
        try
        {
            var handle = token.WaitHandle;
            return false;
        }
        catch (ObjectDisposedException)
        {
            return true;
        }
    }

    // A NetworkStream requires a connected socket. Own an ephemeral loopback pair;
    // writes are overridden to observe real production CancellationTokens and hold
    // precisely header/payload I/O without scheduler/backpressure heuristics.
    private sealed class OwnedPair : IDisposable
    {
        private readonly TcpClient sender = new TcpClient();
        private TcpClient receiver;
        private readonly TcpListener listener = new TcpListener(IPAddress.Loopback, 0);
        public ObservingStream Stream;
        public NetworkStream ActualStream => sender.GetStream();
        public NetworkStream PeerStream => receiver.GetStream();
        public int ReceivedBytes => receiver.Available;

        public int FillSendBuffer()
        {
            byte[] block = new byte[4096];
            int total = 0;
            sender.Client.Blocking = false;
            try
            {
                while (total < 16 * 1024 * 1024)
                {
                    try
                    {
                        total += sender.Client.Send(block);
                    }
                    catch (SocketException error) when (error.SocketErrorCode == SocketError.WouldBlock)
                    {
                        Console.WriteLine(
                            "RUNTIME: owned socket prefill bytes="
                                + total
                                + " send_buffer="
                                + sender.SendBufferSize
                                + " receive_buffer="
                                + receiver.ReceiveBufferSize
                        );
                        return total;
                    }
                }
                throw new Exception("owned prefill did not reach SocketError.WouldBlock");
            }
            finally
            {
                try
                {
                    sender.Client.Blocking = true;
                }
                catch (ObjectDisposedException) { }
                catch (SocketException) { }
            }
        }

        public int DrainAvailable(int expected)
        {
            byte[] block = new byte[65536];
            int total = 0;
            var deadline = System.Diagnostics.Stopwatch.StartNew();
            while (total < expected && deadline.ElapsedMilliseconds < 2000)
            {
                int available = receiver.Available;
                if (available == 0)
                {
                    receiver.Client.Poll(10000, SelectMode.SelectRead);
                    continue;
                }
                total += receiver.Client.Receive(block, 0, Math.Min(block.Length, available), SocketFlags.None);
            }
            return total;
        }

        public OwnedPair(int holdWrite = 0, int failWrite = 0)
        {
            try
            {
                listener.Server.ReceiveBufferSize = 4096;
                sender.SendBufferSize = 4096;
                listener.Start();
                sender.Connect(IPAddress.Loopback, ((IPEndPoint)listener.LocalEndpoint).Port);
                receiver = listener.AcceptTcpClient();
                sender.SendBufferSize = 4096;
                receiver.ReceiveBufferSize = 4096;
                Stream = new ObservingStream(sender.Client, holdWrite, failWrite);
            }
            catch
            {
                Dispose();
                throw;
            }
            finally
            {
                listener.Stop();
            }
        }

        public void Dispose()
        {
            Stream?.Dispose();
            sender.Dispose();
            receiver?.Dispose();
            listener.Stop();
        }
    }

    private sealed class ObservingStream : NetworkStream
    {
        private readonly int holdWrite,
            failWrite;
        private readonly TaskCompletionSource<bool> held = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        public readonly List<byte[]> Writes = new List<byte[]>();
        public readonly List<CancellationToken> Tokens = new List<CancellationToken>();
        public int ActiveWaits,
            CancellationCallbacks;
        public bool IgnoreCancellation,
            CompleteOnDispose,
            ThrowOnFirstDispose;
        public int DisposeAttempts;

        public ObservingStream(Socket socket, int holdWrite, int failWrite)
            : base(socket, false)
        {
            this.holdWrite = holdWrite;
            this.failWrite = failWrite;
        }

        public override Task WriteAsync(byte[] bytes, int offset, int count, CancellationToken token)
        {
            Writes.Add(bytes.Skip(offset).Take(count).ToArray());
            Tokens.Add(token);
            if (Writes.Count == failWrite)
                return Task.FromException(new IOException("owned write failure"));
            if (Writes.Count != holdWrite)
                return Task.CompletedTask;
            return HoldAsync(token);
        }

        private async Task HoldAsync(CancellationToken token)
        {
            Interlocked.Increment(ref ActiveWaits);
            try
            {
                if (IgnoreCancellation)
                {
                    await held.Task.ConfigureAwait(false);
                    return;
                }
                using (
                    token.Register(() =>
                    {
                        Interlocked.Increment(ref CancellationCallbacks);
                        held.TrySetCanceled(token);
                    })
                )
                {
                    await held.Task.ConfigureAwait(false);
                }
            }
            finally
            {
                Interlocked.Decrement(ref ActiveWaits);
            }
        }

        public void Release() => held.TrySetResult(true);

        public void Abort() => held.TrySetException(new IOException("owned fixture cleanup"));

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                DisposeAttempts++;
                if (ThrowOnFirstDispose && DisposeAttempts == 1)
                    throw new IOException("owned dispose failure");
                if (CompleteOnDispose)
                    Release();
                else
                    Abort();
            }
            base.Dispose(disposing);
        }
    }

    private static async Task TimeoutAsync(int heldWrite)
    {
        using (var pair = new OwnedPair(heldWrite))
        {
            Task write = WriteFrame(pair.Stream, new byte[] { 1, 2, 3 });
            try
            {
                Check(pair.Stream.Tokens.Count == heldWrite, "expected write stage was not reached");
                Check(pair.Stream.Tokens.All(t => t.CanBeCanceled), "timeout token missing");
                Check(pair.Stream.Tokens.All(t => t == pair.Stream.Tokens[0]), "header/payload token changed");
                Check(
                    await Task.WhenAny(write, Task.Delay(2000)).ConfigureAwait(false) == write,
                    "80 ms timeout did not cancel held write before 2000 ms watchdog"
                );
                try
                {
                    await write.ConfigureAwait(false);
                    throw new Exception("held write succeeded instead of canceling");
                }
                catch (OperationCanceledException) { }
                Check(pair.Stream.CancellationCallbacks == 1, "cancellation callback missing/duplicated");
                Check(pair.Stream.ActiveWaits == 0, "held write/registration did not settle");
                Check(SourceDisposed(pair.Stream.Tokens[0]), "timeout CTS not disposed after cancellation");
            }
            finally
            {
                pair.Stream.Abort();
                try
                {
                    await SettleWithin(write, "owned cleanup could not settle write").ConfigureAwait(false);
                }
                catch (IOException) { }
                catch (OperationCanceledException) { }
                Check(pair.Stream.ActiveWaits == 0, "owned wait leaked on cleanup");
            }
        }
    }

    private static async Task LifetimeAsync()
    {
        using (var pair = new OwnedPair(2))
        {
            Task write = WriteFrame(pair.Stream, new byte[] { 4 });
            try
            {
                Check(!SourceDisposed(pair.Stream.Tokens[0]), "timeout CTS disposed while payload write still pending");
                pair.Stream.Release();
                await SettleWithin(write, "normal write did not settle").ConfigureAwait(false);
                Check(SourceDisposed(pair.Stream.Tokens[0]), "timeout CTS not disposed after success");
            }
            finally
            {
                pair.Stream.Abort();
                try
                {
                    await SettleWithin(write, "owned cleanup could not settle write").ConfigureAwait(false);
                }
                catch (IOException) { }
                Check(pair.Stream.ActiveWaits == 0, "owned wait leaked on cleanup");
            }
        }
    }

    private static async Task NormalFrameAsync(byte[] payload)
    {
        using (var pair = new OwnedPair())
        {
            await SettleWithin(WriteFrame(pair.Stream, payload), "normal frame pending").ConfigureAwait(false);
            byte[] expected = new byte[8];
            expected[7] = (byte)payload.Length;
            Check(pair.Stream.Writes.Count == 2, "expected header and payload writes");
            Check(pair.Stream.Writes[0].SequenceEqual(expected), "length header changed");
            Check(pair.Stream.Writes[1].SequenceEqual(payload), "payload changed");
            Check(SourceDisposed(pair.Stream.Tokens[0]), "CTS not disposed after synchronous success");
            Check(pair.Stream.CanWrite, "successful frame closed its stream");
        }
    }

    private static async Task FailureAsync()
    {
        using (var pair = new OwnedPair(failWrite: 2))
        {
            try
            {
                await WriteFrame(pair.Stream, new byte[] { 7 }).ConfigureAwait(false);
                throw new Exception("failure swallowed");
            }
            catch (IOException error)
            {
                Check(error.Message == "owned write failure", "failure changed");
            }
            Check(SourceDisposed(pair.Stream.Tokens[0]), "CTS not disposed after write failure");
        }
    }

    private static async Task NullPayloadAsync()
    {
        using (var pair = new OwnedPair())
        {
            try
            {
                await WriteFrame(pair.Stream, null).ConfigureAwait(false);
                throw new Exception("null payload accepted");
            }
            catch (ArgumentNullException) { }
            Check(pair.Stream.Writes.Count == 0, "invalid payload wrote bytes");
        }
    }

    private static async Task ActualSocketTimeoutAsync(bool prefill)
    {
        MethodInfo arrayWrite = typeof(NetworkStream).GetMethod("WriteAsync", new[] { typeof(byte[]), typeof(int), typeof(int), typeof(CancellationToken) });
        Console.WriteLine("RUNTIME: array WriteAsync declared by " + arrayWrite.DeclaringType.FullName);
        using (var pair = new OwnedPair())
        using (NetworkStream stream = pair.ActualStream)
        {
            Task fill = null;
            if (prefill)
            {
                // Some Mono socket APIs can synchronously wait before returning.
                // Keep even the fill call behind the watchdog/owned socket cleanup.
                fill = Task.Run(() => pair.FillSendBuffer());
                Task settled = await Task.WhenAny(fill, Task.Delay(50)).ConfigureAwait(false);
                if (settled == fill)
                    await fill.ConfigureAwait(false);
                Console.WriteLine("RUNTIME: prefill pending=" + !fill.IsCompleted);
            }
            // The peer initially never reads. The prefilled case must fail on its
            // deadline; a non-prefilled success is drained to verify completeness.
            Task write = Task.Run(() => WriteFrame(stream, new byte[16 * 1024 * 1024]));
            try
            {
                if (prefill)
                    Check(!write.IsCompleted, "owned socket did not backpressure the production frame write");
                for (int count = 0; count < 20 && pair.ReceivedBytes == 0; count++)
                    await Task.Delay(1).ConfigureAwait(false);
                Check(pair.ReceivedBytes > 0, "owned peer did not receive frame bytes");
                Check(
                    await Task.WhenAny(write, Task.Delay(2000)).ConfigureAwait(false) == write,
                    "real Mono socket write ignored 80 ms timeout before 2000 ms watchdog"
                );
                try
                {
                    await write.ConfigureAwait(false);
                    int expected = 16 * 1024 * 1024 + 8;
                    int received = pair.DrainAvailable(expected);
                    Console.WriteLine("RUNTIME: unexpected success peer bytes=" + received + " frame_bytes=" + expected + " prefill=" + prefill);
                    Check(received == expected, "successful frame was incomplete; received=" + received + "/" + expected);
                    Check(!prefill, "prefilled unread frame succeeded instead of timing out");
                }
                catch (OperationCanceledException) { }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
            }
            finally
            {
                stream.Dispose(); // Owns its socket, exactly like TcpClient.GetStream in production.
                try
                {
                    await SettleWithin(write, "owned real socket write failed to settle after socket disposal").ConfigureAwait(false);
                }
                catch (OperationCanceledException) { }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
                if (fill != null)
                {
                    try
                    {
                        await SettleWithin(fill, "owned prefill did not settle after cleanup").ConfigureAwait(false);
                    }
                    catch (ObjectDisposedException) { }
                    catch (SocketException) { }
                    catch (IOException) { }
                }
            }
        }
    }

    private static void Set(string name, object value) => Host.GetField(name, PrivateStatic).SetValue(null, value);

    private static async Task ActualStreamReuseAsync()
    {
        using (var pair = new OwnedPair())
        using (NetworkStream stream = pair.ActualStream)
        {
            await SettleWithin(Task.Run(() => WriteFrame(stream, new byte[] { 1, 2, 3 })), "first real small frame pending").ConfigureAwait(false);
            await Task.Delay(120).ConfigureAwait(false); // Past the injected 80 ms deadline.
            Check(stream.CanWrite, "successful write left a timer that closed the reusable stream");
            await SettleWithin(Task.Run(() => WriteFrame(stream, new byte[] { 4, 5, 6 })), "second real small frame pending").ConfigureAwait(false);
            Check(pair.DrainAvailable(22) == 22, "two complete small frames were not received");
        }
    }

    private static async Task SilentReadTimeoutAsync()
    {
        using (var pair = new OwnedPair())
        using (NetworkStream stream = pair.ActualStream)
        {
            Task<string> read = Task.Run(() => ReadFrame(stream));
            try
            {
                Check(
                    await Task.WhenAny(read, Task.Delay(2000)).ConfigureAwait(false) == read,
                    "silent real Mono read ignored 80 ms timeout before 2000 ms watchdog"
                );
                try
                {
                    await read.ConfigureAwait(false);
                    throw new Exception("silent peer produced a frame");
                }
                catch (IOException error)
                {
                    Check(error.Message == "Read timed out", "timeout outcome changed: " + error.Message);
                }
            }
            finally
            {
                stream.Dispose();
                try
                {
                    await SettleWithin(read, "owned silent read failed to settle after disposal").ConfigureAwait(false);
                }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
                catch (OperationCanceledException) { }
            }
        }
    }

    private static byte[] Frame(byte[] payload)
    {
        byte[] frame = new byte[8 + payload.Length];
        frame[7] = (byte)payload.Length;
        Buffer.BlockCopy(payload, 0, frame, 8, payload.Length);
        return frame;
    }

    private static async Task PartialPayloadReadAsync()
    {
        using (var pair = new OwnedPair())
        using (NetworkStream stream = pair.ActualStream)
        {
            byte[] prefix = Frame(new byte[] { (byte)'p', (byte)'i', (byte)'n' }).Take(9).ToArray();
            await SettleWithin(pair.PeerStream.WriteAsync(prefix, 0, prefix.Length), "owned partial frame send pending").ConfigureAwait(false);
            Task<string> read = Task.Run(() => ReadFrame(stream));
            try
            {
                Check(await Task.WhenAny(read, Task.Delay(2000)).ConfigureAwait(false) == read, "partial payload read ignored timeout");
                try
                {
                    await read.ConfigureAwait(false);
                    throw new Exception("partial payload accepted");
                }
                catch (IOException error)
                {
                    Check(error.Message == "Read timed out", "partial payload timeout outcome changed");
                }
            }
            finally
            {
                stream.Dispose();
                try
                {
                    await SettleWithin(read, "partial read cleanup pending").ConfigureAwait(false);
                }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
                catch (OperationCanceledException) { }
            }
        }
    }

    private static async Task FragmentedReadReuseAsync()
    {
        using (var pair = new OwnedPair())
        using (NetworkStream stream = pair.ActualStream)
        {
            byte[] frame = Frame(new byte[] { (byte)'p', (byte)'i', (byte)'n', (byte)'g' });
            Task<string> read = Task.Run(() => ReadFrame(stream));
            await pair.PeerStream.WriteAsync(frame, 0, 4).ConfigureAwait(false);
            await Task.Delay(5).ConfigureAwait(false);
            await pair.PeerStream.WriteAsync(frame, 4, 6).ConfigureAwait(false);
            await Task.Delay(5).ConfigureAwait(false);
            await pair.PeerStream.WriteAsync(frame, 10, 2).ConfigureAwait(false);
            await SettleWithin(read, "fragmented read did not settle").ConfigureAwait(false);
            Check(read.Result == "ping", "fragmented UTF8 payload changed");
            await Task.Delay(120).ConfigureAwait(false);
            Check(stream.CanRead, "successful read left deadline callback on reusable stream");
            await pair.PeerStream.WriteAsync(frame, 0, frame.Length).ConfigureAwait(false);
            Task<string> again = Task.Run(() => ReadFrame(stream));
            await SettleWithin(again, "reused frame read pending").ConfigureAwait(false);
            Check(again.Result == "ping", "reused read changed payload");
        }
    }

    private static async Task ExternalReadCancellationAsync(bool raceClose)
    {
        using (var pair = new OwnedPair())
        using (NetworkStream stream = pair.ActualStream)
        using (var cancel = new CancellationTokenSource())
        {
            Task<string> read = Task.Run(() => ReadFrame(stream, cancel.Token));
            await Task.Delay(10).ConfigureAwait(false);
            cancel.Cancel();
            if (raceClose)
                stream.Dispose();
            try
            {
                Check(await Task.WhenAny(read, Task.Delay(2000)).ConfigureAwait(false) == read, "external cancellation did not release read");
                try
                {
                    await read.ConfigureAwait(false);
                    throw new Exception("external read cancellation succeeded");
                }
                catch (OperationCanceledException error)
                {
                    Check(error.CancellationToken == cancel.Token, "external cancellation lost caller token");
                }
            }
            finally
            {
                stream.Dispose();
                try
                {
                    await SettleWithin(read, "externally canceled read cleanup pending").ConfigureAwait(false);
                }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
                catch (OperationCanceledException) { }
            }
        }
    }

    private static async Task ClosedStreamAsync(bool reading)
    {
        using (var pair = new OwnedPair())
        {
            NetworkStream stream = pair.ActualStream;
            stream.Dispose();
            Task operation = Task.Run(() => reading ? (Task)ReadFrame(stream) : WriteFrame(stream, new byte[] { 1 }));
            Check(await Task.WhenAny(operation, Task.Delay(2000)).ConfigureAwait(false) == operation, "closed stream operation pending");
            try
            {
                await operation.ConfigureAwait(false);
                throw new Exception("closed stream operation succeeded");
            }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
            await Task.Delay(120).ConfigureAwait(false); // A stale callback must not crash the timer thread.
        }
    }

    private static async Task TimeoutDisposalEdgeAsync(bool falseSuccess)
    {
        using (var pair = new OwnedPair(2))
        {
            pair.Stream.IgnoreCancellation = falseSuccess;
            pair.Stream.CompleteOnDispose = falseSuccess;
            pair.Stream.ThrowOnFirstDispose = !falseSuccess;
            Task write = WriteFrame(pair.Stream, new byte[] { 1 });
            try
            {
                Check(await Task.WhenAny(write, Task.Delay(2000)).ConfigureAwait(false) == write, "timeout disposal edge did not settle");
                try
                {
                    await write.ConfigureAwait(false);
                    throw new Exception("deadline cleanup was reported as success");
                }
                catch (OperationCanceledException) { }
                Check(pair.Stream.DisposeAttempts >= 1, "deadline callback did not attempt owned stream disposal");
                Check(SourceDisposed(pair.Stream.Tokens[0]) && pair.Stream.ActiveWaits == 0, "deadline edge leaked CTS/wait");
            }
            finally
            {
                pair.Stream.Abort();
                try
                {
                    await SettleWithin(write, "disposal edge cleanup pending").ConfigureAwait(false);
                }
                catch (OperationCanceledException) { }
                catch (IOException) { }
            }
        }
    }

    private static object Get(string name) => Host.GetField(name, PrivateStatic).GetValue(null);

    private static Dictionary<string, QueuedCommand> ResetQueue()
    {
        var queue = (Dictionary<string, QueuedCommand>)Get("commandQueue");
        queue.Clear();
        Set("isRunning", true);
        Set("ownedEndpoint", true); // WriteHeartbeat exits before any discovery/prefs/file I/O.
        Set("processingCommands", 0);
        Set("nextHeartbeatAt", 0d);
        TransportCommandDispatcher.Calls = 0;
        TransportCommandDispatcher.Settlement = Task.CompletedTask;
        return queue;
    }

    private static QueuedCommand Command(string json = "ping", CancellationToken cancel = default) =>
        new QueuedCommand
        {
            CommandJson = json,
            Tcs = new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously),
            OwnerCancellation = cancel,
        };

    private static void GuardOrdering(string source)
    {
        string body = File.ReadAllText(source);
        int start = body.IndexOf("private static void ProcessCommands()", StringComparison.Ordinal);
        body = body.Substring(start, body.IndexOf("private static void ExecuteQueuedCommand", start, StringComparison.Ordinal) - start);
        int scan = body.IndexOf("var canceled = commandQueue.Where", StringComparison.Ordinal);
        int firstGuard = body.IndexOf("if (commandQueue.Count == 0)", StringComparison.Ordinal);
        Check(firstGuard >= 0 && firstGuard < scan, "empty guard still follows LINQ cancellation scan");
        int secondGuard = body.IndexOf("if (commandQueue.Count == 0)", firstGuard + 1, StringComparison.Ordinal);
        Check(secondGuard > scan, "post-prune empty guard removed");
    }

    private static void EmptyTick()
    {
        var queue = ResetQueue();
        Pump.Invoke(null, null);
        Check(queue.Count == 0 && TransportCommandDispatcher.Calls == 0, "empty tick executed work");
        Check((int)Get("processingCommands") == 0, "empty return failed to reset processing guard");
        Check((double)Get("nextHeartbeatAt") > UnityEditor.EditorApplication.timeSinceStartup, "empty tick skipped heartbeat scheduling");
    }

    private static void CanceledOnly()
    {
        var queue = ResetQueue();
        using (var cancel = new CancellationTokenSource())
        {
            cancel.Cancel();
            var item = Command(cancel: cancel.Token);
            queue.Add("canceled", item);
            Pump.Invoke(null, null);
            Check(item.Tcs.Task.IsCanceled && queue.Count == 0, "canceled item was not pruned");
            Check((int)Get("processingCommands") == 0 && TransportCommandDispatcher.Calls == 0, "canceled-only path failed to exit/reset");
        }
    }

    private static void CanceledThenLive()
    {
        var queue = ResetQueue();
        using (var cancel = new CancellationTokenSource())
        {
            cancel.Cancel();
            var abandoned = Command(cancel: cancel.Token);
            var live = Command();
            queue.Add("canceled", abandoned);
            queue.Add("live", live);
            Pump.Invoke(null, null);
            Check(abandoned.Tcs.Task.IsCanceled && live.Tcs.Task.IsCompleted && live.Tcs.Task.Result.Contains("pong"), "pruning blocked following live work");
            Check(queue.Count == 0, "completed work retained");
        }
    }

    private static void ExecutingBarrier()
    {
        var queue = ResetQueue();
        using (var cancel = new CancellationTokenSource())
        {
            cancel.Cancel();
            var executing = Command(cancel: cancel.Token);
            executing.IsExecuting = true;
            var live = Command();
            queue.Add("executing", executing);
            queue.Add("live", live);
            Pump.Invoke(null, null);
            Check(queue.Count == 2 && !executing.Tcs.Task.IsCompleted && !live.Tcs.Task.IsCompleted, "executing cancellation released ordering barrier");
            Check((int)Get("processingCommands") == 0, "barrier return failed to reset processing guard");
        }
    }

    private static async Task SettlementBarrierAsync()
    {
        var queue = ResetQueue();
        var settlement = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        TransportCommandDispatcher.Settlement = settlement.Task;
        var first = Command("{\"type\":\"owned-command\"}");
        var second = Command();
        queue.Add("first", first);
        queue.Add("second", second);
        try
        {
            Pump.Invoke(null, null);
            Check(first.Tcs.Task.IsCompleted && first.IsExecuting && queue.Count == 2, "first response did not retain unsettled command");
            Pump.Invoke(null, null);
            Check(!second.Tcs.Task.IsCompleted && TransportCommandDispatcher.Calls == 1, "unsettled execution allowed later work");
        }
        finally
        {
            settlement.TrySetResult(true);
        }
        Func<bool> containsFirst = () =>
        {
            lock (Get("lockObj"))
                return queue.ContainsKey("first");
        };
        for (int count = 0; count < 200 && containsFirst(); count++)
            await Task.Delay(5).ConfigureAwait(false);
        Check(!containsFirst(), "settled command not removed");
        Pump.Invoke(null, null);
        Check(second.Tcs.Task.IsCompleted && queue.Count == 0, "settlement did not release next command");
    }

    public static int Main(string[] args)
    {
        if (args.Length != 1)
        {
            Console.WriteLine("FAIL: original host source path required");
            return 1;
        }
        Test("header write timeout cancels and cleans up", () => TimeoutAsync(1).GetAwaiter().GetResult());
        Test("payload write timeout cancels and cleans up", () => TimeoutAsync(2).GetAwaiter().GetResult());
        Test("timeout CTS lives through pending write and disposes after success", () => LifetimeAsync().GetAwaiter().GetResult());
        Test("normal frame preserves length and payload", () => NormalFrameAsync(new byte[] { 9, 8, 7 }).GetAwaiter().GetResult());
        Test("zero-length frame compatibility preserved", () => NormalFrameAsync(Array.Empty<byte>()).GetAwaiter().GetResult());
        Test("write failures propagate and dispose timeout CTS", () => FailureAsync().GetAwaiter().GetResult());
        Test("null payload rejected before any write", () => NullPayloadAsync().GetAwaiter().GetResult());
        Test("real Mono large frame is complete on success or fails on deadline", () => ActualSocketTimeoutAsync(false).GetAwaiter().GetResult());
        Test("prefilled real Mono socket timeout fails and settles", () => ActualSocketTimeoutAsync(true).GetAwaiter().GetResult());
        Test("successful real stream stays reusable beyond disposed deadline", () => ActualStreamReuseAsync().GetAwaiter().GetResult());
        Test("silent real Mono frame header read times out and settles", () => SilentReadTimeoutAsync().GetAwaiter().GetResult());
        Test("partial real Mono frame payload read times out and settles", () => PartialPayloadReadAsync().GetAwaiter().GetResult());
        Test("fragmented real frame reads preserve payload and reusable stream", () => FragmentedReadReuseAsync().GetAwaiter().GetResult());
        Test("external read cancellation retains caller token", () => ExternalReadCancellationAsync(false).GetAwaiter().GetResult());
        Test("external cancellation and repeated stream disposal settle safely", () => ExternalReadCancellationAsync(true).GetAwaiter().GetResult());
        Test("already closed real stream read fails without stale timer callback", () => ClosedStreamAsync(true).GetAwaiter().GetResult());
        Test("already closed real stream write fails without stale timer callback", () => ClosedStreamAsync(false).GetAwaiter().GetResult());
        Test("deadline rejects successful partial-cleanup completion", () => TimeoutDisposalEdgeAsync(true).GetAwaiter().GetResult());
        Test("throwing deadline disposal callback cannot crash timer", () => TimeoutDisposalEdgeAsync(false).GetAwaiter().GetResult());
        Test("empty queue guard precedes LINQ; post-prune guard retained", () => GuardOrdering(args[0]));
        Test("empty editor tick retains heartbeat and resets processing guard", EmptyTick);
        Test("canceled-only queue pruned and processing guard reset", CanceledOnly);
        Test("canceled queued work does not block following live request", CanceledThenLive);
        Test("canceled executing request keeps later mutations ordered", ExecutingBarrier);
        Test("response completion retains barrier until actual settlement", () => SettlementBarrierAsync().GetAwaiter().GetResult());
        Console.WriteLine("RESULT: " + passed + "/" + (passed + failed) + " focused stdio checks passed");
        return failed == 0 ? 0 : 1;
    }
}
