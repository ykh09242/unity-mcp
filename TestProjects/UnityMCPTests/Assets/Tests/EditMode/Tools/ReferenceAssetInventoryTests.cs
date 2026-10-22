using System;
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

namespace MCPForUnityTests.EditMode.Tools
{
    [TestFixture]
    public sealed class ReferenceAssetInventoryTests
    {
        private string root;
        private SerializationMode? savedSerialization;

        private static MethodInfo CollectMethod =>
            typeof(ManageAsset)
                .Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceAssetInventory")
                ?.GetMethod("Collect", BindingFlags.Static | BindingFlags.NonPublic);

        private static SortedSet<string> Collect(
            IEnumerable<string> guids,
            Func<string, string> paths,
            Func<string, bool> folders,
            int maximum,
            CancellationToken token
        )
        {
            Assert.IsNotNull(CollectMethod, "One explicit file-budget inventory must replace the hidden raw-GUID ceiling.");
            return (SortedSet<string>)CollectMethod.Invoke(null, new object[] { guids, paths, folders, maximum, token, 0 });
        }

        [TestCase(65535)]
        [TestCase(65536)]
        [TestCase(65537)]
        [TestCase(250000)]
        public void AdvertisedMaximumCountsDistinctFilesWithoutHiddenGuidCeiling(int count)
        {
            var ids = Enumerable
                .Range(0, count)
                .Select(i => "Assets/node-" + i.ToString("D6"))
                .Concat(new[] { "Assets/folder", "Assets/node-000001", "Packages/excluded" });
            var result = Collect(ids, id => id, path => path == "Assets/folder", count, CancellationToken.None);
            Assert.AreEqual(count, result.Count);
            CollectionAssert.AreEqual(Enumerable.Range(0, count).Select(i => "Assets/node-" + i.ToString("D6")), result);
        }

        [Test]
        public void InventoryCancellationStopsBeforeResolvingTheNextCandidate()
        {
            using var cancellation = new CancellationTokenSource();
            int visited = 0;
            var error = Assert.Throws<TargetInvocationException>(() =>
                Collect(
                    new[] { "a", "b", "c" },
                    id =>
                    {
                        visited++;
                        cancellation.Cancel();
                        return "Assets/" + id;
                    },
                    _ => false,
                    250000,
                    cancellation.Token
                )
            );
            Assert.IsInstanceOf<OperationCanceledException>(error.InnerException);
            Assert.AreEqual(1, visited);
        }

        [Test]
        public void CapacityRefusalReportsActualRequestedAndSupportedLimits()
        {
            var error = Assert.Throws<TargetInvocationException>(() =>
                Collect(new[] { "Assets/a", "Assets/b" }, id => id, _ => false, 1, CancellationToken.None)
            );
            Assert.IsInstanceOf<ArgumentException>(error.InnerException);
            StringAssert.Contains("maxAssets=1", error.InnerException.Message);
            StringAssert.Contains("maximum 250000", error.InnerException.Message);
            StringAssert.DoesNotContain("4096", error.InnerException.Message);
        }

        [TearDown]
        public void TearDown()
        {
            typeof(ManageAsset)
                .Assembly.GetType("MCPForUnity.Editor.Tools.ReferenceSnapshotStore")
                .GetMethod("Clear", BindingFlags.Static | BindingFlags.NonPublic)
                .Invoke(null, null);
            if (savedSerialization.HasValue)
            {
                EditorSettings.serializationMode = savedSerialization.Value;
                savedSerialization = null;
            }
            if (root != null)
            {
                StringAssert.StartsWith("Assets/__McpLargeInventory_", root);
                Assert.IsTrue(AssetDatabase.DeleteAsset(root));
                root = null;
            }
        }

        [Test, Explicit("Controller-owned native 65,537-file boundary and complete continuation."), Timeout(1800000)]
        public void NativeInventoryAbove65536CompletesEveryCandidateAndReferencePage() => VerifyNativeInventory(65537);

        [Test]
        public void SmallNativeInventoryCompletesEveryCandidateAndReferencePage() => VerifyNativeInventory(128);

        private void VerifyNativeInventory(int count)
        {
            savedSerialization = EditorSettings.serializationMode;
            EditorSettings.serializationMode = SerializationMode.ForceText;
            root = "Assets/__McpLargeInventory_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            string inventory = root + "/inventory";
            AssetDatabase.CreateFolder(root, "inventory");
            string source = root + "/target.bytes";
            File.WriteAllText(source, "owned target");
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport);
            var template = ScriptableObject.CreateInstance<ReferenceDiagnosticFixture>();
            template.reference = AssetDatabase.LoadAssetAtPath<TextAsset>(source);
            string first = inventory + "/owner-000.asset";
            AssetDatabase.CreateAsset(template, first);
            AssetDatabase.SaveAssets();
            string yaml = File.ReadAllText(first);
            Assert.IsTrue(yaml.StartsWith("%YAML"), "The isolated fixture requires text serialization.");
            AssetDatabase.DisallowAutoRefresh();
            var preparation = Stopwatch.StartNew();
            try
            {
                for (int i = 1; i < count; i++)
                    File.WriteAllText(
                        inventory + (i < 100 ? "/owner-" + i.ToString("D3") + ".asset" : "/empty-" + i.ToString("D5") + ".b13f"),
                        i < 100 ? yaml.Replace("m_Name: owner-000", "m_Name: owner-" + i.ToString("D3")) : "owned inventory fixture"
                    );
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }
            finally
            {
                AssetDatabase.AllowAutoRefresh();
            }
            preparation.Stop();
            UnityEngine.Debug.Log("REFERENCE_INVENTORY_STAGE imported " + count);
            var native = Stopwatch.StartNew();
            string[] ids = AssetDatabase.FindAssets("", new[] { inventory });
            native.Stop();
            Assert.AreEqual(count, ids.Count(id => !AssetDatabase.IsValidFolder(AssetDatabase.GUIDToAssetPath(id))), "Complete native file inventory.");
            JObject Request(int maximum, int page = 1, string cursor = null)
            {
                var request = new JObject
                {
                    ["action"] = "get_reverse_references",
                    ["path"] = source,
                    ["scanRoot"] = inventory,
                    ["maxAssets"] = maximum,
                    ["maxWork"] = 4000000,
                    ["pageSize"] = 7,
                    ["pageNumber"] = page,
                    ["referenceSnapshot"] = cursor == null,
                };
                if (cursor != null)
                    request["referenceCursor"] = cursor;
                return request;
            }
            foreach (int maximum in new[] { 64, 1024, 65535, 65536 }.Where(value => value < count))
            {
                var refused = JObject.FromObject(ManageAsset.HandleCommand(Request(maximum)));
                Assert.IsFalse(refused.Value<bool>("success"));
                StringAssert.Contains("maxAssets=" + maximum, refused.ToString());
                StringAssert.Contains("maximum 250000", refused.ToString());
            }
            UnityEngine.Debug.Log("REFERENCE_INVENTORY_STAGE inventory and refusals verified " + count);
            var total = Stopwatch.StartNew();
            var paths = new List<string>();
            string cursor = null;
            int pages = 0;
            for (int page = 1; page <= 15; page++)
            {
                var response = JObject.FromObject(ManageAsset.HandleCommand(Request(250000, page, cursor)));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var data = (JObject)response["data"];
                if (page == 1)
                    UnityEngine.Debug.Log("REFERENCE_INVENTORY_STAGE complete scan " + count);
                Assert.IsTrue(data.Value<bool>("scanComplete"));
                Assert.AreEqual(count, data.Value<int>("inspectedAssets"));
                Assert.AreEqual(250000, data.Value<int>("maximumMaxAssets"));
                Assert.AreEqual(100, data.Value<int>("totalEntries"));
                string nextCursor = data.Value<string>("referenceCursor");
                Assert.AreEqual("captured", data.Value<string>("freshness"));
                Assert.IsNotEmpty(nextCursor);
                if (cursor != null)
                    Assert.AreEqual(cursor, nextCursor);
                cursor = nextCursor;
                paths.AddRange(data["entries"].Select(row => row.Value<string>("path")));
                pages++;
                Assert.AreEqual(page < 15, data.Value<bool>("hasMore"));
            }
            total.Stop();
            CollectionAssert.AreEqual(Enumerable.Range(0, 100).Select(i => inventory + "/owner-" + i.ToString("D3") + ".asset"), paths);
            TestContext.Out.WriteLine(
                "REFERENCE_INVENTORY "
                    + new JObject
                    {
                        ["candidates"] = count,
                        ["nativeGuidCount"] = ids.Length,
                        ["nativeEnumerationMs"] = native.Elapsed.TotalMilliseconds,
                        ["fixtureImportMs"] = preparation.Elapsed.TotalMilliseconds,
                        ["completeScanAndPagesMs"] = total.Elapsed.TotalMilliseconds,
                        ["pages"] = pages,
                        ["references"] = paths.Count,
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
        }
    }
}
