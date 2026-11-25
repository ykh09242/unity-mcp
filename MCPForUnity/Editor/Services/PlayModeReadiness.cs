using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services
{
    // A native request cannot await across the domain reload caused by entering Play Mode.
    [InitializeOnLoad]
    internal static class PlayModeReadiness
    {
        private const string SessionKey = "MCPForUnity.PlayModeReadinessV1";
        private const string PlayingKey = "MCPForUnity.PlayModeReadiness.EnteredV1";
        private const int HistoryLimit = 8;

        private sealed class Job
        {
            public string job_id;
            public string wait_until;
            public string status = "running";
            public string error;
            public long deadline_unix_ms;
            public double deadline_editor_seconds;
            public int timeout_seconds;
            public bool observation_expired;
            public bool observed_after_deadline;
            public bool transition_requested;
            public bool entered;
            public int entry_frame;

            [JsonProperty(NullValueHandling = NullValueHandling.Ignore)]
            public List<Job> history;
        }

        private static Job _job;
        private static IDisposable _playRequest;
        private static bool _waitingForEditorUpdate;
        internal static bool HasPendingPlayRequest =>
            (_playRequest != null || _waitingForEditorUpdate) && _job?.status == "running" && !_job.transition_requested;

        // During Play, Unity can defer a requested reload after compilation has finished.
        // Edit-mode work still waits for Unity's pending compilation flag to clear.
        internal static bool IsEditorStable =>
            !EditorApplication.isUpdating
            && !EditorStateCache.IsDomainReloadPending
            && !EditorStateCache.IsPlayModeTransition
            && !(EditorApplication.isPlaying ? EditorStateCache.GetActualIsCompiling() : EditorApplication.isCompiling);
        internal static Func<double> _clockOverride = null;
        internal static Func<bool> _readinessOverride = null;

        private static double Now() => _clockOverride?.Invoke() ?? EditorApplication.timeSinceStartup;

        static PlayModeReadiness()
        {
            Restore();
            EditorApplication.update += OnEditorUpdate;
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
        }

        private static void Restore()
        {
            _playRequest?.Dispose();
            _playRequest = null;
            _waitingForEditorUpdate = false;
            _job = null;
            string saved = SessionState.GetString(SessionKey, "");
            if (!string.IsNullOrEmpty(saved))
            {
                try
                {
                    _job = JsonConvert.DeserializeObject<Job>(saved);
                    if (_job != null && _job.status == "running" && _job.deadline_editor_seconds <= 0)
                    {
                        // Upgrade the pre-existing session record once, retaining only its
                        // remaining time. SessionState cannot cross an Editor process restart.
                        double remaining = Math.Max(0, Math.Min(86400, (_job.deadline_unix_ms - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) / 1000d));
                        _job.deadline_editor_seconds = Now() + remaining;
                        Persist();
                    }
                    SchedulePlayRequest();
                }
                catch (JsonException)
                {
                    SessionState.EraseString(SessionKey);
                }
            }
        }

        public static object Start(JObject parameters)
        {
            string requestedId = parameters["job_id"]?.ToString();
            if (requestedId != null && !Guid.TryParseExact(requestedId, "N", out _))
                return new ErrorResponse("job_id must be a 32-character UUID when starting a readiness job.");
            string target = parameters["wait_until"]?.ToString();
            if (target != "scene_loaded" && target != "first_frame")
                return new ErrorResponse("wait_until must be scene_loaded or first_frame.");
            if (!PaginationBounds.TryRead(parameters["timeout_seconds"], 300, 1, 86400, "timeout_seconds", out int timeout, out string error))
                return new ErrorResponse(error);
            Tick();
            Job existing = Find(requestedId);
            if (existing != null)
            {
                if (existing.wait_until != target || existing.timeout_seconds != timeout)
                    return new ErrorResponse("play_readiness_job_id_conflict", Snapshot(existing));
                return new SuccessResponse("Existing play mode readiness job.", Snapshot(existing));
            }
            if (_job != null && _job.status == "running")
                return new ErrorResponse("play_readiness_busy", Snapshot(_job));
            var history = _job?.history ?? new List<Job>();
            if (_job != null)
            {
                _job.history = null;
                history.Add(_job);
                if (history.Count > HistoryLimit)
                    history.RemoveRange(0, history.Count - HistoryLimit);
            }
            _job = new Job
            {
                history = history,
                job_id = requestedId ?? Guid.NewGuid().ToString("N"),
                wait_until = target,
                deadline_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + timeout * 1000L,
                deadline_editor_seconds = Now() + timeout,
                timeout_seconds = timeout,
                // Do not attempt to enter again when a user or another tool is already entering.
                transition_requested = EditorApplication.isPlayingOrWillChangePlaymode,
            };
            if (EditorApplication.isPlaying && SessionState.GetBool(PlayingKey, false))
            {
                _job.entered = true;
                _job.entry_frame = Time.frameCount;
            }
            Persist();
            // Return the job ID before requesting the transition that may destroy this domain.
            SchedulePlayRequest();
            return new SuccessResponse("Play mode readiness job started; poll get_play_mode_job.", Snapshot(_job));
        }

        private static Job Find(string id) =>
            string.IsNullOrEmpty(id) ? null
            : _job?.job_id == id ? _job
            : _job?.history?.Find(job => job.job_id == id);

        public static object Get(JObject parameters, bool cancel)
        {
            Job job = Find(parameters["job_id"]?.ToString());
            if (job == null)
                return new ErrorResponse("play_readiness_job_not_found");
            if (cancel)
                Cancel(job, "Readiness monitoring cancelled; Play Mode is unchanged.");
            else if (job == _job)
                Observe(allowExpired: true);
            return new SuccessResponse("Play mode readiness status.", Snapshot(job));
        }

        public static void Cancel(string reason) => Cancel(_job, reason);

        private static void Cancel(Job job, string reason)
        {
            if (job == null || (job.status != "running" && job.status != "timed_out"))
                return;
            if (job == _job)
            {
                _playRequest?.Dispose();
                _playRequest = null;
                _waitingForEditorUpdate = false;
            }
            job.status = "cancelled";
            job.error = reason;
            Persist();
        }

        private static void SchedulePlayRequest()
        {
            _playRequest?.Dispose();
            _playRequest = null;
            _waitingForEditorUpdate = false;
            if (
                _job == null
                || _job.status != "running"
                || _job.transition_requested
                || _job.entered
                || !Guid.TryParseExact(_job.job_id, "N", out _)
                || (_job.wait_until != "scene_loaded" && _job.wait_until != "first_frame")
                || double.IsNaN(_job.deadline_editor_seconds)
                || double.IsInfinity(_job.deadline_editor_seconds)
                || Now() >= _job.deadline_editor_seconds
                || EditorApplication.isPlayingOrWillChangePlaymode
            )
                return;
            _playRequest = EditorMainThreadWakeup.Schedule(RequestPlay);
        }

        private static void RequestPlay()
        {
            _playRequest?.Dispose();
            _playRequest = null;
            _waitingForEditorUpdate = false;
            Tick();
            if (_job == null || _job.status != "running" || _job.transition_requested)
                return;
            try
            {
                if (EditorApplication.isPlayingOrWillChangePlaymode)
                {
                    _job.transition_requested = true;
                    Persist();
                    return;
                }
                // The queued callback has released its own reservation. A different
                // native owner may have appeared since Start or across a reload.
                if (NativeOperationBarriers.TryGetBlocker(null, null, out var blocker))
                {
                    if (
                        blocker.Reason == "editor_compiling"
                        || blocker.Reason == "editor_importing"
                        || (blocker.Reason == "native_job_active" && EditorStateCache.IsDomainReloadPending)
                    )
                    {
                        _waitingForEditorUpdate = true;
                        return;
                    }
                    _job.status = "failed";
                    _job.error = "Play Mode entry blocked: " + blocker.Reason;
                    Persist();
                    return;
                }
                _job.transition_requested = true;
                Persist();
                EditorApplication.isPlaying = true;
            }
            catch (Exception e)
            {
                _job.status = "failed";
                _job.error = e.Message;
                Persist();
            }
        }

        private static void OnPlayModeChanged(PlayModeStateChange state)
        {
            SessionState.SetBool(PlayingKey, state == PlayModeStateChange.EnteredPlayMode);
            if (state == PlayModeStateChange.ExitingPlayMode || state == PlayModeStateChange.EnteredEditMode)
                Cancel("Play Mode exited before readiness was confirmed.");
            else if (state == PlayModeStateChange.EnteredPlayMode && _job != null && (_job.status == "running" || _job.status == "timed_out"))
            {
                _job.entered = true;
                _job.entry_frame = Time.frameCount;
                Persist();
                Tick();
            }
        }

        private static void Tick() => Observe(allowExpired: false);

        private static void OnEditorUpdate()
        {
            Tick();
            // Native wake ticks can run during compilation. Retry on a normal Editor
            // update after it settles, under the same ID, deadline and ownership checks.
            if (
                _waitingForEditorUpdate
                && _job?.status == "running"
                && !EditorStateCache.GetActualIsCompiling()
                && !EditorApplication.isUpdating
                && !EditorStateCache.IsDomainReloadPending
            )
                RequestPlay();
        }

        private static void Observe(bool allowExpired)
        {
            if (_job == null || (_job.status != "running" && !(allowExpired && _job.status == "timed_out")))
                return;
            bool expired = Now() >= _job.deadline_editor_seconds;
            // A blocked Editor can first observe completion after its budget. Report the
            // current state honestly without implying timely completion or replaying Play.
            if (IsReady())
            {
                _job.status = "succeeded";
                _job.error = null;
                _job.observation_expired |= expired;
                _job.observed_after_deadline = expired;
                Persist();
            }
            else if (expired && _job.status == "running")
            {
                _job.status = "timed_out";
                _job.observation_expired = true;
                _job.error = "Readiness observation expired; Play Mode is unchanged. Poll get_play_mode_job with this job_id before starting another request.";
                Persist();
            }
        }

        private static bool IsReady()
        {
            if (_readinessOverride != null)
                return _readinessOverride();
            if (!_job.entered || !EditorApplication.isPlaying || !IsEditorStable)
                return false;
            Scene active = SceneManager.GetActiveScene();
            return active.IsValid() && active.isLoaded && (_job.wait_until != "first_frame" || Time.frameCount > _job.entry_frame);
        }

        private static object Snapshot(Job job) =>
            new
            {
                job.job_id,
                job.wait_until,
                job.status,
                job.error,
                job.deadline_unix_ms,
                job.deadline_editor_seconds,
                job.timeout_seconds,
                job.observation_expired,
                job.observed_after_deadline,
                job.transition_requested,
                is_playing = EditorApplication.isPlaying,
                is_paused = EditorApplication.isPaused,
                frame_count = Time.frameCount,
                readiness_scope = "Loaded active scene; first_frame means a subsequent simulation frame, not rendering or application async initialization.",
            };

        private static void Persist() => SessionState.SetString(SessionKey, JsonConvert.SerializeObject(_job));
    }
}
