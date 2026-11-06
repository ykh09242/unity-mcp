using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class StdioFrameBudgetTests
    {
        [Test]
        public void PreparedDenseInputIsRefusedBeforeTypedRetentionAndRestoresTheLease()
        {
            string dense = "{\"type\":\"x\",\"params\":{\"numbers\":[" + string.Join(",", new int[4096]) + "]}}";
            int rawBytes = Encoding.UTF8.GetByteCount(dense);
            var budget = new StdioFrameBudget(rawBytes * 5L + 1024, 1024, 1);
            using (var lease = budget.AcquireAsync(rawBytes, CancellationToken.None).Result)
            {
                Assert.IsFalse(StdioBridgeHost.TryPrepareCommandInput(dense, JObject.Parse(dense), lease, out var input));
                Assert.IsNull(input, "Capacity refusal must happen before retaining a typed command for dispatch.");
                Assert.AreEqual(rawBytes * 5L, budget.ReservedBytes, "A failed increase must leave the original charge intact.");
            }
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void SparseLargeStringKeepsItsFrameLimitAndAdjustedRelease()
        {
            string payload = "{\"type\":\"x\",\"params\":{\"text\":\"" + new string('x', 16384) + "\"}}";
            int rawBytes = Encoding.UTF8.GetByteCount(payload);
            var budget = new StdioFrameBudget(rawBytes * 6L, 1024, 1);
            var lease = budget.AcquireAsync(rawBytes, CancellationToken.None).Result;
            Assert.IsTrue(StdioBridgeHost.TryPrepareCommandInput(payload, JObject.Parse(payload), lease, out var input));
            Assert.AreEqual(16384, ((string)input.Command.@params["text"]).Length);
            Assert.GreaterOrEqual(budget.ReservedBytes, rawBytes * 5L);
            lease.Dispose();
            lease.Dispose();
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void ConcurrentGrowthAndDisposeNeverLoseOrDoubleReleaseAdjustedOwnership()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            for (int i = 0; i < 128; i++)
            {
                var lease = budget.AcquireAsync(10, CancellationToken.None).Result;
                Task.WhenAll(Task.Run(() => lease.TryIncreaseTo(90)), Task.Run(() => lease.Dispose())).GetAwaiter().GetResult();
                Assert.IsFalse(lease.TryIncreaseTo(100), "A disposed owner cannot re-acquire capacity.");
                lease.Dispose();
                Assert.AreEqual(0, budget.ReservedBytes);
            }
        }

        [Test]
        public void TwoMaximumFramesKeepSmallControlIngressAvailable()
        {
            var budget = new StdioFrameBudget();
            var first = budget.AcquireAsync(128 * 1024 * 1024, CancellationToken.None).GetAwaiter().GetResult();
            var second = budget.AcquireAsync(128 * 1024 * 1024, CancellationToken.None).GetAwaiter().GetResult();
            var waiting = budget.AcquireAsync(16 * 1024 * 1024, CancellationToken.None);
            Assert.IsFalse(waiting.IsCompleted);
            var control = budget.AcquireAsync(512, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(1280L * 1024 * 1024, budget.LargeReservedBytes);
            Assert.AreEqual(512 * 5, budget.SmallReservedBytes);
            Assert.AreEqual(1, budget.PendingCount);
            first.Dispose();
            Assert.IsTrue(waiting.IsCompleted, "A released large reservation must advance its FIFO despite ongoing small traffic.");
            waiting.GetAwaiter().GetResult().Dispose();
            control.Dispose();
            second.Dispose();
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void WaitingLargeFrameFencesLaterLargeFrameUntilItsTurn()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            var held = budget.AcquireAsync(16, CancellationToken.None).Result;
            var first = budget.AcquireAsync(10, CancellationToken.None);
            var later = budget.AcquireAsync(2, CancellationToken.None);
            Assert.IsFalse(first.IsCompleted);
            Assert.IsFalse(later.IsCompleted, "Large-lane FIFO must not let a smaller later body starve the first waiter.");
            held.Dispose();
            Assert.IsTrue(first.IsCompleted);
            Assert.IsTrue(later.IsCompleted);
            Assert.AreEqual(60, budget.LargeReservedBytes);
            first.Result.Dispose();
            later.Result.Dispose();
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void CancelingHeadWaiterUnblocksNextAndReclaimsQueueState()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            var held = budget.AcquireAsync(16, CancellationToken.None).Result;
            using var cancellation = new CancellationTokenSource();
            var canceled = budget.AcquireAsync(10, cancellation.Token);
            var next = budget.AcquireAsync(2, CancellationToken.None);
            cancellation.Cancel();
            Assert.IsTrue(canceled.IsCanceled);
            Assert.IsTrue(next.IsCompleted);
            Assert.AreEqual(0, budget.PendingCount);
            Assert.AreEqual(90, budget.LargeReservedBytes);
            next.Result.Dispose();
            held.Dispose();
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void CancelAtGrantDoesNotLoseOrPrematurelyReleaseTheGrantedOwner()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            var old = budget.AcquireAsync(20, CancellationToken.None).Result;
            using var cancellation = new CancellationTokenSource();
            var granted = budget.AcquireAsync(16, cancellation.Token);
            old.Dispose();
            cancellation.Cancel();
            Assert.IsTrue(granted.IsCompleted);
            Assert.IsFalse(granted.IsCanceled, "Once granted, the frame reader owns release even if its token cancels before it observes the lease.");
            Assert.AreEqual(80, budget.ReservedBytes);
            granted.Result.Dispose();
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void StopAndRestartCannotForgetAnOldActiveReservation()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            using var oldConnection = new CancellationTokenSource();
            var oldActive = budget.AcquireAsync(20, oldConnection.Token).Result;
            var oldQueued = budget.AcquireAsync(10, oldConnection.Token);
            oldConnection.Cancel();
            Assert.IsTrue(oldQueued.IsCanceled);
            Assert.AreEqual(100, budget.ReservedBytes, "Connection cancellation cannot release text retained by actual old work.");
            var newGeneration = budget.AcquireAsync(20, CancellationToken.None);
            Assert.IsFalse(newGeneration.IsCompleted);
            oldActive.Dispose();
            Assert.IsTrue(newGeneration.IsCompleted);
            oldActive.Dispose();
            Assert.AreEqual(100, budget.ReservedBytes, "A stale duplicate release must not release the new generation's owner.");
            newGeneration.Result.Dispose();
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void WaitingReservationCountIsBoundedWithoutHoldingPayloadBytes()
        {
            var budget = new StdioFrameBudget(100, 10, 1, 2);
            var active = budget.AcquireAsync(20, CancellationToken.None).Result;
            using var cancellation = new CancellationTokenSource();
            var first = budget.AcquireAsync(2, cancellation.Token);
            var second = budget.AcquireAsync(2, cancellation.Token);
            var rejected = budget.AcquireAsync(2, cancellation.Token);
            Assert.IsTrue(rejected.IsFaulted);
            Assert.Throws<InvalidOperationException>(() => rejected.GetAwaiter().GetResult());
            Assert.AreEqual(2, budget.PendingCount);
            Assert.AreEqual(100, budget.ReservedBytes);
            cancellation.Cancel();
            Assert.IsTrue(first.IsCanceled && second.IsCanceled);
            active.Dispose();
            Assert.AreEqual(0, budget.PendingCount);
            Assert.AreEqual(0, budget.ReservedBytes);
        }

        [Test]
        public void SixteenNormalLargeFramesFitWithoutReducingTheSingleFrameLimit()
        {
            var budget = new StdioFrameBudget();
            var leases = new StdioFrameBudget.Lease[16];
            try
            {
                for (int i = 0; i < leases.Length; i++)
                    leases[i] = budget.AcquireAsync(16 * 1024 * 1024, CancellationToken.None).GetAwaiter().GetResult();
                Assert.AreEqual(1280L * 1024 * 1024, budget.ReservedBytes);
            }
            finally
            {
                foreach (var lease in leases)
                    lease?.Dispose();
            }
            Assert.AreEqual(0, budget.ReservedBytes);
            using var maximum = budget.AcquireAsync(128 * 1024 * 1024, CancellationToken.None).GetAwaiter().GetResult();
        }

        [Test]
        public void ConcurrentCancellationAndGrantNeverLoseOrDoubleReleaseOwnership()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            for (int i = 0; i < 128; i++)
            {
                using var cancellation = new CancellationTokenSource();
                var active = budget.AcquireAsync(20, CancellationToken.None).Result;
                var waiting = budget.AcquireAsync(20, cancellation.Token);
                Task.WhenAll(Task.Run(() => cancellation.Cancel()), Task.Run(() => active.Dispose())).GetAwaiter().GetResult();
                if (!waiting.IsCanceled)
                    waiting.GetAwaiter().GetResult().Dispose();
                active.Dispose();
                Assert.AreEqual(0, budget.PendingCount);
                Assert.AreEqual(0, budget.ReservedBytes);
            }
        }

        [Test]
        public void CanceledBeforeAdmissionAndCrossThreadDoubleReleaseRemainExact()
        {
            var budget = new StdioFrameBudget(100, 10, 1);
            using var canceled = new CancellationTokenSource();
            canceled.Cancel();
            Assert.IsTrue(budget.AcquireAsync(10, canceled.Token).IsCanceled);
            Assert.AreEqual(0, budget.ReservedBytes);
            var held = budget.AcquireAsync(20, CancellationToken.None).Result;
            Task.WhenAll(Task.Run(() => held.Dispose()), Task.Run(() => held.Dispose())).GetAwaiter().GetResult();
            Assert.AreEqual(0, budget.ReservedBytes);
        }
    }
}
