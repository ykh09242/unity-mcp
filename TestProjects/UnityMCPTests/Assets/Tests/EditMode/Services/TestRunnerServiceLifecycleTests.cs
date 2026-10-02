using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;

namespace MCPForUnityTests.Editor.Services
{
    public class TestRunnerServiceLifecycleTests
    {
        private TestRunnerService _service;
        private TestRunnerApi _api;
        private readonly Dictionary<FieldInfo, object> _status = new Dictionary<FieldInfo, object>();
        private int _idleTime;
        private int _interactionMode;
        private bool _playOptionsEnabled;
        private EnterPlayModeOptions _playOptions;
        private bool _throttleActive;
        private bool _throttleCaptured;
        private int _savedIdle;
        private int _savedMode;
        private bool _stateCaptured;
        private Dictionary<string, TestJob> _jobs;
        private string _currentJobId;
        private string _jobSession;
        private string _currentJobSession;
        private long _persistTime;
        private const BindingFlags StaticPrivate = BindingFlags.NonPublic | BindingFlags.Static;
        private static Dictionary<string, TestJob> Jobs =>
            (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", StaticPrivate).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            if (PlayModeOptionsGuard.IsPending) Assert.Ignore("An existing run owns the PlayMode options backup.");
            foreach (var field in typeof(TestRunStatus).GetFields(BindingFlags.NonPublic | BindingFlags.Static))
                if (!field.IsInitOnly) _status[field] = field.GetValue(null);
            _idleTime = EditorPrefs.GetInt("ApplicationIdleTime", 4);
            _interactionMode = EditorPrefs.GetInt("InteractionMode", 0);
            _playOptionsEnabled = EditorSettings.enterPlayModeOptionsEnabled;
            _playOptions = EditorSettings.enterPlayModeOptions;
            _throttleActive = SessionState.GetBool("TestRunnerNoThrottle_TestRunActive", false);
            _throttleCaptured = SessionState.GetBool("TestRunnerNoThrottle_SettingsCaptured", false);
            _savedIdle = SessionState.GetInt("TestRunnerNoThrottle_PrevIdleTime", 4);
            _savedMode = SessionState.GetInt("TestRunnerNoThrottle_PrevInteractionMode", 0);
            _jobs = new Dictionary<string, TestJob>(Jobs);
            _currentJobId = TestJobManager.CurrentJobId;
            _jobSession = SessionState.GetString("MCPForUnity.TestJobsV1", string.Empty);
            _currentJobSession = SessionState.GetString("MCPForUnity.CurrentTestJobIdV1", string.Empty);
            _persistTime = (long)typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate).GetValue(null);
            Jobs.Clear();
            SetCurrent(null);
            _stateCaptured = true;
            SessionState.SetBool("TestRunnerNoThrottle_TestRunActive", false);
            SessionState.SetBool("TestRunnerNoThrottle_SettingsCaptured", false);
            _service = new TestRunnerService();
            _api = (TestRunnerApi)typeof(TestRunnerService).GetField("_testRunnerApi", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service);
        }

        [TearDown]
        public void TearDown()
        {
            if (!_stateCaptured) return;
            _service?.Dispose();
            foreach (var pair in _status) pair.Key.SetValue(null, pair.Value);
            _status.Clear();
            EditorPrefs.SetInt("ApplicationIdleTime", _idleTime);
            EditorPrefs.SetInt("InteractionMode", _interactionMode);
            EditorSettings.enterPlayModeOptionsEnabled = _playOptionsEnabled;
            EditorSettings.enterPlayModeOptions = _playOptions;
            SessionState.SetBool("TestRunnerNoThrottle_TestRunActive", _throttleActive);
            SessionState.SetBool("TestRunnerNoThrottle_SettingsCaptured", _throttleCaptured);
            SessionState.SetInt("TestRunnerNoThrottle_PrevIdleTime", _savedIdle);
            SessionState.SetInt("TestRunnerNoThrottle_PrevInteractionMode", _savedMode);
            Jobs.Clear();
            foreach (var pair in _jobs) Jobs.Add(pair.Key, pair.Value);
            SetCurrent(_currentJobId);
            typeof(TestJobManager).GetField("_lastPersistUnixMs", StaticPrivate).SetValue(null, _persistTime);
            SessionState.SetString("MCPForUnity.TestJobsV1", _jobSession);
            SessionState.SetString("MCPForUnity.CurrentTestJobIdV1", _currentJobSession);
            _stateCaptured = false;
        }

        [Test]
        public void ExecuteFailure_DoesNotLeaveServiceBusyAndAllowsRetry()
        {
            int calls = 0;
            SetSchedule(_ => throw new InvalidOperationException("schedule-failure-" + ++calls));
            var first = _service.RunTestsAsync(TestMode.EditMode);
            Assert.AreEqual("schedule-failure-1", ObserveFailure(first).Message);
            Assert.IsNull(typeof(TestRunnerService).GetField("_runCompletionSource", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service));
            var second = _service.RunTestsAsync(TestMode.EditMode);
            Assert.AreEqual("schedule-failure-2", ObserveFailure(second).Message);
            Assert.AreEqual(2, calls);
        }

        [Test]
        public void PlayModeExecuteFailure_RestoresThrottleAndPlayModeOptions()
        {
            EditorPrefs.SetInt("ApplicationIdleTime", 17);
            EditorPrefs.SetInt("InteractionMode", 2);
            SetSchedule(_ => throw new InvalidOperationException("schedule-failure"));
            var run = _service.RunTestsAsync(TestMode.PlayMode);
            Assert.AreEqual("schedule-failure", ObserveFailure(run).Message);
            Assert.AreEqual(17, EditorPrefs.GetInt("ApplicationIdleTime", 4));
            Assert.AreEqual(2, EditorPrefs.GetInt("InteractionMode", 0));
            Assert.AreEqual(_playOptionsEnabled, EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(_playOptions, EditorSettings.enterPlayModeOptions);
            Assert.IsFalse(TestRunStatus.IsRunning);
        }

        private void SetSchedule(Func<ExecutionSettings, string> schedule)
        {
            var field = typeof(TestRunnerApi).GetField("ScheduleJob", BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.IsNotNull(field, "The pinned Unity Test Framework must expose its scheduling seam.");
            field.SetValue(_api, schedule);
        }

        [Test]
        public async Task ClearedRunCallbacks_DoNotModifyOrFinalizeNewJob()
        {
            var oldJob = new TestJob { JobId = "old", Status = TestJobStatus.Running };
            Jobs.Add(oldJob.JobId, oldJob);
            SetCurrent(oldJob.JobId);
            SetSchedule(_ => "scheduled-old");
            var oldRun = _service.RunTestsAsync(TestMode.EditMode);
            Assert.IsTrue(TestJobManager.ClearStuckJob());

            var newJob = new TestJob { JobId = "new", Status = TestJobStatus.Running, TotalTests = 7, CompletedTests = 3 };
            Jobs.Add(newJob.JobId, newJob);
            SetCurrent(newJob.JobId);
            // The newer request is queued behind the old run's operation lock.
            int schedules = 0;
            SetSchedule(_ => { schedules++; throw new InvalidOperationException("new-startup-failure"); });
            var newRun = _service.RunTestsAsync(TestMode.EditMode);
            Assert.IsFalse(newRun.IsCompleted);

            _service.RunStarted(null);
            int? totalAfterOldStart = newJob.TotalTests;
            int completedAfterOldStart = newJob.CompletedTests;
            _service.RunFinished(null);
            await oldRun;
            try { await newRun; Assert.Fail("Expected the injected new startup failure."); }
            catch (InvalidOperationException ex) { Assert.AreEqual("new-startup-failure", ex.Message); }

            Assert.AreEqual("7/3/Running/new", $"{totalAfterOldStart}/{completedAfterOldStart}/{newJob.Status}/{TestJobManager.CurrentJobId}",
                "Old callbacks must preserve the replacement's progress, status and ownership.");
            Assert.AreEqual(TestJobStatus.Failed, oldJob.Status);
            Assert.AreEqual(1, schedules);
        }

        [Test]
        public async Task SynchronousRunFinished_CompletesOriginalRunTask()
        {
            SetSchedule(_ => { _service.RunFinished(null); return "finished-synchronously"; });
            var result = await _service.RunTestsAsync(TestMode.EditMode);
            Assert.IsNotNull(result);
            Assert.AreEqual(0, result.Total);
            Assert.IsNull(typeof(TestRunnerService).GetField("_runCompletionSource", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service));
        }

        private static void SetCurrent(string id) =>
            typeof(TestJobManager).GetField("_currentJobId", StaticPrivate).SetValue(null, id);

        private static Exception ObserveFailure(Task task)
        {
            Assert.IsTrue(task.IsCompleted, "A startup exception must complete synchronously without pumping Editor updates.");
            try { task.GetAwaiter().GetResult(); }
            catch (Exception ex) { return ex; }
            Assert.Fail("Expected the injected scheduling failure.");
            return null;
        }
    }
}
