using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests
{
    public sealed class WorkloadMeasurementCoverageTests
    {
        [Test]
        public void AllocationPositiveControlAndFullRecorderCannotBecomeZero()
        {
            var complete = WorkloadProbe.MeasureCurrentThreadAllocation(() => GC.KeepAlive(new byte[65536]));
            if (complete.Value<string>("status") == "complete")
            {
                Assert.AreEqual("Bytes", complete.Value<string>("unit"));
                Assert.GreaterOrEqual(complete.Value<long>("bytes"), 65536);
                Assert.GreaterOrEqual(complete.Value<long>("positive_control_bytes"), 65536);
            }
            else
            {
                Assert.That(complete.Value<string>("status"), Is.EqualTo("unit_mismatch").Or.EqualTo("positive_control_failed").Or.EqualTo("invalid"));
                Assert.AreEqual(JTokenType.Null, complete["bytes"].Type);
            }
            var full = WorkloadProbe.MeasureCurrentThreadAllocation(
                () =>
                {
                    for (int i = 0; i < 128; i++)
                        GC.KeepAlive(new byte[256]);
                },
                capacity: 16
            );
            Assert.AreEqual("full", full.Value<string>("status"), full.ToString());
            Assert.AreEqual(JTokenType.Null, full["bytes"].Type);
            var invalid = WorkloadProbe.MeasureCurrentThreadAllocation(() => GC.KeepAlive(new byte[256]), counter: "MCPForUnity.UnknownAllocationCounter");
            Assert.AreEqual("invalid", invalid.Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, invalid["bytes"].Type);
            TestContext.Out.WriteLine(
                "ALLOCATION_COVERAGE "
                    + new JObject
                    {
                        ["positive"] = complete,
                        ["full"] = full,
                        ["invalid"] = invalid,
                    }.ToString(Formatting.None)
            );
        }

        [Test, Explicit("Controller-owned constant-history no-work control."), Timeout(180000)]
        public async Task NoWorkObservationReleasesOwnersAndKeepsHistoryOutOfMeasuredHeap()
        {
            string nonce = Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE");
            Assert.IsTrue(Guid.TryParseExact(nonce, "N", out _), "An owned native controller is required.");
            string output = Path.GetFullPath(Path.Combine(Application.dataPath, "../Library/NativeQa", nonce, "observation-control"));
            string previous = Environment.GetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT");
            try
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT", output);
                Directory.CreateDirectory(output);
                int before = WorkloadProbe.ActiveObservers;
                bool observedFailure = false;
                try
                {
                    await WorkloadProbe.RunObservedAsync(
                        "failure_control",
                        "owned",
                        1,
                        () => Task.FromException<string>(new InvalidOperationException("controlled observation failure")),
                        () => new Dictionary<string, long> { ["work_owners"] = 0 }
                    );
                }
                catch (InvalidOperationException ex) when (ex.Message == "controlled observation failure")
                {
                    observedFailure = true;
                }
                Assert.IsTrue(observedFailure);
                Assert.AreEqual(before, WorkloadProbe.ActiveObservers);
                Assert.AreEqual(0, Directory.GetFiles(output, ".observation-*.jsonl").Length);
                // Prime observer/JIT paths before the longer equal no-work control.
                await WorkloadProbe.RunNoWorkControl(7);
                for (int cycle = 0; cycle < 3; cycle++)
                {
                    await WorkloadProbe.RunNoWorkControl(64);
                    var result = JObject.Parse(File.ReadAllText(Path.Combine(output, "harness_control-no_work.json")));
                    var points = (JArray)result["observations"];
                    Assert.AreEqual(64, points.Count);
                    Assert.IsTrue(result.Value<bool>("observer_released"));
                    Assert.AreEqual(0, WorkloadProbe.ActiveObservers);
                    StringAssert.StartsWith("one_current_row", result.Value<string>("history_retention"));
                    Assert.AreEqual(0, Directory.GetFiles(output, ".observation-*.jsonl").Length);
                    double Median(long[] values)
                    {
                        Array.Sort(values);
                        return (values[values.Length / 2 - 1] + values[values.Length / 2]) / 2.0;
                    }
                    double early = Median(points.Skip(8).Take(16).Select(p => p.Value<long>("post_idle_managed_bytes")).ToArray());
                    double late = Median(points.Skip(48).Take(16).Select(p => p.Value<long>("post_idle_managed_bytes")).ToArray());
                    Assert.Less(late - early, Math.Max(1048576, early * 0.01), "No-work observer retention exceeded its declared advisory band.");
                    TestContext.Out.WriteLine(
                        "NO_WORK_CONTROL "
                            + new JObject
                            {
                                ["cycle"] = cycle,
                                ["rounds"] = 64,
                                ["earlyMedianManagedBytes"] = early,
                                ["lateMedianManagedBytes"] = late,
                                ["deltaBytes"] = late - early,
                                ["ownersAfter"] = WorkloadProbe.ActiveObservers,
                                ["scope"] = "whole_editor_post_gc; no leak-absence claim",
                                ["output"] = output,
                            }.ToString(Formatting.None)
                    );
                    File.Move(Path.Combine(output, "harness_control-no_work.json"), Path.Combine(output, "harness_control-cycle-" + cycle + ".json"));
                }
            }
            finally
            {
                Environment.SetEnvironmentVariable("UNITY_MCP_WORKLOAD_OUTPUT", previous);
            }
        }

        [Test, Explicit("Controller-owned actual artifact worker allocation pass."), Timeout(180000)]
        public async Task ArtifactWorkerAllocationHasExplicitSupportAndCompleteOutput()
        {
            var method = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", BindingFlags.Public | BindingFlags.Static);
            Func<long> counter = method == null ? null : (Func<long>)method.CreateDelegate(typeof(Func<long>));
            var create =
                (Func<HttpResult, string, string, string, string, string, CancellationToken, PreparedArtifact>)
                    typeof(PreparedArtifact)
                        .GetMethod("Create", BindingFlags.Static | BindingFlags.NonPublic)
                        .CreateDelegate(typeof(Func<HttpResult, string, string, string, string, string, CancellationToken, PreparedArtifact>));
            string root = Path.Combine(Application.temporaryCachePath, "McpWorkerAllocation-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                long? control = await Task.Run(() =>
                {
                    if (counter == null)
                        return (long?)null;
                    try
                    {
                        long before = counter();
                        GC.KeepAlive(new byte[262144]);
                        long delta = counter() - before;
                        return delta >= 262144 ? (long?)delta : null;
                    }
                    catch (Exception ex) when (ex is NotSupportedException || ex is NotImplementedException || ex is EntryPointNotFoundException)
                    {
                        return null;
                    }
                });
                foreach (int size in new[] { 512 * 1024, 64 * 1024 * 1024 })
                {
                    byte[] body = new byte[size];
                    new System.Random(73).NextBytes(body);
                    byte[] signature = { 137, 80, 78, 71, 13, 10, 26, 10 };
                    Buffer.BlockCopy(signature, 0, body, 0, signature.Length);
                    using var sha = SHA256.Create();
                    string digest = BitConverter.ToString(sha.ComputeHash(body)).Replace("-", "").ToLowerInvariant();
                    using var input = new HttpResult { Body = body };
                    long? allocated = null;
                    using (
                        var prepared = await Task.Run(() =>
                        {
                            long before = control.HasValue ? counter() : 0;
                            var value = create(input, root, "image", "fal", "png", "png", CancellationToken.None);
                            try
                            {
                                if (control.HasValue)
                                {
                                    long delta = counter() - before;
                                    allocated = delta >= 0 ? (long?)delta : null;
                                }
                                return value;
                            }
                            catch
                            {
                                value.Dispose();
                                throw;
                            }
                        })
                    )
                    {
                        Assert.AreEqual(size, prepared.Length);
                        Assert.AreEqual(digest, prepared.Hash);
                    }
                    Assert.AreEqual(0, Directory.GetFiles(Path.Combine(root, "Temp/McpAssetPublication")).Length);
                    TestContext.Out.WriteLine(
                        "WORKER_ALLOCATION "
                            + new JObject
                            {
                                ["inputBytes"] = size,
                                ["status"] = allocated.HasValue ? "complete" : "unsupported",
                                ["reason"] = allocated.HasValue ? null : "worker_counter_failed_known_size_positive_control",
                                ["unit"] = "Bytes",
                                ["allocatedBytes"] = allocated.HasValue ? new JValue(allocated.Value) : JValue.CreateNull(),
                                ["positiveControlBytes"] = control.HasValue ? new JValue(control.Value) : JValue.CreateNull(),
                                ["scope"] = "artifact preparation current worker thread; input allocation excluded",
                                ["stagingFilesAfter"] = 0,
                            }.ToString(Formatting.None)
                    );
                }
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }
    }
}
