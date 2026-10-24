using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Windows.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Windows
{
    [Parallelizable(ParallelScope.None)]
    public class PlayScenarioReportProjectionTests
    {
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic;

        [Test]
        public void FailedRestoreDiscardsItsRunnerAndCachedEvidenceTogether()
        {
            Assert.AreEqual(0, PlayScenarioSuiteService.ActiveSubscriptionCount, "This isolated restore test must not detach a live suite.");
            Type service = typeof(PlayScenarioSuiteService);
            string[] names = { "_runner", "_finalized", "_editorProjection" };
            FieldInfo[] fields = names.Select(name => service.GetField(name, Static)).ToArray();
            object[] previous = fields.Select(field => field.GetValue(null)).ToArray();
            const string key = "MCPForUnity.PlayScenarioSuite.V1";
            string saved = SessionState.GetString(key, "");
            try
            {
                var definition = Definition("restore-projection");
                var suite = new PlayScenarioSuiteDefinition
                {
                    Name = definition.Name,
                    Scenarios = new List<string> { definition.Name },
                };
                var state = PlayScenarioSuiteRunner.Create(suite, new[] { definition }, 1, 300, null, 1000);
                state.Report.Status = "succeeded";
                state.Report.Scenarios[0].Report = Report(1);
                fields[0].SetValue(null, new PlayScenarioSuiteRunner(state, (_, __, ___, ____, _____) => null, _ => null, _ => null, () => { }, () => false));
                fields[1].SetValue(null, true);
                fields[2].SetValue(null, Activator.CreateInstance(fields[2].FieldType, true));
                PlayScenarioSuiteService.EditorStatus(state.Report.SuiteId);
                FieldInfo owner = fields[2].FieldType.GetField("source", Instance);
                Assert.AreSame(state.Report, owner.GetValue(fields[2].GetValue(null)));
                SessionState.SetString(key, "{");
                LogAssert.Expect(LogType.Warning, new Regex("^MCP play scenario suite restore failed:"));
                service.GetMethod("Restore", Static).Invoke(null, null);
                Assert.IsNull(fields[0].GetValue(null));
                Assert.IsNull(owner.GetValue(fields[2].GetValue(null)), "Discarding a failed restored owner must release its evidence cache.");
            }
            finally
            {
                service.GetMethod("Detach", Static).Invoke(null, null);
                for (int index = 0; index < fields.Length; index++)
                    fields[index].SetValue(null, previous[index]);
                if (string.IsNullOrEmpty(saved))
                    SessionState.EraseString(key);
                else
                    SessionState.SetString(key, saved);
            }
        }

        [Test]
        public void NewSuiteAdmissionRetiresPreviouslyPolledEvidenceBeforeAnyWindowPoll()
        {
            Assert.IsFalse(PlayScenarioService.IsBusy, "This isolated lifecycle test must not replace an active child.");
            Assert.AreEqual(0, PlayScenarioSuiteService.ActiveSubscriptionCount, "This isolated test must not detach a live suite.");
            Assert.IsFalse(EditorApplication.isCompiling || EditorApplication.isUpdating || EditorApplication.isPlayingOrWillChangePlaymode);
            Type service = typeof(PlayScenarioSuiteService);
            string[] fieldNames = { "_runner", "_finalized", "_nextPoll", "_editorProjection" };
            FieldInfo[] fields = fieldNames.Select(name => service.GetField(name, Static)).ToArray();
            object[] previous = fields.Select(field => field.GetValue(null)).ToArray();
            const string sessionKey = "MCPForUnity.PlayScenarioSuite.V1";
            string[] keys = { sessionKey, sessionKey + ".definition.0", sessionKey + ".definition.1" };
            string[] saved = keys.Select(key => SessionState.GetString(key, "")).ToArray();
            object checkpoint = service.GetField("Checkpoint", Static).GetValue(null);
            FieldInfo[] checkpointFields = new[] { "frozen", "identity", "frozenBytes" }
                .Select(name => checkpoint.GetType().GetField(name, Instance))
                .ToArray();
            object[] checkpointValues = checkpointFields.Select(field => field.GetValue(checkpoint)).ToArray();
            string name = "projection-" + Guid.NewGuid().ToString("N");
            string project = Path.GetDirectoryName(Application.dataPath);
            var scenarioStore = new PlayScenarioStore(project);
            var suiteStore = new PlayScenarioSuiteStore(project);
            try
            {
                var definition = Definition(name);
                scenarioStore.Save(definition);
                var suite = new PlayScenarioSuiteDefinition
                {
                    Name = name,
                    Scenarios = new List<string> { name },
                };
                suiteStore.Save(suite);
                var state = PlayScenarioSuiteRunner.Create(suite, new[] { definition }, 1, 300, null, 1000);
                state.Report.Status = "succeeded";
                state.Report.Scenarios[0].Status = "succeeded";
                state.Report.Scenarios[0].Report = Report(19200);
                fields[0].SetValue(null, new PlayScenarioSuiteRunner(state, (_, __, ___, ____, _____) => null, _ => null, _ => null, () => { }, () => false));
                fields[1].SetValue(null, true);
                fields[3].SetValue(null, Activator.CreateInstance(fields[3].FieldType, true));
                PlayScenarioSuiteService.EditorStatus(state.Report.SuiteId);
                FieldInfo owner = fields[3].FieldType.GetField("source", Instance);
                Assert.AreSame(state.Report, owner.GetValue(fields[3].GetValue(null)), "The old evidence must actually be cached before admission.");

                object response = PlayScenarioSuiteService.Handle(new JObject { ["action"] = "suite_run", ["name"] = name });
                Assert.IsInstanceOf<SuccessResponse>(response, response is ErrorResponse error ? error.Error : "Unexpected admission response.");
                Assert.AreEqual(3, PlayScenarioSuiteService.ActiveSubscriptionCount, "Real suite admission must own its normal update subscriptions.");
                Assert.AreNotSame(state.Report, ((PlayScenarioSuiteRunner)fields[0].GetValue(null)).State.Report);
                Assert.IsNull(
                    owner.GetValue(fields[3].GetValue(null)),
                    "Admitting a new suite must retire the obsolete evidence projection before any future window poll."
                );
            }
            finally
            {
                // Start only queues work. This synchronous test never yields or invokes Tick,
                // so detach before the next Editor update can request Play Mode or a child.
                service.GetMethod("Detach", Static).Invoke(null, null);
                for (int index = 0; index < fields.Length; index++)
                    fields[index].SetValue(null, previous[index]);
                for (int index = 0; index < checkpointFields.Length; index++)
                    checkpointFields[index].SetValue(checkpoint, checkpointValues[index]);
                for (int index = 0; index < keys.Length; index++)
                    if (string.IsNullOrEmpty(saved[index]))
                        SessionState.EraseString(keys[index]);
                    else
                        SessionState.SetString(keys[index], saved[index]);
                suiteStore.Delete(name);
                scenarioStore.Delete(name);
            }
        }

        [Test]
        public void SuiteEditorStatusReusesRevisionAndEvidenceWhilePublicStatusRemainsComplete()
        {
            MethodInfo read = typeof(PlayScenarioSuiteService).GetMethod("EditorStatus", Static);
            Assert.IsNotNull(read, "The window needs an internal status path that does not serialize completed suite evidence on every poll.");
            FieldInfo runnerField = typeof(PlayScenarioSuiteService).GetField("_runner", Static);
            FieldInfo finalizedField = typeof(PlayScenarioSuiteService).GetField("_finalized", Static);
            object previousRunner = runnerField.GetValue(null);
            object previousFinalized = finalizedField.GetValue(null);
            FieldInfo projectionField = typeof(PlayScenarioSuiteService).GetField("_editorProjection", Static);
            object previousProjection = projectionField.GetValue(null);
            try
            {
                projectionField.SetValue(null, Activator.CreateInstance(projectionField.FieldType, true));
                var definitions = new[] { Definition("completed"), Definition("slow") };
                var state = PlayScenarioSuiteRunner.Create(
                    new PlayScenarioSuiteDefinition { Name = "projection", Scenarios = definitions.Select(item => item.Name).ToList() },
                    definitions,
                    1,
                    300,
                    "revision",
                    1000
                );
                state.Report.Scenarios[0].Status = "succeeded";
                state.Report.Scenarios[0].Report = Report(19200);
                state.Report.Scenarios[1].Status = "running";
                state.Cursor = 1;
                runnerField.SetValue(null, new PlayScenarioSuiteRunner(state, (_, __, ___, ____, _____) => null, _ => null, _ => null, () => { }, () => true));
                finalizedField.SetValue(null, false);
                object first = ((SuccessResponse)read.Invoke(null, new object[] { state.Report.SuiteId })).Data;
                object second = ((SuccessResponse)read.Invoke(null, new object[] { state.Report.SuiteId })).Data;
                Assert.AreSame(first, second, "An unchanged poll must reuse the revisioned snapshot, including its completed evidence.");
                JObject firstData = SnapshotData(first);
                JObject evidence = ChildReports(first)[0];
                Assert.AreEqual(19200, ((JArray)evidence["steps"]).Count);
                state.Report.Scenarios[1].Status = "succeeded";
                state.Report.Status = "succeeded";
                object revised = ((SuccessResponse)read.Invoke(null, new object[] { state.Report.SuiteId })).Data;
                Assert.AreNotSame(first, revised);
                JObject revisedData = SnapshotData(revised);
                Assert.AreEqual("running", (string)revisedData["status"], "The service must retain its finalization fence.");
                Assert.AreSame(evidence, ChildReports(revised)[0], "A revised lightweight summary must not copy historical evidence.");
                Assert.Greater(Revision(revised), Revision(first));
                Assert.AreEqual(
                    "running",
                    (string)firstData["scenarios"][1]["status"],
                    "Published snapshots must stay detached from later coordination changes."
                );
                finalizedField.SetValue(null, true);
                object terminal = ((SuccessResponse)read.Invoke(null, new object[] { state.Report.SuiteId })).Data;
                Assert.AreEqual("succeeded", (string)SnapshotData(terminal)["status"]);
                Assert.AreSame(terminal, ((SuccessResponse)read.Invoke(null, new object[] { state.Report.SuiteId })).Data);
                state.Report.Scenarios[0].Report = Report(3);
                object replaced = ((SuccessResponse)read.Invoke(null, new object[] { state.Report.SuiteId })).Data;
                Assert.AreNotSame(terminal, replaced, "Replacing completed evidence must invalidate its internal revision.");
                Assert.AreEqual(3, ((JArray)ChildReports(replaced)[0]["steps"]).Count);
                Assert.AreEqual(19200, ((JArray)evidence["steps"]).Count);
                var full = (SuccessResponse)PlayScenarioSuiteService.Handle(new JObject { ["action"] = "suite_status", ["suite_id"] = state.Report.SuiteId });
                Assert.IsTrue(JToken.DeepEquals(JObject.FromObject(state.Report, PlayScenarioSuiteStore.Serializer()), (JObject)full.Data));
            }
            finally
            {
                runnerField.SetValue(null, previousRunner);
                finalizedField.SetValue(null, previousFinalized);
                projectionField.SetValue(null, previousProjection);
            }
        }

        [UnityTest]
        public IEnumerator LateStepAndLogChangesRetainHistoricalRowsFoldoutAndAllSupportedSteps()
        {
            string previousJob = SessionState.GetString(PlayScenarioWindow.SessionJobKey, "");
            bool previousMode = SessionState.GetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
            PlayScenarioWindow window = null;
            try
            {
                SessionState.SetString(PlayScenarioWindow.SessionJobKey, "");
                SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, false);
                window = ScriptableObject.CreateInstance<PlayScenarioWindow>();
                window.position = new Rect(30, 50, 1200, 800);
                window.Show();
                yield return null;
                window.CreateGUI();
                typeof(PlayScenarioWindow).GetMethod("SelectScenarioPane", Instance).Invoke(window, new object[] { "run" });
                MethodInfo accept = typeof(PlayScenarioWindow).GetMethod("AcceptReport", Instance);
                JObject first = Report(19200);
                accept.Invoke(window, new object[] { new SuccessResponse("first", first) });
                typeof(PlayScenarioWindow).GetMethod("StopPolling", Instance).Invoke(window, null);
                yield return null;
                var results = (ScrollView)typeof(PlayScenarioWindow).GetField("runResults", Instance).GetValue(window);
                Assert.IsNotNull(results.panel, "The retained report must be exercised on a real attached Editor panel.");
                Assert.Greater(results.verticalScroller.highValue, 500, "The long history must have a real scrollable layout.");
                var rows = results.Query<Label>(className: "scenario-result-row").ToList();
                Assert.AreEqual(19201, rows.Count, "Every supported step and the captured log must remain available.");
                Label historical = rows[0];
                Label last = rows[19199];
                Foldout logs = results.Q<Foldout>();
                logs.value = false;
                results.scrollOffset = new Vector2(0, 500);
                yield return null;
                Vector2 offset = results.scrollOffset;
                JObject changed = (JObject)first.DeepClone();
                changed["steps"][19199]["detail"] = "waiting: 123";
                changed["logs"] = new JArray(new JObject { ["type"] = "Log", ["message"] = "latest log" });
                accept.Invoke(window, new object[] { new SuccessResponse("changed", changed) });
                typeof(PlayScenarioWindow).GetMethod("StopPolling", Instance).Invoke(window, null);
                yield return null;
                var updated = results.Query<Label>(className: "scenario-result-row").ToList();
                Assert.AreSame(historical, updated[0], "Small progress changes must not recreate historical rows.");
                Assert.AreSame(last, updated[19199], "A changing step must update its existing row.");
                StringAssert.Contains("waiting: 123", updated[19199].text);
                Assert.AreSame(logs, results.Q<Foldout>());
                Assert.IsFalse(logs.value, "Polling must preserve the user's log foldout state.");
                Assert.AreEqual(offset, results.scrollOffset);
                Assert.AreEqual(19201, updated.Count);
                StringAssert.Contains("latest log", updated.Last().text);
            }
            finally
            {
                if (window != null)
                {
                    typeof(EditorWindow).GetProperty("hasUnsavedChanges").SetValue(window, false);
                    UnityEngine.Object.DestroyImmediate(window);
                }
                SessionState.SetString(PlayScenarioWindow.SessionJobKey, previousJob);
                SessionState.SetBool(PlayScenarioWindow.SessionSuiteModeKey, previousMode);
            }
        }

        private static JObject SnapshotData(object snapshot) => (JObject)snapshot.GetType().GetField("Data", Instance).GetValue(snapshot);

        private static long Revision(object snapshot) => (long)snapshot.GetType().GetField("Revision", Instance).GetValue(snapshot);

        private static IReadOnlyList<JObject> ChildReports(object snapshot) =>
            (IReadOnlyList<JObject>)snapshot.GetType().GetField("ChildReports", Instance).GetValue(snapshot);

        internal static PlayScenarioDefinition Definition(string name) =>
            new PlayScenarioDefinition
            {
                Name = name,
                Steps = new List<PlayScenarioStep>
                {
                    new PlayScenarioStep
                    {
                        Name = "Menu",
                        Action = "load_scene",
                        Scene = "Assets/ProjectionFixture.unity",
                    },
                },
            };

        internal static JObject Report(int count) =>
            JObject.FromObject(
                new PlayScenarioRun
                {
                    JobId = new string('a', 32),
                    Scenario = Definition("projection"),
                    Status = "running",
                    Phase = "executing",
                    Steps = Enumerable
                        .Range(0, count)
                        .Select(index => new PlayScenarioStepResult
                        {
                            Stage =
                                index % 192 < 64 ? "setup"
                                : index % 192 < 128 ? "main"
                                : "cleanup",
                            Iteration = index / 192 + 1,
                            StepIndex = index % 64,
                            Name = "step-" + index,
                            Status = index == count - 1 ? "running" : "succeeded",
                            Detail = "complete",
                        })
                        .ToList(),
                    Logs = new List<PlayScenarioLog>
                    {
                        new PlayScenarioLog { Type = "Log", Message = "initial log" },
                    },
                }
            );
    }
}
