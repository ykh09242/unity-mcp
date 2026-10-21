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
        private object previousReadObserver;
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
            previousReadObserver = Field("ReadFileForTests")?.GetValue(null);
            Field("StorageRootOverride")?.SetValue(null, ownedRoot);
        }

        [TearDown]
        public void TearDown()
        {
            Field("StorageRootOverride")?.SetValue(null, previousStorage);
            Field("sessionId")?.SetValue(null, previousSession);
            Field("UtcNow")?.SetValue(null, previousClock);
            Field("ReadFileForTests")?.SetValue(null, previousReadObserver);
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

        private Func<string, JObject> StatusLookup() =>
            (Func<string, JObject>)
                Delegate.CreateDelegate(typeof(Func<string, JObject>), Store.GetMethod("FindStatus", BindingFlags.NonPublic | BindingFlags.Static));

        [TestCase(1)]
        [TestCase(16)]
        [TestCase(64)]
        public void ExactPersistedStatusReadsOneSummaryWithEquivalentOutput(int reportCount)
        {
            var jobs = new List<BuildJob>();
            for (int index = 0; index < reportCount; index++)
                jobs.Add(Complete(BuildJobState.Succeeded, index + 1));
            ForgetStaticJobs();
            string selected = jobs[0].JobId;
            Func<string, JObject> direct = StatusLookup();
            var files =
                (Func<bool, string[]>)
                    Delegate.CreateDelegate(typeof(Func<bool, string[]>), Store.GetMethod("Files", BindingFlags.NonPublic | BindingFlags.Static));
            var load =
                (Func<string, string, bool, JObject>)
                    Delegate.CreateDelegate(typeof(Func<string, string, bool, JObject>), Store.GetMethod("Load", BindingFlags.NonPublic | BindingFlags.Static));
            JObject LegacyScan()
            {
                // Previous production algorithm, same typed storage calls and same owned files.
                var records = new List<JObject>();
                foreach (string path in files(false))
                {
                    JObject record = load(Path.GetFileNameWithoutExtension(path), null, false);
                    if ((string)record["summary"]["job_id"] == selected)
                        records.Add(record);
                }
                JObject latest = records.OrderByDescending(record => (string)record["created_at"], StringComparer.Ordinal).FirstOrDefault();
                if (latest == null)
                    return null;
                var summary = (JObject)latest["summary"].DeepClone();
                summary["report_id"] = latest["report_id"];
                summary["report_revision"] = latest["report_revision"];
                summary["persisted"] = true;
                return summary;
            }
            JObject expected = LegacyScan();
            Assert.IsTrue(JToken.DeepEquals(expected, direct(selected)));
            long calibration = GC.GetAllocatedBytesForCurrentThread();
            GC.KeepAlive(new byte[4096]);
            bool allocationCounterAvailable = GC.GetAllocatedBytesForCurrentThread() > calibration;
            int reads = 0,
                maximumExactReads = 0;
            Field("ReadFileForTests").SetValue(null, (Action<string>)(_ => reads++));
            for (int round = 0; round < 3; round++)
            for (int position = 0; position < 2; position++)
            {
                bool legacy = (round + position) % 2 == 0;
                reads = 0;
                long allocated = GC.GetAllocatedBytesForCurrentThread();
                long start = System.Diagnostics.Stopwatch.GetTimestamp();
                JObject actual = legacy ? LegacyScan() : direct(selected);
                double milliseconds = (System.Diagnostics.Stopwatch.GetTimestamp() - start) * 1000.0 / System.Diagnostics.Stopwatch.Frequency;
                allocated = GC.GetAllocatedBytesForCurrentThread() - allocated;
                Assert.IsTrue(JToken.DeepEquals(expected, actual), actual?.ToString());
                if (legacy)
                    Assert.AreEqual(reportCount, reads);
                else
                    maximumExactReads = Math.Max(maximumExactReads, reads);
                TestContext.WriteLine(
                    $"BETA05 mode={(legacy ? "scan" : "exact")} reports={reportCount} round={round} reads={reads} allocated_bytes={(allocationCounterAvailable ? allocated.ToString() : "unavailable")} elapsed_ms={milliseconds:F3}"
                );
            }
            Assert.AreEqual(1, maximumExactReads, "Exact persisted status must open only its deterministic summary.");
            JObject detached = direct(selected);
            detached["job_id"] = "caller-edited";
            Assert.AreEqual(selected, (string)direct(selected)["job_id"]);
        }

        [Test]
        public void ExactStatusPreservesMissingCorruptExpiredAndLatestSemantics()
        {
            DateTime now = DateTime.UtcNow;
            Field("UtcNow").SetValue(null, (Func<DateTime>)(() => now));
            BuildJob earlier = Complete(BuildJobState.Succeeded);
            now = now.AddMinutes(1);
            BuildJob later = Complete(BuildJobState.Failed);
            string laterId = Id(later);
            ForgetStaticJobs();
            Func<string, JObject> status = StatusLookup();
            Assert.AreEqual(later.JobId, (string)status(null)["job_id"]);
            Assert.IsNull(status("build-" + Guid.NewGuid().ToString("N")));
            string path = Path.Combine(ownedRoot, laterId + ".json");
            File.Delete(path);
            WriteFixtureFile(path, "{corrupt");
            Assert.IsNull(status(later.JobId));
            Assert.AreEqual(earlier.JobId, (string)status(null)["job_id"]);
            now = now.AddDays(31);
            Assert.IsNull(status(earlier.JobId));
            Assert.IsNull(status(null));
        }

        [Test]
        public void ExactStatusVerifiesJobIdentityInsideTheMatchingArtifact()
        {
            BuildJob requested = Complete(BuildJobState.Succeeded),
                other = Complete(BuildJobState.Succeeded);
            string id = Id(requested),
                otherId = Id(other);
            string originalPath = Path.Combine(ownedRoot, otherId + ".json");
            var read =
                (Func<string, JObject>)
                    Delegate.CreateDelegate(typeof(Func<string, JObject>), Store.GetMethod("Read", BindingFlags.NonPublic | BindingFlags.Static));
            JObject envelope = read(originalPath);
            JObject record = JObject.Parse((string)envelope["payload"]);
            record["report_id"] = id;
            string payload = record.ToString(Newtonsoft.Json.Formatting.None);
            envelope["payload"] = payload;
            envelope["revision"] = (string)Store.GetMethod("Hash", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { payload });
            string path = Path.Combine(ownedRoot, id + ".json");
            File.Delete(path);
            WriteFixtureFile(path, envelope.ToString(Newtonsoft.Json.Formatting.None));
            ForgetStaticJobs();
            Assert.IsNull(StatusLookup()(requested.JobId));
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
        public void InterruptedMarkersCannotExhaustNewSessionAdmissionAndRemainObservable()
        {
            var interrupted = new List<BuildJob>();
            for (int i = 0; i < 64; i++)
            {
                var job = new BuildJob("interrupted-" + i, BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe");
                BuildJobStore.AddBuildJob(job);
                Assert.IsNull(job.ReportPersistenceError);
                interrupted.Add(job);
            }
            ForgetStaticJobs();
            var current = new BuildJob("current-session", BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe");
            BuildJobStore.AddBuildJob(current);
            Assert.IsNull(current.ReportPersistenceError, "Prior-session observations cannot consume live marker capacity forever.");
            var history = Success(Send("report_history"));
            Assert.AreEqual(64, history["active_observations"].Count(row => (string)row["state"] == "interrupted"));
            Assert.AreEqual("pending", (string)history["active_observations"].Single(row => (string)row["job_id"] == current.JobId)["state"]);
            Assert.AreEqual(1, Directory.GetFiles(ownedRoot, "*.active.json").Length);
            Assert.IsFalse(history["reports"].Any(), "Retired markers are not completed native reports.");
            foreach (var job in interrupted)
                Assert.IsNull(BuildJobStore.GetBuildJob(job.JobId), "Recovery must not restart an interrupted build.");
        }

        [Test]
        public void RecoveryRemovesOnlyAbandonedOwnedArtifactsAndIgnoresForeignInventory()
        {
            Directory.CreateDirectory(ownedRoot);
            string currentSession = (string)Field("sessionId").GetValue(null);
            string oldSession = Guid.NewGuid().ToString("N");
            string oldTemporary = Path.Combine(ownedRoot, "write-v2-" + oldSession + "-" + Guid.NewGuid().ToString("N") + ".tmp");
            string liveTemporary = Path.Combine(ownedRoot, "write-v2-" + currentSession + "-" + Guid.NewGuid().ToString("N") + ".tmp");
            string legacyTemporary = Path.Combine(ownedRoot, "write-" + Guid.NewGuid().ToString("N") + ".tmp");
            foreach (string path in new[] { oldTemporary, liveTemporary, legacyTemporary })
                WriteFixtureFile(path, "owned partial write fixture");
            // Unrelated files must neither be deleted nor exhaust report inventory admission.
            for (int i = 0; i < 300; i++)
                WriteFixtureFile(Path.Combine(ownedRoot, "foreign-" + i + ".txt"), "preserve");
            string abandoned = GenerationFixture("abandoned", oldSession);
            string live = GenerationFixture("live-publisher", currentSession);
            string foreign = GenerationFixture("foreign-generation", oldSession);
            WriteFixtureFile(Path.Combine(foreign, "foreign.txt"), "preserve");
            var completed = Complete(BuildJobState.Succeeded);
            Assert.IsNull(completed.ReportPersistenceError);
            Assert.IsFalse(Directory.Exists(abandoned));
            Assert.IsTrue(Directory.Exists(live));
            Assert.IsTrue(Directory.Exists(foreign));
            Assert.IsFalse(File.Exists(oldTemporary));
            Assert.IsTrue(File.Exists(liveTemporary));
            Assert.IsTrue(File.Exists(legacyTemporary), "Legacy temporary names have no session ownership evidence.");
            Assert.AreEqual(300, Directory.GetFiles(ownedRoot, "foreign-*.txt").Length);
            string report = Id(completed);
            string revision = (string)Success(Send("report", "report_id", report))["report_revision"];
            ForgetStaticJobs();
            Complete(BuildJobState.Succeeded);
            Assert.AreEqual(revision, (string)Success(Send("report", "report_id", report))["report_revision"], "Published generations must survive recovery.");
            Assert.IsFalse(Directory.Exists(live), "A subsequent session can reclaim an unpublished owned generation.");
        }

        [Test]
        public void InterruptedArchiveRetentionIsIndependentOfCurrentJobsAndDeduplicatesCrashReplay()
        {
            var old = new BuildJob("archived-observation", BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe");
            BuildJobStore.AddBuildJob(old);
            string markerPath = Directory.GetFiles(ownedRoot, "*.active.json").Single();
            string markerText = File.ReadAllText(markerPath);
            ForgetStaticJobs();
            Complete(BuildJobState.Succeeded);
            Assert.IsFalse(File.Exists(markerPath));
            // Simulate a crash after archive publication but before removal of the source marker.
            WriteFixtureFile(markerPath, markerText);
            var history = Success(Send("report_history"));
            Assert.AreEqual(1, history["active_observations"].Count(row => (string)row["job_id"] == old.JobId));
            ForgetStaticJobs();
            Complete(BuildJobState.Succeeded);
            Assert.IsFalse(File.Exists(markerPath));
            Field("UtcNow").SetValue(null, new Func<DateTime>(() => DateTime.UtcNow.AddDays(31)));
            Complete(BuildJobState.Succeeded);
            Assert.AreEqual(0, Success(Send("report_history"))["active_observations"].Count());
        }

        [Test]
        public void IncrementalRecoveryKeepsOwnerUntilAllShardsAreDeleted()
        {
            Directory.CreateDirectory(ownedRoot);
            string directory = GenerationFixture("many-orphan-shards", Guid.NewGuid().ToString("N"));
            for (int i = 1; i <= 300; i++)
                WriteFixtureFile(Path.Combine(directory, "packed_assets-" + i.ToString("D6") + ".json"), "{\"rows\":[]}");
            var completed = Complete(BuildJobState.Succeeded);
            Assert.IsNull(completed.ReportPersistenceError);
            Assert.IsTrue(Directory.Exists(directory));
            Assert.IsTrue(File.Exists(Path.Combine(directory, "owner.json")));
            Assert.AreEqual(46, Directory.GetFiles(directory).Length, "A recovery pass deletes at most 256 files.");
            Field("UtcNow").SetValue(null, new Func<DateTime>(() => DateTime.UtcNow.AddSeconds(6)));
            Complete(BuildJobState.Succeeded);
            Assert.IsFalse(Directory.Exists(directory));
            Assert.IsFalse(((System.Collections.IEnumerable)Field("PublishingGenerations").GetValue(null)).Cast<object>().Any());
        }

        [Test]
        public void CorruptInterruptedArchiveCannotBlockValidPriorMarkers()
        {
            Directory.CreateDirectory(ownedRoot);
            const string corrupt = "{preserve this corrupt evidence";
            WriteFixtureFile(Path.Combine(ownedRoot, "interrupted.json"), corrupt);
            for (int i = 0; i < 64; i++)
                BuildJobStore.AddBuildJob(new BuildJob("prior-" + i, BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe"));
            ForgetStaticJobs();
            var current = new BuildJob("current-after-corruption", BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe");
            BuildJobStore.AddBuildJob(current);
            Assert.IsNull(current.ReportPersistenceError);
            var history = Success(Send("report_history"));
            Assert.AreEqual(64, history["active_observations"].Count(row => (string)row["state"] == "interrupted"));
            var preserved = Directory.GetFiles(ownedRoot, "interrupted-*.corrupt.json");
            Assert.AreEqual(1, preserved.Length);
            string streamPath = (string)Store.GetMethod("StreamPath", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { preserved[0] });
            using var stream = new FileStream(streamPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            using var reader = new StreamReader(stream);
            Assert.AreEqual(corrupt, reader.ReadToEnd());
            Assert.IsTrue(history["active_observations"].Any(row => (string)row["state"] == "corrupt_observation"));
        }

        [Test]
        public void RecoveryAdvancesPastProtectedRootsGenerationsTemporaryFilesAndMarkers()
        {
            Directory.CreateDirectory(ownedRoot);
            string oldSession = Guid.NewGuid().ToString("N");
            string currentSession = (string)Field("sessionId").GetValue(null);
            var roots = new List<string>();
            for (int i = 0; i < 140; i++)
                roots.Add(GenerationFixture("protected-" + i, currentSession));
            // Select the last enumerated directory, independent of filesystem enumeration order.
            string lastRoot = Directory.EnumerateDirectories(ownedRoot, "br-*.details").Last();
            string reclaim = Directory.EnumerateDirectories(lastRoot).Single();
            string ownerPath = Path.Combine(reclaim, "owner.json");
            var owner = new JObject
            {
                ["report_id"] = Path.GetFileName(lastRoot).Replace(".details", ""),
                ["generation"] = Path.GetFileName(reclaim),
                ["session"] = oldSession,
            };
            Store
                .GetMethod("Atomic", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { ownerPath, System.Text.Encoding.UTF8.GetBytes(owner.ToString()) });
            for (int i = 0; i < 140; i++)
                WriteFixtureFile(Path.Combine(ownedRoot, "br-foreign-" + i + ".active.json"), "preserve");
            var marker = new BuildJob("beyond-foreign-markers", BuildTarget.StandaloneWindows64, "Builds/Owned/Game.exe");
            BuildJobStore.AddBuildJob(marker);
            string validMarker = Directory.GetFiles(ownedRoot, "*.active.json").Single(path => !Path.GetFileName(path).StartsWith("br-foreign-"));
            var oldMarker = JObject.Parse(File.ReadAllText(validMarker));
            oldMarker["session"] = oldSession;
            Store
                .GetMethod("Atomic", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { validMarker, System.Text.Encoding.UTF8.GetBytes(oldMarker.ToString()) });
            for (int i = 0; i < 260; i++)
                WriteFixtureFile(Path.Combine(ownedRoot, "write-v2-" + currentSession + "-" + Guid.NewGuid().ToString("N") + ".tmp"), "live");
            // Invalid reserved-prefix names must also be advanced past and preserved.
            string oldTemporary = Path.Combine(ownedRoot, "write-v2-" + oldSession + "-" + Guid.NewGuid().ToString("N") + ".tmp");
            WriteFixtureFile(oldTemporary, "old");
            string nestedRoot = null;
            for (int i = 0; i < 140; i++)
                nestedRoot = Path.GetDirectoryName(GenerationFixture("protected-generations", currentSession));
            string nestedReclaim = Directory.EnumerateDirectories(nestedRoot).Last();
            var nestedOwner = new JObject
            {
                ["report_id"] = Path.GetFileName(nestedRoot).Replace(".details", ""),
                ["generation"] = Path.GetFileName(nestedReclaim),
                ["session"] = oldSession,
            };
            Store
                .GetMethod("Atomic", BindingFlags.NonPublic | BindingFlags.Static)
                .Invoke(null, new object[] { Path.Combine(nestedReclaim, "owner.json"), System.Text.Encoding.UTF8.GetBytes(nestedOwner.ToString()) });
            for (int pass = 1; pass <= 8; pass++)
            {
                int advance = pass * 6;
                Field("UtcNow").SetValue(null, new Func<DateTime>(() => DateTime.UtcNow.AddSeconds(advance)));
                Store.GetMethod("RecoverArtifacts", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            }
            Assert.IsFalse(Directory.Exists(reclaim), "Retained roots cannot permanently hide a later orphan.");
            Assert.IsFalse(File.Exists(oldTemporary));
            Assert.IsFalse(File.Exists(validMarker));
            Assert.AreEqual(139, roots.Count(Directory.Exists));
            Assert.IsFalse(Directory.Exists(nestedReclaim), "Continuation must also advance inside one report's generation tree.");
            Assert.AreEqual(139, Directory.GetDirectories(nestedRoot).Length);
            Assert.AreEqual(260, Directory.GetFiles(ownedRoot, "write-v2-*.tmp").Length);
            Assert.AreEqual(140, Directory.GetFiles(ownedRoot, "br-foreign-*.active.json").Length);
        }

        private void WriteFixtureFile(string path, string text)
        {
            StringAssert.StartsWith(ownedRoot + Path.DirectorySeparatorChar, path);
            string streamPath = (string)Store.GetMethod("StreamPath", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { path });
            using var stream = new FileStream(streamPath, FileMode.CreateNew, FileAccess.Write, FileShare.None);
            using var writer = new StreamWriter(stream);
            writer.Write(text);
        }

        private string GenerationFixture(string job, string session)
        {
            string id = (string)Store.GetMethod("Identity", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { job });
            string generation = Guid.NewGuid().ToString("N");
            string path = Path.Combine(ownedRoot, id + ".details", generation);
            Directory.CreateDirectory(path);
            WriteFixtureFile(
                Path.Combine(path, "owner.json"),
                new JObject
                {
                    ["report_id"] = id,
                    ["generation"] = generation,
                    ["session"] = session,
                }.ToString()
            );
            WriteFixtureFile(Path.Combine(path, "packed_assets-000000.json"), "{\"rows\":[]}");
            return path;
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

        private (double milliseconds, long allocated) MeasureReportWork(Action work)
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
            Assert.Less(recorder.Count, recorder.Capacity, "Allocation samples must not be truncated.");
            long bytes = 0;
            for (int i = 0; i < recorder.Count; i++)
                bytes += recorder.GetSample(i).Value;
            return (clock.Elapsed.TotalMilliseconds, recorder.Valid && bytes > 0 ? bytes : -1);
        }

        [Test]
        public void DetailRowValidationMatchesLegacyRulesAndMeasuresAllocation()
        {
            var source = Prepared(Complete(BuildJobState.Succeeded));
            string id = Persist(source);
            var record = (JObject)Store.GetMethod("Load", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { id, null, false });
            var owner = new JObject { ["rows"] = new JArray(PackedRows(256)) };
            var rows = (JArray)owner["rows"];
            var validate = (Action<JObject>)Store.GetMethod("Validate", BindingFlags.NonPublic | BindingFlags.Static).CreateDelegate(typeof(Action<JObject>));
            var candidate =
                (Action<JObject, string, JArray>)
                    Store.GetMethod("ValidateRows", BindingFlags.NonPublic | BindingFlags.Static).CreateDelegate(typeof(Action<JObject, string, JArray>));
            // Exact previous validation path, against the same parsed summary and shard rows.
            void Legacy()
            {
                var ordinary = (JObject)record.DeepClone();
                ordinary["schema_version"] = 1;
                ordinary.Remove("detail_index");
                ordinary.Remove("report_revision");
                ordinary["packed_assets"] = rows;
                validate(ordinary);
            }
            void Current() => candidate(record, "packed_assets", rows);
            Legacy();
            Current();
            for (int repetition = 0; repetition < 3; repetition++)
            {
                (double milliseconds, long allocated) before,
                    after;
                if (repetition % 2 == 0)
                {
                    before = MeasureReportWork(Legacy);
                    after = MeasureReportWork(Current);
                }
                else
                {
                    after = MeasureReportWork(Current);
                    before = MeasureReportWork(Legacy);
                }
                TestContext.WriteLine(
                    $"BETA06_ROWS repetition={repetition} rows=256 legacy_ms={before.milliseconds:F3} current_ms={after.milliseconds:F3} legacy_alloc={before.allocated} current_alloc={after.allocated}"
                );
            }
            Assert.AreSame(owner.Property("rows"), rows.Parent);
            Assert.AreEqual(256, rows.Count);
        }

        [TestCase("sequential")]
        [TestCase("random")]
        [TestCase("repeated")]
        public void DetailPagesRetainOrderedDetachedRowsAndReadCoverage(string mode)
        {
            var source = Prepared(Complete(BuildJobState.Succeeded));
            source["packed_assets_complete"] = true;
            string id = (string)
                Store
                    .GetMethod("WriteSnapshot", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(null, new object[] { source, Enumerable.Empty<JObject>(), PackedRows(10000) });
            int[] pages =
                mode == "sequential" ? Enumerable.Range(1, 12).ToArray()
                : mode == "random" ? new[] { 91, 3, 79, 10, 40, 100, 1, 36, 85, 62, 25, 7 }
                : Enumerable.Repeat(51, 12).ToArray();
            var reads = new List<string>();
            Field("ReadFileForTests").SetValue(null, (Action<string>)(path => reads.Add(Path.GetFileName(path))));
            // Warm the same page order, then measure complete public responses.
            foreach (int page in pages)
                Success(Send("report", "report_id", id, "report_section", "packed_assets", "page_size", 100, "page_number", page));
            reads.Clear();
            double milliseconds = 0;
            long allocated = 0;
            foreach (int page in pages)
            {
                var measured = MeasureReportWork(() =>
                {
                    var data = Success(Send("report", "report_id", id, "report_section", "packed_assets", "page_size", 100, "page_number", page));
                    Assert.AreEqual(10000, data.Value<int>("total_entries"));
                    var entries = (JArray)data["entries"];
                    Assert.AreEqual(100, entries.Count);
                    for (int i = 0; i < 100; i++)
                        Assert.AreEqual("Assets/Owned/Entry" + ((page - 1) * 100 + i) + ".asset", (string)entries[i]["source_asset_path"]);
                    entries[0]["source_asset_path"] = "Assets/CallerEdited.asset";
                });
                milliseconds += measured.milliseconds;
                allocated = allocated >= 0 && measured.allocated >= 0 ? allocated + measured.allocated : -1;
            }
            int shards = pages.Sum(page => ((page * 100 - 1) / 256) - (((page - 1) * 100) / 256) + 1);
            Assert.AreEqual(shards, reads.Count(name => name.StartsWith("packed_assets-", StringComparison.Ordinal) && name != "packed_assets-index.json"));
            Assert.AreEqual(pages.Length, reads.Count(name => name == "packed_assets-index.json"));
            TestContext.WriteLine(
                $"BETA06_PAGES mode={mode} pages={pages.Length} rows=10000 ms={milliseconds:F3} alloc={allocated} reads={reads.Count} cache_bytes=0"
            );
        }

        [TestCase("steps", "unknown")]
        [TestCase("steps", "path")]
        [TestCase("steps", "negative")]
        [TestCase("steps", "infinite")]
        [TestCase("steps", "scalar")]
        [TestCase("packed_assets", "unknown")]
        [TestCase("packed_assets", "path")]
        [TestCase("packed_assets", "negative")]
        [TestCase("packed_assets", "scalar")]
        public void DetailRowsRejectInvalidDataUnderBothValidationPaths(string section, string defect)
        {
            JObject row =
                section == "steps"
                    ? new JObject
                    {
                        ["name"] = "Compile",
                        ["depth"] = 0,
                        ["duration_seconds"] = 1.0,
                    }
                    : new JObject { ["source_asset_path"] = "Assets/Owned.asset", ["packed_size_bytes"] = 20 };
            if (defect == "unknown")
                row["unexpected"] = "value";
            if (defect == "path")
                row[section == "steps" ? "name" : "source_asset_path"] = "C:/private/file";
            if (defect == "negative")
                row[section == "steps" ? "duration_seconds" : "packed_size_bytes"] = -1;
            if (defect == "infinite")
                row["message_count"] = double.PositiveInfinity;
            var rows = new JArray(defect == "scalar" ? (JToken)new JValue("bad") : row);
            var source = Prepared(Complete(BuildJobState.Succeeded));
            var validate = Store.GetMethod("Validate", BindingFlags.NonPublic | BindingFlags.Static);
            var validateRows = Store.GetMethod("ValidateRows", BindingFlags.NonPublic | BindingFlags.Static);
            var legacy = (JObject)source.DeepClone();
            legacy[section] = rows;
            Assert.Throws<TargetInvocationException>(() => validate.Invoke(null, new object[] { legacy }));
            Assert.Throws<TargetInvocationException>(() => validateRows.Invoke(null, new object[] { source, section, rows }));
        }

        [TestCase("valid")]
        [TestCase("invalid_utf8")]
        [TestCase("duplicate")]
        [TestCase("trailing")]
        [TestCase("wrong_hash")]
        public void VerifiedDetailBytesPreserveStrictDecodingAndIntegrity(string mode)
        {
            Directory.CreateDirectory(ownedRoot);
            byte[] bytes = new System.Text.UTF8Encoding(false, true).GetBytes("{\"text\":\"한글 🏰\"}");
            if (mode == "invalid_utf8")
                bytes = new byte[] { 0xff };
            if (mode == "duplicate")
                bytes = System.Text.Encoding.UTF8.GetBytes("{\"text\":1,\"text\":2}");
            if (mode == "trailing")
                bytes = System.Text.Encoding.UTF8.GetBytes("{} {}");
            string path = Path.Combine(ownedRoot, "verified-bytes.json");
            File.WriteAllBytes(path, bytes);
            string hash;
            using (var sha = System.Security.Cryptography.SHA256.Create())
                hash = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            if (mode == "wrong_hash")
                hash = new string('0', 64);
            var read = Store.GetMethod("ReadVerified", BindingFlags.NonPublic | BindingFlags.Static);
            if (mode == "valid")
            {
                var parsed = (JObject)read.Invoke(null, new object[] { path, hash });
                Assert.AreEqual("한글 🏰", (string)parsed["text"]);
                parsed["text"] = "caller-edit";
                Assert.AreEqual("한글 🏰", (string)((JObject)read.Invoke(null, new object[] { path, hash }))["text"]);
            }
            else
                Assert.Throws<TargetInvocationException>(() => read.Invoke(null, new object[] { path, hash }));
        }

        [Test, Explicit("Controller-owned large workload cohort only.")]
        public void LargeReportCohortReadsCompleteOrderedPages()
        {
            var job = Complete(BuildJobState.Succeeded);
            var record = Prepared(job);
            record["packed_assets_complete"] = true;
            string id = (string)
                Store
                    .GetMethod("WriteSnapshot", BindingFlags.NonPublic | BindingFlags.Static)
                    .Invoke(null, new object[] { record, Enumerable.Empty<JObject>(), PackedRows(10000) });
            for (int i = 0; i < 63; i++)
                Complete(BuildJobState.Succeeded);
            MCPForUnityTests.WorkloadProbe.Run(
                "report_pages",
                "indexed",
                10000,
                () =>
                {
                    Assert.AreEqual(64, Success(Send("report_history", "page_size", 100)).Value<int>("total_reports"));
                    var rows = new System.Text.StringBuilder();
                    for (int page = 1; page <= 100; page++)
                    {
                        var data = Success(Send("report", "report_id", id, "report_section", "packed_assets", "page_size", 100, "page_number", page));
                        Assert.IsTrue(data.Value<bool>("details_complete"));
                        Assert.AreEqual(10000, data.Value<int>("total_entries"));
                        var entries = (JArray)data["entries"];
                        Assert.AreEqual(100, entries.Count);
                        for (int i = 0; i < entries.Count; i++)
                        {
                            string expected = "Assets/Owned/Entry" + ((page - 1) * 100 + i) + ".asset";
                            Assert.AreEqual(expected, entries[i].Value<string>("source_asset_path"));
                            rows.Append(entries[i].ToString(Newtonsoft.Json.Formatting.None)).Append('\n');
                        }
                    }
                    return rows.ToString();
                }
            );
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
