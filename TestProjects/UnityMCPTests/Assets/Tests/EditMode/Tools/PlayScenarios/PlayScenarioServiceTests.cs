using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioServiceTests
    {
        private const string SessionKey = "MCPForUnity.PlayScenario.V1";
        private static readonly Type Service = typeof(PlayScenarioService);
        private readonly Dictionary<FieldInfo, object> _previous = new Dictionary<FieldInfo, object>();
        private string _saved;

        [SetUp]
        public void SetUp()
        {
            // Do not replace a user's active job if these tests run in their editor.
            var current = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            var capture = Service.GetField("_failureCapture", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            bool pendingCapture =
                capture != null && (bool)capture.GetType().GetProperty("Pending", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(capture);
            if (current?.Running == true || pendingCapture)
                Assert.Ignore("An active scenario owns the editor.");
            _saved = SessionState.GetString(SessionKey, "");
            foreach (FieldInfo field in Service.GetFields(BindingFlags.Static | BindingFlags.NonPublic).Where(field => !field.IsLiteral && !field.IsInitOnly))
                _previous[field] = field.GetValue(null);
        }

        [TearDown]
        public void TearDown()
        {
            if (_previous.Count == 0)
                return;
            Invoke("Detach");
            foreach (var pair in _previous)
                pair.Key.SetValue(null, pair.Value);
            if (_saved.Length == 0)
                SessionState.EraseString(SessionKey);
            else
                SessionState.SetString(SessionKey, _saved);
            _previous.Clear();
        }

        [Test]
        public void RestoreReschedulesAStartThatHadNotRequestedPlayYet()
        {
            PlayScenarioRun run = StartingRun();
            SessionState.SetString(SessionKey, JsonConvert.SerializeObject(run));
            Invoke("Restore");
            Assert.That(QueuedPlayRequests(), Is.EqualTo(1));
            var restored = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            restored.Cancel(run.StartedUnixMs + 1);
            Invoke("Detach");
            Assert.That(QueuedPlayRequests(), Is.Zero);
            Assert.That(restored.State.Status, Is.EqualTo("cancelled"));
            Assert.That(Service.GetField("_logCallback", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null), Is.Null);
        }

        [Test]
        public void RestorePreservesIsoLookingStepNamesAndLogsAsStrings()
        {
            PlayScenarioRun run = StartingRun();
            const string text = "2026-10-10T12:34:56.000Z";
            run.Scenario.Steps[0].Name = text;
            run.Steps[0].Name = text;
            run.Logs.Add(
                new PlayScenarioLog
                {
                    Type = "Log",
                    Message = text,
                    StackTrace = text,
                }
            );
            SessionState.SetString(SessionKey, JsonConvert.SerializeObject(run));
            Invoke("Restore");
            var restored = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.That(restored.State.Steps[0].Name, Is.EqualTo(text));
            Assert.That(restored.State.Logs[0].Message, Is.EqualTo(text));
            restored.Cancel(run.StartedUnixMs + 1);
        }

        [TestCase("status")]
        [TestCase("cancel")]
        public void JobIdsWithTrailingNewlinesAreRejected(string action)
        {
            var response = (IMcpResponse)ManagePlayScenario.HandleCommand(new JObject { ["action"] = action, ["job_id"] = new string('a', 32) + "\n" });
            Assert.That(response.Success, Is.False);
        }

        [Test]
        public void ReadingStatusDoesNotConsumeAnUnexpectedErrorBeforeTheRunnerHandlesIt()
        {
            PlayScenarioRun run = StartingRun();
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            var logs = new PlayScenarioLogBuffer();
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, logs);
            Service.GetField("_processedUnexpectedLogCount", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, 0);
            logs.Add(run.StartedUnixMs, "Error", "Unexpected initialization failure", "stack");

            var snapshot = (IMcpResponse)PlayScenarioService.Status(run.JobId);
            Assert.That(snapshot.Success, Is.True);
            Assert.That(run.UnexpectedLogCount, Is.EqualTo(1));
            Invoke("DrainAndApplyErrors");
            Assert.That(run.Status, Is.EqualTo("failed"));
            StringAssert.Contains("Unexpected initialization failure", run.Error);
            Assert.That(run.RunnerResourcesReleased, Is.True);
            Invoke("DrainAndApplyErrors");
            Assert.That(run.UnexpectedLogCount, Is.EqualTo(1));
        }

        [TestCase("{'action':'reports','name':null}")]
        [TestCase("{'action':'reports','name':'../outside'}")]
        [TestCase("{'action':'reports','repeat_count':2}")]
        [TestCase("{'action':'reports','job_id':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}")]
        public void ReportHistoryRejectsInvalidFiltersAndUnrelatedArguments(string json)
        {
            var response = (IMcpResponse)ManagePlayScenario.HandleCommand(JObject.Parse(json));
            Assert.That(response.Success, Is.False);
        }

        [Test]
        public void TerminalOutcomeIsNotPublishedUntilFailureEvidenceAndReportAreFinalized()
        {
            PlayScenarioRun run = StartingRun();
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            engine.Cancel(run.StartedUnixMs + 1);
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, null);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, false);
            var snapshotMethod = Service.GetMethod("Snapshot", BindingFlags.Static | BindingFlags.NonPublic);

            var pending = (JObject)snapshotMethod.Invoke(null, new object[] { run });
            Assert.That((string)pending["status"], Is.EqualTo("running"));
            Assert.That((string)pending["phase"], Is.EqualTo("finalizing"));
            Assert.That((string)pending["pending_status"], Is.EqualTo("cancelled"));
            Assert.That((bool)pending["runner_resources_released"], Is.False);
            Assert.That(run.Status, Is.EqualTo("cancelled"));

            var replacement = (IMcpResponse)PlayScenarioService.Start("must-not-replace-unfinalized-job", 1, 30, null);
            Assert.That(replacement.Success, Is.False, "A new run must not discard a completed capture before its report is saved.");
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, true);
            var finished = (JObject)snapshotMethod.Invoke(null, new object[] { run });
            Assert.That((string)finished["status"], Is.EqualTo("cancelled"));
            Assert.That((string)finished["phase"], Is.EqualTo("finished"));
            Assert.That((bool)finished["runner_resources_released"], Is.True);
        }

        private static int QueuedPlayRequests() =>
            EditorApplication
                .delayCall?.GetInvocationList()
                .Count(callback => callback.Method.DeclaringType == Service && callback.Method.Name == "RequestPlay")
            ?? 0;

        private static void Invoke(string name) => Service.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);

        private static PlayScenarioRun StartingRun()
        {
            PlayScenarioDefinition definition = PlayScenarioDefinition.Parse(
                JObject.Parse(
                    @"{
                'name':'service-test','steps':[{'name':'Menu','action':'load_scene','scene':'Assets/Menu.unity'}]}"
                )
            );
            return PlayScenarioEngine.Create(definition, Guid.NewGuid().ToString("N"), 1, 30, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());
        }
    }
}
