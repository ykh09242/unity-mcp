using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
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
        private readonly Dictionary<FieldInfo, object> _previousCheckpoint = new Dictionary<FieldInfo, object>();
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
            var checkpoint = Service.GetField("Checkpoint", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            foreach (FieldInfo field in checkpoint.GetType().GetFields(BindingFlags.Instance | BindingFlags.NonPublic).Where(field => !field.IsInitOnly))
                _previousCheckpoint[field] = field.GetValue(checkpoint);
        }

        [TearDown]
        public void TearDown()
        {
            if (_previous.Count == 0)
                return;
            Invoke("Detach");
            foreach (var pair in _previous)
                pair.Key.SetValue(null, pair.Value);
            var checkpoint = Service.GetField("Checkpoint", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            foreach (var pair in _previousCheckpoint)
                pair.Key.SetValue(checkpoint, pair.Value);
            if (_saved.Length == 0)
                SessionState.EraseString(SessionKey);
            else
                SessionState.SetString(SessionKey, _saved);
            for (int i = 0; i < 2; i++)
                SessionState.SetString(SessionKey + ".definition." + i, _savedDefinitions[i]);
            _previous.Clear();
            _previousCheckpoint.Clear();
        }

        [TestCase(true)]
        [TestCase(false)]
        public void WindowAndSuiteStartsRejectForeignNativeOwnerBeforeReserving(bool runningTest)
        {
            string owner = Guid.NewGuid().ToString("N");
            FieldInfo currentTest = typeof(TestJobManager).GetField("_currentJobId", BindingFlags.Static | BindingFlags.NonPublic);
            object savedTest = currentTest.GetValue(null);
            string suiteCheckpoint = SessionState.GetString("MCPForUnity.PlayScenarioSuite.V1", "");
            if (runningTest && savedTest != null)
                Assert.Ignore("An active test job owns the editor.");
            try
            {
                if (runningTest)
                    currentTest.SetValue(null, owner);
                else
                    BuildRunner.ReserveNative(owner);
                var scenario = (ErrorResponse)PlayScenarioService.Start("missing-unadmitted-scenario", 1, 30, owner);
                Assert.AreEqual("native_job_active", scenario.Error);
                var suite = (ErrorResponse)
                    PlayScenarioSuiteService.Handle(
                        new JObject
                        {
                            ["action"] = "suite_run",
                            ["name"] = "missing-unadmitted-suite",
                            ["suite_id"] = owner,
                        }
                    );
                Assert.AreEqual("native_job_active", suite.Error);
                Assert.AreEqual(0, QueuedPlayRequests());
                Assert.AreEqual(_saved, SessionState.GetString(SessionKey, ""));
                Assert.AreEqual(suiteCheckpoint, SessionState.GetString("MCPForUnity.PlayScenarioSuite.V1", ""));
            }
            finally
            {
                if (runningTest)
                    currentTest.SetValue(null, savedTest);
                else
                    BuildRunner.ReleaseNative(owner);
            }
        }

        [Test]
        public void OnlyReservedSuiteChildContinuesAndRechecksForeignWorkBeforeRequestingPlay()
        {
            if (PlayScenarioSuiteService.IsBusy || EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("An idle Edit Mode editor is required.");
            var definition = StartingRun().Scenario;
            var store = new PlayScenarioStore(Path.GetDirectoryName(UnityEngine.Application.dataPath));
            if (store.ListReports(null).Count >= 20)
                Assert.Ignore("Preserve the existing report history.");
            var state = PlayScenarioSuiteRunner.Create(
                new PlayScenarioSuiteDefinition
                {
                    Name = "admission-suite",
                    Scenarios = new List<string> { definition.Name },
                },
                new[] { definition },
                1,
                30,
                null,
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            );
            var runner = new PlayScenarioSuiteRunner(state, null, null, null, () => { }, () => false);
            FieldInfo owner = typeof(PlayScenarioSuiteService).GetField("_runner", BindingFlags.Static | BindingFlags.NonPublic);
            var previous = owner.GetValue(null);
            string id = runner.Current.JobId;
            string foreign = Guid.NewGuid().ToString("N");
            try
            {
                owner.SetValue(null, runner);
                Assert.IsFalse(((IMcpResponse)PlayScenarioService.Start(definition, 1, 30, id)).Success, "Pending children have no admission.");
                runner.Current.Status = "running";
                Assert.IsFalse(
                    ((IMcpResponse)PlayScenarioService.Start(definition.Name, 1, 30, id)).Success,
                    "A public request cannot claim a suite reservation."
                );
                Assert.IsFalse(((IMcpResponse)PlayScenarioService.Start(definition, 1, 30, foreign)).Success);
                Assert.IsTrue(((IMcpResponse)PlayScenarioService.Start(definition, 1, 30, id)).Success);
                Assert.IsFalse(NativeOperationBarriers.TryGetScenarioBlocker(id, id, out _));
                Assert.IsTrue(NativeOperationBarriers.TryGetScenarioBlocker(foreign, id, out _));
                Assert.IsTrue(NativeOperationBarriers.TryGetScenarioBlocker(id, foreign, out _));
                BuildRunner.ReserveNative(id); // A coincident ID from a different native family remains foreign.
                Invoke("RequestPlay");
                var engine = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
                Assert.IsFalse(engine.State.PlayRequested);
                Assert.AreEqual("failed", engine.State.Status);
                StringAssert.Contains("native_job_active", engine.State.Error);
                Assert.AreEqual(0, QueuedPlayRequests());
                var replay = (SuccessResponse)PlayScenarioService.Start(definition, 1, 30, id);
                Assert.AreEqual("failed", (string)((JObject)replay.Data)["status"]);
            }
            finally
            {
                BuildRunner.ReleaseNative(id);
                owner.SetValue(null, previous);
                string reportPath = Path.Combine(Path.GetDirectoryName(UnityEngine.Application.dataPath), "Library/MCPForUnity/PlayScenarioRuns", id + ".json");
                if (File.Exists(reportPath))
                    File.Delete(reportPath);
            }
        }

        [Test]
        public void DatasetJobRetrySurvivesTemplateEditsAndDeletionWithoutStartingAgain()
        {
            if (PlayScenarioSuiteService.IsBusy || EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("An idle Edit Mode editor is required.");
            string root = Path.GetDirectoryName(UnityEngine.Application.dataPath);
            var store = new PlayScenarioStore(root);
            if (store.ListReports().Count >= 20)
                Assert.Ignore("Preserve the existing report history.");
            string name = "retry-" + Guid.NewGuid().ToString("N");
            const string dataset = "case--one";
            string job = Guid.NewGuid().ToString("N");
            string reportPath = Path.Combine(root, "Library/MCPForUnity/PlayScenarioRuns", job + ".json");
            var template = JObject.FromObject(StartingRun().Scenario);
            template["name"] = name;
            template["parameters"] = JObject.Parse("{'limit':{'type':'integer','default':5}}");
            template["parameter_bindings"] = JArray.Parse("[{'parameter':'limit','path':'/steps/0/timeout_seconds'}]");
            template["datasets"] = JArray.Parse("[{'name':'case--one','values':{'limit':7}}]");
            var engineField = Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic);
            try
            {
                store.Save(PlayScenarioDefinition.Parse(template));
                var admitted = (SuccessResponse)PlayScenarioService.Start(name, 2, 30, job, "build", dataset);
                var initial = (JObject)admitted.Data;
                Assert.AreEqual(7, (int)initial["scenario"]["steps"][0]["timeout_seconds"]);
                string hash = (string)initial["reproduction"]["definition_hash"];
                object runningEngine = engineField.GetValue(null);
                object playRequest = Service.GetField("_playRequest", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
                template["datasets"][0]["values"]["limit"] = 11;
                store.Save(PlayScenarioDefinition.Parse(template));
                var running = (SuccessResponse)PlayScenarioService.Start(name, 2, 30, job, "build", dataset);
                Assert.AreEqual(hash, (string)((JObject)running.Data)["reproduction"]["definition_hash"]);
                Assert.AreSame(runningEngine, engineField.GetValue(null));
                Assert.AreSame(playRequest, Service.GetField("_playRequest", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));

                store.Delete(name);
                PlayScenarioService.Cancel(job);
                PlayScenarioRun report = store.GetReport(job);
                Assert.AreEqual("cancelled", report.Status);
                Assert.AreEqual(name, report.RequestedName);
                Assert.AreEqual(dataset, report.RequestedDataset);
                engineField.SetValue(null, null);
                var replay = (SuccessResponse)PlayScenarioService.Start(name, 2, 30, job, "build", dataset);
                Assert.AreEqual("cancelled", (string)((JObject)replay.Data)["status"]);
                Assert.AreEqual(hash, (string)((JObject)replay.Data)["reproduction"]["definition_hash"]);
                Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start(name, 2, 30, job, "build", "other"));
                Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start(name + "--case", 2, 30, job, "build", "one"));
                Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start(name + "--" + dataset, 2, 30, job, "build"));
                Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start(name, 1, 30, job, "build", dataset));
                Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start(name, 2, 31, job, "build", dataset));
                Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start(name, 2, 30, job, "other", dataset));
                Assert.IsNull(engineField.GetValue(null));
                Assert.AreEqual(0, QueuedPlayRequests());
            }
            finally
            {
                store.Delete(name);
                if (File.Exists(reportPath))
                    File.Delete(reportPath);
            }
        }

        [Test]
        public void LegacyReportsReplayUnambiguousNamesAndRejectUnprovableDatasetIdentity()
        {
            PlayScenarioRun legacy = StartingRun();
            var engine = new PlayScenarioEngine(legacy, new UnityPlayScenarioHost());
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Assert.IsInstanceOf<SuccessResponse>(PlayScenarioService.Start(legacy.Scenario.Name, 1, 30, legacy.JobId));
            legacy.Scenario.Name = "suite--case";
            legacy.Reproduction.DefinitionHash = PlayScenarioReproduction.Hash(legacy.Scenario);
            PlayScenarioStore.ValidateReport(JObject.FromObject(legacy));
            Assert.IsInstanceOf<SuccessResponse>(PlayScenarioService.Status(legacy.JobId));
            Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start("suite", 1, 30, legacy.JobId, dataset: "case"));
            Assert.IsInstanceOf<ErrorResponse>(PlayScenarioService.Start("suite--case", 1, 30, legacy.JobId));
            Assert.AreEqual(0, QueuedPlayRequests());
        }

        [Test]
        public void FailedStartRestoresPreviousJobAndCheckpointAndCanBeRetried()
        {
            if (EditorApplication.isPlaying || EditorApplication.isPlayingOrWillChangePlaymode)
                Assert.Ignore("This test requires a stable Edit Mode editor.");
            PlayScenarioRun previous = StartingRun();
            var previousEngine = new PlayScenarioEngine(previous, new UnityPlayScenarioHost());
            previousEngine.Cancel(previous.StartedUnixMs + 1);
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, previousEngine);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, true);
            var checkpoint = (PlayScenarioSessionCheckpoint)Service.GetField("Checkpoint", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            checkpoint.Write(previous, previous.Scenario);
            string saved = SessionState.GetString(SessionKey, "");
            string[] slots = { SessionState.GetString(SessionKey + ".definition.0", ""), SessionState.GetString(SessionKey + ".definition.1", "") };
            string jobId = Guid.NewGuid().ToString("N");
            checkpoint.BeforePointerWrite = () => throw new InvalidOperationException("injected checkpoint failure");
            try
            {
                Assert.Throws<InvalidOperationException>(() => PlayScenarioService.Start(previous.Scenario, 1, 30, jobId));
                Assert.AreSame(previousEngine, Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null));
                Assert.IsFalse(PlayScenarioService.IsBusy);
                Assert.AreEqual(0, PlayScenarioService.ActiveSubscriptionCount);
                Assert.AreEqual(0, QueuedPlayRequests());
                Assert.AreEqual(saved, SessionState.GetString(SessionKey, ""));
                Assert.AreEqual(slots[0], SessionState.GetString(SessionKey + ".definition.0", ""));
                Assert.AreEqual(slots[1], SessionState.GetString(SessionKey + ".definition.1", ""));
                Assert.AreEqual(previous.JobId, (string)checkpoint.Read(saved)["job_id"]);
                long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                var suite = new PlayScenarioSuiteDefinition
                {
                    Name = "admission-suite",
                    Scenarios = new List<string> { previous.Scenario.Name },
                };
                var suiteState = PlayScenarioSuiteRunner.Create(suite, new[] { previous.Scenario }, 1, 30, null, now);
                var runner = new PlayScenarioSuiteRunner(
                    suiteState,
                    (definition, repeats, timeout, childId, revision) =>
                    {
                        PlayScenarioService.Start(definition, repeats, timeout, childId, revision);
                        return null;
                    },
                    id => null,
                    id => null,
                    () => { },
                    () => PlayScenarioService.IsBusy
                );
                runner.Tick(now, true);
                Assert.AreEqual("failed", suiteState.Report.Status);
                Assert.AreEqual("failed", suiteState.Report.Scenarios[0].Status);
                Assert.IsFalse(PlayScenarioService.IsBusy, "A terminal suite must not leave its child running.");
                Assert.AreEqual(0, PlayScenarioService.ActiveSubscriptionCount);
                Assert.AreEqual(0, QueuedPlayRequests());
            }
            finally
            {
                checkpoint.BeforePointerWrite = null;
            }
            var response = (IMcpResponse)PlayScenarioService.Start(previous.Scenario, 1, 30, jobId);
            Assert.IsTrue(response.Success);
            var current = (PlayScenarioEngine)Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);
            Assert.AreEqual(jobId, current.State.JobId);
            Assert.IsTrue(current.State.Steps.All(step => step.Status == "pending"));
            Invoke("Detach");
            current.Release();
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
        public void FinalizedOneShotReleaseFailureRequiresInspectionAndRestartAcrossReload()
        {
            if (new PlayScenarioStore(Path.GetDirectoryName(UnityEngine.Application.dataPath)).ListReports().Count >= 20)
                Assert.Ignore("Preserve the existing report history.");
            var run = StartingRun();
            var host = new FailingReleaseHost();
            var engine = new PlayScenarioEngine(run, host);
            Service.GetField("_engine", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, engine);
            Service.GetField("_logs", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, null);
            Service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic).SetValue(null, false);
            string path = Path.Combine(Path.GetDirectoryName(UnityEngine.Application.dataPath), "Library/MCPForUnity/PlayScenarioRuns", run.JobId + ".json");
            try
            {
                engine.Cancel(run.StartedUnixMs + 1);
                Assert.AreEqual(false, run.RunnerResourcesReleased);
                Assert.IsTrue(NativeOperationBarriers.ObserveReadiness(false).Retryable, "Finalization can still make progress.");
                Service.GetMethod("Synchronize", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, new object[] { true });
                Assert.That(run.ReportError, Does.Contain("injected host release failure"));
                AssertCleanupRecovery(run.JobId);
                Invoke("Restore");
                AssertCleanupRecovery(run.JobId);
                Assert.AreEqual(1, host.Attempts);
            }
            finally
            {
                if (File.Exists(path))
                    File.Delete(path);
            }
        }

        private static void AssertCleanupRecovery(string jobId)
        {
            var blocker = NativeOperationBarriers.ObserveReadiness(false);
            Assert.AreEqual("recovery_required", blocker.Reason);
            Assert.IsFalse(blocker.Retryable);
            Assert.AreEqual("inspect_native_work_then_restart_editor", blocker.RecoveryAction);
            Assert.AreEqual("restart_required", blocker.Recovery.Mode);
            Assert.AreEqual("play_scenario", blocker.Recovery.Source);
            Assert.AreEqual(jobId, blocker.Recovery.JobId);
            Assert.IsTrue(NativeOperationBarriers.TryGetScenarioBlocker(jobId, jobId, out blocker));
            Assert.IsFalse(blocker.Retryable, "A continuation cannot exempt an unreleased terminal owner.");
        }

        private sealed class FailingReleaseHost : IPlayScenarioHost
        {
            internal int Attempts;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll) => throw new InvalidOperationException("Unexpected execution.");

            public void Release()
            {
                Attempts++;
                throw new InvalidOperationException("injected host release failure");
            }
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
        public void FrozenCheckpointIgnoresGlobalJsonDefaultSettings()
        {
            string key = "MCPForUnity.Tests.CheckpointDefaults." + Guid.NewGuid().ToString("N");
            var previous = JsonConvert.DefaultSettings;
            try
            {
                JsonConvert.DefaultSettings = () => new JsonSerializerSettings { TypeNameHandling = TypeNameHandling.All };
                PlayScenarioRun run = StartingRun();
                var checkpoint = new PlayScenarioSessionCheckpoint(key, typeof(PlayScenarioRun), "scenario");
                checkpoint.Write(run, run.Scenario);
                JObject restored = checkpoint.Read(SessionState.GetString(key, ""));
                Assert.AreEqual(run.Scenario.Name, (string)restored["scenario"]["name"]);
                Assert.IsFalse(
                    restored.Descendants().OfType<JProperty>().Any(property => property.Name == "$type"),
                    "Recovery input must use the repository serializer, independent of user global settings."
                );
            }
            finally
            {
                JsonConvert.DefaultSettings = previous;
                SessionState.EraseString(key);
                SessionState.EraseString(key + ".definition.0");
                SessionState.EraseString(key + ".definition.1");
            }
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
            Service.GetField("_playRequest", BindingFlags.Static | BindingFlags.NonPublic).GetValue(null) == null ? 0 : 1;

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
