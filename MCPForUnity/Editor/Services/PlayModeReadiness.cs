using System;
using MCPForUnity.Editor.Helpers;
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

        private sealed class Job
        {
            public string job_id;
            public string wait_until;
            public string status = "running";
            public string error;
            public long deadline_unix_ms;
            public bool transition_requested;
            public bool entered;
            public int entry_frame;
        }

        private static Job _job;

        static PlayModeReadiness()
        {
            Restore();
            EditorApplication.update += Tick;
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
        }

        private static void Restore()
        {
            string saved = SessionState.GetString(SessionKey, "");
            if (!string.IsNullOrEmpty(saved))
            {
                try
                {
                    _job = JsonConvert.DeserializeObject<Job>(saved);
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
            if (!PaginationBounds.TryRead(parameters["timeout_seconds"], 30, 1, 300, "timeout_seconds", out int timeout, out string error))
                return new ErrorResponse(error);
            Tick();
            if (requestedId != null && _job != null && _job.job_id == requestedId)
                return new SuccessResponse("Existing play mode readiness job.", Snapshot());
            if (_job != null && _job.status == "running")
                return new ErrorResponse("play_readiness_busy", Snapshot());
            _job = new Job
            {
                job_id = requestedId ?? Guid.NewGuid().ToString("N"),
                wait_until = target,
                deadline_unix_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + timeout * 1000L,
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
            EditorApplication.delayCall += RequestPlay;
            return new SuccessResponse("Play mode readiness job started; poll get_play_mode_job.", Snapshot());
        }

        public static object Get(JObject parameters, bool cancel)
        {
            string id = parameters["job_id"]?.ToString();
            if (string.IsNullOrEmpty(id) || _job == null || _job.job_id != id)
                return new ErrorResponse("play_readiness_job_not_found");
            if (cancel)
                Cancel("Readiness monitoring cancelled; Play Mode is unchanged.");
            else
                Tick();
            return new SuccessResponse("Play mode readiness status.", Snapshot());
        }

        public static void Cancel(string reason)
        {
            if (_job == null || _job.status != "running")
                return;
            _job.status = "cancelled";
            _job.error = reason;
            Persist();
        }

        private static void RequestPlay()
        {
            Tick();
            if (_job == null || _job.status != "running" || _job.transition_requested)
                return;
            _job.transition_requested = true;
            Persist();
            try
            {
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
            else if (state == PlayModeStateChange.EnteredPlayMode && _job != null && _job.status == "running")
            {
                _job.entered = true;
                _job.entry_frame = Time.frameCount;
                Persist();
                Tick();
            }
        }

        private static void Tick()
        {
            if (_job == null || _job.status != "running")
                return;
            if (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() >= _job.deadline_unix_ms)
            {
                _job.status = "timed_out";
                _job.error = "Readiness deadline expired; Play Mode is unchanged.";
                Persist();
                return;
            }
            if (!_job.entered || !EditorApplication.isPlaying || EditorApplication.isCompiling || EditorApplication.isUpdating)
                return;
            Scene active = SceneManager.GetActiveScene();
            if (!active.IsValid() || !active.isLoaded)
                return;
            if (_job.wait_until == "first_frame" && Time.frameCount <= _job.entry_frame)
                return;
            _job.status = "succeeded";
            Persist();
        }

        private static object Snapshot() =>
            new
            {
                _job.job_id,
                _job.wait_until,
                _job.status,
                _job.error,
                _job.deadline_unix_ms,
                is_playing = EditorApplication.isPlaying,
                is_paused = EditorApplication.isPaused,
                frame_count = Time.frameCount,
                readiness_scope = "Loaded active scene; first_frame means a subsequent simulation frame, not rendering or application async initialization.",
            };

        private static void Persist() => SessionState.SetString(SessionKey, JsonConvert.SerializeObject(_job));
    }
}
