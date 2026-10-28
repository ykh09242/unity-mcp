using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using NUnit.Framework.Interfaces;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using UnityEngine.TestTools;
using RunState = UnityEditor.TestTools.TestRunner.Api.RunState;
using TestMode = UnityEditor.TestTools.TestRunner.Api.TestMode;
using TestStatus = UnityEditor.TestTools.TestRunner.Api.TestStatus;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// Exercises reload recovery without starting a nested Unity test run. The synthetic
    /// callbacks have the same result tree contract as TestRunnerApi.RunFinished.
    /// </summary>
    public class TestJobManagerReloadTests
    {
        private const BindingFlags PrivateStatic = BindingFlags.NonPublic | BindingFlags.Static;
        private const string JobsKey = "MCPForUnity.TestJobsV1";
        private const string CurrentKey = "MCPForUnity.CurrentTestJobIdV1";
        private const string GuardPrefix = "MCPForUnity.PlayModeOptions.";
        private const string MarkerPath = "Library/MCPPlayModeOptionsBackup.txt";
        private Dictionary<string, TestJob> _jobs;
        private Dictionary<string, TestJob> _originalJobs;
        private string _originalCurrent;
        private string _originalSessionJobs;
        private string _originalSessionCurrent;
        private object _originalService;
        private object _originalLastPersist;
        private Dictionary<FieldInfo, object> _originalStatus;
        private bool _originalOptionsEnabled;
        private EnterPlayModeOptions _originalOptions;
        private bool _guardPending;
        private bool _guardEnabled;
        private int _guardOptions;
        private byte[] _marker;
        private object _originalResultStore;
        private string _resultRoot;

        private static FieldInfo ManagerField(string name) => typeof(TestJobManager).GetField(name, PrivateStatic);

        private static FieldInfo ServiceField => typeof(MCPServiceLocator).GetField("_testRunnerService", PrivateStatic);

        private static void InvokeManager(string name, params object[] args) => typeof(TestJobManager).GetMethod(name, PrivateStatic).Invoke(null, args);

        private bool _oldControlDisabled;
        private object _originalActiveOwner;
        private string _originalRetired;
        private const string RetiredKey = "MCPForUnity.TestRunner.RetiredRunJobId";

        [SetUp]
        public void SetUp()
        {
            _originalActiveOwner = typeof(TestRunnerService).GetField("_activeRunOwner", PrivateStatic).GetValue(null);
            _originalRetired = SessionState.GetString(RetiredKey, "");
            typeof(TestRunnerService).GetField("_activeRunOwner", PrivateStatic).SetValue(null, null);
            SessionState.SetString(RetiredKey, "");
            _oldControlDisabled = NativeTestRunControl.DisableForTests;
            NativeTestRunControl.DisableForTests = true;
            _jobs = (Dictionary<string, TestJob>)ManagerField("Jobs").GetValue(null);
            _originalJobs = new Dictionary<string, TestJob>(_jobs);
            _originalCurrent = TestJobManager.CurrentJobId;
            _originalSessionJobs = SessionState.GetString(JobsKey, string.Empty);
            _originalSessionCurrent = SessionState.GetString(CurrentKey, string.Empty);
            _originalLastPersist = ManagerField("_lastPersistUnixMs").GetValue(null);
            _originalService = ServiceField.GetValue(null);
            _originalStatus = typeof(TestRunStatus).GetFields(PrivateStatic).Where(f => !f.IsInitOnly).ToDictionary(f => f, f => f.GetValue(null));
            _originalOptionsEnabled = EditorSettings.enterPlayModeOptionsEnabled;
            _originalOptions = EditorSettings.enterPlayModeOptions;
            _guardPending = SessionState.GetBool(GuardPrefix + "PendingRestore", false);
            _guardEnabled = SessionState.GetBool(GuardPrefix + "OriginalEnabled", false);
            _guardOptions = SessionState.GetInt(GuardPrefix + "OriginalOptions", 0);
            _marker = File.Exists(MarkerPath) ? File.ReadAllBytes(MarkerPath) : null;
            _originalResultStore = ManagerField("ResultStoreOverride").GetValue(null);
            _resultRoot = Path.Combine(Path.GetTempPath(), "mcp-reload-results-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_resultRoot);
            ManagerField("ResultStoreOverride").SetValue(null, TestJobResultStoreTests.NewStore(_resultRoot));
            _jobs.Clear();
            ManagerField("_currentJobId").SetValue(null, null);
            ServiceField.SetValue(null, null);
            TestRunStatus.MarkFinished();
            PlayModeOptionsGuard.Clear();
        }

        [TearDown]
        public void TearDown()
        {
            NativeTestRunControl.DisableForTests = _oldControlDisabled;
            ManagerField("ResultStoreOverride").SetValue(null, _originalResultStore);
            if (Directory.Exists(_resultRoot))
                Directory.Delete(_resultRoot, true);
            // Do not reset the whole service locator: the outer Editor test run may own it.
            (ServiceField.GetValue(null) as IDisposable)?.Dispose();
            ServiceField.SetValue(null, _originalService);
            typeof(TestRunnerService).GetField("_activeRunOwner", PrivateStatic).SetValue(null, _originalActiveOwner);
            SessionState.SetString(RetiredKey, _originalRetired);
            _jobs.Clear();
            foreach (var entry in _originalJobs)
                _jobs.Add(entry.Key, entry.Value);
            ManagerField("_currentJobId").SetValue(null, _originalCurrent);
            ManagerField("_lastPersistUnixMs").SetValue(null, _originalLastPersist);
            SessionState.SetString(JobsKey, _originalSessionJobs);
            SessionState.SetString(CurrentKey, _originalSessionCurrent);
            foreach (var entry in _originalStatus)
                entry.Key.SetValue(null, entry.Value);
            EditorSettings.enterPlayModeOptions = _originalOptions;
            EditorSettings.enterPlayModeOptionsEnabled = _originalOptionsEnabled;
            SessionState.SetBool(GuardPrefix + "PendingRestore", _guardPending);
            SessionState.SetBool(GuardPrefix + "OriginalEnabled", _guardEnabled);
            SessionState.SetInt(GuardPrefix + "OriginalOptions", _guardOptions);
            if (_marker == null)
            {
                if (File.Exists(MarkerPath))
                    File.Delete(MarkerPath);
            }
            else
                File.WriteAllBytes(MarkerPath, _marker);
        }

        private TestJob AddJob(string id = "1234567890abcdef1234567890abcdef")
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            var job = new TestJob
            {
                JobId = id,
                Mode = "EditMode",
                Status = TestJobStatus.Running,
                StartedUnixMs = now,
                LastUpdateUnixMs = now,
                FailuresSoFar = new List<TestJobFailure>(),
            };
            _jobs[id] = job;
            ManagerField("_currentJobId").SetValue(null, id);
            return job;
        }

        private TestRunnerService RestoreCallbacks()
        {
            InvokeManager("RestoreRunningJobCallbacks");
            return ServiceField.GetValue(null) as TestRunnerService;
        }

        [Test]
        public void RestoreRunningJob_RecreatesLazyServiceOnce_WithoutRestartingRun()
        {
            var job = AddJob();
            job.CompletedTests = 3;
            job.TotalTests = 8;
            InvokeManager("PersistToSessionState", true);
            _jobs.Clear();
            ManagerField("_currentJobId").SetValue(null, null);
            InvokeManager("TryRestoreFromSessionState");

            var service = RestoreCallbacks();
            Assert.NotNull(service);
            Assert.AreSame(service, RestoreCallbacks(), "Repeated recovery must not register a second service.");
            Assert.IsTrue(TestRunStatus.IsRunning);
            Assert.AreEqual(TestMode.EditMode, TestRunStatus.Mode);
            Assert.AreEqual(3, _jobs[job.JobId].CompletedTests, "Recovery must not reset progress or execute tests again.");
            Assert.AreEqual(8, _jobs[job.JobId].TotalTests);
        }

        [Test]
        public void RestoreCallbacks_WithoutRunningJob_DoesNotCreateService()
        {
            Assert.IsNull(RestoreCallbacks());
            Assert.IsFalse(TestRunStatus.IsRunning);
        }

        [Test]
        public void ReloadInterruptionRetainsCompletedLeavesWithoutReportingTestAssertionFailure()
        {
            var job = AddJob();
            job.TotalTests = 82;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.Completed", "Passed"));
            InvokeManager("BeforeAssemblyReload");
            _jobs.Clear();
            InvokeManager("TryRestoreFromSessionState");
            service.OnError("Native test execution stopped.");
            var restored = _jobs[job.JobId];
            Assert.AreEqual("interrupted_by_reload", restored.CompletionKind);
            Assert.AreEqual(1, restored.CompletedTests);
            Assert.AreEqual(82, restored.TotalTests);
            Assert.AreEqual("partial", restored.ResultAvailability);
            Assert.AreEqual(1, restored.RetainedSummary.Passed);
            Assert.AreEqual(0, restored.RetainedSummary.Failed);
            Assert.NotNull(restored.ResultArtifact);
            var response = JObject.FromObject(
                MCPForUnity.Editor.Tools.GetTestJob.HandleCommand(
                    new JObject
                    {
                        ["job_id"] = job.JobId,
                        ["page_size"] = 10,
                        ["includeDetails"] = true,
                    }
                )
            );
            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual("interrupted_by_reload", (string)response["data"]["completion_kind"]);
            Assert.AreEqual(82, (int)response["data"]["progress"]["total"]);
            Assert.AreEqual(1, ((JArray)response["data"]["result_page"]["items"]).Count);
        }

        [Test]
        public void ExpectedReloadResumePreservesProgressAndAllowsNormalCompletion()
        {
            var job = AddJob();
            job.TotalTests = 2;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.Before", "Passed"));
            InvokeManager("BeforeAssemblyReload");
            TestJobManager.OnRunStarted(1); // A resumed runner can report only remaining tests.
            Assert.AreEqual(2, job.TotalTests);
            Assert.AreEqual(1, job.CompletedTests);
            service.TestFinished(new ResultStub("Fixture.After", "Passed"));
            service.RunFinished(ResultStub.Suite(new ResultStub("Fixture.Before", "Passed"), new ResultStub("Fixture.After", "Passed")));
            Assert.AreEqual(TestJobStatus.Succeeded, job.Status);
            Assert.AreEqual("completed", job.CompletionKind);
            Assert.AreEqual(2, job.CompletedTests);
        }

        [Test]
        public void BeforeReload_FlushesProgressDespitePersistenceThrottle()
        {
            var job = AddJob();
            InvokeManager("PersistToSessionState", true);
            ManagerField("_lastPersistUnixMs").SetValue(null, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 1000);
            TestJobManager.OnLeafTestFinished("Fixture.LastTest", true, "assertion failed");
            Assert.AreEqual(0, (int)JObject.Parse(SessionState.GetString(JobsKey, ""))["jobs"][0]["completed_tests"]);

            InvokeManager("BeforeAssemblyReload");
            _jobs.Clear();
            InvokeManager("TryRestoreFromSessionState");

            Assert.AreEqual(1, _jobs[job.JobId].CompletedTests);
            Assert.AreEqual("Fixture.LastTest", _jobs[job.JobId].LastFinishedTestFullName);
            Assert.AreEqual("assertion failed", _jobs[job.JobId].FailuresSoFar.Single().Message);
        }

        [Test]
        public void RecoveredRunFinished_FinalizesWithoutOriginalTask_AndRetainsEarlierResults()
        {
            var job = AddJob();
            var service = RestoreCallbacks();
            var passedBeforeReload = new ResultStub("Fixture.BeforeReload", "Passed");
            var failedAfterReload = new ResultStub("Fixture.AfterReload", "Failed", "assertion failed");
            service.TestFinished(failedAfterReload);
            service.RunFinished(ResultStub.Suite(passedBeforeReload, failedAfterReload));

            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.IsNull(TestJobManager.CurrentJobId);
            Assert.IsFalse(TestRunStatus.IsRunning);
            Assert.AreEqual(2, job.CompletedTests);
            Assert.AreEqual(2, job.Result.Results.Count);
            Assert.AreEqual(0, RetainedResults(service).Count, "Final results must not retain Unity adaptor trees.");
            var payload = JObject.FromObject(TestJobManager.ToSerializable(job, false, true));
            Assert.AreEqual(1, (int)payload["result"]["summary"]["failed"]);
            Assert.AreEqual("assertion failed", (string)payload["result"]["results"][0]["message"]);
        }

        [Test]
        public void IdleService_DoesNotRetainManualRunResults()
        {
            var service = new TestRunnerService();
            ServiceField.SetValue(null, service);
            service.TestFinished(new ResultStub("Manual.First", "Passed"));
            service.TestFinished(new ResultStub("Manual.Second", "Failed"));
            Assert.AreEqual(0, RetainedResults(service).Count, "Global manual-run callbacks must not accumulate in the idle MCP service.");
        }

        [TestCase(false, 0)]
        [TestCase(true, 1000)]
        public void RunFinished_OnlyMaterializesResultsForAnOwnedRun(bool recoveredRun, int expectedReads)
        {
            TestJob job = recoveredRun ? AddJob() : null;
            var service = recoveredRun ? RestoreCallbacks() : new TestRunnerService();
            ServiceField.SetValue(null, service);
            var leaves = Enumerable.Range(0, 1000).Select(index => new ResultStub("Fixture.Test" + index, "Passed")).ToArray();

            service.RunFinished(ResultStub.Suite(leaves));

            Assert.AreEqual(
                expectedReads,
                leaves.Sum(leaf => leaf.NameReads),
                "Global callbacks for an idle service must not materialize an unrelated result tree."
            );
            if (recoveredRun)
            {
                Assert.AreEqual(TestJobStatus.Succeeded, job.Status);
                Assert.AreEqual(1000, job.Result.Results.Count);
            }
        }

        [Test]
        public void RecoveredRunError_ReleasesCollectedResults()
        {
            AddJob();
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.BeforeError", "Passed"));
            Assert.AreEqual(1, RetainedResults(service).Count);
            service.OnError("Run interrupted");
            Assert.AreEqual(0, RetainedResults(service).Count);
        }

        [Test]
        public void StartupFailure_ReleasesCollectedResults()
        {
            AddJob();
            var service = new TestRunnerService();
            ServiceField.SetValue(null, service);
            var api = typeof(TestRunnerService).GetField("_testRunnerApi", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);
            var scheduler = typeof(TestRunnerApi).GetField("ScheduleJob", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.NotNull(scheduler);
            scheduler.SetValue(
                api,
                new Func<ExecutionSettings, string>(_ =>
                {
                    service.TestFinished(new ResultStub("Fixture.BeforeStartupFailure", "Passed"));
                    throw new InvalidOperationException("schedule-failure");
                })
            );
            var run = service.RunTestsAsync(TestMode.EditMode);
            Assert.IsTrue(run.IsCompleted);
            Assert.Throws<InvalidOperationException>(() => run.GetAwaiter().GetResult());
            Assert.AreEqual(0, RetainedResults(service).Count);
        }

        private static List<ITestResultAdaptor> RetainedResults(TestRunnerService service) =>
            (List<ITestResultAdaptor>)typeof(TestRunnerService).GetField("_leafResults", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);

        [Test]
        public void LateCallbacksFromRecoveredJob_DoNotMutateNewJobOrItsSettings()
        {
            AddJob("old-job");
            var service = RestoreCallbacks();
            var newer = AddJob("new-job");
            newer.CompletedTests = 7;
            newer.TotalTests = 9;
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;

            service.RunStarted(null);
            service.TestFinished(new ResultStub("Old.Finished", "Failed"));
            service.RunFinished(ResultStub.Suite(new ResultStub("Old.Finished", "Failed")));

            Assert.AreEqual("new-job", TestJobManager.CurrentJobId);
            Assert.AreEqual(TestJobStatus.Running, newer.Status);
            Assert.AreEqual(7, newer.CompletedTests);
            Assert.AreEqual(9, newer.TotalTests);
            Assert.IsTrue(TestRunStatus.IsRunning);
            Assert.IsTrue(PlayModeOptionsGuard.IsPending);
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
        }

        [Test]
        public void RecoveredRun_CannotStartOverlappingRunAfterJobIsCleared()
        {
            AddJob();
            var service = RestoreCallbacks();
            TestJobManager.ClearStuckJob();
            Assert.Throws<InvalidOperationException>(() => service.RunTestsAsync(TestMode.EditMode).GetAwaiter().GetResult());
        }

        [Test]
        public void RecoveredInitializationError_FailsJobAndRestoresSettings()
        {
            var job = AddJob();
            var service = RestoreCallbacks();
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;

            ((IErrorCallbacks)service).OnError("Prebuild setup failed");

            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.AreEqual("Prebuild setup failed", job.Error);
            Assert.IsNull(TestJobManager.CurrentJobId);
            Assert.IsFalse(TestRunStatus.IsRunning);
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
        }

        [UnityTest]
        public IEnumerator ClearedRecoveredRun_AfterRunFinished_CanStartNextRun() => CheckRecoveredRunRestart(false);

        [UnityTest]
        public IEnumerator ClearedRecoveredRun_AfterError_CanStartNextRun() => CheckRecoveredRunRestart(true);

        private IEnumerator CheckRecoveredRunRestart(bool error)
        {
            AddJob("old-job");
            var service = RestoreCallbacks();
            TestJobManager.ClearStuckJob();
            if (error)
                service.OnError("Old initialization failed");
            else
                service.RunFinished(ResultStub.Suite(new ResultStub("Old.Pass", "Passed")));

            // Replace only this API instance's scheduler so this does not execute a nested
            // test run. The project pins Test Framework 1.1.33, which exposes this test seam.
            var api = typeof(TestRunnerService).GetField("_testRunnerApi", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(service);
            var scheduler = typeof(TestRunnerApi).GetField("ScheduleJob", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.NotNull(scheduler);
            bool scheduled = false;
            scheduler.SetValue(
                api,
                new Func<ExecutionSettings, string>(_ =>
                {
                    scheduled = true;
                    return "synthetic-run";
                })
            );
            var nextJob = AddJob("next-job");
            var pending = service.RunTestsAsync(TestMode.EditMode);
            Assert.IsTrue(scheduled, "Terminal callbacks must release recovered ownership.");
            service.RunFinished(ResultStub.Suite(new ResultStub("Next.Pass", "Passed")));
            double deadline = EditorApplication.timeSinceStartup + 5;
            while (!pending.IsCompleted && EditorApplication.timeSinceStartup < deadline)
                yield return null;
            Assert.IsTrue(pending.IsCompleted, "Completion must release the original async waiter.");
            var result = pending.GetAwaiter().GetResult();
            Assert.AreEqual(1, result.Passed);
            Assert.AreEqual(TestJobStatus.Succeeded, nextJob.Status);
        }

        [Test]
        public void ResultTree_ExcludesEmptySuitesFromIndividualResults()
        {
            var emptySuite = new ResultStub("Fixture.EmptySuite", "Failed") { IsSuite = true };
            var leaf = new ResultStub("Fixture.Test", "Passed");
            var result = TestRunResult.Create(ResultStub.Suite(emptySuite, leaf), new ITestResultAdaptor[] { emptySuite });
            Assert.AreEqual(1, result.Results.Count);
            Assert.AreEqual("Fixture.Test", result.Results[0].FullName);
        }

        [Test]
        public void PlayModeGuard_KeepsOriginalSettingsBackupUntilRecoveredRunFinishes()
        {
            AddJob();
            PlayModeOptionsGuard.Save(false, EnterPlayModeOptions.None);
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;
            // TestRunStatus is transient and initially false after a reload.
            PlayModeOptionsGuard.RestoreIfIdle();
            Assert.IsTrue(PlayModeOptionsGuard.IsPending);
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            var service = RestoreCallbacks();
            service.RunFinished(ResultStub.Suite(new ResultStub("Fixture.Pass", "Passed")));
            Assert.IsFalse(PlayModeOptionsGuard.IsPending);
            Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.None, EditorSettings.enterPlayModeOptions);
        }

        [Test]
        public void FailedRun_ReturnsSummaryWithoutDetailsUnlessRequested()
        {
            var job = AddJob();
            job.Status = TestJobStatus.Failed;
            job.Result = TestRunResult.Create(ResultStub.Suite(new ResultStub("Fixture.Fail", "Failed")), Array.Empty<ITestResultAdaptor>());
            var payload = JObject.FromObject(TestJobManager.ToSerializable(job, false, false));
            Assert.AreEqual("failed", (string)payload["status"]);
            Assert.AreEqual(1, (int)payload["result"]["summary"]["failed"]);
            Assert.AreEqual(JTokenType.Null, payload["result"]["results"].Type);
        }

        [Test]
        public void Snapshot_ReportsActualRunInBackgroundSetting()
        {
            var payload = JObject.FromObject(TestJobManager.ToSerializable(AddJob(), false, false));
            Assert.AreEqual(Application.runInBackground, (bool)payload["progress"]["run_in_background"]);
        }

        [Test]
        public void NoThrottle_ErrorBeforeRunStarted_RestoresCapturedPreferences()
        {
            const string activeKey = "TestRunnerNoThrottle_TestRunActive";
            const string capturedKey = "TestRunnerNoThrottle_SettingsCaptured";
            const string idleKey = "TestRunnerNoThrottle_PrevIdleTime";
            const string modeKey = "TestRunnerNoThrottle_PrevInteractionMode";
            bool active = SessionState.GetBool(activeKey, false);
            bool captured = SessionState.GetBool(capturedKey, false);
            int previousIdle = SessionState.GetInt(idleKey, 4);
            int previousMode = SessionState.GetInt(modeKey, 0);
            var read = TestRunnerNoThrottle.ReadPreference;
            var write = TestRunnerNoThrottle.WritePreference;
            var apply = TestRunnerNoThrottle.ApplyInteractionPreferences;
            var preferences = new Dictionary<string, int> { ["ApplicationIdleTime"] = 7, ["InteractionMode"] = 0 };
            int refreshes = 0;
            string originalLeaseRoot = TestRunnerNoThrottle.LeaseRootForTests;
            string ownedLeaseRoot = System.IO.Path.Combine(Application.temporaryCachePath, "McpPreferenceLeaseTests", Guid.NewGuid().ToString("N"));
            try
            {
                TestRunnerNoThrottle.LeaseRootForTests = ownedLeaseRoot;
                SessionState.SetBool(capturedKey, false);
                TestRunnerNoThrottle.ReadPreference = (key, fallback) => preferences.TryGetValue(key, out int value) ? value : fallback;
                TestRunnerNoThrottle.WritePreference = (key, value) => preferences[key] = value;
                TestRunnerNoThrottle.ApplyInteractionPreferences = () => refreshes++;
                TestRunnerNoThrottle.ApplyNoThrottlingPreemptive();
                Assert.AreEqual(0, preferences["ApplicationIdleTime"]);
                Assert.AreEqual(1, preferences["InteractionMode"]);

                var callbackType = typeof(TestRunnerNoThrottle).GetNestedType("TestCallbacks", BindingFlags.NonPublic);
                var callback = Activator.CreateInstance(callbackType, true) as IErrorCallbacks;
                Assert.NotNull(callback, "Initialization errors must be observed even before RunStarted.");
                callback.OnError("Prebuild setup failed");

                Assert.AreEqual(7, preferences["ApplicationIdleTime"]);
                Assert.AreEqual(0, preferences["InteractionMode"]);
                Assert.IsFalse(SessionState.GetBool(activeKey, true));
                Assert.IsFalse(SessionState.GetBool(capturedKey, true));
                Assert.AreEqual(2, refreshes, "Apply and error restoration must use only the owned refresh action.");
            }
            finally
            {
                TestRunnerNoThrottle.RestoreThrottling();
                TestRunnerNoThrottle.LeaseRootForTests = originalLeaseRoot;
                if (System.IO.Directory.Exists(ownedLeaseRoot))
                    System.IO.Directory.Delete(ownedLeaseRoot, true);
                SessionState.SetBool(activeKey, active);
                SessionState.SetBool(capturedKey, captured);
                SessionState.SetInt(idleKey, previousIdle);
                SessionState.SetInt(modeKey, previousMode);
                TestRunnerNoThrottle.ReadPreference = read;
                TestRunnerNoThrottle.WritePreference = write;
                TestRunnerNoThrottle.ApplyInteractionPreferences = apply;
            }
        }

        [Test]
        public void Capture_NodeVisitCapCountsEmptyGroupsAndPreservesRootSummary()
        {
            var groups = Enumerable.Range(0, 50001).Select(index => new ResultStub("Empty" + index, "Passed") { IsSuite = true }).ToArray();
            var result = TestRunResult.Create(ResultStub.Suite(groups), Array.Empty<ITestResultAdaptor>());
            Assert.AreEqual("traversal_limit", result.Availability);
            Assert.AreEqual(50001, result.Passed);
            Assert.AreEqual(0, result.Results.Count);
        }

        [Test]
        public void Capture_DepthCapPreservesAuthoritativeRootSummary()
        {
            ITestResultAdaptor root = new ResultStub("Leaf", "Failed");
            for (int depth = 0; depth < 130; depth++)
                root = ResultStub.Suite(root);
            var result = TestRunResult.Create(root, Array.Empty<ITestResultAdaptor>());
            Assert.AreEqual("traversal_limit", result.Availability);
            Assert.AreEqual(1, result.Failed);
            Assert.AreEqual(0, result.Results.Count);
        }

        [Test]
        public void Capture_StoreFactoryFailureKeepsExactRootSummary()
        {
            var result = TestRunResult.Create(
                ResultStub.Suite(new ResultStub("Leaf", "Failed")),
                Array.Empty<ITestResultAdaptor>(),
                () => throw new IOException("synthetic store failure"),
                "1234567890abcdef1234567890abcdef",
                "EditMode"
            );
            Assert.AreEqual("write_failed", result.Availability);
            Assert.AreEqual(1, result.Failed);
            Assert.AreEqual(0, result.Results.Count);
        }

        [Test]
        public void Capture_PortableWindowsPathAdmissionKeepsExactRootSummary()
        {
            if (Path.DirectorySeparatorChar != '\\')
                return;
            string longRoot = Path.Combine(_resultRoot, new string('x', 120));
            var result = TestRunResult.Create(
                ResultStub.Suite(new ResultStub("Leaf", "Failed")),
                Array.Empty<ITestResultAdaptor>(),
                () => new TestJobResultStore(longRoot, TestJobResultStoreTests.SessionId),
                TestJobResultStoreTests.JobId,
                "EditMode"
            );
            Assert.AreEqual("path_limit_exceeded", result.Availability);
            Assert.AreEqual(1, result.Failed);
            Assert.AreEqual(0, result.Results.Count);
        }

        [Test]
        public void RecoveredRunFinished_DoesNotInvokePollingInitializationTimeout()
        {
            var job = AddJob();
            job.StartedUnixMs = 1;
            job.InitTimeoutMs = 1;
            job.TotalTests = null;
            var service = RestoreCallbacks();
            service.RunFinished(ResultStub.Suite(new ResultStub("Fixture.Completed", "Passed")));
            Assert.AreEqual(TestJobStatus.Succeeded, job.Status);
            Assert.IsNull(job.Error);
            Assert.AreEqual(1, job.Result.Passed);
        }

        [Test]
        public void PartialRunFinishedAfterReloadKeepsCheckpointAndExpectedCount()
        {
            var job = AddJob();
            job.TotalTests = 82;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.Completed", "Passed"));
            typeof(TestJobManager).GetMethod("BeforeAssemblyReload", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
            service.RunFinished(ResultStub.Suite(new ResultStub("Fixture.Completed", "Passed")));
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.AreEqual("interrupted_by_reload", job.CompletionKind);
            Assert.AreEqual("partial", job.ResultAvailability);
            Assert.AreEqual(82, job.TotalTests);
            Assert.AreEqual(1, job.CompletedTests);
            Assert.AreEqual(0, job.RetainedSummary.Failed);
        }

        private TestRunnerService ReloadOwner(TestRunnerService service)
        {
            InvokeManager("BeforeAssemblyReload");
            // Model destruction of the managed domain, not user disposal/retirement of a live run.
            typeof(TestRunnerService).GetField("_activeRunOwner", PrivateStatic).SetValue(null, null);
            service.Dispose();
            ServiceField.SetValue(null, null);
            _jobs.Clear();
            ManagerField("_currentJobId").SetValue(null, null);
            InvokeManager("TryRestoreFromSessionState");
            var resumed = RestoreCallbacks();
            Assert.AreNotSame(service, resumed);
            return resumed;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FreshOwnerShortTerminalPreservesOriginalRunAndPriorFailure(bool priorFailure)
        {
            var job = AddJob();
            job.TotalTests = 82;
            var service = RestoreCallbacks();
            for (int index = 0; index < 81; index++)
                service.TestFinished(new ResultStub("Fixture.Case" + index, index == 0 && priorFailure ? "Failed" : "Passed"));
            service = ReloadOwner(service);
            var replay = new ResultStub("Fixture.Case80", "Passed");
            service.RunStarted(replay.Test);
            service.TestStarted(replay.Test);
            service.TestFinished(replay);
            service.RunFinished(ResultStub.Suite(replay));

            var restored = _jobs[job.JobId];
            Assert.AreEqual(TestJobStatus.Failed, restored.Status);
            Assert.AreEqual("interrupted_by_reload", restored.CompletionKind);
            Assert.AreEqual(82, restored.TotalTests);
            Assert.AreEqual(81, restored.CompletedTests);
            Assert.AreEqual(priorFailure ? 1 : 0, restored.RetainedSummary.Failed);
            Assert.AreEqual(81, (int)JObject.FromObject(TestJobManager.Page(restored, 100, null, false))["total_count"]);
            Assert.IsFalse(restored.AwaitingReloadResume, "Callbacks resumed; completeness must not depend on this liveness flag.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FreshOwnerRemainingOnlyTerminalReconcilesCompleteRun(bool priorFailure)
        {
            var job = AddJob();
            job.TotalTests = 82;
            var service = RestoreCallbacks();
            for (int index = 0; index < 81; index++)
                service.TestFinished(new ResultStub("Fixture.Case" + index, index == 0 && priorFailure ? "Failed" : "Passed"));
            service = ReloadOwner(service);
            var remaining = new ResultStub("Fixture.Case81", "Passed");
            service.RunStarted(remaining.Test);
            service.TestStarted(remaining.Test);
            service.TestFinished(remaining);
            service.RunFinished(ResultStub.Suite(remaining));

            var restored = _jobs[job.JobId];
            Assert.AreEqual(priorFailure ? TestJobStatus.Failed : TestJobStatus.Succeeded, restored.Status);
            Assert.AreEqual("completed", restored.CompletionKind);
            Assert.AreEqual(82, restored.TotalTests);
            Assert.AreEqual(82, restored.CompletedTests);
            Assert.AreEqual(priorFailure ? 1 : 0, restored.RetainedSummary.Failed);
            Assert.AreEqual(82, restored.Result.Results.Count);
        }

        [Test]
        public void FreshOwnerErrorPersistsLeavesObservedAfterCheckpoint()
        {
            var job = AddJob();
            job.TotalTests = 3;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.A", "Failed"));
            service = ReloadOwner(service);
            var next = new ResultStub("Fixture.B", "Passed");
            service.RunStarted(next.Test);
            service.TestStarted(next.Test);
            service.TestFinished(next);
            service.OnError("Synthetic native failure");

            var restored = _jobs[job.JobId];
            Assert.AreEqual(TestJobStatus.Failed, restored.Status);
            Assert.AreEqual("interrupted_by_reload", restored.CompletionKind);
            Assert.AreEqual(2, restored.CompletedTests);
            Assert.AreEqual(1, restored.RetainedSummary.Failed);
            var page = JObject.FromObject(TestJobManager.Page(restored, 100, null, false));
            CollectionAssert.AreEquivalent(new[] { "Fixture.A", "Fixture.B" }, page["items"].Select(row => (string)row["fullName"]));
            Assert.AreEqual(0, RetainedResults(service).Count);
        }

        [Test]
        public void AcceptedCancellationWithoutRunFinishedPreservesResumedLeaves()
        {
            var job = AddJob();
            job.TotalTests = 3;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.A", "Passed"));
            service = ReloadOwner(service);
            service.TestFinished(new ResultStub("Fixture.B", "Failed"));
            job = _jobs[job.JobId];
            const string nativeGuid = "fedcba0987654321fedcba0987654321";
            var snapshot = new JObject
            {
                ["registered"] = new JArray(nativeGuid),
                ["active"] = new JArray(nativeGuid),
                ["serialized_active"] = new JArray(nativeGuid),
                ["global_active"] = true,
            };
            using var control = new NativeTestRunControl(job.JobId, 0, () => 10, () => snapshot, _ => true);
            job.Control = control;
            job.NativeControl = control.State;
            control.BindGuid(nativeGuid);
            control.Request("user");
            Assert.IsTrue(control.CancelAccepted);
            control.ObserveFinal(nativeGuid);
            snapshot = new JObject
            {
                ["registered"] = new JArray(),
                ["active"] = new JArray(),
                ["serialized_active"] = new JArray(),
                ["global_active"] = false,
            };
            control.NoteEditorUpdate();
            control.TickFromEditorUpdate();
            Assert.IsTrue(control.Released);
            InvokeManager("TickNativeControls");

            Assert.AreEqual(TestJobStatus.Cancelled, job.Status);
            Assert.AreEqual("cancelled", job.CompletionKind);
            Assert.AreEqual(2, job.CompletedTests);
            Assert.AreEqual(1, job.RetainedSummary.Failed);
            var page = JObject.FromObject(TestJobManager.Page(job, 100, null, false));
            CollectionAssert.AreEquivalent(new[] { "Fixture.A", "Fixture.B" }, page["items"].Select(row => (string)row["fullName"]));
            Assert.AreEqual(0, RetainedResults(service).Count);
            Assert.IsNull(TestJobManager.CurrentJobId);
            Assert.IsFalse(TestRunnerService.HasOwnedNativeRun);
        }

        [Test]
        public void DuplicateDisplayNamesRetainDistinctIdsAndReplayCannotHideFailure()
        {
            var job = AddJob();
            job.TotalTests = 2;
            var service = RestoreCallbacks();
            var failed = new ResultStub("Fixture.SameName", "Failed") { TestId = "first", StableIdentity = "parent/case0" };
            var passed = new ResultStub("Fixture.SameName", "Passed") { TestId = "second", StableIdentity = "parent/case1" };
            var replay = new ResultStub("Fixture.SameName", "Passed") { TestId = "first", StableIdentity = "parent/case0" };
            service.TestFinished(failed);
            service.TestFinished(passed);
            service.TestFinished(replay);
            service.RunFinished(ResultStub.Suite(replay, passed));
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.AreEqual(2, job.CompletedTests);
            Assert.AreEqual(2, job.Result.Results.Select(row => row.TestId).Distinct().Count());
            Assert.AreEqual(1, job.RetainedSummary.Failed);
            Assert.AreEqual(1, job.Result.Results.Count(row => row.State == "Failed"));
        }

        [Test]
        public void FreshOwnerChangedNativeIdUsesUniqueStructuralIdentityWithoutHidingFailure()
        {
            var job = AddJob();
            job.TotalTests = 2;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.SameName", "Failed") { TestId = "old-first", StableIdentity = "parent/case0" });
            service = ReloadOwner(service);
            var replay = new ResultStub("Fixture.SameName", "Passed") { TestId = "new-first", StableIdentity = "parent/case0" };
            var next = new ResultStub("Fixture.SameName", "Passed") { TestId = "new-second", StableIdentity = "parent/case1" };
            service.RunStarted(next.Test);
            service.TestStarted(next.Test);
            service.TestFinished(replay);
            service.TestFinished(next);
            service.RunFinished(ResultStub.Suite(replay, next));
            var restored = _jobs[job.JobId];
            Assert.AreEqual(TestJobStatus.Failed, restored.Status);
            Assert.AreEqual("completed", restored.CompletionKind);
            Assert.AreEqual(2, restored.CompletedTests);
            Assert.AreEqual(1, restored.RetainedSummary.Failed);
            Assert.AreEqual(2, restored.Result.Results.Count);
        }

        [Test]
        public void TerminalCaptureLimitPreservesFailureReasonAndCheckpointPages()
        {
            var job = AddJob();
            job.TotalTests = 2;
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.A", "Failed"));
            var previous = TestJobManager.SnapshotResultsForCapture(job.JobId);
            string generation = (string)job.ResultArtifact["generation"];
            var unavailable = new TestRunResult(new TestRunSummary(2, 2, 0, 0, 0.2, "Passed"), Array.Empty<TestRunTestResult>())
            {
                Availability = "quota_exceeded",
            };
            TestJobManager.FinalizeCurrentJobFromRunFinished(unavailable, previous);
            Assert.AreEqual(TestJobStatus.Failed, job.Status);
            Assert.AreEqual("quota_exceeded", job.ResultAvailability);
            Assert.AreEqual(1, job.RetainedSummary.Failed);
            Assert.AreEqual(generation, (string)job.ResultArtifact["generation"]);
            Assert.AreEqual(1, (int)JObject.FromObject(TestJobManager.Page(job, 100, null, false))["total_count"]);
        }

        [Test]
        public void LegacyRowsWithoutIdentityRemainReadableAndAreNeverCollapsedByName()
        {
            var job = AddJob();
            var summary = new TestRunSummary(2, 1, 1, 0, 0.2, "Partial");
            var legacy = new TestRunResult(
                summary,
                new[]
                {
                    new TestRunTestResult("same", "Fixture.SameName", "Failed", 0.1, "failure", null, null),
                    new TestRunTestResult("same", "Fixture.SameName", "Passed", 0.1, null, null, null),
                }
            );
            job.RetainedSummary = summary;
            job.ResultArtifact = TestJobManager.ResultStore.Save(job.JobId, job.Mode, legacy, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
            var rows = TestJobManager.ResultStore.Results(job.JobId, job.ResultArtifact).ToArray();
            Assert.AreEqual(2, rows.Length);
            Assert.IsTrue(rows.All(row => row.TestId == null));
            var service = RestoreCallbacks();
            service.TestFinished(new ResultStub("Fixture.SameName", "Passed") { TestId = "new", StableIdentity = "parent/new" });
            service.OnError("Synthetic stop");
            Assert.AreEqual(3, job.RetainedSummary.Total);
            Assert.AreEqual(1, job.RetainedSummary.Failed);
            Assert.AreEqual(3, (int)JObject.FromObject(TestJobManager.Page(job, 100, null, false))["total_count"]);
        }

        private sealed class ResultStub : ITestResultAdaptor
        {
            private readonly ITestResultAdaptor[] _children;
            private readonly string _name;

            public ResultStub(string name, string state, string message = null, params ITestResultAdaptor[] children)
            {
                _name = FullName = name;
                TestId = StableIdentity = name;
                ResultState = state;
                Message = message;
                _children = children;
            }

            public static ResultStub Suite(params ITestResultAdaptor[] children) => new ResultStub("Suite", "Failed", null, children);

            public bool IsSuite { get; set; }
            public string TestId { get; set; }
            public string StableIdentity { get; set; }
            public ITestAdaptor Test => new TestStub(FullName, IsSuite || HasChildren, TestId, StableIdentity);
            public int NameReads { get; private set; }
            public string Name
            {
                get
                {
                    NameReads++;
                    return _name;
                }
            }
            public string FullName { get; }
            public string ResultState { get; }
            public TestStatus TestStatus => ResultState == "Passed" ? TestStatus.Passed : TestStatus.Failed;
            public double Duration => 0.1;
            public DateTime StartTime => DateTime.UtcNow;
            public DateTime EndTime => DateTime.UtcNow;
            public string Message { get; }
            public string StackTrace => "Fixture.cs:12";
            public int AssertCount => 1;
            public int FailCount =>
                HasChildren ? _children.Sum(c => c.FailCount)
                : ResultState == "Failed" ? 1
                : 0;
            public int PassCount =>
                HasChildren ? _children.Sum(c => c.PassCount)
                : ResultState == "Passed" ? 1
                : 0;
            public int SkipCount => 0;
            public int InconclusiveCount => 0;
            public bool HasChildren => _children.Length > 0;
            public IEnumerable<ITestResultAdaptor> Children => _children;
            public string Output => "test output";

            public TNode ToXml() => new TNode("test-case");
        }

        private sealed class TestStub : ITestAdaptor
        {
            public TestStub(string name, bool isSuite, string id, string uniqueName)
            {
                Name = name;
                IsSuite = isSuite;
                Id = id;
                UniqueName = uniqueName;
            }

            public string Id { get; }
            public string Name { get; }
            public string FullName => Name;
            public int TestCaseCount => IsSuite ? 0 : 1;
            public bool HasChildren => false;
            public bool IsSuite { get; }
            public IEnumerable<ITestAdaptor> Children => Array.Empty<ITestAdaptor>();
            public ITestAdaptor Parent => null;
            public int TestCaseTimeout => 0;
            public ITypeInfo TypeInfo => null;
            public IMethodInfo Method => null;
            public object[] Arguments => Array.Empty<object>();
            public string[] Categories => Array.Empty<string>();
            public bool IsTestAssembly => false;
            public RunState RunState => RunState.Runnable;
            public string Description => null;
            public string SkipReason => null;
            public string ParentId => null;
            public string ParentFullName => null;
            public string UniqueName { get; }
            public string ParentUniqueName => null;
            public int ChildIndex => 0;
            public TestMode TestMode => TestMode.EditMode;
        }
    }
}
