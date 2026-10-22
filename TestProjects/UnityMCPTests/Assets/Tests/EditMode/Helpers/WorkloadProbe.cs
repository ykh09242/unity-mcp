using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Profiling;
using UnityEngine;

namespace MCPForUnityTests
{
    // Explicit isolated-project probes only. No runtime instrumentation or workload limits.
    internal static partial class WorkloadProbe
    {
        internal static void Run(string scenario, string variant, int units, Func<string> verifiedOutput)
        {
            string output = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT");
            Assert.IsNotEmpty(output, "An explicit controller-owned output directory is required.");
            var samples = new List<object>();
            string expected = null;
            for (int i = 0; i < 7; i++)
            {
                var timer = Stopwatch.StartNew();
                string value = verifiedOutput();
                timer.Stop();
                string digest = Digest(value);
                if (expected == null)
                    expected = digest;
                Assert.AreEqual(expected, digest, "Completed output and ordering must remain identical.");
                samples.Add(
                    new
                    {
                        preparation = i == 0 ? "first_call_warm_library" : "warm",
                        elapsed_ms = timer.Elapsed.TotalMilliseconds,
                        completed_units = units,
                        output_sha256 = digest,
                        main_thread_work_ms = timer.Elapsed.TotalMilliseconds,
                    }
                );
            }
            // Separate untimed instrumentation pass. This is allocation traffic, not retention/RSS.
            string allocationOutput = null;
            JObject allocation = MeasureCurrentThreadAllocation(() => allocationOutput = verifiedOutput());
            Assert.AreEqual(expected, Digest(allocationOutput));
            allocationOutput = null;
            long before = GC.GetTotalMemory(true);
            Assert.AreEqual(expected, Digest(verifiedOutput()));
            long retained = GC.GetTotalMemory(true) - before;
            Directory.CreateDirectory(output);
            File.WriteAllText(
                Path.Combine(output, scenario + "-" + variant + ".json"),
                JsonConvert.SerializeObject(
                    new
                    {
                        schema = "unity-mcp-workload-raw-v1",
                        inputs_sha256 = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_INPUTS_SHA256"),
                        scenario,
                        variant,
                        expected_units = units,
                        completed = true,
                        environment = new
                        {
                            editor = Application.unityVersion,
                            backend = "Editor Mono",
                            os = SystemInfo.operatingSystem,
                            cpu = SystemInfo.processorType,
                            processors = SystemInfo.processorCount,
                            memory_mb = SystemInfo.systemMemorySize,
                            gpu = SystemInfo.graphicsDeviceType.ToString(),
                        },
                        samples,
                        allocation_bytes = allocation["bytes"],
                        allocation_observation = allocation,
                        worker_allocation_observation = new
                        {
                            metric = "managed_allocated_bytes",
                            scope = "worker_threads",
                            status = "not_measured",
                            reason = "main_thread_pass_only",
                            bytes = (long?)null,
                        },
                        retained_managed_delta_bytes = retained,
                        main_thread_stall_ms = (double?)null,
                    },
                    Formatting.Indented
                )
            );
        }

        internal static JObject MeasureCurrentThreadAllocation(Action work, int capacity = 262144, string counter = "GC.Alloc")
        {
            JObject control = RecordAllocation(() => GC.KeepAlive(new byte[65536]), 1024, counter);
            JObject measured = RecordAllocation(work, capacity, counter);
            bool validControl = control.Value<string>("status") == "complete" && control.Value<long>("bytes") >= 65536;
            measured["positive_control_bytes"] = validControl ? control["bytes"] : JValue.CreateNull();
            if (measured.Value<string>("status") == "complete" && !validControl)
            {
                measured["status"] = "positive_control_failed";
                measured["reason"] = "known_64_kib_allocation_not_observed_as_bytes";
                measured["bytes"] = JValue.CreateNull();
            }
            return measured;
        }

        private static JObject RecordAllocation(Action work, int capacity, string counter)
        {
            using var recorder = ProfilerRecorder.StartNew(
                ProfilerCategory.Memory,
                counter,
                capacity,
                ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            work();
            recorder.Stop();
            int samples = recorder.Valid ? recorder.Count : 0;
            int available = recorder.Valid ? recorder.Capacity : capacity;
            string unit = recorder.Valid ? recorder.UnitType.ToString() : "invalid";
            string status =
                !recorder.Valid ? "invalid"
                : samples >= available ? "full"
                : unit != "Bytes" ? "unit_mismatch"
                : samples == 0 ? "empty"
                : "complete";
            long bytes = 0;
            if (status == "complete")
                for (int i = 0; i < samples; i++)
                    bytes += recorder.GetSample(i).Value;
            return new JObject
            {
                ["metric"] = "unity_gc_alloc_bytes",
                ["scope"] = "current_thread",
                ["method"] = "profiler_recorder",
                ["unit"] = unit,
                ["reason"] = status == "unit_mismatch" ? "recorder_value_is_not_allocation_bytes" : null,
                ["status"] = status,
                ["samples"] = samples,
                ["capacity"] = available,
                ["bytes"] = status == "complete" ? new JValue(bytes) : JValue.CreateNull(),
            };
        }

        private static string Digest(string value)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }
    }
}
