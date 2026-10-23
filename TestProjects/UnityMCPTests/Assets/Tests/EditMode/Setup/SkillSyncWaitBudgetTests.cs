using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    [Parallelizable(ParallelScope.None)]
    public class SkillSyncWaitBudgetTests
    {
        private const BindingFlags Internal = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
        private static readonly FieldInfo ContextField = typeof(TransportCommandDispatcher).GetField("_mainThreadContext", Internal);
        private SynchronizationContext _previous;
        private QueuedContext _context;
        private double _now;
        private readonly SkillSyncSource _source = SkillSyncSource.Latest("https://github.com/ykh09242/unity-mcp", "beta");

        [SetUp]
        public void SetUp()
        {
            _now = 0;
            _previous = (SynchronizationContext)ContextField.GetValue(null);
            _context = new QueuedContext();
            ContextField.SetValue(null, _context);
        }

        [TearDown]
        public void TearDown()
        {
            _context.Drain();
            ContextField.SetValue(null, _previous);
            _context.Dispose();
        }

        private object Budget(double seconds = 300, CancellationToken token = default)
        {
            var type = typeof(SkillSyncService).Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncWaitBudget");
            Assert.IsNotNull(type, "Skill verification needs a shared, cancellable Editor wait budget.");
            try
            {
                return Activator.CreateInstance(type, Internal, null, new object[] { seconds, token, (Func<double>)(() => _now) }, null);
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException ?? error;
            }
        }

        private static SkillSyncSource Read(object budget, Func<SkillSyncSource> reader)
        {
            try
            {
                return (SkillSyncSource)budget.GetType().GetMethod("Read", Internal).Invoke(budget, new object[] { reader });
            }
            catch (TargetInvocationException error)
            {
                throw error.InnerException ?? error;
            }
        }

        private Task<SkillSyncSource> Queue(object budget, Func<SkillSyncSource> reader)
        {
            _context.Posted.Reset();
            var task = Task.Run(() => Read(budget, reader));
            Assert.IsTrue(
                SpinWait.SpinUntil(() => _context.Posted.IsSet || task.IsCompleted, TimeSpan.FromSeconds(60)),
                "The owned worker did not post or retire its read."
            );
            return task;
        }

        [Test]
        public void SlowReadsShareWaitAllowanceButDownloadTimeDoesNotSpendIt()
        {
            var budget = Budget();
            int calls = 0;
            int editorThread = Thread.CurrentThread.ManagedThreadId;
            Func<SkillSyncSource> reader = () =>
            {
                Assert.AreEqual(editorThread, Thread.CurrentThread.ManagedThreadId);
                calls++;
                return _source;
            };
            for (int index = 0; index < 2; index++)
            {
                var task = Queue(budget, reader);
                _now += 60;
                Assert.IsFalse(task.IsCompleted);
                _context.Drain();
                Assert.AreSame(_source, task.GetAwaiter().GetResult());
                _now += 3600; // Time outside Editor observation must not cap a large download/apply.
            }
            Assert.AreEqual(2, calls);
            Assert.AreEqual(180d, budget.GetType().GetProperty("RemainingSeconds", Internal).GetValue(budget));
        }

        [Test]
        public void CumulativeExpiryPreventsLateReaderInvocation()
        {
            var budget = Budget(60);
            int calls = 0;
            var first = Queue(
                budget,
                () =>
                {
                    calls++;
                    return _source;
                }
            );
            _now += 59;
            _context.Drain();
            Assert.AreSame(_source, first.GetAwaiter().GetResult());
            var second = Queue(
                budget,
                () =>
                {
                    calls++;
                    return _source;
                }
            );
            _now += 2;
            _context.Drain();
            Assert.Throws<TimeoutException>(() => second.GetAwaiter().GetResult());
            Assert.AreEqual(1, calls);
        }

        [Test]
        public void CancellationRetiresQueuedReadWithoutPumpingEditor()
        {
            using var cancellation = new CancellationTokenSource();
            var budget = Budget(86400, cancellation.Token);
            int calls = 0;
            var read = Queue(
                budget,
                () =>
                {
                    calls++;
                    return _source;
                }
            );
            cancellation.Cancel();
            Assert.Catch<OperationCanceledException>(() => read.GetAwaiter().GetResult());
            _context.Drain();
            Assert.AreEqual(0, calls);
        }

        [Test]
        public void RealWaitExpiryRetiresReadWithoutWaitingForEditorCallback()
        {
            var budget = Budget(0.1);
            int calls = 0;
            var read = Queue(
                budget,
                () =>
                {
                    calls++;
                    return _source;
                }
            );
            Assert.Throws<TimeoutException>(() => read.GetAwaiter().GetResult());
            _context.Drain();
            Assert.AreEqual(0, calls);
        }

        [TestCase(0)]
        [TestCase(-1)]
        [TestCase(double.NaN)]
        [TestCase(double.PositiveInfinity)]
        [TestCase(86401)]
        public void InvalidBudgetIsRejectedBeforePosting(double seconds)
        {
            Assert.Throws<ArgumentOutOfRangeException>(() => Budget(seconds));
            Assert.IsFalse(_context.Posted.IsSet);
        }

        private sealed class QueuedContext : SynchronizationContext, IDisposable
        {
            private readonly Queue<Action> _queue = new();
            internal readonly ManualResetEventSlim Posted = new();

            public override void Post(SendOrPostCallback callback, object state)
            {
                lock (_queue)
                    _queue.Enqueue(() => callback(state));
                Posted.Set();
            }

            internal void Drain()
            {
                while (true)
                {
                    Action callback;
                    lock (_queue)
                    {
                        if (_queue.Count == 0)
                            return;
                        callback = _queue.Dequeue();
                    }
                    callback();
                }
            }

            public void Dispose() => Posted.Dispose();
        }
    }
}
