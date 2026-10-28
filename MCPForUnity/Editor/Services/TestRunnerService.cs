using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Concrete implementation of <see cref="ITestRunnerService"/>.
    /// Coordinates Unity Test Runner operations and produces structured results.
    /// </summary>
    internal sealed class TestRunnerService : ITestRunnerService, IBudgetedTestDiscovery, IErrorCallbacks, IDisposable
    {
        private static readonly TestMode[] AllModes = { TestMode.EditMode, TestMode.PlayMode };

        private readonly TestRunnerApi _testRunnerApi;
        private readonly Action<TestMode, Action<ITestAdaptor>> _retrieveTestList;
        private readonly TimeSpan _retrievalTimeout;
        private readonly Action<CancellationTokenSource> _observeDiscoveryBudget;
        private readonly SemaphoreSlim _operationLock = new SemaphoreSlim(1, 1);
        private readonly CancellationTokenSource _lifetimeCancellation = new CancellationTokenSource();
        private readonly List<ITestResultAdaptor> _leafResults = new List<ITestResultAdaptor>();
        private TaskCompletionSource<TestRunResult> _runCompletionSource;
        private string _trackedJobId;
        private bool _disposed;
        private bool _apiReleased;
        private static TestRunnerService _activeRunOwner;
        private static readonly HashSet<PendingRunAdmission> PendingRunAdmissions = new();

        private sealed class PendingRunAdmission
        {
            internal string JobId;
            internal string RetirementError;
            internal CancellationTokenSource Cancellation;
        }

        private const string RetiredRunSessionKey = "MCPForUnity.TestRunner.RetiredRunJobId";

        private void ThrowIfDisposed()
        {
            if (_disposed)
                throw new ObjectDisposedException(nameof(TestRunnerService));
        }

        internal static bool HasRetiredRun => !string.IsNullOrEmpty(SessionState.GetString(RetiredRunSessionKey, string.Empty));
        internal static bool HasOwnedNativeRun => _activeRunOwner != null || HasRetiredRun;

        internal static void ThrowIfRetiredRunPending()
        {
            if (HasRetiredRun)
                throw new InvalidOperationException(
                    "The previous Unity test run was retired but has not stopped. Stop it in Test Runner or wait for its completion before retrying."
                );
        }

        internal static void RetireJob(string jobId, string message)
        {
            TestJobManager.RetireQueuedControl(jobId);
            lock (PendingRunAdmissions)
            {
                foreach (var pending in PendingRunAdmissions.Where(p => p.JobId == jobId).ToArray())
                {
                    pending.RetirementError ??= message;
                    pending.Cancellation.Cancel();
                }
            }
            var owner = _activeRunOwner;
            if (owner == null || owner._trackedJobId != jobId)
                return;

            // Fault the caller without transferring ownership of Unity's untagged callbacks.
            // SessionState preserves this quarantine if the managed domain reloads.
            SessionState.SetString(RetiredRunSessionKey, jobId);
            var completion = owner._runCompletionSource;
            owner._runCompletionSource = null;
            CheckpointObservedResults(jobId);
            owner._leafResults.Clear();
            TestRunnerNoThrottle.RestoreAfterRun();
            if (PlayModeOptionsGuard.IsPending)
                PlayModeOptionsGuard.Restore();
            completion?.TrySetException(new InvalidOperationException(message));
        }

        internal static void ConfirmNativeTermination(string jobId)
        {
            // Called only after the post-Dispose GUID witness and a later coherent idle check.
            var control = TestJobManager.ControlFor(jobId);
            if (control == null || !control.Released)
                return;
            var owner = _activeRunOwner;
            if (owner != null && owner._trackedJobId == jobId)
                owner.CompleteRun(null, new OperationCanceledException("Native test execution stopped."));
            if (SessionState.GetString(RetiredRunSessionKey, "") == jobId)
                SessionState.SetString(RetiredRunSessionKey, "");
        }

        public TestRunnerService()
            : this(null, TimeSpan.FromSeconds(OperationTimeouts.EnvironmentSeconds("UNITY_MCP_TEST_DISCOVERY_TIMEOUT", 300))) { }

        internal TestRunnerService(
            Action<TestMode, Action<ITestAdaptor>> retrieveTestList,
            TimeSpan retrievalTimeout,
            Action<CancellationTokenSource> observeDiscoveryBudget = null
        )
        {
            _testRunnerApi = ScriptableObject.CreateInstance<TestRunnerApi>();
            _retrieveTestList = retrieveTestList ?? _testRunnerApi.RetrieveTestList;
            _retrievalTimeout = retrievalTimeout;
            _observeDiscoveryBudget = observeDiscoveryBudget;
            _testRunnerApi.hideFlags = HideFlags.HideAndDontSave;
            _testRunnerApi.RegisterCallbacks(this);
            var retiredJobId = SessionState.GetString(RetiredRunSessionKey, string.Empty);
            if (!string.IsNullOrEmpty(retiredJobId))
            {
                _trackedJobId = retiredJobId;
                _activeRunOwner = this;
            }
            AssemblyReloadEvents.beforeAssemblyReload += ReleaseApi;
            EditorApplication.quitting += ReleaseApi;
        }

        internal void ResumeJobAfterReload(string jobId, string mode)
        {
            if (_runCompletionSource != null || _trackedJobId != null || string.IsNullOrEmpty(jobId) || TestJobManager.CurrentJobId != jobId)
            {
                return;
            }

            _trackedJobId = jobId;
            _activeRunOwner = this;
            if (Enum.TryParse<TestMode>(mode, out var testMode))
            {
                TestRunStatus.MarkStarted(testMode);
            }
        }

        private bool IsTrackingCurrentJob => !string.IsNullOrEmpty(_trackedJobId) && TestJobManager.CurrentJobId == _trackedJobId;

        public Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode) => GetTestsAsync(mode, _retrievalTimeout);

        public async Task<IReadOnlyList<Dictionary<string, string>>> GetTestsAsync(TestMode? mode, TimeSpan budget)
        {
            if (budget <= TimeSpan.Zero || budget.TotalSeconds > OperationTimeouts.MaximumSeconds)
                throw new ArgumentOutOfRangeException(nameof(budget));
            ThrowIfDisposed();
            ThrowIfRetiredRunPending();
            var lifetime = _lifetimeCancellation.Token;
            using var observation = CancellationTokenSource.CreateLinkedTokenSource(lifetime);
            observation.CancelAfter(budget);
            _observeDiscoveryBudget?.Invoke(observation);
            var cancellationToken = observation.Token;
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            bool entered = false;
            try
            {
                await _operationLock.WaitAsync(cancellationToken).ConfigureAwait(true);
                entered = true;
                cancellationToken.ThrowIfCancellationRequested();
                ThrowIfDisposed();
                ThrowIfRetiredRunPending();
                var modes = mode.HasValue ? new[] { mode.Value } : AllModes;

                var results = new List<Dictionary<string, string>>();
                var seen = new HashSet<string>(StringComparer.Ordinal);

                foreach (var m in modes)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var remaining = budget - elapsed.Elapsed;
                    if (remaining <= TimeSpan.Zero)
                        throw new TimeoutException("Test discovery observation budget expired.");
                    var root = await RetrieveTestRootAsync(m, cancellationToken, remaining).ConfigureAwait(true);
                    // An accepted callback wins a later observation timer while the main
                    // thread continuation is paused. Disposal still invalidates the result.
                    lifetime.ThrowIfCancellationRequested();
                    if (root != null)
                    {
                        CollectFromNode(root, m, results, seen, new List<string>());
                    }
                }

                return results;
            }
            catch (OperationCanceledException) when (!lifetime.IsCancellationRequested)
            {
                throw new TimeoutException("Test discovery observation budget expired.");
            }
            finally
            {
                if (entered && !_disposed)
                    _operationLock.Release();
            }
        }

        public async Task<TestRunResult> RunTestsAsync(TestMode mode, TestFilterOptions filterOptions = null)
        {
            ThrowIfDisposed();
            ThrowIfRetiredRunPending();
            // The pre-reload Task no longer exists, but its Unity run may still be active.
            // Clearing a job must not let a second run consume the first run's callbacks.
            if (_trackedJobId != null && _runCompletionSource == null)
            {
                throw new InvalidOperationException("A recovered Unity test run is still in progress.");
            }

            if (TestJobManager.OtherNativeAdmissionBlocked(TestJobManager.CurrentJobId))
                throw new InvalidOperationException("A Unity test run or native cleanup is already in progress.");
            TestJobManager.EnsureNativeControl(TestJobManager.CurrentJobId);
            // Bind ownership before discovery or another operation can delay admission.
            // A retired waiter must never inherit the manager's later current job.
            var admission = new PendingRunAdmission
            {
                JobId = TestJobManager.CurrentJobId,
                Cancellation = CancellationTokenSource.CreateLinkedTokenSource(_lifetimeCancellation.Token),
            };
            lock (PendingRunAdmissions)
                PendingRunAdmissions.Add(admission);
            try
            {
                await _operationLock.WaitAsync(admission.Cancellation.Token).ConfigureAwait(true);
            }
            catch (OperationCanceledException) when (admission.RetirementError != null)
            {
                throw new InvalidOperationException(admission.RetirementError);
            }
            finally
            {
                lock (PendingRunAdmissions)
                {
                    PendingRunAdmissions.Remove(admission);
                    admission.Cancellation.Dispose();
                }
            }
            return await RunAdmittedTestsAsync(mode, filterOptions, admission.JobId, admission.RetirementError).ConfigureAwait(true);
        }

        private async Task<TestRunResult> RunAdmittedTestsAsync(TestMode mode, TestFilterOptions filterOptions, string jobId, string retirementError)
        {
            Task<TestRunResult> runTask;
            TaskCompletionSource<TestRunResult> completionSource = null;
            bool nativeInvoked = false;
            var nativeControl = TestJobManager.ControlFor(jobId);
            bool appliedNoThrottling = false;
            try
            {
                ThrowIfDisposed();
                ThrowIfRetiredRunPending();
                nativeControl?.CheckAdmission();
                if (retirementError != null || (jobId != null && TestJobManager.CurrentJobId != jobId))
                    throw new InvalidOperationException(retirementError ?? "Test job was retired before execution started.");
                if (_runCompletionSource != null && !_runCompletionSource.Task.IsCompleted)
                {
                    throw new InvalidOperationException("A Unity test run is already in progress.");
                }

                if (EditorApplication.isPlaying || EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    throw new InvalidOperationException("Cannot start a test run while the Editor is in or entering Play Mode. Stop Play Mode and try again.");
                }

                // Persisted jobs and reload callbacks retain ownership across the project's
                // normal Play Mode transition. Do not alter its domain/scene reload semantics.
                _leafResults.Clear();
                completionSource = new TaskCompletionSource<TestRunResult>(TaskCreationOptions.RunContinuationsAsynchronously);
                _trackedJobId = jobId;
                _activeRunOwner = this;
                _runCompletionSource = completionSource;
                runTask = completionSource.Task;
                // Mark running immediately so readiness snapshots reflect the busy state even before callbacks fire.
                TestRunStatus.MarkStarted(mode);

                var filter = new Filter
                {
                    testMode = mode,
                    testNames = filterOptions?.TestNames,
                    groupNames = filterOptions?.GroupNames,
                    categoryNames = filterOptions?.CategoryNames,
                    assemblyNames = filterOptions?.AssemblyNames,
                };
                var settings = new ExecutionSettings(filter);

                // Save dirty scenes for all test modes to prevent modal dialogs blocking MCP
                // (Issue #525: EditMode tests were blocked by save dialog)
                SaveDirtyScenesIfNeeded();

                // EditMode also needs background Editor ticks before RunStarted is delivered.
                appliedNoThrottling = true;
                TestRunnerNoThrottle.ApplyNoThrottlingPreemptive();

                nativeControl?.CheckAdmission();
                // Recheck after queued admission and scene preparation, immediately before Execute.
                if (EditorStateCache.GetActualIsCompiling() || EditorApplication.isUpdating || EditorStateCache.IsDomainReloadPending)
                    throw new InvalidOperationException(
                        "Editor became busy before test execution; wait for compilation, import and reload to settle before retrying."
                    );
                nativeControl?.MarkExecuting();
                nativeInvoked = true;
                string nativeGuid = _testRunnerApi.Execute(settings);
                nativeControl?.BindGuid(nativeGuid);
            }
            catch
            {
                if (!nativeInvoked)
                    nativeControl?.AbortBeforeExecute();
                // Execute can throw before RunFinished is delivered. Release this run's
                // completion source so a later request can retry instead of staying busy.
                if (completionSource != null && _runCompletionSource == completionSource)
                {
                    CheckpointObservedResults(_trackedJobId);
                    _trackedJobId = null;
                    if (_activeRunOwner == this)
                        _activeRunOwner = null;
                    _runCompletionSource = null;
                    _leafResults.Clear();
                }
                if (appliedNoThrottling)
                {
                    TestRunnerNoThrottle.RestoreAfterRun();
                }
                // Ensure the status is cleared if we failed to start the run.
                TestRunStatus.MarkFinished();

                if (!_disposed)
                    _operationLock.Release();
                throw;
            }

            try
            {
                return await runTask.ConfigureAwait(true);
            }
            finally
            {
                if (!_disposed)
                    _operationLock.Release();
            }
        }

        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            _lifetimeCancellation.Cancel();
            if (_activeRunOwner == this)
            {
                if (_trackedJobId != null)
                    RetireJob(_trackedJobId, "Unity test service disposed before the run completed.");
                _activeRunOwner = null;
            }
            ReleaseApi();

            // WaitAsync removes canceled waiters asynchronously. Disposing a held semaphore
            // here can orphan them; no WaitHandle is used, so it can be collected after they unwind.
            if (_operationLock.CurrentCount > 0)
                _operationLock.Dispose();
            _lifetimeCancellation.Dispose();
        }

        internal static void CheckpointBeforeReload() => CheckpointObservedResults(TestJobManager.CurrentJobId);

        internal static void CheckpointObservedResults(string jobId)
        {
            var owner = _activeRunOwner;
            if (jobId != null && owner != null && owner._trackedJobId == jobId)
                TestJobManager.CheckpointObservedResults(jobId, owner._leafResults);
        }

        private void ReleaseApi()
        {
            AssemblyReloadEvents.beforeAssemblyReload -= ReleaseApi;
            EditorApplication.quitting -= ReleaseApi;
            if (_apiReleased)
                return;
            _apiReleased = true;

            // Reload must release the hidden API without retiring the persisted test run.
            try
            {
                _testRunnerApi?.UnregisterCallbacks(this);
            }
            catch
            {
                // Ignore cleanup errors
            }

            if (_testRunnerApi != null)
            {
                ScriptableObject.DestroyImmediate(_testRunnerApi);
            }
        }

        #region TestRunnerApi callbacks

        public void RunStarted(ITestAdaptor testsToRun)
        {
            if (!TestJobManager.ControlOwnsCallback(_trackedJobId))
                return;
            // RunStarted may be redelivered during recovery. Admission owns the initial reset.
            if (!IsTrackingCurrentJob)
            {
                return;
            }
            try
            {
                // Best-effort progress info for async polling (avoid heavy payloads).
                int? total = null;
                if (testsToRun != null)
                {
                    total = CountLeafTests(testsToRun);
                }
                TestJobManager.OnRunStarted(total);
            }
            catch
            {
                TestJobManager.OnRunStarted(null);
            }
        }

        public void RunFinished(ITestResultAdaptor result)
        {
            if (!TestJobManager.ControlOwnsCallback(_trackedJobId))
                return;
            if (_runCompletionSource == null && _trackedJobId == null)
            {
                return;
            }

            // Always create payload and clean up job state, even if _runCompletionSource is null.
            // This handles domain reload scenarios (e.g., PlayMode tests) where the TestRunnerService
            // is recreated and _runCompletionSource is lost, but TestJobManager state persists via
            // SessionState and the Test Runner still delivers the RunFinished callback.
            // Materialize the checkpoint before another immutable generation can evict it.
            var previous = IsTrackingCurrentJob ? TestJobManager.SnapshotResultsForCapture(_trackedJobId) : null;
            var payload =
                _runCompletionSource != null || IsTrackingCurrentJob
                    ? TestRunResult.Create(
                        result,
                        _leafResults,
                        () => TestJobManager.ResultStore,
                        _trackedJobId,
                        TestJobManager.ModeForCapture(_trackedJobId),
                        TestJobManager.ReloadGenerationForCapture(_trackedJobId)
                    )
                    : null;
            CompleteRun(payload, null, previous);
        }

        public void OnError(string message)
        {
            if (!TestJobManager.ControlOwnsCallback(_trackedJobId))
                return;
            // Build/prebuild failures use IErrorCallbacks instead of RunFinished.
            CompleteRun(null, new InvalidOperationException(message ?? "Unity test run failed."));
        }

        private void CompleteRun(TestRunResult payload, Exception error, TestRunResult previous = null)
        {
            // A late callback from a cleared job must not finish a newer job or restore
            // settings belonging to it. Callbacks do not contain an MCP job identifier.
            if (error != null)
                CheckpointObservedResults(_trackedJobId);
            bool ownsCurrentJob = IsTrackingCurrentJob;
            bool canCleanUp = ownsCurrentJob || !TestJobManager.HasRunningJob;
            if (ownsCurrentJob)
            {
                if (error == null)
                {
                    TestJobManager.OnRunFinished();
                    payload = TestJobManager.FinalizeCurrentJobFromRunFinished(payload, previous);
                }
                else
                {
                    TestJobManager.FinalizeCurrentJobFromRunError(error.Message);
                }
            }

            if (canCleanUp)
                TestRunStatus.MarkFinished(error == null ? payload?.Summary : null);

            // Complete recovery for a run admitted by an older package before the domain reload.
            // New runs retain the project's configured Enter Play Mode settings.
            if (canCleanUp && _trackedJobId != null && _runCompletionSource == null && PlayModeOptionsGuard.IsPending)
            {
                PlayModeOptionsGuard.Restore();
            }

            // Report result to awaiting caller if we have a completion source.
            var completion = _runCompletionSource;
            _runCompletionSource = null;
            if (SessionState.GetString(RetiredRunSessionKey, string.Empty) == _trackedJobId)
                SessionState.SetString(RetiredRunSessionKey, string.Empty);
            _trackedJobId = null;
            if (_activeRunOwner == this)
                _activeRunOwner = null;
            // The payload owns materialized result values, not Unity's adaptor trees.
            _leafResults.Clear();
            if (completion != null)
            {
                if (error == null)
                    completion.TrySetResult(payload);
                else
                    completion.TrySetException(error);
            }
        }

        public void TestStarted(ITestAdaptor test)
        {
            if (!TestJobManager.ControlOwnsCallback(_trackedJobId))
                return;
            if (!IsTrackingCurrentJob)
            {
                return;
            }
            try
            {
                // Prefer FullName for uniqueness; fall back to Name.
                string fullName = test?.FullName;
                if (string.IsNullOrWhiteSpace(fullName))
                {
                    fullName = test?.Name;
                }
                TestJobManager.OnTestStarted(fullName);
            }
            catch
            {
                // ignore
            }
        }

        public void TestFinished(ITestResultAdaptor result)
        {
            if (!TestJobManager.ControlOwnsCallback(_trackedJobId))
                return;
            if (result == null || (_runCompletionSource == null && !IsTrackingCurrentJob))
            {
                return;
            }

            if (!result.HasChildren && result.Test?.IsSuite != true)
            {
                _leafResults.Add(result);
                try
                {
                    string fullName = result.Test?.FullName;
                    if (string.IsNullOrWhiteSpace(fullName))
                    {
                        fullName = result.Test?.Name;
                    }

                    bool isFailure = false;
                    string message = null;
                    try
                    {
                        // NUnit outcomes are strings in the adaptor; keep it simple.
                        string outcome = result.ResultState;
                        if (!string.IsNullOrWhiteSpace(outcome))
                        {
                            var o = outcome.Trim().ToLowerInvariant();
                            isFailure = o.Contains("failed") || o.Contains("error");
                        }
                        message = result.Message;
                    }
                    catch
                    {
                        // ignore adaptor quirks
                    }

                    if (IsTrackingCurrentJob)
                    {
                        TestJobManager.OnLeafTestFinished(fullName, isFailure, message);
                    }
                }
                catch
                {
                    // ignore
                }
            }
        }

        #endregion

        private static int CountLeafTests(ITestAdaptor node)
        {
            if (node == null)
            {
                return 0;
            }

            if (!node.HasChildren)
            {
                return 1;
            }

            int total = 0;
            try
            {
                foreach (var child in node.Children)
                {
                    total += CountLeafTests(child);
                }
            }
            catch
            {
                // If Unity changes the adaptor behavior, treat it as "unknown total".
                return 0;
            }

            return total;
        }

        private static void SaveDirtyScenesIfNeeded()
        {
            int sceneCount = SceneManager.sceneCount;
            for (int i = 0; i < sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.isDirty)
                {
                    if (string.IsNullOrEmpty(scene.path))
                    {
                        McpLog.Warn($"[TestRunnerService] Skipping unsaved scene '{scene.name}': save it manually before running tests.");
                        continue;
                    }
                    try
                    {
                        EditorSceneManager.SaveScene(scene);
                    }
                    catch (Exception ex)
                    {
                        McpLog.Warn($"[TestRunnerService] Failed to save dirty scene '{scene.name}': {ex.Message}");
                    }
                }
            }
        }

        #region Test list helpers

        private async Task<ITestAdaptor> RetrieveTestRootAsync(TestMode mode, CancellationToken cancellationToken, TimeSpan remaining)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var completion = new TaskCompletionSource<ITestAdaptor>(TaskCreationOptions.RunContinuationsAsynchronously);
            using var registration = cancellationToken.Register(() => completion.TrySetCanceled());
            using var timeout = new Timer(
                _ => completion.TrySetException(new TimeoutException($"Timed out retrieving the {mode} test list.")),
                null,
                remaining,
                Timeout.InfiniteTimeSpan
            );
            try
            {
                cancellationToken.ThrowIfCancellationRequested();
                _retrieveTestList(mode, root => completion.TrySetResult(root));
                // Ensure the editor pumps at least one additional update in case the window is unfocused.
                EditorApplication.QueuePlayerLoopUpdate();
                return await completion.Task.ConfigureAwait(true);
            }
            catch (Exception ex)
            {
                // Startup can throw after the timer won. Settle and observe the owned task
                // before rethrowing so no unawaited timeout fault remains.
                completion.TrySetException(ex);
                _ = completion.Task.Exception;
                if (ex is TimeoutException)
                    McpLog.Warn($"[TestRunnerService] Timeout waiting for test retrieval callback for {mode}");
                throw;
            }
        }

        private static void CollectFromNode(ITestAdaptor node, TestMode mode, List<Dictionary<string, string>> output, HashSet<string> seen, List<string> path)
        {
            if (node == null)
            {
                return;
            }

            bool hasName = !string.IsNullOrEmpty(node.Name);
            if (hasName)
            {
                path.Add(node.Name);
            }

            bool hasChildren = node.HasChildren && node.Children != null;

            if (!node.IsSuite)
            {
                string fullName = string.IsNullOrEmpty(node.FullName) ? node.Name ?? string.Empty : node.FullName;
                string key = $"{mode}:{fullName}";

                if (!string.IsNullOrEmpty(fullName) && seen.Add(key))
                {
                    string computedPath = path.Count > 0 ? string.Join("/", path) : fullName;
                    output.Add(
                        new Dictionary<string, string>
                        {
                            ["name"] = node.Name ?? fullName,
                            ["full_name"] = fullName,
                            ["path"] = computedPath,
                            ["mode"] = mode.ToString(),
                        }
                    );
                }
            }
            else if (hasChildren)
            {
                foreach (var child in node.Children)
                {
                    CollectFromNode(child, mode, output, seen, path);
                }
            }

            if (hasName && path.Count > 0)
            {
                path.RemoveAt(path.Count - 1);
            }
        }

        #endregion
    }

    /// <summary>
    /// Summary of a Unity test run.
    /// </summary>
    public sealed class TestRunResult
    {
        internal TestRunResult(TestRunSummary summary, IReadOnlyList<TestRunTestResult> results)
        {
            Summary = summary;
            Results = results;
        }

        public TestRunSummary Summary { get; }
        public IReadOnlyList<TestRunTestResult> Results { get; }
        internal Newtonsoft.Json.Linq.JObject Artifact { get; set; }
        internal string Availability { get; set; }

        public int Total => Summary.Total;
        public int Passed => Summary.Passed;
        public int Failed => Summary.Failed;
        public int Skipped => Summary.Skipped;

        public object ToSerializable(string mode, bool includeDetails = false, bool includeFailedTests = false)
        {
            // Determine which results to include
            IEnumerable<object> resultsToSerialize;
            if (includeDetails)
            {
                // Include all test results
                resultsToSerialize = Results.Select(r => r.ToSerializable());
            }
            else if (includeFailedTests)
            {
                // Include only failed and skipped tests
                resultsToSerialize = Results.Where(r => !string.Equals(r.State, "Passed", StringComparison.OrdinalIgnoreCase)).Select(r => r.ToSerializable());
            }
            else
            {
                // No individual test results
                resultsToSerialize = null;
            }

            return new
            {
                mode,
                summary = Summary.ToSerializable(),
                results = resultsToSerialize?.ToList(),
            };
        }

        internal static TestRunResult Create(
            ITestResultAdaptor summary,
            IReadOnlyList<ITestResultAdaptor> tests,
            Func<TestJobResultStore> storeFactory = null,
            string jobId = null,
            string mode = null,
            int identityGeneration = 0
        )
        {
            var authoritative =
                summary == null
                    ? null
                    : new TestRunSummary(
                        summary.PassCount + summary.FailCount + summary.SkipCount,
                        summary.PassCount,
                        summary.FailCount,
                        summary.SkipCount,
                        summary.Duration,
                        summary.ResultState ?? "Unknown"
                    );
            try
            {
                var store = storeFactory?.Invoke();
                var rows = CaptureLeaves(summary, tests, identityGeneration);
                if (store != null && jobId != null && authoritative != null)
                {
                    var descriptor = store.SaveCapture(jobId, mode, authoritative, rows, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
                    return new TestRunResult(authoritative, store.Results(jobId, descriptor)) { Artifact = descriptor, Availability = "available" };
                }
                var materialized = rows.ToList();
                return new TestRunResult(
                    authoritative
                        ?? new TestRunSummary(
                            materialized.Count,
                            materialized.Count(t => t.State == "Passed"),
                            materialized.Count(t => t.State == "Failed"),
                            materialized.Count(t => t.State == "Skipped"),
                            materialized.Sum(t => t.DurationSeconds),
                            "Unknown"
                        ),
                    materialized
                );
            }
            catch (Exception error)
            {
                if (authoritative == null)
                    throw;
                string reason =
                    error is System.IO.PathTooLongException ? "path_too_long"
                    : error is System.IO.InvalidDataException && error.Message == "path_limit_exceeded" ? "path_limit_exceeded"
                    : error.Message == "traversal_limit" ? "traversal_limit"
                    : error.Message == "quota_exceeded" ? "quota_exceeded"
                    : "write_failed";
                return new TestRunResult(authoritative, Array.Empty<TestRunTestResult>()) { Availability = reason };
            }
        }

        private static IEnumerable<TestRunTestResult> CaptureLeaves(
            ITestResultAdaptor summary,
            IReadOnlyList<ITestResultAdaptor> fallback,
            int identityGeneration
        )
        {
            // Managed visit/depth limits do not preempt work inside Unity/native getters.
            var stack = new Stack<(IEnumerator<ITestResultAdaptor> Nodes, int Depth)>();
            int visited = 0,
                leaves = 0;
            IEnumerable<ITestResultAdaptor> initial = summary != null && summary.HasChildren ? new[] { summary } : fallback;
            stack.Push((initial.GetEnumerator(), 0));
            try
            {
                while (stack.Count > 0)
                {
                    var frame = stack.Peek();
                    if (!frame.Nodes.MoveNext())
                    {
                        frame.Nodes.Dispose();
                        stack.Pop();
                        continue;
                    }
                    if (++visited > 50000 || frame.Depth > 128)
                        throw new System.IO.InvalidDataException("traversal_limit");
                    var node = frame.Nodes.Current;
                    if (node == null)
                        continue;
                    if (node.HasChildren)
                    {
                        var children = node.Children;
                        if (children != null)
                            stack.Push((children.GetEnumerator(), frame.Depth + 1));
                    }
                    else if (node.Test?.IsSuite != true)
                    {
                        if (++leaves > 10000)
                            throw new System.IO.InvalidDataException("quota_exceeded");
                        yield return TestRunTestResult.FromAdaptor(node, identityGeneration);
                    }
                }
            }
            finally
            {
                while (stack.Count > 0)
                    stack.Pop().Nodes.Dispose();
            }
        }
    }

    public sealed class TestRunSummary
    {
        [Newtonsoft.Json.JsonConstructor]
        internal TestRunSummary(int total, int passed, int failed, int skipped, double durationSeconds, string resultState)
        {
            Total = total;
            Passed = passed;
            Failed = failed;
            Skipped = skipped;
            DurationSeconds = durationSeconds;
            ResultState = resultState;
        }

        public int Total { get; }
        public int Passed { get; }
        public int Failed { get; }
        public int Skipped { get; }
        public double DurationSeconds { get; }
        public string ResultState { get; }

        internal object ToSerializable()
        {
            return new
            {
                total = Total,
                passed = Passed,
                failed = Failed,
                skipped = Skipped,
                durationSeconds = DurationSeconds,
                resultState = ResultState,
            };
        }
    }

    public sealed class TestRunTestResult
    {
        internal TestRunTestResult(
            string name,
            string fullName,
            string state,
            double durationSeconds,
            string message,
            string stackTrace,
            string output,
            string testId = null,
            string reloadIdentity = null,
            int identityGeneration = 0
        )
        {
            Name = name;
            FullName = fullName;
            State = state;
            DurationSeconds = durationSeconds;
            Message = message;
            StackTrace = stackTrace;
            Output = output;
            TestId = testId;
            ReloadIdentity = reloadIdentity;
            IdentityGeneration = identityGeneration;
        }

        public string TestId { get; }
        internal string ReloadIdentity { get; }
        internal int IdentityGeneration { get; }
        internal bool IsFailure =>
            State?.IndexOf("fail", StringComparison.OrdinalIgnoreCase) >= 0 || State?.IndexOf("error", StringComparison.OrdinalIgnoreCase) >= 0;
        public string Name { get; }
        public string FullName { get; }
        public string State { get; }
        public double DurationSeconds { get; }
        public string Message { get; }
        public string StackTrace { get; }
        public string Output { get; }

        internal object ToSerializable()
        {
            return new
            {
                testId = TestId,
                reloadIdentity = ReloadIdentity,
                identityGeneration = IdentityGeneration,
                name = Name,
                fullName = FullName,
                state = State,
                durationSeconds = DurationSeconds,
                message = Message,
                stackTrace = StackTrace,
                output = Output,
            };
        }

        internal static TestRunTestResult FromAdaptor(ITestResultAdaptor adaptor, int identityGeneration = 0)
        {
            if (adaptor == null)
            {
                return new TestRunTestResult(string.Empty, string.Empty, "Unknown", 0.0, string.Empty, string.Empty, string.Empty);
            }

            return new TestRunTestResult(
                adaptor.Name,
                adaptor.FullName,
                adaptor.ResultState,
                adaptor.Duration,
                adaptor.Message,
                adaptor.StackTrace,
                adaptor.Output,
                Identity(adaptor.Test?.Id),
                Identity(adaptor.Test?.UniqueName),
                identityGeneration
            );
        }

        private static string Identity(string value)
        {
            if (string.IsNullOrEmpty(value))
                return null;
            using var hash = SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant();
        }
    }
}
