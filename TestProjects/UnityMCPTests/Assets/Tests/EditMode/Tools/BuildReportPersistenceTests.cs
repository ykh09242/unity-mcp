using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class BuildReportPersistenceTests
    {
        private string ownedRoot;
        private readonly Dictionary<string, BuildJob> previousJobs = new Dictionary<string, BuildJob>();
        private readonly Dictionary<string, BatchJob> previousBatches = new Dictionary<string, BatchJob>();
        private BuildJob previousLast;
        private object previousStorage;
        private object previousSession;
        private object previousClock;
        private Type Store => typeof(BuildJobStore).Assembly.GetType("MCPForUnity.Editor.Tools.Build.BuildReportStore");
        private static Dictionary<string, BuildJob> Jobs =>
            (Dictionary<string, BuildJob>)typeof(BuildJobStore).GetField("_buildJobs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
        private static Dictionary<string, BatchJob> Batches =>
            (Dictionary<string, BatchJob>)typeof(BuildJobStore).GetField("_batchJobs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
        private static FieldInfo Last => typeof(BuildJobStore).GetField("_lastCompletedJob", BindingFlags.NonPublic | BindingFlags.Static);

        private FieldInfo Field(string name) => Store?.GetField(name, BindingFlags.NonPublic | BindingFlags.Static);

        [SetUp]
        public void SetUp()
        {
            Assert.IsFalse(BuildPipeline.isBuildingPlayer, "The report fixture must not interfere with an active native build.");
            if (BuildJobStore.ActiveJobId != null)
                Assert.Ignore("An unowned live build job must remain untouched.");
            previousJobs.Clear();
            previousBatches.Clear();
            foreach (var pair in Jobs)
                previousJobs.Add(pair.Key, pair.Value);
            foreach (var pair in Batches)
                previousBatches.Add(pair.Key, pair.Value);
            previousLast = BuildJobStore.LastCompletedJob;
            Jobs.Clear();
            Batches.Clear();
            Last.SetValue(null, null);
            ownedRoot = Path.Combine(Path.GetDirectoryName(Application.dataPath), ".work", "BuildReportFixture_" + Guid.NewGuid().ToString("N"));
            previousStorage = Field("StorageRootOverride")?.GetValue(null);
            previousSession = Field("sessionId")?.GetValue(null);
            previousClock = Field("UtcNow")?.GetValue(null);
            Field("StorageRootOverride")?.SetValue(null, ownedRoot);
        }

        [TearDown]
        public void TearDown()
        {
            Field("StorageRootOverride")?.SetValue(null, previousStorage);
            Field("sessionId")?.SetValue(null, previousSession);
            Field("UtcNow")?.SetValue(null, previousClock);
            Jobs.Clear();
            Batches.Clear();
            foreach (var pair in previousJobs)
                Jobs.Add(pair.Key, pair.Value);
            foreach (var pair in previousBatches)
                Batches.Add(pair.Key, pair.Value);
            Last.SetValue(null, previousLast);
            if (Directory.Exists(ownedRoot))
            {
                StringAssert.StartsWith(Path.Combine(Path.GetDirectoryName(Application.dataPath), ".work", "BuildReportFixture_"), ownedRoot);
                Assert.IsFalse((File.GetAttributes(ownedRoot) & FileAttributes.ReparsePoint) != 0);
                Directory.Delete(ownedRoot, true);
            }
        }

        private static JObject Send(string action, params object[] options)
        {
            var request = new JObject { ["action"] = action };
            for (int i = 0; i < options.Length; i += 2)
                request[(string)options[i]] = JToken.FromObject(options[i + 1]);
            return JObject.FromObject(ManageBuild.HandleCommand(request));
        }

        private static JToken Success(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return response["data"];
        }

        private BuildJob Complete(BuildJobState state, double mb = 1, int errors = 0)
        {
            var job = new BuildJob("build-" + Guid.NewGuid().ToString("N"), BuildTarget.StandaloneWindows64, Path.Combine(ownedRoot, "private", "Game.exe"))
            {
                State = state,
                StartedAt = DateTime.UtcNow.AddSeconds(-2),
                CompletedAt = DateTime.UtcNow,
                TotalSizeMb = mb,
                TotalErrors = errors,
                TotalWarnings = 1,
                ErrorMessage = "Do not persist arbitrary exception paths C:/private/account/build.log",
            };
            BuildJobStore.AddBuildJob(job);
            BuildJobStore.SetLastCompleted(job);
            return job;
        }

        private void ForgetStaticJobs()
        {
            Jobs.Clear();
            Batches.Clear();
            Last.SetValue(null, null);
            Assert.IsNotNull(Store, "Build report persistence must survive the loss of the in-memory job store.");
            Field("sessionId").SetValue(null, Guid.NewGuid().ToString("N"));
        }

        private string Id(BuildJob job)
        {
            var history = Success(Send("report_history"));
            return (string)history["reports"].Single(row => (string)row["job_id"] == job.JobId)["report_id"];
        }

        [Test]
        public void CompletedSuccessAndFailureRemainRetrievableAfterStaticJobLossWithoutAbsolutePaths()
        {
            // Given: completed observations from both lifecycle outcomes.
            var good = Complete(BuildJobState.Succeeded);
            var bad = Complete(BuildJobState.Failed, 0, 2);
            string goodId = Id(good),
                badId = Id(bad);
            var before = Success(Send("report", "report_id", goodId));
            // When: static jobs/session state are lost as on domain reload.
            ForgetStaticJobs();
            var after = Success(Send("report", "report_id", goodId));
            var failure = Success(Send("report", "report_id", badId));
            // Then: completion is preserved, fallback observations are honest, and paths stay private.
            Assert.AreEqual(before["report_revision"], after["report_revision"]);
            Assert.AreEqual("succeeded", (string)after["summary"]["result"]);
            Assert.AreEqual("failed", (string)failure["summary"]["result"]);
            Assert.IsFalse((bool)after["summary"]["native_report_available"]);
            Assert.AreEqual(goodId, (string)Success(Send("status", "job_id", good.JobId))["report_id"]);
            foreach (string path in Directory.GetFiles(ownedRoot, "*.json"))
            {
                string text = File.ReadAllText(path);
                Assert.IsFalse(
                    text.Contains(ownedRoot) || text.Contains("C:/private") || text.Contains("C:\\private"),
                    "Persisted artifacts must not expose absolute paths."
                );
            }
        }

        [Test]
        public void ReloadedActiveJobsAreInterruptedObservationsNotCompletedReports()
        {
            // Given: a pending request with no completed native report.
            var active = new BuildJob("build-" + Guid.NewGuid().ToString("N"), BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe");
            BuildJobStore.AddBuildJob(active);
            // When: a new Editor domain observes its persisted marker.
            ForgetStaticJobs();
            var history = Success(Send("report_history"));
            // Then: it cannot be mistaken for an available or resumed BuildReport.
            var row = history["active_observations"].Single(item => (string)item["job_id"] == active.JobId);
            Assert.AreEqual("interrupted", (string)row["state"]);
            Assert.IsFalse((bool)row["resumed"]);
            Assert.IsFalse(history["reports"].Any(item => (string)item["job_id"] == active.JobId));
        }

        [Test]
        public void BatchTerminalSummaryAndChildrenSurviveStaticJobLoss()
        {
            // Given: mixed terminal child outcomes and a completed ordinary batch.
            var good = Complete(BuildJobState.Succeeded, 2);
            var bad = Complete(BuildJobState.Failed, 0, 3);
            var batch = new BatchJob("batch-" + Guid.NewGuid().ToString("N")) { State = BuildJobState.Building, CurrentIndex = 1 };
            batch.Children.Add(good);
            batch.Children.Add(bad);
            BuildJobStore.AddBatchJob(batch);
            // When: normal batch finalization persists its outcome then static state disappears.
            BuildRunner.ScheduleNextBatchBuild(batch, _ => throw new InvalidOperationException("No extra child may run."));
            ForgetStaticJobs();
            var history = Success(Send("report_history"));
            string id = (string)history["reports"].Single(row => (string)row["job_id"] == batch.JobId)["report_id"];
            var report = Success(Send("report", "report_id", id));
            // Then: batch counts/results and child report identities remain linked.
            Assert.AreEqual("batch", (string)report["summary"]["kind"]);
            Assert.AreEqual("failed", (string)report["summary"]["result"]);
            Assert.AreEqual(2, (int)report["summary"]["child_count"]);
            Assert.AreEqual(2, report["children"].Count());
            Assert.IsTrue(report["children"].All(row => row["report_id"] != null));
        }

        private JObject Prepared(BuildJob job)
        {
            Assert.IsNotNull(Store, "The bounded build report store must exist.");
            return (JObject)Store.GetMethod("CreateRecord", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { job, null });
        }

        private string Persist(JObject record) =>
            (string)Store.GetMethod("PersistSnapshot", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { record });

        [Test]
        public void DetailsPageAndComparisonReportAddedRemovedAndSizeChangesRatherThanFileLength()
        {
            // Given: owned prepared report DTOs at the storage boundary, not fabricated native builds.
            var a = Complete(BuildJobState.Succeeded, 1);
            var b = Complete(BuildJobState.Succeeded, 2);
            var old = Prepared(a);
            var current = Prepared(b);
            old["steps"] = new JArray(
                new JObject
                {
                    ["name"] = "Removed",
                    ["depth"] = 0,
                    ["duration_seconds"] = 1.0,
                }
            );
            old["packed_assets"] = new JArray(new JObject { ["source_asset_path"] = "Assets/Owned/Old.asset", ["packed_size_bytes"] = 10 });
            current["steps"] = new JArray(
                Enumerable
                    .Range(0, 205)
                    .Select(i => new JObject
                    {
                        ["name"] = "Step" + i,
                        ["depth"] = 0,
                        ["duration_seconds"] = 2.0,
                    })
            );
            current["packed_assets"] = new JArray(
                Enumerable.Range(0, 205).Select(i => new JObject { ["source_asset_path"] = "Assets/Owned/New" + i + ".asset", ["packed_size_bytes"] = 20 })
            );
            string aid = Persist(old),
                bid = Persist(current);
            // When: requesting all packed-asset pages and comparing semantic inventories.
            var p1 = Success(Send("report", "report_id", bid, "report_section", "packed_assets", "page_size", 100));
            var p2 = Success(
                Send(
                    "report",
                    "report_id",
                    bid,
                    "report_section",
                    "packed_assets",
                    "page_size",
                    100,
                    "page_number",
                    2,
                    "report_revision",
                    (string)p1["report_revision"]
                )
            );
            var p3 = Success(Send("report", "report_id", bid, "report_section", "packed_assets", "page_size", 100, "page_number", 3));
            var compared = Success(Send("compare_reports", "baseline_report_id", aid, "candidate_report_id", bid, "report_section", "packed_assets"));
            // Then: all 205 details are reachable and added/removed bytes/counts are meaningful.
            Assert.AreEqual(100, p1["entries"].Count());
            Assert.AreEqual(100, p2["entries"].Count());
            Assert.AreEqual(5, p3["entries"].Count());
            Assert.AreEqual(205, (int)p1["total_entries"]);
            Assert.AreEqual(205, (int)compared["added_count"]);
            Assert.AreEqual(1, (int)compared["removed_count"]);
            Assert.AreEqual(4090, (decimal)compared["packed_size_delta_bytes"]);
            var stepDiff = Success(Send("compare_reports", "baseline_report_id", aid, "candidate_report_id", bid, "report_section", "steps"));
            Assert.AreEqual(205, (int)stepDiff["added_count"]);
            Assert.AreEqual(1, (int)stepDiff["removed_count"]);
            old["packed_assets"] = new JArray(
                new JObject { ["source_asset_path"] = "Assets/Owned/Shared.asset", ["packed_size_bytes"] = 5 },
                new JObject { ["source_asset_path"] = "Assets/Owned/Shared.asset", ["packed_size_bytes"] = 5 }
            );
            current["packed_assets"] = new JArray(new JObject { ["source_asset_path"] = "Assets/Owned/Shared.asset", ["packed_size_bytes"] = 10 });
            Persist(old);
            Persist(current);
            var occurrences = Success(Send("compare_reports", "baseline_report_id", aid, "candidate_report_id", bid, "report_section", "packed_assets"));
            Assert.AreEqual(1, (int)occurrences["changed_count"], "Equal bytes must not hide a changed packed-object occurrence count.");
            Assert.AreEqual(0, (decimal)occurrences["packed_size_delta_bytes"]);
            Assert.AreEqual(2, (int)occurrences["entries"][0]["baseline"]["occurrences"]);
            Assert.AreEqual(1, (int)occurrences["entries"][0]["candidate"]["occurrences"]);
            current["summary"]["warnings"] = 7;
            Persist(current);
            Assert.AreEqual(
                "build_report_revision_conflict",
                (string)Send("report", "report_id", bid, "report_revision", (string)p1["report_revision"])["code"]
            );
        }

        [Test]
        public void RetentionAndShardsKeepSummaryAndAllBoundedDetailRows()
        {
            // Given: more completed observations than retention, and a large bounded detail inventory.
            var first = Complete(BuildJobState.Succeeded);
            string firstId = Id(first);
            for (int i = 0; i < 65; i++)
                Complete(BuildJobState.Succeeded);
            var history = Success(Send("report_history", "page_size", 100));
            Assert.AreEqual(64, (int)history["total_reports"], "Completed history must remain within the declared retention bound.");
            Assert.AreEqual("build_report_not_found_or_evicted", (string)Send("report", "report_id", firstId)["code"]);
            var job = Complete(BuildJobState.Succeeded, 3);
            var record = Prepared(job);
            record["steps"] = new JArray(
                Enumerable
                    .Range(0, 512)
                    .Select(i => new JObject
                    {
                        ["name"] = new string('s', 200) + i,
                        ["depth"] = 0,
                        ["duration_seconds"] = 1.0,
                    })
            );
            record["packed_assets"] = new JArray(
                Enumerable
                    .Range(0, 2048)
                    .Select(i => new JObject { ["source_asset_path"] = "Assets/Owned/" + new string('x', 470) + i + ".asset", ["packed_size_bytes"] = 20 })
            );
            record["steps_complete"] = true;
            record["packed_assets_complete"] = true;
            // When: detail bytes exceed the artifact budget even though row counts fit their limits.
            string id = Persist(record);
            var details = Success(Send("report", "report_id", id, "report_section", "packed_assets", "page_number", int.MaxValue));
            // Then: the terminal summary stays compact while every captured row remains pageable.
            Assert.LessOrEqual(new FileInfo(Path.Combine(ownedRoot, id + ".json")).Length, 512 * 1024);
            Assert.AreEqual("succeeded", (string)details["summary"]["result"]);
            Assert.IsTrue((bool)details["details_complete"]);
            Assert.AreEqual(2048, details.Value<int>("total_entries"));
            Assert.AreEqual(0, details["entries"].Count(), "A far-out page must be empty without integer overflow.");
            Assert.AreEqual("invalid_build_report_request", (string)Send("report", "report_id", id, "page_size", 0)["code"]);
        }

        private static System.Collections.Generic.IEnumerable<JObject> PackedRows(int count)
        {
            for (int i = 0; i < count; i++)
                yield return new JObject { ["source_asset_path"] = "Assets/Owned/Entry" + i + ".asset", ["packed_size_bytes"] = i + 1 };
        }

        [Test]
        public void LargeDetailLatePageReadsOnlyItsShardsAndKeepsCorruptionVisible()
        {
            var job = Complete(BuildJobState.Succeeded);
            var record = Prepared(job);
            record["packed_assets_complete"] = true;
            string id = (string)
                Store
                    .GetMethod("WriteSnapshot", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(null, new object[] { record, Enumerable.Empty<JObject>(), PackedRows(100000) });
            string summaryPath = Path.Combine(ownedRoot, id + ".json");
            Assert.Less(new FileInfo(summaryPath).Length, 16384);
            var envelope = JObject.Parse(File.ReadAllText(summaryPath));
            var indexed = JObject.Parse(envelope.Value<string>("payload"));
            string generation = indexed["detail_index"].Value<string>("generation");
            string first = Path.Combine(ownedRoot, id + ".details", generation, "packed_assets-000000.json");
            Store
                .GetMethod("Atomic", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { first, System.Text.Encoding.UTF8.GetBytes("corrupt earlier shard") });
            var later = Success(Send("report", "report_id", id, "report_section", "packed_assets", "page_size", 100, "page_number", 901));
            Assert.AreEqual(100000, later.Value<int>("total_entries"));
            Assert.AreEqual(100, later["entries"].Count());
            Assert.AreEqual("Assets/Owned/Entry90000.asset", later["entries"][0].Value<string>("source_asset_path"));
            Assert.IsTrue(later.Value<bool>("details_complete"));
            Assert.AreEqual("requested_page_verified", later.Value<string>("details_integrity"));
            Assert.AreEqual("build_report_corrupt", Send("report", "report_id", id, "report_section", "packed_assets").Value<string>("code"));
            var history = Success(Send("report_history"));
            Assert.AreEqual("not_checked", history["reports"][0].Value<string>("details_integrity"));
            Assert.IsFalse(Directory.EnumerateFiles(ownedRoot, "write-*.tmp").Any());
        }

        private double MeasureHistory()
        {
            var clock = System.Diagnostics.Stopwatch.StartNew();
            Assert.AreEqual(64, Success(Send("report_history", "page_size", 100)).Value<int>("total_reports"));
            clock.Stop();
            return clock.Elapsed.TotalMilliseconds;
        }

        private long MeasureOneLoadAllocations(string id)
        {
            using var recorder = Unity.Profiling.ProfilerRecorder.StartNew(
                Unity.Profiling.ProfilerCategory.Memory,
                "GC.Alloc",
                131072,
                Unity.Profiling.ProfilerRecorderOptions.StartImmediately | Unity.Profiling.ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            Assert.IsTrue(recorder.Valid);
            Store.GetMethod("Load", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { id, null, false });
            recorder.Stop();
            Assert.Less(recorder.Count, recorder.Capacity, "Allocation measurement must not silently truncate.");
            long bytes = 0;
            for (int i = 0; i < recorder.Count; i++)
                bytes += recorder.GetSample(i).Value;
            return bytes;
        }

        [Test]
        public void SixtyFourReportHistoryBenchmarkKeepsLegacyCompatibilityAndReducesAllocation()
        {
            Directory.CreateDirectory(ownedRoot);
            var sourceRecords = new List<JObject>();
            for (int i = 0; i < 64; i++)
            {
                var job = new BuildJob("benchmark-" + i, BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe")
                {
                    State = BuildJobState.Succeeded,
                    CompletedAt = DateTime.UtcNow,
                };
                var record = Prepared(job);
                record["packed_assets"] = new JArray(PackedRows(1024));
                record["packed_assets_complete"] = true;
                sourceRecords.Add(record);
                string text = record.ToString(Newtonsoft.Json.Formatting.None);
                string revision = (string)Store.GetMethod("Hash", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { text });
                // Real previous storage format, not a mocked history implementation.
                var envelope = new JObject { ["revision"] = revision, ["record"] = record };
                File.WriteAllText(Path.Combine(ownedRoot, record.Value<string>("report_id") + ".json"), envelope.ToString(Newtonsoft.Json.Formatting.None));
            }
            var legacyCold = MeasureHistory();
            var legacyWarm = MeasureHistory();
            long legacyAllocation = MeasureOneLoadAllocations(sourceRecords[0].Value<string>("report_id"));
            // Compare equivalent retained inventories; fixture preparation is not a migration benchmark.
            // Delete only the exact legacy files just authored, then persist the same records.
            foreach (JObject record in sourceRecords)
                File.Delete(Path.Combine(ownedRoot, record.Value<string>("report_id") + ".json"));
            foreach (JObject record in sourceRecords)
                Persist(record);
            var indexedCold = MeasureHistory();
            var indexedWarm = MeasureHistory();
            long indexedAllocation = MeasureOneLoadAllocations(sourceRecords[0].Value<string>("report_id"));
            TestContext.WriteLine(
                $"NEXT13_HISTORY legacy_cold_ms={legacyCold:F3} legacy_warm_ms={legacyWarm:F3} indexed_cold_ms={indexedCold:F3} indexed_warm_ms={indexedWarm:F3} legacy_one_record_alloc_bytes={legacyAllocation} indexed_one_record_alloc_bytes={indexedAllocation}"
            );
            Assert.Greater(legacyAllocation, 0);
            Assert.Less(indexedAllocation, legacyAllocation / 10);
            Assert.IsTrue(Directory.GetFiles(ownedRoot, "br-*.json").All(path => new FileInfo(path).Length < 16384));
        }

        [Test]
        public void ReplacementDoesNotDeleteGenerationWithUnknownFilesOrMismatchedOwner()
        {
            var source = Prepared(Complete(BuildJobState.Succeeded));
            string id = Persist(source);
            string directory = Directory.GetDirectories(Path.Combine(ownedRoot, id + ".details")).Single();
            string unknown = Path.Combine(directory, "user-notes.txt");
            Store
                .GetMethod("Atomic", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { unknown, System.Text.Encoding.UTF8.GetBytes("preserve") });
            Persist(source);
            Assert.IsTrue(File.Exists(unknown));
            File.Delete(unknown);
            Store
                .GetMethod("Atomic", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { Path.Combine(directory, "owner.json"), System.Text.Encoding.UTF8.GetBytes("{}") });
            Persist(source);
            Assert.IsTrue(Directory.Exists(directory));
            Assert.IsTrue(Send("report", "report_id", id).Value<bool>("success"));
        }

        [Test]
        public void OptionalStorageFailureCannotChangeSuccessfulBuildOutcome()
        {
            File.WriteAllText(ownedRoot, "Owned file deliberately blocks report directory creation");
            try
            {
                var job = Complete(BuildJobState.Succeeded);
                Assert.AreEqual(BuildJobState.Succeeded, job.State);
                Assert.AreEqual("build_report_persistence_unavailable", job.ReportPersistenceError);
            }
            finally
            {
                File.Delete(ownedRoot);
            }
        }

        [Test]
        public void CorruptionExpiryAndUnsafeIdsAreExplicitWithoutLeakingFilesystemPaths()
        {
            // Given: one retained report in an owned artifact directory.
            var job = Complete(BuildJobState.Failed);
            string id = Id(job);
            // When: retention expires, and later a known owned artifact becomes corrupt.
            Field("UtcNow").SetValue(null, new Func<DateTime>(() => DateTime.UtcNow.AddDays(31)));
            Assert.AreEqual("build_report_expired", (string)Send("report", "report_id", id)["code"]);
            Field("UtcNow").SetValue(null, previousClock);
            File.WriteAllText(Path.Combine(ownedRoot, id + ".json"), "{broken");
            var corrupt = Send("report", "report_id", id);
            // Then: corruption, expiry and malformed identity are distinguished, never raw exceptions.
            Assert.AreEqual("build_report_corrupt", (string)corrupt["code"]);
            Assert.IsFalse(corrupt.ToString().Contains(ownedRoot));
            Assert.IsFalse((bool)Send("report", "report_id", "../outside")["success"]);
        }
    }
}
