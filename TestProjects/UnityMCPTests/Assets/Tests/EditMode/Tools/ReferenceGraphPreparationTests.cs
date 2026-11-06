using System;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class ReferenceGraphPreparationTests
    {
        [TestCase(0)]
        [TestCase(1)]
        [TestCase(513)]
        [TestCase(10001)]
        public void SlicesProduceCompleteOrdinalDistinctSnapshots(int count)
        {
            string[] input = Enumerable.Range(0, count).Select(i => i % 7 == 0 ? "excluded" : (count - i % 137).ToString("D8")).ToArray();
            string[] expected = input
                .Where(value => value != "excluded")
                .Distinct(StringComparer.Ordinal)
                .OrderBy(value => value, StringComparer.Ordinal)
                .ToArray();
            var preparation = new ReferenceGraphSnapshotPreparation(input, value => value == "excluded" ? null : value);
            int steps = 0;
            while (!preparation.Complete)
            {
                preparation.Step(17, double.PositiveInfinity, CancellationToken.None);
                Assert.LessOrEqual(preparation.LastStepOperations, 17);
                Assert.Less(++steps, 100000);
            }
            CollectionAssert.AreEqual(expected, preparation.Result);
            Assert.AreEqual(count, preparation.InputCount);
            if (count > 513)
                Assert.Greater(steps, 10, "Small pages must not hide one monolithic preparation step.");
        }

        [Test]
        public void CancellationStopsConversionBeforeAnotherItemAndDoesNotPublish()
        {
            int converted = 0;
            using var cancellation = new CancellationTokenSource();
            var preparation = new ReferenceGraphSnapshotPreparation(
                new[] { "c", "b", "a" },
                value =>
                {
                    if (++converted == 1)
                        cancellation.Cancel();
                    return value;
                }
            );
            Assert.Throws<OperationCanceledException>(() => preparation.Step(100, double.PositiveInfinity, cancellation.Token));
            Assert.AreEqual(1, converted);
            Assert.IsFalse(preparation.Complete);
            Assert.IsNull(preparation.Result);
        }

        [Test]
        public void FiniteTimeSliceReturnsAfterOneIndivisibleConversion()
        {
            var preparation = new ReferenceGraphSnapshotPreparation(new[] { "c", "b", "a" }, value => value);
            preparation.Step(100, 0, CancellationToken.None);
            Assert.AreEqual(1, preparation.LastStepOperations);
            Assert.IsFalse(preparation.Complete);
        }

        [Test]
        public async Task CanceledConsumerLeavesSharedPreparationAndOneSnapshotForOtherConsumers()
        {
            int nativeCalls = 0;
            string key = "shared-test:" + Guid.NewGuid().ToString("N");
            using var canceled = new CancellationTokenSource();
            ReferenceGraphPreparation Create() =>
                new ReferenceGraphPreparation(
                    "inventory",
                    () =>
                    {
                        nativeCalls++;
                        return Enumerable.Range(0, 513).Reverse().Select(i => i.ToString("D8")).ToArray();
                    },
                    value => value,
                    () => true
                );
            Task<ReferenceGraphPreparation.Snapshot> first = ReferenceGraphPreparation.Acquire(key, Create, canceled.Token);
            Task<ReferenceGraphPreparation.Snapshot> second = ReferenceGraphPreparation.Acquire(key, Create, CancellationToken.None);
            Task<ReferenceGraphPreparation.Snapshot> third = ReferenceGraphPreparation.Acquire(key, Create, CancellationToken.None);
            Assert.AreEqual(0, nativeCalls, "Native census must be deferred out of command admission.");
            canceled.Cancel();
            try
            {
                await first;
                Assert.Fail("Canceled consumer received a snapshot.");
            }
            catch (OperationCanceledException) { }
            ReferenceGraphPreparation.Snapshot[] remaining = await Task.WhenAll(second, third);
            Assert.AreEqual(1, nativeCalls);
            Assert.AreSame(remaining[0], remaining[1]);
            CollectionAssert.AreEqual(Enumerable.Range(0, 513).Select(i => i.ToString("D8")), remaining[0].Items);
        }

        [Test]
        public async Task LastConsumerCancellationReleasesQueuedCensusAndAllowsANewConsumer()
        {
            int nativeCalls = 0;
            string key = "cancel-test:" + Guid.NewGuid().ToString("N");
            ReferenceGraphPreparation retired = null;
            ReferenceGraphPreparation Create() =>
                retired = new ReferenceGraphPreparation(
                    "dependencies",
                    () =>
                    {
                        nativeCalls++;
                        return new[] { "b", "a", "a" };
                    },
                    value => value,
                    () => true
                );
            using (var cancellation = new CancellationTokenSource())
            {
                Task<ReferenceGraphPreparation.Snapshot> pending = ReferenceGraphPreparation.Acquire(key, Create, cancellation.Token);
                cancellation.Cancel();
                try
                {
                    await pending;
                    Assert.Fail("Last canceled consumer received a snapshot.");
                }
                catch (OperationCanceledException) { }
            }
            typeof(ReferenceGraphPreparation)
                .GetMethod("Tick", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                .Invoke(retired, null);
            Assert.AreEqual(0, nativeCalls);
            ReferenceGraphPreparation.Snapshot completed = await ReferenceGraphPreparation.Acquire(key, Create, CancellationToken.None);
            Assert.AreEqual(1, nativeCalls);
            CollectionAssert.AreEqual(new[] { "a", "b" }, completed.Items);
        }

        [Test]
        public async Task RevisionChangeBetweenSlicesCannotPublishMixedEvidence()
        {
            bool current = true;
            int conversions = 0;
            string key = "revision-test:" + Guid.NewGuid().ToString("N");
            Task<ReferenceGraphPreparation.Snapshot> pending = ReferenceGraphPreparation.Acquire(
                key,
                () =>
                    new ReferenceGraphPreparation(
                        "dependencies",
                        () => new[] { "a", "b", "c" },
                        value =>
                        {
                            conversions++;
                            current = false;
                            return value;
                        },
                        () => current
                    ),
                CancellationToken.None
            );
            try
            {
                await pending;
                Assert.Fail("Changed source revision published mixed evidence.");
            }
            catch (InvalidOperationException) { }
            Assert.Greater(conversions, 0);
        }

        [Test]
        public async Task ColdInventorySharesSnapshotAndContinuationPreservesOrdinalCensus()
        {
            var field = typeof(ReferenceGraphCollector).GetField("inventory", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            field.SetValue(null, null);
            var request = new Newtonsoft.Json.Linq.JObject { ["action"] = "inventory", ["page_size"] = 1 };
            Task<Newtonsoft.Json.Linq.JObject> first = ReferenceGraphCollector.HandleAsync(request, CancellationToken.None);
            Task<Newtonsoft.Json.Linq.JObject> second = ReferenceGraphCollector.HandleAsync(request, CancellationToken.None);
            Assert.IsFalse(first.IsCompleted, "Cold census preparation must yield before its native call.");
            Newtonsoft.Json.Linq.JObject[] pages = await Task.WhenAll(first, second);
            Assert.AreEqual(pages[0].Value<string>("snapshot"), pages[1].Value<string>("snapshot"));
            Assert.AreEqual(pages[0].Value<long>("revision"), pages[1].Value<long>("revision"));
            string[] census = (string[])field.GetValue(null);
            CollectionAssert.AreEqual(census.OrderBy(value => value, StringComparer.Ordinal), census);
            var continuation = (Newtonsoft.Json.Linq.JObject)request.DeepClone();
            continuation["snapshot"] = pages[0]["snapshot"];
            continuation["offset"] = Math.Min(1, census.Length);
            Newtonsoft.Json.Linq.JObject next = await ReferenceGraphCollector.HandleAsync(continuation, CancellationToken.None);
            Assert.AreEqual(pages[0].Value<string>("snapshot"), next.Value<string>("snapshot"));
            Assert.AreEqual(pages[0].Value<long>("revision"), next.Value<long>("revision"));
        }

        [Test, Explicit("Controlled synthetic stage timing; does not measure AssetDatabase calls.")]
        public void SyntheticStageWorkReportsObservedSliceMeasurements()
        {
            const int count = 100001;
            string[] source = Enumerable.Range(0, count).Reverse().Select(i => (i % 50003).ToString("D8")).ToArray();
            var preparation = new ReferenceGraphSnapshotPreparation(source, value => value);
            var total = Stopwatch.StartNew();
            double maximum = 0;
            int slices = 0;
            while (!preparation.Complete)
            {
                var slice = Stopwatch.StartNew();
                preparation.Step(8192, 4, CancellationToken.None);
                maximum = Math.Max(maximum, slice.Elapsed.TotalMilliseconds);
                Assert.LessOrEqual(preparation.LastStepOperations, 8192);
                slices++;
            }
            Assert.AreEqual(50003, preparation.Result.Length);
            CollectionAssert.AreEqual(Enumerable.Range(0, 50003).Select(i => i.ToString("D8")), preparation.Result);
            TestContext.WriteLine($"F08 synthetic_items={count} slices={slices} total_ms={total.Elapsed.TotalMilliseconds:F3} maximum_slice_ms={maximum:F3}");
        }
    }
}
