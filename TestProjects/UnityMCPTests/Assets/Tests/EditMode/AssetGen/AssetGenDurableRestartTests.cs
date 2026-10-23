using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.ExceptionServices;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenDurableRestartTests
    {
        private const string Prefix = "MCPForUnity.AssetGen.Job.";
        private const string Index = "MCPForUnity.AssetGen.JobIndex";
        private const BindingFlags Flags = BindingFlags.Static | BindingFlags.NonPublic;
        private string root;
        private string owner;
        private string instance;
        private double now;
        private DateTimeOffset utc;
        private FakeHttpTransport http;

        private sealed class MemoryKeys : ISecureKeyStore
        {
            public bool TryGet(string provider, out string key)
            {
                key = "tcr10-synthetic-key";
                return true;
            }

            public bool Has(string provider) => true;

            public void Set(string provider, string key) => throw new InvalidOperationException();

            public void Delete(string provider) => throw new InvalidOperationException();
        }

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            root = Path.Combine(Path.GetTempPath(), "TcrGeneration_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            owner = new string('a', 64);
            instance = Guid.NewGuid().ToString("N");
            now = 1000;
            utc = DateTimeOffset.FromUnixTimeSeconds(1800000000);
            http = new FakeHttpTransport
            {
                Handler = request => new HttpResult
                {
                    IsSuccess = true,
                    Status = 200,
                    Text = (
                        request.Method == "POST"
                            ? new JObject
                            {
                                ["code"] = 0,
                                ["data"] = new JObject { ["task_id"] = "tcr10-owned-task" },
                            }
                            : new JObject
                            {
                                ["code"] = 0,
                                ["data"] = new JObject { ["status"] = "running", ["progress"] = 25 },
                            }
                    ).ToString(Formatting.None),
                },
            };
            Configure();
        }

        private void Configure()
        {
            SecureKeyStore.OverrideForTests(new MemoryKeys());
            AssetGenJobManager.ClockForTests = () => now;
            AssetGenJobManager.TransportOverrideForTests = http;
            AssetGenJobManager.PollIntervalSeconds = 0;
            AssetGenJobManager.TimeoutSeconds = 600;
            AssetGenJobManager.SkipModelVerificationForTests = true;
            AssetGenJobManager.ImportOverrideForTests = (job, path) => throw new AssertionException("No import is authorized by this fixture.");
            // Baseline has only SessionState; the missing seam must not hide the restart regression.
            typeof(AssetGenJobManager)
                .GetMethod("ConfigureDurableStoreForTests", Flags)
                ?.Invoke(null, new object[] { root, owner, instance, (Func<DateTimeOffset>)(() => utc) });
        }

        [TearDown]
        public void TearDown()
        {
            AssetGenJobManager.ResetForTests();
            SecureKeyStore.ResetForTests();
            if (root != null && Path.GetFileName(root).StartsWith("TcrGeneration_", StringComparison.Ordinal))
                Directory.Delete(root, true);
        }

        private AssetGenJob Start() =>
            AssetGenJobManager.StartModelGeneration(
                new ModelGenRequest
                {
                    Provider = "tripo",
                    Mode = "text",
                    Prompt = "tcr10-private-prompt",
                    Format = "glb",
                    Name = "owned",
                    OutputFolder = "Assets/TcrGeneration_" + Path.GetFileName(root).Substring("TcrGeneration_".Length),
                }
            );

        private static void Pump(string id, int count = 4)
        {
            for (int i = 0; i < count; i++)
                AssetGenJobManager.TryAdvanceForTests(id);
        }

        private void Restart(double downtime = 20)
        {
            string[] ids = SessionState.GetString(Index, "").Split(',');
            AssetGenJobManager.ResetTransientForTests();
            foreach (string id in ids)
                SessionState.EraseString(Prefix + id);
            SessionState.EraseString(Index);
            instance = Guid.NewGuid().ToString("N");
            now = 5;
            utc = utc.AddSeconds(downtime);
            Configure();
            AssetGenJobManager.RestoreJobs();
        }

        private static void Persist(AssetGenJob job, AssetGenJobCheckpoint cp)
        {
            try
            {
                typeof(AssetGenJobManager).GetMethod("PersistRequired", Flags).Invoke(null, new object[] { job, cp });
            }
            catch (TargetInvocationException ex)
            {
                ExceptionDispatchInfo.Capture(ex.InnerException).Throw();
            }
        }

        [TestCase("polling")]
        [TestCase("downloading")]
        public void RestartResumesReceiptWithoutAnotherProviderSubmission(string phase)
        {
            var job = Start();
            Pump(job.JobId, 2);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
            var checkpoint = AssetGenJobCheckpoint.Read(AssetGenJobCheckpoint.ParseRecord(SessionState.GetString(Prefix + job.JobId, "")), job.JobId, now);
            checkpoint.Phase = phase;
            Persist(job, checkpoint);
            Restart();
            Pump(job.JobId);
            var restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.IsNotNull(restored, "Confirmed provider work must remain discoverable after SessionState is lost.");
            Assert.AreEqual(AssetGenJobState.Running, restored.State);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
            Assert.IsTrue(http.RecordedRequests.Any(r => r.Method == "GET" && r.Url.EndsWith("/tcr10-owned-task", StringComparison.Ordinal)));
            string disk = string.Join("\n", Directory.GetFiles(root, "*.json").Select(File.ReadAllText));
            StringAssert.DoesNotContain("tcr10-private-prompt", disk);
            StringAssert.DoesNotContain("tcr10-synthetic-key", disk);
        }

        [TestCase("preparing", "reload_before_submission")]
        [TestCase("submitting", "submission_outcome_unknown")]
        [TestCase("writing", "local_write_outcome_unknown")]
        [TestCase("importing", "import_outcome_unknown")]
        public void RestartNeverReplaysUncertainMutation(string phase, string code)
        {
            var job = Start();
            Pump(job.JobId, 2);
            var cp = AssetGenJobCheckpoint.Read(AssetGenJobCheckpoint.ParseRecord(SessionState.GetString(Prefix + job.JobId, "")), job.JobId, now);
            cp.Phase = phase;
            if (phase == "writing" || phase == "importing")
            {
                cp.SavedPath = cp.OutputFolder + "/owned.glb";
                cp.SavedHash = new string('b', 64);
                cp.SavedLength = 10;
            }
            Persist(job, cp);
            int calls = http.RecordedRequests.Count;
            Restart();
            Pump(job.JobId);
            var restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.IsNotNull(restored);
            Assert.AreEqual(AssetGenJobState.Failed, restored.State);
            Assert.AreEqual(code, restored.RecoveryCode);
            Assert.AreEqual(calls, http.RecordedRequests.Count);
        }

        [Test]
        public void DowntimeConsumesOriginalBudgetInsteadOfResettingIt()
        {
            var job = Start();
            Pump(job.JobId, 2);
            Restart(550);
            Pump(job.JobId, 1);
            Assert.IsNotNull(AssetGenJobManager.GetJob(job.JobId));
            Assert.AreEqual(AssetGenJobState.Running, AssetGenJobManager.GetJob(job.JobId).State);
            now += 51;
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual("job_timeout", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
        }

        [Test]
        public void JournalWriteDenialStopsBeforeProviderSubmission()
        {
            string denied = Path.Combine(root, "not-a-directory");
            File.WriteAllText(denied, "owned obstruction");
            typeof(AssetGenJobManager)
                .GetMethod("ConfigureDurableStoreForTests", Flags)
                ?.Invoke(null, new object[] { denied, owner, instance, (Func<DateTimeOffset>)(() => utc) });
            var job = Start();
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, job.State);
            Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void DefaultJournalSupportsTheCurrentLongProjectPath()
        {
            var job = Start();
            var record = AssetGenJobCheckpoint.ParseRecord(SessionState.GetString(Prefix + job.JobId, ""));
            AssetGenJobManager.ResetTransientForTests();
            string uniqueOwner = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            AssetGenJobManager.ConfigureDurableStoreForTests(null, uniqueOwner, instance, () => utc);
            var journal = (AssetGenJobJournal)typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null);
            string owned = (string)typeof(AssetGenJobJournal).GetField("root", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(journal);
            string projectLibrary =
                Path.GetFullPath(Path.Combine(UnityEngine.Application.dataPath, "..", "Library", "MCPForUnity")) + Path.DirectorySeparatorChar;
            Assert.IsTrue(owned.StartsWith(projectLibrary, StringComparison.OrdinalIgnoreCase));
            Assert.IsFalse(Directory.Exists(owned) || File.Exists(owned));
            try
            {
                journal.Save(record, new[] { job.JobId }, now, true);
                Assert.AreEqual(1, journal.Read(now).Count);
            }
            finally
            {
                AssetGenJobManager.ResetTransientForTests();
                if (Directory.Exists(owned))
                    Directory.Delete(owned, true);
            }
        }

        [Test]
        public void ActiveLeasePreventsAnotherInstanceFromResuming()
        {
            var job = Start();
            Pump(job.JobId, 2);
            using var other = new AssetGenJobJournal(root, owner, Guid.NewGuid().ToString("N"), () => utc);
            Assert.Throws<IOException>(() => other.Read(now));
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UnverifiedRecordIsRetainedWithoutResuming(bool foreign)
        {
            var job = Start();
            Pump(job.JobId, 2);
            AssetGenJobManager.ResetTransientForTests();
            string path = Path.Combine(root, job.JobId + ".json");
            string invalid = "{invalid";
            if (foreign)
            {
                var envelope = JObject.Parse(File.ReadAllText(path));
                envelope["owner"] = new string('f', 64);
                invalid = envelope.ToString(Formatting.None);
            }
            File.WriteAllText(path, invalid);
            int calls = http.RecordedRequests.Count;
            Restart();
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual("checkpoint_invalid", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(calls, http.RecordedRequests.Count);
            Assert.AreEqual(invalid, File.ReadAllText(path));
        }

        [Test]
        public void CorruptIndexBlocksNewAdmissionAndRetainsEvidence()
        {
            var job = Start();
            Pump(job.JobId, 2);
            AssetGenJobManager.ResetTransientForTests();
            string path = Path.Combine(root, "index.json");
            File.WriteAllText(path, "{invalid");
            Restart();
            Assert.Throws<IOException>(() => AssetGenJobManager.GetJob(job.JobId));
            Assert.Throws<IOException>(() => Start());
            Assert.AreEqual("{invalid", File.ReadAllText(path));
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
        }

        [Test]
        public void MissingRecordRemainsDiscoverableAsUnavailable()
        {
            var job = Start();
            AssetGenJobManager.ResetTransientForTests();
            File.Delete(Path.Combine(root, job.JobId + ".json"));
            Restart();
            var restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.IsNotNull(restored);
            Assert.AreEqual("checkpoint_invalid", restored.RecoveryCode);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void ClockRollbackDoesNotGrantANewExecutionBudget()
        {
            var job = Start();
            Pump(job.JobId, 2);
            Restart(-1);
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual("checkpoint_clock_uncertain", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(1, http.RecordedRequests.Count(r => r.Method == "POST"));
        }

        [Test]
        public void ProgressOnlyUpdatesDoNotRewriteDurableCheckpoints()
        {
            var job = Start();
            Pump(job.JobId, 2);
            string path = Path.Combine(root, job.JobId + ".json");
            string before = File.ReadAllText(path);
            utc = utc.AddSeconds(1);
            job.Progress = .7f;
            typeof(AssetGenJobManager).GetMethod("Persist", Flags).Invoke(null, new object[] { job });
            Assert.AreEqual(before, File.ReadAllText(path));
            Assert.AreEqual(.7f, (float)JObject.Parse(SessionState.GetString(Prefix + job.JobId, ""))["Progress"]);
        }

        [Test]
        public void CorruptFirstRecordDoesNotHideAnotherConfirmedJob()
        {
            var first = Start();
            var second = Start();
            Pump(first.JobId, 2);
            Pump(second.JobId, 2);
            AssetGenJobManager.ResetTransientForTests();
            File.WriteAllText(Path.Combine(root, first.JobId + ".json"), "{invalid");
            Restart();
            Pump(second.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob(first.JobId).State);
            Assert.AreEqual(AssetGenJobState.Running, AssetGenJobManager.GetJob(second.JobId).State);
            Assert.AreEqual(2, http.RecordedRequests.Count(r => r.Method == "POST"));
        }

        [Test]
        public void DurableRecoveryPreservesUnboundSameProcessHistory()
        {
            var job = Start();
            Pump(job.JobId, 2);
            const string legacyId = "legacy-terminal";
            SessionState.SetString(Prefix + legacyId, JsonConvert.SerializeObject(new AssetGenJob { JobId = legacyId, State = AssetGenJobState.Done }));
            SessionState.SetString(Index, job.JobId + "," + legacyId);
            AssetGenJobManager.ResetTransientForTests();
            Configure();
            AssetGenJobManager.RestoreJobs();
            Assert.IsNotNull(AssetGenJobManager.GetJob(legacyId), "Unbound same-process history must not disappear when durable records also exist.");
            Assert.AreEqual(AssetGenJobState.Done, AssetGenJobManager.GetJob(legacyId).State);
            Assert.AreEqual(AssetGenJobState.Running, AssetGenJobManager.GetJob(job.JobId).State);
        }

        [Test]
        public void TerminalCancellationSurvivesCompleteRestart()
        {
            var job = Start();
            Pump(job.JobId, 2);
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            int calls = http.RecordedRequests.Count;
            Restart();
            Pump(job.JobId);
            Assert.IsNotNull(AssetGenJobManager.GetJob(job.JobId));
            Assert.AreEqual(AssetGenJobState.Canceled, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual(calls, http.RecordedRequests.Count);
        }
    }
}
