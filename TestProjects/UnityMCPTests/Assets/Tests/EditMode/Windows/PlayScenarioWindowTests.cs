using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    public class PlayScenarioWindowTests
    {
        private string root;
        private string previousJob;
        private bool previousSuiteMode;
        private PlayScenarioStore store;
        private PlayScenarioWindow window;
        private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;

        [UnitySetUp]
        public IEnumerator SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "unity-mcp-scenario-window-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            store = new PlayScenarioStore(root);
            previousJob = SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
            previousSuiteMode = SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            window = ScriptableObject.CreateInstance<PlayScenarioWindow>();
            window.ConfigureForTests(store);
            window.ShowDialog = (_, __, ___, ____, _____) => 1;
            window.Show();
            yield return null;
            window.CreateGUI();
            Assert.IsNotNull(window.rootVisualElement.panel, "The test window must attach to an Editor panel before dispatching field changes.");
            Assert.IsNotNull(Field<TextField>("scenarioName").panel);
        }

        [TearDown]
        public void TearDown()
        {
            if (window != null)
            {
                typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
                UnityEngine.Object.DestroyImmediate(window);
            }
            SessionState.SetString(PlayScenarioWindow.SessionJobKey, previousJob);
            SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, previousSuiteMode);
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        private T Field<T>(string name)
            where T : VisualElement => window.rootVisualElement.Q<T>(name);

        private void Click(string name)
        {
            Button button = Field<Button>(name);
            Assert.IsNotNull(button, name);
            Assert.IsTrue(button.enabledInHierarchy, name + " must be enabled");
            // Execute the registered UITK Clickable callback, without a mouse/panel dependency.
            typeof(Clickable).GetMethod("Invoke", PrivateInstance).Invoke(button.clickable, new object[] { null });
        }

        private static PlayScenarioDefinition Definition(string name = "saved-flow") =>
            new PlayScenarioDefinition
            {
                Name = name,
                Steps = new System.Collections.Generic.List<PlayScenarioStep>
                {
                    new PlayScenarioStep
                    {
                        Name = "Menu",
                        Action = "load_scene",
                        Scene = "Assets/ScenarioMenu.unity",
                    },
                    new PlayScenarioStep
                    {
                        Name = "Start",
                        Action = "click_ui",
                        Target = "Canvas/StartButton",
                    },
                    new PlayScenarioStep
                    {
                        Name = "Game",
                        Action = "wait_scene",
                        Scene = "Assets/ScenarioGame.unity",
                    },
                    new PlayScenarioStep
                    {
                        Name = "Player",
                        Action = "wait_object",
                        Target = "Player",
                    },
                },
            };

        private void ConfigureTemplate(string name = "created-flow")
        {
            Field<TextField>("scenarioName").value = name;
            Field<TextField>("stepScene0").value = "Assets/ScenarioMenu.unity";
            Field<TextField>("stepScene2").value = "Assets/ScenarioGame.unity";
        }

        private void OpenSaved(string name = "saved-flow")
        {
            store.Save(Definition(name));
            Click("refreshScenarios");
            Click("savedScenario_" + name);
        }

        private object Response(string id, string status = "running") =>
            new SuccessResponse(
                "Observed " + status,
                JObject.FromObject(
                    new PlayScenarioRun
                    {
                        JobId = id,
                        Scenario = Definition(),
                        Status = status,
                        Phase = status == "running" ? "executing" : "finished",
                        RepeatCount = 1,
                        Steps = new System.Collections.Generic.List<PlayScenarioStepResult>
                        {
                            new PlayScenarioStepResult
                            {
                                Iteration = 1,
                                StepIndex = 0,
                                Name = "Menu",
                                Action = "load_scene",
                                Status = status,
                                Detail = "menu loaded",
                            },
                        },
                        Logs = new System.Collections.Generic.List<PlayScenarioLog>
                        {
                            new PlayScenarioLog { Type = "Log", Message = "scenario-log" },
                        },
                        ReportPath = status == "running" ? null : "Library/MCPForUnity/PlayScenarioRuns/" + id + ".json",
                    }
                )
            );

        private void ForceStatusTick()
        {
            typeof(PlayScenarioWindow).GetField("nextStatusRefresh", PrivateInstance).SetValue(window, 0d);
            typeof(PlayScenarioWindow).GetMethod("PollStatus", PrivateInstance).Invoke(window, null);
        }

        [Test]
        public void TemplateRequiresScenePathsAndGuiRecreationDoesNotDuplicateControls()
        {
            Assert.AreEqual(4, window.Draft.Steps.Count);
            Assert.AreEqual("load_scene", window.Draft.Steps[0].Action);
            Assert.AreEqual("Canvas/StartButton", Field<TextField>("stepTarget1").value);
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            Assert.IsFalse(Field<Button>("runScenario").enabledSelf);
            Assert.AreEqual(0, store.List().Count);
            int children = window.rootVisualElement.childCount;
            window.CreateGUI();
            Assert.AreEqual(children, window.rootVisualElement.childCount);
            Assert.AreEqual(1, window.rootVisualElement.Query<Button>("saveScenario").ToList().Count);
            ConfigureTemplate();
            Assert.IsTrue(Field<Button>("saveScenario").enabledSelf);
            Assert.IsTrue(window.hasUnsavedChanges);
            Field<IntegerField>("pollInterval").value = 99;
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            Assert.AreEqual(0, store.List().Count);
        }

        [Test]
        public void ButtonsRefreshSaveEditAndDeleteOnlyTheInjectedStore()
        {
            store.Save(Definition());
            Assert.IsNull(Field<Button>("savedScenario_saved-flow"));
            Click("refreshScenarios");
            Click("savedScenario_saved-flow");
            Assert.IsFalse(Field<TextField>("scenarioName").enabledSelf);
            Field<TextField>("stepName1").value = "Edited Start";
            Field<IntegerField>("pollInterval").value = 600;
            Click("saveScenario");
            Assert.AreEqual("Edited Start", store.Get("saved-flow").Steps[1].Name);
            Assert.AreEqual(600, store.Get("saved-flow").PollIntervalMs);
            Assert.IsFalse(window.hasUnsavedChanges);
            Click("deleteScenario");
            Assert.AreEqual(1, store.List().Count, "cancelled delete");
            window.ShowDialog = (_, __, ___, ____, _____) => 0;
            Click("deleteScenario");
            Assert.AreEqual(0, store.List().Count);
            Assert.IsNull(Field<Button>("savedScenario_saved-flow"));
            Assert.IsTrue(Field<TextField>("scenarioName").enabledSelf);
            ConfigureTemplate();
            Click("saveScenario");
            CollectionAssert.AreEqual(new[] { "created-flow" }, store.List());
        }

        [Test]
        public void StepButtonsEditActionReorderDeleteAndValidateFirstLoad()
        {
            ConfigureTemplate();
            Click("addStep");
            Assert.AreEqual(5, window.Draft.Steps.Count);
            Field<TextField>("stepName4").value = "Final marker";
            Field<TextField>("stepTarget4").value = "World/Marker";
            Field<IntegerField>("stepTimeout4").value = 7;
            Click("moveStepUp4");
            Assert.AreEqual("Final marker", window.Draft.Steps[3].Name);
            Click("moveStepDown3");
            Assert.AreEqual("Final marker", window.Draft.Steps[4].Name);
            Assert.AreEqual(7, window.Draft.Steps[4].TimeoutSeconds);
            Field<PopupField<string>>("stepAction4").value = "wait_scene";
            Assert.IsNull(Field<TextField>("stepTarget4"));
            Assert.AreEqual("", Field<TextField>("stepScene4").value);
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            Click("deleteStep4");
            Assert.AreEqual(4, window.Draft.Steps.Count);
            Assert.IsTrue(Field<Button>("saveScenario").enabledSelf);
            Click("moveStepDown0");
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            StringAssert.Contains("must load_scene", Field<Label>("scenarioValidation").text.ToLowerInvariant());
        }

        [Test]
        public void NavigationProtectsUnsavedEditsForCancelDiscardAndSave()
        {
            OpenSaved();
            store.Save(Definition("second-flow"));
            Click("refreshScenarios");
            Field<TextField>("stepName1").value = "Unsaved";
            Click("savedScenario_second-flow");
            Assert.AreEqual("saved-flow", window.Draft.Name);
            Assert.AreEqual("Unsaved", window.Draft.Steps[1].Name);
            window.ShowDialog = (_, __, ___, ____, _____) => 2;
            Click("savedScenario_second-flow");
            Assert.AreEqual("second-flow", window.Draft.Name);
            Assert.AreEqual("Start", store.Get("saved-flow").Steps[1].Name);
            Field<TextField>("stepName1").value = "Saved by navigation";
            window.ShowDialog = (_, __, ___, ____, _____) => 0;
            Click("newScenario");
            Assert.AreEqual("Saved by navigation", store.Get("second-flow").Steps[1].Name);
            Assert.AreEqual("menu-start-player", window.Draft.Name);
            Assert.IsTrue(window.hasUnsavedChanges);
            Click("savedScenario_saved-flow");
            Assert.AreEqual("menu-start-player", window.Draft.Name, "invalid template cannot save before navigation");
        }

        [Test]
        public void SaveAsCopyKeepsOriginalAndRejectsExistingName()
        {
            OpenSaved();
            Field<TextField>("stepName1").value = "Copy Start";
            Field<TextField>("copyScenarioName").value = "copy-flow";
            Click("saveScenarioCopy");
            CollectionAssert.AreEqual(new[] { "copy-flow", "saved-flow" }, store.List());
            Assert.AreEqual("Start", store.Get("saved-flow").Steps[1].Name);
            Assert.AreEqual("Copy Start", store.Get("copy-flow").Steps[1].Name);
            Field<TextField>("copyScenarioName").value = "saved-flow";
            Click("saveScenarioCopy");
            StringAssert.Contains("already exists", Field<Label>("scenarioMessage").text);
            Assert.AreEqual("Start", store.Get("saved-flow").Steps[1].Name);
        }

        [Test]
        public void CurrentSelectionCopiesOnlyAnExactHierarchyPath()
        {
            var prior = Selection.activeObject;
            var parent = new GameObject("ScenarioWindowOwnedRoot");
            var child = new GameObject("Marker");
            child.transform.SetParent(parent.transform);
            try
            {
                Selection.activeGameObject = child;
                Click("useSelection3");
                Assert.AreEqual("ScenarioWindowOwnedRoot/Marker", window.Draft.Steps[3].Target);
                UnityEngine.Object.DestroyImmediate(parent);
                Assert.AreEqual("ScenarioWindowOwnedRoot/Marker", Field<TextField>("stepTarget3").value);
                Click("undoDraft");
                Assert.AreEqual("Player", window.Draft.Steps[3].Target);
                Click("redoDraft");
                Assert.AreEqual("ScenarioWindowOwnedRoot/Marker", window.Draft.Steps[3].Target);
            }
            finally
            {
                Selection.activeObject = prior;
                if (parent != null)
                    UnityEngine.Object.DestroyImmediate(parent);
            }
        }

        [Test]
        public void SaveRunUsesValidatedSavedDefinitionAndDisplaysResultsLogsAndCancel()
        {
            ConfigureTemplate();
            Field<IntegerField>("repeatCount").value = 2;
            Field<IntegerField>("runTimeout").value = 120;
            int starts = 0;
            window.StartRun = (name, repeats, timeout, id) =>
            {
                starts++;
                Assert.AreEqual("created-flow", store.Get(name).Name, "save must precede Start");
                Assert.AreEqual(2, repeats);
                Assert.AreEqual(120, timeout);
                Assert.AreEqual(32, id.Length);
                return Response(id);
            };
            window.CancelRun = id => Response(id, "cancelled");
            Click("runScenario");
            Assert.AreEqual(1, starts);
            Assert.IsTrue(window.IsPolling);
            Assert.IsFalse(Field<Button>("runScenario").enabledSelf);
            Assert.AreEqual(window.CurrentJobId, SessionState.GetString(PlayScenarioWindow.SessionJobKey, ""));
            Assert.IsTrue(window.rootVisualElement.Query<Label>().ToList().Any(label => label.text.Contains("1.1 Menu")));
            Assert.IsTrue(window.rootVisualElement.Query<Label>().ToList().Any(label => label.text.Contains("scenario-log")));
            Click("cancelScenario");
            Assert.IsFalse(window.IsPolling);
            Assert.IsFalse(Field<Button>("cancelScenario").enabledSelf);
            StringAssert.Contains("cancelled", Field<Label>("runStatus").text);
            Assert.IsTrue(window.rootVisualElement.Query<Label>().ToList().Any(label => label.text.Contains("Report: Library/")));
        }

        [Test]
        public void StatusPollIsThrottledStopsAtTerminalAndDoesNotRefreshSavedList()
        {
            ConfigureTemplate();
            window.StartRun = (_, __, ___, id) => Response(id);
            int reads = 0;
            window.ReadStatus = id =>
            {
                reads++;
                return Response(id, "succeeded");
            };
            Click("runScenario");
            store.Save(Definition("added-outside-window"));
            typeof(PlayScenarioWindow).GetField("nextStatusRefresh", PrivateInstance).SetValue(window, double.MaxValue);
            typeof(PlayScenarioWindow).GetMethod("PollStatus", PrivateInstance).Invoke(window, null);
            Assert.AreEqual(0, reads);
            ForceStatusTick();
            Assert.AreEqual(1, reads);
            Assert.IsFalse(window.IsPolling);
            Assert.IsNull(Field<Button>("savedScenario_added-outside-window"), "status updates must not list files");
            ForceStatusTick();
            Assert.AreEqual(1, reads, "terminal report remains cached while idle");
            Click("refreshScenarios");
            Assert.IsNotNull(Field<Button>("savedScenario_added-outside-window"));
        }

        [Test]
        public void FinalizingSnapshotKeepsPollingUntilScreenshotAndSavedHistoryAreAvailable()
        {
            OpenSaved();
            Field<Toggle>("screenshotOnFailure").value = true;
            Func<string, bool, object> snapshot = (id, finished) =>
            {
                var run = new PlayScenarioRun
                {
                    JobId = id,
                    Scenario = store.Get("saved-flow"),
                    Status = finished ? "failed" : "running",
                    Phase = finished ? "finished" : "finalizing",
                    RepeatCount = 1,
                    StartedUnixMs = 1000,
                    FinishedUnixMs = finished ? 2000 : (long?)null,
                    Error = "Synthetic failure",
                    FailureDiagnostics = new PlayScenarioFailureDiagnostics
                    {
                        CapturedUnixMs = 1900,
                        Observation = "Synthetic failure observation",
                        ScreenshotPath = finished ? "Library/MCPForUnity/PlayScenarioRuns/" + id + ".png" : null,
                    },
                };
                if (finished)
                    store.SaveReport(run);
                var data = JObject.FromObject(run, JsonSerializer.Create());
                data["pending_status"] = finished ? null : new JValue("failed");
                return new SuccessResponse(finished ? "Report finalized" : "Finalizing report", data);
            };
            window.StartRun = (_, __, ___, id) => snapshot(id, false);
            int reads = 0;
            window.ReadStatus = id => snapshot(id, ++reads > 1);
            Click("runScenario");
            Assert.IsTrue(window.IsPolling);
            StringAssert.Contains("finalizing", Field<Label>("runStatus").text);
            Assert.IsTrue(Field<ScrollView>("runResults").Query<Label>().ToList().Any(label => label.text.Contains("Screenshot: Pending capture.")));
            Assert.IsFalse(Field<Button>("runScenario").enabledSelf);
            Assert.IsNull(Field<PopupField<string>>("baselineReport"));
            ForceStatusTick();
            Assert.AreEqual(1, reads);
            Assert.IsTrue(window.IsPolling);
            Assert.AreEqual(0, store.ListReports("saved-flow").Count);
            Assert.IsNull(Field<PopupField<string>>("baselineReport"));
            ForceStatusTick();
            Assert.AreEqual(2, reads);
            Assert.IsFalse(window.IsPolling);
            StringAssert.Contains("failed", Field<Label>("runStatus").text);
            string screenshot = "Library/MCPForUnity/PlayScenarioRuns/" + window.CurrentJobId + ".png";
            Assert.IsTrue(Field<ScrollView>("runResults").Query<Label>().ToList().Any(label => label.text.Contains("Screenshot: " + screenshot)));
            Assert.AreEqual(screenshot, store.GetReport(window.CurrentJobId).FailureDiagnostics.ScreenshotPath);
            Assert.AreEqual(1, Field<PopupField<string>>("baselineReport").choices.Count);
            StringAssert.Contains(window.CurrentJobId, Field<PopupField<string>>("baselineReport").value);
            Assert.IsTrue(Field<Button>("runScenario").enabledSelf);
            ForceStatusTick();
            Assert.AreEqual(2, reads, "Only the finalized snapshot stops polling.");
        }

        [UnityTest]
        public IEnumerator DisableUnsubscribesAndReopenRestoresLatestJobWithoutStartingAgain()
        {
            ConfigureTemplate();
            window.StartRun = (_, __, ___, id) => Response(id);
            int reads = 0;
            window.ReadStatus = id =>
            {
                reads++;
                return Response(id);
            };
            Click("runScenario");
            string id = window.CurrentJobId;
            typeof(PlayScenarioWindow).GetMethod("OnDisable", PrivateInstance).Invoke(window, null);
            Assert.IsFalse(window.IsPolling);
            Assert.IsNull(window.StartRun);
            Assert.IsNull(window.ReadStatus);
            Assert.IsNull(window.CancelRun);
            Assert.IsNull(window.ShowDialog);
            ForceStatusTick();
            Assert.AreEqual(0, reads);
            typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
            UnityEngine.Object.DestroyImmediate(window);
            window = ScriptableObject.CreateInstance<PlayScenarioWindow>();
            window.ConfigureForTests(store);
            window.ReadStatus = restored =>
            {
                reads++;
                Assert.AreEqual(id, restored);
                return Response(restored, "succeeded");
            };
            window.Show();
            yield return null;
            Assert.IsNotNull(window.rootVisualElement.panel);
            Assert.AreEqual(id, window.CurrentJobId);
            Assert.AreEqual(1, reads, "read once on reopen");
            Assert.IsFalse(window.IsPolling);
            ForceStatusTick();
            Assert.AreEqual(1, reads);
        }

        [Test]
        public void SerializedDraftSurvivesEnableAndRunFailuresDoNotLosePriorJob()
        {
            ConfigureTemplate("draft-survives");
            const string exactValue = "2026-10-10T12:13:14.000Z";
            Field<TextField>("stepName1").value = exactValue;
            Field<TextField>("stepTarget3").value = exactValue;
            var previousDefaults = JsonConvert.DefaultSettings;
            try
            {
                foreach (bool globalDefaults in new[] { false, true })
                {
                    JsonConvert.DefaultSettings = globalDefaults
                        ? () => new JsonSerializerSettings { TypeNameHandling = TypeNameHandling.All, DateParseHandling = DateParseHandling.DateTime }
                        : previousDefaults;
                    typeof(PlayScenarioWindow).GetMethod("OnEnable", PrivateInstance).Invoke(window, null);
                    window.CreateGUI();
                    Assert.AreEqual("draft-survives", Field<TextField>("scenarioName").value);
                    Assert.AreEqual(exactValue, window.Draft.Steps[1].Name);
                    Assert.AreEqual(exactValue, window.Draft.Steps[3].Target);
                    Assert.AreEqual(exactValue, Field<TextField>("stepName1").value);
                    Assert.AreEqual(exactValue, Field<TextField>("stepTarget3").value);
                }
            }
            finally
            {
                JsonConvert.DefaultSettings = previousDefaults;
            }
            Assert.IsTrue(window.hasUnsavedChanges);
            window.StartRun = (_, __, ___, id) => new ErrorResponse("busy");
            Click("runScenario");
            Assert.IsFalse(window.IsPolling);
            Assert.AreEqual("", SessionState.GetString(PlayScenarioWindow.SessionJobKey, ""));
            StringAssert.Contains("busy", Field<Label>("scenarioMessage").text);
            Field<IntegerField>("repeatCount").value = 0;
            int starts = 0;
            window.StartRun = (_, __, ___, id) =>
            {
                starts++;
                return Response(id);
            };
            Click("runScenario");
            Assert.AreEqual(0, starts);
            StringAssert.Contains("Repeat count", Field<Label>("scenarioMessage").text);
        }

        [Test]
        public void DraftUndoRedoRestoresInvalidEditsAfterEnableAndSavedCheckpoint()
        {
            OpenSaved();
            Assert.IsFalse(Field<Button>("undoDraft").enabledSelf);
            Field<TextField>("stepName1").value = "Unsaved Start";
            Field<IntegerField>("pollInterval").value = 99;
            typeof(PlayScenarioWindow).GetMethod("OnEnable", PrivateInstance).Invoke(window, null);
            window.CreateGUI();
            Click("undoDraft");
            Assert.AreEqual(250, window.Draft.PollIntervalMs);
            Assert.AreEqual("Unsaved Start", window.Draft.Steps[1].Name);
            Assert.IsTrue(window.hasUnsavedChanges);
            Click("undoDraft");
            Assert.AreEqual("Start", window.Draft.Steps[1].Name);
            Assert.IsFalse(window.hasUnsavedChanges);
            Click("redoDraft");
            Click("saveScenario");
            Click("undoDraft");
            Assert.IsTrue(window.hasUnsavedChanges);
            Assert.AreEqual("Unsaved Start", store.Get("saved-flow").Steps[1].Name);
            Click("redoDraft");
            Assert.IsFalse(window.hasUnsavedChanges);
            Click("redoDraft");
            Assert.AreEqual(99, window.Draft.PollIntervalMs);
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
        }

        [Test]
        public void DraftHistoryIsBoundedAndNewEditsInvalidateRedoAndNavigationClearsIt()
        {
            OpenSaved();
            for (int index = 0; index < 60; index++)
                Field<TextField>("stepName1").value = "Edit " + index;
            for (int index = 0; index < 50; index++)
                Click("undoDraft");
            Assert.AreEqual("Edit 9", window.Draft.Steps[1].Name);
            Assert.IsFalse(Field<Button>("undoDraft").enabledSelf);
            Click("redoDraft");
            Field<TextField>("stepName1").value = "Branch edit";
            Assert.IsFalse(Field<Button>("redoDraft").enabledSelf);
            window.ShowDialog = (_, __, ___, ____, _____) => 2;
            Click("newScenario");
            Assert.IsFalse(Field<Button>("undoDraft").enabledSelf);
            Assert.IsFalse(Field<Button>("redoDraft").enabledSelf);
            Assert.IsTrue(window.hasUnsavedChanges);
        }

        [Test]
        public void DuplicateStepCreatesIndependentCopyAndSupportsUndoRedoAndLimit()
        {
            OpenSaved();
            Click("duplicateStep1");
            Assert.AreEqual(5, window.Draft.Steps.Count);
            Assert.AreEqual("Canvas/StartButton", window.Draft.Steps[2].Target);
            Assert.AreNotSame(window.Draft.Steps[1], window.Draft.Steps[2]);
            Field<TextField>("stepTarget2").value = "Canvas/OtherButton";
            Assert.AreEqual("Canvas/StartButton", window.Draft.Steps[1].Target);
            Click("undoDraft");
            Click("undoDraft");
            Assert.AreEqual(4, window.Draft.Steps.Count);
            Click("redoDraft");
            Assert.AreEqual(5, window.Draft.Steps.Count);
            for (int index = 5; index < 32; index++)
                Click("duplicateStep1");
            Assert.IsFalse(Field<Button>("duplicateStep1").enabledSelf);
        }

        [Test]
        public void StructuredConditionsPoliciesAndStagesSurviveUndoAndSave()
        {
            OpenSaved();
            Field<IntegerField>("completionStableMs").value = 500;
            Field<PopupField<string>>("logPolicyMode").value = "log_only";
            Click("addAllowedMessage");
            Field<TextField>("allowedMessage0").value = "Expected synthetic error";
            Field<Toggle>("metricsEnabled").value = true;
            Field<IntegerField>("metricsWarmup").value = 0;
            Field<Toggle>("screenshotOnFailure").value = true;
            Field<Toggle>("stepCount3Enabled").value = true;
            Field<IntegerField>("stepCount3").value = 1;
            Field<PopupField<string>>("stepActive3").value = "Inactive";
            Field<TextField>("stepComponent3").value = "UnityEngine.Transform";
            Field<Toggle>("stepPropertyEnabled3").value = true;
            Field<TextField>("stepPropertyPath3").value = "m_LocalPosition.x";
            Field<PopupField<string>>("stepPropertyType3").value = "Number";
            Field<TextField>("stepPropertyValue3").value = "3.5";
            Field<Toggle>("stepStable3Enabled").value = true;
            Field<IntegerField>("stepStable3").value = 300;
            Click("addSetupStep");
            Field<PopupField<string>>("stepActionsetup0").value = "load_scene";
            Field<TextField>("stepScenesetup0").value = "Assets/ScenarioMenu.unity";
            Click("addCleanupStep");
            Field<TextField>("stepTargetcleanup0").value = "Player";
            Click("saveScenario");
            var saved = store.Get("saved-flow");
            Assert.AreEqual(1, saved.SetupSteps.Count);
            Assert.AreEqual(1, saved.CleanupSteps.Count);
            Assert.AreEqual(500, saved.CompletionStableMs);
            Assert.AreEqual("log_only", saved.LogPolicy.Mode);
            CollectionAssert.AreEqual(new[] { "Expected synthetic error" }, saved.LogPolicy.AllowedMessages);
            Assert.IsTrue(saved.Metrics.Enabled);
            Assert.IsTrue(saved.Diagnostics.ScreenshotOnFailure);
            Assert.AreEqual(false, saved.Steps[3].Active);
            Assert.AreEqual(3.5d, saved.Steps[3].Property.Equals.Value<double>());
            Click("duplicateStepcleanup0");
            Click("undoDraft");
            Assert.AreEqual(1, window.Draft.CleanupSteps.Count);
            Assert.IsFalse(window.hasUnsavedChanges);
            Field<PopupField<string>>("stepAction3").value = "click_ui";
            Assert.IsNull(window.Draft.Steps[3].Count);
            Assert.IsNull(window.Draft.Steps[3].Component);
            Assert.IsNull(window.Draft.Steps[3].Property);
            Assert.IsNull(window.Draft.Steps[3].StableForMs);
            Click("undoDraft");
            Assert.AreEqual("wait_object", window.Draft.Steps[3].Action);
            Assert.AreEqual(3.5d, window.Draft.Steps[3].Property.Equals.Value<double>());
            Click("duplicateStepsetup0");
            Field<TextField>("stepNamesetup1").value = "Second setup";
            Click("moveStepUpsetup1");
            Assert.AreEqual("Second setup", window.Draft.SetupSteps[0].Name);
            Click("undoDraft");
            Assert.AreEqual("Second setup", window.Draft.SetupSteps[1].Name);
            Assert.AreEqual(1, window.Draft.CleanupSteps.Count);
            Click("redoDraft");
            Assert.AreEqual("Second setup", window.Draft.SetupSteps[0].Name);
            Assert.AreEqual("wait_object", window.Draft.Steps[3].Action);
        }

        [Test]
        public void OptionalCountAndActiveControlsPreserveZeroAndInactiveSemantics()
        {
            OpenSaved();
            Field<Toggle>("stepCount3Enabled").value = true;
            Assert.AreEqual(0, window.Draft.Steps[3].Count);
            Assert.IsNull(window.Draft.Steps[3].Active);
            Assert.IsTrue(Field<Button>("saveScenario").enabledSelf);
            Field<PopupField<string>>("stepActive3").value = "Inactive";
            Assert.AreEqual(false, window.Draft.Steps[3].Active);
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            Field<IntegerField>("stepCount3").value = 1;
            Assert.IsTrue(Field<Button>("saveScenario").enabledSelf);
            Field<Toggle>("stepCount3Enabled").value = false;
            Assert.IsNull(window.Draft.Steps[3].Count);
            Assert.AreEqual(false, window.Draft.Steps[3].Active);
            Assert.IsTrue(Field<Button>("saveScenario").enabledSelf);
            Field<PopupField<string>>("stepActive3").value = "Default (active)";
            Assert.IsNull(window.Draft.Steps[3].Active);
            Assert.IsFalse(window.hasUnsavedChanges);
        }

        [Test]
        public void PreflightIsExplicitReadOnlyAndClearsStaleChecksOnEdit()
        {
            OpenSaved();
            int checks = 0;
            window.CheckPreflight = definition =>
            {
                checks++;
                Assert.AreEqual("saved-flow", definition.Name);
                return new JObject
                {
                    ["success"] = true,
                    ["data"] = new JObject
                    {
                        ["valid"] = false,
                        ["checks"] = new JArray(
                            new JObject
                            {
                                ["stage"] = "main",
                                ["index"] = 1,
                                ["name"] = "Start",
                                ["status"] = "failed",
                                ["detail"] = "Target is ambiguous.",
                            }
                        ),
                    },
                };
            };
            Assert.AreEqual(0, checks);
            Click("preflightScenario");
            Assert.AreEqual(1, checks);
            StringAssert.Contains("Target is ambiguous", Field<Label>("preflightResult").text);
            Assert.IsFalse(window.hasUnsavedChanges);
            Assert.AreEqual("Start", store.Get("saved-flow").Steps[1].Name);
            Field<TextField>("stepName1").value = "Edited";
            Assert.AreEqual("", Field<Label>("preflightResult").text);
            Assert.AreEqual(1, checks);
        }

        [Test]
        public void ReportHistoryRefreshAndComparisonReadStoredTerminalDataWithoutStatusPolling()
        {
            OpenSaved();
            int reads = 0;
            window.ReadStatus = _ =>
            {
                reads++;
                return new ErrorResponse("Unexpected status read");
            };
            foreach (int index in new[] { 0, 1 })
            {
                store.SaveReport(
                    new PlayScenarioRun
                    {
                        JobId = new string(index == 0 ? 'a' : 'b', 32),
                        Scenario = Definition(),
                        RepeatCount = 1,
                        Status = index == 0 ? "failed" : "succeeded",
                        Phase = "finished",
                        StartedUnixMs = 1000 + index,
                        FinishedUnixMs = 2000 + index,
                        Steps = new System.Collections.Generic.List<PlayScenarioStepResult>
                        {
                            new PlayScenarioStepResult
                            {
                                Stage = "main",
                                Iteration = 1,
                                StepIndex = 0,
                                Name = "Menu",
                                Action = "load_scene",
                                Status = index == 0 ? "failed" : "passed",
                                StartedUnixMs = 1000,
                                FinishedUnixMs = 1100 + index * 100,
                                PollCount = index + 1,
                            },
                        },
                        MetricsSummary = "Synthetic samples",
                        MetricWarnings = new System.Collections.Generic.List<string> { "Synthetic warning " + index },
                    }
                );
            }
            Assert.IsNull(Field<PopupField<string>>("baselineReport"));
            Click("refreshReports");
            Assert.AreEqual(2, Field<PopupField<string>>("baselineReport").choices.Count);
            Click("compareReports");
            string text = string.Join("\n", Field<ScrollView>("reportComparison").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("failed", text);
            StringAssert.Contains("passed", text);
            StringAssert.Contains("100 ms", text);
            StringAssert.Contains("200 ms", text);
            StringAssert.Contains("polls", text);
            StringAssert.Contains("Synthetic warning", text);
            Assert.AreEqual(0, reads);
            Assert.IsFalse(window.IsPolling);
            Assert.IsFalse(window.hasUnsavedChanges);
            window.CreateGUI();
            Assert.IsNull(Field<PopupField<string>>("baselineReport"), "GUI reconstruction does not scan report history.");
        }

        [Test]
        public void SaveAndDiscardUseSharedValidationEvenWithGlobalJsonDefaults()
        {
            var previous = JsonConvert.DefaultSettings;
            try
            {
                JsonConvert.DefaultSettings = () => new JsonSerializerSettings { TypeNameHandling = TypeNameHandling.All };
                ConfigureTemplate();
                Click("saveScenario");
                Assert.AreEqual("created-flow", store.Get("created-flow").Name);
                Field<TextField>("stepName1").value = "Discard me";
                window.DiscardChanges();
                Assert.AreEqual("Click Start", window.Draft.Steps[1].Name);
                Assert.IsFalse(window.hasUnsavedChanges);
                Field<TextField>("stepScene0").value = "Assets/Resources/GameData/private.unity";
                Assert.Throws<ArgumentException>(() => window.SaveChanges());
                Assert.IsTrue(window.hasUnsavedChanges);
                Assert.AreEqual("Assets/ScenarioMenu.unity", store.Get("created-flow").Steps[0].Scene);
            }
            finally
            {
                JsonConvert.DefaultSettings = previous;
            }
        }

        [Test]
        public void TagsTargetIdsClickModeAndResourceLimitsUseSerializedDraftHistory()
        {
            ConfigureTemplate();
            Click("addScenarioTag");
            Field<TextField>("scenarioTag0").value = "smoke";
            Field<Toggle>("resourcesEnabled").value = true;
            Field<IntegerField>("resourcesScriptableObjects").value = 2;
            Field<IntegerField>("resourcesSubscriptions").value = 3;
            Field<IntegerField>("resourcesHandles").value = 4;
            Field<PopupField<string>>("stepTargetSelector1").value = "Test ID";
            Assert.IsNull(Field<TextField>("stepTarget1"));
            Field<TextField>("stepTargetId1").value = "menu.start";
            Field<PopupField<string>>("stepClickMode1").value = "Raycast + pointer events";
            Assert.AreEqual("raycast", window.Draft.Steps[1].ClickMode);
            Click("undoDraft");
            Assert.AreEqual("Direct handler", Field<PopupField<string>>("stepClickMode1").value);
            Click("redoDraft");
            Assert.AreEqual("Raycast + pointer events", Field<PopupField<string>>("stepClickMode1").value);
            Click("saveScenario");
            PlayScenarioDefinition saved = store.Get("created-flow");
            CollectionAssert.AreEqual(new[] { "smoke" }, saved.Tags);
            Assert.IsNull(saved.Steps[1].Target);
            Assert.AreEqual("menu.start", saved.Steps[1].TargetId);
            Assert.AreEqual("raycast", saved.Steps[1].ClickMode);
            Assert.IsTrue(saved.Resources.Enabled);
            Assert.AreEqual(2, saved.Resources.MaxScriptableObjects);
            Assert.AreEqual(3, saved.Resources.MaxSubscriptions);
            Assert.AreEqual(4, saved.Resources.MaxHandles);
            Field<TextField>("stepTargetId1").value = "menu.alternate";
            typeof(PlayScenarioWindow).GetMethod("OnEnable", PrivateInstance).Invoke(window, null);
            window.CreateGUI();
            Assert.AreEqual("menu.alternate", Field<TextField>("stepTargetId1").value);
            Assert.IsTrue(window.hasUnsavedChanges);
            Field<PopupField<string>>("stepAction1").value = "wait_scene";
            Assert.IsNull(window.Draft.Steps[1].TargetId);
            Assert.IsNull(window.Draft.Steps[1].ClickMode);
            Assert.IsNull(Field<PopupField<string>>("stepClickMode1"));
        }

        [Test]
        public void MarkerSelectionCopiesIdWithoutAddingOrDirtyingSceneComponents()
        {
            var prior = Selection.activeObject;
            var previewScene = UnityEditor.SceneManagement.EditorSceneManager.NewPreviewScene();
            var selected = new GameObject("ScenarioWindowIdSelection");
            UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(selected, previewScene);
            var marker = selected.AddComponent<MCPForUnity.Runtime.PlayScenarios.PlayScenarioTarget>();
            marker.TargetId = "player.main";
            EditorUtility.ClearDirty(marker);
            EditorUtility.ClearDirty(selected);
            try
            {
                Selection.activeGameObject = selected;
                Field<PopupField<string>>("stepTargetSelector3").value = "Test ID";
                int components = selected.GetComponents<Component>().Length;
                bool sceneDirty = previewScene.isDirty;
                Click("useSelection3");
                Assert.AreEqual("player.main", window.Draft.Steps[3].TargetId);
                Assert.AreEqual(components, selected.GetComponents<Component>().Length);
                Assert.IsFalse(EditorUtility.IsDirty(marker));
                Assert.IsFalse(EditorUtility.IsDirty(selected));
                Assert.AreEqual(sceneDirty, previewScene.isDirty);
                marker.TargetId = "";
                Click("useSelection3");
                Assert.AreEqual("player.main", window.Draft.Steps[3].TargetId, "Missing marker ID must preserve the prior draft value.");
                StringAssert.Contains("Author the marker explicitly", Field<Label>("scenarioMessage").text);
                UnityEngine.Object.DestroyImmediate(selected);
                Assert.AreEqual("player.main", Field<TextField>("stepTargetId3").value);
            }
            finally
            {
                Selection.activeObject = prior;
                UnityEditor.SceneManagement.EditorSceneManager.ClosePreviewScene(previewScene);
            }
        }

        [Test]
        public void StructuredFailureResourcesAndReproductionAreVisibleInRunResults()
        {
            ConfigureTemplate();
            window.StartRun = (_, __, ___, id) =>
                new SuccessResponse(
                    "Synthetic failure",
                    JObject.FromObject(
                        new PlayScenarioRun
                        {
                            JobId = id,
                            Scenario = Definition(),
                            Status = "failed",
                            Phase = "finished",
                            Failure = new PlayScenarioFailure
                            {
                                Code = "target_not_found",
                                Stage = "main",
                                Iteration = 1,
                                StepIndex = 1,
                                Target = "player.main",
                                Expected = "one target",
                                Actual = "none",
                                Message = "Target missing",
                            },
                            CleanupFailures = new System.Collections.Generic.List<PlayScenarioFailure>
                            {
                                new PlayScenarioFailure { Code = "cleanup_failed", Message = "Cleanup unavailable" },
                            },
                            ResourceChecks = new System.Collections.Generic.List<PlayScenarioResourceCheck>
                            {
                                new PlayScenarioResourceCheck
                                {
                                    Iteration = 1,
                                    NewScriptableObjects = 2,
                                    NewSubscriptions = 0,
                                    NewHandles = 1,
                                    Passed = false,
                                },
                            },
                            Reproduction = new PlayScenarioReproduction
                            {
                                DefinitionHash = new string('a', 64),
                                UnityVersion = "2021.3",
                                PackageVersion = "test-package",
                                SourceRevision = "caller-label",
                            },
                        }
                    )
                );
            Click("runScenario");
            string rendered = string.Join("\n", Field<ScrollView>("runResults").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("Failure code: target_not_found", rendered);
            StringAssert.Contains("actual: none", rendered);
            StringAssert.Contains("Cleanup failure code: cleanup_failed", rendered);
            StringAssert.Contains("new runtime ScriptableObjects 2", rendered);
            StringAssert.Contains("Unity: 2021.3; package: test-package", rendered);
            StringAssert.Contains("Source revision (caller-provided): caller-label", rendered);
        }

        [Test]
        public void ResetActionShowsOnlyIdsAndTimeoutAndClearsOtherActionFields()
        {
            ConfigureTemplate();
            Field<PopupField<string>>("stepAction1").value = "reset_state";
            Assert.IsNull(Field<TextField>("stepTarget1"));
            Assert.IsNull(Field<TextField>("stepTargetId1"));
            Assert.IsNull(Field<PopupField<string>>("stepClickMode1"));
            Assert.IsNotNull(Field<IntegerField>("stepTimeout1"));
            Field<TextField>("stepResetIds1").value = "game.session\nplayer.state";
            Field<IntegerField>("stepTimeout1").value = 12;
            var validated = PlayScenarioDefinition.Parse(JObject.FromObject(window.Draft, JsonSerializer.Create()));
            CollectionAssert.AreEqual(new[] { "game.session", "player.state" }, validated.Steps[1].ResetIds);
            var wireStep = (JObject)JObject.FromObject(validated, JsonSerializer.Create())["steps"][1];
            CollectionAssert.AreEquivalent(new[] { "name", "action", "timeout_seconds", "reset_ids" }, wireStep.Properties().Select(property => property.Name));
            Field<TextField>("stepResetIds1").value = "game.session\ngame.session";
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            Field<TextField>("stepResetIds1").value = "game.session";
            Click("saveScenario");
            CollectionAssert.AreEqual(new[] { "game.session" }, store.Get("created-flow").Steps[1].ResetIds);
            Field<PopupField<string>>("stepAction1").value = "wait_object";
            Assert.IsNull(window.Draft.Steps[1].ResetIds);
            Assert.IsNull(Field<TextField>("stepResetIds1"));
            Assert.IsNotNull(Field<TextField>("stepTarget1"));
            Field<PopupField<string>>("stepAction0").value = "reset_state";
            Field<TextField>("stepResetIds0").value = "game.session";
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf, "The first executed step must still load_scene.");
        }

        [Test]
        public void QueryBudgetAndTimelineDefaultsRoundTripAndPreserveScreenshotSetting()
        {
            ConfigureTemplate();
            Assert.IsFalse(Field<Toggle>("queryBudgetEnabled").value);
            Assert.AreEqual(4096, Field<IntegerField>("queryMaxTargetSearches").value);
            Assert.AreEqual(1000000, Field<IntegerField>("queryMaxHierarchyVisits").value);
            Assert.IsFalse(Field<VisualElement>("queryBudgetFields").enabledSelf);
            Assert.IsFalse(Field<Toggle>("recordTimeline").value);
            Field<Toggle>("queryBudgetEnabled").value = true;
            Assert.IsTrue(Field<VisualElement>("queryBudgetFields").enabledSelf);
            Field<IntegerField>("queryMaxTargetSearches").value = 0;
            Field<IntegerField>("queryMaxHierarchyVisits").value = 0;
            Field<Toggle>("recordTimeline").value = true;
            Field<Toggle>("screenshotOnFailure").value = true;
            Assert.IsTrue(window.Draft.Diagnostics.RecordTimeline, "Changing screenshot must preserve timeline.");
            Field<Toggle>("recordTimeline").value = false;
            Assert.IsTrue(window.Draft.Diagnostics.ScreenshotOnFailure, "Changing timeline must preserve screenshot.");
            Field<Toggle>("recordTimeline").value = true;
            Click("saveScenario");
            var saved = store.Get("created-flow");
            Assert.IsTrue(saved.QueryBudget.Enabled);
            Assert.AreEqual(0, saved.QueryBudget.MaxTargetSearches);
            Assert.AreEqual(0, saved.QueryBudget.MaxHierarchyVisits);
            Assert.IsTrue(saved.Diagnostics.RecordTimeline);
            Assert.IsTrue(saved.Diagnostics.ScreenshotOnFailure);
            Field<IntegerField>("queryMaxHierarchyVisits").value = 10000001;
            Assert.IsFalse(Field<Button>("saveScenario").enabledSelf);
            Click("undoDraft");
            Assert.AreEqual(0, Field<IntegerField>("queryMaxHierarchyVisits").value);
            Assert.IsTrue(Field<Toggle>("recordTimeline").value);
        }

        [Test]
        public void PlayerBuildRequiresCleanSavedDraftExplicitFolderAndIdleEditor()
        {
            int builds = 0;
            int pickers = 0;
            bool busy = false;
            window.IsEditorBusy = () => busy;
            window.ChoosePlayerBuildFolder = () =>
            {
                pickers++;
                return "explicit-output";
            };
            window.BuildPlayerBundle = (name, folder) =>
            {
                builds++;
                Assert.AreEqual("created-flow", name);
                Assert.AreEqual("explicit-output", folder);
                Assert.IsFalse(Field<Button>("buildPlayerScenario").enabledSelf);
                return "explicit-output/scenario-player.bundle.json";
            };
            ConfigureTemplate();
            Assert.IsFalse(Field<Button>("buildPlayerScenario").enabledSelf);
            typeof(PlayScenarioWindow).GetMethod("BuildPlayer", PrivateInstance).Invoke(window, null);
            Assert.AreEqual(0, pickers);
            Click("saveScenario");
            Assert.IsTrue(Field<Button>("buildPlayerScenario").enabledSelf);
            busy = true;
            typeof(PlayScenarioWindow).GetMethod("UpdateValidation", PrivateInstance).Invoke(window, null);
            Assert.IsFalse(Field<Button>("buildPlayerScenario").enabledSelf);
            typeof(PlayScenarioWindow).GetMethod("BuildPlayer", PrivateInstance).Invoke(window, null);
            Assert.AreEqual(0, pickers);
            busy = false;
            typeof(PlayScenarioWindow).GetMethod("UpdateValidation", PrivateInstance).Invoke(window, null);
            Click("buildPlayerScenario");
            Assert.AreEqual(1, builds);
            Assert.AreEqual(1, pickers);
            StringAssert.Contains("scenario-player.bundle.json", Field<Label>("scenarioMessage").text);
            window.ChoosePlayerBuildFolder = () => "";
            Click("buildPlayerScenario");
            Assert.AreEqual(1, builds, "Cancelling the folder picker must not start a build.");
            window.ChoosePlayerBuildFolder = () => "explicit-output";
            window.BuildPlayerBundle = (_, __) => throw new InvalidOperationException("test build failure");
            Click("buildPlayerScenario");
            StringAssert.Contains("Player build failed: test build failure", Field<Label>("scenarioMessage").text);
            Assert.IsTrue(Field<Button>("buildPlayerScenario").enabledSelf);
            Field<TextField>("stepName1").value = "Unsaved edit";
            Assert.IsFalse(Field<Button>("buildPlayerScenario").enabledSelf);
        }

        [Test]
        public void IdenticalStatusReportPreservesResultControlsWhileChangedDataUpdatesThem()
        {
            ConfigureTemplate();
            window.StartRun = (_, __, ___, job) => Response(job);
            window.ReadStatus = job =>
            {
                var response = (SuccessResponse)Response(job);
                var data = (JObject)response.Data;
                data["next_poll_unix_ms"] = 9876;
                ((JObject)data["steps"][0])["poll_count"] = 17;
                return response;
            };
            Click("runScenario");
            var previous = Field<Label>("runStatus");
            ForceStatusTick();
            Assert.AreSame(previous, Field<Label>("runStatus"));
            window.ReadStatus = job => Response(job, "succeeded");
            ForceStatusTick();
            Assert.AreNotSame(previous, Field<Label>("runStatus"));
            StringAssert.Contains("succeeded", Field<Label>("runStatus").text);
        }

        [Test]
        public void SavedReportDetailsShowQueryCountsRetainedOwnerCallsiteAndTimelineWithoutPolling()
        {
            OpenSaved();
            int reads = 0;
            window.ReadStatus = _ =>
            {
                reads++;
                return new ErrorResponse("Unexpected status read");
            };
            store.SaveReport(
                new PlayScenarioRun
                {
                    JobId = new string('d', 32),
                    Scenario = Definition(),
                    RepeatCount = 1,
                    StartedUnixMs = 1000,
                    FinishedUnixMs = 2000,
                    Status = "failed",
                    Phase = "finished",
                    QueryCounts = new PlayScenarioQueryCounts { TargetSearches = 9, HierarchyVisits = 30 },
                    Timeline = new System.Collections.Generic.List<PlayScenarioTimelineEvent>
                    {
                        new PlayScenarioTimelineEvent
                        {
                            Sequence = 3,
                            TimestampUnixMs = 1234,
                            Stage = "main",
                            Iteration = 1,
                            StepIndex = 1,
                            Event = "observation_changed",
                            Detail = "player appeared",
                        },
                    },
                    DroppedTimelineCount = 2,
                    ResourceChecks = new System.Collections.Generic.List<PlayScenarioResourceCheck>
                    {
                        new PlayScenarioResourceCheck
                        {
                            Iteration = 1,
                            NewHandles = 5,
                            Passed = false,
                            RetainedResources = new System.Collections.Generic.List<PlayScenarioRetainedResource>
                            {
                                new PlayScenarioRetainedResource
                                {
                                    Id = 7,
                                    Kind = "handle",
                                    Owner = "game.session",
                                    TypeName = "Lease",
                                    ResourceName = "persistent lease",
                                    SourceFile = "Session.cs",
                                    SourceMember = "Create",
                                    SourceLine = 42,
                                },
                            },
                            OmittedResourceCount = 4,
                        },
                    },
                }
            );
            Click("refreshReports");
            Click("viewReportDetails");
            string rendered = string.Join("\n", Field<ScrollView>("reportComparison").Query<Label>().ToList().Select(label => label.text));
            StringAssert.Contains("target searches 9; hierarchy visits 30", rendered);
            StringAssert.Contains("owner: game.session", rendered);
            StringAssert.Contains("Session.cs:42 Create", rendered);
            StringAssert.Contains("player appeared", rendered);
            StringAssert.Contains("Earlier timeline events omitted: 2", rendered);
            StringAssert.Contains("Additional retained resources omitted: 4", rendered);
            Assert.AreEqual(0, reads);
            Assert.IsFalse(window.IsPolling);
        }

        [Test]
        public void SuiteAuthoringDelegatesNativeQueueAndOnlyPollsActiveSuite()
        {
            OpenSaved();
            int lists = 0;
            int reads = 0;
            int runs = 0;
            const string suiteId = "11111111111111111111111111111111";
            JObject savedSuite = null;
            window.HandleSuite = request =>
            {
                string action = (string)request["action"];
                if (action == "suite_save")
                {
                    savedSuite = (JObject)request["suite"].DeepClone();
                    return new SuccessResponse("Saved", savedSuite);
                }
                if (action == "suite_list")
                {
                    lists++;
                    return new SuccessResponse("Suites", new JObject { ["suites"] = new JArray("editor-suite") });
                }
                if (action == "suite_reports")
                    return new SuccessResponse("Reports", new JObject { ["reports"] = new JArray() });
                if (action == "suite_run")
                {
                    runs++;
                    Assert.AreEqual("editor-suite", (string)request["name"]);
                    Assert.IsNull(request["suite_id"], "Native service assigns suite identity.");
                    Assert.AreEqual("revision-label", (string)request["source_revision"]);
                    Assert.AreEqual(2, (int)request["repeat_count"]);
                    Assert.AreEqual(90, (int)request["timeout_seconds"]);
                }
                else if (action == "suite_status")
                {
                    reads++;
                    Assert.AreEqual(suiteId, (string)request["suite_id"]);
                }
                else if (action != "suite_cancel")
                    Assert.Fail("Unexpected suite request: " + action);
                return new SuccessResponse(
                    "Suite observed",
                    new JObject
                    {
                        ["suite_id"] = suiteId,
                        ["suite"] = savedSuite,
                        ["status"] = action == "suite_status" ? "succeeded" : "running",
                        ["scenarios"] = new JArray(
                            new JObject
                            {
                                ["name"] = "saved-flow",
                                ["status"] = action == "suite_status" ? "succeeded" : "running",
                                ["job_id"] = new string('2', 32),
                            }
                        ),
                    }
                );
            };
            Field<TextField>("suiteName").value = "editor-suite";
            Field<TextField>("suiteScenarios").value = "saved-flow";
            Field<TextField>("suiteTags").value = "smoke\nregression";
            Field<PopupField<string>>("suiteFailurePolicy").value = "continue";
            Field<TextField>("suiteSourceRevision").value = "revision-label";
            Field<IntegerField>("suiteRepeatCount").value = 2;
            Field<IntegerField>("suiteTimeout").value = 90;
            Assert.IsTrue(window.hasUnsavedChanges);
            Click("refreshSuites");
            Assert.AreEqual(1, lists);
            Click("runSuite");
            Assert.AreEqual(1, runs);
            Assert.AreEqual("continue", (string)savedSuite["failure_policy"]);
            CollectionAssert.AreEqual(new[] { "smoke", "regression" }, ((JArray)savedSuite["tags"]).Values<string>());
            Assert.IsTrue(window.IsPolling);
            Assert.IsFalse(Field<Button>("runSuite").enabledSelf);
            Assert.IsFalse(Field<Button>("runScenario").enabledSelf);
            Assert.IsTrue(SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false));
            Click("cancelScenario");
            Assert.IsTrue(window.IsPolling, "Cancellation observes native child cleanup before terminal status.");
            ForceStatusTick();
            Assert.AreEqual(1, reads);
            Assert.IsFalse(window.IsPolling);
            Assert.IsTrue(Field<Button>("runSuite").enabledSelf);
            ForceStatusTick();
            Assert.AreEqual(1, reads);
            Assert.AreEqual(1, lists, "Status polling must not continuously refresh saved suites.");
        }

        [Test]
        public void SuiteDraftRestoreAndNavigationProtectUnsavedDefinition()
        {
            Field<TextField>("suiteName").value = "draft-suite";
            Field<TextField>("suiteScenarios").value = "first\nsecond";
            Field<TextField>("suiteTags").value = "smoke";
            Field<TextField>("suiteSourceRevision").value = "persisted-label";
            Field<IntegerField>("suiteRepeatCount").value = 3;
            Field<IntegerField>("suiteTimeout").value = 70;
            typeof(PlayScenarioWindow).GetMethod("OnEnable", PrivateInstance).Invoke(window, null);
            window.CreateGUI();
            Assert.AreEqual("draft-suite", Field<TextField>("suiteName").value);
            Assert.AreEqual("first\nsecond", Field<TextField>("suiteScenarios").value);
            Assert.AreEqual("persisted-label", Field<TextField>("suiteSourceRevision").value);
            Assert.AreEqual(3, Field<IntegerField>("suiteRepeatCount").value);
            Assert.AreEqual(70, Field<IntegerField>("suiteTimeout").value);
            Assert.IsTrue(window.hasUnsavedChanges);
            int gets = 0;
            window.HandleSuite = request =>
            {
                if ((string)request["action"] == "suite_list")
                    return new SuccessResponse("Listed", new JObject { ["suites"] = new JArray("other-suite") });
                gets++;
                return new SuccessResponse(
                    "Loaded",
                    new JObject
                    {
                        ["schema_version"] = 1,
                        ["name"] = "other-suite",
                        ["scenarios"] = new JArray("first"),
                        ["tags"] = new JArray(),
                        ["failure_policy"] = "stop",
                    }
                );
            };
            Click("refreshSuites");
            Click("savedSuite_other-suite");
            Assert.AreEqual(0, gets);
            Assert.AreEqual("draft-suite", Field<TextField>("suiteName").value);
            window.ShowDialog = (_, __, ___, ____, _____) => 2;
            Click("savedSuite_other-suite");
            Assert.AreEqual(1, gets);
            Assert.AreEqual("other-suite", Field<TextField>("suiteName").value);
        }
    }
}
