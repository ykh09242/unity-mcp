using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Profiling;
using UnityEditor;
using UnityEngine.TestTools;
using EngineProfiler = UnityEngine.Profiling.Profiler;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ProfilerWindowTests
    {
        private readonly List<string> windows = new List<string>();
        private EditorApplication.CallbackFunction emitter;

        private static JObject Call(JObject options) => JObject.FromObject(ManageProfiler.HandleCommand(options).GetAwaiter().GetResult());

        private static void Success(JObject result) => Assert.IsTrue(result.Value<bool>("success"), result.ToString());

        private JObject Start(string[] markers, int frames = 12, int warmup = 2, int timeout = 10)
        {
            var response = Call(
                new JObject
                {
                    ["action"] = "window_start",
                    ["category"] = "Scripts",
                    ["markers"] = new JArray(markers),
                    ["frame_count"] = frames,
                    ["warmup_frames"] = warmup,
                    ["timeout_seconds"] = timeout,
                    ["include_samples"] = true,
                }
            );
            Success(response);
            windows.Add(response["data"].Value<string>("window_id"));
            return (JObject)response["data"];
        }

        private static JObject Query(string id, string action = "window_status") => Call(new JObject { ["action"] = action, ["window_id"] = id });

        private static bool Terminal(JObject status) => new[] { "completed", "cancelled", "expired", "failed" }.Contains(status["data"].Value<string>("state"));

        private static IEnumerator Finish(string id)
        {
            double deadline = EditorApplication.timeSinceStartup + 15;
            while (EditorApplication.timeSinceStartup < deadline)
            {
                var status = Query(id);
                Success(status);
                if (Terminal(status))
                    yield break;
                EditorApplication.QueuePlayerLoopUpdate();
                yield return null;
            }
            Assert.Fail("Owned profiling window did not terminate within its bounded deadline.");
        }

        private static void Work(ProfilerMarker marker, double milliseconds)
        {
            using (marker.Auto())
            {
                long start = Stopwatch.GetTimestamp();
                while ((Stopwatch.GetTimestamp() - start) * 1000.0 / Stopwatch.Frequency < milliseconds) { }
            }
        }

        [TearDown]
        public void TearDown()
        {
            if (emitter != null)
                EditorApplication.update -= emitter;
            emitter = null;
            foreach (string id in windows)
                Query(id, "window_cancel");
            windows.Clear();
        }

        [UnityTest]
        public IEnumerator OwnedCpuSpike_ReportsActualSamplesUnitsAndNearestRankStatistics()
        {
            string spikeName = "McpWindowSpike_" + Guid.NewGuid().ToString("N");
            string fastName = "McpWindowFast_" + Guid.NewGuid().ToString("N");
            var spike = new ProfilerMarker(ProfilerCategory.Scripts, spikeName);
            var fast = new ProfilerMarker(ProfilerCategory.Scripts, fastName);
            int calls = 0;
            emitter = () =>
            {
                Work(spike, ++calls % 3 == 0 ? 20 : 0.2);
                Work(fast, 0.1);
            };
            EditorApplication.update += emitter;
            string id = Start(new[] { spikeName, fastName }).Value<string>("window_id");
            yield return Finish(id);
            var response = Query(id, "window_result");
            Success(response);
            var data = response["data"];
            Assert.AreEqual("completed", data.Value<string>("state"));
            Assert.AreEqual(0, data.Value<int>("active_recorders"));
            Assert.AreEqual("nearest_rank_ceil_p_times_n", data.Value<string>("percentile_definition"));
            var row = data["markers"].Single(item => item.Value<string>("name") == spikeName);
            Assert.Greater(calls, 0, "The owned marker emitter must actually execute.");
            Assert.AreEqual("available", row.Value<string>("status"), response.ToString());
            Assert.AreEqual("TimeNanoseconds", row.Value<string>("unit"));
            long[] values = row["samples"].Select(item => item.Value<long>("value")).OrderBy(value => value).ToArray();
            Assert.Greater(values.Length, 0);
            Assert.LessOrEqual(values.Length, 12);
            Assert.AreEqual(values.Length, row.Value<int>("sample_count"));
            Assert.AreEqual(values.Average(value => (double)value), row.Value<double>("mean"), 0.1);
            Assert.AreEqual(values[(int)Math.Ceiling(0.95 * values.Length) - 1], row.Value<long>("p95"));
            Assert.AreEqual(values.Last(), row.Value<long>("max"));
            Assert.AreEqual((values[(values.Length - 1) / 2] / 2.0) + (values[values.Length / 2] / 2.0), row.Value<double>("median"), 0.1);
            Assert.Greater(row.Value<long>("p95"), 10000000, "The deliberately emitted CPU spike must be observed.");
            Assert.AreEqual(spikeName, data["ranking_by_max"][0].Value<string>());
            Assert.AreEqual("not_measured", data["measurement_overhead"].Value<string>("profiler_overhead_status"));
        }

        [UnityTest]
        public IEnumerator RegisteredButUnemittedMarker_IsMissingRatherThanZero()
        {
            string name = "McpWindowSilent_" + Guid.NewGuid().ToString("N");
            var marker = new ProfilerMarker(ProfilerCategory.Scripts, name);
            Assert.AreNotEqual(IntPtr.Zero, marker.Handle);
            string id = Start(new[] { name }, 4, 0).Value<string>("window_id");
            yield return Finish(id);
            var response = Query(id, "window_result");
            Success(response);
            var row = response["data"]["markers"][0];
            Assert.AreEqual("missing", row.Value<string>("status"));
            Assert.AreEqual(0, row.Value<int>("sample_count"));
            Assert.AreEqual(JTokenType.Null, row["mean"].Type);
            Assert.AreEqual(JTokenType.Null, row["max"].Type);
        }

        [UnityTest]
        public IEnumerator UnknownMarker_IsUnavailableRatherThanFabricatedZero()
        {
            string id = Start(new[] { "McpNeverRegistered_" + Guid.NewGuid().ToString("N") }, 3, 0).Value<string>("window_id");
            yield return Finish(id);
            var response = Query(id, "window_result");
            Success(response);
            var row = response["data"]["markers"][0];
            Assert.AreEqual("unavailable", row.Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, row["mean"].Type);
            Assert.AreEqual(0, response["data"].Value<int>("active_recorders"));
        }

        [Test]
        public void Cancel_DisposesOnlyOwnedRecordersAndIsIdempotent()
        {
            string name = "McpWindowCancel_" + Guid.NewGuid().ToString("N");
            var marker = new ProfilerMarker(ProfilerCategory.Scripts, name);
            using (var external = ProfilerRecorder.StartNew(marker, 4))
            {
                bool before = EngineProfiler.enabled;
                string id = Start(new[] { name }, 600).Value<string>("window_id");
                var cancelled = Query(id, "window_cancel");
                Success(cancelled);
                Assert.AreEqual("cancelled", cancelled["data"].Value<string>("state"));
                Assert.AreEqual(0, cancelled["data"].Value<int>("active_recorders"));
                Assert.IsTrue(external.Valid);
                Assert.IsTrue(external.IsRunning);
                Assert.AreEqual(before, EngineProfiler.enabled);
                Success(Query(id, "window_cancel"));
                Success(Query(id, "window_result"));
            }
        }

        [UnityTest]
        public IEnumerator Deadline_ExpiresAndDisposesWithoutInventingCompletion()
        {
            string name = "McpWindowTimeout_" + Guid.NewGuid().ToString("N");
            var marker = new ProfilerMarker(ProfilerCategory.Scripts, name);
            Assert.AreNotEqual(IntPtr.Zero, marker.Handle);
            emitter = () => Work(marker, 20);
            EditorApplication.update += emitter;
            string id = Start(new[] { name }, 600, 120, 1).Value<string>("window_id");
            yield return Finish(id);
            var response = Query(id, "window_result");
            Success(response);
            Assert.AreEqual("expired", response["data"].Value<string>("state"));
            Assert.IsFalse(response["data"].Value<bool>("complete"));
            Assert.AreEqual(0, response["data"].Value<int>("active_recorders"));
        }

        [TestCase("frame_count", "0")]
        [TestCase("frame_count", "601")]
        [TestCase("frame_count", "true")]
        [TestCase("warmup_frames", "-1")]
        [TestCase("warmup_frames", "121")]
        [TestCase("timeout_seconds", "0")]
        [TestCase("timeout_seconds", "121")]
        [TestCase("include_samples", "0")]
        [TestCase("markers", "[]")]
        [TestCase("markers", "[\"duplicate\",\"duplicate\"]")]
        [TestCase("markers", "[true]")]
        public void InvalidBounds_AreRejectedBeforeRecorderCreation(string key, string json)
        {
            var options = new JObject
            {
                ["action"] = "window_start",
                ["category"] = "Scripts",
                ["markers"] = new JArray("McpInvalid"),
            };
            options[key] = JToken.Parse(json);
            var response = Call(options);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("Invalid", response.Value<string>("error"));
        }

        [TestCase("window_status")]
        [TestCase("window_result")]
        [TestCase("window_cancel")]
        public void UnknownWindow_IsExplicitlyRejected(string action)
        {
            var response = Query(Guid.NewGuid().ToString("N"), action);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("window_not_found", response.Value<string>("code"));
        }

        [Test]
        public void RunningWindow_ResultIsNotMisrepresentedAsTerminal()
        {
            var marker = new ProfilerMarker(ProfilerCategory.Scripts, "McpWindowPending");
            Assert.AreNotEqual(IntPtr.Zero, marker.Handle);
            string id = Start(new[] { "McpWindowPending" }, 600).Value<string>("window_id");
            var result = Query(id, "window_result");
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual("window_not_ready", result.Value<string>("code"));
        }
    }
}
