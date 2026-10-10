using System.Collections.Generic;
using Newtonsoft.Json;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Serializable values only; never retains scene objects or callbacks.</summary>
    public sealed class PlayScenarioRun
    {
        [JsonProperty("job_id")]
        public string JobId;

        [JsonProperty("scenario")]
        public PlayScenarioDefinition Scenario;

        [JsonProperty("status")]
        public string Status = "running";

        [JsonProperty("phase")]
        public string Phase = "starting";

        [JsonProperty("repeat_count")]
        public int RepeatCount;

        [JsonProperty("started_unix_ms")]
        public long StartedUnixMs;

        [JsonProperty("deadline_unix_ms")]
        public long DeadlineUnixMs;

        [JsonProperty("finished_unix_ms")]
        public long? FinishedUnixMs;

        [JsonProperty("error")]
        public string Error;

        [JsonProperty("play_requested")]
        public bool PlayRequested;

        [JsonProperty("cursor")]
        public int Cursor;

        [JsonProperty("next_poll_unix_ms")]
        public long NextPollUnixMs;

        [JsonProperty("steps")]
        public List<PlayScenarioStepResult> Steps = new List<PlayScenarioStepResult>();

        [JsonProperty("logs")]
        public List<PlayScenarioLog> Logs = new List<PlayScenarioLog>();

        [JsonProperty("dropped_log_count")]
        public int DroppedLogCount;

        [JsonProperty("report_path")]
        public string ReportPath;

        [JsonProperty("report_error")]
        public string ReportError;
    }

    public sealed class PlayScenarioStepResult
    {
        [JsonProperty("iteration")]
        public int Iteration;

        [JsonProperty("step_index")]
        public int StepIndex;

        [JsonProperty("name")]
        public string Name;

        [JsonProperty("action")]
        public string Action;

        [JsonProperty("status")]
        public string Status = "pending";

        [JsonProperty("started_unix_ms")]
        public long? StartedUnixMs;

        [JsonProperty("finished_unix_ms")]
        public long? FinishedUnixMs;

        [JsonProperty("detail")]
        public string Detail;

        [JsonProperty("poll_count")]
        public int PollCount;
    }

    public sealed class PlayScenarioLog
    {
        [JsonProperty("timestamp_unix_ms")]
        public long TimestampUnixMs;

        [JsonProperty("type")]
        public string Type;

        [JsonProperty("message")]
        public string Message;

        [JsonProperty("stack_trace")]
        public string StackTrace;
    }

    public sealed class PlayScenarioObservation
    {
        public bool Ready { get; }
        public string Detail { get; }

        public PlayScenarioObservation(bool ready, string detail)
        {
            Ready = ready;
            Detail = detail;
        }
    }

    /// <summary>Evaluate only the current condition; a ready click dispatches exactly once.</summary>
    public interface IPlayScenarioHost
    {
        PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll);
        void Release();
    }
}
