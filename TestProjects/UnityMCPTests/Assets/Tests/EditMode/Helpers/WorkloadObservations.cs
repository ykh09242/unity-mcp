using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests
{
    internal static partial class WorkloadProbe
    {
        private static readonly string ObservationDomain = Guid.NewGuid().ToString("N");
        internal static int ActiveObservers { get; private set; }

        // Callback intervals are whole-Editor observations, not attributable GPU/frame stalls.
        private sealed class UpdateObservation : IDisposable
        {
            private readonly Stopwatch clock = Stopwatch.StartNew();
            private double previous;
            private int target;
            private double until;
            private TaskCompletionSource<bool> waiting;
            private bool disposed;
            internal int Updates;
            internal double MaximumGap;
            internal long PeakManaged;

            internal UpdateObservation()
            {
                ActiveObservers++;
                EditorApplication.update += Tick;
            }

            private void Tick()
            {
                double now = clock.Elapsed.TotalMilliseconds;
                if (Updates > 0)
                    MaximumGap = Math.Max(MaximumGap, now - previous);
                previous = now;
                Updates++;
                PeakManaged = Math.Max(PeakManaged, GC.GetTotalMemory(false));
                if (Updates >= target && now >= until)
                {
                    var completed = waiting;
                    waiting = null;
                    completed?.TrySetResult(true);
                }
            }

            internal Task Wait(int updates, double milliseconds = 0)
            {
                Assert.IsNull(waiting, "One explicit observation waiter at a time.");
                target = Updates + updates;
                until = clock.Elapsed.TotalMilliseconds + milliseconds;
                waiting = new TaskCompletionSource<bool>();
                return waiting.Task;
            }

            internal void ResetMeasurements()
            {
                MaximumGap = 0;
                PeakManaged = GC.GetTotalMemory(false);
            }

            public void Dispose()
            {
                if (disposed)
                    return;
                disposed = true;
                EditorApplication.update -= Tick;
                ActiveObservers--;
                waiting?.TrySetCanceled();
                waiting = null;
            }
        }

        private static JObject MemoryPoint(Dictionary<string, long> owners)
        {
            var point = new JObject
            {
                ["domain"] = ObservationDomain,
                ["post_idle_managed_bytes"] = GC.GetTotalMemory(true),
                ["owner_counts"] = JObject.FromObject(owners),
                ["working_set_bytes"] = null,
                ["process_handle_count"] = null,
            };
            try
            {
                using var process = Process.GetCurrentProcess();
                process.Refresh();
                // Some Unity Mono platforms return zero for unsupported process counters.
                // A running Editor cannot have an empty working set or no process handles.
                if (process.WorkingSet64 > 0)
                    point["working_set_bytes"] = process.WorkingSet64;
                if (process.HandleCount > 0)
                    point["process_handle_count"] = process.HandleCount;
            }
            catch (Exception) { /* Unsupported process counters remain explicitly unknown. */ }
            return point;
        }

        internal static Task RunNoWorkControl(int rounds = 64) =>
            RunObservedCore(
                "harness_control",
                "no_work",
                1,
                () => Task.FromResult("owned-no-work-control"),
                () => new Dictionary<string, long> { ["work_owners"] = 0 },
                synchronous: true,
                rounds: rounds
            );

        internal static Task RunObserved(string scenario, string variant, int units, Func<string> work, Func<Dictionary<string, long>> owners) =>
            RunObservedCore(scenario, variant, units, () => Task.FromResult(work()), owners, synchronous: true);

        internal static Task RunObservedAsync(string scenario, string variant, int units, Func<Task<string>> work, Func<Dictionary<string, long>> owners) =>
            RunObservedCore(scenario, variant, units, work, owners, synchronous: false);

        private static async Task RunObservedCore(
            string scenario,
            string variant,
            int units,
            Func<Task<string>> work,
            Func<Dictionary<string, long>> owners,
            bool synchronous,
            int rounds = 7
        )
        {
            if (rounds < 1)
                throw new ArgumentOutOfRangeException(nameof(rounds));
            string output = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT");
            Assert.IsNotEmpty(output, "An explicit controller-owned output directory is required.");
            Directory.CreateDirectory(output);
            using var journal = new FileStream(
                Path.Combine(output, ".observation-" + Guid.NewGuid().ToString("N") + ".jsonl"),
                FileMode.CreateNew,
                FileAccess.ReadWrite,
                FileShare.None,
                4096,
                FileOptions.DeleteOnClose
            );
            using var journalWriter = new StreamWriter(journal, new UTF8Encoding(false), 4096, leaveOpen: true);
            int observersBefore = ActiveObservers;
            var ownersBefore = JObject.FromObject(owners());
            var baseline = MemoryPoint(owners());
            string expected = null;
            double maximumGap = 0;
            using (var watch = new UpdateObservation())
            {
                for (int run = 0; run < rounds; run++)
                {
                    await watch.Wait(2);
                    watch.ResetMeasurements();
                    int startUpdates = watch.Updates;
                    var timer = Stopwatch.StartNew();
                    string value = await work();
                    timer.Stop();
                    string hash = Digest(value);
                    value = null;
                    expected ??= hash;
                    Assert.AreEqual(expected, hash, "Complete ordered output must remain identical.");
                    long afterWork = GC.GetTotalMemory(false);
                    // Include the callback following synchronous work, then separate idle/GC evidence.
                    await watch.Wait(1);
                    double workGap = watch.MaximumGap;
                    int workUpdates = watch.Updates - startUpdates;
                    maximumGap = Math.Max(maximumGap, workGap);
                    await watch.Wait(3, 100);
                    JObject point = MemoryPoint(owners());
                    Assert.IsTrue(JToken.DeepEquals(ownersBefore, point["owner_counts"]), "Owned resources did not return to their prepared baseline.");
                    point["run_index"] = run;
                    point["after_work_managed_bytes"] = afterWork;
                    point["sampled_peak_managed_bytes"] = Math.Max(Math.Max(watch.PeakManaged, afterWork), (long)point["post_idle_managed_bytes"]);
                    point["editor_update_count"] = workUpdates;
                    point["editor_update_gap_ms"] = workGap;
                    var sample = new JObject
                    {
                        ["preparation"] = run == 0 ? "first_call_warm_library" : "warm",
                        ["elapsed_ms"] = timer.Elapsed.TotalMilliseconds,
                        ["completed_units"] = units,
                        ["output_sha256"] = hash,
                        ["main_thread_work_ms"] = synchronous ? new JValue(timer.Elapsed.TotalMilliseconds) : JValue.CreateNull(),
                    };
                    // Stream bounded current rows; materialize history only after observation ends.
                    journalWriter.WriteLine(new JObject { ["sample"] = sample, ["observation"] = point }.ToString(Formatting.None));
                    journalWriter.Flush();
                }
            }
            Assert.AreEqual(observersBefore, ActiveObservers, "Measurement must release its update subscription.");
            var samples = new JArray();
            var observations = new JArray();
            journal.Position = 0;
            using (var reader = new StreamReader(journal, Encoding.UTF8, false, 4096, leaveOpen: true))
                while (!reader.EndOfStream)
                {
                    var row = JObject.Parse(reader.ReadLine());
                    samples.Add(row["sample"]);
                    observations.Add(row["observation"]);
                }
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
                        allocation_bytes = (long?)null,
                        allocation_observation = new
                        {
                            metric = "unity_gc_alloc_bytes",
                            scope = "current_thread",
                            status = "not_measured",
                            reason = "observed_async_or_update_cohort",
                            bytes = (long?)null,
                        },
                        worker_allocation_observation = new
                        {
                            metric = "managed_allocated_bytes",
                            scope = "worker_threads",
                            status = "not_measured",
                            reason = "requires_separate_worker_pass",
                            bytes = (long?)null,
                        },
                        history_retention = "one_current_row_plus_fixed_stream_buffers; materialized_after_measurement",
                        retained_managed_delta_bytes = (long)observations.Last["post_idle_managed_bytes"] - (long)baseline["post_idle_managed_bytes"],
                        main_thread_stall_ms = maximumGap,
                        observation_schema = "editor-update-retention-v1",
                        observation_basis = "editor_update_callback_intervals; sampled managed peak; forced-GC post-idle; process-wide counters",
                        baseline,
                        observations,
                        observer_released = ActiveObservers == observersBefore,
                    },
                    Formatting.Indented
                )
            );
        }

        internal static IEnumerator RecordReloadIdle(string phase)
        {
            string output = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT");
            if (string.IsNullOrEmpty(output))
                yield break;
            int observersBefore = ActiveObservers;
            using (var watch = new UpdateObservation())
            {
                Task idle = watch.Wait(3, 100);
                while (!idle.IsCompleted)
                    yield return null;
                idle.GetAwaiter().GetResult();
            }
            Assert.AreEqual(observersBefore, ActiveObservers);
            var point = MemoryPoint(new Dictionary<string, long> { ["probe_observers"] = ActiveObservers });
            point["phase"] = phase;
            point["inputs_sha256"] = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_INPUTS_SHA256");
            Directory.CreateDirectory(output);
            string file = Path.Combine(output, "reload-retention.json");
            var points = File.Exists(file) ? JArray.Parse(File.ReadAllText(file)) : new JArray();
            Assert.IsFalse(points.Any(item => (string)item["phase"] == phase), "Reload output must belong to a fresh invocation.");
            points.Add(point);
            File.WriteAllText(file, points.ToString());
        }
    }
}
