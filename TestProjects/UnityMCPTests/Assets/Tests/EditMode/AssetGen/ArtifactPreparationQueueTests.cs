using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ArtifactPreparationQueueTests
    {
        [Test]
        public async Task ShortQueuedWorkPassesUnagedLargeWorkWithoutPreemptingActiveWork()
        {
            var queue = new ArtifactPreparationQueue(1, () => 0);
            IDisposable active = await queue.AcquireAsync(1, CancellationToken.None);
            Task<IDisposable> large = queue.AcquireAsync(32 * 1024 * 1024, CancellationToken.None);
            Task<IDisposable> small = queue.AcquireAsync(512 * 1024, CancellationToken.None);
            Assert.IsFalse(large.IsCompleted || small.IsCompleted);
            active.Dispose();
            active.Dispose(); // Repeated cleanup must not grant another slot.
            Task<IDisposable> first = await Task.WhenAny(large, small);
            bool smallFirst = ReferenceEquals(first, small);
            using (await first)
                Assert.IsFalse((smallFirst ? large : small).IsCompleted);
            using (await (smallFirst ? large : small)) { }
            Assert.IsTrue(smallFirst, "Small pending files should not wait behind another unstarted large copy.");
        }

        [Test]
        public async Task AgedLargeWorkAllowsThreeShortestGrantsThenProgressesDuringNewArrivals()
        {
            double now = 0;
            var queue = new ArtifactPreparationQueue(1, () => now);
            IDisposable active = await queue.AcquireAsync(1, CancellationToken.None);
            var pending = new Dictionary<Task<IDisposable>, string>();
            Task<IDisposable> large = queue.AcquireAsync(8L * 1024 * 1024 * 1024, CancellationToken.None);
            pending.Add(large, "large");
            now = 3;
            for (int i = 0; i < 4; i++)
                pending.Add(queue.AcquireAsync(1, CancellationToken.None), "small-" + i);
            active.Dispose();
            var granted = new List<string>();
            while (pending.Count > 0)
            {
                Task<IDisposable> next = await Task.WhenAny(pending.Keys);
                using (await next)
                {
                    granted.Add(pending[next]);
                    pending.Remove(next);
                    // New arrivals cannot repeatedly push the oldest large job back.
                    if (granted.Count <= 6)
                        pending.Add(queue.AcquireAsync(1, CancellationToken.None), "arrival-" + granted.Count);
                }
            }
            CollectionAssert.AreEqual(new[] { "small-0", "small-1", "small-2", "large" }, granted.Take(4));
            using (await queue.AcquireAsync(1, CancellationToken.None)) { }
        }

        [TestCase(0)]
        [TestCase(2)]
        public async Task CancelingOldestAgedWorkPreservesNextLargeProgress(int consumedBypasses)
        {
            double now = 0;
            var queue = new ArtifactPreparationQueue(1, () => now);
            IDisposable lease = await queue.AcquireAsync(1, CancellationToken.None);
            using var cancel = new CancellationTokenSource();
            Task<IDisposable> canceled = queue.AcquireAsync(long.MaxValue, cancel.Token);
            Task<IDisposable> large = queue.AcquireAsync(long.MaxValue, CancellationToken.None);
            var pending = new List<Task<IDisposable>> { large };
            now = 3;
            for (int i = 0; i < 8; i++)
                pending.Add(queue.AcquireAsync(1, CancellationToken.None));
            for (int i = 0; i < consumedBypasses; i++)
            {
                lease.Dispose();
                Task<IDisposable> next = await Task.WhenAny(pending.Append(canceled));
                lease = await next;
                pending.Remove(next);
            }
            cancel.Cancel();
            bool wasCanceled = false;
            try
            {
                using (await canceled) { }
            }
            catch (OperationCanceledException)
            {
                wasCanceled = true;
            }
            lease.Dispose();
            int grants = 0,
                largeGrant = -1;
            while (pending.Count > 0)
            {
                Task<IDisposable> next = await Task.WhenAny(pending);
                using (await next)
                {
                    grants++;
                    if (ReferenceEquals(next, large))
                        largeGrant = grants;
                    pending.Remove(next);
                }
            }
            Assert.IsTrue(wasCanceled);
            Assert.That(largeGrant, Is.InRange(1, 4), "Canceling the oldest must not permit indefinite large-job bypass.");
        }

        [Test]
        public async Task CanceledWaiterReleasesMetadataWithoutConsumingAWorker()
        {
            var queue = new ArtifactPreparationQueue(1);
            IDisposable active = await queue.AcquireAsync(1, CancellationToken.None);
            using var cancel = new CancellationTokenSource();
            Task<IDisposable> canceled = queue.AcquireAsync(1, cancel.Token);
            Task<IDisposable> next = queue.AcquireAsync(2, CancellationToken.None);
            cancel.Cancel();
            try
            {
                using (await canceled)
                    Assert.Fail("Canceled work was granted");
            }
            catch (OperationCanceledException) { }
            active.Dispose();
            using (await next) { }
            using (await queue.AcquireAsync(3, CancellationToken.None)) { }
        }
    }
}
