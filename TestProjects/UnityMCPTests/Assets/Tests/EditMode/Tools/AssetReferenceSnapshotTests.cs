using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools
{
    [TestFixture]
    public sealed class AssetReferenceSnapshotTests
    {
        private string root;
        private string source;
        private string reverseSource;
        private string scanRoot;
        private readonly List<string> nodePaths = new List<string>();

        [SetUp]
        public void SetUp()
        {
            AssetDatabase.DisallowAutoRefresh();
            string name = "__McpReferencePages_" + Guid.NewGuid().ToString("N");
            root = "Assets/" + name;
            source = root + "/source.asset";
            scanRoot = root;
            nodePaths.Clear();
            AssetDatabase.CreateFolder("Assets", name);
            Clear();
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                Clear();
                StringAssert.StartsWith("Assets/__McpReferencePages_", root);
                Assert.IsTrue(AssetDatabase.DeleteAsset(root));
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
        }

        private static void Clear() =>
            typeof(ManageAsset)
                .Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceSnapshotStore")
                .GetMethod("Clear", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, null);

        private void Graph(int count, bool separateReverseTarget = false)
        {
            var center = ScriptableObject.CreateInstance<ReferenceDiagnosticFixture>();
            AssetDatabase.CreateAsset(center, source);
            reverseSource = source;
            Object reverseTarget = center;
            if (separateReverseTarget)
            {
                reverseSource = root + "/target.bytes";
                File.WriteAllText(reverseSource, "owned reverse target");
                AssetDatabase.ImportAsset(reverseSource, ImportAssetOptions.ForceSynchronousImport);
                reverseTarget = AssetDatabase.LoadAssetAtPath<TextAsset>(reverseSource);
            }
            AssetDatabase.StartAssetEditing();
            try
            {
                for (int i = 0; i < count; i++)
                {
                    var node = ScriptableObject.CreateInstance<ReferenceDiagnosticFixture>();
                    node.reference = reverseTarget;
                    string folder = root;
                    if (separateReverseTarget)
                    {
                        folder += "/group-" + (i / 1000);
                        if (i % 1000 == 0)
                            AssetDatabase.CreateFolder(root, "group-" + (i / 1000));
                    }
                    string path = folder + "/node-" + i.ToString("D5") + ".asset";
                    AssetDatabase.CreateAsset(node, path);
                    nodePaths.Add(path);
                    center.references.Add(node);
                }
                EditorUtility.SetDirty(center);
                AssetDatabase.SaveAssets();
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
            }
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport);
        }

        private JObject Request(string action, int page = 1, int size = 3, string cursor = null, bool capture = false)
        {
            var p = new JObject
            {
                ["action"] = action,
                ["path"] = action == "get_dependencies" ? source : reverseSource,
                ["scanRoot"] = scanRoot,
                ["maxAssets"] = 250000,
                ["maxWork"] = 4000000,
                ["pageSize"] = size,
                ["pageNumber"] = page,
            };
            if (cursor != null)
                p["referenceCursor"] = cursor;
            if (capture)
                p["referenceSnapshot"] = true;
            return p;
        }

        private static JObject Data(JObject p)
        {
            var response = JObject.FromObject(ManageAsset.HandleCommand(p));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return (JObject)response["data"];
        }

        [TestCase("get_dependencies")]
        [TestCase("get_reverse_references")]
        public void CapturedPagesMatchFullUnionWithoutReopeningSource(string action)
        {
            Graph(9);
            var expected = (JArray)Data(Request(action, size: 100))["entries"];
            var first = Data(Request(action, capture: true));
            Assert.AreEqual("captured", first.Value<string>("freshness"));
            string cursor = first.Value<string>("referenceCursor");
            Assert.IsNotEmpty(cursor);
            Assert.AreEqual(2, first.Value<int>("nextPageNumber"));
            var union = new JArray(((JArray)first["entries"]).Select(row => row.DeepClone()));
            first["entries"][0]["path"] = "mutated caller copy";
            using (File.Open(source, FileMode.Open, FileAccess.Read, FileShare.None))
            {
                Assert.AreEqual(expected[0]["path"], Data(Request(action, cursor: cursor))["entries"][0]["path"]);
                int pages = (expected.Count + 2) / 3;
                for (int page = 2; page <= pages; page++)
                {
                    var data = Data(Request(action, page, cursor: cursor));
                    foreach (var row in data["entries"])
                        union.Add(row);
                    Assert.AreEqual(page < pages, data.Value<bool>("hasMore"));
                }
            }
            Assert.IsTrue(JToken.DeepEquals(expected, union));
        }

        [Test]
        public void ImportedChangesAndReloadInvalidateOnlyOwnedAssetSnapshots()
        {
            Graph(4);
            string cursor = Data(Request("get_dependencies", capture: true)).Value<string>("referenceCursor");
            var wrong = Request("get_dependencies", cursor: cursor);
            wrong["recursive"] = true;
            Assert.IsFalse(JObject.FromObject(ManageAsset.HandleCommand(wrong)).Value<bool>("success"));
            File.AppendAllText(source, "\n# imported revision changed\n");
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            Assert.IsFalse(JObject.FromObject(ManageAsset.HandleCommand(Request("get_dependencies", cursor: cursor))).Value<bool>("success"));
            cursor = Data(Request("get_dependencies", capture: true)).Value<string>("referenceCursor");
            Clear();
            Assert.IsFalse(JObject.FromObject(ManageAsset.HandleCommand(Request("get_dependencies", cursor: cursor))).Value<bool>("success"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SavedEditorTopologyChangesInvalidateAssetCursor(bool saveOnlyDirtyAsset)
        {
            Graph(3);
            var captured = Data(Request("get_dependencies", size: 100, capture: true));
            var center = AssetDatabase.LoadAssetAtPath<ReferenceDiagnosticFixture>(source);
            center.references.RemoveAt(0);
            EditorUtility.SetDirty(center);
            if (saveOnlyDirtyAsset)
                AssetDatabase.SaveAssetIfDirty(center);
            else
                AssetDatabase.SaveAssets();
            Assert.IsFalse(
                JObject
                    .FromObject(ManageAsset.HandleCommand(Request("get_dependencies", cursor: captured.Value<string>("referenceCursor"))))
                    .Value<bool>("success"),
                "A saved imported graph change must invalidate its previous cursor."
            );
            var live = Data(Request("get_dependencies", size: 100));
            Assert.AreEqual(captured.Value<int>("observedEntries") - 1, live.Value<int>("observedEntries"));
        }

        [Test]
        public void CapturedEvidenceCannotReplaceLiveMutationRevisionChecks()
        {
            Graph(2);
            var preview = Data(Request("preview_delete", capture: true));
            var guarded = Request("delete");
            guarded["referenceRevision"] = preview["referenceRevision"];
            guarded["referenceCursor"] = preview["referenceCursor"];
            Assert.IsFalse(JObject.FromObject(ManageAsset.HandleCommand(guarded)).Value<bool>("success"));
            guarded.Remove("referenceCursor");
            File.AppendAllText(source, "\n# changed without import\n");
            var response = JObject.FromObject(ManageAsset.HandleCommand(guarded));
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("reference_revision_conflict", response.ToString());
            Assert.IsTrue(File.Exists(source));
        }

        private static Type Store => typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceSnapshotStore");
        private static long RetainedBytes => (long)Store.GetField("retainedBytes", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);

        [Test]
        public void ExpiredCursorReleasesItsDiskAndOffsetOwnership()
        {
            Graph(3);
            string cursor = Data(Request("get_dependencies", capture: true)).Value<string>("referenceCursor");
            Assert.Greater(RetainedBytes, 0);
            var entries = (IDictionary)Store.GetField("Entries", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            object node = entries[cursor];
            object snapshot = node.GetType().GetProperty("Value").GetValue(node);
            snapshot.GetType().GetField("Touched", BindingFlags.NonPublic | BindingFlags.Instance).SetValue(snapshot, -10000d);
            Store.GetField("sweepAt", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, 0d);
            Assert.IsFalse(JObject.FromObject(ManageAsset.HandleCommand(Request("get_dependencies", cursor: cursor))).Value<bool>("success"));
            Assert.AreEqual(0, RetainedBytes);
            Assert.AreEqual(0, entries.Count);
        }

        private static IEnumerable<JObject> CanceledRows(CancellationTokenSource cancellation)
        {
            yield return new JObject { ["path"] = "Assets/first.asset" };
            cancellation.Cancel();
            yield return new JObject { ["path"] = "Assets/second.asset" };
        }

        [Test]
        public void CancellationBeforePublicationDeletesIncompleteDiskEvidence()
        {
            string directory = "Temp/McpReferenceSnapshots";
            string[] before = Directory.Exists(directory) ? Directory.GetFiles(directory) : Array.Empty<string>();
            using var cancellation = new CancellationTokenSource();
            var error = Assert.Throws<TargetInvocationException>(() =>
                Store
                    .GetMethod("CaptureAssets", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(null, new object[] { new JObject(), CanceledRows(cancellation), 2, "owned canceled capture", cancellation.Token })
            );
            Assert.IsInstanceOf<OperationCanceledException>(error.InnerException);
            Assert.AreEqual(0, RetainedBytes);
            CollectionAssert.AreEquivalent(before, Directory.GetFiles(directory));
        }

        [Test, Explicit("Controller-owned complete 100-page reference measurement."), Timeout(720000)]
        public void CompleteTenThousandEntryPagesPreserveOutputAndReleaseRetention()
        {
            Graph(9999, true);
            foreach (string action in new[] { "get_dependencies", "get_reverse_references" })
            {
                bool forward = action == "get_dependencies";
                scanRoot = forward ? root : root + "/group-0";
                int expectedEntries = forward ? 10000 : 1000;
                int pageSize = forward ? 100 : 10;
                JArray expected = null;
                for (int round = 0; round < 4; round++)
                {
                    bool captured = round % 2 == 1;
                    Clear();
                    GC.Collect();
                    long before = GC.GetTotalMemory(true);
                    var timer = Stopwatch.StartNew();
                    string cursor = null;
                    string revision = null;
                    var union = new JArray();
                    for (int page = 1; page <= 100; page++)
                    {
                        var data = Data(Request(action, page, pageSize, cursor, captured && page == 1));
                        Assert.IsTrue(data.Value<bool>("scanComplete"));
                        Assert.AreEqual(expectedEntries, data.Value<int>("totalEntries"));
                        revision ??= data.Value<string>("referenceRevision");
                        Assert.AreEqual(revision, data.Value<string>("referenceRevision"));
                        cursor = captured ? data.Value<string>("referenceCursor") : null;
                        foreach (var row in data["entries"])
                            union.Add(row);
                    }
                    timer.Stop();
                    long retained = GC.GetTotalMemory(true) - before;
                    if (expected == null)
                        expected = union;
                    else
                        Assert.IsTrue(JToken.DeepEquals(expected, union));
                    Assert.AreEqual(expectedEntries, union.Count);
                    long diskBytes = RetainedBytes;
                    Clear();
                    Assert.AreEqual(0, RetainedBytes);
                    TestContext.Out.WriteLine(
                        "REFERENCE_PAGES "
                            + new JObject
                            {
                                ["action"] = action,
                                ["captured"] = captured,
                                ["round"] = round,
                                ["pages"] = 100,
                                ["scanRoot"] = scanRoot,
                                ["pageSize"] = pageSize,
                                ["entries"] = union.Count,
                                ["milliseconds"] = timer.Elapsed.TotalMilliseconds,
                                ["managedDelta"] = retained,
                                ["snapshotDiskBytes"] = diskBytes,
                                ["afterClearDiskBytes"] = RetainedBytes,
                            }.ToString(Newtonsoft.Json.Formatting.None)
                    );
                    UnityEngine.Debug.Log($"Reference page round complete: {action}, captured={captured}, milliseconds={timer.Elapsed.TotalMilliseconds:F1}");
                    if (cursor != null)
                        Assert.IsFalse(JObject.FromObject(ManageAsset.HandleCommand(Request(action, cursor: cursor))).Value<bool>("success"));
                }
            }
            // Verify the complete large reverse union independently of repeated baseline cost.
            scanRoot = root;
            var complete = new JArray();
            string fullCursor = null;
            for (int page = 1; page <= 100; page++)
            {
                var data = Data(Request("get_reverse_references", page, 100, fullCursor, page == 1));
                fullCursor = data.Value<string>("referenceCursor");
                Assert.AreEqual(9999, data.Value<int>("totalEntries"));
                foreach (var row in data["entries"])
                    complete.Add(row);
                if (page == 1 || page == 50 || page == 100)
                    Assert.IsTrue(JToken.DeepEquals(data["entries"], Data(Request("get_reverse_references", page, 100))["entries"]));
            }
            CollectionAssert.AreEqual(nodePaths.OrderBy(path => path, StringComparer.Ordinal), complete.Select(row => row.Value<string>("path")));
            long fullDisk = RetainedBytes;
            Clear();
            Assert.AreEqual(0, RetainedBytes);
            TestContext.Out.WriteLine(
                "REFERENCE_LARGE_REVERSE "
                    + new JObject
                    {
                        ["entries"] = complete.Count,
                        ["pages"] = 100,
                        ["livePagesCompared"] = 3,
                        ["snapshotDiskBytes"] = fullDisk,
                        ["afterClearDiskBytes"] = RetainedBytes,
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
        }
    }
}
