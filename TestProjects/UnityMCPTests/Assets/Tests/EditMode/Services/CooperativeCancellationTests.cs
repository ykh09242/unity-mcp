using System;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class CooperativeCancellationTests
    {
        [Test]
        public void CancelControl_UsesOnlyOwningWorkAndCallsOutsideQueueLock()
        {
            using var lifetime = new CancellationTokenSource();
            var owner = new ConnectionCommandWork(lifetime.Token);
            var other = new ConnectionCommandWork(lifetime.Token);
            var active = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool canceled = false;
            var start = typeof(ConnectionCommandWork).GetMethod("TryStart", new[] { typeof(string), typeof(Func<Task, Task>), typeof(Action) });
            var cancel = typeof(ConnectionCommandWork).GetMethod("TryCancel");
            Assert.IsNotNull(start, "Owned work must accept an explicit cooperative cancellation callback.");
            Assert.IsNotNull(cancel, "Cancel must resolve IDs within the owning connection.");
            Action cancellation = () =>
            {
                Assert.IsTrue(Task.Run(() => { _ = owner.DrainAsync(); }).Wait(TimeSpan.FromSeconds(1)), "Cancellation callbacks must execute outside the queue lock.");
                canceled = true;
                active.TrySetResult(true);
            };
            Assert.IsNull(start.Invoke(owner, new object[] { "request", new Func<Task, Task>(_ => active.Task), cancellation }));
            Assert.IsFalse((bool)cancel.Invoke(other, new object[] { "request" }));
            Assert.IsFalse(canceled);
            Assert.IsTrue((bool)cancel.Invoke(owner, new object[] { "request" }));
            Assert.IsTrue(canceled);
            Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            Assert.IsFalse((bool)cancel.Invoke(owner, new object[] { "request" }));
        }

        [Test]
        public void PrestartConnectionCancellation_DoesNotInvokeWork()
        {
            using var lifetime = new CancellationTokenSource();
            var owner = new ConnectionCommandWork(lifetime.Token);
            lifetime.Cancel();
            bool executed = false;
            Assert.AreEqual("Connection closed", owner.TryStart("queued", _ => { executed = true; return Task.CompletedTask; }));
            Assert.IsFalse(executed);
        }
    }
}
