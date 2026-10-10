using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor.UIElements;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    internal sealed class PlayScenarioSuiteEditor
    {
        private readonly Func<JObject, object> handle;
        private readonly Action<string, bool> message;
        private readonly Action<JObject> start;
        private readonly Func<string, string, string, string, string, int> dialog;
        private readonly Func<string> readDraft;
        private readonly Func<string> readClean;
        private readonly Action<string, bool> persist;
        private readonly PlayScenarioStore scenarios;
        private readonly TextField name;
        private readonly TextField names;
        private readonly TextField tags;
        private readonly TextField revision;
        private readonly PopupField<string> policy;
        private readonly IntegerField repeats;
        private readonly IntegerField timeout;
        private readonly VisualElement library;
        private readonly ScrollView history;
        private readonly Label preview;
        private readonly Button run;
        private bool restoring;

        internal PlayScenarioSuiteEditor(
            VisualElement parent,
            PlayScenarioStore scenarios,
            Func<JObject, object> handle,
            Action<string, bool> message,
            Action<JObject> start,
            Func<string, string, string, string, string, int> dialog,
            Func<string> readDraft,
            Func<string> readClean,
            Action<string, bool> persist,
            Func<JObject> readRunOptions,
            Action<JObject> writeRunOptions
        )
        {
            this.scenarios = scenarios;
            this.handle = handle;
            this.message = message;
            this.start = start;
            this.dialog = dialog;
            this.readDraft = readDraft;
            this.readClean = readClean;
            this.persist = persist;
            var section = new Foldout { text = "Scenario suites", name = "scenarioSuites" };
            section.AddToClassList("scenario-suite-editor");
            section.Add(new Label("Save an ordered queue, optionally adding saved scenarios with any matching tag. Unity owns the queue and active child."));
            section.Add(new Button(RefreshLibrary) { name = "refreshSuites", text = "Refresh suites" });
            library = new VisualElement { name = "savedSuites" };
            section.Add(library);
            name = Field(section, "Suite name", "suiteName", false);
            names = Field(section, "Ordered scenario names (one per line)", "suiteScenarios", true);
            tags = Field(section, "Any matching tags (one per line)", "suiteTags", true);
            policy = new PopupField<string>("Failure policy", new List<string> { "stop", "continue" }, 0) { name = "suiteFailurePolicy" };
            policy.RegisterValueChangedCallback(_ => Edited());
            section.Add(policy);
            section.Add(new Button(Preview) { name = "previewSuite", text = "Preview saved selection" });
            preview = new Label { name = "suitePreview" };
            preview.AddToClassList("scenario-note");
            section.Add(preview);
            section.Add(new Button(() => Save()) { name = "saveSuite", text = "Save suite" });
            section.Add(new Button(Delete) { name = "deleteSuite", text = "Delete saved suite" });
            JObject runOptions = readRunOptions();
            repeats = new IntegerField("Child repeat count") { name = "suiteRepeatCount", value = (int?)runOptions["repeat_count"] ?? 1 };
            timeout = new IntegerField("Total suite timeout (s)") { name = "suiteTimeout", value = (int?)runOptions["timeout_seconds"] ?? 300 };
            revision = new TextField("Source revision label (optional)")
            {
                name = "suiteSourceRevision",
                value = (string)runOptions["source_revision"] ?? "",
                maxLength = 128,
            };
            Action persistRunOptions = () =>
                writeRunOptions(
                    new JObject
                    {
                        ["repeat_count"] = repeats.value,
                        ["timeout_seconds"] = timeout.value,
                        ["source_revision"] = revision.value,
                    }
                );
            repeats.RegisterValueChangedCallback(_ => persistRunOptions());
            timeout.RegisterValueChangedCallback(_ => persistRunOptions());
            revision.RegisterValueChangedCallback(_ => persistRunOptions());
            section.Add(repeats);
            section.Add(timeout);
            section.Add(revision);
            section.Add(
                new Label(
                    "Suite uses saved scenario definitions frozen at admission. Total timeout: 1–1800 s. Cleanup and report finalization finish before the next child."
                )
            );
            run = new Button(Run) { name = "runSuite", text = "Save & Run suite" };
            section.Add(run);
            section.Add(new Button(RefreshReports) { name = "refreshSuiteReports", text = "Refresh suite reports" });
            history = new ScrollView { name = "suiteReports" };
            history.AddToClassList("scenario-history-comparison");
            section.Add(history);
            parent.Add(section);
            Restore(ParseDraft(readDraft()));
        }

        private TextField Field(VisualElement parent, string label, string elementName, bool multiline)
        {
            var field = new TextField(label) { name = elementName, multiline = multiline };
            if (multiline)
                field.AddToClassList("scenario-suite-lines");
            field.RegisterValueChangedCallback(_ => Edited());
            parent.Add(field);
            return field;
        }

        private static JObject ParseDraft(string json) =>
            string.IsNullOrEmpty(json)
                ? new JObject
                {
                    ["schema_version"] = 1,
                    ["name"] = "smoke-suite",
                    ["scenarios"] = new JArray(),
                    ["tags"] = new JArray(),
                    ["failure_policy"] = "stop",
                }
                : JObject.Parse(json);

        private static JArray Lines(string value) =>
            new JArray(
                (value ?? "").Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries).Select(item => item.Trim()).Where(item => item.Length > 0)
            );

        private JObject Definition() =>
            new JObject
            {
                ["schema_version"] = 1,
                ["name"] = name.value,
                ["scenarios"] = Lines(names.value),
                ["tags"] = Lines(tags.value),
                ["failure_policy"] = policy.value,
            };

        private void Edited()
        {
            if (restoring)
                return;
            persist(Definition().ToString(Formatting.None), false);
            preview.text = "Selection changed. Preview reads saved definitions only when requested.";
        }

        private void Restore(JObject definition)
        {
            restoring = true;
            name.SetValueWithoutNotify((string)definition["name"] ?? "");
            names.SetValueWithoutNotify(string.Join("\n", ((JArray)definition["scenarios"] ?? new JArray()).Values<string>()));
            tags.SetValueWithoutNotify(string.Join("\n", ((JArray)definition["tags"] ?? new JArray()).Values<string>()));
            policy.SetValueWithoutNotify((string)definition["failure_policy"] ?? "stop");
            restoring = false;
        }

        private JObject Call(JObject request)
        {
            object response = handle(request);
            if (!(response is SuccessResponse success))
                throw new InvalidOperationException(response is ErrorResponse error ? error.Error : "Unexpected suite response.");
            return success.Data as JObject ?? JObject.FromObject(success.Data, JsonSerializer.Create());
        }

        internal bool Save()
        {
            try
            {
                JObject saved = Call(new JObject { ["action"] = "suite_save", ["suite"] = Definition() });
                Restore(saved);
                persist(saved.ToString(Formatting.None), true);
                message("Saved suite " + saved["name"] + ".", false);
                return true;
            }
            catch (Exception exception)
            {
                message(exception.Message, true);
                return false;
            }
        }

        internal void Discard()
        {
            JObject clean = ParseDraft(readClean());
            Restore(clean);
            persist(clean.ToString(Formatting.None), true);
        }

        internal void SetRunning(bool value) => run.SetEnabled(!value);

        private void RefreshLibrary()
        {
            try
            {
                JObject data = Call(new JObject { ["action"] = "suite_list" });
                library.Clear();
                foreach (string savedName in ((JArray)data["suites"] ?? new JArray()).Values<string>())
                {
                    string selected = savedName;
                    library.Add(new Button(() => Load(selected)) { name = "savedSuite_" + selected, text = selected });
                }
                if (library.childCount == 0)
                    library.Add(new Label("No saved suites."));
            }
            catch (Exception exception)
            {
                message(exception.Message, true);
            }
        }

        private void Load(string selected)
        {
            try
            {
                if (readDraft() != readClean())
                {
                    int choice = dialog("Unsaved suite changes", "Save suite changes before opening another suite?", "Save", "Cancel", "Discard");
                    if (choice == 1 || (choice == 0 && !Save()))
                        return;
                }
                JObject loaded = Call(new JObject { ["action"] = "suite_get", ["name"] = selected });
                Restore(loaded);
                persist(loaded.ToString(Formatting.None), true);
                preview.text = "Loaded " + selected + ". Preview to inspect current saved scenario selection.";
            }
            catch (Exception exception)
            {
                message(exception.Message, true);
            }
        }

        private void Delete()
        {
            if (
                dialog(
                    "Delete suite",
                    "Delete saved suite '" + name.value + "'? Unsaved suite edits will also be discarded; existing suite reports remain available.",
                    "Delete",
                    "Cancel",
                    ""
                ) != 0
            )
                return;
            try
            {
                Call(new JObject { ["action"] = "suite_delete", ["name"] = name.value });
                JObject empty = ParseDraft(null);
                Restore(empty);
                persist(empty.ToString(Formatting.None), true);
                RefreshLibrary();
                message("Deleted saved suite.", false);
            }
            catch (Exception exception)
            {
                message(exception.Message, true);
            }
        }

        private void Preview()
        {
            try
            {
                var selected = Lines(names.value).Values<string>().ToList();
                var requested = Lines(tags.value).Values<string>().ToList();
                foreach (string scenarioName in scenarios.List())
                    if (scenarios.Get(scenarioName).Tags.Any(requested.Contains) && !selected.Contains(scenarioName))
                        selected.Add(scenarioName);
                if (selected.Count == 0 || selected.Count > 16 || selected.Distinct().Count() != selected.Count)
                    throw new InvalidOperationException("Selection must resolve to 1–16 unique saved scenarios.");
                foreach (string scenarioName in selected)
                    scenarios.Get(scenarioName);
                preview.text = "Saved selection: " + string.Join(" → ", selected) + ". Preview is not an execution reservation.";
            }
            catch (Exception exception)
            {
                preview.text = exception.Message;
                message(exception.Message, true);
            }
        }

        private void Run()
        {
            if (!Save())
                return;
            var request = new JObject
            {
                ["action"] = "suite_run",
                ["name"] = name.value,
                ["repeat_count"] = repeats.value,
                ["timeout_seconds"] = timeout.value,
            };
            if (!string.IsNullOrEmpty(revision.value))
                request["source_revision"] = revision.value;
            start(request);
        }

        internal void RefreshReports()
        {
            try
            {
                JObject data = Call(new JObject { ["action"] = "suite_reports", ["name"] = name.value });
                history.Clear();
                foreach (JObject report in ((JArray)data["reports"] ?? new JArray()).OfType<JObject>())
                    AppendReport(
                        report,
                        text =>
                        {
                            var label = new Label(text);
                            label.AddToClassList("scenario-note");
                            history.Add(label);
                        }
                    );
                if (history.childCount == 0)
                    history.Add(new Label("No saved suite reports."));
            }
            catch (Exception exception)
            {
                message(exception.Message, true);
            }
        }

        internal static void AppendReport(JObject report, Action<string> add)
        {
            add("Suite: " + report["suite_id"] + " • " + report["status"]);
            if (report["error"]?.Type == JTokenType.String)
                add("Suite error: " + report["error"]);
            foreach (JObject child in ((JArray)report["scenarios"] ?? new JArray()).OfType<JObject>())
            {
                add(
                    child["name"]
                        + ": "
                        + child["status"]
                        + "; job: "
                        + child["job_id"]
                        + (child["skip_reason"]?.Type == JTokenType.String ? "; " + child["skip_reason"] : "")
                );
                if (child["report"] is JObject detail)
                {
                    if (detail["error"]?.Type == JTokenType.String)
                        add("Child error: " + detail["error"]);
                    PlayScenarioHistoryEditor.AppendStructuredDetails(detail, add);
                    if (detail["report_path"]?.Type == JTokenType.String)
                        add("Child report: " + detail["report_path"]);
                    if (detail["failure_diagnostics"] is JObject diagnostics)
                    {
                        if (diagnostics["screenshot_path"]?.Type == JTokenType.String)
                            add("Screenshot: " + diagnostics["screenshot_path"]);
                        else if (diagnostics["screenshot_error"]?.Type == JTokenType.String)
                            add("Screenshot: " + diagnostics["screenshot_error"]);
                    }
                }
            }
            if (report["source_revision"]?.Type == JTokenType.String)
                add("Source revision (caller-provided): " + report["source_revision"]);
            if (report["report_path"]?.Type == JTokenType.String)
                add("Suite report: " + report["report_path"]);
            if (report["report_error"]?.Type == JTokenType.String)
                add("Suite report save error: " + report["report_error"]);
        }
    }
}
