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
        private string[] _savedDefinitions;

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
            _savedDefinitions = new[] { SessionState.GetString(SessionKey + ".definition.0", ""), SessionState.GetString(SessionKey + ".definition.1", "") };
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
            for (int i = 0; i < 2; i++)
                SessionState.SetString(SessionKey + ".definition." + i, _savedDefinitions[i]);
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
            var editorResponse = (SuccessResponse)PlayScenarioService.EditorStatus(run.JobId);
            var editorSnapshot = ((PlayScenarioEditorSnapshot)editorResponse.Data).Data;
            Assert.That((string)editorSnapshot["status"], Is.EqualTo("running"));
            Assert.That((string)editorSnapshot["phase"], Is.EqualTo("finalizing"));
            Assert.That((bool)editorSnapshot["runner_resources_released"], Is.False);
            Assert.That(editorSnapshot["scenario"]?["steps"], Is.Null);

            var replacement = (IMcpResponse)PlayScenarioService.Start("must-not-replace-unfinalized-job", 1, 30, null);
            Assert.That(replacement.Success, Is.False, "A new run must not discard a completed capture before its report is saved.");
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, true);
            var finished = (JObject)snapshotMethod.Invoke(null, new object[] { run });
            Assert.That((string)finished["status"], Is.EqualTo("cancelled"));
            Assert.That((string)finished["phase"], Is.EqualTo("finished"));
            Assert.That((bool)finished["runner_resources_released"], Is.True);
        }

        [Test]
        public void SuiteCoordinationDoesNotProjectAnActiveReport()
        {
            PlayScenarioRun run = StartingRun();
            run.Logs.Add(new PlayScenarioLog { Type = "Log", Message = new string('x', 100000) });
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            var logs = new PlayScenarioLogBuffer();
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, logs);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, false);
            logs.Add(run.StartedUnixMs, "Log", "fresh observation", "");
            Func<string, JObject> observe = SuiteObserver(run);

            JObject pending = observe(run.JobId);

            Assert.That((string)pending["status"], Is.EqualTo("running"));
            Assert.That(pending["scenario"], Is.Null, "Coordination must not project frozen inputs.");
            Assert.That(pending["steps"], Is.Null, "Coordination must not project accumulated steps.");
            Assert.That(pending["logs"], Is.Null, "Coordination must not project accumulated logs.");
            Assert.That(pending.ToString(Formatting.None).Length, Is.LessThan(256));
            Assert.That(run.Logs.Last().Message, Is.EqualTo("fresh observation"), "Observation still drains fresh logs.");
            var full = (JObject)((SuccessResponse)PlayScenarioService.Status(run.JobId)).Data;
            Assert.That(full["scenario"], Is.Not.Null, "Public status retains its complete contract.");
            Assert.That(full["logs"].Count(), Is.EqualTo(2));
        }

        [Test]
        public void SuiteCoordinationFetchesDetachedTerminalEvidenceOnlyAfterFinalization()
        {
            PlayScenarioRun run = StartingRun();
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            engine.Cancel(run.StartedUnixMs + 1);
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, null);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, false);
            Func<string, JObject> observe = SuiteObserver(run);
            JObject pending = observe(run.JobId);
            Assert.That((string)pending["status"], Is.EqualTo("running"));
            Assert.That((bool)pending["runner_resources_released"], Is.False);
            Assert.That(pending["steps"], Is.Null, "Pending finalization must remain a small observation.");

            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, true);
            JObject terminal = observe(run.JobId);
            JObject expected = (JObject)((SuccessResponse)PlayScenarioService.Status(run.JobId)).Data;
            Assert.That(JToken.DeepEquals(terminal, expected), Is.True);
            terminal["status"] = "consumer mutation";
            Assert.That(run.Status, Is.EqualTo("cancelled"));
        }

        private static Func<string, JObject> SuiteObserver(PlayScenarioRun run)
        {
            var suite = new PlayScenarioSuiteDefinition
            {
                Name = "observation-suite",
                Scenarios = new List<string> { run.Scenario.Name },
            };
            var state = PlayScenarioSuiteRunner.Create(suite, new[] { run.Scenario }, 1, 30, null, run.StartedUnixMs);
            var runner = (PlayScenarioSuiteRunner)
                typeof(PlayScenarioSuiteService).GetMethod("CreateRunner", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, new object[] { state });
            return (Func<string, JObject>)typeof(PlayScenarioSuiteRunner).GetField("observe", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(runner);
        }

        [Test]
        public void CheckpointSeparatesFrozenDefinitionAndRestoresFreshLogEvidence()
        {
            PlayScenarioRun run = StartingRun();
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            var logs = new PlayScenarioLogBuffer();
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, logs);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, false);
            var synchronize = (Action<bool>)Service.GetMethod("Synchronize", BindingFlags.Static | BindingFlags.NonPublic).CreateDelegate(typeof(Action<bool>));
            synchronize(true);
            JObject saved = JObject.Parse(SessionState.GetString(SessionKey, ""));
            Assert.IsNull(saved["scenario"], "Frozen inputs should not be projected into each mutable checkpoint.");
            logs.Add(17, "Log", "2026-10-10T12:34:56.000Z", "new-log");
            PlayScenarioService.Status(run.JobId); // Reading consumes pending logs without an engine transition.
            Service.GetField("_checkpointAt", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, 0L);
            synchronize(false);
            Invoke("Restore");
            var restored = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.AreEqual(run.Scenario.Name, restored.State.Scenario.Name);
            Assert.AreEqual("2026-10-10T12:34:56.000Z", restored.State.Logs.Last().Message);
            restored.Cancel(run.StartedUnixMs + 1);
        }

        [Test]
        public void UnchangedCheckpointDoesNotSerializeOnAnExpiredHeartbeat()
        {
            PlayScenarioRun run = StartingRun();
            var engine = new PlayScenarioEngine(run, new UnityPlayScenarioHost());
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, null);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, false);
            var synchronize = (Action<bool>)Service.GetMethod("Synchronize", BindingFlags.Static | BindingFlags.NonPublic).CreateDelegate(typeof(Action<bool>));
            synchronize(true);
            Service.GetField("_checkpointAt", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, 0L);
            using var recorder = Unity.Profiling.ProfilerRecorder.StartNew(
                Unity.Profiling.ProfilerCategory.Memory,
                "GC.Alloc",
                8192,
                Unity.Profiling.ProfilerRecorderOptions.StartImmediately | Unity.Profiling.ProfilerRecorderOptions.CollectOnlyOnCurrentThread
            );
            synchronize(false);
            recorder.Stop();
            Assert.IsTrue(recorder.Valid);
            Assert.AreEqual(0, recorder.Count, "No changed engine, poll or log evidence requires no checkpoint projection.");
        }

        [Test]
        public void SplitSuiteCheckpointRestoresLegacyAndRejectsMissingFrozenIdentity()
        {
            string key = "MCPForUnity.Tests.Opt02." + Guid.NewGuid().ToString("N");
            try
            {
                var definition = StartingRun().Scenario;
                var state = PlayScenarioSuiteRunner.Create(
                    new PlayScenarioSuiteDefinition
                    {
                        Name = "checkpoint-suite",
                        Scenarios = new List<string> { definition.Name },
                    },
                    new[] { definition },
                    1,
                    30,
                    null,
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                );
                var checkpoint = new PlayScenarioSessionCheckpoint(key, typeof(PlayScenarioSuiteState), "definitions", 40 * 1024 * 1024);
                string legacy = JsonConvert.SerializeObject(state);
                Assert.IsTrue(JToken.DeepEquals(JObject.Parse(legacy), checkpoint.Read(legacy)));
                checkpoint.Write(state, state.Definitions);
                string saved = SessionState.GetString(key, "");
                JObject compact = JObject.Parse(saved);
                string pointer = (string)compact["_frozen_definition"];
                Assert.IsNull(compact["definitions"]);
                Assert.AreEqual(definition.Name, (string)checkpoint.Read(saved)["definitions"][0]["name"]);
                SessionState.SetString(key + ".definition." + (pointer[0] == '0' ? '1' : '0'), "orphaned incomplete replacement");
                Assert.AreEqual(
                    definition.Name,
                    (string)checkpoint.Read(saved)["definitions"][0]["name"],
                    "The active pointer must ignore an unfinished other-slot write."
                );
                SessionState.EraseString(key + ".definition." + pointer[0]);
                Assert.That(() => checkpoint.Read(saved), Throws.Exception, "Missing frozen inputs must not be substituted or replayed.");
            }
            finally
            {
                SessionState.EraseString(key);
                SessionState.EraseString(key + ".definition.0");
                SessionState.EraseString(key + ".definition.1");
            }
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
