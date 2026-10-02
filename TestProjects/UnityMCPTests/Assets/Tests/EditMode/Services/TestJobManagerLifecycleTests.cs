using System;
using System.Collections.Generic;
using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Services
{
    public class TestJobManagerLifecycleTests
    {
        private const BindingFlags StaticPrivate = BindingFlags.NonPublic | BindingFlags.Static;
        private Dictionary<string, TestJob> _originalJobs;
        private string _originalCurrent;
        private string _originalSessionJobs;
        private string _originalSessionCurrent;
        private long _originalPersistTime;

        private static Dictionary<string, TestJob> Jobs =>
            (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", StaticPrivate).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            _originalJobs = new Dictionary<string, TestJob>(Jobs);
            _originalCurrent = TestJobManager.CurrentJobId;
            _originalSessionJobs = SessionState.GetString("MCPForUnity.TestJobsV1", string.Empty);
            _originalSessionCurrent = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", string.Empty);
            _originalPersistTime = (long)typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate).GetValue(null);
            Jobs.Clear();
            SetCurrent(null);
        }

        [TearDown]
        public void TearDown()
        {
            Jobs.Clear();
            foreach (var pair in _originalJobs) Jobs.Add(pair.Key, pair.Value);
            SetCurrent(_originalCurrent);
            typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate).SetValue(null, _originalPersistTime);
            SessionState.SetString("MCPForUnity.TestJobsV1", _originalSessionJobs);
            SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", _originalSessionCurrent);
        }

        [TestCase(false, false, -1)]
        [TestCase(false, true, 1)]
        [TestCase(true, false, 2)]
        public void FailedRun_PreservesFinalSummaryAndRequestedResults(bool includeDetails, bool includeFailed, int expectedResults)
        {
            var job = NewJob("failed-run", 1);
            Jobs.Add(job.JobId, job);
            SetCurrent(job.JobId);
            var result = new TestRunResult(new TestRunSummary(2, 1, 1, 0, 1.5, "Failed"), new[]
            {
                new TestRunTestResult("Pass", "Suite.Pass", "Passed", 0.5, null, null, null),
                new TestRunTestResult("Fail", "Suite.Fail", "Failed", 1, "assertion failed", "stack", "output")
            });

            TestJobManager.FinalizeCurrentJobFromRunFinished(result);
            var payload = JObject.FromObject(TestJobManager.ToSerializable(job, includeDetails, includeFailed));

            Assert.AreEqual("failed", (string)payload["status"]);
            Assert.AreEqual(JTokenType.Object, payload["result"].Type, "A failed assertion still produces a final run result.");
            Assert.AreEqual(1, (int?)payload["result"]?["summary"]?["failed"]);
            if (expectedResults < 0) Assert.AreEqual(JTokenType.Null, payload["result"]["results"].Type);
            else
            {
                Assert.AreEqual(expectedResults, ((JArray)payload["result"]["results"]).Count);
                Assert.AreEqual("assertion failed", (string)payload["result"]["results"][expectedResults - 1]["message"]);
            }
        }

        [Test]
        public void FailedStartup_WithoutRunResult_KeepsNullResult()
        {
            var job = NewJob("startup-failure", 1);
            job.Status = TestJobStatus.Failed;
            job.Error = "startup failed";
            var payload = JObject.FromObject(TestJobManager.ToSerializable(job, true, true));
            Assert.AreEqual(JTokenType.Null, payload["result"].Type);
            Assert.AreEqual("startup failed", (string)payload["error"]);
        }

        [Test]
        public void CompletedHistory_IsBoundedAndRetainsNewestResults()
        {
            for (int i = 0; i < 25; i++)
            {
                var job = NewJob("history-" + i, i + 1);
                job.Status = TestJobStatus.Succeeded;
                Jobs.Add(job.JobId, job);
            }
            Persist();
            Assert.AreEqual(10, Jobs.Count, "Terminal results must not accumulate until domain reload.");
            Assert.IsFalse(Jobs.ContainsKey("history-14"));
            Assert.IsTrue(Jobs.ContainsKey("history-15"));
            Assert.IsTrue(Jobs.ContainsKey("history-24"));
        }

        [Test]
        public void HistoryPruning_PreservesOldActiveJobAcrossReload()
        {
            var active = NewJob("active", DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            Jobs.Add(active.JobId, active);
            SetCurrent(active.JobId);
            for (int i = 0; i < 20; i++)
            {
                var job = NewJob("terminal-" + i, active.LastUpdateUnixMs + i + 1);
                job.Status = TestJobStatus.Failed;
                Jobs.Add(job.JobId, job);
            }
            Persist();
            Assert.AreEqual(10, Jobs.Count);
            Assert.AreSame(active, Jobs[active.JobId]);
            Jobs.Clear();
            typeof(TestJobManager).GetMethod("TryRestoreFromSessionState", StaticPrivate).Invoke(null, null);
            Assert.IsTrue(Jobs.ContainsKey(active.JobId), "The active job must remain in the persisted snapshot.");
            Assert.AreEqual(active.JobId, TestJobManager.CurrentJobId);
        }

        private static TestJob NewJob(string id, long updated) => new TestJob
        {
            JobId = id, Status = TestJobStatus.Running, Mode = "EditMode",
            StartedUnixMs = updated, LastUpdateUnixMs = updated, FailuresSoFar = new List<TestJobFailure>()
        };

        private static void SetCurrent(string id) =>
            typeof(TestJobManager).GetField("_currentJobId", StaticPrivate).SetValue(null, id);

        private static void Persist() =>
            typeof(TestJobManager).GetMethod("PersistToSessionState", StaticPrivate).Invoke(null, new object[] { true });
    }
}
