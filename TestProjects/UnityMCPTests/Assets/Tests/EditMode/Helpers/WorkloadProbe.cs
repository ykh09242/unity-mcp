using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using Newtonsoft.Json;
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
            long? allocated = null;
            using (
                var recorder = ProfilerRecorder.StartNew(
                    ProfilerCategory.Memory,
                    "GC.Alloc",
                    262144,
                    ProfilerRecorderOptions.StartImmediately | ProfilerRecorderOptions.CollectOnlyOnCurrentThread
                )
            )
            {
                Assert.AreEqual(expected, Digest(verifiedOutput()));
                recorder.Stop();
                if (recorder.Valid && recorder.Count < recorder.Capacity)
                {
                    long bytes = 0;
                    for (int i = 0; i < recorder.Count; i++)
                        bytes += recorder.GetSample(i).Value;
                    allocated = bytes;
                }
            }
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
                        allocation_bytes = allocated,
                        retained_managed_delta_bytes = retained,
                        main_thread_stall_ms = (double?)null,
                    },
                    Formatting.Indented
                )
            );
        }

        private static string Digest(string value)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }
    }
}
