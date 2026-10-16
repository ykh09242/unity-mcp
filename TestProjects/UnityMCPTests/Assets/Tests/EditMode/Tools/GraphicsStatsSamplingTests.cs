using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Graphics;
using MCPForUnity.Editor.Tools.Profiler;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Profiling;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class GraphicsStatsSamplingTests
    {
        private static Type Stats => typeof(ManageGraphics).Assembly.GetType("MCPForUnity.Editor.Tools.Graphics.RenderingStatsOps", true);

        [UnityTest]
        public IEnumerator StatsGet_WaitsForCompletedSamplesAndPreservesCounterKeys()
        {
            // Given a direct graphics request on the Editor main thread.
            // When counters begin recording, the request must not report initialization zeroes.
            var pending = ManageGraphics.HandleCommand(new JObject { ["action"] = "stats_get" }) as Task<object>;
            Assert.IsNotNull(pending, "stats_get must wait for completed native recorder samples.");
            Assert.IsFalse(pending.IsCompleted);
            yield return Await(pending);

            // Then existing measurement keys survive with explicit evidence for unavailable values.
            var response = JObject.FromObject(pending.GetAwaiter().GetResult());
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            var data = response["data"];
            Assert.IsNotNull(data["draw_calls"]);
            Assert.IsNotNull(data["batches"]);
            Assert.IsNotNull(data["triangles"]);
            foreach (var property in (JObject)data["counter_status"])
            {
                bool valid = property.Value.Value<bool>("valid");
                Assert.AreEqual(valid, data[property.Key].Type != JTokenType.Null, property.Key);
                if (valid)
                {
                    Assert.Greater(property.Value.Value<int>("sample_count"), 0, property.Key);
                    Assert.AreEqual("completed", property.Value.Value<string>("status"));
                }
            }
        }

        [UnityTest]
        public IEnumerator NativeMemoryCounter_UsesCompletedSampleAndUnsupportedCounterIsNull()
        {
            // Given a supported native memory counter and an unregistered name.
            var method = Stats.GetMethod("CaptureCountersAsync", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);
            var counters = new[] { ("GC Reserved Memory", "memory"), ("__McpMissing_" + Guid.NewGuid().ToString("N"), "missing") };

            // When the actual recorder sampler captures both together.
            var pending = (Task<object>)method.Invoke(null, new object[] { ProfilerCategory.Memory, counters, CancellationToken.None });
            Assert.IsFalse(pending.IsCompleted);
            yield return Await(pending);
            var response = JObject.FromObject(pending.GetAwaiter().GetResult());

            // Then native data is usable and an unsupported measurement cannot look like zero.
            Assert.Greater(response["data"].Value<long>("memory"), 0);
            Assert.IsTrue(response["data"]["counter_status"]["memory"].Value<bool>("valid"));
            Assert.Greater(response["data"]["counter_status"]["memory"].Value<int>("sample_count"), 0);
            Assert.AreEqual(JTokenType.Null, response["data"]["missing"].Type);
            Assert.AreEqual("unsupported", response["data"]["counter_status"]["missing"].Value<string>("status"));
            Assert.IsTrue(response.Value<bool>("partial"));
            Assert.AreEqual(0, ActiveCaptures());
        }

        [UnityTest]
        public IEnumerator DispatcherCancellation_SettlesAfterNativeRecordersAreDisposed()
        {
            // Given the registered real graphics handler and an active native capture.
            CommandRegistry.Initialize();
            using var cancellation = new CancellationTokenSource();
            var pending = CommandRegistry.InvokeCommandAsync("manage_graphics", new JObject { ["action"] = "stats_get" }, cancellation.Token);
            Assert.IsFalse(pending.IsCompleted);
            Assert.AreEqual(1, ActiveCaptures());

            // When cancellation is requested off the Editor thread.
            var cancel = Task.Run(() => cancellation.Cancel());
            yield return Await(cancel);
            yield return Await(pending);

            // Then cancellation propagates through the dispatcher and native resources are gone.
            Assert.IsTrue(pending.IsCanceled, pending.Status.ToString());
            Assert.AreEqual(0, ActiveCaptures());
        }

        [UnityTest]
        public IEnumerator DispatcherCompletion_SerializesTheCompletedResponse()
        {
            // Given the production transport dispatcher and graphics handler.
            CommandRegistry.Initialize();
            var completion = new TaskCompletionSource<string>();

            // When stats_get runs through the real serialized command path.
            var immediate = CommandRegistry.ExecuteCommand("manage_graphics", new JObject { ["action"] = "stats_get" }, completion);
            Assert.IsNull(immediate, "The dispatcher must defer its response until native sampling completes.");
            yield return Await(completion.Task);

            // Then the response contains measurements rather than a serialized Task.
            var response = JObject.Parse(completion.Task.GetAwaiter().GetResult());
            Assert.IsTrue(response["result"].Value<bool>("success"));
            Assert.AreEqual("completed_frame", response["result"]["sampling"].Value<string>("source"));
            Assert.IsNotNull(response["result"]["data"]["counter_status"]);
            Assert.AreEqual(0, ActiveCaptures());
        }

        [UnityTest]
        public IEnumerator ResourceDispatcherCancellation_UsesTheSameSamplerCleanup()
        {
            // Given the production rendering-stats resource entry point.
            CommandRegistry.Initialize();
            using var cancellation = new CancellationTokenSource();
            var pending = CommandRegistry.InvokeCommandAsync("get_rendering_stats", new JObject(), cancellation.Token);
            Assert.IsFalse(pending.IsCompleted);
            Assert.AreEqual(1, ActiveCaptures());

            // When the request is canceled before the next Editor update.
            cancellation.Cancel();
            Assert.IsFalse(pending.IsCompleted, "Cleanup must settle on the main thread update.");
            yield return Await(pending);

            // Then the resource cannot leave a native capture alive.
            Assert.IsTrue(pending.IsCanceled);
            Assert.AreEqual(0, ActiveCaptures());
        }

        [Test]
        public void SupportedCompletedZero_IsDistinctFromAnUnavailableMeasurement()
        {
            // Given completed-sample metadata with a zero and a supported recorder with no sample yet.
            var method = Stats.GetMethod("DescribeCounter", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);

            // When evidence is serialized through the same production response builder.
            var zero = JObject.FromObject(method.Invoke(null, new object[] { true, 1, 0L, "Count", null }));
            var pending = JObject.FromObject(method.Invoke(null, new object[] { true, 0, 0L, "Count", null }));
            var missing = JObject.FromObject(method.Invoke(null, new object[] { false, 0, 0L, "Unknown", null }));

            // Then only the completed zero is a valid numeric measurement.
            Assert.IsTrue(zero.Value<bool>("valid"));
            Assert.AreEqual(0, zero.Value<long>("value"));
            Assert.IsFalse(pending.Value<bool>("valid"));
            Assert.AreEqual(JTokenType.Null, pending["value"].Type);
            Assert.AreEqual("no_completed_sample", pending.Value<string>("status"));
            Assert.AreEqual("unsupported", missing.Value<string>("status"));
        }

        [Test]
        public void FrameDebuggerMissingMembers_AreExplicitlyPartialAndPreserveKnownZero()
        {
            // Given an event for which reflection returned a valid zero and no shader details.
            var entry = new Dictionary<string, object> { ["index"] = 3, ["vertexCount"] = 0 };
            var method = typeof(FrameDebuggerOps).GetMethod("DescribeEventCompleteness", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method);

            // When completeness metadata is applied to the extracted event fields.
            method.Invoke(null, new object[] { entry });
            var response = JObject.FromObject(entry);

            // Then callers can see exactly which reflection data is unavailable.
            Assert.AreEqual(0, response.Value<int>("vertexCount"));
            Assert.IsTrue(response.Value<bool>("partial"));
            Assert.That(response["unavailable_fields"].Values<string>(), Does.Contain("shaderName"));
            Assert.That(response["unavailable_fields"].Values<string>(), Does.Not.Contain("vertexCount"));
        }

        private static int ActiveCaptures()
        {
            var property = Stats.GetProperty("ActiveCaptureCount", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(property);
            return (int)property.GetValue(null);
        }

        private static IEnumerator Await(Task pending)
        {
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (!pending.IsCompleted && DateTime.UtcNow < deadline)
                yield return null;
            Assert.IsTrue(pending.IsCompleted, "Sampler exceeded its bounded capture time.");
        }
    }
}
