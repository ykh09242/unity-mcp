using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor.UIElements;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal sealed class PlayScenarioHistoryEditor
    {
        private readonly PlayScenarioStore store;
        private readonly Func<string> scenarioName;
        private readonly Action<string, bool> message;
        private readonly VisualElement controls;
        private readonly ScrollView comparison;
        private IReadOnlyList<PlayScenarioRun> reports = new List<PlayScenarioRun>();

        internal PlayScenarioHistoryEditor(VisualElement parent, PlayScenarioStore store, Func<string> scenarioName, Action<string, bool> message)
        {
            this.store = store;
            this.scenarioName = scenarioName;
            this.message = message;
            var section = new Foldout { text = "Previous runs and comparison", name = "reportHistory" };
            section.Add(new Button(Refresh) { text = "Refresh history", name = "refreshReports" });
            controls = new VisualElement { name = "reportHistoryControls" };
            comparison = new ScrollView { name = "reportComparison" };
            comparison.AddToClassList("scenario-history-comparison");
            section.Add(controls);
            section.Add(comparison);
            parent.Add(section);
            controls.Add(new Label("Refresh to read up to 20 saved terminal reports for this scenario."));
        }

        internal void Refresh()
        {
            try
            {
                reports = store.ListReports(scenarioName());
                controls.Clear();
                comparison.Clear();
                if (reports.Count == 0)
                {
                    controls.Add(new Label("No saved terminal reports for this scenario."));
                    return;
                }
                var choices = reports.Select(report => report.JobId + " • " + report.Status).ToList();
                var baseline = new PopupField<string>("Baseline", choices, Math.Min(1, choices.Count - 1)) { name = "baselineReport" };
                var candidate = new PopupField<string>("Candidate", choices, 0) { name = "candidateReport" };
                controls.Add(baseline);
                controls.Add(candidate);
                controls.Add(
                    new Button(() => Compare(reports[baseline.index], reports[candidate.index])) { text = "Compare reports", name = "compareReports" }
                );
                controls.Add(new Label("Newest reports first. Comparing stored data does not query the scene or running job."));
            }
            catch (Exception exception)
            {
                message(exception.Message, true);
            }
        }

        private void Add(string text)
        {
            var label = new Label(text);
            label.AddToClassList("scenario-note");
            comparison.Add(label);
        }

        private static string Key(PlayScenarioStepResult step) => (step.Stage ?? "main") + "." + step.Iteration + "." + (step.StepIndex + 1);

        private static string Duration(PlayScenarioStepResult step) =>
            step?.StartedUnixMs != null && step.FinishedUnixMs != null ? (step.FinishedUnixMs.Value - step.StartedUnixMs.Value) + " ms" : "n/a";

        internal static void AppendStructuredDetails(JObject data, Action<string> add)
        {
            if (data["failure"] is JObject failure)
                AppendFailure("Failure", failure, add);
            if (data["cleanup_failures"] is JArray cleanup)
                foreach (JObject item in cleanup.OfType<JObject>())
                    AppendFailure("Cleanup failure", item, add);
            if (data["resource_checks"] is JArray resources)
                foreach (JObject item in resources.OfType<JObject>())
                {
                    add(
                        "Resource release check, iteration "
                            + item["iteration"]
                            + ": "
                            + ((bool?)item["passed"] == true ? "passed" : "failed")
                            + "; new runtime ScriptableObjects "
                            + (item["new_scriptable_objects"]?.Type == JTokenType.Integer ? item["new_scriptable_objects"].ToString() : "unavailable")
                            + "; subscriptions "
                            + (item["new_subscriptions"]?.Type == JTokenType.Integer ? item["new_subscriptions"].ToString() : "unavailable")
                            + "; handles "
                            + (item["new_handles"]?.Type == JTokenType.Integer ? item["new_handles"].ToString() : "unavailable")
                    );
                    if (item["error"]?.Type == JTokenType.String)
                        add("Resource check error: " + item["error"]);
                }
            if (data["cleanup_error"]?.Type == JTokenType.String)
                add("Cleanup error: " + data["cleanup_error"]);
            if (data["runner_resources_released"]?.Type == JTokenType.Boolean)
                add("Runner resources released: " + data["runner_resources_released"]);
            if (data["reproduction"] is JObject reproduction)
            {
                add("Definition hash: " + reproduction["definition_hash"]);
                add("Unity: " + reproduction["unity_version"] + "; package: " + reproduction["package_version"]);
                if (reproduction["source_revision"]?.Type == JTokenType.String)
                    add("Source revision (caller-provided): " + reproduction["source_revision"]);
            }
        }

        private static void AppendFailure(string label, JObject failure, Action<string> add)
        {
            add(
                label
                    + " code: "
                    + failure["code"]
                    + "; stage: "
                    + failure["stage"]
                    + "; iteration: "
                    + failure["iteration"]
                    + "; step: "
                    + failure["step_index"]
            );
            add(label + " detail: " + failure["message"]);
            foreach (string field in new[] { "target", "component", "property_path", "expected", "actual" })
                if (failure[field] != null && failure[field].Type != JTokenType.Null)
                    add(field.Replace('_', ' ') + ": " + failure[field]);
        }

        private void Compare(PlayScenarioRun baseline, PlayScenarioRun candidate)
        {
            comparison.Clear();
            if (baseline.JobId == candidate.JobId)
            {
                Add("Choose two different reports to compare.");
                return;
            }
            Add($"Baseline {baseline.JobId}: {baseline.Status}; candidate {candidate.JobId}: {candidate.Status}");
            if (
                JObject.FromObject(baseline.Scenario, JsonSerializer.Create()).ToString(Formatting.None)
                != JObject.FromObject(candidate.Scenario, JsonSerializer.Create()).ToString(Formatting.None)
            )
                Add("Definitions differ. Rows align by stage, iteration and step index; review changed step names/actions.");
            var previous = baseline.Steps.GroupBy(Key).ToDictionary(group => group.Key, group => group.First());
            var current = candidate.Steps.GroupBy(Key).ToDictionary(group => group.Key, group => group.First());
            if (previous.Count != baseline.Steps.Count || current.Count != candidate.Steps.Count)
                Add("A report contains duplicate step result keys. The first result for each key is shown.");
            foreach (string key in previous.Keys.Union(current.Keys))
            {
                previous.TryGetValue(key, out var left);
                current.TryGetValue(key, out var right);
                Add(
                    $"{key} {left?.Name ?? "(absent)"} ({left?.Action ?? "n/a"}) → {right?.Name ?? "(absent)"} ({right?.Action ?? "n/a"}): {left?.Status ?? "absent"} → {right?.Status ?? "absent"}; duration {Duration(left)} → {Duration(right)}; polls {left?.PollCount.ToString() ?? "n/a"} → {right?.PollCount.ToString() ?? "n/a"}"
                );
            }
            Add("Baseline metrics: " + (baseline.MetricsSummary ?? "No summary"));
            foreach (string warning in baseline.MetricWarnings)
                Add("Baseline warning: " + warning);
            Add("Candidate metrics: " + (candidate.MetricsSummary ?? "No summary"));
            foreach (string warning in candidate.MetricWarnings)
                Add("Candidate warning: " + warning);
            Add("Baseline structured details:");
            AppendStructuredDetails(JObject.FromObject(baseline, JsonSerializer.Create()), Add);
            Add("Candidate structured details:");
            AppendStructuredDetails(JObject.FromObject(candidate, JsonSerializer.Create()), Add);
            if (!string.IsNullOrEmpty(baseline.Error))
                Add("Baseline error: " + baseline.Error);
            if (!string.IsNullOrEmpty(candidate.Error))
                Add("Candidate error: " + candidate.Error);
        }
    }
}
