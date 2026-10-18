using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
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
