using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnity.Editor.Windows.PlayScenarios
{
    public sealed class PlayScenarioWindow : EditorWindow
    {
        internal const string SessionJobKey = "MCPForUnity.PlayScenarios.WindowJobId";

        [SerializeField]
        private string draftJson;

        [SerializeField]
        private string savedName;

        [SerializeField]
        private bool dirty;

        [SerializeField]
        private string jobId;

        [SerializeField]
        private int repeatCount = 1;

        [SerializeField]
        private int runTimeoutSeconds = 300;
        private PlayScenarioDefinition draft;
        private PlayScenarioRun report;
        private PlayScenarioStore injectedStore;
        private ScrollView scenarioList;
        private ScrollView stepList;
        private ScrollView runResults;
        private TextField nameField;
        private TextField copyNameField;
        private IntegerField pollField;
        private Label validation;
        private Label message;
        private Button saveButton;
        private Button copyButton;
        private Button deleteButton;
        private Button runButton;
        private Button cancelButton;
        private bool polling;
        private double nextStatusRefresh;
        internal Func<string, int, int, string, object> StartRun;
        internal Func<string, object> ReadStatus;
        internal Func<string, object> CancelRun;
        internal Func<string, string, string, string, string, int> ShowDialog;
        internal bool IsPolling => polling;
        internal string CurrentJobId => jobId;
        internal PlayScenarioDefinition Draft => draft;
        private PlayScenarioStore Store => injectedStore ?? (injectedStore = new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath)));

        [MenuItem("Window/MCP for Unity/Play Scenarios", priority = 4)]
        public static void Open() => GetWindow<PlayScenarioWindow>("Play Scenarios").Show();

        private void OnEnable()
        {
            titleContent = new GUIContent("Play Scenarios");
            minSize = new Vector2(850, 560);
            saveChangesMessage = "Save changes to this Play Scenario before closing?";
            draft = null;
            RestoreDraft();
            if (string.IsNullOrEmpty(jobId))
                jobId = SessionState.GetString(SessionJobKey, "");
            hasUnsavedChanges = dirty;
        }

        private void OnDisable()
        {
            StopPolling();
            StartRun = null;
            ReadStatus = null;
            CancelRun = null;
            ShowDialog = null;
        }

        public void CreateGUI()
        {
            StopPolling();
            RestoreDraft();
            rootVisualElement.Clear();
            rootVisualElement.AddToClassList("mcp-editor");
            rootVisualElement.AddToClassList("play-scenarios");
            rootVisualElement.EnableInClassList("unity-theme-light", !EditorGUIUtility.isProSkin);
            rootVisualElement.EnableInClassList("unity-theme-dark", EditorGUIUtility.isProSkin);
            string packageRoot = AssetPathUtility.GetMcpPackageRootPath();
            foreach (string style in new[] { "Components/Common.uss", "PlayScenarios/PlayScenarioWindow.uss" })
            {
                var sheet = AssetDatabase.LoadAssetAtPath<StyleSheet>($"{packageRoot}/Editor/Windows/{style}");
                if (sheet != null && !rootVisualElement.styleSheets.Contains(sheet))
                    rootVisualElement.styleSheets.Add(sheet);
            }
            var heading = new Label("Play Scenarios");
            heading.AddToClassList("scenario-heading");
            rootVisualElement.Add(heading);
            rootVisualElement.Add(
                Note("Save a repeatable menu → Start → game → Player flow. Configure the scene assets and exact hierarchy paths before running.")
            );
            var columns = new VisualElement();
            columns.AddToClassList("scenario-columns");
            rootVisualElement.Add(columns);
            BuildSavedList(columns);
            BuildEditor(columns);
            BuildResults(columns);
            message = new Label { name = "scenarioMessage" };
            message.AddToClassList("scenario-message");
            rootVisualElement.Add(message);
            RefreshSavedList();
            RenderDraft();
            if (!string.IsNullOrEmpty(jobId))
                RefreshRunStatus();
            else
                RenderReport();
        }

        private void BuildSavedList(VisualElement columns)
        {
            var panel = Panel("scenario-library", "Saved scenarios");
            var toolbar = Row();
            toolbar.Add(ActionButton("Refresh", "refreshScenarios", RefreshSavedList));
            toolbar.Add(ActionButton("New", "newScenario", NewScenario));
            panel.Add(toolbar);
            scenarioList = new ScrollView { name = "savedScenarios" };
            scenarioList.AddToClassList("scenario-scroll");
            panel.Add(scenarioList);
            panel.Add(Note("Definitions are saved in ProjectSettings/MCPForUnity/PlayScenarios."));
            columns.Add(panel);
        }

        private void BuildEditor(VisualElement columns)
        {
            var panel = Panel("scenario-editor", "Definition");
            nameField = new TextField("Name") { name = "scenarioName" };
            nameField.RegisterValueChangedCallback(evt => Edit(() => draft.Name = evt.newValue));
            panel.Add(nameField);
            pollField = new IntegerField("Poll interval (ms)") { name = "pollInterval" };
            pollField.RegisterValueChangedCallback(evt => Edit(() => draft.PollIntervalMs = evt.newValue));
            panel.Add(pollField);
            panel.Add(Note("Name: lowercase slug. Poll: 100–2000 ms. First step must load_scene. Each wait is bounded to 1–120 seconds."));
            var toolbar = Row();
            saveButton = ActionButton("Save", "saveScenario", () => TrySave());
            toolbar.Add(saveButton);
            deleteButton = ActionButton("Delete", "deleteScenario", DeleteScenario);
            toolbar.Add(deleteButton);
            toolbar.Add(ActionButton("Add Step", "addStep", AddStep));
            panel.Add(toolbar);
            var copyRow = Row();
            copyNameField = new TextField("Copy name") { name = "copyScenarioName" };
            copyNameField.AddToClassList("scenario-copy-name");
            copyNameField.RegisterValueChangedCallback(_ => UpdateValidation());
            copyRow.Add(copyNameField);
            copyButton = ActionButton("Save As Copy", "saveScenarioCopy", () => SaveCopy());
            copyRow.Add(copyButton);
            panel.Add(copyRow);
            validation = new Label { name = "scenarioValidation" };
            validation.AddToClassList("scenario-validation");
            panel.Add(validation);
            stepList = new ScrollView { name = "scenarioSteps" };
            stepList.AddToClassList("scenario-scroll");
            panel.Add(stepList);
            columns.Add(panel);
        }

        private void BuildResults(VisualElement columns)
        {
            var panel = Panel("scenario-results", "Run & results");
            var repeats = new IntegerField("Repeat count") { name = "repeatCount", value = repeatCount };
            repeats.RegisterValueChangedCallback(evt => repeatCount = evt.newValue);
            panel.Add(repeats);
            var timeout = new IntegerField("Run timeout (s)") { name = "runTimeout", value = runTimeoutSeconds };
            timeout.RegisterValueChangedCallback(evt => runTimeoutSeconds = evt.newValue);
            panel.Add(timeout);
            panel.Add(Note("Repeat: 1–10. Total timeout: 1–1800 s. Each repeat reloads the first scene; static state and DontDestroyOnLoad objects persist."));
            var toolbar = Row();
            runButton = ActionButton("Save & Run", "runScenario", RunScenario);
            runButton.tooltip = "Saves the validated definition, then starts Play Mode. The backend run continues if this window is closed.";
            cancelButton = ActionButton("Cancel Run", "cancelScenario", CancelScenario);
            toolbar.Add(runButton);
            toolbar.Add(cancelButton);
            panel.Add(toolbar);
            panel.Add(Note("Closing this window does not cancel a run. Use Cancel Run to stop it. Finishing or cancelling leaves Play Mode unchanged."));
            runResults = new ScrollView { name = "runResults" };
            runResults.AddToClassList("scenario-scroll");
            panel.Add(runResults);
            columns.Add(panel);
        }

        private static VisualElement Panel(string className, string title)
        {
            var panel = new VisualElement();
            panel.AddToClassList("scenario-panel");
            panel.AddToClassList(className);
            var label = new Label(title);
            label.AddToClassList("scenario-section-title");
            panel.Add(label);
            return panel;
        }

        private static VisualElement Row()
        {
            var row = new VisualElement();
            row.AddToClassList("scenario-row");
            return row;
        }

        private static Label Note(string text)
        {
            var label = new Label(text);
            label.AddToClassList("scenario-note");
            return label;
        }

        private static Button ActionButton(string text, string name, Action action) => new Button(action) { text = text, name = name };

        private void RestoreDraft()
        {
            if (draft != null)
                return;
            try
            {
                if (string.IsNullOrEmpty(draftJson))
                    draft = Template();
                else
                {
                    using (var reader = new JsonTextReader(new StringReader(draftJson)) { DateParseHandling = DateParseHandling.None })
                        draft = JObject.Load(reader).ToObject<PlayScenarioDefinition>(JsonSerializer.Create());
                }
            }
            catch (JsonException)
            {
                draft = Template();
            }
            if (draft == null || draft.Steps == null)
                draft = Template();
            PersistDraft();
        }

        private static PlayScenarioDefinition Template() =>
            new PlayScenarioDefinition
            {
                Name = "menu-start-player",
                Steps = new List<PlayScenarioStep>
                {
                    new PlayScenarioStep
                    {
                        Name = "Load menu",
                        Action = "load_scene",
                        Scene = "",
                    },
                    new PlayScenarioStep
                    {
                        Name = "Click Start",
                        Action = "click_ui",
                        Target = "Canvas/StartButton",
                    },
                    new PlayScenarioStep
                    {
                        Name = "Wait for game scene",
                        Action = "wait_scene",
                        Scene = "",
                    },
                    new PlayScenarioStep
                    {
                        Name = "Wait for Player",
                        Action = "wait_object",
                        Target = "Player",
                    },
                },
            };

        private static JObject Json(object value) => JObject.FromObject(value, JsonSerializer.Create());

        private void PersistDraft() => draftJson = Json(draft).ToString(Formatting.None);

        private void Edit(Action change)
        {
            change();
            PersistDraft();
            dirty = true;
            hasUnsavedChanges = true;
            UpdateValidation();
        }

        private void RenderDraft()
        {
            nameField.SetValueWithoutNotify(draft.Name);
            nameField.SetEnabled(string.IsNullOrEmpty(savedName));
            nameField.tooltip = string.IsNullOrEmpty(savedName)
                ? "A lowercase scenario name, 1–64 characters."
                : "Saved names are fixed. Save As Copy creates a separate scenario.";
            pollField.SetValueWithoutNotify(draft.PollIntervalMs);
            copyNameField.SetValueWithoutNotify("");
            deleteButton.SetEnabled(!string.IsNullOrEmpty(savedName));
            hasUnsavedChanges = dirty;
            RenderSteps();
            UpdateValidation();
        }

        private void RenderSteps() => PlayScenarioStepEditor.Render(stepList, draft, Edit, SetMessage);

        private bool ValidateDraft(out PlayScenarioDefinition validated)
        {
            try
            {
                validated = PlayScenarioDefinition.Parse(Json(draft));
                validation.text = dirty ? "Valid definition • unsaved changes" : "Valid definition";
                validation.RemoveFromClassList("scenario-error");
                return true;
            }
            catch (ArgumentException exception)
            {
                validated = null;
                validation.text = exception.Message;
                validation.AddToClassList("scenario-error");
                return false;
            }
        }

        private void UpdateValidation()
        {
            if (validation == null)
                return;
            bool valid = ValidateDraft(out _);
            saveButton.SetEnabled(valid);
            runButton.SetEnabled(valid && report?.Status != "running");
            bool copyValid = valid;
            try
            {
                PlayScenarioDefinition.ValidateName(copyNameField.value);
            }
            catch (ArgumentException)
            {
                copyValid = false;
            }
            copyButton.SetEnabled(copyValid && copyNameField.value != savedName);
        }

        private void RefreshSavedList()
        {
            if (scenarioList == null)
                return;
            try
            {
                scenarioList.Clear();
                var names = Store.List();
                if (names.Count == 0)
                    scenarioList.Add(Note("No saved scenarios. Choose New to configure your first flow."));
                foreach (string name in names)
                {
                    var button = ActionButton(name, "savedScenario_" + name, () => LoadScenario(name));
                    button.AddToClassList("scenario-list-item");
                    button.EnableInClassList("scenario-selected", name == savedName);
                    scenarioList.Add(button);
                }
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
            }
        }

        private int Dialog(string title, string text, string yes, string cancel, string alternate) =>
            ShowDialog != null ? ShowDialog(title, text, yes, cancel, alternate) : EditorUtility.DisplayDialogComplex(title, text, yes, cancel, alternate);

        private bool CanNavigate()
        {
            if (!dirty)
                return true;
            int choice = Dialog("Unsaved scenario", "Save changes before switching scenarios?", "Save", "Cancel", "Discard");
            if (choice == 0)
                return TrySave();
            return choice == 2;
        }

        private void NewScenario()
        {
            if (!CanNavigate())
                return;
            savedName = null;
            draft = Template();
            dirty = true;
            PersistDraft();
            RenderDraft();
            RefreshSavedList();
            SetMessage("Choose both scene assets and replace the template target paths.", false);
        }

        private void LoadScenario(string name)
        {
            if (name == savedName || !CanNavigate())
                return;
            try
            {
                draft = Store.Get(name);
                savedName = name;
                dirty = false;
                PersistDraft();
                RenderDraft();
                RefreshSavedList();
                SetMessage("Loaded " + name + ".", false);
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
            }
        }

        private bool TrySave()
        {
            try
            {
                SaveCurrent();
                return true;
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
                return false;
            }
        }

        private void SaveCurrent()
        {
            PlayScenarioDefinition validated = PlayScenarioDefinition.Parse(Json(draft));
            if (string.IsNullOrEmpty(savedName) && Store.List().Contains(validated.Name))
                throw new InvalidOperationException("This name already exists. Open that scenario to edit it, or choose a new name.");
            Store.Save(validated);
            draft = validated;
            savedName = validated.Name;
            dirty = false;
            PersistDraft();
            base.SaveChanges();
            RenderDraft();
            RefreshSavedList();
            SetMessage("Saved " + savedName + ".", false);
        }

        public override void SaveChanges() => SaveCurrent();

        public override void DiscardChanges()
        {
            try
            {
                draft = string.IsNullOrEmpty(savedName) ? Template() : Store.Get(savedName);
            }
            catch (FileNotFoundException)
            {
                savedName = null;
                draft = Template();
            }
            dirty = false;
            PersistDraft();
            base.DiscardChanges();
            if (nameField != null)
                RenderDraft();
        }

        private void SaveCopy()
        {
            try
            {
                PlayScenarioDefinition copy = PlayScenarioDefinition.Parse(Json(draft));
                copy.Name = copyNameField.value;
                PlayScenarioDefinition.ValidateName(copy.Name);
                if (Store.List().Contains(copy.Name))
                    throw new InvalidOperationException("Copy name already exists. Choose a new name.");
                Store.Save(copy);
                draft = copy;
                savedName = copy.Name;
                dirty = false;
                PersistDraft();
                base.SaveChanges();
                RenderDraft();
                RefreshSavedList();
                SetMessage("Created separate scenario " + savedName + ".", false);
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
            }
        }

        private void DeleteScenario()
        {
            if (string.IsNullOrEmpty(savedName))
                return;
            string deleting = savedName;
            if (
                Dialog(
                    "Delete scenario",
                    "Delete '" + deleting + "'? Unsaved edits to it will also be discarded. Existing run reports remain available.",
                    "Delete",
                    "Cancel",
                    ""
                ) != 0
            )
                return;
            try
            {
                Store.Delete(deleting);
                savedName = null;
                draft = Template();
                dirty = false;
                PersistDraft();
                hasUnsavedChanges = false;
                RenderDraft();
                RefreshSavedList();
                SetMessage("Deleted " + deleting + ".", false);
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
            }
        }

        private void AddStep()
        {
            if (draft.Steps.Count >= 32)
            {
                SetMessage("A scenario supports at most 32 steps.", true);
                return;
            }
            Edit(() =>
                draft.Steps.Add(
                    new PlayScenarioStep
                    {
                        Name = "Wait for object",
                        Action = "wait_object",
                        Target = "",
                    }
                )
            );
            RenderSteps();
        }

        private void RunScenario()
        {
            if (repeatCount < 1 || repeatCount > 10 || runTimeoutSeconds < 1 || runTimeoutSeconds > 1800)
            {
                SetMessage("Repeat count must be 1–10 and run timeout 1–1800 seconds.", true);
                return;
            }
            if (!TrySave())
                return;
            string priorId = jobId;
            jobId = Guid.NewGuid().ToString("N");
            SessionState.SetString(SessionJobKey, jobId);
            try
            {
                object result =
                    StartRun != null
                        ? StartRun(savedName, repeatCount, runTimeoutSeconds, jobId)
                        : PlayScenarioService.Start(savedName, repeatCount, runTimeoutSeconds, jobId);
                if (!AcceptReport(result))
                {
                    jobId = priorId;
                    SessionState.SetString(SessionJobKey, jobId ?? "");
                }
            }
            catch (Exception exception)
            {
                jobId = priorId;
                SessionState.SetString(SessionJobKey, jobId ?? "");
                SetMessage(exception.Message, true);
            }
        }

        private void CancelScenario()
        {
            if (string.IsNullOrEmpty(jobId))
                return;
            try
            {
                AcceptReport(CancelRun != null ? CancelRun(jobId) : PlayScenarioService.Cancel(jobId));
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
            }
        }

        private bool AcceptReport(object response)
        {
            if (!(response is SuccessResponse success))
            {
                StopPolling();
                SetMessage(response is ErrorResponse error ? error.Error : "Unexpected scenario response.", true);
                return false;
            }
            JObject data = success.Data as JObject ?? Json(success.Data);
            report = data.ToObject<PlayScenarioRun>(JsonSerializer.Create());
            if (report == null)
                throw new InvalidOperationException("Scenario response contained no report.");
            jobId = report.JobId;
            SessionState.SetString(SessionJobKey, jobId ?? "");
            RenderReport();
            if (report.Status == "running")
                StartPolling();
            else
                StopPolling();
            SetMessage(success.Message, false);
            UpdateValidation();
            return true;
        }

        private void StartPolling()
        {
            if (polling)
                return;
            polling = true;
            nextStatusRefresh = EditorApplication.timeSinceStartup + 0.5;
            EditorApplication.update += PollStatus;
        }

        private void StopPolling()
        {
            EditorApplication.update -= PollStatus;
            polling = false;
        }

        private void PollStatus()
        {
            if (!polling || EditorApplication.timeSinceStartup < nextStatusRefresh)
                return;
            nextStatusRefresh = EditorApplication.timeSinceStartup + 0.5;
            RefreshRunStatus();
        }

        private void RefreshRunStatus()
        {
            try
            {
                AcceptReport(ReadStatus != null ? ReadStatus(jobId) : PlayScenarioService.Status(jobId));
            }
            catch (Exception exception)
            {
                StopPolling();
                SetMessage(exception.Message, true);
            }
        }

        private void RenderReport()
        {
            if (runResults == null)
                return;
            runResults.Clear();
            cancelButton.SetEnabled(report?.Status == "running");
            if (report == null)
            {
                runResults.Add(Note("No run yet. Save & Run starts a job and displays its step results here."));
                return;
            }
            runResults.Add(new Label(report.Status + " • " + report.Phase) { name = "runStatus" });
            runResults.Add(Note("Job: " + report.JobId));
            if (!string.IsNullOrEmpty(report.Error))
            {
                var error = Note(report.Error);
                error.AddToClassList("scenario-error");
                runResults.Add(error);
            }
            if (!string.IsNullOrEmpty(report.ReportPath))
                runResults.Add(Note("Report: " + report.ReportPath));
            if (!string.IsNullOrEmpty(report.ReportError))
                runResults.Add(Note("Report save error: " + report.ReportError));
            foreach (var step in report.Steps)
            {
                var text = Note($"{step.Iteration}.{step.StepIndex + 1} {step.Name} — {step.Status}\n{step.Detail}");
                text.AddToClassList("scenario-result-row");
                runResults.Add(text);
            }
            var logs = new Foldout { text = "Captured logs (" + report.Logs.Count + ")", value = true };
            foreach (var log in report.Logs)
            {
                var text = Note(log.Type + ": " + log.Message + (string.IsNullOrEmpty(log.StackTrace) ? "" : "\n" + log.StackTrace));
                text.AddToClassList("scenario-result-row");
                logs.Add(text);
            }
            if (report.DroppedLogCount > 0)
                logs.Add(Note("Older captured logs dropped: " + report.DroppedLogCount));
            runResults.Add(logs);
        }

        private void SetMessage(string text, bool error)
        {
            if (message == null)
                return;
            message.text = text;
            message.EnableInClassList("scenario-error", error);
        }

        internal void ConfigureForTests(PlayScenarioStore store) => injectedStore = store;
    }
}
