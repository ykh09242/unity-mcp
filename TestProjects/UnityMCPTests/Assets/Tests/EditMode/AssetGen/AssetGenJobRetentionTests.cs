using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class AssetGenJobRetentionTests
    {
        private const int TerminalLimit = 100;
        private const string IndexKey = "MCPForUnity.AssetGen.JobIndex";
        private const string JobPrefix = "MCPForUnity.AssetGen.Job.";
        private static readonly BindingFlags PrivateStatic = BindingFlags.Static | BindingFlags.NonPublic;

        [SetUp]
        public void SetUp()
        {
            AssetGenJobManager.ResetForTests();
            // Legacy metadata fixtures remain independent of any real project journal.
            AssetGenJobManager.TransportOverrideForTests = new FakeHttpTransport
            {
                Handler = _ => throw new AssertionException("Unexpected provider request in history fixture."),
            };
        }

        [TearDown]
        public void TearDown() => AssetGenJobManager.ResetForTests();

        private static AssetGenJob Store(string id, AssetGenJobState state)
        {
            var job = new AssetGenJob { JobId = id, State = state };
            Store(job);
            return job;
        }

        private static void Store(AssetGenJob job)
        {
            var jobs = (Dictionary<string, AssetGenJob>)typeof(AssetGenJobManager).GetField("Jobs", PrivateStatic).GetValue(null);
            jobs[job.JobId] = job;
            typeof(AssetGenJobManager).GetMethod("Persist", PrivateStatic).Invoke(null, new object[] { job });
        }

        [TestCase(AssetGenJobState.Done)]
        [TestCase(AssetGenJobState.Failed)]
        [TestCase(AssetGenJobState.Canceled)]
        public void TerminalHistory_EvictsOldestMetadataFromMemoryAndSession(AssetGenJobState state)
        {
            for (int i = 0; i < TerminalLimit + 5; i++)
                Store("history-" + i, state);
            Assert.AreEqual(TerminalLimit, AssetGenJobManager.RecentJobs(int.MaxValue).Count);
            for (int i = 0; i < 5; i++)
            {
                Assert.IsNull(AssetGenJobManager.GetJob("history-" + i));
                Assert.AreEqual(string.Empty, SessionState.GetString(JobPrefix + "history-" + i, string.Empty));
            }
            Assert.AreEqual("history-104", AssetGenJobManager.RecentJobs(1)[0].JobId);
            Assert.AreEqual(TerminalLimit, SessionState.GetString(IndexKey, string.Empty).Split(',').Length);
        }

        [Test]
        public void OldActiveJob_SurvivesEvictionAndRemainsQueryableWhenItFinishes()
        {
            var active = Store("active", AssetGenJobState.Running);
            for (int i = 0; i < TerminalLimit + 5; i++)
                Store("finished-" + i, AssetGenJobState.Done);
            Assert.AreSame(active, AssetGenJobManager.GetJob(active.JobId));
            Assert.AreEqual(TerminalLimit + 1, AssetGenJobManager.RecentJobs(int.MaxValue).Count);
            active.State = AssetGenJobState.Done;
            Store(active);
            Assert.AreSame(active, AssetGenJobManager.GetJob(active.JobId));
            Assert.AreEqual(active.JobId, AssetGenJobManager.RecentJobs(1)[0].JobId);
            Assert.IsNull(AssetGenJobManager.GetJob("finished-5"));
            Assert.AreEqual(TerminalLimit, AssetGenJobManager.RecentJobs(int.MaxValue).Count);
        }

        [Test]
        public void RepeatedProgressAndTerminalWrites_DoNotDuplicateIndexOrHistory()
        {
            var job = Store("repeated", AssetGenJobState.Running);
            for (int i = 0; i < 50; i++)
            {
                job.Progress = i / 50f;
                Store(job);
            }
            job.State = AssetGenJobState.Done;
            for (int i = 0; i < 50; i++)
                Store(job);
            Assert.AreEqual("repeated", SessionState.GetString(IndexKey, string.Empty));
            Assert.AreEqual(1, AssetGenJobManager.RecentJobs(int.MaxValue).Count);
        }

        [TestCase(0)]
        [TestCase(10)]
        public void RestoreLegacyHistory_BoundsMetadataAndRetainsInterruptedOldActiveJob(int excess)
        {
            var ids = new List<string> { "interrupted" };
            for (int i = 0; i < TerminalLimit + excess; i++)
            {
                string id = "restored-" + i;
                ids.Add(id);
                SessionState.SetString(JobPrefix + id, JsonConvert.SerializeObject(new AssetGenJob { JobId = id, State = AssetGenJobState.Done }));
            }
            SessionState.SetString(
                JobPrefix + "interrupted",
                JsonConvert.SerializeObject(new AssetGenJob { JobId = "interrupted", State = AssetGenJobState.Running })
            );
            SessionState.SetString(IndexKey, string.Join(",", ids));
            typeof(AssetGenJobManager).GetMethod("RestoreJobs", PrivateStatic).Invoke(null, null);
            Assert.AreEqual(TerminalLimit, AssetGenJobManager.RecentJobs(int.MaxValue).Count);
            Assert.AreEqual(AssetGenJobState.Failed, AssetGenJobManager.GetJob("interrupted").State);
            StringAssert.Contains("reload", AssetGenJobManager.GetJob("interrupted").Error);
            for (int i = 0; i < excess + 1; i++)
            {
                Assert.IsNull(AssetGenJobManager.GetJob("restored-" + i));
                Assert.AreEqual(string.Empty, SessionState.GetString(JobPrefix + "restored-" + i, string.Empty));
            }
            string index = SessionState.GetString(IndexKey, string.Empty);
            Assert.AreEqual(TerminalLimit, index.Split(',').Length);
            StringAssert.EndsWith(",interrupted", index);
        }

        [TestCase("normalization_variant_failed")]
        [TestCase("archive_selection_limit")]
        public void SafePersistedFailure_RetainsKnownImportAndArchiveEvidence(string code)
        {
            var evidence = new JObject
            {
                ["archive_path"] = "Assets/owned/archive.zip",
                ["requested_path"] = "main/model.obj",
                ["selected_path"] = null,
                ["selected_asset_path"] = null,
                ["extraction_root"] = null,
                ["mode"] = "explicit",
                ["policy"] = "builtin_then_ordinal",
                ["extraction"] = "not_started",
                ["candidates"] = new JArray(),
                ["candidate_count"] = 0,
                ["candidates_complete"] = false,
                ["selection_error_code"] = "archive_selection_limit",
                ["limit"] = "candidate_count",
            };
            var job = new AssetGenJob
            {
                JobId = "owned-projection",
                Kind = "model",
                Provider = "meshy",
                State = AssetGenJobState.Failed,
                AssetPath = "Assets/owned/archive.zip",
                AssetGuid = new string('a', 32),
                ModelImportStage = "registered",
                ModelImportErrorCode = code,
                ArchiveSelection = (JObject)evidence.DeepClone(),
                Error = "PRIVATE_PROVIDER_BODY https://example.invalid/file?signature=PRIVATE_SIGNATURE",
            };
            job.ArchiveSelection["unknown_provider_payload"] = "PRIVATE_EXTENSION";
            Store(job);
            string saved = SessionState.GetString(JobPrefix + job.JobId, "");
            StringAssert.DoesNotContain("PRIVATE_", saved);
            AssetGenJobManager.ResetTransientForTests();
            AssetGenJobManager.RestoreJobs();
            AssetGenJob restored = AssetGenJobManager.GetJob(job.JobId);
            Assert.AreEqual(code, restored.ModelImportErrorCode);
            Assert.AreEqual(job.AssetGuid, restored.AssetGuid);
            Assert.AreEqual(job.AssetPath, restored.AssetPath);
            Assert.AreEqual("registered", restored.ModelImportStage);
            Assert.IsTrue(JToken.DeepEquals(evidence, restored.ArchiveSelection), restored.ArchiveSelection?.ToString());
            StringAssert.DoesNotContain("PRIVATE_", restored.Error);
        }
    }
}
