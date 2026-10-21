using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ManageAssetSearchTests
    {
        private string _folder;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/AssetSearchTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            for (int i = 0; i < 5; i++)
                File.WriteAllText(_folder + "/item" + i + ".txt", "test " + i);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(_folder);
        }

        [Test]
        public void Search_ReturnsRequestedPageWithTotalMatchingCount()
        {
            var expectedPaths = AssetDatabase.FindAssets("t:TextAsset", new[] { _folder }).Select(AssetDatabase.GUIDToAssetPath).Skip(2).Take(2).ToArray();

            var response = Search(2, 2);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(5, response["data"]["totalAssets"].Value<int>());
            CollectionAssert.AreEqual(expectedPaths, response["data"]["assets"].Select(a => a["path"].Value<string>()).ToArray());
        }

        [Test]
        public void HugePageNumber_ReturnsEmptyPageWithoutWrapping()
        {
            var response = Search(50, int.MaxValue);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(5, response["data"]["totalAssets"].Value<int>());
            Assert.IsEmpty(response["data"]["assets"]);
        }

        [TestCase(0, 1)]
        [TestCase(-1, 1)]
        [TestCase(1, 0)]
        [TestCase(1, -1)]
        public void InvalidPagination_ReturnsError(int pageSize, int pageNumber)
        {
            var response = Search(pageSize, pageNumber);

            Assert.IsFalse(response.Value<bool>("success"));
        }

        [TestCase("missing-folder")]
        [TestCase("item0.txt")]
        public void InvalidScope_ReturnsErrorInsteadOfSearchingEntireProject(string child)
        {
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "search",
                        ["path"] = _folder + "/" + child,
                        ["filterType"] = "TextAsset",
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("not a valid folder", response.Value<string>("error"));
        }

        [Test]
        public void InvalidDate_ReturnsErrorInsteadOfDroppingFilter()
        {
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "search",
                        ["path"] = _folder,
                        ["filterDateAfter"] = "not-a-date",
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("filterDateAfter", response.Value<string>("error"));
        }

        [Test]
        public void DateFilter_AppliesBeforePagingAndTotalCount()
        {
            var cutoff = DateTime.UtcNow.AddDays(-1);
            File.SetLastWriteTimeUtc(_folder + "/item0.txt", cutoff.AddDays(-1));
            for (int i = 1; i < 5; i++)
                File.SetLastWriteTimeUtc(_folder + "/item" + i + ".txt", cutoff.AddHours(1));
            var expectedPaths = AssetDatabase
                .FindAssets("t:TextAsset", new[] { _folder })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(path => File.GetLastWriteTimeUtc(path) > cutoff)
                .Skip(2)
                .Take(2)
                .ToArray();

            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "search",
                        ["path"] = _folder,
                        ["filterType"] = "TextAsset",
                        ["filterDateAfter"] = cutoff.ToString("o"),
                        ["pageSize"] = 2,
                        ["pageNumber"] = 2,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(4, response["data"]["totalAssets"].Value<int>());
            CollectionAssert.AreEqual(expectedPaths, response["data"]["assets"].Select(a => a["path"].Value<string>()).ToArray());
        }

        [Test]
        public void LegacyPages_RepeatDiscoveryAndLoadOnlyReturnedObjects()
        {
            // Given: real imported assets and the same inert observer used by metadata acceptance.
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: requesting two legacy pages with preview disabled.
                var first = Search(2, 1);
                var second = Search(2, 2);
                // Then: the baseline positively observes discovery and the existing instance-ID loads.
                Assert.IsTrue(first.Value<bool>("success"));
                Assert.IsTrue(second.Value<bool>("success"));
                Assert.AreEqual(2, operations.Count(value => value == "find_assets"));
                Assert.AreEqual(4, operations.Count(value => value == "load_asset"));
                Assert.AreEqual(0, operations.Count(value => value == "asset_preview"));
                Assert.IsTrue(first["data"]["assets"].All(row => row.Value<int>("instanceID") != 0));
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [Test]
        public void MetadataPages_ReuseOrderedRowsWithoutObjectOrPreviewLoads()
        {
            // Given: expected order comes independently from Unity, before observing the tool.
            var expected = AssetDatabase.FindAssets("t:TextAsset", new[] { _folder }).Select(AssetDatabase.GUIDToAssetPath).ToArray();
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: a new metadata search is continued and the second page is repeated.
                var first = Metadata(2, 1);
                Assert.IsTrue(first.Value<bool>("success"), first.ToString());
                string token = first["data"].Value<string>("searchSnapshot");
                var second = Metadata(2, 2, token);
                var repeated = Metadata(2, 2, token);
                var last = Metadata(2, 3, token);
                // Then: one discovery produces exactly the original ordered rows, with idempotent pages.
                Assert.IsTrue(second.Value<bool>("success"), second.ToString());
                Assert.IsTrue(last.Value<bool>("success"), last.ToString());
                CollectionAssert.AreEqual(
                    expected,
                    first["data"]["assets"].Concat(second["data"]["assets"]).Concat(last["data"]["assets"]).Select(row => row.Value<string>("path"))
                );
                Assert.IsTrue(JToken.DeepEquals(second["data"]["assets"], repeated["data"]["assets"]));
                Assert.AreEqual(5, first["data"].Value<int>("totalAssets"));
                Assert.IsFalse(last["data"].Value<bool>("hasMore"));
                Assert.AreEqual(1, operations.Count(value => value == "find_assets"));
                Assert.AreEqual(5, operations.Count(value => value == "read_write_time"));
                Assert.AreEqual(0, operations.Count(value => value == "load_asset"));
                Assert.AreEqual(0, operations.Count(value => value == "asset_preview"));
                Assert.IsTrue(
                    first["data"]
                        ["assets"]
                        .All(row => row.Value<int>("instanceID") == 0 && !row.Value<bool>("instanceIDAvailable") && row.Value<bool>("metadataOnly"))
                );
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [Test]
        public void MetadataDateFilter_PrecedesSnapshotCountAndPaging()
        {
            // Given: exact-cutoff and older files must be excluded, without sorting the remaining GUID order.
            var cutoff = DateTime.UtcNow.AddDays(-1);
            File.SetLastWriteTimeUtc(_folder + "/item0.txt", cutoff);
            File.SetLastWriteTimeUtc(_folder + "/item1.txt", cutoff.AddSeconds(-1));
            for (int i = 2; i < 5; i++)
                File.SetLastWriteTimeUtc(_folder + "/item" + i + ".txt", cutoff.AddHours(1));
            var expected = AssetDatabase
                .FindAssets("t:TextAsset", new[] { _folder })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(path => File.GetLastWriteTimeUtc(path) > cutoff)
                .Skip(2)
                .Take(2)
                .ToArray();
            // When: filtering and requesting page two of the new snapshot.
            var response = Metadata(2, 2, date: cutoff.ToString("o"));
            // Then: count and page both describe only the three eligible rows.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(3, response["data"].Value<int>("totalAssets"));
            CollectionAssert.AreEqual(expected, response["data"]["assets"].Select(row => row.Value<string>("path")));
        }

        [Test]
        public void SnapshotExpiry_IsFixedAndExpiresAtExactMonotonicDeadline()
        {
            // Given: a deterministic monotonic clock, restored even when an assertion fails.
            var clock = SnapshotType.GetField("Clock", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = clock.GetValue(null);
            double now = 100;
            try
            {
                clock.SetValue(null, (Func<double>)(() => now));
                var first = Metadata(2, 1);
                Assert.IsTrue(first.Value<bool>("success"), first.ToString());
                string token = first["data"].Value<string>("searchSnapshot");
                now = 1899.999;
                Assert.IsTrue(Metadata(2, 2, token).Value<bool>("success"));
                // When: reaching the original deadline, despite the recent read.
                now = 1900;
                var expired = Metadata(2, 2, token);
                // Then: no read extends the token lifetime, and a fresh query is the positive control.
                Assert.AreEqual("stale_search_snapshot", expired.Value<string>("code"));
                Assert.IsTrue(Metadata(2, 1).Value<bool>("success"));
            }
            finally
            {
                clock.SetValue(null, previous);
                InvalidateSnapshots();
            }
        }

        [Test]
        public void SnapshotCapacity_EvictsLeastRecentlyReadEntry()
        {
            // Given: four distinct snapshots, with the oldest refreshed in LRU order only.
            var tokens = Enumerable.Range(0, 4).Select(_ => Metadata(2, 1)["data"].Value<string>("searchSnapshot")).ToArray();
            Assert.IsTrue(Metadata(2, 2, tokens[0]).Value<bool>("success"));
            // When: admitting a fifth snapshot.
            Assert.IsTrue(Metadata(2, 1).Value<bool>("success"));
            // Then: only the least recently used entry is stale.
            Assert.AreEqual("stale_search_snapshot", Metadata(2, 2, tokens[1]).Value<string>("code"));
            Assert.IsTrue(Metadata(2, 2, tokens[0]).Value<bool>("success"));
        }

        [TestCase("import")]
        [TestCase("move")]
        [TestCase("delete")]
        public void ActualAssetNotification_InvalidatesOldTokenAndAllowsFreshSearch(string operation)
        {
            // Given: a real snapshot before an actual owned AssetDatabase mutation.
            string token = Metadata(2, 1)["data"].Value<string>("searchSnapshot");
            // When: Unity imports, moves, or deletes the owned asset, without calling the hook by reflection.
            switch (operation)
            {
                case "import":
                    File.AppendAllText(_folder + "/item0.txt", "changed");
                    AssetDatabase.ImportAsset(_folder + "/item0.txt", ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
                    break;
                case "move":
                    Assert.IsEmpty(AssetDatabase.MoveAsset(_folder + "/item0.txt", _folder + "/moved.txt"));
                    break;
                case "delete":
                    Assert.IsTrue(AssetDatabase.DeleteAsset(_folder + "/item0.txt"));
                    break;
            }
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            // Then: a continuation cannot rebuild silently; an explicit new search succeeds.
            Assert.AreEqual("stale_search_snapshot", Metadata(2, 2, token).Value<string>("code"));
            Assert.IsTrue(Metadata(2, 1).Value<bool>("success"));
        }

        [Test]
        public void SnapshotConflictAndMalformedToken_DoNotDiscoverOrLoad()
        {
            // Given: an admitted snapshot and the same actual-call observer as the legacy baseline.
            string token = Metadata(2, 1)["data"].Value<string>("searchSnapshot");
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: changing page size or supplying an invalid token.
                var mismatch = Metadata(3, 2, token);
                var malformed = Metadata(2, 2, "invalid");
                // Then: both fail at the snapshot boundary, without a fallback search or object load.
                Assert.AreEqual("search_snapshot_query_mismatch", mismatch.Value<string>("code"));
                Assert.AreEqual("invalid_search_snapshot", malformed.Value<string>("code"));
                Assert.IsEmpty(operations);
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [Test]
        public void InvalidationDuringDiscovery_RefusesMixedSnapshot()
        {
            // Given: the actual FindAssets boundary changes epoch during snapshot creation.
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(
                    null,
                    (Action<string>)(
                        operation =>
                        {
                            if (operation == "find_assets")
                                InvalidateSnapshots();
                        }
                    )
                );
                // When: discovery runs in the invalidated epoch.
                var result = Metadata(2, 1);
                // Then: no mixed snapshot is admitted.
                Assert.AreEqual("stale_search_snapshot", result.Value<string>("code"));
            }
            finally
            {
                field.SetValue(null, previous);
            }
            Assert.IsTrue(Metadata(2, 1).Value<bool>("success"));
        }

        [Test]
        public void MetadataHugePage_IsEmptyWithoutOverflow()
        {
            // Given/When: an enormous page over a bounded real snapshot.
            var result = Metadata(1000, int.MaxValue);
            // Then: the filtered count remains accurate and the page is empty.
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(5, result["data"].Value<int>("totalAssets"));
            Assert.IsEmpty(result["data"]["assets"]);
            Assert.IsFalse(result["data"].Value<bool>("hasMore"));
        }

        [Test]
        public void MetadataRowsBeyondOldLimitRemainFullyPageable()
        {
            var rows = new List<JObject>();
            var append = SnapshotType.GetMethod("TryAppendRow", BindingFlags.NonPublic | BindingFlags.Static);
            int bytes = 0;
            for (int i = 0; i < 5000; i++)
            {
                object[] input =
                {
                    rows,
                    new JObject { ["path"] = "Assets/Item" + i + ".asset" },
                    bytes,
                };
                Assert.IsTrue((bool)append.Invoke(null, input), "Metadata row " + i + " must be admitted.");
                bytes = (int)input[2];
            }
            var type = SnapshotType.GetNestedType("Snapshot", BindingFlags.NonPublic);
            object snapshot = Activator.CreateInstance(
                type,
                BindingFlags.Instance | BindingFlags.NonPublic,
                null,
                new object[] { "owned", "query", rows, bytes, double.MaxValue, "future" },
                null
            );
            long epoch = (long)SnapshotType.GetField("Epoch", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
            var page = SnapshotType.GetMethod("Page", BindingFlags.NonPublic | BindingFlags.Static);
            JObject result = JObject.FromObject(page.Invoke(null, new object[] { snapshot, 1000, 5, epoch }));
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(5000, (int)result["data"]["totalAssets"]);
            Assert.AreEqual("Assets/Item4999.asset", (string)result["data"]["assets"][999]["path"]);
            Assert.IsFalse((bool)result["data"]["hasMore"]);
        }

        [Test]
        public void RowLimit_RefusesTheNextRowWithoutAppending()
        {
            // Given: the documented maximum count, independent of production constants.
            var rows = Enumerable.Repeat(new JObject { ["path"] = "owned" }, 250000).ToList();
            object[] arguments =
            {
                rows,
                new JObject { ["path"] = "overflow" },
                0,
            };
            // When: the next row reaches the actual incremental admission seam.
            bool accepted = (bool)SnapshotType.GetMethod("TryAppendRow", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, arguments);
            // Then: no truncated-success snapshot can be built past the limit.
            Assert.IsFalse(accepted);
            Assert.AreEqual(250000, rows.Count);
            Assert.AreEqual(0, arguments[2]);
        }

        [Test]
        public void PayloadLimit_AccountsUtf8AndRefusesBeyondExactBoundary()
        {
            // Given: an independently measured multibyte fixture row at the documented byte ceiling.
            var row = new JObject { ["path"] = "owned-\u03b1\u03b2\u03b3" };
            int bytes = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
            var rows = new List<JObject>();
            var append = SnapshotType.GetMethod("TryAppendRow", BindingFlags.NonPublic | BindingFlags.Static);
            object[] exact = { rows, row, 64 * 1024 * 1024 - bytes };
            Assert.IsTrue((bool)append.Invoke(null, exact));
            Assert.AreEqual(64 * 1024 * 1024, exact[2]);
            // When: appending even one additional row.
            object[] overflow = { rows, row, exact[2] };
            bool accepted = (bool)append.Invoke(null, overflow);
            // Then: aggregate accounting cannot silently overflow, and the rows are unchanged.
            Assert.IsFalse(accepted);
            Assert.AreEqual(1, rows.Count);
            Assert.AreEqual(exact[2], overflow[2]);
        }

        [Test]
        public void OversizedSerializedRow_IsRefusedWithoutRetention()
        {
            // Given: a single fixture row larger than the documented serialized-row cap.
            var rows = new List<JObject>();
            object[] arguments =
            {
                rows,
                new JObject { ["path"] = new string('x', 64 * 1024) },
                0,
            };
            // When: it reaches the same admission seam used by real search results.
            bool accepted = (bool)SnapshotType.GetMethod("TryAppendRow", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, arguments);
            // Then: the row is never retained.
            Assert.IsFalse(accepted);
            Assert.IsEmpty(rows);
            Assert.AreEqual(0, arguments[2]);
        }

        [Test]
        public void MetadataLongQuery_IsRefusedBeforeDiscovery()
        {
            // Given: a query exceeding the new opt-in's documented per-string cap.
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: passing the oversized string through the actual native boundary.
                var result = JObject.FromObject(
                    ManageAsset.HandleCommand(
                        new JObject
                        {
                            ["action"] = "search_metadata_v1",
                            ["path"] = _folder,
                            ["searchPattern"] = new string('x', 4097),
                        }
                    )
                );
                // Then: no discovery or load precedes the typed limit error.
                Assert.AreEqual("search_snapshot_limit_exceeded", result.Value<string>("code"));
                Assert.IsEmpty(operations);
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [Test]
        public void UnknownWellFormedToken_IsStaleWithoutDiscovery()
        {
            // Given: a syntactically valid token which this Editor never admitted.
            string token = "mcp-as-v1_" + new string('a', 32) + "_" + new string('b', 32);
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: trying to continue its page.
                var result = Metadata(2, 2, token);
                // Then: it refuses without a fallback search.
                Assert.AreEqual("stale_search_snapshot", result.Value<string>("code"));
                Assert.IsEmpty(operations);
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [Test]
        public void InProgressPayload_EvictsRetainedEntryBeforeExceedingAggregateAccounting()
        {
            // Given: a real admitted snapshot and a bounded synthetic pending-row accounting fixture.
            string token = Metadata(2, 1)["data"].Value<string>("searchSnapshot");
            var row = new JObject { ["path"] = "owned" };
            int bytes = Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
            object[] arguments = { new List<JObject>(), row, 64 * 1024 * 1024 - bytes };
            // When: the same incremental admission method needs the whole accounted budget.
            Assert.IsTrue((bool)SnapshotType.GetMethod("TryAppendRow", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, arguments));
            // Then: previous retained rows have already been evicted, before the new row is retained.
            Assert.AreEqual("stale_search_snapshot", Metadata(2, 2, token).Value<string>("code"));
            Assert.IsTrue(Metadata(2, 1).Value<bool>("success"));
        }

        [Test]
        public void OwnFailedMutationAttempt_InvalidatesSynchronouslyWithoutImportNotification()
        {
            // Given: a real token and a nonexistent owned target, so no asset import can explain invalidation.
            string token = Metadata(2, 1)["data"].Value<string>("searchSnapshot");
            // When: a relevant mutation reaches dispatch and fails because the target does not exist.
            var failed = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["path"] = _folder + "/missing.txt",
                        ["properties"] = new JObject(),
                    }
                )
            );
            // Then: the token is already stale, with a fresh search as the positive control.
            Assert.IsFalse(failed.Value<bool>("success"));
            Assert.AreEqual("stale_search_snapshot", Metadata(2, 2, token).Value<string>("code"));
            Assert.IsTrue(Metadata(2, 1).Value<bool>("success"));
        }

        [TestCase("searchPattern", "item1")]
        [TestCase("filterType", "Material")]
        [TestCase("filterDateAfter", "2000-01-01T00:00:00Z")]
        [TestCase("path", "Assets")]
        public void SnapshotFingerprint_RejectsChangedQuerySemanticsBeforeDiscovery(string key, string value)
        {
            // Given: a token for the original effective query/scope/date/type.
            string token = Metadata(2, 1)["data"].Value<string>("searchSnapshot");
            var request = new JObject
            {
                ["action"] = "search_metadata_v1",
                ["path"] = _folder,
                ["filterType"] = "TextAsset",
                ["pageSize"] = 2,
                ["pageNumber"] = 2,
                ["searchSnapshot"] = token,
            };
            request[key] = value;
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: a continuation changes one query semantic while retaining the token.
                var result = JObject.FromObject(ManageAsset.HandleCommand(request));
                // Then: no discovery or object loading can follow that conflict.
                Assert.AreEqual("search_snapshot_query_mismatch", result.Value<string>("code"));
                Assert.IsEmpty(operations);
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        [Test]
        public void MetadataPreview_IsRefusedAtNativeBoundaryBeforeDiscovery()
        {
            // Given: a direct native request, independent of Python validation.
            var operations = new List<string>();
            var field = SnapshotType.GetField("OperationObserved", BindingFlags.NonPublic | BindingFlags.Static);
            var previous = field.GetValue(null);
            try
            {
                field.SetValue(null, (Action<string>)(operations.Add));
                // When: metadata search also asks for a preview.
                var result = JObject.FromObject(
                    ManageAsset.HandleCommand(
                        new JObject
                        {
                            ["action"] = "search_metadata_v1",
                            ["path"] = _folder,
                            ["generatePreview"] = true,
                        }
                    )
                );
                // Then: the native boundary itself refuses it without asset calls.
                Assert.AreEqual("invalid_metadata_search", result.Value<string>("code"));
                Assert.IsEmpty(operations);
            }
            finally
            {
                field.SetValue(null, previous);
            }
        }

        private static Type SnapshotType => typeof(ManageAsset).Assembly.GetType("MCPForUnity.Editor.Helpers.AssetSearchSnapshots", true);

        [UnityEngine.TestTools.UnityTest]
        public System.Collections.IEnumerator IdleSnapshotsExpireWithoutAnotherSearch()
        {
            var clock = SnapshotType.GetField("Clock", BindingFlags.NonPublic | BindingFlags.Static);
            object previous = clock.GetValue(null);
            double now = 1000;
            try
            {
                InvalidateSnapshots();
                clock.SetValue(null, (Func<double>)(() => now));
                var result = Metadata(1, 1);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                var entries = (System.Collections.IDictionary)SnapshotType.GetField("Entries", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                Assert.AreEqual(1, entries.Count);
                now += 1801;
                yield return null;
                yield return null;
                Assert.AreEqual(0, entries.Count, "Expired rows must be released even when no new search arrives.");
                Assert.AreEqual(0, SnapshotType.GetField("RetainedBytes", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null));
                Assert.IsFalse((bool)SnapshotType.GetField("CleanupScheduled", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null));
            }
            finally
            {
                InvalidateSnapshots();
                clock.SetValue(null, previous);
            }
        }

        private static void InvalidateSnapshots() => SnapshotType.GetMethod("Invalidate", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);

        private JObject Metadata(int size, int page, string token = null, string date = null)
        {
            var request = new JObject
            {
                ["action"] = "search_metadata_v1",
                ["path"] = _folder,
                ["filterType"] = "TextAsset",
                ["pageSize"] = size,
                ["pageNumber"] = page,
            };
            if (token != null)
                request["searchSnapshot"] = token;
            if (date != null)
                request["filterDateAfter"] = date;
            return JObject.FromObject(ManageAsset.HandleCommand(request));
        }

        private JObject Search(int pageSize, int pageNumber) =>
            JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "search",
                        ["path"] = _folder,
                        ["filterType"] = "TextAsset",
                        ["pageSize"] = pageSize,
                        ["pageNumber"] = pageNumber,
                    }
                )
            );
    }
}
