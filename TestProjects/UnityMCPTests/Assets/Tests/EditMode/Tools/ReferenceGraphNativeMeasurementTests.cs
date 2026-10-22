using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.Profiling;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class ReferenceGraphNativeMeasurementTests
    {
        private const BindingFlags Hidden = BindingFlags.NonPublic | BindingFlags.Static;
        private const string StateKey = "MCP.ReferenceGraph.Journal.v1";
        private static Type Collector => typeof(ManageReferenceGraph).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphCollector");
        private static Type Journal => typeof(ManageReferenceGraph).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceGraphJournal");

        private static void Owned()
        {
            Assert.IsTrue(Application.isBatchMode);
            Assert.IsTrue(Guid.TryParseExact(Environment.GetEnvironmentVariable("UNITY_MCP_NATIVE_QA_NONCE"), "N", out _));
        }

        private static string Hash(byte[] bytes)
        {
            using var sha = SHA256.Create();
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static JObject Measure(string scenario, int round, Action action)
        {
            GC.Collect();
            GC.WaitForPendingFinalizers();
            GC.Collect();
            long calibration = GC.GetAllocatedBytesForCurrentThread();
            GC.KeepAlive(new byte[4096]);
            bool counter = GC.GetAllocatedBytesForCurrentThread() - calibration >= 4096;
            long allocated = GC.GetAllocatedBytesForCurrentThread(),
                heap = Profiler.GetMonoUsedSizeLong();
            int collections = GC.CollectionCount(0);
            var watch = Stopwatch.StartNew();
            action();
            watch.Stop();
            return new JObject
            {
                ["scenario"] = scenario,
                ["round"] = round,
                ["milliseconds"] = watch.Elapsed.TotalMilliseconds,
                ["allocated_bytes"] = counter ? (JToken)(GC.GetAllocatedBytesForCurrentThread() - allocated) : JValue.CreateNull(),
                ["managed_heap_change_bytes"] = Profiler.GetMonoUsedSizeLong() - heap,
                ["gc_collections"] = GC.CollectionCount(0) - collections,
            };
        }

        private static void Report(JObject result) => TestContext.Out.WriteLine("REFERENCE_NATIVE_MEASUREMENT " + result.ToString(Formatting.None));

        [Test, Explicit("Controller-owned large native source measurements."), Timeout(300000)]
        public void CompleteLargeSourcePagesPreserveBytesAndFences()
        {
            Owned();
            const string root = "Assets/__McpReferenceMeasureFixtures_e11";
            Assert.IsTrue(AssetDatabase.IsValidFolder(root), "The controller must import owned fixtures before the run.");
            foreach (string format in new[] { "csharp", "unity_yaml" })
            {
                string path = root + (format == "csharp" ? "/Owned.cs" : "/Owned.asset");
                string guid = AssetDatabase.AssetPathToGUID(path);
                Assert.IsNotEmpty(guid);
                byte[] expected = File.ReadAllBytes(path);
                string expectedHash = Hash(expected);
                for (int round = 0; round < 4; round++)
                {
                    int pages = 0;
                    double worstPage = 0;
                    string digest = null;
                    JObject measured = Measure(
                        format,
                        round,
                        () =>
                        {
                            using var sha = SHA256.Create();
                            var request = new JObject { ["action"] = "source", ["guid"] = guid };
                            long total = 0;
                            while (true)
                            {
                                var watch = Stopwatch.StartNew();
                                JObject page = ReferenceGraphCollectorTests.Call(
                                    "source",
                                    request.Properties().Where(p => p.Name != "action").SelectMany(p => new object[] { p.Name, p.Value }).ToArray()
                                );
                                watch.Stop();
                                worstPage = Math.Max(worstPage, watch.Elapsed.TotalMilliseconds);
                                byte[] bytes = Convert.FromBase64String(page.Value<string>("bytes_base64"));
                                sha.TransformBlock(bytes, 0, bytes.Length, null, 0);
                                total += bytes.Length;
                                pages++;
                                Assert.AreEqual(format, page.Value<string>("source_format"));
                                if (page["next_offset"].Type == JTokenType.Null)
                                    break;
                                request["offset"] = page["next_offset"];
                                request["fingerprint"] = page["fingerprint"];
                            }
                            sha.TransformFinalBlock(Array.Empty<byte>(), 0, 0);
                            digest = BitConverter.ToString(sha.Hash).Replace("-", "").ToLowerInvariant();
                            Assert.AreEqual(expected.Length, total);
                        }
                    );
                    Assert.AreEqual(expectedHash, digest);
                    measured["bytes"] = expected.Length;
                    measured["pages"] = pages;
                    measured["maximum_page_milliseconds"] = worstPage;
                    measured["sha256"] = digest;
                    Report(measured);
                }
                JObject first = ReferenceGraphCollectorTests.Call("source", "guid", guid);
                try
                {
                    File.AppendAllText(path, "\n");
                    Assert.Throws<TargetInvocationException>(() =>
                        ReferenceGraphCollectorTests.Call("source", "guid", guid, "offset", 65536, "fingerprint", first.Value<string>("fingerprint"))
                    );
                }
                finally
                {
                    File.WriteAllBytes(path, expected);
                }
            }
        }

        [Test, Explicit("Controller-owned journal serialization and census history measurements."), Timeout(300000)]
        public void NearCapacityJournalAndRepeatedCensusPreserveHistory()
        {
            Owned();
            ReferenceGraphCollectorTests.Call("status");
            Journal.GetMethod("Save", Hidden).Invoke(null, null);
            string stored = SessionState.GetString(StateKey, "");
            var original = (JArray)Journal.GetField("changes", Hidden).GetValue(null);
            string epoch = (string)Journal.GetProperty("Epoch", Hidden).GetValue(null);
            long revision = (long)Journal.GetProperty("Revision", Hidden).GetValue(null);
            try
            {
                var rows = new JArray();
                for (int i = 0; i < 65000; i++)
                    rows.Add(
                        new JObject
                        {
                            ["revision"] = i + 1,
                            ["path"] = "Assets/__SyntheticHistory/" + i + ".asset",
                            ["deleted"] = true,
                            ["file_stamp"] = null,
                        }
                    );
                Journal.GetField("changes", Hidden).SetValue(null, rows);
                Journal.GetProperty("Revision", Hidden).SetValue(null, 65000L);
                string expected = new JObject
                {
                    ["epoch"] = epoch,
                    ["revision"] = 65000L,
                    ["changes"] = rows.DeepClone(),
                }.ToString(Formatting.None);
                for (int round = 0; round < 4; round++)
                {
                    JObject save = Measure("journal_save_65000", round, () => Journal.GetMethod("Save", Hidden).Invoke(null, null));
                    string encoded = SessionState.GetString(StateKey, "");
                    Assert.AreEqual(expected, encoded);
                    save["characters"] = encoded.Length;
                    save["rows"] = 65000;
                    Report(save);
                    JObject restore = Measure("journal_restore_65000", round, () => Journal.GetMethod("Restore", Hidden).Invoke(null, null));
                    Assert.AreEqual(65000L, Journal.GetProperty("Revision", Hidden).GetValue(null));
                    Report(restore);
                }

                Collector.GetField("inventory", Hidden).SetValue(null, null);
                JObject census = ReferenceGraphCollectorTests.Call("inventory", "page_size", 1);
                string token = census.Value<string>("snapshot");
                // Simulate 50,000 later import/catalog records without touching any file.
                Collector.GetField("inventoryRevision", Hidden).SetValue(null, 15000L);
                for (int round = 0; round < 4; round++)
                {
                    int replayed = 0,
                        pages = 0;
                    JObject result = Measure(
                        "cached_census_plus_50000_history",
                        round,
                        () =>
                        {
                            JObject start = ReferenceGraphCollectorTests.Call("inventory", "page_size", 1);
                            Assert.AreEqual(token, start.Value<string>("snapshot"));
                            long after = start.Value<long>("revision");
                            while (true)
                            {
                                JObject page = ReferenceGraphCollectorTests.Call("changes", "epoch", epoch, "after", after, "page_size", 1000);
                                replayed += page["changes"].Count();
                                pages++;
                                if (!page.Value<bool>("has_more"))
                                    break;
                                after = page.Value<long>("next_after");
                            }
                        }
                    );
                    result["replayed_rows"] = replayed;
                    result["history_pages"] = pages;
                    result["native_inventory_size"] = ((string[])Collector.GetField("inventory", Hidden).GetValue(null)).Length;
                    Assert.AreEqual(50000, replayed);
                    Report(result);
                    string[] candidate = null;
                    JObject fresh = Measure(
                        "candidate_fresh_find_sort",
                        round,
                        () =>
                        {
                            candidate = AssetDatabase.FindAssets("");
                            Array.Sort(candidate, StringComparer.Ordinal);
                        }
                    );
                    CollectionAssert.AreEqual((string[])Collector.GetField("inventory", Hidden).GetValue(null), candidate);
                    fresh["native_inventory_size"] = candidate.Length;
                    // Publishing a fresh token into the single-snapshot cache is
                    // not equivalent: another census may still own this token.
                    Collector.GetField("inventoryToken", Hidden).SetValue(null, Guid.NewGuid().ToString("N"));
                    try
                    {
                        var invalidated = Assert.Throws<TargetInvocationException>(() =>
                            ReferenceGraphCollectorTests.Call("inventory", "snapshot", token, "offset", 1, "page_size", 1)
                        );
                        Assert.IsInstanceOf<InvalidOperationException>(invalidated.InnerException);
                        fresh["continuation_equivalent"] = false;
                    }
                    finally
                    {
                        Collector.GetField("inventoryToken", Hidden).SetValue(null, token);
                    }
                    Report(fresh);
                    Assert.AreEqual(
                        token,
                        ReferenceGraphCollectorTests.Call("inventory", "snapshot", token, "offset", 1, "page_size", 1).Value<string>("snapshot")
                    );
                }
            }
            finally
            {
                Journal.GetField("changes", Hidden).SetValue(null, original);
                Journal.GetProperty("Epoch", Hidden).SetValue(null, epoch);
                Journal.GetProperty("Revision", Hidden).SetValue(null, revision);
                SessionState.SetString(StateKey, stored);
                Collector.GetField("inventory", Hidden).SetValue(null, null);
            }
        }
    }
}
