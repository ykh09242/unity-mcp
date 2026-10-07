using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;

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
        private object _originalTestService;
        private static FieldInfo TestServiceField => typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate);

        private static Dictionary<string, TestJob> Jobs => (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", StaticPrivate).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            _originalJobs = new Dictionary<string, TestJob>(Jobs);
            _originalCurrent = TestJobManager.CurrentJobId;
            _originalSessionJobs = SessionState.GetString("MCPForUnity.TestJobsV1", string.Empty);
            _originalSessionCurrent = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", string.Empty);
            _originalPersistTime = (long)typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate).GetValue(null);
            _originalTestService = TestServiceField.GetValue(null);
            Jobs.Clear();
            SetCurrent(null);
        }

        [TearDown]
        public void TearDown()
        {
            Jobs.Clear();
            foreach (var pair in _originalJobs)
                Jobs.Add(pair.Key, pair.Value);
            SetCurrent(_originalCurrent);
            typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate).SetValue(null, _originalPersistTime);
            SessionState.SetString("MCPForUnity.TestJobsV1", _originalSessionJobs);
            SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", _originalSessionCurrent);
            TestServiceField.SetValue(null, _originalTestService);
        }

        [Test]
        public void SynchronousStartupFailure_FailsAndPersistsReservedJob()
        {
            TestServiceField.SetValue(null, new ThrowingTestService());

            var error = Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));

            Assert.AreEqual("synchronous-startup-failure", error.Message);
            Assert.IsFalse(TestJobManager.HasRunningJob, "A rejected startup must release its running reservation.");
            var job = Jobs.Values.Single();
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.AreEqual(error.Message, job.Error);
            Assert.IsNotNull(job.FinishedUnixMs);
            var persisted = JObject.Parse(SessionState.GetString("MCPForUnity.TestJobsV1", ""));
            Assert.AreEqual(JTokenType.Null, persisted["current_job_id"].Type);
            Assert.AreEqual("failed", (string)persisted["jobs"][0]["status"]);
        }

        [Test]
        public void SynchronousStartupFailure_DoesNotBlockNextRequest()
        {
            var service = new ThrowingTestService();
            TestServiceField.SetValue(null, service);
            Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));

            var error = Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));

            Assert.AreEqual("synchronous-startup-failure", error.Message);
            Assert.AreEqual(2, service.Calls, "A retry must reach the service instead of the already-running guard.");
            Assert.AreEqual(2, Jobs.Count);
            Assert.IsTrue(Jobs.Values.All(job => job.Status == TestJobStatus.Failed));
        }

        private sealed class ThrowingTestService : ITestRunnerService
        {
            public int Calls { get; private set; }

            public Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions filterOptions = null)
            {
                Calls++;
                throw new InvalidOperationException("synchronous-startup-failure");
            }

            public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode) => throw new NotSupportedException();
        }

        [TestCase(false, false, -1)]
        [TestCase(false, true, 1)]
        [TestCase(true, false, 2)]
        public void FailedRun_PreservesFinalSummaryAndRequestedResults(bool includeDetails, bool includeFailed, int expectedResults)
        {
            var job = NewJob("failed-run", 1);
            Jobs.Add(job.JobId, job);
            SetCurrent(job.JobId);
            var result = new TestRunResult(
                new TestRunSummary(2, 1, 1, 0, 1.5, "Failed"),
                new[]
                {
                    new TestRunTestResult("Pass", "Suite.Pass", "Passed", 0.5, null, null, null),
                    new TestRunTestResult("Fail", "Suite.Fail", "Failed", 1, "assertion failed", "stack", "output"),
                }
            );

            TestJobManager.FinalizeCurrentJobFromRunFinished(result);
            var payload = JObject.FromObject(TestJobManager.ToSerializable(job, includeDetails, includeFailed));

            Assert.AreEqual("failed", (string)payload["status"]);
            Assert.AreEqual(JTokenType.Object, payload["result"].Type, "A failed assertion still produces a final run result.");
            Assert.AreEqual(1, (int?)payload["result"]?["summary"]?["failed"]);
            if (expectedResults < 0)
                Assert.AreEqual(JTokenType.Null, payload["result"]["results"].Type);
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
        public void Diagnostics_LongLinuxVulkanTest_IsSuspectedWithoutFailingTheJob()
        {
            // Given a long single test with no recent progress callback.
            const long now = 100000;
            var job = NewJob("long-linux-test", now - 65000);
            job.CurrentTestFullName = "Suite.LongTest";
            job.CurrentTestStartedUnixMs = now - 65000;
            // When the environment diagnosis is built.
            var data = JObject.FromObject(TestJobManager.BuildDiagnostics(job, now, "6000.0.69f1", "LinuxEditor", "Vulkan", true, false, false));
            // Then it stays running, with an explicitly conditional reproduction suggestion.
            Assert.AreEqual(TestJobStatus.Running, job.Status);
            Assert.IsNull(job.Result);
            Assert.IsTrue((bool)data["stall_suspected"]);
            Assert.AreEqual(65000, (long)data["last_progress_age_ms"]);
            CollectionAssert.Contains(data["possible_causes"].ToObject<string[]>(), "linux_vulkan_backend_candidate");
            StringAssert.Contains("separate reproduction session", data["recommended_actions"].ToString());
            StringAssert.Contains("not a confirmed cause", data["recommended_actions"].ToString());
        }

        [Test]
        public void Diagnostics_InitializationFailed_PreservesExistingFailureAndEffectiveTimeout()
        {
            // Given an initialization failure already finalized by the existing timeout path.
            var job = NewJob("init-failed", 100000);
            job.StartedUnixMs = 50000;
            job.Status = TestJobStatus.Failed;
            job.Error = "Test job failed to initialize (tests did not start within timeout)";
            job.InitTimeoutMs = 120000;
            // When diagnostics are requested.
            var data = JObject.FromObject(TestJobManager.BuildDiagnostics(job, 100000, "2021.3.45f2", "WindowsEditor", "Direct3D11", false, false, false));
            // Then they describe the existing failure without replacing its error/result.
            Assert.IsTrue((bool)data["initialization_failed"]);
            Assert.IsFalse((bool)data["stall_suspected"]);
            Assert.AreEqual(120000, (long)data["initialization_timeout_ms"]);
            Assert.AreEqual(50000, (long)data["last_progress_age_ms"], "Recording a timeout must not fabricate initialization progress.");
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.AreEqual("Test job failed to initialize (tests did not start within timeout)", job.Error);
            Assert.IsNull(job.Result);
        }

        [Test]
        public void Diagnostics_HealthyLinuxVulkanJob_DoesNotSuggestABackendFault()
        {
            // Given a normal recently progressing Linux/Vulkan job.
            var job = NewJob("healthy", 100000);
            // When diagnostics are requested before the no-progress threshold.
            var data = JObject.FromObject(TestJobManager.BuildDiagnostics(job, 100100, "6000.0.69f1", "LinuxEditor", "Vulkan", true, false, false));
            // Then platform choice alone is not evidence of a fault.
            Assert.IsFalse((bool)data["stall_suspected"]);
            Assert.AreEqual(0, ((JArray)data["possible_causes"]).Count);
            Assert.AreEqual(0, ((JArray)data["recommended_actions"]).Count);
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

        private static TestJob NewJob(string id, long updated) =>
            new TestJob
            {
                JobId = id,
                Status = TestJobStatus.Running,
                Mode = "EditMode",
                StartedUnixMs = updated,
                LastUpdateUnixMs = updated,
                FailuresSoFar = new List<TestJobFailure>(),
            };

        private static void SetCurrent(string id) => typeof(TestJobManager).GetField("_currentJobId", StaticPrivate).SetValue(null, id);

        private static void Persist() => typeof(TestJobManager).GetMethod("PersistToSessionState", StaticPrivate).Invoke(null, new object[] { true });
    }
}
