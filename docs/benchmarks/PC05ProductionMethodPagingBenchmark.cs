using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using MCPForUnity.Editor.Resources.Scene;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Resources
{
    // Opt-in production-method source crossover; comparator arms detect drift only.
    public sealed class PC05ProductionMethodPagingBenchmark
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
            string sourceStage = Environment.GetEnvironmentVariable("PC05_SOURCE_STAGE");
            string sourcePath = Environment.GetEnvironmentVariable("PC05_PRODUCTION_SOURCE");
            Assert.IsTrue(sourceStage == "baseline" || sourceStage == "candidate1-array-compaction", "Name the actual staged production source.");
            Assert.IsTrue(int.TryParse(Environment.GetEnvironmentVariable("PC05_BLOCK"), out int block) && block >= 1 && block <= 3);
            Assert.IsTrue(
                int.TryParse(Environment.GetEnvironmentVariable("PC05_SESSION_POSITION"), out int sessionPosition)
                    && sessionPosition >= 1
                    && sessionPosition <= 2
            );
            bool candidateFirst = block == 2;
            bool expectedCandidate = sessionPosition == 1 ? candidateFirst : !candidateFirst;
            Assert.AreEqual(
                expectedCandidate,
                sourceStage == "candidate1-array-compaction",
                "Preserve baseline/candidate, candidate/baseline, baseline/candidate source orders."
            );
            Assert.IsFalse(string.IsNullOrWhiteSpace(sourcePath), "QA must identify the exact staged package source file.");
            Assert.IsTrue(Path.IsPathRooted(sourcePath), "PC05_PRODUCTION_SOURCE must be an absolute source path.");
            string productionSourceSHA256 = Hash(File.ReadAllBytes(sourcePath));
            string expectedSourceSHA256 =
                sourceStage == "baseline"
                    ? "1de68ed4a6b11ebcb0fdb99b4ba8c069334d82a9be7338eb959fdcd152da7198"
                    : "59c7834e59afab00d3b56de5df1e053791c16d3c8b4793c1d65889146c8a923c";
            Assert.AreEqual(expectedSourceSHA256, productionSourceSHA256, "The staged source must match the frozen source stage.");
            var productionMethodBody = typeof(GameObjectComponentsResource).GetMethod("HandleCommand").GetMethodBody();
            Assert.IsNotNull(productionMethodBody, "Capture the loaded production method's compiled IL as provenance.");
            string productionMethodILSHA256 = Hash(productionMethodBody.GetILAsByteArray());
            string productionAssemblyMvid = typeof(GameObjectComponentsResource).Module.ModuleVersionId.ToString();
            TestContext.WriteLine(
                "PC05_PROVENANCE="
                    + JsonConvert.SerializeObject(
                        new
                        {
                            sourceStage,
                            block,
                            sessionPosition,
                            productionSourceSHA256,
                            productionMethodILSHA256,
                            productionAssemblyMvid,
                        }
                    )
            );
            if (phase != "parity")
            {
                outputDirectory = Environment.GetEnvironmentVariable("PC05_OUTPUT_DIR");
                Assert.IsFalse(string.IsNullOrWhiteSpace(outputDirectory), "QA must set PC05_OUTPUT_DIR to the private native run evidence directory.");
                Assert.IsTrue(Path.IsPathRooted(outputDirectory), "PC05_OUTPUT_DIR must be an absolute, QA-owned evidence directory.");
            }
            Assert.AreSame(
                typeof(GameObjectComponentsResource).Assembly,
                typeof(PC04FrozenBaselineComponentsResource).Assembly,
                "Compile the frozen comparator beside the package resource, outside the test assembly."
            );
            rows.Clear();
            CalibrateAllocationCounter();
            foreach (int count in new[] { 1, 16, 256, 2048 })
            {
                var target = new GameObject("PC05Native" + count);
                try
                {
                    for (int index = 1; index < count; index++)
                        target.AddComponent<PC05NativeMarker>().Index = index;
                    Assert.AreEqual(count, target.GetComponents<Component>().Length, "Actual component count includes Transform.");
                    foreach (bool properties in new[] { false, true })
                    foreach (int pageSize in new[] { 1, 25, 100 })
                    {
                        if (phase != "parity" && !SelectedCohort(count, pageSize, properties))
                            continue;
                        var first = Parameters(target, 0, pageSize, properties);
                        AssertResponseTypeIdentity(first);
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
                                // Four within-session production repeats; the two frozen arms diagnose drift only.
                                foreach (var arm in new[] { "production-a", "production-b", "production-c", "production-d", "control-a", "control-b" })
                                    Measure(phase, count, pageSize, properties, workload, wire, arm);
                            }
                        }
                        TestContext.WriteLine("PC05 " + phase + " count=" + count + " page=" + pageSize + " properties=" + properties);
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
                string output = Path.Combine(
                    outputDirectory,
                    "pc05-" + sourceStage + "-" + phase + "-" + DateTime.UtcNow.ToString("yyyyMMddTHHmmssfff") + ".json"
                );
                File.WriteAllText(
                    output,
                    JsonConvert.SerializeObject(
                        new
                        {
                            schema = "pc05-production-crossover-native-v1",
                            sourceStage,
                            block,
                            sessionPosition,
                            productionSourceSHA256,
                            expectedSourceSHA256,
                            sourcePinVerified = true,
                            productionAssemblyMvid,
                            productionMethodILSHA256,
                            clockedHandler = "GameObjectComponentsResource.HandleCommand",
                            productionArmCount = 4,
                            driftControlArmCount = 2,
                            productionAssembly = typeof(GameObjectComponentsResource).Assembly.GetName().Name,
                            comparatorAssembly = typeof(PC04FrozenBaselineComponentsResource).Assembly.GetName().Name,
                            responseTypeIdentityVerified = true,
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
                TestContext.WriteLine("PC05_RESULT=" + output);
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
            // Select the dispatch before timing; both source stages execute this identical call path.
            bool driftControl = arm.StartsWith("control", StringComparison.Ordinal);
            Func<JObject, object> handler = driftControl ? Baseline : Candidate;
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

        private static string Hash(byte[] bytes)
        {
            using (var hash = SHA256.Create())
                return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
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

        private static void AssertResponseTypeIdentity(JObject request)
        {
            object baseline = Baseline(request);
            object candidate = Candidate(request);
            Assert.AreSame(baseline.GetType(), candidate.GetType(), "Response envelope CLR types must match before measurement.");
            object baselineData = baseline.GetType().GetProperty("data").GetValue(baseline);
            object candidateData = candidate.GetType().GetProperty("data").GetValue(candidate);
            Assert.AreSame(baselineData.GetType(), candidateData.GetType(), "Response data CLR types must match before measurement.");
            Assert.AreSame(typeof(GameObjectComponentsResource).Assembly, candidate.GetType().Assembly);
            if (!(bool)request["includeProperties"])
            {
                var baselineRows = (List<object>)baselineData.GetType().GetProperty("components").GetValue(baselineData);
                var candidateRows = (List<object>)candidateData.GetType().GetProperty("components").GetValue(candidateData);
                if (baselineRows.Count > 0 && candidateRows.Count > 0)
                    Assert.AreSame(baselineRows[0].GetType(), candidateRows[0].GetType(), "Metadata row CLR types must match before measurement.");
            }
            Assert.IsTrue(JToken.DeepEquals(JToken.FromObject(baseline), JToken.FromObject(candidate)));
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

        private static object Baseline(JObject request) => PC04FrozenBaselineComponentsResource.HandleCommand(request);

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

    public sealed class PC05NativeMarker : MonoBehaviour
    {
        public int Index;
    }
}
