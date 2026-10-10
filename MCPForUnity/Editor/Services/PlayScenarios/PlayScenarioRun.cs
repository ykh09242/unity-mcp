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

        [JsonProperty("pending_status")]
        public string PendingStatus;

        [JsonProperty("pending_error")]
        public string PendingError;

        [JsonProperty("cleanup_deadline_unix_ms")]
        public long? CleanupDeadlineUnixMs;

        [JsonProperty("settle_deadline_unix_ms")]
        public long? SettleDeadlineUnixMs;

        [JsonProperty("cleanup_error")]
        public string CleanupError;

        [JsonProperty("unexpected_log_count")]
        public int UnexpectedLogCount;

        [JsonProperty("unexpected_log_error")]
        public string UnexpectedLogError;

        [JsonProperty("last_unexpected_log_error")]
        public string LastUnexpectedLogError;

        [JsonProperty("metrics")]
        public List<PlayScenarioMetricsSnapshot> Metrics = new List<PlayScenarioMetricsSnapshot>();

        [JsonProperty("metric_warnings")]
        public List<string> MetricWarnings = new List<string>();

        [JsonProperty("metrics_summary")]
        public string MetricsSummary;

        [JsonProperty("failure_diagnostics")]
        public PlayScenarioFailureDiagnostics FailureDiagnostics;

        [JsonProperty("failure")]
        public PlayScenarioFailure Failure;

        [JsonProperty("cleanup_failures")]
        public List<PlayScenarioFailure> CleanupFailures = new List<PlayScenarioFailure>();

        [JsonProperty("reproduction")]
        public PlayScenarioReproduction Reproduction;

        [JsonProperty("resource_checks")]
        public List<PlayScenarioResourceCheck> ResourceChecks = new List<PlayScenarioResourceCheck>();

        [JsonProperty("runner_resources_released")]
        public bool? RunnerResourcesReleased;
    }

    public sealed class PlayScenarioStepResult
    {
        [JsonProperty("stage")]
        public string Stage = "main";

        [JsonProperty("stable_since_unix_ms")]
        public long? StableSinceUnixMs;

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

        public PlayScenarioFailure Failure { get; }

        public PlayScenarioObservation(bool ready, string detail, PlayScenarioFailure failure = null)
        {
            Ready = ready;
            Detail = detail;
            Failure = failure;
        }
    }

    public sealed class PlayScenarioMetricsSnapshot
    {
        [JsonProperty("iteration")]
        public int Iteration;

        [JsonProperty("timestamp_unix_ms")]
        public long TimestampUnixMs;

        [JsonProperty("managed_bytes")]
        public long? ManagedBytes;

        [JsonProperty("allocated_bytes")]
        public long? AllocatedBytes;

        [JsonProperty("object_count")]
        public int? ObjectCount;

        [JsonProperty("runner_subscription_count")]
        public int? RunnerSubscriptionCount;

        [JsonProperty("runner_handle_count")]
        public int? RunnerHandleCount;

        [JsonProperty("ignored_for_trend")]
        public bool IgnoredForTrend;

        [JsonProperty("error")]
        public string Error;
    }

    public sealed class PlayScenarioFailureDiagnostics
    {
        [JsonProperty("captured_unix_ms")]
        public long CapturedUnixMs;

        [JsonProperty("active_scene")]
        public string ActiveScene;

        [JsonProperty("target")]
        public string Target;

        [JsonProperty("observation")]
        public string Observation;

        [JsonProperty("target_detail")]
        public string TargetDetail;

        [JsonProperty("screenshot_path")]
        public string ScreenshotPath;

        [JsonProperty("screenshot_error")]
        public string ScreenshotError;
    }

    /// <summary>Optional sampling boundary; existing hosts need only implement step evaluation.</summary>
    public interface IPlayScenarioMetricsHost
    {
        PlayScenarioMetricsSnapshot CaptureMetrics(int iteration, long now);
    }

    /// <summary>Evaluate only the current condition; a ready click dispatches exactly once.</summary>
    public interface IPlayScenarioHost
    {
        PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll);
        void Release();
    }
}
