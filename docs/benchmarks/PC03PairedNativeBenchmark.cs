using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Resources
{
    // Private native measurement helper; no resource registration or shared component buffers.
    public sealed class PC03PairedNativeBenchmark
    {
        private readonly List<object> rows = new List<object>();
        private Func<long> allocationCounter;
        private long calibrationDelta;

        [Test]
        [Timeout(900000)]
        public void VerifyNativeParity() => Run("parity");

        [Test, Explicit("Run only during the root-owned exclusive timing window.")]
        [Timeout(900000)]
        public void RunTimingMatrix() => Run("timing");

        [Test, Explicit("Separate allocation pass; do not infer bytes unless calibration succeeds.")]
        [Timeout(900000)]
        public void RunAllocationMatrix() => Run("allocation");

        private void Run(string phase)
        {
            string outputDirectory = null;
            if (phase != "parity")
            {
                outputDirectory = Environment.GetEnvironmentVariable("PC03_OUTPUT_DIR");
                Assert.IsFalse(string.IsNullOrWhiteSpace(outputDirectory), "QA must set PC03_OUTPUT_DIR to the private native run evidence directory.");
                Assert.IsTrue(Path.IsPathRooted(outputDirectory), "PC03_OUTPUT_DIR must be an absolute, QA-owned evidence directory.");
            }
            rows.Clear();
            CalibrateAllocationCounter();
            foreach (int count in new[] { 1, 16, 256, 2048 })
            {
                var target = new GameObject("PC03Native" + count);
                try
                {
                    for (int index = 1; index < count; index++)
                        target.AddComponent<PC03NativeMarker>().Index = index;
                    Assert.AreEqual(count, target.GetComponents<Component>().Length, "Actual component count includes Transform.");
                    foreach (bool properties in new[] { false, true })
                    foreach (int pageSize in new[] { 1, 25, 100 })
                    {
                        if (phase != "parity" && !SelectedCohort(count, pageSize, properties))
                            continue;
                        var first = Parameters(target, 0, pageSize, properties);
                        var middle = Parameters(target, count / 2, pageSize, properties);
                        var end = Parameters(target, count - 1, pageSize, properties);
                        var beyond = Parameters(target, count + 17, pageSize, properties);
                        var full = Enumerable
                            .Range(0, (count + pageSize - 1) / pageSize)
                            .Select(index => Parameters(target, index * pageSize, pageSize, properties))
                            .ToArray();
                        var cases = new[]
                        {
                            new Workload("first", new[] { first }),
                            new Workload("middle", new[] { middle }),
                            new Workload("end", new[] { end }),
                            new Workload("beyond", new[] { beyond }),
                            new Workload("full", full),
                            new Workload("mixed", Enumerable.Range(0, 4).SelectMany(_ => new[] { first, end, middle, beyond }).ToArray()),
                        };
                        foreach (var workload in cases)
                        {
                            if (phase == "parity")
                            {
                                foreach (var request in workload.Requests)
                                {
                                    var actual = JToken.FromObject(Candidate(request));
                                    Assert.IsTrue((bool)actual["success"]);
                                    Assert.AreEqual(count, (int)actual["data"]["totalCount"]);
                                    Assert.IsFalse(actual.SelectTokens("$..serializationError").Any(), "Marker values must not be truncated by the budget.");
                                    int cursor = Math.Min(count, (int)request["cursor"]);
                                    var ids = target.GetComponents<Component>().Skip(cursor).Take(pageSize).Select(value => value.GetInstanceIDCompat());
                                    CollectionAssert.AreEqual(ids, actual["data"]["components"].Select(value => (int)value["instanceID"]));
                                    Assert.IsTrue(
                                        JToken.DeepEquals(JToken.FromObject(Baseline(request)), actual),
                                        "Parity: " + count + "/" + pageSize + "/" + properties + "/" + workload.Name
                                    );
                                }
                                AssertDetached(first);
                                continue;
                            }
                            foreach (bool wire in new[] { false, true })
                            {
                                // Warm both exact paths before collecting paired samples.
                                InvokeAll(Baseline, workload.Requests, wire);
                                InvokeAll(Candidate, workload.Requests, wire);
                                // ABBA plus unchanged A/A control, with identical actual objects and parameters.
                                foreach (var arm in new[] { "baseline-a", "candidate-a", "candidate-b", "baseline-b", "control-a", "control-b" })
                                    Measure(phase, count, pageSize, properties, workload, wire, arm);
                            }
                        }
                        TestContext.WriteLine("PC03 " + phase + " count=" + count + " page=" + pageSize + " properties=" + properties);
                    }
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(target);
                }
            }
            if (phase != "parity")
            {
                Assert.AreEqual(720, rows.Count, "The predeclared ten-cohort matrix must be complete.");
                Directory.CreateDirectory(outputDirectory);
                string output = Path.Combine(outputDirectory, "pc03-" + phase + "-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff") + ".json");
                File.WriteAllText(
                    output,
                    JsonConvert.SerializeObject(
                        new
                        {
                            schema = "pc03-native-v1",
                            unityVersion = Application.unityVersion,
                            phase,
                            stopwatchFrequency = Stopwatch.Frequency,
                            allocationCounterCalibrated = allocationCounter != null,
                            calibrationDelta,
                            rows,
                        },
                        Formatting.Indented
                    )
                );
                TestContext.WriteLine("PC03_RESULT=" + output);
            }
        }

        private static bool SelectedCohort(int count, int pageSize, bool properties) =>
            (count == 1 && pageSize == 1)
            || (count == 16 && pageSize == 25)
            || (count == 256 && pageSize == 100)
            || (count == 2048 && pageSize == 1 && !properties)
            || (count == 2048 && pageSize == 25 && properties)
            || (count == 2048 && pageSize == 100);

        private void Measure(string phase, int count, int pageSize, bool properties, Workload workload, bool wire, string arm)
        {
            Func<JObject, object> handler = arm.StartsWith("candidate", StringComparison.Ordinal) ? Candidate : Baseline;
            int repeats = workload.Name == "full" ? 3 : 24;
            int operations = repeats * workload.Requests.Length;
            var pageTicks = new long[operations];
            var cycleTicks = new long[repeats];
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            long retainedBefore = GC.GetTotalMemory(false);
            long sampledPeakManaged = retainedBefore;
            var collections = new[] { GC.CollectionCount(0), GC.CollectionCount(1), GC.CollectionCount(2) };
            using (var process = Process.GetCurrentProcess())
            {
                long workingBefore = process.WorkingSet64;
                long sampledPeakWorkingSet = workingBefore;
                long cpuBefore = process.TotalProcessorTime.Ticks;
                long? allocated = phase == "allocation" && allocationCounter != null ? 0L : (long?)null;
                int at = 0;
                long wallStart = Stopwatch.GetTimestamp();
                for (int repeat = 0; repeat < repeats; repeat++)
                {
                    long cycleStart = Stopwatch.GetTimestamp();
                    foreach (var request in workload.Requests)
                    {
                        long allocatedBefore = allocated.HasValue ? allocationCounter() : 0;
                        long start = Stopwatch.GetTimestamp();
                        object result = handler(request);
                        if (wire)
                            result = JsonConvert.SerializeObject(result, Formatting.None);
                        pageTicks[at++] = Stopwatch.GetTimestamp() - start;
                        if (allocated.HasValue)
                            allocated += allocationCounter() - allocatedBefore;
                        GC.KeepAlive(result);
                        result = null;
                        if (phase == "allocation")
                        {
                            sampledPeakManaged = Math.Max(sampledPeakManaged, GC.GetTotalMemory(false));
                            if (at % 32 == 0)
                            {
                                process.Refresh();
                                sampledPeakWorkingSet = Math.Max(sampledPeakWorkingSet, process.WorkingSet64);
                            }
                        }
                    }
                    cycleTicks[repeat] = Stopwatch.GetTimestamp() - cycleStart;
                }
                long wallTicks = Stopwatch.GetTimestamp() - wallStart;
                long cpuTicks = process.TotalProcessorTime.Ticks - cpuBefore;
                long managedBeforeRelease = GC.GetTotalMemory(false);
                int[] collectionDeltas = Enumerable.Range(0, 3).Select(index => GC.CollectionCount(index) - collections[index]).ToArray();
                process.Refresh();
                long workingAfter = process.WorkingSet64;
                long processLifetimePeakWorkingSet = process.PeakWorkingSet64;
                GC.Collect();
                GC.WaitForPendingFinalizers();
                GC.Collect();
                long retainedAfter = GC.GetTotalMemory(false);
                Array.Sort(pageTicks);
                Array.Sort(cycleTicks);
                rows.Add(
                    new
                    {
                        count,
                        pageSize,
                        properties,
                        workload = workload.Name,
                        phase,
                        arm,
                        output = wire ? "construct-and-wire" : "construct",
                        repeats,
                        pagesPerRepeat = workload.Requests.Length,
                        operations,
                        wallTicks,
                        cpuTicks,
                        pageMedianTicks = Percentile(pageTicks, 0.50),
                        pageP95Ticks = Percentile(pageTicks, 0.95),
                        pageP99Ticks = Percentile(pageTicks, 0.99),
                        pageMaxTicks = pageTicks[pageTicks.Length - 1],
                        cycleMedianTicks = Percentile(cycleTicks, 0.50),
                        cycleMaxTicks = cycleTicks[cycleTicks.Length - 1],
                        allocatedBytes = allocated,
                        collectionDeltas,
                        retainedBefore,
                        managedBeforeRelease,
                        retainedAfter,
                        sampledPeakManaged = phase == "allocation" ? sampledPeakManaged : (long?)null,
                        workingBefore,
                        workingAfter,
                        sampledPeakWorkingSet = phase == "allocation" ? Math.Max(sampledPeakWorkingSet, workingAfter) : (long?)null,
                        processLifetimePeakWorkingSet,
                    }
                );
            }
        }

        private void CalibrateAllocationCounter()
        {
            allocationCounter = null;
            calibrationDelta = 0;
            var method = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", Type.EmptyTypes);
            if (method == null)
                return;
            var counter = (Func<long>)Delegate.CreateDelegate(typeof(Func<long>), method);
            counter();
            long before = counter();
            var calibration = new byte[65536];
            calibration[0] = 1;
            calibrationDelta = counter() - before;
            GC.KeepAlive(calibration);
            if (calibrationDelta >= 65536)
                allocationCounter = counter;
        }

        private static void AssertDetached(JObject request)
        {
            object result = Candidate(request);
            var frozen = JToken.FromObject(result);
            Candidate(request);
            Assert.IsTrue(JToken.DeepEquals(frozen, JToken.FromObject(result)));
            object data = result.GetType().GetProperty("data").GetValue(result);
            ((List<object>)data.GetType().GetProperty("components").GetValue(data)).Clear();
            Assert.IsTrue(JToken.DeepEquals(JToken.FromObject(Baseline(request)), JToken.FromObject(Candidate(request))));
        }

        private static void InvokeAll(Func<JObject, object> handler, JObject[] requests, bool wire)
        {
            foreach (var request in requests)
            {
                object result = handler(request);
                if (wire)
                    result = JsonConvert.SerializeObject(result, Formatting.None);
                GC.KeepAlive(result);
            }
        }

        private static long Percentile(long[] sorted, double percentile) => sorted[(int)Math.Ceiling((sorted.Length - 1) * percentile)];

        private static object Baseline(JObject request) => PC03FrozenBaselineComponentsResource.HandleCommand(request);

        private static object Candidate(JObject request) => GameObjectComponentsResource.HandleCommand(request);

        private static JObject Parameters(GameObject target, int cursor, int pageSize, bool properties) =>
            new JObject
            {
                ["instanceID"] = target.GetInstanceIDCompat(),
                ["cursor"] = cursor,
                ["pageSize"] = pageSize,
                ["includeProperties"] = properties,
            };

        private sealed class Workload
        {
            public readonly string Name;
            public readonly JObject[] Requests;

            public Workload(string name, JObject[] requests)
            {
                Name = name;
                Requests = requests;
            }
        }
    }

    public sealed class PC03NativeMarker : MonoBehaviour
    {
        public int Index;
    }
}
