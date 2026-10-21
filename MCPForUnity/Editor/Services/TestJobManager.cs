using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEditorInternal;

namespace MCPForUnity.Editor.Services
{
    internal enum TestJobStatus
    {
        Running,
        Succeeded,
        Failed,
        Cancelled,
    }

    internal sealed class TestJobFailure
    {
        public string FullName { get; set; }
        public string Message { get; set; }
    }

    internal sealed class TestJob
    {
        public string JobId { get; set; }
        public TestJobStatus Status { get; set; }
        public string Mode { get; set; }
        public long StartedUnixMs { get; set; }
        public long? FinishedUnixMs { get; set; }
        public long LastUpdateUnixMs { get; set; }
        public int? TotalTests { get; set; }
        public int CompletedTests { get; set; }
        public string CurrentTestFullName { get; set; }
        public long? CurrentTestStartedUnixMs { get; set; }
        public string LastFinishedTestFullName { get; set; }
        public long? LastFinishedUnixMs { get; set; }
        public List<TestJobFailure> FailuresSoFar { get; set; }
        public string Error { get; set; }
        public TestRunResult Result { get; set; }
        public long InitTimeoutMs { get; set; }
        public TestRunSummary RetainedSummary { get; set; }
        public JObject ResultArtifact { get; set; }
        public string CompletionKind { get; set; }
        public string ResultAvailability { get; set; }
        public JObject NativeControl { get; set; }
        internal NativeTestRunControl Control;
    }

    /// <summary>
    /// Tracks async test jobs started via MCP tools. This is not intended to capture manual Test Runner UI runs.
    /// </summary>
    [InitializeOnLoad]
    internal static class TestJobManager
    {
        // Keep this small to avoid ballooning payloads during polling.
        private const int FailureCap = 25;
        private const long StuckThresholdMs = 60_000;
        private const long DefaultInitializationTimeoutMs = 120_000; // Large-project startup; override per job via init_timeout.
        private const long MaxInitializationTimeoutMs = 3_600_000; // Explicit initialization policy, separate from execution.
        private const int MaxJobsToKeep = 10;
        private const long MinPersistIntervalMs = 1000; // Throttle persistence to reduce overhead

        // SessionState survives domain reloads within the same Unity Editor session.
        private const string SessionKeyJobs = "MCPForUnity.TestJobsV1";
        private const string SessionKeyCurrentJobId = "MCPForUnity.CurrentTestJobIdV1";

        private static readonly object LockObj = new();
        private static readonly Dictionary<string, TestJob> Jobs = new();
        private static string _currentJobId;
        private static long _lastPersistUnixMs;
        private static bool _controlMonitor;
        private static TestJobResultStore ResultStoreOverride = null;
        private static TestJobResultStore _resultStore;
        internal static TestJobResultStore ResultStore
        {
            get
            {
                if (ResultStoreOverride != null)
                    return ResultStoreOverride;
                if (_resultStore != null)
                    return _resultStore;
                const string key = "MCPForUnity.TestResultSessionV1";
                string id = SessionState.GetString(key, "");
                if (string.IsNullOrEmpty(id))
                {
                    id = Guid.NewGuid().ToString("N");
                    SessionState.SetString(key, id);
                }
                return _resultStore = new TestJobResultStore(Directory.GetParent(UnityEngine.Application.dataPath).FullName, id);
            }
        }

        static TestJobManager()
        {
            // Restore after domain reloads (e.g., compilation while a job is running).
            TryRestoreFromSessionState();
            AssemblyReloadEvents.beforeAssemblyReload += BeforeAssemblyReload;
            RestoreRunningJobCallbacks();
        }

        private static void BeforeAssemblyReload()
        {
            // Progress callbacks are normally throttled. Flush the last update before the
            // managed domain (and the original RunTestsAsync task) is discarded.
            PersistToSessionState(force: true);
            foreach (var job in Jobs.Values)
                job.Control?.Dispose();
        }

        private static void RestoreRunningJobCallbacks()
        {
            try
            {
                if (TestRunnerService.HasRetiredRun)
                {
                    // A timed-out caller is gone, but Unity can still deliver its terminal callback.
                    // Recreate the callback owner even though its manager record is already failed.
                    _ = MCPServiceLocator.Tests;
                    return;
                }
                TestJob job;
                lock (LockObj)
                {
                    if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out job) || job.Status != TestJobStatus.Running)
                    {
                        return;
                    }
                }

                // Polling a restored job never otherwise touches the lazy test service.
                // Re-register callbacks now, before the Test Runner resumes after reload.
                if (MCPServiceLocator.Tests is TestRunnerService service)
                {
                    service.ResumeJobAfterReload(job.JobId, job.Mode);
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[TestJobManager] Failed to restore test callbacks: {ex.Message}");
            }
        }

        public static string CurrentJobId
        {
            get
            {
                lock (LockObj)
                    return _currentJobId;
            }
        }

        public static bool HasRunningJob
        {
            get
            {
                lock (LockObj)
                {
                    return !string.IsNullOrEmpty(_currentJobId);
                }
            }
        }

        /// <summary>
        /// Force-clears any stuck or orphaned test job. Call this when tests get stuck due to
        /// assembly reloads or other interruptions.
        /// </summary>
        /// <returns>True if a job was cleared, false if no running job exists.</returns>
        public static bool ClearStuckJob()
        {
            bool cleared = false;
            string retiredJobId;
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId))
                {
                    return false;
                }

                retiredJobId = _currentJobId;
                if (Jobs.TryGetValue(_currentJobId, out var job) && job.Status == TestJobStatus.Running)
                {
                    long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    job.Status = TestJobStatus.Failed;
                    job.Error = "Job cleared manually (stuck or orphaned)";
                    job.CompletionKind = "interrupted";
                    job.FinishedUnixMs = now;
                    job.LastUpdateUnixMs = now;
                    McpLog.Warn($"[TestJobManager] Manually cleared stuck job {_currentJobId}");
                    cleared = true;
                }

                _currentJobId = null;
            }
            TestRunnerService.RetireJob(retiredJobId, "Job cleared manually (stuck or orphaned)");
            PersistToSessionState(force: true);
            return cleared;
        }

        private sealed class PersistedState
        {
            public int version { get; set; } = 2;
            public string current_job_id { get; set; }
            public List<PersistedJob> jobs { get; set; }
        }

        private sealed class PersistedJob
        {
            public string job_id { get; set; }
            public string status { get; set; }
            public string mode { get; set; }
            public long started_unix_ms { get; set; }
            public long? finished_unix_ms { get; set; }
            public long last_update_unix_ms { get; set; }
            public int? total_tests { get; set; }
            public int completed_tests { get; set; }
            public string current_test_full_name { get; set; }
            public long? current_test_started_unix_ms { get; set; }
            public string last_finished_test_full_name { get; set; }
            public long? last_finished_unix_ms { get; set; }
            public List<TestJobFailure> failures_so_far { get; set; }
            public string error { get; set; }
            public long init_timeout_ms { get; set; }
            public TestRunSummary summary { get; set; }
            public JObject artifact { get; set; }
            public string completion_kind { get; set; }
            public string result_availability { get; set; }
            public int omitted_failure_previews { get; set; }
            public JObject native_control { get; set; }
        }

        private static TestJobStatus ParseStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
            {
                return TestJobStatus.Running;
            }

            string s = status.Trim().ToLowerInvariant();
            return s switch
            {
                "succeeded" => TestJobStatus.Succeeded,
                "failed" => TestJobStatus.Failed,
                "cancelled" => TestJobStatus.Cancelled,
                _ => TestJobStatus.Running,
            };
        }

        private static bool Integer(JToken value, long maximum = long.MaxValue)
        {
            if (value?.Type != JTokenType.Integer)
                return false;
            try
            {
                long number = (long)value;
                return number >= 0 && number <= maximum;
            }
            catch (OverflowException)
            {
                return false;
            }
        }

        private static bool ValidSummary(JToken token)
        {
            if (token is not JObject value)
                return false;
            JToken Field(string name) => value.Properties().FirstOrDefault(p => string.Equals(p.Name, name, StringComparison.OrdinalIgnoreCase))?.Value;
            foreach (string name in new[] { "total", "passed", "failed", "skipped" })
                if (!Integer(Field(name), int.MaxValue))
                    return false;
            var duration = Field("durationSeconds");
            bool supportedDuration =
                duration?.Type == JTokenType.Integer
                || duration?.Type == JTokenType.Float
                || duration?.Type == JTokenType.String && ((string)duration == "NaN" || (string)duration == "Infinity" || (string)duration == "-Infinity");
            return supportedDuration && Field("resultState")?.Type == JTokenType.String && Encoding.UTF8.GetByteCount((string)Field("resultState")) <= 256;
        }

        private static PersistedState ReadHistory(string json)
        {
            if (Encoding.UTF8.GetByteCount(json) > 48 * 1024)
                throw new InvalidDataException("history byte limit");
            using var reader = new JsonTextReader(new StringReader(json)) { MaxDepth = 32, DateParseHandling = DateParseHandling.None };
            var envelope = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
            if (reader.Read() || envelope["version"] != null && (!Integer(envelope["version"], 2) || (int)envelope["version"] < 1))
                throw new InvalidDataException("unsupported history schema");
            if (envelope["jobs"] is not JArray rows || rows.Count > MaxJobsToKeep)
                throw new InvalidDataException("history count limit");
            var state = new PersistedState
            {
                current_job_id = envelope["current_job_id"]?.Type == JTokenType.String ? (string)envelope["current_job_id"] : null,
                jobs = new List<PersistedJob>(),
            };
            foreach (var token in rows)
            {
                if (token is not JObject source)
                    continue;
                try
                {
                    var row = (JObject)source.DeepClone();
                    string id = row["job_id"]?.Type == JTokenType.String ? (string)row["job_id"] : null;
                    string status = row["status"]?.Type == JTokenType.String ? ((string)row["status"]).Trim().ToLowerInvariant() : null;
                    if (
                        string.IsNullOrWhiteSpace(id)
                        || Encoding.UTF8.GetByteCount(id) > 256
                        || status != "running" && status != "failed" && status != "succeeded" && status != "cancelled"
                        || !Integer(row["started_unix_ms"])
                        || !Integer(row["last_update_unix_ms"])
                        || !Integer(row["completed_tests"], int.MaxValue)
                    )
                        continue;
                    bool corrupt = false;
                    if (row["summary"] != null && row["summary"].Type != JTokenType.Null && !ValidSummary(row["summary"]))
                    {
                        row["summary"] = null;
                        corrupt = true;
                    }
                    if (row["artifact"] != null && row["artifact"].Type != JTokenType.Null && !TestJobResultStore.ValidDescriptor(row["artifact"], id))
                    {
                        row["artifact"] = null;
                        corrupt = true;
                    }
                    if (
                        row["native_control"] != null
                        && row["native_control"].Type != JTokenType.Null
                        && !NativeTestRunControl.ValidState(row["native_control"], id)
                    )
                    {
                        // A corrupt optional flag is not proof of native termination.
                        // Keep the authoritative outcome, but retain an unknown owner barrier.
                        row["native_control"] = NativeTestRunControl.CorruptState(id);
                        corrupt = true;
                    }
                    if (corrupt)
                        row["result_availability"] = "corrupt";
                    if (Encoding.UTF8.GetByteCount(row.ToString(Formatting.None)) > 4096)
                    {
                        row["artifact"] = null;
                        row["failures_so_far"] = new JArray();
                        row["error"] = null;
                        row["current_test_full_name"] = null;
                        row["last_finished_test_full_name"] = null;
                        row["result_availability"] = "corrupt";
                    }
                    if (Encoding.UTF8.GetByteCount(row.ToString(Formatting.None)) > 4096)
                        continue;
                    state.jobs.Add(row.ToObject<PersistedJob>());
                }
                catch (Exception error)
                    when (error is JsonException || error is ArgumentException || error is OverflowException || error is InvalidCastException) { }
            }
            return state;
        }

        private static void TryRestoreFromSessionState()
        {
            try
            {
                string json = SessionState.GetString(SessionKeyJobs, string.Empty);
                if (string.IsNullOrWhiteSpace(json))
                {
                    var legacy = SessionState.GetString(SessionKeyCurrentJobId, string.Empty);
                    _currentJobId = string.IsNullOrWhiteSpace(legacy) ? null : legacy;
                    return;
                }

                var state = ReadHistory(json);
                if (state?.jobs == null)
                {
                    return;
                }

                lock (LockObj)
                {
                    Jobs.Clear();
                    foreach (var pj in state.jobs)
                    {
                        if (pj == null || string.IsNullOrWhiteSpace(pj.job_id))
                        {
                            continue;
                        }

                        Jobs[pj.job_id] = new TestJob
                        {
                            JobId = pj.job_id,
                            Status = ParseStatus(pj.status),
                            Mode = pj.mode,
                            StartedUnixMs = pj.started_unix_ms,
                            FinishedUnixMs = pj.finished_unix_ms,
                            LastUpdateUnixMs = pj.last_update_unix_ms,
                            TotalTests = pj.total_tests,
                            CompletedTests = pj.completed_tests,
                            CurrentTestFullName = pj.current_test_full_name,
                            CurrentTestStartedUnixMs = pj.current_test_started_unix_ms,
                            LastFinishedTestFullName = pj.last_finished_test_full_name,
                            LastFinishedUnixMs = pj.last_finished_unix_ms,
                            FailuresSoFar = pj.failures_so_far ?? new List<TestJobFailure>(),
                            Error = pj.error,
                            InitTimeoutMs = pj.init_timeout_ms,
                            RetainedSummary = pj.summary,
                            ResultArtifact = pj.artifact,
                            CompletionKind = pj.completion_kind,
                            ResultAvailability = pj.result_availability ?? "legacy_unavailable",
                            NativeControl = pj.native_control,
                            Result = null,
                        };
                    }

                    foreach (var restored in Jobs.Values)
                    {
                        if (restored.NativeControl != null && (bool?)restored.NativeControl["settled"] != true)
                        {
                            restored.Control = NativeTestRunControl.Restore(restored.JobId, restored.NativeControl);
                            if (restored.Control != null)
                                BindControl(restored);
                        }
                    }

                    _currentJobId = string.IsNullOrWhiteSpace(state.current_job_id) ? null : state.current_job_id;
                    if (!string.IsNullOrEmpty(_currentJobId) && !Jobs.ContainsKey(_currentJobId))
                    {
                        _currentJobId = null;
                    }

                    // Detect and clean up stale "running" jobs that were orphaned by domain reload.
                    // After a domain reload, TestRunStatus resets to not-running, but _currentJobId
                    // may still be set. If the job hasn't been updated recently, it's likely orphaned.
                    if (!string.IsNullOrEmpty(_currentJobId) && Jobs.TryGetValue(_currentJobId, out var currentJob))
                    {
                        // Managed native ownership survives history age. Its captured GUID, final
                        // progress and idle observer decide termination; wall clock is not evidence.
                        if (currentJob.Status == TestJobStatus.Running && currentJob.Control == null)
                        {
                            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                            long staleCutoffMs = 5 * 60 * 1000; // 5 minutes
                            if (now - currentJob.LastUpdateUnixMs > staleCutoffMs)
                            {
                                McpLog.Warn(
                                    $"[TestJobManager] Clearing stale job {_currentJobId} (last update {(now - currentJob.LastUpdateUnixMs) / 1000}s ago)"
                                );
                                currentJob.Status = TestJobStatus.Failed;
                                currentJob.Error = "Job orphaned after domain reload";
                                currentJob.FinishedUnixMs = now;
                                _currentJobId = null;
                            }
                        }
                    }
                }
            }
            catch (Exception)
            {
                // Restoration is best-effort; never block editor load.
                McpLog.Warn("[TestJobManager] Stored job history could not be restored.");
            }
        }

        private static string Preview(string text) => text == null ? null : TestJobResultStore.BoundedText(text, 256, out _);

        private static bool PersistToSessionState(bool force = false)
        {
            OperationActivity.Invalidate();
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            // Throttle non-critical updates to reduce overhead during large test runs
            if (!force && (now - _lastPersistUnixMs) < MinPersistIntervalMs)
            {
                return true;
            }

            try
            {
                PersistedState snapshot;
                lock (LockObj)
                {
                    // Bound completed results in memory as well as SessionState. An active
                    // run must survive even when it is older than the completed history.
                    if (Jobs.Count > MaxJobsToKeep)
                    {
                        var expiredIds = Jobs
                            .Values.Where(j => j.Status != TestJobStatus.Running && (j.NativeControl == null || (bool?)j.NativeControl["released"] == true))
                            .OrderBy(j => j.LastUpdateUnixMs)
                            .Take(Jobs.Count - MaxJobsToKeep)
                            .Select(j => j.JobId)
                            .ToList();
                        foreach (string id in expiredIds)
                            Jobs.Remove(id);
                    }
                    var jobs = Jobs
                        .Values.OrderByDescending(j => j.LastUpdateUnixMs)
                        .Select(j => new PersistedJob
                        {
                            job_id = j.JobId,
                            status = j.Status.ToString().ToLowerInvariant(),
                            mode = j.Mode,
                            started_unix_ms = j.StartedUnixMs,
                            finished_unix_ms = j.FinishedUnixMs,
                            last_update_unix_ms = j.LastUpdateUnixMs,
                            total_tests = j.TotalTests,
                            completed_tests = j.CompletedTests,
                            current_test_full_name = Preview(j.CurrentTestFullName),
                            current_test_started_unix_ms = j.CurrentTestStartedUnixMs,
                            last_finished_test_full_name = Preview(j.LastFinishedTestFullName),
                            last_finished_unix_ms = j.LastFinishedUnixMs,
                            failures_so_far = (j.FailuresSoFar ?? new List<TestJobFailure>())
                                .Take(FailureCap)
                                .Select(f => new TestJobFailure { FullName = Preview(f.FullName), Message = Preview(f.Message) })
                                .ToList(),
                            error = Preview(j.Error),
                            init_timeout_ms = j.InitTimeoutMs,
                            summary = j.RetainedSummary,
                            artifact = j.ResultArtifact,
                            completion_kind = j.CompletionKind,
                            result_availability = j.ResultAvailability,
                            native_control = j.NativeControl,
                        })
                        .ToList();

                    foreach (var persisted in jobs)
                    {
                        while (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(persisted)) > 4096 && persisted.failures_so_far.Count > 0)
                        {
                            persisted.failures_so_far.RemoveAt(persisted.failures_so_far.Count - 1);
                            persisted.omitted_failure_previews++;
                        }
                        if (Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(persisted)) > 4096)
                        {
                            persisted.current_test_full_name = null;
                            persisted.last_finished_test_full_name = null;
                            persisted.error = null;
                        }
                    }
                    if (jobs.Any(row => Encoding.UTF8.GetByteCount(JsonConvert.SerializeObject(row)) > 4096))
                        throw new InvalidDataException("Test job row exceeds its byte limit.");
                    snapshot = new PersistedState { current_job_id = _currentJobId, jobs = jobs };
                }

                string encoded = JsonConvert.SerializeObject(snapshot);
                if (Encoding.UTF8.GetByteCount(encoded) > 48 * 1024)
                    throw new InvalidDataException("Test job metadata exceeds its byte limit.");
                SessionState.SetString(SessionKeyCurrentJobId, snapshot.current_job_id ?? string.Empty);
                SessionState.SetString(SessionKeyJobs, encoded);
                if (SessionState.GetString(SessionKeyJobs, "") != encoded)
                    return false;
                _lastPersistUnixMs = now;
                return true;
            }
            catch (Exception)
            {
                McpLog.Warn("[TestJobManager] Failed to persist bounded job metadata.");
                return false;
            }
        }

        internal static void AppendActivity(List<OperationActivityRow> rows)
        {
            lock (LockObj)
                foreach (var job in Jobs.Values)
                    rows.Add(OperationActivity.TestRow(job));
        }

        internal static bool PersistResultArtifactMetadata() => PersistToSessionState(force: true);

        private static void BindControl(TestJob job)
        {
            job.NativeControl = job.Control.State;
            job.Control.Persist = () => PersistToSessionState(force: true);
            if (!_controlMonitor)
            {
                _controlMonitor = true;
                EditorApplication.update += TickNativeControls;
            }
        }

        internal static NativeTestRunControl EnsureNativeControl(string id, long timeoutMs = 0)
        {
            if (id == null || !Jobs.TryGetValue(id, out var job))
                return null;
            if (job.Control != null)
                return job.Control;
            job.Control = NativeTestRunControl.Create(id, timeoutMs);
            if (job.Control != null)
            {
                BindControl(job);
                PersistToSessionState(force: true);
            }
            return job.Control;
        }

        internal static NativeTestRunControl ControlFor(string id) => id != null && Jobs.TryGetValue(id, out var job) ? job.Control : null;

        internal static bool ControlOwnsCallback(string id) => ControlFor(id)?.AcceptsCallback() ?? true;

        internal static bool NativeAdmissionBlocked => Jobs.Values.Any(job => job.Control != null && !job.Control.Released);

        internal static bool OtherNativeAdmissionBlocked(string admittedJobId) =>
            Jobs.Values.Any(job =>
                job.Control != null
                && !job.Control.Released
                && (job.JobId != admittedJobId || job.Control.NativeGuid != null || (bool?)job.Control.State["native_invoked"] == true)
            );

        internal static void RetireQueuedControl(string id)
        {
            var control = ControlFor(id);
            if (control != null && control.NativeGuid == null)
                control.RetireQueued();
        }

        internal static object CancelJob(string id)
        {
            if (!NativeTestRunControl.Available)
                throw new NotSupportedException("unsupported_native_test_control");
            if (!Jobs.TryGetValue(id, out var job))
                throw new ArgumentException("Test job not found.");
            if (job.Control == null)
                throw new NotSupportedException("unsupported_native_test_control: job has no captured native control ownership");
            if (!job.Control.Released)
                job.Control.Request("user");
            TickNativeControls();
            return new
            {
                job_id = id,
                status = job.Status.ToString().ToLowerInvariant(),
                native_control = job.NativeControl,
            };
        }

        private static void TickNativeControls()
        {
            foreach (var job in Jobs.Values.ToArray())
            {
                var control = job.Control;
                if (control == null || (bool?)control.State["settled"] == true)
                    continue;
                if (!control.Released)
                    control.Tick();
                if (!control.Released)
                    continue;
                if (control.Requested && job.Status == TestJobStatus.Running)
                {
                    bool stoppedBeforeStart = control.NativeGuid == null;
                    if (stoppedBeforeStart)
                        TestRunnerService.RetireJob(job.JobId, "Test job stopped before native execution.");
                    // The API bool acknowledges an intent, not the authoritative outcome.
                    // A retained callback summary survives reload even when Result is evicted.
                    var summary = job.RetainedSummary ?? job.Result?.Summary;
                    bool cancelledResult = summary != null && new[] { "Canceled", "Cancelled" }.Contains(summary.ResultState, StringComparer.OrdinalIgnoreCase);
                    if (stoppedBeforeStart || cancelledResult || control.CancelAccepted && summary == null)
                    {
                        bool deadline = (string)control.State["reason"] == "deadline";
                        job.Status = deadline ? TestJobStatus.Failed : TestJobStatus.Cancelled;
                        job.CompletionKind = deadline ? "deadline_exceeded" : "cancelled";
                        job.Error = deadline ? "Native test job deadline exceeded." : null;
                        if (summary == null)
                            job.ResultAvailability = "unavailable";
                    }
                    else
                    {
                        job.Status = summary == null || summary.Failed > 0 ? TestJobStatus.Failed : TestJobStatus.Succeeded;
                        job.CompletionKind = summary == null ? "interrupted" : "completed";
                    }
                    job.FinishedUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    job.LastUpdateUnixMs = job.FinishedUnixMs.Value;
                    if (_currentJobId == job.JobId)
                        _currentJobId = null;
                    TestRunStatus.MarkFinished();
                    PersistToSessionState(force: true);
                }
                TestRunnerService.ConfirmNativeTermination(job.JobId);
                control.State["settled"] = true;
                PersistToSessionState(force: true);
                control.Dispose();
            }
            if (!NativeAdmissionBlocked && _controlMonitor)
            {
                EditorApplication.update -= TickNativeControls;
                _controlMonitor = false;
            }
        }

        public static string StartJob(TestMode mode, TestFilterOptions filterOptions = null, long initTimeoutMs = 0, long runTimeoutMs = 0)
        {
            if (runTimeoutMs < 0 || runTimeoutMs > 86400000)
                throw new ArgumentException("runTimeoutMs must be 1..86400000 when supplied.");
            if (runTimeoutMs > 0 && !NativeTestRunControl.Available)
                throw new NotSupportedException("unsupported_native_test_control");
            if (NativeAdmissionBlocked)
                throw new InvalidOperationException("A Unity test run or native cleanup is already in progress.");
            TestRunnerService.ThrowIfRetiredRunPending();
            // Clamp to valid range: non-positive values mean "use default", cap at 10 minutes
            if (initTimeoutMs < 0)
                initTimeoutMs = 0;
            if (initTimeoutMs > MaxInitializationTimeoutMs)
                initTimeoutMs = MaxInitializationTimeoutMs;

            string jobId = Guid.NewGuid().ToString("N");
            long started = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            string modeStr = mode.ToString();

            var job = new TestJob
            {
                JobId = jobId,
                Status = TestJobStatus.Running,
                Mode = modeStr,
                StartedUnixMs = started,
                FinishedUnixMs = null,
                LastUpdateUnixMs = started,
                TotalTests = null,
                CompletedTests = 0,
                CurrentTestFullName = null,
                CurrentTestStartedUnixMs = null,
                LastFinishedTestFullName = null,
                LastFinishedUnixMs = null,
                FailuresSoFar = new List<TestJobFailure>(),
                Error = null,
                Result = null,
                InitTimeoutMs = initTimeoutMs,
            };

            // Single lock scope for check-and-set to avoid TOCTOU race
            lock (LockObj)
            {
                if (!string.IsNullOrEmpty(_currentJobId))
                {
                    throw new InvalidOperationException("A Unity test run is already in progress.");
                }
                Jobs[jobId] = job;
                _currentJobId = jobId;
            }
            if (runTimeoutMs > 0 && EnsureNativeControl(jobId, runTimeoutMs) == null)
            {
                Jobs.Remove(jobId);
                _currentJobId = null;
                throw new NotSupportedException("unsupported_native_test_control");
            }
            PersistToSessionState(force: true);

            // Kick the run (must be called on main thread; our command handlers already run there).
            Task<TestRunResult> task;
            try
            {
                task = MCPServiceLocator.Tests.RunTestsAsync(mode, filterOptions);
            }
            catch (Exception ex)
            {
                // A synchronous startup failure has no task continuation to release the job.
                FinalizeFromTask(jobId, Task.FromException<TestRunResult>(ex));
                throw;
            }

            void FinalizeJob(Action finalize)
            {
                // Ensure state mutation happens on main thread to avoid Unity API surprises.
                EditorApplication.delayCall += () =>
                {
                    try
                    {
                        finalize();
                    }
                    catch (Exception ex)
                    {
                        McpLog.Error($"[TestJobManager] Finalize failed: {ex.Message}\n{ex.StackTrace}");
                    }
                };
            }

            task.ContinueWith(
                t =>
                {
                    // NOTE: We now finalize jobs deterministically from the TestRunnerService RunFinished callback.
                    // This continuation is retained as a safety net in case RunFinished is not delivered.
                    FinalizeJob(() => FinalizeFromTask(jobId, t));
                },
                TaskScheduler.Default
            );

            return jobId;
        }

        public static void FinalizeCurrentJobFromRunFinished(TestRunResult resultPayload)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out var job))
                {
                    return;
                }

                job.LastUpdateUnixMs = now;
                job.FinishedUnixMs = now;
                job.Status = resultPayload != null && resultPayload.Failed > 0 ? TestJobStatus.Failed : TestJobStatus.Succeeded;
                job.Error = null;
                job.Result = resultPayload;
                RetainResult(job, resultPayload);
                if (resultPayload != null)
                {
                    job.TotalTests = resultPayload.Total;
                    job.CompletedTests = resultPayload.Total;
                }
                job.CurrentTestFullName = null;
                job.CurrentTestStartedUnixMs = null;
                if (job.Control?.Requested == true && !job.Control.Released)
                {
                    job.Status = TestJobStatus.Running;
                    job.FinishedUnixMs = null;
                    job.CompletionKind = "cancellation_pending";
                }
                else
                    _currentJobId = null;
            }
            PersistToSessionState(force: true);
        }

        internal static void FinalizeCurrentJobFromRunError(string message)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out var job))
                {
                    return;
                }
                job.Status = TestJobStatus.Failed;
                job.Error = message;
                job.CompletionKind = "interrupted";
                job.LastUpdateUnixMs = now;
                job.FinishedUnixMs = now;
                job.CurrentTestFullName = null;
                job.CurrentTestStartedUnixMs = null;
                _currentJobId = null;
            }
            PersistToSessionState(force: true);
        }

        public static void OnRunStarted(int? totalTests)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out var job))
                {
                    return;
                }

                job.LastUpdateUnixMs = now;
                job.TotalTests = totalTests;
                job.CompletedTests = 0;
                job.CurrentTestFullName = null;
                job.CurrentTestStartedUnixMs = null;
                job.LastFinishedTestFullName = null;
                job.LastFinishedUnixMs = null;
                job.FailuresSoFar ??= new List<TestJobFailure>();
                job.FailuresSoFar.Clear();
            }
            PersistToSessionState(force: true);
        }

        public static void OnTestStarted(string testFullName)
        {
            if (string.IsNullOrWhiteSpace(testFullName))
            {
                return;
            }

            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out var job))
                {
                    return;
                }

                job.LastUpdateUnixMs = now;
                job.CurrentTestFullName = testFullName;
                job.CurrentTestStartedUnixMs = now;
            }
            PersistToSessionState();
        }

        public static void OnLeafTestFinished(string testFullName, bool isFailure, string message)
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out var job))
                {
                    return;
                }

                job.LastUpdateUnixMs = now;
                job.CompletedTests = Math.Max(0, job.CompletedTests + 1);
                job.LastFinishedTestFullName = testFullName;
                job.LastFinishedUnixMs = now;

                if (isFailure)
                {
                    job.FailuresSoFar ??= new List<TestJobFailure>();
                    if (job.FailuresSoFar.Count < FailureCap)
                    {
                        job.FailuresSoFar.Add(
                            new TestJobFailure
                            {
                                FullName = Preview(testFullName),
                                Message = string.IsNullOrWhiteSpace(message) ? "Test failed" : Preview(message),
                            }
                        );
                    }
                }
            }
            PersistToSessionState();
        }

        public static void OnRunFinished()
        {
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            lock (LockObj)
            {
                if (string.IsNullOrEmpty(_currentJobId) || !Jobs.TryGetValue(_currentJobId, out var job))
                {
                    return;
                }

                job.LastUpdateUnixMs = now;
                job.CurrentTestFullName = null;
            }
            PersistToSessionState(force: true);
        }

        internal static TestJob GetCompletedJobForExport(string jobId)
        {
            if (string.IsNullOrWhiteSpace(jobId))
                return null;

            // Export observes completed results; polling's initialization timeout must not
            // retire an active or pending run as a side effect of a read-only request.
            lock (LockObj)
            {
                if (
                    !Jobs.TryGetValue(jobId, out var job)
                    || (job.Status != TestJobStatus.Succeeded && job.Status != TestJobStatus.Failed)
                    || job.CompletionKind != "completed"
                    || job.ResultArtifact == null
                )
                    return null;
                return job;
            }
        }

        internal static TestJob GetJob(string jobId)
        {
            if (string.IsNullOrWhiteSpace(jobId))
            {
                return null;
            }

            TestJob jobToReturn = null;
            bool shouldPersist = false;
            bool retiredCurrentJob = false;
            lock (LockObj)
            {
                if (!Jobs.TryGetValue(jobId, out var job))
                {
                    return null;
                }

                // Check if job is stuck in "running" state without having called OnRunStarted (TotalTests still null).
                // This happens when tests fail to initialize (e.g., unsaved scene, compilation issues).
                // After 15 seconds without initialization, auto-fail the job to prevent hanging.
                if (job.Status == TestJobStatus.Running && job.TotalTests == null && job.Control?.Requested != true)
                {
                    long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    long initTimeout = job.InitTimeoutMs > 0 ? job.InitTimeoutMs : DefaultInitializationTimeoutMs;
                    if (!EditorStateCache.GetActualIsCompiling() && !EditorApplication.isUpdating && now - job.StartedUnixMs > initTimeout)
                    {
                        McpLog.Warn($"[TestJobManager] Job {jobId} failed to initialize within {initTimeout}ms, auto-failing");
                        job.Status = TestJobStatus.Failed;
                        job.Error = "Test job failed to initialize (tests did not start within timeout)";
                        job.CompletionKind = "interrupted";
                        job.FinishedUnixMs = now;
                        job.LastUpdateUnixMs = now;
                        if (_currentJobId == jobId)
                        {
                            _currentJobId = null;
                            retiredCurrentJob = true;
                            // Keep TestRunStatus in sync: when initialization times out, neither
                            // RunStarted nor RunFinished fires, so the running flag would otherwise leak.
                            // Only clear it if this job is still the active one — a newer job may have taken over.
                            TestRunStatus.MarkFinished();
                        }
                        shouldPersist = true;
                    }
                }

                jobToReturn = job;
            }

            if (retiredCurrentJob)
                TestRunnerService.RetireJob(jobId, jobToReturn.Error);
            if (shouldPersist)
            {
                PersistToSessionState(force: true);
            }
            return jobToReturn;
        }

        internal static object ToSerializable(TestJob job, bool includeDetails, bool includeFailedTests)
        {
            if (job == null)
            {
                return null;
            }

            object resultPayload = null;
            if (job.Status != TestJobStatus.Running)
            {
                try
                {
                    var result = RestoreResult(job);
                    if (result != null)
                        resultPayload = result.ToSerializable(job.Mode, includeDetails, includeFailedTests);
                }
                catch (Exception error)
                {
                    job.ResultAvailability = error is FileNotFoundException ? (error.Message == "evicted" ? "evicted" : "missing") : "corrupt";
                    if (job.RetainedSummary != null)
                        resultPayload = new
                        {
                            mode = job.Mode,
                            summary = job.RetainedSummary.ToSerializable(),
                            results = (object)null,
                        };
                }
            }

            return new
            {
                job_id = job.JobId,
                status = job.Status.ToString().ToLowerInvariant(),
                mode = job.Mode,
                started_unix_ms = job.StartedUnixMs,
                finished_unix_ms = job.FinishedUnixMs,
                last_update_unix_ms = job.LastUpdateUnixMs,
                progress = new
                {
                    completed = job.CompletedTests,
                    total = job.TotalTests,
                    current_test_full_name = job.CurrentTestFullName,
                    current_test_started_unix_ms = job.CurrentTestStartedUnixMs,
                    last_finished_test_full_name = job.LastFinishedTestFullName,
                    last_finished_unix_ms = job.LastFinishedUnixMs,
                    stuck_suspected = IsStuck(job),
                    editor_is_focused = InternalEditorUtility.isApplicationActive,
                    run_in_background = UnityEngine.Application.runInBackground,
                    blocked_reason = GetBlockedReason(job),
                    failures_so_far = BuildFailuresPayload(job.FailuresSoFar),
                    failures_capped = (job.FailuresSoFar != null && job.FailuresSoFar.Count >= FailureCap),
                },
                error = job.Error,
                result = resultPayload,
                native_control = job.NativeControl,
                result_truncated_count = (int?)job.ResultArtifact?["truncated_result_count"] ?? 0,
                completion_kind = job.CompletionKind,
                result_availability = job.ResultAvailability ?? (job.Result == null ? "legacy_unavailable" : "available"),
                diagnostics = BuildDiagnostics(
                    job,
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    UnityEngine.Application.unityVersion,
                    UnityEngine.Application.platform.ToString(),
                    UnityEngine.SystemInfo.graphicsDeviceType.ToString(),
                    InternalEditorUtility.isApplicationActive,
                    EditorStateCache.GetActualIsCompiling(),
                    EditorApplication.isUpdating
                ),
            };
        }

        private static void RetainResult(TestJob job, TestRunResult result)
        {
            if (result == null)
            {
                job.CompletionKind = "interrupted";
                return;
            }
            job.CompletionKind = "completed";
            job.RetainedSummary = result.Summary;
            job.ResultArtifact = result.Artifact;
            job.ResultAvailability = result.Availability;
            if (result.Artifact == null && result.Availability == null)
            {
                try
                {
                    job.ResultArtifact = ResultStore.Save(job.JobId, job.Mode, result, job.FinishedUnixMs ?? job.LastUpdateUnixMs);
                    job.ResultAvailability = "available";
                }
                catch (Exception error)
                {
                    job.ResultAvailability =
                        error is PathTooLongException ? "path_too_long"
                        : error is InvalidDataException && error.Message == "path_limit_exceeded" ? "path_limit_exceeded"
                        : error.Message == "quota_exceeded" ? "quota_exceeded"
                        : "write_failed";
                }
            }
            if (job.ResultArtifact != null)
            {
                job.ResultArtifact["status"] = job.Status.ToString().ToLowerInvariant();
                job.ResultArtifact["started_unix_ms"] = job.StartedUnixMs;
                job.ResultArtifact["finished_unix_ms"] = job.FinishedUnixMs;
            }
        }

        private static TestRunResult RestoreResult(TestJob job)
        {
            if (job.ResultArtifact != null)
                ResultStore.Manifest(job.JobId, job.ResultArtifact);
            if (job.Result != null)
                return job.Result;
            if (job.RetainedSummary == null)
                return null;
            if (job.ResultArtifact == null)
                return new TestRunResult(job.RetainedSummary, Array.Empty<TestRunTestResult>());
            ResultStore.Manifest(job.JobId, job.ResultArtifact);
            return new TestRunResult(job.RetainedSummary, ResultStore.Results(job.JobId, job.ResultArtifact));
        }

        internal static string ModeForCapture(string jobId)
        {
            // RunFinished must not call GetJob: polling can expire initialization.
            lock (LockObj)
                return jobId != null && Jobs.TryGetValue(jobId, out var job) ? job.Mode : null;
        }

        internal static object Page(TestJob job, int size, string cursor, bool failedOnly)
        {
            if (job.CompletionKind != "completed" || job.ResultArtifact == null)
                throw new InvalidOperationException("Test result details are unavailable.");
            return ResultStore.ReadPage(job.JobId, job.ResultArtifact, size, cursor, failedOnly);
        }

        // Pure observation core: tests can model a Linux/Vulkan environment without
        // switching the active editor's graphics API or launching another editor.
        internal static object BuildDiagnostics(
            TestJob job,
            long nowUnixMs,
            string unityVersion,
            string platform,
            string graphicsApi,
            bool focused,
            bool compiling,
            bool importing
        )
        {
            bool initFailed =
                job.Status == TestJobStatus.Failed
                && job.Result == null
                && (job.Error?.StartsWith("Test job failed to initialize", StringComparison.Ordinal) ?? false);
            // The timeout finalizer updates LastUpdateUnixMs when it records failure;
            // that bookkeeping is not evidence that initialization made progress.
            long lastProgress = !initFailed && job.LastUpdateUnixMs > 0 ? job.LastUpdateUnixMs : job.StartedUnixMs;
            long progressAge = Math.Max(0, nowUnixMs - lastProgress);
            bool suspected = job.Status == TestJobStatus.Running && progressAge > StuckThresholdMs;
            var causes = new List<string>();
            var actions = new List<string>();
            if (suspected)
            {
                causes.Add(string.IsNullOrEmpty(job.CurrentTestFullName) ? "test_initialization_or_reload_pending" : "long_running_test_or_editor_stall");
                actions.Add(
                    "Inspect Test Runner and Editor.log. No progress alone cannot distinguish a long test, a native dialog, or a blocked editor thread; the job remains running."
                );
            }
            if (initFailed)
            {
                causes.Add("test_runner_initialization_timeout");
                actions.Add(
                    "Inspect Editor.log and Test Runner startup. For PlayMode/domain reload delays, retry with a larger init_timeout (maximum 3600000 ms)."
                );
            }
            if (suspected || initFailed)
            {
                if (compiling)
                    causes.Add("editor_compiling");
                if (importing)
                    causes.Add("editor_importing_assets");
                if (!focused)
                    causes.Add("background_editor_throttling_possible");
                if (
                    string.Equals(platform, "LinuxEditor", StringComparison.OrdinalIgnoreCase)
                    && string.Equals(graphicsApi, "Vulkan", StringComparison.OrdinalIgnoreCase)
                )
                {
                    causes.Add("linux_vulkan_backend_candidate");
                    actions.Add(
                        "Linux/Vulkan is an environment candidate, not a confirmed cause. In a separate reproduction session, compare another supported graphics backend using the same Unity version and test; preserve logs. Do not switch or terminate this editor automatically."
                    );
                }
            }
            return new
            {
                unity_version = unityVersion,
                platform,
                graphics_api = graphicsApi,
                last_progress_age_ms = progressAge,
                initialization_timeout_ms = job.InitTimeoutMs > 0 ? job.InitTimeoutMs : DefaultInitializationTimeoutMs,
                initialization_failed = initFailed,
                stall_suspected = suspected,
                possible_causes = causes.ToArray(),
                recommended_actions = actions.ToArray(),
            };
        }

        private static string GetBlockedReason(TestJob job)
        {
            if (job == null || job.Status != TestJobStatus.Running)
            {
                return null;
            }

            if (!IsStuck(job))
            {
                return null;
            }

            // This matches the real-world symptom you observed: background Unity can get heavily throttled by OS/Editor.
            if (!InternalEditorUtility.isApplicationActive)
            {
                return "editor_unfocused";
            }

            if (EditorStateCache.GetActualIsCompiling())
            {
                return "compiling";
            }

            if (EditorApplication.isUpdating)
            {
                return "asset_import";
            }

            return "unknown";
        }

        private static bool IsStuck(TestJob job)
        {
            if (job == null || job.Status != TestJobStatus.Running)
            {
                return false;
            }

            if (string.IsNullOrWhiteSpace(job.CurrentTestFullName) || !job.CurrentTestStartedUnixMs.HasValue)
            {
                return false;
            }

            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            return (now - job.CurrentTestStartedUnixMs.Value) > StuckThresholdMs;
        }

        private static object[] BuildFailuresPayload(List<TestJobFailure> failures)
        {
            if (failures == null || failures.Count == 0)
            {
                return Array.Empty<object>();
            }

            var list = new object[failures.Count];
            for (int i = 0; i < failures.Count; i++)
            {
                var f = failures[i];
                list[i] = new { full_name = f?.FullName, message = f?.Message };
            }
            return list;
        }

        private static void FinalizeFromTask(string jobId, Task<TestRunResult> task)
        {
            lock (LockObj)
            {
                if (!Jobs.TryGetValue(jobId, out var existing))
                {
                    if (_currentJobId == jobId)
                        _currentJobId = null;
                    return;
                }

                if (existing.Control?.Requested == true && !existing.Control.Released)
                    return;
                // If RunFinished already finalized the job, do nothing.
                if (existing.Status != TestJobStatus.Running)
                {
                    if (_currentJobId == jobId)
                        _currentJobId = null;
                    return;
                }

                existing.LastUpdateUnixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                existing.FinishedUnixMs = existing.LastUpdateUnixMs;

                if (task.IsFaulted)
                {
                    existing.Status = TestJobStatus.Failed;
                    existing.Error = task.Exception?.GetBaseException()?.Message ?? "Unknown test job failure";
                    existing.Result = null;
                }
                else if (task.IsCanceled)
                {
                    existing.Status = TestJobStatus.Failed;
                    existing.Error = "Test job canceled";
                    existing.Result = null;
                }
                else
                {
                    var result = task.Result;
                    existing.Status = result != null && result.Failed > 0 ? TestJobStatus.Failed : TestJobStatus.Succeeded;
                    existing.Error = null;
                    existing.Result = result;
                    RetainResult(existing, result);
                }

                if (_currentJobId == jobId)
                {
                    _currentJobId = null;
                }
            }
            PersistToSessionState(force: true);
        }
    }
}
