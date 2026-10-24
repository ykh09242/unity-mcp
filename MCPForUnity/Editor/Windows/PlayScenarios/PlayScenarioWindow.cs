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
        private PlayScenarioSuiteEditorSnapshot suiteSnapshot;
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
        private JObject reportSnapshot;
        private bool draftValid;
        private PlayScenarioStore injectedStore;
        private ScrollView scenarioList;
        private VisualElement scenarioColumns;
        private VisualElement libraryPane;
        private VisualElement editorPane;
        private VisualElement runPane;
        private VisualElement resultsPane;
        private bool? compactScenarioLayout;

        [SerializeField]
        private string selectedScenarioPane = "library";

        [SerializeField]
        private int workflowLayoutVersion;

        [SerializeField]
        private string activeRunName;
        private Label draftContext;
        private Label runPreparation;
        private Label activeRunLabel;
        private VisualElement activeRunStrip;
        private Foldout optionalStages;
        private Foldout advancedOptions;
        private bool optionsConfigured;
        private JObject defaultOptionSnapshot;
        private VisualElement stepList;
        private ScrollView runResults;
        private VisualElement optionsContainer;
        private VisualElement setupList;
        private VisualElement cleanupList;
        private PlayScenarioHistoryEditor history;
        private PlayScenarioPlayerSessionEditor playerSessionEditor;
        private PlayScenarioSoakEditor playerSoakEditor;
        private PlayScenarioSoakComparisonEditor playerSoakComparisonEditor;
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
        private PlayScenarioReportView reportView;
        private string reportViewKey;
        internal Func<string> ChoosePlayerBuildFolder;
        internal Func<string> ChoosePlayerSessionFile;
        internal Func<string> ChoosePlayerSoakFile;
        internal Func<string, string> ChoosePlayerSoakComparisonFile;
        internal Func<string, string, string, string> BuildPlayerBundle;
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
            minSize = new Vector2(500, 340);
            saveChangesMessage = "Save changes to this Play Scenario before closing?";
            draft = null;
            RestoreDraft();
            if (string.IsNullOrEmpty(jobId))
            {
                jobId = SessionState.GetString(SessionJobKey, "");
                suiteMode = SessionState.GetBool(SessionSuiteModeKey, false);
            }
            if (workflowLayoutVersion == 0)
            {
                if (selectedScenarioPane == "run")
                    selectedScenarioPane = "results";
                workflowLayoutVersion = 1;
            }
            hasUnsavedChanges = dirty || suiteDirty;
        }

        private void OnDisable()
        {
            rootVisualElement.UnregisterCallback<GeometryChangedEvent>(OnScenarioGeometryChanged);
            playerSessionEditor?.Dispose();
            playerSoakEditor?.Dispose();
            playerSoakComparisonEditor?.Dispose();
            playerSessionEditor = null;
            playerSoakEditor = null;
            playerSoakComparisonEditor = null;
            StopPolling();
            StartRun = null;
            ReadStatus = null;
            CancelRun = null;
            CheckPreflight = null;
            HandleSuite = null;
            ShowDialog = null;
            ChoosePlayerBuildFolder = null;
            ChoosePlayerSessionFile = null;
            ChoosePlayerSoakComparisonFile = null;
            BuildPlayerBundle = null;
            IsEditorBusy = null;
        }

        public void CreateGUI()
        {
            StopPolling();
            RestoreDraft();
            rootVisualElement.UnregisterCallback<GeometryChangedEvent>(OnScenarioGeometryChanged);
            rootVisualElement.Clear();
            compactScenarioLayout = null;
            reportView = null;
            reportViewKey = null;
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
            draftContext = new Label { name = "scenarioDraftContext" };
            draftContext.AddToClassList("scenario-draft-context");
            rootVisualElement.Add(draftContext);
            var tabs = Row();
            tabs.name = "scenarioWorkflow";
            tabs.AddToClassList("scenario-tabs");
            foreach (
                var entry in new[]
                {
                    (Id: "library", Title: "Scenarios"),
                    (Id: "edit", Title: "Edit steps"),
                    (Id: "run", Title: "Run"),
                    (Id: "results", Title: "Results"),
                }
            )
            {
                string id = entry.Id;
                var tab = ActionButton(entry.Title, "scenario-tab-" + id, () => SelectScenarioPane(id));
                tab.AddToClassList("scenario-tab");
                tabs.Add(tab);
            }
            rootVisualElement.Add(tabs);
            activeRunStrip = Row();
            activeRunStrip.name = "scenarioActiveRun";
            activeRunStrip.AddToClassList("scenario-active-run");
            activeRunLabel = new Label { name = "scenarioActiveRunLabel" };
            activeRunLabel.AddToClassList("scenario-active-run-label");
            activeRunStrip.Add(activeRunLabel);
            activeRunStrip.Add(ActionButton("View results", "showScenarioResults", ShowCurrentResults));
            cancelButton = ActionButton("Cancel run", "cancelScenario", CancelScenario);
            activeRunStrip.Add(cancelButton);
            rootVisualElement.Add(activeRunStrip);
            var columns = new VisualElement();
            columns.AddToClassList("scenario-columns");
            rootVisualElement.Add(columns);
            BuildSavedList(columns);
            BuildEditor(columns);
            BuildRun(columns);
            BuildResults(columns);
            scenarioColumns = columns;
            libraryPane = columns.Q(className: "scenario-library");
            editorPane = columns.Q(className: "scenario-editor");
            runPane = columns.Q(className: "scenario-run");
            resultsPane = columns.Q(className: "scenario-results");
            rootVisualElement.RegisterCallback<GeometryChangedEvent>(OnScenarioGeometryChanged);
            UpdateScenarioLayout(position.width, position.height);
            message = new Label { name = "scenarioMessage" };
            message.AddToClassList("scenario-message");
            message.AddToClassList("scenario-message-empty");
            rootVisualElement.Add(message);
            RefreshSavedList();
            RenderDraft();
            if (!string.IsNullOrEmpty(jobId))
                RefreshRunStatus();
            else
                RenderReport();
        }

        private void OnScenarioGeometryChanged(GeometryChangedEvent evt) => UpdateScenarioLayout(evt.newRect.width, evt.newRect.height);

        private void UpdateScenarioLayout(float width, float height)
        {
            if (scenarioColumns == null || float.IsNaN(width) || width <= 0)
                return;
            rootVisualElement.EnableInClassList("scenario-short", height > 0 && height < 300);
            compactScenarioLayout = width < 700;
            rootVisualElement.EnableInClassList("scenario-compact", compactScenarioLayout == true);
            SelectScenarioPane(selectedScenarioPane);
        }

        private void SelectScenarioPane(string id)
        {
            selectedScenarioPane = id == "library" || id == "edit" || id == "run" || id == "results" ? id : "library";
            libraryPane?.EnableInClassList("scenario-pane-active", selectedScenarioPane == "library");
            editorPane?.EnableInClassList("scenario-pane-active", selectedScenarioPane == "edit");
            runPane?.EnableInClassList("scenario-pane-active", selectedScenarioPane == "run");
            resultsPane?.EnableInClassList("scenario-pane-active", selectedScenarioPane == "results");
            foreach (string pane in new[] { "library", "edit", "run", "results" })
                rootVisualElement.Q<Button>("scenario-tab-" + pane)?.EnableInClassList("scenario-tab-selected", pane == selectedScenarioPane);
        }

        private void ShowCurrentResults()
        {
            var tools = rootVisualElement.Q<Foldout>("scenarioResultTools");
            if (tools != null && tools.value)
                tools.value = false;
            SelectScenarioPane("results");
        }

        private void BuildSavedList(VisualElement columns)
        {
            var panel = Panel("scenario-library");
            var toolbar = Row();
            var create = ActionButton("Create scenario", "newScenario", NewScenario);
            create.tooltip = "Create the example flow, then choose its scene assets and targets.";
            create.AddToClassList("scenario-primary-button");
            toolbar.Add(create);
            var refresh = ActionButton("Refresh list", "refreshScenarios", RefreshSavedList);
            refresh.tooltip = "Read definitions saved in this project's ProjectSettings/MCPForUnity/PlayScenarios folder.";
            toolbar.Add(refresh);
            panel.Add(toolbar);
            scenarioList = new ScrollView { name = "savedScenarios" };
            scenarioList.AddToClassList("scenario-scroll");
            panel.Add(scenarioList);
            columns.Add(panel);
        }

        private void BuildEditor(VisualElement columns)
        {
            var container = Panel("scenario-editor");
            container.AddToClassList("scenario-edit");
            var panel = new ScrollView { name = "scenarioEditorScroll" };
            panel.AddToClassList("scenario-scroll");
            container.Add(panel);
            nameField = new TextField("Scenario name") { name = "scenarioName" };
            nameField.RegisterValueChangedCallback(evt => Edit(() => draft.Name = evt.newValue));
            panel.Add(nameField);
            var toolbar = Row();
            saveButton = ActionButton("Save scenario", "saveScenario", () => TrySave());
            saveButton.AddToClassList("scenario-primary-button");
            toolbar.Add(saveButton);
            undoButton = ActionButton("Undo", "undoDraft", () => RestoreHistory(undoDrafts, redoDrafts));
            redoButton = ActionButton("Redo", "redoDraft", () => RestoreHistory(redoDrafts, undoDrafts));
            toolbar.Add(undoButton);
            toolbar.Add(redoButton);
            toolbar.Add(ActionButton("Next: Run", "reviewScenarioRun", () => SelectScenarioPane("run")));
            panel.Add(toolbar);
            validation = new Label { name = "scenarioValidation" };
            validation.AddToClassList("scenario-validation");
            panel.Add(validation);
            var authoring = new VisualElement { name = "scenarioAuthoring" };
            authoring.AddToClassList("scenario-authoring");
            var stepsHeading = Row();
            stepsHeading.Add(new Label("Steps"));
            var add = ActionButton("Add step", "addStep", AddStep);
            add.AddToClassList("scenario-primary-button");
            stepsHeading.Add(add);
            authoring.Add(stepsHeading);
            stepList = new VisualElement { name = "scenarioSteps" };
            authoring.Add(stepList);
            optionalStages = new Foldout { name = "scenarioOptionalStages", value = false };
            optionalStages.AddToClassList("scenario-optional-group");
            setupList = BuildStage(optionalStages, "Setup", "setup");
            cleanupList = BuildStage(optionalStages, "Cleanup", "cleanup");
            authoring.Add(optionalStages);
            advancedOptions = new Foldout { name = "scenarioAdvancedOptions", value = false };
            advancedOptions.AddToClassList("scenario-optional-group");
            pollField = new IntegerField("Check conditions every (ms)") { name = "pollInterval" };
            pollField.RegisterValueChangedCallback(evt => Edit(() => draft.PollIntervalMs = evt.newValue));
            advancedOptions.Add(pollField);
            optionsContainer = new VisualElement();
            advancedOptions.Add(optionsContainer);
            authoring.Add(advancedOptions);
            var checks = new Foldout
            {
                text = "Scene checks",
                name = "scenarioSceneChecks",
                value = false,
            };
            checks.AddToClassList("scenario-optional-group");
            var preflight = ActionButton("Check scenes and current targets", "preflightScenario", Preflight);
            preflight.tooltip = "Read-only scene asset and current target checks. Future runtime targets are checked when their step runs.";
            checks.Add(preflight);
            preflightResult = new Label { name = "preflightResult" };
            preflightResult.AddToClassList("scenario-note");
            checks.Add(preflightResult);
            authoring.Add(checks);
            var manage = new Foldout
            {
                text = "Copy & delete",
                name = "scenarioManage",
                value = false,
            };
            manage.AddToClassList("scenario-optional-group");
            var copyRow = Row();
            copyNameField = new TextField("New scenario name") { name = "copyScenarioName" };
            copyNameField.AddToClassList("scenario-copy-name");
            copyNameField.RegisterValueChangedCallback(_ => UpdateValidation());
            copyRow.Add(copyNameField);
            copyButton = ActionButton("Save a copy", "saveScenarioCopy", () => SaveCopy());
            copyRow.Add(copyButton);
            manage.Add(copyRow);
            deleteButton = ActionButton("Delete saved scenario", "deleteScenario", DeleteScenario);
            manage.Add(deleteButton);
            authoring.Add(manage);
            panel.Add(authoring);
            columns.Add(container);
        }

        private void BuildRun(VisualElement columns)
        {
            var container = Panel("scenario-run");
            var panel = new ScrollView { name = "scenarioRunScroll" };
            panel.AddToClassList("scenario-scroll");
            container.Add(panel);
            runPreparation = Note("");
            runPreparation.name = "scenarioRunPreparation";
            panel.Add(runPreparation);
            var toolbar = Row();
            runButton = ActionButton("Save & Run in Editor", "runScenario", RunScenario);
            runButton.AddToClassList("scenario-primary-button");
            runButton.tooltip = "Saves this scenario and starts Play Mode. The run continues if this window closes.";
            toolbar.Add(runButton);
            toolbar.Add(
                ActionButton(
                    "Review steps",
                    "reviewScenarioSteps",
                    () =>
                    {
                        SelectScenarioPane("edit");
                        rootVisualElement.Q<ScrollView>("scenarioEditorScroll")?.ScrollTo(validation);
                    }
                )
            );
            panel.Add(toolbar);
            var repeats = new IntegerField("Repeats") { name = "repeatCount", value = repeatCount };
            repeats.tooltip = "Run the flow 1–100 times, including its configured setup and cleanup steps.";
            repeats.RegisterValueChangedCallback(evt => repeatCount = evt.newValue);
            panel.Add(repeats);
            var options = new Foldout
            {
                text = "Run settings",
                name = "scenarioRunOptions",
                value = false,
            };
            var timeout = new IntegerField("Time budget (s)") { name = "runTimeout", value = runTimeoutSeconds };
            timeout.tooltip = "Total run budget: 1–86400 seconds.";
            timeout.RegisterValueChangedCallback(evt => runTimeoutSeconds = evt.newValue);
            options.Add(timeout);
            var revision = new TextField("Source revision label")
            {
                name = "sourceRevision",
                value = sourceRevision,
                maxLength = 128,
            };
            revision.tooltip = "Optional caller-provided label recorded with this run or frozen in a standalone Player build.";
            revision.RegisterValueChangedCallback(evt => sourceRevision = evt.newValue);
            options.Add(revision);
            var runHelp = new Foldout { text = "Help", value = false };
            runHelp.Add(Note("Each repeat runs setup → steps → cleanup. Closing this window does not cancel a run; use Cancel run in the status strip."));
            options.Add(runHelp);
            panel.Add(options);
            var suites = new VisualElement();
            suites.AddToClassList("scenario-optional-group");
            suiteEditor = new PlayScenarioSuiteEditor(
                suites,
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
            var suiteGroup = suites.Q<Foldout>("scenarioSuites");
            suiteGroup.value = false;
            UpdateSuiteSummary(suiteGroup);
            MoveHelpText(suiteGroup);
            panel.Add(suites);
            var player = new Foldout
            {
                text = "Standalone Player",
                name = "scenarioStandaloneBuild",
                value = false,
            };
            player.AddToClassList("scenario-optional-group");
            buildPlayerButton = ActionButton("Build standalone Player", "buildPlayerScenario", BuildPlayer);
            buildPlayerButton.tooltip = "Build a Windows x64 Mono test Player. This does not start the Editor run.";
            player.Add(buildPlayerButton);
            panel.Add(player);
            columns.Add(container);
        }

        private void BuildResults(VisualElement columns)
        {
            var container = Panel("scenario-results");
            runResults = new ScrollView { name = "runResults" };
            runResults.AddToClassList("scenario-scroll");
            runResults.AddToClassList("scenario-current-report");
            container.Add(runResults);
            var tools = new Foldout
            {
                name = "scenarioResultTools",
                text = "Saved & Player results",
                value = false,
            };
            tools.AddToClassList("scenario-results-tools");
            var panel = new ScrollView { name = "scenarioResultsScroll" };
            panel.AddToClassList("scenario-evidence-scroll");
            tools.Add(panel);
            tools.RegisterValueChangedCallback(evt =>
            {
                if (ReferenceEquals(evt.target, tools))
                    container.EnableInClassList("scenario-results-evidence-open", evt.newValue);
            });
            container.Add(tools);
            history = new PlayScenarioHistoryEditor(panel, Store, () => draft.Name, SetMessage);
            playerSessionEditor?.Dispose();
            playerSoakEditor?.Dispose();
            playerSoakComparisonEditor?.Dispose();
            playerSessionEditor = new PlayScenarioPlayerSessionEditor(
                panel,
                SetMessage,
                () => ChoosePlayerSessionFile != null ? ChoosePlayerSessionFile() : EditorUtility.OpenFilePanel("Open Player session.json", "", "json"),
                disposeOnDetach: false
            );
            playerSoakEditor = new PlayScenarioSoakEditor(
                panel,
                SetMessage,
                () => ChoosePlayerSoakFile != null ? ChoosePlayerSoakFile() : EditorUtility.OpenFilePanel("Open Player soak evidence", "", "json"),
                disposeOnDetach: false
            );
            playerSoakComparisonEditor = new PlayScenarioSoakComparisonEditor(
                panel,
                side =>
                    ChoosePlayerSoakComparisonFile != null
                        ? ChoosePlayerSoakComparisonFile(side)
                        : EditorUtility.OpenFilePanel("Open " + side.ToLowerInvariant() + " soak evidence", "", "json"),
                disposeOnDetach: false
            );
            panel.Q<Foldout>("reportHistory").text = "Saved runs";
            panel.Q<Foldout>("reportHistory").tooltip = "Previous reports for the currently selected scenario.";
            panel.Q<Foldout>("playerSession").text = "Import a standalone Player run";
            foreach (Foldout section in panel.Children().OfType<Foldout>())
            {
                section.value = false;
                MoveHelpText(section);
            }
            columns.Add(container);
        }

        private VisualElement BuildStage(VisualElement parent, string title, string stage)
        {
            var section = new Foldout
            {
                text = title,
                name = stage + "Steps",
                value = false,
            };
            var list = new VisualElement();
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

        private static VisualElement Panel(string className)
        {
            var panel = new VisualElement();
            panel.AddToClassList("scenario-panel");
            panel.AddToClassList(className);
            return panel;
        }

        private static void MoveHelpText(Foldout section)
        {
            var prose = section.Children().OfType<Label>().Where(label => string.IsNullOrEmpty(label.name) && label.text?.Length > 80).ToList();
            if (prose.Count == 0)
                return;
            var help = new Foldout { text = "Help", value = false };
            foreach (Label label in prose)
            {
                label.RemoveFromHierarchy();
                label.AddToClassList("scenario-note");
                help.Add(label);
            }
            section.Add(help);
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
                ? "Use lowercase letters, numbers, dashes or underscores; for example menu-start-player. Maximum 64 characters."
                : "This is the saved scenario identifier. Save a copy to give the flow a different name.";
            pollField.SetValueWithoutNotify(draft.PollIntervalMs);
            copyNameField.SetValueWithoutNotify("");
            deleteButton.SetEnabled(!string.IsNullOrEmpty(savedName));
            hasUnsavedChanges = dirty || suiteDirty;
            var openOptions = optionsContainer
                .Query<Foldout>()
                .ToList()
                .Where(section => !string.IsNullOrEmpty(section.name))
                .ToDictionary(section => section.name, section => section.value);
            PlayScenarioOptionsEditor.Render(optionsContainer, draft, Edit);
            foreach (Foldout section in optionsContainer.Query<Foldout>().ToList())
            {
                section.value = !string.IsNullOrEmpty(section.name) && openOptions.TryGetValue(section.name, out bool expanded) && expanded;
                MoveHelpText(section);
            }
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
                draftValid = true;
                validation.text = dirty ? "Ready to save" : "Ready";
                validation.RemoveFromClassList("scenario-error");
                return true;
            }
            catch (ArgumentException exception)
            {
                validated = null;
                draftValid = false;
                validation.text = FriendlyValidationMessage(exception.Message);
                validation.tooltip = exception.Message;
                validation.AddToClassList("scenario-error");
                return false;
            }
        }

        private void UpdateValidation() => UpdateValidationCore(true);

        private void UpdateValidationCore(bool revalidate)
        {
            if (validation == null)
                return;
            bool valid = revalidate ? ValidateDraft(out _) : draftValid;
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
            UpdateWorkflowContext(revalidate);
        }

        private string FriendlyValidationMessage(string fallback)
        {
            foreach (
                var stage in new[]
                {
                    (Name: "setup", Steps: draft.SetupSteps),
                    (Name: "main", Steps: draft.Steps),
                    (Name: "cleanup", Steps: draft.CleanupSteps),
                }
            )
                for (int index = 0; index < stage.Steps.Count; index++)
                {
                    PlayScenarioStep step = stage.Steps[index];
                    if ((step.Action == "load_scene" || step.Action == "wait_scene") && string.IsNullOrWhiteSpace(step.Scene))
                        return $"Choose a scene for {stage.Name} step {index + 1}: {step.Name}.";
                }
            return fallback;
        }

        private static JObject OptionSnapshot(PlayScenarioDefinition value) =>
            Json(
                new
                {
                    value.PollIntervalMs,
                    RecordedDraft = value.Draft == true,
                    value.CleanupTimeoutSeconds,
                    value.CompletionStableMs,
                    value.LogPolicy,
                    value.Metrics,
                    value.Diagnostics,
                    value.Resources,
                    value.QueryBudget,
                    Tagged = value.Tags?.Count > 0,
                    Parameters = value.Parameters != null,
                    Bindings = value.ParameterBindings != null,
                    Datasets = value.Datasets != null,
                }
            );

        private static void SetLabel(Label label, string text)
        {
            if (label != null && label.text != text)
                label.text = text;
        }

        private void UpdateWorkflowContext(bool inspectOptions)
        {
            if (draftContext == null || draft == null)
                return;
            string saveState =
                dirty ? "Unsaved changes"
                : string.IsNullOrEmpty(savedName) ? "New draft"
                : "Saved";
            SetLabel(
                draftContext,
                (string.IsNullOrEmpty(draft.Name) ? "Untitled scenario" : draft.Name) + " · " + saveState + (suiteDirty ? " · Suite changes unsaved" : "")
            );
            draftContext.tooltip = draftContext.text;
            SetLabel(
                runPreparation,
                Running ? "Run in progress. See Results."
                    : !draftValid ? validation.text
                    : ""
            );
            runPreparation?.EnableInClassList("scenario-message-empty", string.IsNullOrEmpty(runPreparation.text));
            runPreparation?.EnableInClassList("scenario-error", !draftValid && !Running);
            if (inspectOptions)
            {
                if (defaultOptionSnapshot == null)
                    defaultOptionSnapshot = OptionSnapshot(new PlayScenarioDefinition());
                JObject currentOptions = OptionSnapshot(draft);
                optionsConfigured = !JToken.DeepEquals(defaultOptionSnapshot, currentOptions);
                bool policiesConfigured = new[]
                {
                    "CleanupTimeoutSeconds",
                    "CompletionStableMs",
                    "LogPolicy",
                    "Metrics",
                    "Diagnostics",
                    "Resources",
                    "QueryBudget",
                }.Any(field => !JToken.DeepEquals(defaultOptionSnapshot[field], currentOptions[field]));
                var policies = optionsContainer.Q<Foldout>("scenarioOptions");
                if (policies != null)
                    policies.text = policiesConfigured ? "Policies & evidence · Configured" : "Policies & evidence";
            }
            optionalStages.text = $"Setup {draft.SetupSteps.Count} · Cleanup {draft.CleanupSteps.Count}";
            optionalStages.tooltip = "Setup runs before each repeat; cleanup runs afterward.";
            advancedOptions.text = optionsConfigured ? "Settings · Configured" : "Settings";
            bool hasRun = !string.IsNullOrEmpty(jobId);
            activeRunStrip.EnableInClassList("scenario-active-run-visible", hasRun);
            activeRunStrip.EnableInClassList("scenario-run-finished", !Running);
            string outcome = report?.Status ?? (string)suiteReport?["status"] ?? "unknown";
            string name =
                !string.IsNullOrEmpty(activeRunName) ? activeRunName
                : hasRun ? jobId.Substring(0, Math.Min(8, jobId.Length))
                : "";
            SetLabel(activeRunLabel, (suiteMode ? "Suite: " : "Run: ") + name + " · " + outcome);
            activeRunLabel.tooltip = name + " · " + outcome + " · Job " + jobId;
            cancelButton.SetEnabled(Running);
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
                {
                    scenarioList.Add(Note("Create a scenario: Load scene → Click UI → Wait for scene/object."));
                }
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
            SelectScenarioPane("edit");
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
                SelectScenarioPane("edit");
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
            draftValid = true;
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
            if (repeatCount < 1 || repeatCount > PlayScenarioDefinition.MaximumRepeats || runTimeoutSeconds < 1 || runTimeoutSeconds > 86400)
            {
                SetMessage("Repeat count must be 1–100 and run timeout 1–86400 seconds.", true);
                return;
            }
            if (!TrySave())
            {
                SelectScenarioPane("edit");
                return;
            }
            string priorId = jobId;
            string priorName = activeRunName;
            activeRunName = savedName;
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
                    activeRunName = priorName;
                    SessionState.SetString(SessionJobKey, jobId ?? "");
                }
                else
                    ShowCurrentResults();
            }
            catch (Exception exception)
            {
                jobId = priorId;
                activeRunName = priorName;
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
                string revision = string.IsNullOrEmpty(sourceRevision) ? null : sourceRevision;
                string bundle =
                    BuildPlayerBundle != null
                        ? BuildPlayerBundle(savedName, folder, revision)
                        : PlayScenarioPlayerBuild.Build(savedName, folder, revision).BundlePath;
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
            UpdateSuiteSummary(rootVisualElement.Q<Foldout>("scenarioSuites"));
            UpdateWorkflowContext(false);
        }

        private void UpdateSuiteSummary(Foldout section)
        {
            if (section == null)
                return;
            bool configured = false;
            if (!string.IsNullOrEmpty(suiteDraftJson))
            {
                JObject definition = JObject.Parse(suiteDraftJson);
                configured = (definition["scenarios"] as JArray)?.Count > 0 || (definition["tags"] as JArray)?.Count > 0;
            }
            section.text =
                suiteDirty ? "Suites · Unsaved changes"
                : configured ? "Suites · Configured"
                : "Suites";
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
            string previousName = activeRunName;
            activeRunName = (string)request["name"];
            try
            {
                if (!AcceptSuiteReport(CallSuite(request)))
                {
                    jobId = previousId;
                    suiteMode = previousMode;
                    activeRunName = previousName;
                    SessionState.SetString(SessionJobKey, jobId ?? "");
                    SessionState.SetBool(SessionSuiteModeKey, suiteMode);
                }
                else
                    ShowCurrentResults();
            }
            catch (Exception exception)
            {
                jobId = previousId;
                suiteMode = previousMode;
                activeRunName = previousName;
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
            var snapshot = success.Data as PlayScenarioSuiteEditorSnapshot;
            JObject data = snapshot?.Data ?? success.Data as JObject ?? Json(success.Data);
            string name = (string)data["suite"]?["name"];
            if (!string.IsNullOrEmpty(name))
                activeRunName = name;
            bool changed = snapshot != null ? !ReferenceEquals(snapshot, suiteSnapshot) : !JToken.DeepEquals(suiteReport, data);
            suiteReport = data;
            suiteSnapshot = snapshot;
            jobId = (string)suiteReport["suite_id"];
            suiteMode = true;
            report = null;
            reportSnapshot = null;
            SessionState.SetString(SessionJobKey, jobId ?? "");
            SessionState.SetBool(SessionSuiteModeKey, true);
            if (changed || reportView == null)
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
            UpdateValidationCore(false);
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
            suiteSnapshot = null;
            SessionState.SetBool(SessionSuiteModeKey, false);
            if (success.Data is JObject full && full["scenario"]?["name"]?.Type == JTokenType.String)
                activeRunName = (string)full["scenario"]["name"];
            JObject data = success.Data is PlayScenarioEditorSnapshot snapshot
                ? snapshot.Data
                : PlayScenarioEditorSnapshot.FromSnapshot(success.Data as JObject ?? Json(success.Data));
            bool changed = report == null || !JToken.DeepEquals(reportSnapshot, data);
            if (changed)
            {
                // The view reads the detached projection directly. Only the small run identity
                // and lifecycle fields need a typed value; historical rows retain scalar values.
                report = new PlayScenarioRun
                {
                    JobId = (string)data["job_id"],
                    Status = (string)data["status"],
                    Phase = (string)data["phase"],
                };
                reportSnapshot = data;
            }
            if (report == null)
                throw new InvalidOperationException("Scenario response contained no report.");
            string previousReportId = jobId;
            bool completed = report.Status != "running";
            jobId = report.JobId;
            SessionState.SetString(SessionJobKey, jobId ?? "");
            if (changed)
                RenderReport();
            if (completed && (wasRunning || previousReportId != jobId))
                history.Refresh();
            if (report.Status == "running")
                StartPolling();
            else
                StopPolling();
            SetMessage(success.Message, false);
            UpdateValidationCore(false);
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
                    AcceptSuiteReport(
                        HandleSuite == null
                            ? PlayScenarioSuiteService.EditorStatus(jobId)
                            : CallSuite(new JObject { ["action"] = "suite_status", ["suite_id"] = jobId })
                    );
                else
                    AcceptReport(ReadStatus != null ? ReadStatus(jobId) : PlayScenarioService.EditorStatus(jobId));
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
            if (report == null && suiteReport == null)
            {
                runResults.Clear();
                runResults.Add(Note("No run yet. Open Run to start this scenario; its step results will appear here."));
                return;
            }
            string key = (suiteMode ? "suite:" : "scenario:") + jobId;
            if (reportView == null || reportViewKey != key)
            {
                reportView = new PlayScenarioReportView(runResults, suiteMode);
                reportViewKey = key;
            }
            if (suiteMode)
                reportView.RenderSuite(suiteReport, suiteSnapshot?.ChildReports);
            else
                reportView.RenderScenario(reportSnapshot);
        }

        private void SetMessage(string text, bool error)
        {
            if (message == null)
                return;
            message.text = text;
            message.tooltip = text;
            message.EnableInClassList("scenario-error", error);
            message.EnableInClassList("scenario-message-empty", string.IsNullOrEmpty(text));
        }

        internal void ConfigureForTests(PlayScenarioStore store) => injectedStore = store;
    }
}
