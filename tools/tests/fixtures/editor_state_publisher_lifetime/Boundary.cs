using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
#if LEGACY_EDITOR_STATE
using Observation = Newtonsoft.Json.Linq.JObject;
#else
using Observation = MCPForUnity.Editor.Services.EditorStateFrame;
#endif

namespace UnityEditor
{
    internal static class EditorApplication
    {
        internal static void QueuePlayerLoopUpdate() { }
    }
}

// Only I/O and unrelated loop boundaries are replaced. Capture, cancellation, and
// drain orchestration are extracted verbatim from WebSocketTransportClient.
internal partial class TransportLifetimeBoundary
{
    private readonly object _ownershipLock = new();
    private readonly Dictionary<CancellationTokenSource, Task> _cancellations = new();
    private ClientWebSocket _socket;
    private CancellationTokenSource _connectionCts;
    private CommandAdmission _commandAdmission;
    private Task _receiveTask;
    private Task _keepAliveTask;
    private Task _registrationTask;
    private ToolSettingsRegistration _toolSettingsRegistration;
    private ConnectionCommandWork _commandWork;
    private Task _statePublisherCompletion = Task.CompletedTask;
    internal Func<Observation, CancellationToken, Task> Send;

    internal TransportLifetimeBoundary(EditorStatePublisher publisher, ClientWebSocket socket, CancellationTokenSource cts)
    {
        _statePublisher = publisher;
        _statePublisherCompletion = PublisherCompletion(publisher);
        _socket = socket;
        _connectionCts = cts;
    }

    internal IDisposable Publisher => _statePublisher;
    internal Task PublisherSettlement => _statePublisherCompletion;

    private static Task PublisherCompletion(EditorStatePublisher publisher)
    {
        var completion = typeof(EditorStatePublisher).GetProperty("Completion", BindingFlags.Instance | BindingFlags.NonPublic);
        return publisher == null ? Task.CompletedTask : (Task)completion?.GetValue(publisher) ?? Task.CompletedTask;
    }

    internal Task StartRegistration()
    {
        _registrationTask = CompleteRegistrationAsync(new JObject(), _connectionCts.Token);
        return _registrationTask;
    }

    private static bool HasCapability(JObject payload, string capability) => true;

    private Task SendRegisterToolsAsync(CancellationToken token) => Task.CompletedTask;

    private Task SendStateAsync(Observation state, CancellationToken ownerToken, CancellationToken token) => Send(state, token);

    private Task HandleSocketClosureAsync(string reason, CancellationToken token) => Task.FromException(new Exception(reason));

    internal Func<Task> CaptureDrain()
    {
        var loops = CaptureConnectionLoops();
        return () => StopCapturedConnectionLoopsAsync(loops);
    }

    internal void Replace(EditorStatePublisher publisher, ClientWebSocket socket, CancellationTokenSource cts)
    {
        _statePublisher = publisher;
        _statePublisherCompletion = PublisherCompletion(publisher);
        _socket = socket;
        _connectionCts = cts;
    }

    internal sealed class ClientWebSocket
    {
        internal Action OnAbort;
        internal bool Aborted;

        public void Abort()
        {
            Aborted = true;
            OnAbort?.Invoke();
        }
    }

    private sealed class CommandAdmission : IDisposable
    {
        internal CancellationToken Token => CancellationToken.None;

        internal void Retire() { }

        public void Dispose() { }
    }

    private sealed class ToolSettingsRegistration : IDisposable
    {
        internal Task Completion => Task.CompletedTask;

        public void Dispose() { }
    }

    private sealed class ConnectionCommandWork
    {
        internal Task DrainAsync() => Task.CompletedTask;
    }
}

namespace MCPForUnity.Editor.Helpers
{
    internal static class EditorMainThreadWakeup
    {
        internal static void Request(bool immediate = false) { }
    }

    internal static class McpLog
    {
        internal static void Warn(string message) { }
    }

    // Unity boundary: synchronous initial observation and idempotent unsubscription.
    internal static class EditorStateCache
    {
        private static readonly object Gate = new();
        private static readonly List<Action<Observation>> Observers = new();
        internal static Action<object> BeforeInitial;
        internal static Action<object> BeforeReturn;
        internal static bool FailSubscribe;
        internal static string Epoch => "test-epoch";
        internal static int Count
        {
            get
            {
                lock (Gate)
                    return Observers.Count;
            }
        }
        internal static int Removed;

        internal static IDisposable Subscribe(Action<Observation> observer)
        {
            if (FailSubscribe)
                throw new InvalidOperationException("subscribe failed");
            lock (Gate)
                Observers.Add(observer);
            try
            {
                BeforeInitial?.Invoke(observer.Target);
                observer(CreateObservation(1));
                BeforeReturn?.Invoke(observer.Target);
            }
            catch
            {
                lock (Gate)
                    Observers.Remove(observer);
                throw;
            }
            return new Subscription(observer);
        }

        internal static void Emit(int sequence)
        {
            Action<Observation>[] observers;
            lock (Gate)
                observers = Observers.ToArray();
            foreach (var observer in observers)
                observer(CreateObservation(sequence));
        }

        private static Observation CreateObservation(int sequence)
        {
#if LEGACY_EDITOR_STATE
            return new JObject { ["sequence"] = sequence, ["observed_at_unix_ms"] = sequence };
#else
            return new EditorStateFrame.Content(Epoch, new JObject { ["sequence"] = sequence }).Observe(sequence);
#endif
        }

        internal static void Reset()
        {
            lock (Gate)
                Observers.Clear();
            BeforeInitial = null;
            BeforeReturn = null;
            FailSubscribe = false;
            Removed = 0;
        }

        private sealed class Subscription : IDisposable
        {
            private Action<Observation> _observer;

            internal Subscription(Action<Observation> observer)
            {
                _observer = observer;
            }

            public void Dispose()
            {
                var observer = System.Threading.Interlocked.Exchange(ref _observer, null);
                if (observer == null)
                    return;
                lock (Gate)
                {
                    if (Observers.Remove(observer))
                        Removed++;
                }
            }
        }
    }
}
