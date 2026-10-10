using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.PlayScenarios.Integration
{
    public partial class PlayScenarioNativeFlowTests
    {
        private const string SuiteContextKey = "MCPForUnity.Tests.PlayScenarioIntegration.Suite";

        [UnityTest]
        public IEnumerator SuiteAdmissionFreezesSecondDefinitionAndSurvivesPlayEntryReload()
        {
            Save(Context.RepeatName, RepeatSteps());
            Save(Context.NextName, RepeatSteps());
            JObject admitted = StartSuite("continue", Context.RepeatName, Context.NextName);
            string suiteId = (string)admitted["suite_id"];
            Assert.That(admitted["scenarios"].All(child => (string)child["status"] == "pending"), Is.True);
            Assert.That((string)admitted["source_revision"], Is.EqualTo("synthetic-suite-revision"));
            Assert.That(
                ((IMcpResponse)ManagePlayScenario.HandleCommand(new JObject { ["action"] = "run", ["name"] = Context.NextName })).Success,
                Is.False,
                "A suite reserves the gap before its first child."
            );
            // Mutate the on-disk definition after admission. The queued child must retain its frozen menu flow.
            Save(Context.NextName, new JArray(SceneStep("changed", "load_scene", "Assets/UnbuiltAfterAdmission.unity")));
            JObject runningRetry = RetrySuite(suiteId, 60);
            Assert.That((string)runningRetry["suite_id"], Is.EqualTo(suiteId));
            Assert.That(
                runningRetry["scenarios"].Select(child => (string)child["job_id"]),
                Is.EqualTo(admitted["scenarios"].Select(child => (string)child["job_id"]))
            );
            yield return new EnterPlayMode();
            suiteId = SessionState.GetString(SuiteContextKey + ".Id", "");
            yield return FinishSuite(suiteId);
            JObject report = SuiteStatus(suiteId);
            Assert.That((string)report["status"], Is.EqualTo("succeeded"), report.ToString());
            Assert.That(report["scenarios"].Count(), Is.EqualTo(2));
            foreach (JToken child in report["scenarios"])
            {
                Assert.That((string)child["status"], Is.EqualTo("succeeded"));
                Assert.That((string)child["definition_hash"], Is.EqualTo((string)child["report"]["reproduction"]["definition_hash"]));
                Assert.That((string)child["report"]["scenario"]["steps"][0]["scene"], Is.EqualTo(Menu));
                Assert.That((string)child["report"]["reproduction"]["source_revision"], Is.EqualTo("synthetic-suite-revision"));
                Assert.That((bool)child["report"]["runner_resources_released"], Is.True);
                AssertReport((JObject)child["report"]);
            }
            AssertSuiteReport(report);
            // A completed retry must load the original request/report even after the saved suite is deleted.
            Command(new JObject { ["action"] = "suite_delete", ["name"] = Context.RepeatName });
            FieldInfo runnerField = typeof(PlayScenarioSuiteService).GetField("_runner", BindingFlags.Static | BindingFlags.NonPublic);
            object current = runnerField.GetValue(null);
            try
            {
                runnerField.SetValue(null, null);
                JObject persistedRetry = RetrySuite(suiteId, 60);
                Assert.That((string)persistedRetry["status"], Is.EqualTo("succeeded"));
                Assert.That((long)persistedRetry["started_unix_ms"], Is.EqualTo((long)report["started_unix_ms"]));
                Assert.That(
                    persistedRetry["scenarios"].Select(child => (string)child["definition_hash"]),
                    Is.EqualTo(report["scenarios"].Select(child => (string)child["definition_hash"]))
                );
                var mismatch =
                    ManagePlayScenario.HandleCommand(
                        new JObject
                        {
                            ["action"] = "suite_run",
                            ["name"] = Context.RepeatName,
                            ["suite_id"] = suiteId,
                            ["repeat_count"] = 2,
                            ["timeout_seconds"] = 60,
                            ["source_revision"] = "synthetic-suite-revision",
                        }
                    ) as ErrorResponse;
                Assert.That(mismatch, Is.Not.Null);
                Assert.That(mismatch.Error, Does.Contain("different run request"));
            }
            finally
            {
                runnerField.SetValue(null, current);
            }
        }

        [UnityTest]
        public IEnumerator SuiteStopAndContinuePoliciesPreserveFailureAndPersistOnlyFinalizedChildren()
        {
            Save(Context.WaitName, new JArray(SceneStep("menu", "load_scene", Menu), ObjectStep("missing", "wait_object", "NeverAppears", 1)));
            Save(Context.NextName, RepeatSteps());
            yield return new EnterPlayMode();
            foreach (string policy in new[] { "continue", "stop" })
            {
                JObject admitted = StartSuite(policy, Context.WaitName, Context.NextName);
                string suiteId = (string)admitted["suite_id"];
                yield return FinishSuite(suiteId);
                JObject report = SuiteStatus(suiteId);
                Assert.That((string)report["status"], Is.EqualTo(policy == "stop" ? "timed_out" : "failed"), report.ToString());
                Assert.That((string)report["scenarios"][0]["status"], Is.EqualTo("timed_out"));
                Assert.That((string)report["scenarios"][1]["status"], Is.EqualTo(policy == "stop" ? "skipped" : "succeeded"));
                Assert.That((string)report["error"], Is.Not.Empty);
                AssertSuiteReport(report);
            }
        }

        [UnityTest]
        public IEnumerator SuiteCancellationAndTotalTimeoutWaitForChildReportAndSkipRemainingQueue()
        {
            Save(Context.WaitName, new JArray(SceneStep("menu", "load_scene", Menu), ObjectStep("missing", "wait_object", "NeverAppears", 20)));
            Save(Context.NextName, RepeatSteps());
            yield return new EnterPlayMode();
            foreach (bool expire in new[] { false, true })
            {
                JObject admitted = StartSuite("continue", Context.WaitName, Context.NextName, expire ? 1 : 30);
                string suiteId = (string)admitted["suite_id"];
                double wait = Time.realtimeSinceStartupAsDouble + 10;
                while ((string)SuiteStatus(suiteId)["scenarios"][0]["status"] == "pending" && Time.realtimeSinceStartupAsDouble < wait)
                    yield return null;
                if (!expire)
                    Command(new JObject { ["action"] = "suite_cancel", ["suite_id"] = suiteId });
                yield return FinishSuite(suiteId);
                JObject report = SuiteStatus(suiteId);
                Assert.That((string)report["status"], Is.EqualTo(expire ? "timed_out" : "cancelled"), report.ToString());
                Assert.That((string)report["scenarios"][1]["status"], Is.EqualTo("skipped"));
                Assert.That(report["scenarios"][0]["report"], Is.TypeOf<JObject>());
                Assert.That((bool)report["scenarios"][0]["report"]["runner_resources_released"], Is.True);
                Assert.That((string)report["scenarios"][0]["report"]["report_path"], Is.Not.Empty);
                AssertSuiteReport(report);
            }
        }

        [UnityTearDown]
        public IEnumerator CleanupSuites()
        {
            string id = SessionState.GetString(SuiteContextKey + ".Id", "");
            if (Guid.TryParseExact(id, "N", out _))
            {
                PlayScenarioSuiteService.Handle(new JObject { ["action"] = "suite_cancel", ["suite_id"] = id });
                double deadline = Time.realtimeSinceStartupAsDouble + 10;
                while (Time.realtimeSinceStartupAsDouble < deadline)
                {
                    var response = PlayScenarioSuiteService.Handle(new JObject { ["action"] = "suite_status", ["suite_id"] = id }) as SuccessResponse;
                    if (response == null || (string)((JObject)response.Data)["status"] != "running")
                        break;
                    yield return null;
                }
            }
            string name = SessionState.GetString(SuiteContextKey + ".Name", "");
            if (!string.IsNullOrEmpty(name))
                PlayScenarioSuiteService.Handle(new JObject { ["action"] = "suite_delete", ["name"] = name });
            SessionState.EraseString(SuiteContextKey + ".Id");
            SessionState.EraseString(SuiteContextKey + ".Name");
        }

        private static JObject StartSuite(string policy, string first, string second, int timeout = 60)
        {
            string name = Context.RepeatName;
            SessionState.SetString(SuiteContextKey + ".Name", name);
            Command(
                new JObject
                {
                    ["action"] = "suite_save",
                    ["suite"] = new JObject
                    {
                        ["name"] = name,
                        ["scenarios"] = new JArray(first, second),
                        ["failure_policy"] = policy,
                    },
                }
            );
            string requestedId = Guid.NewGuid().ToString("N");
            JObject admitted = Command(
                new JObject
                {
                    ["action"] = "suite_run",
                    ["suite_id"] = requestedId,
                    ["name"] = name,
                    ["timeout_seconds"] = timeout,
                    ["source_revision"] = "synthetic-suite-revision",
                }
            );
            Assert.That((string)admitted["suite_id"], Is.EqualTo(requestedId));
            SessionState.SetString(SuiteContextKey + ".Id", (string)admitted["suite_id"]);
            return admitted;
        }

        private static JObject RetrySuite(string id, int timeout) =>
            Command(
                new JObject
                {
                    ["action"] = "suite_run",
                    ["name"] = Context.RepeatName,
                    ["suite_id"] = id,
                    ["repeat_count"] = 1,
                    ["timeout_seconds"] = timeout,
                    ["source_revision"] = "synthetic-suite-revision",
                }
            );

        private static JObject SuiteStatus(string id) => Command(new JObject { ["action"] = "suite_status", ["suite_id"] = id });

        private static IEnumerator FinishSuite(string id)
        {
            double deadline = Time.realtimeSinceStartupAsDouble + 40;
            while ((string)SuiteStatus(id)["status"] == "running" && Time.realtimeSinceStartupAsDouble < deadline)
                yield return null;
            Assert.That((string)SuiteStatus(id)["status"], Is.Not.EqualTo("running"), SuiteStatus(id).ToString());
        }

        private static void AssertSuiteReport(JObject report)
        {
            Assert.That((string)report["report_error"], Is.Null, report.ToString());
            string project = Path.GetDirectoryName(Application.dataPath);
            Assert.That(File.Exists(Path.Combine(project, (string)report["report_path"])), Is.True);
            foreach (JToken child in report["scenarios"].Where(child => (string)child["status"] != "skipped"))
                AssertReport((JObject)child["report"]);
            string evidence = Path.Combine(project, "Library/MCPForUnity/PlayScenarioIntegrationEvidence");
            Directory.CreateDirectory(evidence);
            File.WriteAllText(Path.Combine(evidence, (string)report["suite_id"] + ".suite.json"), report.ToString());
        }
    }
}
