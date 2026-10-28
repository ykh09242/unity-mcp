using System;
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
            Action cancellation = () =>
            {
                Assert.IsTrue(
                    Task.Run(() =>
                        {
                            _ = owner.DrainAsync();
                        })
                        .Wait(TimeSpan.FromSeconds(1)),
                    "Cancellation callbacks must execute outside the queue lock."
                );
                canceled = true;
                active.TrySetResult(true);
            };
            Assert.IsNull(owner.TryStart("request", _ => active.Task, cancellation));
            Assert.IsFalse(other.TryCancel("request"));
            Assert.IsFalse(canceled);
            Assert.IsTrue(owner.TryCancel("request"));
            Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            Assert.IsTrue(canceled);
            Assert.IsFalse(owner.TryCancel("request"));
        }

        [Test]
        public void HeldCancellationCallback_ReturnsControlAndRetainsCapacityAndOrderingAfterHandlerSettles()
        {
            var owner = new ConnectionCommandWork(CancellationToken.None, 2);
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            var handler = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int callbacks = 0;
            int mutations = 0;
            Assert.IsNull(
                owner.TryStart(
                    "held",
                    _ => handler.Task,
                    () =>
                    {
                        Interlocked.Increment(ref callbacks);
                        entered.Set();
                        release.Wait();
                    }
                )
            );
            Assert.IsNull(
                owner.TryStart(
                    "next",
                    async predecessor =>
                    {
                        await predecessor.ConfigureAwait(false);
                        Interlocked.Increment(ref mutations);
                    }
                )
            );
            Task<bool> cancel = Task.Run(() => owner.TryCancel("held"));
            try
            {
                Assert.IsTrue(entered.Wait(TimeSpan.FromSeconds(5)));
                Assert.IsTrue(cancel.Wait(TimeSpan.FromSeconds(1)), "A held callback must not block receive-loop control.");
                Assert.IsTrue(cancel.Result);
                Assert.IsFalse(owner.TryCancel("held"), "Repeated cancel must not schedule another callback.");
                handler.TrySetResult(true);
                Assert.IsFalse(owner.DrainAsync().Wait(TimeSpan.FromMilliseconds(50)), "Handler settlement cannot drop a running cancellation callback.");
                Assert.AreEqual(0, Volatile.Read(ref mutations));
                Assert.AreEqual("Command queue is full", owner.TryStart("overflow", _ => Task.CompletedTask));
                release.Set();
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
                Assert.AreEqual(1, callbacks);
                Assert.AreEqual(1, mutations);
                Assert.IsNull(owner.TryStart("after", _ => Task.CompletedTask));
            }
            finally
            {
                release.Set();
                handler.TrySetResult(true);
                Assert.IsTrue(cancel.Wait(TimeSpan.FromSeconds(5)));
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
        }

        [Test]
        public void ThrowingCancellationCallback_IsObservedAndReleasesOnlyAfterHandlerSettlement()
        {
            var owner = new ConnectionCommandWork(CancellationToken.None);
            using var invoked = new ManualResetEventSlim();
            var handler = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            Assert.IsNull(
                owner.TryStart(
                    "throwing",
                    _ => handler.Task,
                    () =>
                    {
                        invoked.Set();
                        throw new InvalidOperationException("test cancellation failure");
                    }
                )
            );
            try
            {
                Assert.DoesNotThrow(() => Assert.IsTrue(owner.TryCancel("throwing")));
                Assert.IsTrue(invoked.Wait(TimeSpan.FromSeconds(5)));
                Assert.IsFalse(owner.DrainAsync().IsCompleted);
            }
            finally
            {
                handler.TrySetResult(true);
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
        }

        [Test]
        public void AsyncCancellation_MarksRequestImmediatelyAndRetainsResourcesUntilCallbackSettlement()
        {
            var owner = new ConnectionCommandWork(CancellationToken.None);
            var handler = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            var callback = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            bool requested = false;
            bool disposed = false;
            Assert.IsNull(
                owner.TryStartWithAsyncCancellation(
                    "owned",
                    _ => handler.Task,
                    () =>
                    {
                        requested = true;
                        return callback.Task;
                    },
                    settled: () => disposed = true
                )
            );
            try
            {
                Assert.IsTrue(owner.TryCancel("owned"));
                Assert.IsTrue(requested, "The wire request marker must be visible before the receive loop admits the next command.");
                handler.TrySetResult(true);
                Assert.IsFalse(owner.DrainAsync().Wait(TimeSpan.FromMilliseconds(50)));
                Assert.IsFalse(disposed, "A running cancellation callback still owns its token source.");
                callback.TrySetResult(true);
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
                Assert.IsTrue(disposed);
            }
            finally
            {
                callback.TrySetResult(true);
                handler.TrySetResult(true);
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
        }

        [Test]
        public void PrestartConnectionCancellation_DoesNotInvokeWork()
        {
            using var lifetime = new CancellationTokenSource();
            var owner = new ConnectionCommandWork(lifetime.Token);
            lifetime.Cancel();
            bool executed = false;
            Assert.AreEqual(
                "Connection closed",
                owner.TryStart(
                    "queued",
                    _ =>
                    {
                        executed = true;
                        return Task.CompletedTask;
                    }
                )
            );
            Assert.IsFalse(executed);
        }
    }
}
