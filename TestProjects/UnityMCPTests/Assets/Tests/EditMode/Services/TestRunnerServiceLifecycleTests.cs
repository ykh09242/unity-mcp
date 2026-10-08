using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using NUnit.Framework.Interfaces;
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
        private ITestRunnerService _originalService;
        private object _originalOwner;
        private CapturingRunner _runner;
        private const string RetiredRunSessionKey = "MCPForUnity.TestRunner.RetiredRunJobId";
        private const BindingFlags StaticPrivate = BindingFlags.NonPublic | BindingFlags.Static;
        private static Dictionary<string, TestJob> Jobs => (Dictionary<string, TestJob>)typeof(TestJobManager).GetField("Jobs", StaticPrivate).GetValue(null);

        [SetUp]
        public void SetUp()
        {
            if (PlayModeOptionsGuard.IsPending || TestRunnerService.HasRetiredRun)
                Assert.Ignore("An existing run owns the PlayMode options backup.");
            foreach (var field in typeof(TestRunStatus).GetFields(BindingFlags.NonPublic | BindingFlags.Static))
                if (!field.IsInitOnly)
                    _status[field] = field.GetValue(null);
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
            _originalService = (ITestRunnerService)typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate).GetValue(null);
            _originalOwner = typeof(TestRunnerService).GetField("_activeRunOwner", StaticPrivate).GetValue(null);
            _service = new TestRunnerService();
            _runner = new CapturingRunner(_service);
            MCPServiceLocator.Register<ITestRunnerService>(_runner);
            _api = (TestRunnerApi)typeof(TestRunnerService).GetField("_testRunnerApi", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service);
        }

        [TearDown]
        public void TearDown()
        {
            if (!_stateCaptured)
                return;
            _service?.Dispose();
            SessionState.SetString(RetiredRunSessionKey, string.Empty);
            typeof(TestRunnerService).GetField("_activeRunOwner", StaticPrivate).SetValue(null, _originalOwner);
            typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate).SetValue(null, _originalService);
            foreach (var pair in _status)
                pair.Key.SetValue(null, pair.Value);
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
            foreach (var pair in _jobs)
                Jobs.Add(pair.Key, pair.Value);
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
        public async Task ClearedRun_RejectsReplacementUntilOldTerminalCallback()
        {
            SetSchedule(_ => "scheduled-old");
            string oldId = TestJobManager.StartJob(TestMode.EditMode);
            Task<TestRunResult> oldRun = _runner.LastRun;
            Assert.IsTrue(TestJobManager.ClearStuckJob());
            await ExpectFailure(oldRun, "Job cleared manually");
            Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));
            Assert.AreEqual(1, Jobs.Count, "Rejected requests must not reserve hidden queued jobs.");
            _service.RunStarted(null);
            _service.RunFinished(null);
            Assert.AreEqual(TestJobStatus.Failed, Jobs[oldId].Status);
            Assert.IsFalse(TestRunnerService.HasRetiredRun);

            SetSchedule(_ => throw new InvalidOperationException("retry-reached-scheduler"));
            Assert.AreEqual("retry-reached-scheduler", ObserveFailure(_service.RunTestsAsync(TestMode.EditMode)).Message);
        }

        [Test]
        public async Task InitializationTimeout_SettlesCallerRejectsQueuedJobsAndRecovers()
        {
            int schedules = 0;
            SetSchedule(_ => "scheduled-" + ++schedules);
            string oldId = TestJobManager.StartJob(TestMode.EditMode, initTimeoutMs: 1);
            Task<TestRunResult> oldRun = _runner.LastRun;
            var queuedDiscovery = _service.GetTestsAsync(TestMode.EditMode);
            Assert.IsFalse(queuedDiscovery.IsCompleted);
            Jobs[oldId].StartedUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 100;
            Assert.AreEqual(TestJobStatus.Failed, TestJobManager.GetJob(oldId).Status);
            await ExpectFailure(oldRun, "Test job failed to initialize");
            await ExpectFailure(queuedDiscovery, "has not stopped");
            Assert.IsNull(TestJobManager.CurrentJobId);
            Assert.IsFalse(TestRunStatus.IsRunning);
            Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));
            await ExpectFailure(_service.RunTestsAsync(TestMode.EditMode), "has not stopped");
            await ExpectFailure(_service.GetTestsAsync(TestMode.EditMode), "has not stopped");
            Assert.AreEqual(1, schedules);
            Assert.AreEqual(1, Jobs.Count);
            _service.RunStarted(null);
            _service.RunFinished(null);
            Assert.AreEqual(TestJobStatus.Failed, Jobs[oldId].Status);
            Assert.IsFalse(TestRunnerService.HasRetiredRun);

            string newId = TestJobManager.StartJob(TestMode.EditMode);
            _service.RunFinished(null);
            await _runner.LastRun;
            Assert.AreEqual(2, schedules);
            Assert.AreEqual(TestJobStatus.Succeeded, Jobs[newId].Status);
        }

        [Test]
        public async Task RetiredRun_ReloadRestoresQuarantineAndTerminalCallbackAllowsRetry()
        {
            SetSchedule(_ => "old");
            string oldId = TestJobManager.StartJob(TestMode.EditMode);
            Assert.IsTrue(TestJobManager.ClearStuckJob());
            await ExpectFailure(_runner.LastRun, "Job cleared manually");
            _service.Dispose();
            typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate).SetValue(null, null);
            typeof(TestRunnerService).GetField("_activeRunOwner", StaticPrivate).SetValue(null, null);
            typeof(TestJobManager).GetMethod("RestoreRunningJobCallbacks", StaticPrivate).Invoke(null, null);
            _service = (TestRunnerService)typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate).GetValue(null);
            Assert.IsNotNull(_service, "The reload hook must register callbacks even when the persisted manager job is failed.");
            _runner = new CapturingRunner(_service);
            MCPServiceLocator.Register<ITestRunnerService>(_runner);
            _api = (TestRunnerApi)typeof(TestRunnerService).GetField("_testRunnerApi", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service);
            Assert.Throws<InvalidOperationException>(() => TestJobManager.StartJob(TestMode.EditMode));
            _service.OnError("old-native-run-stopped");
            Assert.IsFalse(TestRunnerService.HasRetiredRun);
            Assert.AreEqual(TestJobStatus.Failed, Jobs[oldId].Status);
            SetSchedule(_ => throw new InvalidOperationException("reload-retry-reached-scheduler"));
            Assert.AreEqual("reload-retry-reached-scheduler", ObserveFailure(_service.RunTestsAsync(TestMode.EditMode)).Message);
        }

        [Test]
        public async Task RetiredRun_LateCallbacksPreserveReplacementStateAndSettings()
        {
            SetSchedule(_ => "old");
            TestJobManager.StartJob(TestMode.EditMode);
            Assert.IsTrue(TestJobManager.ClearStuckJob());
            await ExpectFailure(_runner.LastRun, "Job cleared manually");
            var replacement = new TestJob
            {
                JobId = "replacement",
                Status = TestJobStatus.Running,
                TotalTests = 7,
                CompletedTests = 3,
            };
            Jobs.Add(replacement.JobId, replacement);
            SetCurrent(replacement.JobId);
            EditorSettings.enterPlayModeOptionsEnabled = true;
            EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;
            _service.RunStarted(null);
            _service.RunFinished(null);
            Assert.AreEqual(
                "7/3/Running/replacement",
                $"{replacement.TotalTests}/{replacement.CompletedTests}/{replacement.Status}/{TestJobManager.CurrentJobId}"
            );
            Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled);
            Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
        }

        [Test]
        public async Task RetireJob_MismatchedIdAndDuplicateCallsDoNotChangeAnotherOwner()
        {
            SetSchedule(_ => "old");
            string id = TestJobManager.StartJob(TestMode.EditMode);
            Task<TestRunResult> run = _runner.LastRun;
            TestRunnerService.RetireJob("different-job", "wrong");
            Assert.IsFalse(run.IsCompleted);
            Assert.IsFalse(TestRunnerService.HasRetiredRun);
            TestJobManager.ClearStuckJob();
            TestRunnerService.RetireJob(id, "duplicate");
            await ExpectFailure(run, "Job cleared manually");
            Assert.AreEqual(id, SessionState.GetString(RetiredRunSessionKey, string.Empty));
            SessionState.SetString(RetiredRunSessionKey, "newer-marker");
            _service.RunFinished(null);
            Assert.AreEqual("newer-marker", SessionState.GetString(RetiredRunSessionKey, string.Empty));
        }

        [Test]
        public void InitializationTimeout_WithoutOwnedRunDoesNotCreateServiceOrQuarantine()
        {
            typeof(TestRunnerService).GetField("_activeRunOwner", StaticPrivate).SetValue(null, null);
            typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate).SetValue(null, null);
            var job = new TestJob
            {
                JobId = "unowned",
                Status = TestJobStatus.Running,
                StartedUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 100,
                InitTimeoutMs = 1,
            };
            Jobs.Add(job.JobId, job);
            SetCurrent(job.JobId);
            Assert.AreEqual(TestJobStatus.Failed, TestJobManager.GetJob(job.JobId).Status);
            Assert.IsNull(typeof(MCPServiceLocator).GetField("_testRunnerService", StaticPrivate).GetValue(null));
            Assert.IsFalse(TestRunnerService.HasRetiredRun);
        }

        [Test]
        public async Task RetiredRun_DropsLateResultAdaptorsWithoutMaterializingThem()
        {
            SetSchedule(_ => "old");
            TestJobManager.StartJob(TestMode.EditMode);
            TestJobManager.ClearStuckJob();
            await ExpectFailure(_runner.LastRun, "Job cleared manually");
            var result = new UnexpectedResult();
            _service.TestFinished(result);
            var retained =
                (List<ITestResultAdaptor>)typeof(TestRunnerService).GetField("_leafResults", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service);
            Assert.AreEqual(0, retained.Count);
            _service.RunFinished(result);
            Assert.IsFalse(TestRunnerService.HasRetiredRun);
        }

        private sealed class UnexpectedResult : ITestResultAdaptor
        {
            private static Exception ReadError => new InvalidOperationException("A retired callback must not read result adaptors.");
            public string Name => throw ReadError;
            public string FullName => throw ReadError;
            public ITestAdaptor Test => throw ReadError;
            public string ResultState => throw ReadError;
            public TestStatus TestStatus => throw ReadError;
            public double Duration => throw ReadError;
            public DateTime StartTime => throw ReadError;
            public DateTime EndTime => throw ReadError;
            public string Message => throw ReadError;
            public string StackTrace => throw ReadError;
            public int AssertCount => throw ReadError;
            public int FailCount => throw ReadError;
            public int PassCount => throw ReadError;
            public int SkipCount => throw ReadError;
            public int InconclusiveCount => throw ReadError;
            public bool HasChildren => throw ReadError;
            public IEnumerable<ITestResultAdaptor> Children => throw ReadError;
            public string Output => throw ReadError;

            public TNode ToXml() => throw ReadError;
        }

        [Test]
        public void RetiredPlayModeRun_DelayedFinallyPreservesReplacementSettingsAndOriginalFailure()
        {
            var previous = SynchronizationContext.Current;
            var paused = new PausedContext();
            try
            {
                SynchronizationContext.SetSynchronizationContext(paused);
                EditorSettings.enterPlayModeOptionsEnabled = false;
                EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.None;
                SetSchedule(_ => "old");
                TestJobManager.StartJob(TestMode.PlayMode);
                var run = _runner.LastRun;
                var queuedDiscovery = _service.GetTestsAsync(TestMode.EditMode);
                var queuedRun = _service.RunTestsAsync(TestMode.EditMode);
                Assert.IsFalse(queuedDiscovery.IsCompleted);
                Assert.IsFalse(queuedRun.IsCompleted);
                TestJobManager.ClearStuckJob();
                Assert.IsFalse(EditorSettings.enterPlayModeOptionsEnabled);
                Assert.AreEqual(EnterPlayModeOptions.None, EditorSettings.enterPlayModeOptions);
                _service.Dispose();
                _service = new TestRunnerService();
                _service.RunFinished(null);
                EditorSettings.enterPlayModeOptionsEnabled = true;
                EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload;
                var deadline = System.Diagnostics.Stopwatch.StartNew();
                while ((!run.IsCompleted || !queuedDiscovery.IsCompleted || !queuedRun.IsCompleted) && deadline.ElapsedMilliseconds < 2000)
                {
                    paused.Drain();
                    Thread.Sleep(1);
                }
                StringAssert.Contains("Job cleared manually", ObserveFailure(run).Message);
                Assert.IsInstanceOf<OperationCanceledException>(ObserveFailure(queuedDiscovery), "Disposal must settle previously queued discovery.");
                Assert.IsInstanceOf<OperationCanceledException>(ObserveFailure(queuedRun), "Disposal must settle previously queued runs.");
                Assert.IsTrue(EditorSettings.enterPlayModeOptionsEnabled, "The retired caller must not restore settings a second time.");
                Assert.AreEqual(EnterPlayModeOptions.DisableDomainReload, EditorSettings.enterPlayModeOptions);
            }
            finally
            {
                SynchronizationContext.SetSynchronizationContext(previous);
            }
        }

        private sealed class PausedContext : SynchronizationContext
        {
            private readonly Queue<Action> _pending = new Queue<Action>();

            public override void Post(SendOrPostCallback callback, object state)
            {
                lock (_pending)
                    _pending.Enqueue(() => callback(state));
            }

            public void Drain()
            {
                while (true)
                {
                    Action next;
                    lock (_pending)
                    {
                        if (_pending.Count == 0)
                            return;
                        next = _pending.Dequeue();
                    }
                    next();
                }
            }
        }

        private static async Task ExpectFailure(Task task, string message)
        {
            Assert.AreSame(task, await Task.WhenAny(task, Task.Delay(2000)), "Retirement must settle its original caller without a Unity terminal callback.");
            try
            {
                await task;
                Assert.Fail("Expected run failure.");
            }
            catch (InvalidOperationException ex)
            {
                StringAssert.Contains(message, ex.Message);
            }
        }

        private sealed class CapturingRunner : ITestRunnerService
        {
            private readonly TestRunnerService _service;
            public Task<TestRunResult> LastRun { get; private set; }

            public CapturingRunner(TestRunnerService service) => _service = service;

            public Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions options = null) => LastRun = _service.RunTestsAsync(mode, options);

            public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode) => _service.GetTestsAsync(mode);
        }

        [Test]
        public void DisposedService_RejectsDiscoveryAndRunsSynchronously()
        {
            _service.Dispose();
            Assert.IsInstanceOf<ObjectDisposedException>(ObserveFailure(_service.GetTestsAsync(TestMode.EditMode)));
            Assert.IsInstanceOf<ObjectDisposedException>(ObserveFailure(_service.RunTestsAsync(TestMode.EditMode)));
        }

        [Test]
        public async Task SynchronousRunFinished_CompletesOriginalRunTask()
        {
            SetSchedule(_ =>
            {
                _service.RunFinished(null);
                return "finished-synchronously";
            });
            var result = await _service.RunTestsAsync(TestMode.EditMode);
            Assert.IsNotNull(result);
            Assert.AreEqual(0, result.Total);
            Assert.IsNull(typeof(TestRunnerService).GetField("_runCompletionSource", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(_service));
        }

        private static void SetCurrent(string id) => typeof(TestJobManager).GetField("_currentJobId", StaticPrivate).SetValue(null, id);

        private static Exception ObserveFailure(Task task)
        {
            Assert.IsTrue(task.IsCompleted, "A startup exception must complete synchronously without pumping Editor updates.");
            try
            {
                task.GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                return ex;
            }
            Assert.Fail("Expected the injected scheduling failure.");
            return null;
        }
    }
}
