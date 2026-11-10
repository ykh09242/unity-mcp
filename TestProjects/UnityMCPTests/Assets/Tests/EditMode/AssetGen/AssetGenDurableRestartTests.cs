using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.ExceptionServices;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Security;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using MCPForUnity.Editor.Tools.AssetGen;
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
            {
                SessionState.EraseString(Prefix + id);
                AssetGenSubmissionIntent.Forget(id);
            }
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
            if (phase == "preparing")
                cp.SubmissionIntentHash = null; // Legacy preparing records have no reconstructable session intent.
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
        public void RestartBeforeSubmission_RefusesMissingSessionIntentWithoutProviderCalls()
        {
            var job = Start();
            Restart();
            Pump(job.JobId);
            var restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.AreEqual(AssetGenJobState.Failed, restored.State);
            Assert.AreEqual("submission_intent_unavailable", restored.RecoveryCode);
            Assert.AreEqual("not_submitted", restored.RecoveryState);
            Assert.IsEmpty(http.RecordedRequests);
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

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void FailedCancellationCheckpointRemainsRetryableWithoutRunner(bool queued, bool published)
        {
            var job = Start();
            if (!queued)
                Pump(job.JobId, 2);
            int calls = http.RecordedRequests.Count;
            string path = Path.Combine(root, job.JobId + ".json");
            string previous = File.ReadAllText(path);
            var journal = (AssetGenJobJournal)typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null);
            InjectCancellationWriteFailure(job, journal, published);
            var parameters = new ToolParams(new JObject { ["job_id"] = job.JobId });
            JObject cancel = JObject.FromObject(AssetGenToolHelpers.Cancel(parameters));
            Assert.IsFalse((bool)cancel["success"]);
            Assert.AreEqual("checkpoint_write_failed", (string)cancel["data"]["recovery_code"]);
            Assert.AreEqual("outcome_unknown", job.RecoveryState);
            Assert.AreEqual(AssetGenJobState.Canceled, job.State, "Local execution must stay canceled even when durability is uncertain.");
            JObject status = JObject.FromObject(AssetGenToolHelpers.Status(parameters, "Model", 1));
            Assert.IsFalse((bool)status["success"]);
            Assert.AreEqual("checkpoint_write_failed", (string)status["data"]["recovery_code"]);
            Assert.IsTrue((bool)status["data"]["retryable"]);
            Pump(job.JobId);
            Assert.AreEqual(calls, http.RecordedRequests.Count);
            if (!published)
                Assert.AreEqual(previous, File.ReadAllText(path));
            else
                Assert.AreEqual((int)AssetGenJobState.Canceled, (int)JObject.Parse(File.ReadAllText(path))["job"]["State"]);
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            journal.AfterPublicationForTests = null;
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId), "Retry must not require a live runner.");
            Assert.AreSame(journal, typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null), "Retry must retain its journal lease.");
            Assert.AreEqual("terminal", job.RecoveryState);
            Assert.AreEqual("canceled", job.RecoveryCode);
            Restart();
            Pump(job.JobId);
            Assert.AreEqual(AssetGenJobState.Canceled, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.AreEqual("canceled", AssetGenJobManager.GetJob(job.JobId).RecoveryCode);
            Assert.AreEqual(calls, http.RecordedRequests.Count);
        }

        private static void InjectCancellationWriteFailure(AssetGenJob job, AssetGenJobJournal journal, bool published)
        {
            if (published)
                journal.AfterPublicationForTests = name =>
                {
                    if (name == job.JobId + ".json")
                        throw new IOException("Injected lost publication acknowledgement.");
                };
            else
                AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected cancellation checkpoint failure.");
        }

        [TestCase(1, false)]
        [TestCase(3, false)]
        [TestCase(3, true)]
        public void CancellationRetryPreservesPublishedCandidateAcrossFailedReplacements(int retries, bool replaceEvidence)
        {
            if (Path.DirectorySeparatorChar != '\\')
                Assert.Ignore("This fixture injects replacement failure using Windows file-sharing semantics.");
            var job = Start();
            var journal = (AssetGenJobJournal)typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null);
            InjectCancellationWriteFailure(job, journal, true);
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            journal.AfterPublicationForTests = null;
            string path = Path.Combine(root, job.JobId + ".json");
            string candidate = File.ReadAllText(path);
            using (var held = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
            {
                for (int i = 0; i < retries; i++)
                {
                    now++;
                    utc = utc.AddSeconds(1);
                    Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
                }
                Assert.AreEqual(candidate, File.ReadAllText(path));
            }
            if (replaceEvidence)
            {
                var envelope = JObject.Parse(candidate);
                envelope["job"]["AssetPath"] = "Assets/Retained/replaced-after-retries.glb";
                string replaced = envelope.ToString(Formatting.None);
                File.WriteAllText(path, replaced);
                Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
                Assert.AreEqual(replaced, File.ReadAllText(path));
            }
            else
            {
                now++;
                utc = utc.AddSeconds(1);
                Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
                Restart();
                Pump(job.JobId);
                Assert.AreEqual(AssetGenJobState.Canceled, AssetGenJobManager.GetJob(job.JobId).State);
            }
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationRetryRejectsSameOwnerReplacement(bool published)
        {
            var job = Start();
            var journal = (AssetGenJobJournal)typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null);
            InjectCancellationWriteFailure(job, journal, published);
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            journal.AfterPublicationForTests = null;
            string path = Path.Combine(root, job.JobId + ".json");
            var envelope = JObject.Parse(File.ReadAllText(path));
            envelope["job"]["AssetPath"] = "Assets/Retained/changed.glb";
            string replaced = envelope.ToString(Formatting.None);
            File.WriteAllText(path, replaced);
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
            Assert.AreEqual(replaced, File.ReadAllText(path));
            Assert.AreSame(journal, typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void CancellationRetryDoesNotAdoptUnrelatedRecordReplacement()
        {
            var job = Start();
            var other = Start();
            AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected cancellation checkpoint failure.");
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            string path = Path.Combine(root, other.JobId + ".json");
            var envelope = JObject.Parse(File.ReadAllText(path));
            envelope["job"]["AssetPath"] = "Assets/Retained/unrelated.glb";
            string replaced = envelope.ToString(Formatting.None);
            File.WriteAllText(path, replaced);
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            Assert.IsFalse(AssetGenJobManager.Cancel(other.JobId), "Retrying one job must not adopt another job's changed preimage.");
            Assert.AreEqual(replaced, File.ReadAllText(path));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationRetryRequiresRetainedPublicationPreimage(bool missing)
        {
            var job = Start();
            var journal = (AssetGenJobJournal)typeof(AssetGenJobManager).GetProperty("Journal", Flags).GetValue(null);
            InjectCancellationWriteFailure(job, journal, true);
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            journal.AfterPublicationForTests = null;
            string path = Path.Combine(root, job.JobId + ".json");
            string candidate = File.ReadAllText(path);
            string backup = Directory.GetFiles(root, ".journal-*.tmp.bak").Single();
            if (missing)
                File.Delete(backup);
            else
                File.WriteAllText(backup, "changed preimage");
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            Assert.AreEqual(candidate, File.ReadAllText(path));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void QueuedCancellationWithoutRunnerRequiresDurableAcknowledgement()
        {
            var job = Start();
            var runners = (System.Collections.IDictionary)typeof(AssetGenJobManager).GetField("Runners", Flags).GetValue(null);
            typeof(AssetGenJobManager).GetMethod("Finalize", Flags).Invoke(null, new[] { runners[job.JobId] });
            AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected queued cancellation failure.");
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
            Restart();
            Assert.AreEqual(AssetGenJobState.Canceled, AssetGenJobManager.GetJob(job.JobId).State);
            Assert.IsEmpty(http.RecordedRequests);
        }

        [Test]
        public void CancellationRetryDoesNotOverwriteForeignJournalEvidence()
        {
            var job = Start();
            AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected cancellation failure.");
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            string path = Path.Combine(root, job.JobId + ".json");
            var envelope = JObject.Parse(File.ReadAllText(path));
            envelope["owner"] = new string('f', 64);
            string foreign = envelope.ToString(Formatting.None);
            File.WriteAllText(path, foreign);
            Assert.IsFalse(AssetGenJobManager.Cancel(job.JobId));
            Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
            Assert.AreEqual(foreign, File.ReadAllText(path));
            Assert.IsEmpty(http.RecordedRequests);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CancellationDuringImportRetainsFinalReceiptUntilCheckpointRetry(bool firstWriteFails)
        {
            int imports = 0;
            string savedPath = null;
            string folder = "Assets/TcrGeneration_" + Path.GetFileName(root).Substring("TcrGeneration_".Length);
            http.Handler = request => new HttpResult
            {
                IsSuccess = true,
                Status = 200,
                Body = request.DownloadProvider != null ? ModelImportPipelineTests.ContainerOnlyGlb() : null,
                Text =
                    request.DownloadProvider != null ? null
                    : request.Method == "POST" ? "{\"code\":0,\"data\":{\"task_id\":\"owned-import-task\"}}"
                    : "{\"code\":0,\"data\":{\"status\":\"success\",\"output\":{\"model\":\"https://tripo-data.rg1.data.tripo3d.com/model.glb\"}}}",
            };
            AssetGenJobManager.ImportOverrideForTests = (job, path) =>
            {
                imports++;
                savedPath = path;
                if (firstWriteFails)
                    AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected initial cancellation failure.");
                Assert.AreEqual(!firstWriteFails, AssetGenJobManager.Cancel(job.JobId));
                AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected final receipt failure.");
                // An importer may return a replacement DTO after cancellation was requested.
                return new AssetGenJob
                {
                    JobId = job.JobId,
                    Kind = job.Kind,
                    Provider = job.Provider,
                    State = AssetGenJobState.Done,
                    AssetPath = path,
                    AssetGuid = new string('a', 32),
                    ModelImportStage = "registered",
                };
            };
            try
            {
                var started = Start();
                Pump(started.JobId, 16);
                var job = AssetGenJobManager.GetJob(started.JobId);
                Assert.AreEqual(1, imports);
                Assert.AreEqual(AssetGenJobState.Canceled, job.State);
                Assert.AreEqual("checkpoint_write_failed", job.RecoveryCode);
                Assert.AreEqual("outcome_unknown", job.RecoveryState);
                Assert.AreEqual(savedPath, job.AssetPath);
                Assert.AreEqual(new string('a', 32), job.AssetGuid);
                Assert.AreEqual("registered", job.ModelImportStage);
                int calls = http.RecordedRequests.Count;
                AssetGenJobManager.BeforeCheckpointWriteForTests = null;
                Assert.IsTrue(AssetGenJobManager.Cancel(job.JobId));
                Restart();
                Pump(job.JobId);
                var restored = AssetGenJobManager.GetJob(job.JobId);
                Assert.AreEqual(AssetGenJobState.Canceled, restored.State);
                Assert.AreEqual(savedPath, restored.AssetPath);
                Assert.AreEqual(new string('a', 32), restored.AssetGuid);
                Assert.AreEqual("registered", restored.ModelImportStage);
                Assert.AreEqual(calls, http.RecordedRequests.Count);
                Assert.AreEqual(1, imports);
            }
            finally
            {
                AssetGenJobManager.BeforeCheckpointWriteForTests = null;
                AssetDatabase.DeleteAsset(folder);
            }
        }

        [Test]
        public void PendingCancellationKeepsItsAdmissionSlotAndSurvivesHistoryEviction()
        {
            var pending = Start();
            AssetGenJobManager.BeforeCheckpointWriteForTests = _ => throw new IOException("Injected cancellation checkpoint failure.");
            Assert.IsFalse(AssetGenJobManager.Cancel(pending.JobId));
            AssetGenJobManager.BeforeCheckpointWriteForTests = null;
            for (int i = 0; i <= AssetGenJobManager.MaxTerminalJobs; i++)
                Assert.IsTrue(AssetGenJobManager.Cancel(Start().JobId));
            Assert.IsNotNull(AssetGenJobManager.GetJob(pending.JobId));
            for (int i = 1; i < AssetGenJobCheckpoint.MaxActive; i++)
                Start();
            Assert.Throws<InvalidOperationException>(() => Start());
            Assert.IsTrue(AssetGenJobManager.Cancel(pending.JobId));
            Assert.DoesNotThrow(() => Start());
            Assert.IsEmpty(http.RecordedRequests);
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
