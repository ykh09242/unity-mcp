using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    /// <summary>Retained report sections. Polls update values without replacing historical controls.</summary>
    internal sealed class PlayScenarioReportView
    {
        private readonly Label status = new Label { name = "runStatus" };
        private readonly TextRows prefix;
        private readonly TextRows paths;
        private readonly TextRows suffix;
        private readonly VisualElement history;
        private readonly Label error;
        private readonly Foldout logs;
        private readonly TextRows droppedLogs;
        private readonly List<StepRow> steps = new List<StepRow>();
        private readonly List<LogRow> logRows = new List<LogRow>();
        private readonly List<SuiteChildRows> children = new List<SuiteChildRows>();

        internal PlayScenarioReportView(VisualElement root, bool suite)
        {
            root.Clear();
            root.Add(status);
            prefix = new TextRows(root);
            if (!suite)
            {
                error = Note("");
                error.AddToClassList("scenario-error");
                error.style.display = DisplayStyle.None;
                root.Add(error);
                paths = new TextRows(root);
            }
            history = Section(root);
            suffix = new TextRows(root);
            if (!suite)
            {
                logs = new Foldout { value = true };
                root.Add(logs);
                droppedLogs = new TextRows(logs);
            }
        }

        internal void RenderScenario(JObject data)
        {
            Set(status, (string)data["status"] + " • " + (string)data["phase"]);
            var before = new List<string> { "Job: " + data["job_id"] };
            PlayScenarioHistoryEditor.AppendStructuredDetails(data, before.Add);
            prefix.Set(before);
            string errorText = (string)data["error"];
            Set(error, errorText ?? "");
            error.style.display = string.IsNullOrEmpty(errorText) ? DisplayStyle.None : DisplayStyle.Flex;
            var reportPaths = new List<string>();
            Add(reportPaths, "Report: ", (string)data["report_path"]);
            Add(reportPaths, "Report save error: ", (string)data["report_error"]);
            paths.Set(reportPaths);

            int count = 0;
            if (data["steps"] is JArray source)
                foreach (JObject step in source.OfType<JObject>())
                {
                    StepValue value = new StepValue(step);
                    if (count == steps.Count)
                    {
                        var row = new StepRow { Label = Note(value.Text()), Value = value };
                        row.Label.AddToClassList("scenario-result-row");
                        history.Add(row.Label);
                        steps.Add(row);
                    }
                    else if (!steps[count].Value.Equals(value))
                    {
                        steps[count].Value = value;
                        Set(steps[count].Label, value.Text());
                    }
                    count++;
                }
            while (steps.Count > count)
            {
                steps[steps.Count - 1].Label.RemoveFromHierarchy();
                steps.RemoveAt(steps.Count - 1);
            }

            var after = new List<string>();
            Add(after, "", (string)data["metrics_summary"]);
            if (data["metric_warnings"] is JArray warnings)
                foreach (string warning in warnings.Values<string>())
                    after.Add("Diagnostic warning: " + warning);
            if (data["metrics"] is JArray metrics)
                foreach (JObject token in metrics.OfType<JObject>())
                {
                    var sample = token.ToObject<PlayScenarioMetricsSnapshot>();
                    after.Add(
                        $"Iteration {sample.Iteration} metrics: managed {sample.ManagedBytes?.ToString() ?? "unavailable"}, allocated {sample.AllocatedBytes?.ToString() ?? "unavailable"}, objects {sample.ObjectCount?.ToString() ?? "unavailable"}; subscriptions {sample.RunnerSubscriptionCount?.ToString() ?? "unavailable"}, handles {sample.RunnerHandleCount?.ToString() ?? "unavailable"}"
                            + (sample.IgnoredForTrend ? " (warmup)" : "")
                            + (string.IsNullOrEmpty(sample.Error) ? "" : " — " + sample.Error)
                    );
                }
            if (data["failure_diagnostics"] is JObject diagnosticToken)
            {
                var diagnostic = diagnosticToken.ToObject<PlayScenarioFailureDiagnostics>();
                after.Add(
                    "Failure observation: "
                        + diagnostic.Observation
                        + "\n"
                        + diagnostic.TargetDetail
                        + "\nScreenshot: "
                        + (
                            diagnostic.ScreenshotPath
                            ?? diagnostic.ScreenshotError
                            ?? (
                                (string)data["status"] == "running" && (bool?)data["scenario"]?["diagnostics"]?["screenshot_on_failure"] == true
                                    ? "Pending capture."
                                    : "Not requested"
                            )
                        )
                );
            }
            suffix.Set(after);

            count = 0;
            if (data["logs"] is JArray captured)
                foreach (JObject log in captured.OfType<JObject>())
                {
                    var value = new LogValue(log);
                    if (count == logRows.Count)
                    {
                        var row = new LogRow { Label = Note(value.Text()), Value = value };
                        row.Label.AddToClassList("scenario-result-row");
                        // The dropped-log note stays after the captured entries.
                        logs.Insert(count, row.Label);
                        logRows.Add(row);
                    }
                    else if (!logRows[count].Value.Equals(value))
                    {
                        logRows[count].Value = value;
                        Set(logRows[count].Label, value.Text());
                    }
                    count++;
                }
            while (logRows.Count > count)
            {
                logRows[logRows.Count - 1].Label.RemoveFromHierarchy();
                logRows.RemoveAt(logRows.Count - 1);
            }
            logs.text = "Captured logs (" + count + ")";
            long dropped = (long?)data["dropped_log_count"] ?? 0;
            droppedLogs.Set(dropped > 0 ? new[] { "Older captured logs dropped: " + dropped } : Array.Empty<string>());
        }

        internal void RenderSuite(JObject data, IReadOnlyList<JObject> reports)
        {
            Set(status, (string)data["status"] + " • suite");
            var before = new List<string> { "Suite: " + data["suite_id"] + " • " + data["status"] };
            if (data["error"]?.Type == JTokenType.String)
                before.Add("Suite error: " + data["error"]);
            prefix.Set(before);
            int count = 0;
            if (data["scenarios"] is JArray scenarios)
                foreach (JObject child in scenarios.OfType<JObject>())
                {
                    JObject report = reports != null ? reports[count] : child["report"] as JObject;
                    if (count == children.Count)
                        children.Add(new SuiteChildRows(history));
                    children[count].Render(child, report);
                    count++;
                }
            while (children.Count > count)
            {
                children[children.Count - 1].Rows.Root.RemoveFromHierarchy();
                children.RemoveAt(children.Count - 1);
            }
            var after = new List<string>();
            if (data["source_revision"]?.Type == JTokenType.String)
                after.Add("Source revision (caller-provided): " + data["source_revision"]);
            if (data["report_path"]?.Type == JTokenType.String)
                after.Add("Suite report: " + data["report_path"]);
            if (data["report_error"]?.Type == JTokenType.String)
                after.Add("Suite report save error: " + data["report_error"]);
            suffix.Set(after);
        }

        private sealed class SuiteChildRows
        {
            internal readonly TextRows Rows;
            private JObject metadata;
            private JObject evidence;

            internal SuiteChildRows(VisualElement root) => Rows = new TextRows(root);

            internal void Render(JObject child, JObject report)
            {
                if (JToken.DeepEquals(metadata, child) && ReferenceEquals(evidence, report))
                    return;
                // Metadata is bounded and detached; retaining a child token would retain its
                // entire parent snapshot, including unrelated historical evidence.
                metadata = new JObject
                {
                    ["name"] = child["name"]?.DeepClone(),
                    ["status"] = child["status"]?.DeepClone(),
                    ["job_id"] = child["job_id"]?.DeepClone(),
                    ["skip_reason"] = child["skip_reason"]?.DeepClone(),
                };
                evidence = report;
                var rows = new List<string>();
                rows.Add(
                    child["name"]
                        + ": "
                        + child["status"]
                        + "; job: "
                        + child["job_id"]
                        + (child["skip_reason"]?.Type == JTokenType.String ? "; " + child["skip_reason"] : "")
                );
                if (report != null)
                {
                    if (report["error"]?.Type == JTokenType.String)
                        rows.Add("Child error: " + report["error"]);
                    PlayScenarioHistoryEditor.AppendStructuredDetails(report, rows.Add);
                    if (report["report_path"]?.Type == JTokenType.String)
                        rows.Add("Child report: " + report["report_path"]);
                    if (report["failure_diagnostics"] is JObject diagnostics)
                    {
                        if (diagnostics["screenshot_path"]?.Type == JTokenType.String)
                            rows.Add("Screenshot: " + diagnostics["screenshot_path"]);
                        else if (diagnostics["screenshot_error"]?.Type == JTokenType.String)
                            rows.Add("Screenshot: " + diagnostics["screenshot_error"]);
                    }
                }
                Rows.Set(rows);
            }
        }

        private sealed class TextRows
        {
            internal readonly VisualElement Root;
            private readonly List<Label> rows = new List<Label>();

            internal TextRows(VisualElement parent) => Root = Section(parent);

            internal void Set(IReadOnlyList<string> values)
            {
                for (int index = 0; index < values.Count; index++)
                    if (index == rows.Count)
                    {
                        Label row = Note(values[index]);
                        Root.Add(row);
                        rows.Add(row);
                    }
                    else
                        PlayScenarioReportView.Set(rows[index], values[index]);
                while (rows.Count > values.Count)
                {
                    rows[rows.Count - 1].RemoveFromHierarchy();
                    rows.RemoveAt(rows.Count - 1);
                }
            }
        }

        private sealed class StepRow
        {
            internal Label Label;
            internal StepValue Value;
        }

        // Scalar values cannot retain the Parent links of an old JSON report. Unchanged rows
        // compare without building display strings, JSON copies or another parsed report graph.
        private readonly struct StepValue : IEquatable<StepValue>
        {
            private readonly string stage,
                name,
                status,
                detail;
            private readonly int iteration,
                index;
            private readonly long searches,
                visits;

            internal StepValue(JObject value)
            {
                stage = (string)value["stage"] ?? "main";
                name = (string)value["name"];
                status = (string)value["status"];
                detail = (string)value["detail"];
                iteration = (int?)value["iteration"] ?? 0;
                index = (int?)value["step_index"] ?? 0;
                searches = (long?)value["query_counts"]?["target_searches"] ?? 0;
                visits = (long?)value["query_counts"]?["hierarchy_visits"] ?? 0;
            }

            public bool Equals(StepValue other) =>
                stage == other.stage
                && name == other.name
                && status == other.status
                && detail == other.detail
                && iteration == other.iteration
                && index == other.index
                && searches == other.searches
                && visits == other.visits;

            internal string Text() =>
                $"{stage}.{iteration}.{index + 1} {name} — {status}\n{detail}\nQueries: target searches {searches}; hierarchy visits {visits}";
        }

        private sealed class LogRow
        {
            internal Label Label;
            internal LogValue Value;
        }

        private readonly struct LogValue : IEquatable<LogValue>
        {
            private readonly string type,
                message,
                stack;

            internal LogValue(JObject value)
            {
                type = (string)value["type"];
                message = (string)value["message"];
                stack = (string)value["stack_trace"];
            }

            public bool Equals(LogValue other) => type == other.type && message == other.message && stack == other.stack;

            internal string Text() => type + ": " + message + (string.IsNullOrEmpty(stack) ? "" : "\n" + stack);
        }

        private static void Add(List<string> rows, string prefix, string text)
        {
            if (!string.IsNullOrEmpty(text))
                rows.Add(prefix + text);
        }

        private static void Set(Label label, string text)
        {
            if (label.text != text)
                label.text = text;
        }

        private static Label Note(string text)
        {
            var label = new Label(text);
            label.AddToClassList("scenario-note");
            return label;
        }

        private static VisualElement Section(VisualElement parent)
        {
            var section = new VisualElement();
            section.style.flexShrink = 0;
            parent.Add(section);
            return section;
        }
    }
}
