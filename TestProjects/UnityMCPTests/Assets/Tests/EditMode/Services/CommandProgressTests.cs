using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class CommandProgressTests
    {
        [Test]
        public void LatestSlot_CoalescesOnlyIncreasingCountsForOneFixedTotal()
        {
            using var progress = new NativeProgressProbe();
            for (int completed = 1; completed <= 100; completed++)
                progress.Report(completed, 100);
            Assert.IsTrue(progress.TryTake(out var update));
            Assert.AreEqual(100, update.Completed);
            Assert.AreEqual(100, update.Total);
            Assert.AreEqual("BatchItems", update.Stage);
            Assert.IsFalse(progress.TryTake(out _), "A burst retains one update, not a callback queue.");
            progress.Report(99, 100);
            progress.Report(100, 100);
            progress.Report(100, 99);
            Assert.IsFalse(progress.TryTake(out _), "Duplicate, decreasing and changed-total observations are dropped.");
        }

        [TestCase(0, 2, 0)]
        [TestCase(-1, 2, 0)]
        [TestCase(3, 2, 0)]
        [TestCase(1, 0, 0)]
        [TestCase(1, 101, 0)]
        [TestCase(1, 2, 999)]
        public void InvalidObservation_DoesNotPoisonTheNextValidObservation(int completed, int total, int stage)
        {
            using var progress = new NativeProgressProbe();
            Assert.DoesNotThrow(() => progress.Report(completed, total, stage));
            Assert.IsFalse(progress.TryTake(out _));
            progress.Report(1, 2);
            Assert.IsTrue(progress.TryTake(out var update));
            Assert.AreEqual(1, update.Completed);
            Assert.AreEqual(2, update.Total);
        }

        [Test]
        public async Task Wait_WakesForUpdateAndIdempotentCloseWithoutLateReports()
        {
            using var progress = new NativeProgressProbe();
            Task waiting = progress.WaitAsync(CancellationToken.None);
            Assert.IsFalse(waiting.IsCompleted);
            progress.Report(1, 2);
            await NativeProgressProbe.AwaitOwned(waiting);
            Assert.IsTrue(progress.TryTake(out _));
            Task closing = progress.WaitAsync(CancellationToken.None);
            Assert.IsFalse(closing.IsCompleted);
            progress.Close();
            progress.Close();
            await NativeProgressProbe.AwaitOwned(closing);
            Assert.DoesNotThrow(() => progress.Report(2, 2));
            Assert.IsFalse(progress.TryTake(out _), "A closed invocation cannot retain late progress.");
        }

        [Test]
        public async Task CancelledWait_DoesNotConsumeOrCloseAnotherInvocation()
        {
            using var cancelled = new NativeProgressProbe();
            using var other = new NativeProgressProbe();
            using var cancellation = new CancellationTokenSource();
            Task waiting = cancelled.WaitAsync(cancellation.Token);
            cancellation.Cancel();
            Assert.AreSame(waiting, await Task.WhenAny(waiting, Task.Delay(5000)));
            Assert.IsTrue(waiting.IsCanceled);
            other.Report(1, 2);
            Assert.IsTrue(other.TryTake(out var update));
            Assert.AreEqual(1, update.Completed);
            cancelled.Report(1, 3);
            Assert.IsTrue(cancelled.TryTake(out update), "Cancelling a waiter alone does not finish its command.");
            Assert.AreEqual(3, update.Total);
            cancelled.Report(2, 3);
            cancelled.Close();
            Assert.IsFalse(cancelled.TryTake(out _), "Close also discards an update queued before foreground exit.");
            other.Report(2, 2);
            Assert.IsTrue(other.TryTake(out update), "Closing one reporter cannot close another command's reporter.");
            Assert.AreEqual(2, update.Completed);
        }

        [Test]
        public async Task ConcurrentReports_LeaveOneLatestBoundedObservation()
        {
            using var progress = new NativeProgressProbe();
            Task[] reporters = Enumerable
                .Range(0, 4)
                .Select(_ =>
                    Task.Run(() =>
                    {
                        for (int repeat = 0; repeat < 25; repeat++)
                        for (int completed = 1; completed <= 100; completed++)
                            progress.Report(completed, 100);
                    })
                )
                .ToArray();
            await NativeProgressProbe.AwaitOwned(Task.WhenAll(reporters));
            Assert.IsTrue(progress.TryTake(out var update));
            Assert.AreEqual(100, update.Completed);
            Assert.IsFalse(progress.TryTake(out _));
        }

        [Test]
        public void Registry_PrefersProgressiveBatchAndRetainsLegacyEntryPoints()
        {
            Type progressType = NativeProgressProbe.ProgressInterface;
            var selected = CommandRegistry.GetCommandMethod(typeof(BatchExecute));
            CollectionAssert.AreEqual(
                new[] { typeof(JObject), typeof(CancellationToken), progressType },
                selected.GetParameters().Select(parameter => parameter.ParameterType).ToArray()
            );
            Assert.IsNotNull(typeof(BatchExecute).GetMethod("HandleCommand", new[] { typeof(JObject) }));
            Assert.IsNotNull(typeof(BatchExecute).GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken) }));
            Assert.IsNotNull(
                typeof(CommandRegistry).GetMethod("ExecuteCommand", new[] { typeof(string), typeof(JObject), typeof(TaskCompletionSource<string>) })
            );
            Assert.IsNotNull(
                typeof(CommandRegistry).GetMethod(
                    "ExecuteCommand",
                    new[] { typeof(string), typeof(JObject), typeof(TaskCompletionSource<string>), typeof(CancellationToken) }
                )
            );
            Assert.IsNotNull(
                typeof(CommandRegistry).GetMethod(
                    "ExecuteCommand",
                    new[] { typeof(string), typeof(JObject), typeof(TaskCompletionSource<string>), typeof(CancellationToken), progressType }
                )
            );
        }

        [Test]
        public async Task StdioWriter_HeaderGatedProgressAndFinalCannotInterleave()
        {
            using var stream = new ProgressHeaderGateStream(false);
            using var original = new CancellationTokenSource();
            var writer = new StdioFrameWriter(stream, stream.AbortExactConnection, CancellationToken.None);
            byte[] progress = WriterProgressPayload(1);
            byte[] result = Encoding.UTF8.GetBytes("{\"status\":\"success\",\"result\":{\"settled\":2}}");
            Task<bool> optional = null;
            Task final = null;
            try
            {
                optional = writer.TryWriteProgressAsync(progress, original.Token);
                await NativeProgressProbe.AwaitOwned(stream.HeaderWritten.Task);
                Assert.IsFalse(optional.IsCompleted);
                final = writer.WriteResultAsync(result, original.Token);
                Assert.IsFalse(final.IsCompleted);
                Assert.AreEqual(8, stream.Snapshot().Length, "Final bytes must not enter a started optional frame.");
                Task<bool> lateOptional = writer.TryWriteProgressAsync(WriterProgressPayload(2), original.Token);
                Assert.IsTrue(lateOptional.IsCompleted, "A busy optional writer drops instead of queuing another producer task.");
                Assert.IsFalse(await lateOptional, "A pending final has priority over any optional write that has not begun.");
                stream.Release.TrySetResult(true);
                await NativeProgressProbe.AwaitOwned(optional);
                await NativeProgressProbe.AwaitOwned(final);
                Assert.IsTrue(await optional);
                CollectionAssert.AreEqual(FrameBytes(progress).Concat(FrameBytes(result)).ToArray(), stream.Snapshot());
                Assert.AreEqual(1, stream.MaxConcurrentWrites);
                Assert.AreEqual(0, stream.AbortCalls);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                Assert.IsTrue(writer.Completion.IsCompleted, "Scope exit must join its actual writer owner.");
            }
            finally
            {
                original.Cancel();
                stream.Release.TrySetResult(true);
                await ObserveWriterTerminal(optional);
                await ObserveWriterTerminal(final);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                await ObserveWriterTerminal(writer.Completion);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task StdioWriter_PartialOptionalCancellationClosesOnlyItsConnection(bool streamIgnoresToken)
        {
            using var stream = new ProgressHeaderGateStream(streamIgnoresToken);
            using var replacement = new ProgressHeaderGateStream(false);
            using var original = new CancellationTokenSource();
            var writer = new StdioFrameWriter(stream, stream.AbortExactConnection, CancellationToken.None);
            Task<bool> optional = null;
            Task final = null;
            try
            {
                optional = writer.TryWriteProgressAsync(WriterProgressPayload(1), original.Token);
                await NativeProgressProbe.AwaitOwned(stream.HeaderWritten.Task);
                final = writer.WriteResultAsync(Encoding.UTF8.GetBytes("{\"status\":\"success\"}"), original.Token);
                original.Cancel();
                await ObserveWriterTerminal(optional);
                await ObserveWriterTerminal(final);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                Assert.AreEqual(1, stream.AbortCalls);
                Assert.IsTrue(stream.IsDisposed);
                Assert.AreEqual(8, stream.Snapshot().Length, "Never append result/header/payload to an interrupted optional header.");
                Assert.IsFalse(optional.Status == TaskStatus.RanToCompletion && optional.Result);
                Assert.IsTrue(final.IsCanceled || final.IsFaulted, "A partial write cannot manufacture a successful final delivery.");
                Assert.AreEqual(0, replacement.AbortCalls);
                Assert.IsFalse(replacement.IsDisposed, "The closure capability names the captured connection, not a replacement.");
                Assert.IsTrue(writer.Completion.IsCompleted);
            }
            finally
            {
                original.Cancel();
                stream.Release.TrySetResult(true);
                await ObserveWriterTerminal(optional);
                await ObserveWriterTerminal(final);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                await ObserveWriterTerminal(writer.Completion);
            }
        }

        [Test]
        public async Task StdioWriter_OriginalDeadlineStopsSlowWriteWithoutFreshBudget()
        {
            using var stream = new ProgressHeaderGateStream(true);
            using var original = new CancellationTokenSource(1000);
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            var writer = new StdioFrameWriter(stream, stream.AbortExactConnection, CancellationToken.None);
            Task<bool> optional = null;
            try
            {
                optional = writer.TryWriteProgressAsync(WriterProgressPayload(1), original.Token);
                await NativeProgressProbe.AwaitOwned(stream.HeaderWritten.Task);
                await ObserveWriterTerminal(optional);
                Assert.IsTrue(original.IsCancellationRequested);
                Assert.Less(elapsed.ElapsedMilliseconds, 3000, "No fresh 300-second frame timer may outlive the original deadline.");
                Assert.AreEqual(1, stream.AbortCalls);
                Assert.AreEqual(8, stream.Snapshot().Length);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                Assert.IsTrue(writer.Completion.IsCompleted);
            }
            finally
            {
                original.Cancel();
                stream.Release.TrySetResult(true);
                await ObserveWriterTerminal(optional);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                await ObserveWriterTerminal(writer.Completion);
            }
        }

        [Test]
        public async Task StdioWriter_PreCancelledOptionalWritesNothingAndLeavesConnectionHealthy()
        {
            using var stream = new ProgressHeaderGateStream(false);
            using var original = new CancellationTokenSource();
            original.Cancel();
            var writer = new StdioFrameWriter(stream, stream.AbortExactConnection, CancellationToken.None);
            try
            {
                Task<bool> optional = writer.TryWriteProgressAsync(WriterProgressPayload(1), original.Token);
                await ObserveWriterTerminal(optional);
                Assert.IsFalse(optional.Status == TaskStatus.RanToCompletion && optional.Result);
                Assert.AreEqual(0, stream.Snapshot().Length);
                Assert.AreEqual(0, stream.AbortCalls);
                Assert.IsFalse(stream.IsDisposed);
                stream.Release.TrySetResult(true);
                byte[] result = Encoding.UTF8.GetBytes("{\"status\":\"success\"}");
                await NativeProgressProbe.AwaitOwned(writer.WriteResultAsync(result, CancellationToken.None));
                CollectionAssert.AreEqual(FrameBytes(result), stream.Snapshot());
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                Assert.IsTrue(writer.Completion.IsCompleted);
            }
            finally
            {
                stream.Release.TrySetResult(true);
                await NativeProgressProbe.AwaitOwned(writer.CloseAsync());
                await ObserveWriterTerminal(writer.Completion);
            }
        }

        private static byte[] WriterProgressPayload(int completed) =>
            Encoding.UTF8.GetBytes(
                "MCPG{\"type\":\"command_progress\",\"id\":\"11111111-1111-4111-8111-111111111111\",\"completed\":"
                    + completed
                    + ",\"total\":2,\"stage\":\"batch_items\"}"
            );

        private static byte[] FrameBytes(byte[] payload)
        {
            var framed = new byte[8 + payload.Length];
            ulong size = (ulong)payload.Length;
            for (int index = 7; index >= 0; index--)
            {
                framed[index] = (byte)size;
                size >>= 8;
            }
            Buffer.BlockCopy(payload, 0, framed, 8, payload.Length);
            return framed;
        }

        // Cleanup observes only expected I/O/cancellation failures, never an assertion
        // or arbitrary exception. Behavioral assertions inspect the terminal tasks above.
        private static async Task ObserveWriterTerminal(Task task)
        {
            if (task == null)
                return;
            Assert.AreSame(task, await Task.WhenAny(task, Task.Delay(5000)), "Owned frame write did not stop/join.");
            try
            {
                await task;
            }
            catch (OperationCanceledException) { }
            catch (IOException) { }
            catch (ObjectDisposedException) { }
        }

        private sealed class ProgressHeaderGateStream : Stream
        {
            private readonly object _lock = new object();
            private readonly MemoryStream _bytes = new MemoryStream();
            private readonly bool _ignoreToken;
            private int _writes,
                _active;
            internal int MaxConcurrentWrites,
                AbortCalls;
            internal bool IsDisposed;
            internal readonly TaskCompletionSource<bool> HeaderWritten = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            internal readonly TaskCompletionSource<bool> Release = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);

            internal ProgressHeaderGateStream(bool ignoreToken)
            {
                _ignoreToken = ignoreToken;
            }

            internal byte[] Snapshot()
            {
                lock (_lock)
                    return _bytes.ToArray();
            }

            internal void AbortExactConnection()
            {
                lock (_lock)
                    AbortCalls++;
                Dispose();
            }

            public override bool CanRead => false;
            public override bool CanSeek => false;
            public override bool CanWrite => !IsDisposed;
            public override long Length => Snapshot().Length;
            public override long Position
            {
                get => Length;
                set => throw new NotSupportedException();
            }

            public override void Flush() { }

            public override Task FlushAsync(CancellationToken token)
            {
                token.ThrowIfCancellationRequested();
                return Task.CompletedTask;
            }

            public override int Read(byte[] bytes, int offset, int count) => throw new NotSupportedException();

            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] bytes, int offset, int count) =>
                throw new InvalidOperationException("Production must use its asynchronous owned writer.");

            public override async Task WriteAsync(byte[] bytes, int offset, int count, CancellationToken token)
            {
                bool first;
                lock (_lock)
                {
                    if (IsDisposed)
                        throw new ObjectDisposedException(nameof(ProgressHeaderGateStream));
                    first = _writes++ == 0;
                    _active++;
                    MaxConcurrentWrites = Math.Max(MaxConcurrentWrites, _active);
                }
                try
                {
                    if (first)
                    {
                        Assert.GreaterOrEqual(count, 8, "The real writer must emit a complete eight-byte header.");
                        lock (_lock)
                            _bytes.Write(bytes, offset, 8);
                        HeaderWritten.TrySetResult(true);
                        if (_ignoreToken)
                            await Release.Task;
                        else
                            using (token.Register(() => Release.TrySetCanceled()))
                                await Release.Task;
                        lock (_lock)
                        {
                            if (IsDisposed)
                                throw new ObjectDisposedException(nameof(ProgressHeaderGateStream));
                            token.ThrowIfCancellationRequested();
                            if (count > 8)
                                _bytes.Write(bytes, offset + 8, count - 8);
                        }
                    }
                    else
                        lock (_lock)
                        {
                            if (IsDisposed)
                                throw new ObjectDisposedException(nameof(ProgressHeaderGateStream));
                            token.ThrowIfCancellationRequested();
                            _bytes.Write(bytes, offset, count);
                        }
                }
                finally
                {
                    lock (_lock)
                        _active--;
                }
            }

#if NETSTANDARD2_1 || NETCOREAPP
            public override ValueTask WriteAsync(ReadOnlyMemory<byte> bytes, CancellationToken token = default)
            {
                byte[] copy = bytes.ToArray();
                return new ValueTask(WriteAsync(copy, 0, copy.Length, token));
            }
#endif

            protected override void Dispose(bool disposing)
            {
                lock (_lock)
                    IsDisposed = true;
                Release.TrySetResult(true);
                base.Dispose(disposing);
            }
        }
    }

    // These reflection adapters keep the behavioral WS baseline compilable before the
    // new native interface exists. Missing helper/signature assertions are prerequisites,
    // never evidence that the real progress-before-final behavior has been exercised.
    internal sealed class NativeProgressProbe : IDisposable
    {
        private const BindingFlags Members = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        private const string Prefix = "MCPForUnity.Editor.Services.Transport.";
        private readonly Type _bufferType;
        private readonly Type _stageType;
        internal object Instance { get; }

        internal static Type ProgressInterface
        {
            get
            {
                Type type = typeof(WebSocketTransportClient).Assembly.GetType(Prefix + "ICommandProgress");
                Assert.IsNotNull(type, "CORE06 interface prerequisite is not implemented; this is not a behavioral RED.");
                return type;
            }
        }

        internal NativeProgressProbe()
        {
            Assembly assembly = typeof(WebSocketTransportClient).Assembly;
            _bufferType = assembly.GetType(Prefix + "CommandProgressBuffer");
            _stageType = assembly.GetType(Prefix + "CommandProgressStage");
            Assert.IsNotNull(_bufferType, "CORE06 buffer prerequisite is not implemented; run the current-API WS behavioral baseline separately.");
            Assert.IsNotNull(_stageType);
            Instance = Activator.CreateInstance(_bufferType, true);
        }

        internal void Report(int completed, int total, int? stage = null)
        {
            object value = stage.HasValue ? Enum.ToObject(_stageType, stage.Value) : Enum.Parse(_stageType, "BatchItems");
            ProgressInterface.GetMethod("Report").Invoke(Instance, new[] { (object)completed, total, value });
        }

        internal bool TryTake(out NativeProgressSample update)
        {
            var values = new object[] { null };
            bool found = (bool)_bufferType.GetMethod("TryTake", Members).Invoke(Instance, values);
            update = found
                ? new NativeProgressSample(Read<int>(values[0], "Completed"), Read<int>(values[0], "Total"), Read<object>(values[0], "Stage").ToString())
                : default;
            return found;
        }

        private static T Read<T>(object value, string name)
        {
            Type type = value.GetType();
            object member = type.GetProperty(name, Members)?.GetValue(value) ?? type.GetField(name, Members)?.GetValue(value);
            Assert.IsNotNull(member, "The immutable update must expose " + name + ".");
            return (T)member;
        }

        internal Task WaitAsync(CancellationToken token) => (Task)_bufferType.GetMethod("WaitAsync", Members).Invoke(Instance, new object[] { token });

        internal void Close() => _bufferType.GetMethod("Close", Members).Invoke(Instance, null);

        public void Dispose() => ((IDisposable)Instance).Dispose();

        internal static Task<object> InvokeBatch(JObject request, CancellationToken token, NativeProgressProbe progress)
        {
            RequireBatchCapacity(request);
            var method = typeof(BatchExecute).GetMethod("HandleCommand", new[] { typeof(JObject), typeof(CancellationToken), ProgressInterface });
            Assert.IsNotNull(method, "CORE06 progressive batch signature prerequisite is missing.");
            return (Task<object>)method.Invoke(null, new object[] { request, token, progress.Instance });
        }

        internal static TransportCommandOperation Dispatch(Command command, CancellationToken token, NativeProgressProbe progress)
        {
            if (command.type == "batch_execute")
                RequireBatchCapacity(command.@params);
            var method = typeof(TransportCommandDispatcher)
                .GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
                .SingleOrDefault(candidate =>
                    candidate.Name == "ExecuteTimedCommandAsync"
                    && candidate.GetParameters().Length == 5
                    && candidate.GetParameters()[4].ParameterType == ProgressInterface
                );
            Assert.IsNotNull(method, "CORE06 explicit dispatcher reporter parameter prerequisite is missing.");
            return (TransportCommandOperation)method.Invoke(null, new object[] { command, token, null, null, progress.Instance });
        }

        internal static async Task AwaitOwned(Task task)
        {
            Assert.AreSame(task, await Task.WhenAny(task, Task.Delay(5000)), "Owned test work did not settle within five seconds.");
            await task;
        }

        internal static void RequireBatchCapacity(JObject request)
        {
            int count = (request?["commands"] as JArray)?.Count ?? 0;
            Assert.LessOrEqual(
                count,
                BatchExecute.GetMaxCommandsPerBatch(),
                "Owned fixture prerequisite: the configured batch ceiling must admit these items; this test never changes saved preferences."
            );
        }

        internal static IDisposable EnableTools(params string[] names) => new ProgressDiscoveryScope(names);
    }

    // The existing service-locator seam keeps owned enablement in memory. Preserve
    // actual discovered metadata and restore both exact prior service references.
    internal sealed class ProgressDiscoveryScope : IToolDiscoveryService, IResourceDiscoveryService, IDisposable
    {
        private static readonly FieldInfo ToolField = typeof(MCPServiceLocator).GetField("_toolDiscoveryService", BindingFlags.Static | BindingFlags.NonPublic);
        private static readonly FieldInfo ResourceField = typeof(MCPServiceLocator).GetField(
            "_resourceDiscoveryService",
            BindingFlags.Static | BindingFlags.NonPublic
        );
        private readonly object _beforeTools;
        private readonly object _beforeResources;
        private readonly IToolDiscoveryService _tools;
        private readonly IResourceDiscoveryService _resources;
        private readonly Dictionary<string, bool> _toolState = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, bool> _resourceState = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);

        internal ProgressDiscoveryScope(string[] enabledTools)
        {
            _beforeTools = ToolField.GetValue(null);
            _beforeResources = ResourceField.GetValue(null);
            _tools = MCPServiceLocator.ToolDiscovery;
            _resources = MCPServiceLocator.ResourceDiscovery;
            foreach (string name in enabledTools)
                _toolState[name] = true;
            ToolField.SetValue(null, this);
            ResourceField.SetValue(null, this);
        }

        public List<MCPForUnity.Editor.Services.ToolMetadata> DiscoverAllTools() => _tools.DiscoverAllTools();

        public MCPForUnity.Editor.Services.ToolMetadata GetToolMetadata(string name) => _tools.GetToolMetadata(name);

        public List<MCPForUnity.Editor.Services.ToolMetadata> GetEnabledTools() => DiscoverAllTools().Where(item => IsToolEnabled(item.Name)).ToList();

        public bool IsToolEnabled(string name) => _toolState.TryGetValue(name, out bool enabled) ? enabled : _tools.IsToolEnabled(name);

        public void SetToolEnabled(string name, bool enabled) => _toolState[name] = enabled;

        public List<MCPForUnity.Editor.Services.ResourceMetadata> DiscoverAllResources() => _resources.DiscoverAllResources();

        public MCPForUnity.Editor.Services.ResourceMetadata GetResourceMetadata(string name) => _resources.GetResourceMetadata(name);

        public List<MCPForUnity.Editor.Services.ResourceMetadata> GetEnabledResources() =>
            DiscoverAllResources().Where(item => IsResourceEnabled(item.Name)).ToList();

        public bool IsResourceEnabled(string name) => _resourceState.TryGetValue(name, out bool enabled) ? enabled : _resources.IsResourceEnabled(name);

        public void SetResourceEnabled(string name, bool enabled) => _resourceState[name] = enabled;

        public void InvalidateCache() { }

        public void Dispose()
        {
            ToolField.SetValue(null, _beforeTools);
            ResourceField.SetValue(null, _beforeResources);
        }
    }

    internal readonly struct NativeProgressSample
    {
        internal readonly int Completed;
        internal readonly int Total;
        internal readonly string Stage;

        internal NativeProgressSample(int completed, int total, string stage)
        {
            Completed = completed;
            Total = total;
            Stage = stage;
        }
    }

    internal sealed class ProgressHandlerScope : IDisposable
    {
        private readonly IDictionary _handlers;
        private readonly Dictionary<string, object> _before = new Dictionary<string, object>();

        internal ProgressHandlerScope()
        {
            CommandRegistry.Initialize();
            _handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
        }

        internal void Set(string name, HandlerInfo handler)
        {
            if (!_before.ContainsKey(name))
                _before[name] = _handlers[name];
            _handlers[name] = handler;
        }

        public void Dispose()
        {
            foreach (var entry in _before)
                if (entry.Value == null)
                    _handlers.Remove(entry.Key);
                else
                    _handlers[entry.Key] = entry.Value;
        }
    }
}
