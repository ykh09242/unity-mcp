using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class TransportMainThreadLifetimeTests
    {
        private static readonly FieldInfo ContextField = typeof(TransportCommandDispatcher).GetField(
            "_mainThreadContext",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private static readonly MethodInfo PumpMethod = typeof(TransportCommandDispatcher).GetMethod(
            "ProcessQueue",
            BindingFlags.NonPublic | BindingFlags.Static
        );
        private SynchronizationContext _originalContext;
        private QueuedContext _context;

        [SetUp]
        public void SetUp()
        {
            _originalContext = (SynchronizationContext)ContextField.GetValue(null);
            _context = new QueuedContext();
            ContextField.SetValue(null, _context);
        }

        [TearDown]
        public void TearDown()
        {
            _context.Drain();
            PumpMethod.Invoke(null, null);
            ContextField.SetValue(null, _originalContext);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CanceledWork_ReleasesCaptureBeforeEditorPump(bool missingContext)
        {
            if (missingContext)
                ContextField.SetValue(null, null);
            using var cancellation = new CancellationTokenSource();
            var posted = PostCapturedWork(cancellation.Token);
            cancellation.Cancel();
            Assert.That(posted.Task.IsCanceled, Is.True);
            Assert.That(_context.Count, Is.EqualTo(missingContext ? 0 : 1));
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            Assert.That(posted.Capture.IsAlive, Is.False, "Canceled work must release its captured payload even while the context callback remains queued.");
            _context.Drain();
            PumpMethod.Invoke(null, null);
            Assert.That(posted.Counter.Calls, Is.Zero);
            GC.KeepAlive(_context);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationAfterQueueConsumption_DoesNotInvokeWork(bool missingContext)
        {
            if (missingContext)
                ContextField.SetValue(null, null);
            using var cancellation = new CancellationTokenSource();
            int calls = 0;
            var task = Post(() => ++calls, cancellation.Token);
            Action consumed;
            if (missingContext)
            {
                var callbacks =
                    (LinkedList<Action>)
                        typeof(TransportCommandDispatcher).GetField("MainThreadCallbacks", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                var pendingLock = typeof(TransportCommandDispatcher).GetField("PendingLock", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                lock (pendingLock)
                {
                    consumed = callbacks.First.Value;
                    callbacks.RemoveFirst();
                }
            }
            else
                consumed = _context.Take();
            cancellation.Cancel();
            Assert.DoesNotThrow(() => consumed());
            Assert.That(task.IsCanceled, Is.True);
            var canceled = Assert.Throws<TaskCanceledException>(() => task.GetAwaiter().GetResult());
            Assert.That(canceled.CancellationToken, Is.EqualTo(cancellation.Token));
            Assert.That(calls, Is.Zero);
        }

        [Test]
        public void AlreadyCanceledWork_IsNeverPosted()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            int calls = 0;
            var task = Post(() => ++calls, cancellation.Token);
            Assert.That(task.IsCanceled, Is.True);
            Assert.That(_context.Count, Is.Zero);
            Assert.That(calls, Is.Zero);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void QueuedWork_ReturnsResultOnMainThread(bool missingContext)
        {
            if (missingContext)
                ContextField.SetValue(null, null);
            int mainThread = Thread.CurrentThread.ManagedThreadId;
            var task = Post(() => Thread.CurrentThread.ManagedThreadId, CancellationToken.None);
            Assert.That(task.IsCompleted, Is.False);
            _context.Drain();
            PumpMethod.Invoke(null, null);
            Assert.That(task.GetAwaiter().GetResult(), Is.EqualTo(mainThread));
        }

        [Test]
        public void CallbackFailure_FaultsReturnedTask()
        {
            var expected = new InvalidOperationException("callback fixture failure");
            var task = Post<int>(() => throw expected, CancellationToken.None);
            Assert.DoesNotThrow(() => _context.Drain());
            Assert.That(task.IsFaulted, Is.True);
            Assert.That(task.Exception.InnerException, Is.SameAs(expected));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationDuringCallback_CancelsResponseWithoutRepeatingExecution(bool missingContext)
        {
            if (missingContext)
                ContextField.SetValue(null, null);
            using var cancellation = new CancellationTokenSource();
            using var started = new ManualResetEventSlim();
            using var released = new ManualResetEventSlim();
            int calls = 0;
            var task = Post(
                () =>
                {
                    Interlocked.Increment(ref calls);
                    started.Set();
                    if (!released.Wait(TimeSpan.FromSeconds(5)))
                        throw new TimeoutException("Cancellation thread did not release the executing callback.");
                    return 42;
                },
                cancellation.Token
            );
            var cancelThread = new Thread(() =>
            {
                if (started.Wait(TimeSpan.FromSeconds(5)))
                    cancellation.Cancel();
                released.Set();
            });
            cancelThread.Start();
            _context.Drain();
            PumpMethod.Invoke(null, null);
            Assert.That(cancelThread.Join(TimeSpan.FromSeconds(5)), Is.True);
            Assert.That(task.IsCanceled, Is.True);
            Assert.That(calls, Is.EqualTo(1));
        }

        private static Task<T> Post<T>(Func<T> callback, CancellationToken token)
        {
            Task<T> task = null;
            var thread = new Thread(() => task = TransportCommandDispatcher.RunOnMainThreadAsync(callback, token));
            thread.Start();
            Assert.That(thread.Join(TimeSpan.FromSeconds(5)), Is.True);
            return task;
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static (WeakReference Capture, Task<int> Task, Counter Counter) PostCapturedWork(CancellationToken token)
        {
            var counter = new Counter();
            var payload = new CapturedWork(counter);
            return (new WeakReference(payload), Post(payload.Invoke, token), counter);
        }

        private sealed class Counter
        {
            public int Calls;
        }

        private sealed class CapturedWork
        {
            private readonly byte[] _payload = new byte[4 * 1024 * 1024];
            private readonly Counter _counter;

            public CapturedWork(Counter counter) => _counter = counter;

            public int Invoke()
            {
                _counter.Calls++;
                return _payload.Length;
            }
        }

        private sealed class QueuedContext : SynchronizationContext
        {
            private readonly Queue<(SendOrPostCallback Callback, object State)> _queue = new();
            public int Count => _queue.Count;

            public override void Post(SendOrPostCallback callback, object state) => _queue.Enqueue((callback, state));

            public Action Take()
            {
                var work = _queue.Dequeue();
                return () => work.Callback(work.State);
            }

            public void Drain()
            {
                while (_queue.Count > 0)
                    Take()();
            }
        }
    }
}
