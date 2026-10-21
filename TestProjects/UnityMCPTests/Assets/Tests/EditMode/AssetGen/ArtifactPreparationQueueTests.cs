using System;
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
        public async Task AgedLargeWorkRunsBeforeNewSmallWork()
        {
            double now = 0;
            var queue = new ArtifactPreparationQueue(1, () => now);
            IDisposable active = await queue.AcquireAsync(1, CancellationToken.None);
            Task<IDisposable> large = queue.AcquireAsync(8L * 1024 * 1024 * 1024, CancellationToken.None);
            now = 3;
            Task<IDisposable> small = queue.AcquireAsync(1, CancellationToken.None);
            active.Dispose();
            Task<IDisposable> first = await Task.WhenAny(large, small);
            bool largeFirst = ReferenceEquals(first, large);
            using (await first) { }
            using (await (largeFirst ? small : large)) { }
            Assert.IsTrue(largeFirst, "Age must prevent an endless stream of small files from starving a large job.");
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
