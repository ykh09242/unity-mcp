using System;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    [TestFixture]
    public class CooperativeCancellationTests
    {
        [TestCase(false)]
        [TestCase(true)]
        public async Task CancellationSettlement_RejectsLateCallbacksAndJoinsConcurrentCallbacks(bool stdio)
        {
            for (int iteration = 0; iteration < 24; iteration++)
            {
                using var source = new CancellationTokenSource();
                using var start = new ManualResetEventSlim();
                using var release = new ManualResetEventSlim();
                var entered = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
                object request;
                CancellationToken token;
                if (stdio)
                {
                    Type type = typeof(StdioBridgeHost).GetNestedType("CommandCancellation", BindingFlags.NonPublic);
                    request = Activator.CreateInstance(type, BindingFlags.Instance | BindingFlags.NonPublic, null, new object[] { source.Token, 30000 }, null);
                    token = (CancellationToken)type.GetProperty("Token", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(request);
                }
                else
                {
                    request = NewWebSocketRequest(new ConnectionCommandWork(CancellationToken.None), "race", source);
                    token = source.Token;
                }
                Type requestType = request.GetType();
                const BindingFlags methods = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
                MethodInfo cancel = requestType.GetMethod(stdio ? "RequestCancellation" : "CancelAsync", methods);
                MethodInfo complete = requestType.GetMethod(stdio ? "CompleteAsync" : "CompleteCancellationAsync", methods);
                using var registration = token.Register(() =>
                {
                    entered.TrySetResult(true);
                    release.Wait();
                });
                var canceling = Task.Run(async () =>
                {
                    start.Wait();
                    if (cancel.Invoke(request, null) is Task task)
                        await task;
                });
                var settling = Task.Run(async () =>
                {
                    start.Wait();
                    await (Task)complete.Invoke(request, null);
                });
                try
                {
                    start.Set();
                    Task observed = await Task.WhenAny(entered.Task, settling, Task.Delay(5000));
                    Assert.IsTrue(observed == entered.Task || observed == settling, "Cancellation/settlement race stalled.");
                    if (entered.Task.IsCompleted)
                        Assert.IsFalse(settling.IsCompleted, "Settlement cannot release a callback that is still running.");
                    else
                    {
                        await canceling;
                        Assert.IsFalse(entered.Task.IsCompleted, "A callback cannot start after settlement closes cancellation admission.");
                    }
                }
                finally
                {
                    release.Set();
                    await Task.WhenAll(canceling, settling);
                    (request as IDisposable)?.Dispose();
                }
                Assert.DoesNotThrow(() => cancel.Invoke(request, null), "Late cancellation must not access a disposed source.");
            }
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void WebSocketCancellation_HoldsDrainCapacityAndSourceUntilCallbacksSettle(bool explicitCancel, bool callbackThrows)
        {
            var owner = new ConnectionCommandWork(CancellationToken.None, 2);
            using var source = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            var handler = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var callback = source.Token.Register(() =>
            {
                entered.Set();
                release.Wait();
                if (callbackThrows)
                    throw new InvalidOperationException("Controlled callback failure");
            });
            object request = NewWebSocketRequest(owner, "held", source);
            int mutations = 0;
            Assert.IsNull(owner.TryStartWithAsyncCancellation("held", _ => handler.Task, RequestCancel(request), settled: ((IDisposable)request).Dispose));
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
            try
            {
                if (explicitCancel)
                    Assert.IsTrue(owner.TryCancel("held"));
                else
                    ScheduleRequestDeadline(request, 20);
                Assert.IsTrue(entered.Wait(TimeSpan.FromSeconds(5)), "Cancellation must reach the controlled callback.");
                handler.TrySetResult(true);
                Assert.IsFalse(owner.DrainAsync().Wait(TimeSpan.FromMilliseconds(50)), "A completed handler must retain the active deadline callback.");
                Assert.AreEqual(0, Volatile.Read(ref mutations));
                Assert.DoesNotThrow(() => _ = source.Token, "The source belongs to the callback until it exits.");
                Assert.AreEqual("Command queue is full", owner.TryStart("overflow", _ => Task.CompletedTask));
                release.Set();
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
                Assert.AreEqual(1, mutations);
                Assert.Throws<ObjectDisposedException>(() => _ = source.Token);
                Assert.IsNull(owner.TryStart("after", _ => Task.CompletedTask));
            }
            finally
            {
                release.Set();
                handler.TrySetResult(true);
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
        }

        private static object NewWebSocketRequest(ConnectionCommandWork owner, string id, CancellationTokenSource source)
        {
            Type type = typeof(WebSocketTransportClient).GetNestedType("QueuedCommand", BindingFlags.NonPublic);
            object request = Activator.CreateInstance(type, true);
            type.GetField("Id").SetValue(request, id);
            type.GetField("Owner").SetValue(request, owner);
            type.GetField("Deadline").SetValue(request, source);
            return request;
        }

        private static Func<Task> RequestCancel(object request) =>
            (Func<Task>)Delegate.CreateDelegate(typeof(Func<Task>), request, request.GetType().GetMethod("CancelAsync"));

        private static void ScheduleRequestDeadline(object request, double remainingMilliseconds) =>
            request.GetType().GetMethod("ScheduleDeadline").Invoke(request, new object[] { remainingMilliseconds });

        [Test]
        public void WebSocketExpiredAtAdmission_MarksDeadlineWithoutBlockingOnCallbacks()
        {
            var owner = new ConnectionCommandWork(CancellationToken.None, 1);
            using var source = new CancellationTokenSource();
            using var entered = new ManualResetEventSlim();
            using var release = new ManualResetEventSlim();
            using var callback = source.Token.Register(() =>
            {
                entered.Set();
                release.Wait();
            });
            object request = NewWebSocketRequest(owner, "expired", source);
            Task<string> admission = Task.Run(() =>
                owner.TryStartWithAsyncCancellation(
                    "expired",
                    _ =>
                    {
                        ScheduleRequestDeadline(request, WebSocketTransportClient.CommandDeadlineRemainingMilliseconds(1, 1000, null, 0));
                        return Task.CompletedTask;
                    },
                    RequestCancel(request),
                    settled: ((IDisposable)request).Dispose
                )
            );
            try
            {
                Assert.IsTrue(admission.Wait(TimeSpan.FromSeconds(5)), "An exhausted deadline must not run arbitrary callbacks on the admitting thread.");
                Assert.IsNull(admission.Result);
                var token = (CancellationToken)request.GetType().GetProperty("RequestCancellation").GetValue(request);
                Assert.IsTrue(token.IsCancellationRequested, "Expiry fences admission before the callback worker is scheduled.");
                Assert.IsFalse((bool)request.GetType().GetProperty("CancelRequested").GetValue(request), "Deadline errors must still say timed out.");
                Assert.IsTrue(entered.Wait(TimeSpan.FromSeconds(5)));
                Assert.IsFalse(owner.DrainAsync().IsCompleted);
                Assert.DoesNotThrow(() => _ = source.Token);
            }
            finally
            {
                release.Set();
                Assert.IsTrue(admission.Wait(TimeSpan.FromSeconds(5)));
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
            Assert.Throws<ObjectDisposedException>(() => _ = source.Token);
        }

        [Test]
        public void WebSocketQueuedDeadline_SkipsHandlerAndRetainsPredecessorOrdering()
        {
            var owner = new ConnectionCommandWork(CancellationToken.None, 2);
            var previous = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var source = new CancellationTokenSource();
            using var expired = new ManualResetEventSlim();
            using var callback = source.Token.Register(expired.Set);
            object request = NewWebSocketRequest(owner, "queued", source);
            int calls = 0;
            Assert.IsNull(owner.TryStart("previous", _ => previous.Task));
            Assert.IsNull(
                owner.TryStartWithAsyncCancellation(
                    "queued",
                    async predecessor =>
                    {
                        ScheduleRequestDeadline(request, 20);
                        try
                        {
                            await ConnectionCommandWork.WaitAsync(predecessor, source.Token).ConfigureAwait(false);
                            source.Token.ThrowIfCancellationRequested();
                            Interlocked.Increment(ref calls);
                        }
                        finally
                        {
                            await predecessor.ConfigureAwait(false);
                        }
                    },
                    RequestCancel(request),
                    settled: ((IDisposable)request).Dispose
                )
            );
            try
            {
                Assert.IsTrue(expired.Wait(TimeSpan.FromSeconds(5)));
                Assert.AreEqual(0, calls);
                Assert.IsFalse(owner.DrainAsync().IsCompleted);
                Assert.AreEqual("Command queue is full", owner.TryStart("overflow", _ => Task.CompletedTask));
            }
            finally
            {
                previous.TrySetResult(true);
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
            Assert.AreEqual(0, calls);
        }

        [Test]
        public void WebSocketLateDeadline_DoesNotCancelReplacementWithReusedId()
        {
            var owner = new ConnectionCommandWork(CancellationToken.None, 1);
            using var source = new CancellationTokenSource();
            object request = NewWebSocketRequest(owner, "reused", source);
            Assert.IsNull(
                owner.TryStartWithAsyncCancellation(
                    "reused",
                    _ =>
                    {
                        ScheduleRequestDeadline(request, 60000);
                        return Task.CompletedTask;
                    },
                    RequestCancel(request),
                    settled: ((IDisposable)request).Dispose
                )
            );
            Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            Assert.Throws<ObjectDisposedException>(() => _ = source.Token);
            var replacement = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
            int cancellations = 0;
            Assert.IsNull(owner.TryStart("reused", _ => replacement.Task, () => Interlocked.Increment(ref cancellations)));
            try
            {
                // Timer.Dispose can leave a callback queued. Invoke that exact production
                // callback after disposal to make the race deterministic.
                Assert.DoesNotThrow(() => request.GetType().GetMethod("Expire", BindingFlags.Instance | BindingFlags.NonPublic).Invoke(request, null));
                Assert.AreEqual(0, cancellations);
                Assert.IsTrue(owner.TryCancel("reused"), "The replacement keeps its cancellation registration.");
            }
            finally
            {
                replacement.TrySetResult(true);
                Assert.IsTrue(owner.DrainAsync().Wait(TimeSpan.FromSeconds(5)));
            }
            Assert.AreEqual(1, cancellations);
        }

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
