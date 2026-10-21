using System.Linq;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    /// <summary>Detached fields displayed by the Editor window; the public status response remains complete.</summary>
    internal sealed class PlayScenarioEditorSnapshot
    {
        internal readonly JObject Data;

        internal PlayScenarioEditorSnapshot(JObject data) => Data = data;

        private static readonly string[] Fields =
        {
            "job_id",
            "status",
            "phase",
            "query_counts",
            "timeline",
            "dropped_timeline_count",
            "failure",
            "cleanup_failures",
            "resource_checks",
            "cleanup_error",
            "runner_resources_released",
            "reproduction",
            "error",
            "report_path",
            "report_error",
            "metrics_summary",
            "metric_warnings",
            "metrics",
            "failure_diagnostics",
            "logs",
            "dropped_log_count",
        };
        private static readonly string[] StepFields = { "stage", "iteration", "step_index", "name", "status", "detail", "query_counts" };

        internal static JObject FromState(PlayScenarioRun value) =>
            JObject.FromObject(
                new
                {
                    job_id = value.JobId,
                    status = value.Status,
                    phase = value.Phase,
                    query_counts = value.QueryCounts,
                    timeline = value.Timeline,
                    dropped_timeline_count = value.DroppedTimelineCount,
                    failure = value.Failure,
                    cleanup_failures = value.CleanupFailures,
                    resource_checks = value.ResourceChecks,
                    cleanup_error = value.CleanupError,
                    runner_resources_released = value.RunnerResourcesReleased,
                    reproduction = value.Reproduction,
                    error = value.Error,
                    report_path = value.ReportPath,
                    report_error = value.ReportError,
                    metrics_summary = value.MetricsSummary,
                    metric_warnings = value.MetricWarnings,
                    metrics = value.Metrics,
                    failure_diagnostics = value.FailureDiagnostics,
                    logs = value.Logs,
                    dropped_log_count = value.DroppedLogCount,
                    steps = value.Steps.Select(step => new
                    {
                        stage = step.Stage,
                        iteration = step.Iteration,
                        step_index = step.StepIndex,
                        name = step.Name,
                        status = step.Status,
                        detail = step.Detail,
                        query_counts = step.QueryCounts,
                    }),
                    scenario = new { diagnostics = new { screenshot_on_failure = value.Scenario?.Diagnostics?.ScreenshotOnFailure ?? false } },
                }
            );

        internal static JObject FromSnapshot(JObject value)
        {
            var result = new JObject();
            foreach (string field in Fields)
                result[field] = value[field]?.DeepClone();
            var steps = new JArray();
            if (value["steps"] is JArray source)
                foreach (JObject step in source.OfType<JObject>())
                {
                    var row = new JObject();
                    foreach (string field in StepFields)
                        row[field] = step[field]?.DeepClone();
                    steps.Add(row);
                }
            result["steps"] = steps;
            result["scenario"] = new JObject
            {
                ["diagnostics"] = new JObject { ["screenshot_on_failure"] = (bool?)value["scenario"]?["diagnostics"]?["screenshot_on_failure"] ?? false },
            };
            return result;
        }
    }
}
