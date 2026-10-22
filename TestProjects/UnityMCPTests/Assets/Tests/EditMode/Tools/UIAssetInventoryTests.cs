using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class UIAssetInventoryTests
    {
        private string root,
            rootGuid;
        private readonly HashSet<string> paths = new HashSet<string>(StringComparer.Ordinal);
        private Type Inventory => typeof(ManageUI).Assembly.GetType("MCPForUnity.Editor.Helpers.UIAssetInventory");
        private Type Search => typeof(ManageUI).Assembly.GetType("MCPForUnity.Editor.Helpers.AssetSearchSnapshots");
        private object priorObserver,
            priorClock,
            priorBudget;
        private int finds,
            resolves;

        private FieldInfo Field(string name) => Inventory?.GetField(name, BindingFlags.NonPublic | BindingFlags.Static);

        private void Invalidate() => Inventory?.GetMethod("Invalidate", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);

        [SetUp]
        public void SetUp()
        {
            root = "Assets/__UIInventory_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(Directory.Exists(AssetPathUtility.GetFullAssetPath(root)));
            rootGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            Assert.IsNotEmpty(rootGuid);
            paths.Clear();
            AddAssets(2, "Initial");
            var panel = ScriptableObject.CreateInstance<PanelSettings>();
            string panelPath = root + "/Panel.asset";
            try
            {
                AssetDatabase.CreateAsset(panel, panelPath);
                paths.Add(panelPath);
            }
            finally
            {
                if (!AssetDatabase.Contains(panel))
                    UnityEngine.Object.DestroyImmediate(panel);
            }
            priorObserver = Search.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            priorClock = Field("Clock")?.GetValue(null);
            priorBudget = Field("CacheBudgetBytes")?.GetValue(null);
            Search
                .GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static)
                .SetValue(
                    null,
                    (Action<string>)(
                        operation =>
                        {
                            if (operation == "ui_find_assets")
                                finds++;
                            if (operation == "ui_resolve_guid")
                                resolves++;
                        }
                    )
                );
            finds = resolves = 0;
            Invalidate();
        }

        [TearDown]
        public void TearDown()
        {
            Invalidate();
            Search.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, priorObserver);
            if (priorClock != null)
                Field("Clock").SetValue(null, priorClock);
            if (priorBudget != null)
                Field("CacheBudgetBytes").SetValue(null, priorBudget);
            if (string.IsNullOrEmpty(rootGuid))
                return;
            Assert.AreEqual(root, AssetDatabase.GUIDToAssetPath(rootGuid));
            Assert.IsTrue(AssetDatabase.DeleteAsset(root));
        }

        private void AddAssets(int count, string prefix)
        {
            AssetDatabase.StartAssetEditing();
            try
            {
                for (int i = 0; i < count; i++)
                    foreach (string extension in new[] { ".uxml", ".uss" })
                    {
                        string path = root + "/" + prefix + i + extension;
                        File.WriteAllText(
                            AssetPathUtility.GetFullAssetPath(path),
                            extension == ".uxml" ? "<ui:UXML xmlns:ui='UnityEngine.UIElements' />" : ".owned { color: red; }"
                        );
                        AssetDatabase.ImportAsset(path);
                        paths.Add(path);
                    }
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
            }
        }

        private JObject List(int size = 2, int page = 1, string revision = null, string filter = null)
        {
            var request = new JObject
            {
                ["action"] = "list",
                ["path"] = root,
                ["page_size"] = size,
                ["page_number"] = page,
            };
            if (revision != null)
                request["inventory_revision"] = revision;
            if (filter != null)
                request["filter_type"] = filter;
            return JObject.FromObject(ManageUI.HandleCommand(request));
        }

        private static JObject Data(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        [Test]
        public void BoundPagesCoverEveryAssetAndRejectImportedMutation()
        {
            var first = Data(List());
            string revision = (string)first["inventory_revision"];
            Assert.IsFalse(string.IsNullOrEmpty(revision), "A retained inventory must expose its revision.");
            var seen = new List<string>();
            for (int page = 1; page <= 3; page++)
                seen.AddRange(Data(List(page: page, revision: revision))["assets"].Select(row => (string)row["path"]));
            CollectionAssert.AreEquivalent(paths, seen);
            Assert.AreEqual(paths.Count, seen.Distinct().Count());
            Assert.AreEqual(3, finds);
            Assert.AreEqual(paths.Count + 2, resolves); // The two UXML inline styles also match the StyleSheet query.
            first["assets"][0]["path"] = "Assets/CallerEdit.uxml";
            Assert.IsTrue(paths.Contains((string)Data(List(revision: revision))["assets"][0]["path"]));
            string before = root + "/Initial0.uxml",
                after = root + "/Renamed.uxml";
            Assert.IsEmpty(AssetDatabase.MoveAsset(before, after));
            Assert.AreEqual("stale_ui_inventory", (string)List(revision: revision)["error"]);
            var fresh = Data(List(100));
            Assert.AreNotEqual(revision, (string)fresh["inventory_revision"]);
            Assert.IsTrue(fresh["assets"].Any(row => (string)row["path"] == after));
        }

        [Test]
        public void QueryMismatchAndMalformedRevisionDoNotRediscoverAssets()
        {
            string revision = (string)Data(List())["inventory_revision"];
            Assert.IsFalse(string.IsNullOrEmpty(revision), "A retained inventory must expose its revision.");
            Data(List(filter: "uxml")); // The destination query already has a different retained revision.
            finds = resolves = 0;
            Assert.AreEqual("ui_inventory_query_mismatch", (string)List(revision: revision, filter: "uxml")["error"]);
            Assert.AreEqual("invalid_ui_inventory_revision", (string)List(revision: "bad")["error"]);
            Assert.AreEqual(0, finds + resolves);
        }

        [TestCase("uxml", ".uxml", 2)]
        [TestCase("uss", ".uss", 2)]
        [TestCase("PanelSettings", ".asset", 1)]
        public void TypeFiltersExcludeEmbeddedSubassets(string filter, string extension, int count)
        {
            var data = Data(List(100, filter: filter));
            Assert.AreEqual(count, data.Value<int>("total"));
            Assert.IsTrue(data["assets"].All(row => ((string)row["path"]).EndsWith(extension, StringComparison.Ordinal)));
        }

        [Test]
        public void CacheEvictionMakesOldRevisionStaleAndKeepsCompleteResults()
        {
            string revision = (string)Data(List())["inventory_revision"];
            foreach (string filter in new[] { "uxml", "uss", "PanelSettings", "unsupported" })
                Data(List(filter: filter));
            Assert.AreEqual("stale_ui_inventory", (string)List(revision: revision)["error"]);
            Assert.AreEqual(paths.Count, Data(List(100)).Value<int>("total"));
        }

        [TestCase(128L)]
        [TestCase(1024L)]
        public void CacheBudgetDoesNotRestrictCompleteLargeInventoryPaging(long budget)
        {
            Assert.IsNotNull(Inventory);
            Field("CacheBudgetBytes").SetValue(null, budget);
            var all = Data(List(100));
            Assert.AreEqual(paths.Count, all.Value<int>("total"));
            Assert.IsFalse(all.Value<bool>("inventory_retained"));
            Assert.AreEqual(JTokenType.Null, all["inventory_revision"].Type);
            var seen = new List<string>();
            for (int page = 1; page <= 3; page++)
                seen.AddRange(Data(List(page: page))["assets"].Select(row => (string)row["path"]));
            CollectionAssert.AreEquivalent(paths, seen);
        }

        [Test]
        public void ExpiredInventoryReleasesIdleOwnerAndRejectsOldRevision()
        {
            Assert.IsNotNull(Inventory);
            double now = 10;
            Field("Clock").SetValue(null, (Func<double>)(() => now));
            string revision = (string)Data(List())["inventory_revision"];
            now += 1801;
            Inventory.GetMethod("CleanupIdle", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            Assert.IsFalse((bool)Field("CleanupScheduled").GetValue(null));
            Assert.AreEqual(0L, (long)Field("RetainedBytes").GetValue(null));
            Assert.AreEqual("stale_ui_inventory", (string)List(revision: revision)["error"]);
        }

        [Test]
        public void InvalidationDuringDiscoveryDoesNotPublishPartialInventory()
        {
            Assert.IsNotNull(Inventory);
            Search
                .GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static)
                .SetValue(
                    null,
                    (Action<string>)(
                        operation =>
                        {
                            if (operation == "ui_find_assets")
                                Invalidate();
                        }
                    )
                );
            Assert.AreEqual("stale_ui_inventory", (string)List()["error"]);
        }

        private (double ms, long allocationEvents) Measure(Action work)
        {
            using var recorder = Unity.Profiling.ProfilerRecorder.StartNew(
                Unity.Profiling.ProfilerCategory.Memory,
                "GC.Alloc",
                131072,
                Unity.Profiling.ProfilerRecorderOptions.StartImmediately | Unity.Profiling.ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            var clock = System.Diagnostics.Stopwatch.StartNew();
            work();
            clock.Stop();
            recorder.Stop();
            Assert.Less(recorder.Count, recorder.Capacity);
            Assert.IsTrue(recorder.Valid, "Allocation event instrumentation is unavailable.");
            return (clock.Elapsed.TotalMilliseconds, recorder.Count);
        }

        [Test]
        public void RepeatedCompleteInventoryPagesMeasureActualDiscoveryAndAllocations()
        {
            AddAssets(48, "Benchmark");
            Invalidate();
            finds = resolves = 0;
            var expected = paths.Where(path => path.EndsWith(".uxml", StringComparison.Ordinal)).ToArray();
            var cold = Measure(() => Assert.AreEqual(expected.Length, Data(List(10, filter: "uxml")).Value<int>("total")));
            TestContext.WriteLine(
                $"BETA07_COLD rows={expected.Length} ms={cold.ms:F3} alloc_events={cold.allocationEvents} find_calls={finds} guid_calls={resolves}"
            );
            for (int repetition = 0; repetition < 3; repetition++)
            {
                finds = resolves = 0;
                double milliseconds = 0;
                long allocationEvents = 0;
                var seen = new List<string>();
                for (int page = 1; page <= 5; page++)
                {
                    var measured = Measure(() => seen.AddRange(Data(List(10, page, filter: "uxml"))["assets"].Select(row => (string)row["path"])));
                    milliseconds += measured.ms;
                    allocationEvents = allocationEvents >= 0 && measured.allocationEvents >= 0 ? allocationEvents + measured.allocationEvents : -1;
                }
                CollectionAssert.AreEquivalent(expected, seen);
                Assert.AreEqual(expected.Length, seen.Count);
                TestContext.WriteLine(
                    $"BETA07_PAGES repetition={repetition} rows={expected.Length} pages=5 ms={milliseconds:F3} alloc_events={allocationEvents} find_calls={finds} guid_calls={resolves}"
                );
            }
        }
    }
}
