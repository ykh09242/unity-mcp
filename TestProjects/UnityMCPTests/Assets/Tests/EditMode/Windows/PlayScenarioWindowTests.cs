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
            StringAssert.Contains("first step", Field<Label>("scenarioValidation").text.ToLowerInvariant());
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
    }
}
