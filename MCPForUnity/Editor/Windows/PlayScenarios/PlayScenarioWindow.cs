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
        private string cleanDraftJson;

        [SerializeField]
        private List<string> undoDrafts = new List<string>();

        [SerializeField]
        private List<string> redoDrafts = new List<string>();

        private const int DraftHistoryLimit = 50;
        internal const string SessionSuiteModeKey = "MCPForUnity.PlayScenarios.WindowSuiteMode";

        [SerializeField]
        private string suiteDraftJson;

        [SerializeField]
        private string suiteCleanJson;

        [SerializeField]
        private bool suiteDirty;

        [SerializeField]
        private bool suiteMode;

        [SerializeField]
        private string sourceRevision = "";

        [SerializeField]
        private int suiteRepeatCount = 1;

        [SerializeField]
        private int suiteTimeoutSeconds = 300;

        [SerializeField]
        private string suiteSourceRevision = "";
        private JObject suiteReport;
        private PlayScenarioSuiteEditor suiteEditor;
        internal Func<JObject, object> HandleSuite;
        private bool Running => report?.Status == "running" || (string)suiteReport?["status"] == "running";

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
        private VisualElement optionsContainer;
        private ScrollView setupList;
        private ScrollView cleanupList;
        private PlayScenarioHistoryEditor history;
        private Label preflightResult;
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
        private Button buildPlayerButton;
        private bool buildingPlayer;
        private string renderedReportJson;
        internal Func<string> ChoosePlayerBuildFolder;
        internal Func<string, string, string> BuildPlayerBundle;
        internal Func<bool> IsEditorBusy;
        private bool EditorBusy =>
            IsEditorBusy != null
                ? IsEditorBusy()
                : EditorApplication.isPlayingOrWillChangePlaymode
                    || EditorApplication.isCompiling
                    || EditorApplication.isUpdating
                    || BuildPipeline.isBuildingPlayer;
        private Button undoButton;
        private Button redoButton;
        private bool polling;
        private double nextStatusRefresh;
        internal Func<string, int, int, string, object> StartRun;
        internal Func<string, object> ReadStatus;
        internal Func<string, object> CancelRun;
        internal Func<PlayScenarioDefinition, JObject> CheckPreflight;
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
            {
                jobId = SessionState.GetString(SessionJobKey, "");
                suiteMode = SessionState.GetBool(SessionSuiteModeKey, false);
            }
            hasUnsavedChanges = dirty || suiteDirty;
        }

        private void OnDisable()
        {
            StopPolling();
            StartRun = null;
            ReadStatus = null;
            CancelRun = null;
            CheckPreflight = null;
            HandleSuite = null;
            ShowDialog = null;
            ChoosePlayerBuildFolder = null;
            BuildPlayerBundle = null;
            IsEditorBusy = null;
        }

        public void CreateGUI()
        {
            StopPolling();
            RestoreDraft();
            rootVisualElement.Clear();
            renderedReportJson = null;
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
                Note("Save a repeatable menu → Start → game → Player flow. Configure scene assets and exact hierarchy paths or stable test IDs before running.")
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
            panel.Add(Note("Name: lowercase slug. Poll: 100–2000 ms. First executed setup/main step must load_scene. Each wait is bounded to 1–120 seconds."));
            var toolbar = Row();
            saveButton = ActionButton("Save", "saveScenario", () => TrySave());
            toolbar.Add(saveButton);
            deleteButton = ActionButton("Delete", "deleteScenario", DeleteScenario);
            toolbar.Add(deleteButton);
            toolbar.Add(ActionButton("Add Step", "addStep", AddStep));
            undoButton = ActionButton("Undo", "undoDraft", () => RestoreHistory(undoDrafts, redoDrafts));
            redoButton = ActionButton("Redo", "redoDraft", () => RestoreHistory(redoDrafts, undoDrafts));
            toolbar.Add(undoButton);
            toolbar.Add(redoButton);
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
            var preflight = ActionButton("Validate current scene", "preflightScenario", Preflight);
            preflight.tooltip = "Read-only scene asset and current target checks. Future scene/runtime checks are deferred.";
            panel.Add(preflight);
            preflightResult = new Label { name = "preflightResult" };
            preflightResult.AddToClassList("scenario-note");
            panel.Add(preflightResult);
            var authoring = new ScrollView { name = "scenarioAuthoring" };
            authoring.AddToClassList("scenario-scroll");
            optionsContainer = new VisualElement();
            authoring.Add(optionsContainer);
            suiteEditor = new PlayScenarioSuiteEditor(
                authoring,
                Store,
                CallSuite,
                SetMessage,
                RunSuite,
                Dialog,
                () => suiteDraftJson,
                () => suiteCleanJson,
                PersistSuiteDraft,
                () =>
                    new JObject
                    {
                        ["repeat_count"] = suiteRepeatCount,
                        ["timeout_seconds"] = suiteTimeoutSeconds,
                        ["source_revision"] = suiteSourceRevision,
                    },
                options =>
                {
                    suiteRepeatCount = (int)options["repeat_count"];
                    suiteTimeoutSeconds = (int)options["timeout_seconds"];
                    suiteSourceRevision = (string)options["source_revision"];
                }
            );
            setupList = BuildStage(authoring, "Setup", "setup");
            stepList = new ScrollView { name = "scenarioSteps" };
            authoring.Add(new Label("Main steps"));
            authoring.Add(stepList);
            cleanupList = BuildStage(authoring, "Cleanup", "cleanup");
            panel.Add(authoring);
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
            var revision = new TextField("Source revision label (optional)")
            {
                name = "sourceRevision",
                value = sourceRevision,
                maxLength = 128,
            };
            revision.RegisterValueChangedCallback(evt => sourceRevision = evt.newValue);
            panel.Add(revision);
            panel.Add(
                Note(
                    "Repeat: 1–10. Total timeout: 1–1800 s. Each repeat executes setup → main → cleanup. Use reset_state after the first load_scene in Setup to reset registered state before each repeat. Static state and DontDestroyOnLoad objects otherwise persist."
                )
            );
            var toolbar = Row();
            runButton = ActionButton("Save & Run", "runScenario", RunScenario);
            runButton.tooltip = "Saves the validated definition, then starts Play Mode. The backend run continues if this window is closed.";
            cancelButton = ActionButton("Cancel Run", "cancelScenario", CancelScenario);
            toolbar.Add(runButton);
            toolbar.Add(cancelButton);
            buildPlayerButton = ActionButton("Build Player", "buildPlayerScenario", BuildPlayer);
            buildPlayerButton.tooltip =
                "Builds the clean saved scenario as a Windows x64 Mono test Player in an explicitly chosen empty folder. The executable is launched separately.";
            toolbar.Add(buildPlayerButton);
            panel.Add(toolbar);
            panel.Add(
                Note(
                    "Closing this window does not cancel a run. Use Cancel Run to stop it. Cleanup runs after completion, failure, timeout or cancellation when possible. Finishing leaves Play Mode unchanged."
                )
            );
            runResults = new ScrollView { name = "runResults" };
            runResults.AddToClassList("scenario-scroll");
            panel.Add(runResults);
            history = new PlayScenarioHistoryEditor(panel, Store, () => draft.Name, SetMessage);
            columns.Add(panel);
        }

        private ScrollView BuildStage(VisualElement parent, string title, string stage)
        {
            var section = new Foldout { text = title + " steps", name = stage + "Steps" };
            var list = new ScrollView();
            section.Add(
                ActionButton(
                    "Add " + title + " Step",
                    "add" + title + "Step",
                    () =>
                    {
                        var steps = stage == "setup" ? draft.SetupSteps : draft.CleanupSteps;
                        if (steps.Count >= 16)
                        {
                            SetMessage("Each setup/cleanup stage supports at most 16 steps.", true);
                            return;
                        }
                        Edit(() =>
                            steps.Add(
                                new PlayScenarioStep
                                {
                                    Name = title + " step",
                                    Action = "wait_object",
                                    Target = "",
                                }
                            )
                        );
                        RenderSteps();
                    }
                )
            );
            section.Add(list);
            parent.Add(section);
            return list;
        }

        private void Preflight()
        {
            try
            {
                var definition = PlayScenarioDefinition.Parse(Json(draft));
                JObject response = CheckPreflight != null ? CheckPreflight(definition) : UnityPlayScenarioHost.Preflight(definition);
                var checks = response["data"]?["checks"] as JArray;
                if (response["success"]?.Value<bool>() != true || checks == null)
                    throw new InvalidOperationException("Unexpected preflight response.");
                preflightResult.text = string.Join(
                    "\n",
                    checks.Select(check => $"{check["stage"]}.{check["index"].Value<int>() + 1} {check["name"]}: {check["status"]} — {check["detail"]}")
                );
                preflightResult.EnableInClassList("scenario-error", response["data"]?["valid"]?.Value<bool>() != true);
            }
            catch (Exception exception)
            {
                preflightResult.text = exception.Message;
                preflightResult.AddToClassList("scenario-error");
            }
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
            draft.SetupSteps = draft.SetupSteps ?? new List<PlayScenarioStep>();
            draft.CleanupSteps = draft.CleanupSteps ?? new List<PlayScenarioStep>();
            PersistDraft();
            if (!dirty && string.IsNullOrEmpty(cleanDraftJson))
                cleanDraftJson = draftJson;
            undoDrafts = undoDrafts ?? new List<string>();
            redoDrafts = redoDrafts ?? new List<string>();
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
            string previous = draftJson;
            change();
            if (preflightResult != null)
                preflightResult.text = "";
            PersistDraft();
            if (previous == draftJson)
                return;
            PushHistory(undoDrafts, previous);
            redoDrafts.Clear();
            dirty = draftJson != cleanDraftJson;
            hasUnsavedChanges = dirty || suiteDirty;
            UpdateValidation();
        }

        private static void PushHistory(List<string> history, string snapshot)
        {
            history.Add(snapshot);
            if (history.Count > DraftHistoryLimit)
                history.RemoveAt(0);
        }

        private void RestoreHistory(List<string> source, List<string> destination)
        {
            if (source.Count == 0)
                return;
            PushHistory(destination, draftJson);
            draftJson = source[source.Count - 1];
            source.RemoveAt(source.Count - 1);
            draft = null;
            dirty = draftJson != cleanDraftJson;
            RestoreDraft();
            RenderDraft();
        }

        private void ResetHistory()
        {
            undoDrafts.Clear();
            redoDrafts.Clear();
            cleanDraftJson = dirty ? null : draftJson;
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
            hasUnsavedChanges = dirty || suiteDirty;
            PlayScenarioOptionsEditor.Render(optionsContainer, draft, Edit);
            preflightResult.text = "";
            RenderSteps();
            UpdateValidation();
        }

        private void RenderSteps()
        {
            PlayScenarioStepEditor.Render(stepList, draft, Edit, SetMessage);
            PlayScenarioStepEditor.Render(setupList, draft.SetupSteps, "setup", Edit, SetMessage, 16);
            PlayScenarioStepEditor.Render(cleanupList, draft.CleanupSteps, "cleanup", Edit, SetMessage, 16);
        }

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
            undoButton.SetEnabled(undoDrafts.Count > 0);
            redoButton.SetEnabled(redoDrafts.Count > 0);
            saveButton.SetEnabled(valid);
            runButton.SetEnabled(valid && !Running && !buildingPlayer);
            buildPlayerButton.SetEnabled(valid && !dirty && !string.IsNullOrEmpty(savedName) && !Running && !buildingPlayer && !EditorBusy);
            suiteEditor?.SetRunning(Running);
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
            ResetHistory();
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
                ResetHistory();
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
            bool firstSave = string.IsNullOrEmpty(savedName);
            draft = validated;
            savedName = validated.Name;
            dirty = false;
            PersistDraft();
            cleanDraftJson = draftJson;
            if (firstSave)
                ResetHistory();
            base.SaveChanges();
            RenderDraft();
            RefreshSavedList();
            SetMessage("Saved " + savedName + ".", false);
        }

        public override void SaveChanges()
        {
            if (dirty)
                SaveCurrent();
            if (suiteDirty && (suiteEditor == null || !suiteEditor.Save()))
                throw new InvalidOperationException("Suite changes could not be saved.");
            base.SaveChanges();
        }

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
            ResetHistory();
            suiteEditor?.Discard();
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
                ResetHistory();
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
                ResetHistory();
                hasUnsavedChanges = suiteDirty;
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
                        : PlayScenarioService.Start(
                            savedName,
                            repeatCount,
                            runTimeoutSeconds,
                            jobId,
                            string.IsNullOrEmpty(sourceRevision) ? null : sourceRevision
                        );
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

        private void BuildPlayer()
        {
            if (Running || buildingPlayer || EditorBusy)
            {
                SetMessage("Wait for the Editor and scenario runner to become idle before building a Player.", true);
                return;
            }
            if (dirty || string.IsNullOrEmpty(savedName) || !ValidateDraft(out _))
            {
                SetMessage("Save a valid scenario before building its Player.", true);
                return;
            }
            try
            {
                // Revalidate persisted data before opening the folder picker or invoking the builder.
                PlayScenarioDefinition.Parse(Json(Store.Get(savedName)));
                string folder =
                    ChoosePlayerBuildFolder != null ? ChoosePlayerBuildFolder() : EditorUtility.OpenFolderPanel("Choose an empty Player build folder", "", "");
                if (string.IsNullOrEmpty(folder))
                    return;
                buildingPlayer = true;
                UpdateValidation();
                SetMessage("Building Player for " + savedName + "...", false);
                EditorUtility.DisplayProgressBar("Play Scenario Player", "Building " + savedName + " for Windows x64 Mono", 0.1f);
                string bundle = BuildPlayerBundle != null ? BuildPlayerBundle(savedName, folder) : PlayScenarioPlayerBuild.Build(savedName, folder).BundlePath;
                SetMessage("Player built. Bundle: " + bundle, false);
            }
            catch (Exception exception)
            {
                SetMessage("Player build failed: " + exception.Message, true);
            }
            finally
            {
                buildingPlayer = false;
                EditorUtility.ClearProgressBar();
                UpdateValidation();
            }
        }

        private void CancelScenario()
        {
            if (string.IsNullOrEmpty(jobId))
                return;
            try
            {
                if (suiteMode)
                    AcceptSuiteReport(CallSuite(new JObject { ["action"] = "suite_cancel", ["suite_id"] = jobId }));
                else
                    AcceptReport(CancelRun != null ? CancelRun(jobId) : PlayScenarioService.Cancel(jobId));
            }
            catch (Exception exception)
            {
                SetMessage(exception.Message, true);
            }
        }

        private object CallSuite(JObject request) => HandleSuite != null ? HandleSuite(request) : PlayScenarioSuiteService.Handle(request);

        private void PersistSuiteDraft(string json, bool saved)
        {
            suiteDraftJson = json;
            if (saved)
                suiteCleanJson = json;
            suiteDirty = suiteDraftJson != suiteCleanJson;
            hasUnsavedChanges = dirty || suiteDirty;
        }

        private void RunSuite(JObject request)
        {
            if (Running)
            {
                SetMessage("A scenario or suite is already running.", true);
                return;
            }
            if (dirty)
            {
                int choice = Dialog(
                    "Unsaved scenario changes",
                    "Suites use saved definitions. Save current scenario changes before running?",
                    "Save",
                    "Cancel",
                    "Run saved definitions"
                );
                if (choice == 1 || (choice == 0 && !TrySave()))
                    return;
            }
            string previousId = jobId;
            bool previousMode = suiteMode;
            try
            {
                if (!AcceptSuiteReport(CallSuite(request)))
                {
                    jobId = previousId;
                    suiteMode = previousMode;
                    SessionState.SetString(SessionJobKey, jobId ?? "");
                    SessionState.SetBool(SessionSuiteModeKey, suiteMode);
                }
            }
            catch (Exception exception)
            {
                jobId = previousId;
                suiteMode = previousMode;
                SessionState.SetString(SessionJobKey, jobId ?? "");
                SessionState.SetBool(SessionSuiteModeKey, suiteMode);
                SetMessage(exception.Message, true);
            }
        }

        private bool AcceptSuiteReport(object response)
        {
            if (!(response is SuccessResponse success))
            {
                StopPolling();
                SetMessage(response is ErrorResponse error ? error.Error : "Unexpected suite response.", true);
                return false;
            }
            bool wasRunning = Running;
            string previousId = jobId;
            suiteReport = success.Data as JObject ?? Json(success.Data);
            jobId = (string)suiteReport["suite_id"];
            suiteMode = true;
            report = null;
            SessionState.SetString(SessionJobKey, jobId ?? "");
            SessionState.SetBool(SessionSuiteModeKey, true);
            RenderReport();
            if (Running)
                StartPolling();
            else
            {
                StopPolling();
                if (wasRunning || previousId != jobId)
                    suiteEditor?.RefreshReports();
            }
            SetMessage(success.Message, false);
            UpdateValidation();
            return true;
        }

        private bool AcceptReport(object response)
        {
            if (!(response is SuccessResponse success))
            {
                StopPolling();
                SetMessage(response is ErrorResponse error ? error.Error : "Unexpected scenario response.", true);
                return false;
            }
            bool wasRunning = report?.Status == "running";
            suiteMode = false;
            suiteReport = null;
            SessionState.SetBool(SessionSuiteModeKey, false);
            JObject data = success.Data as JObject ?? Json(success.Data);
            report = data.ToObject<PlayScenarioRun>(JsonSerializer.Create());
            if (report == null)
                throw new InvalidOperationException("Scenario response contained no report.");
            string previousReportId = jobId;
            bool completed = report.Status != "running";
            jobId = report.JobId;
            SessionState.SetString(SessionJobKey, jobId ?? "");
            RenderReport();
            if (completed && (wasRunning || previousReportId != jobId))
                history.Refresh();
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
                if (suiteMode)
                    AcceptSuiteReport(CallSuite(new JObject { ["action"] = "suite_status", ["suite_id"] = jobId }));
                else
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
            cancelButton.SetEnabled(Running);
            string currentReportJson =
                suiteMode && suiteReport != null ? suiteReport.ToString(Formatting.None)
                : report != null ? ReportDisplayJson(report)
                : "";
            if (currentReportJson == renderedReportJson)
                return;
            renderedReportJson = currentReportJson;
            runResults.Clear();
            if (suiteMode && suiteReport != null)
            {
                runResults.Add(new Label((string)suiteReport["status"] + " • suite") { name = "runStatus" });
                PlayScenarioSuiteEditor.AppendReport(suiteReport, text => runResults.Add(Note(text)));
                return;
            }
            if (report == null)
            {
                runResults.Add(Note("No run yet. Save & Run starts a job and displays its step results here."));
                return;
            }
            runResults.Add(new Label(report.Status + " • " + report.Phase) { name = "runStatus" });
            runResults.Add(Note("Job: " + report.JobId));
            PlayScenarioHistoryEditor.AppendStructuredDetails(Json(report), text => runResults.Add(Note(text)));
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
                var text = Note($"{step.Stage ?? "main"}.{step.Iteration}.{step.StepIndex + 1} {step.Name} — {step.Status}\n{step.Detail}");
                text.text += "\nQueries: " + PlayScenarioHistoryEditor.QuerySummary(Json(step.QueryCounts));
                text.AddToClassList("scenario-result-row");
                runResults.Add(text);
            }
            if (!string.IsNullOrEmpty(report.MetricsSummary))
                runResults.Add(Note(report.MetricsSummary));
            foreach (string warning in report.MetricWarnings)
                runResults.Add(Note("Diagnostic warning: " + warning));
            foreach (var sample in report.Metrics)
                runResults.Add(
                    Note(
                        $"Iteration {sample.Iteration} metrics: managed {sample.ManagedBytes?.ToString() ?? "unavailable"}, allocated {sample.AllocatedBytes?.ToString() ?? "unavailable"}, objects {sample.ObjectCount?.ToString() ?? "unavailable"}; subscriptions {sample.RunnerSubscriptionCount?.ToString() ?? "unavailable"}, handles {sample.RunnerHandleCount?.ToString() ?? "unavailable"}"
                            + (sample.IgnoredForTrend ? " (warmup)" : "")
                            + (string.IsNullOrEmpty(sample.Error) ? "" : " — " + sample.Error)
                    )
                );
            if (report.FailureDiagnostics != null)
                runResults.Add(
                    Note(
                        "Failure observation: "
                            + report.FailureDiagnostics.Observation
                            + "\n"
                            + report.FailureDiagnostics.TargetDetail
                            + "\nScreenshot: "
                            + (
                                report.FailureDiagnostics.ScreenshotPath
                                ?? report.FailureDiagnostics.ScreenshotError
                                ?? (
                                    report.Status == "running" && report.Scenario?.Diagnostics?.ScreenshotOnFailure == true
                                        ? "Pending capture."
                                        : "Not requested"
                                )
                            )
                    )
                );
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

        private static string ReportDisplayJson(PlayScenarioRun value)
        {
            JObject data = Json(value);
            var displayed = new JObject();
            foreach (
                string field in new[]
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
                }
            )
                displayed[field] = data[field];
            var steps = new JArray();
            foreach (JObject step in ((JArray)data["steps"]).OfType<JObject>())
            {
                var row = new JObject();
                foreach (string field in new[] { "stage", "iteration", "step_index", "name", "status", "detail", "query_counts" })
                    row[field] = step[field];
                steps.Add(row);
            }
            displayed["steps"] = steps;
            displayed["screenshot_on_failure"] = value.Scenario?.Diagnostics?.ScreenshotOnFailure ?? false;
            return displayed.ToString(Formatting.None);
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
