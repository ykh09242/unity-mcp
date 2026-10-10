using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioSuiteTests
    {
        private string root;
        private PlayScenarioStore scenarios;
        private PlayScenarioSuiteStore suites;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetTempPath(), "MCPForUnitySuiteTests_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            scenarios = new PlayScenarioStore(root);
            suites = new PlayScenarioSuiteStore(root);
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

        [Test]
        public void SelectionKeepsExplicitOrderThenOrdinalTagMatchesAndFreezesAdmissionValues()
        {
            foreach (string name in new[] { "zulu", "alpha", "beta", "untagged" })
            {
                PlayScenarioDefinition definition = Definition(name);
                if (name != "untagged")
                    definition.Tags.Add("smoke");
                scenarios.Save(definition);
            }
            var suite = new PlayScenarioSuiteDefinition
            {
                Name = "ordered",
                Scenarios = new List<string> { "zulu", "beta" },
                Tags = new List<string> { "smoke" },
            };
            IReadOnlyList<PlayScenarioDefinition> selected = suite.Resolve(scenarios);
            Assert.That(selected.Select(item => item.Name), Is.EqualTo(new[] { "zulu", "beta", "alpha" }));
            PlayScenarioSuiteState state = PlayScenarioSuiteRunner.Create(suite, selected, 1, 30, "caller-label", 1000);
            string admittedHash = state.Report.Scenarios[0].DefinitionHash;
            selected[0].Steps[0].Scene = "Assets/Changed.unity";
            scenarios.Delete("beta");
            Assert.That(state.Definitions[0].Steps[0].Scene, Is.EqualTo("Assets/Menu.unity"));
            Assert.That(state.Definitions[1].Name, Is.EqualTo("beta"));
            Assert.That(state.Report.SourceRevision, Is.EqualTo("caller-label"));
            Assert.That(state.Report.Scenarios[0].DefinitionHash, Is.EqualTo(admittedHash));
            Assert.That(admittedHash, Is.EqualTo(PlayScenarioReproduction.Hash(state.Definitions[0])));
        }

        [TestCase("{'name':'suite','scenarios':['a','a']}")]
        [TestCase("{'name':'suite','tags':['smoke','smoke']}")]
        [TestCase("{'name':'suite','scenarios':[],'tags':[]}")]
        [TestCase("{'name':'suite','scenarios':['a'],'schema_version':2}")]
        [TestCase("{'name':'suite','scenarios':['a'],'failure_policy':'retry'}")]
        [TestCase("{'name':'suite','scenarios':['a'],'timeout_seconds':5}")]
        [TestCase("{'name':'suite','scenarios':[null]}")]
        public void DefinitionRejectsInvalidSelectorsAndUnownedFields(string input) =>
            Assert.Throws<ArgumentException>(() => PlayScenarioSuiteDefinition.Parse(JObject.Parse(input)));

        [Test]
        public void EmptyAndExcessiveResolvedSelectionsFailBeforeExecution()
        {
            var suite = new PlayScenarioSuiteDefinition
            {
                Name = "suite",
                Tags = new List<string> { "smoke" },
            };
            Assert.Throws<ArgumentException>(() => suite.Resolve(scenarios));
            for (int i = 0; i < 17; i++)
            {
                PlayScenarioDefinition definition = Definition("scenario-" + i);
                definition.Tags.Add("smoke");
                scenarios.Save(definition);
            }
            Assert.Throws<ArgumentException>(() => suite.Resolve(scenarios));
        }

        [Test]
        public void SavedSuitesRoundTripAndRejectTamperedNamesAndTrailingJson()
        {
            suites.Save(Suite("stop"));
            Assert.That(suites.List(), Is.EqualTo(new[] { "suite" }));
            Assert.That(suites.Get("suite").Scenarios, Is.EqualTo(new[] { "first", "second" }));
            string path = Path.Combine(root, "ProjectSettings/MCPForUnity/PlayScenarioSuites/suite.json");
            File.WriteAllText(path, "{'name':'other','scenarios':['first']}");
            Assert.Throws<InvalidDataException>(() => suites.Get("suite"));
            File.WriteAllText(path, "{'name':'suite','scenarios':['first']} {}");
            Assert.That(() => suites.Get("suite"), Throws.Exception);
            Assert.Throws<ArgumentException>(() => suites.Get("../outside"));
        }

        [TestCase("stop", "skipped", 1)]
        [TestCase("continue", "succeeded", 2)]
        public void FailurePolicyPreservesFirstFailureAndWaitsForFinalizedChild(string policy, string secondStatus, int launches)
        {
            var harness = new Harness(Suite(policy));
            harness.Runner.Tick(1001, true);
            Assert.That(harness.Starts, Is.EqualTo(1));
            harness.Snapshot = new JObject
            {
                ["status"] = "running",
                ["phase"] = "finalizing",
                ["pending_status"] = "failed",
                ["runner_resources_released"] = false,
            };
            harness.Runner.Tick(1002, true);
            Assert.That(harness.Starts, Is.EqualTo(1));
            Assert.That(harness.Runner.State.Cursor, Is.Zero);
            harness.Complete("failed", "first failure");
            harness.Runner.Tick(1003, true);
            harness.Runner.Tick(1004, true);
            if (policy == "continue")
            {
                harness.Complete("succeeded", null);
                harness.Runner.Tick(1005, true);
            }
            Assert.That(harness.Starts, Is.EqualTo(launches));
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo("failed"));
            Assert.That(harness.Runner.State.Report.Error, Is.EqualTo("first failure"));
            Assert.That(harness.Runner.State.Report.Scenarios[1].Status, Is.EqualTo(secondStatus));
        }

        [TestCase("cancelled")]
        [TestCase("timed_out")]
        public void CancellationAndTimeoutWaitThroughChildCleanupAndCancelOnlyOnce(string reason)
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            harness.Runner.RequestStop(reason, "requested stop", 1002);
            harness.Runner.Tick(1003, true);
            harness.Runner.Tick(1004, true);
            Assert.That(harness.Cancels, Is.EqualTo(1));
            Assert.That(harness.Runner.Running, Is.True);
            Assert.That(harness.Starts, Is.EqualTo(1));
            harness.Complete("cancelled", "child cancelled");
            harness.Runner.Tick(1005, true);
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo(reason));
            Assert.That(harness.Runner.State.Report.Scenarios[1].Status, Is.EqualTo("skipped"));
            Assert.That(harness.Runner.State.Report.Scenarios[0].Report, Is.Not.Null);
        }

        [TestCase("cancelled", 1)]
        [TestCase("cancelled", 50)]
        [TestCase("timed_out", 1)]
        [TestCase("timed_out", 50)]
        public void SynchronousChildCancellationCannotFinishAfterThePersistedSuite(string outcome, int clockAdvance)
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            long sampledNow = outcome == "timed_out" ? harness.Runner.State.DeadlineUnixMs : 1003;
            harness.SynchronousCancelFinishedUnixMs = sampledNow + clockAdvance;
            if (outcome == "cancelled")
                harness.Runner.RequestStop(outcome, "requested stop", sampledNow);
            harness.Runner.Tick(sampledNow, true);
            Assert.That(harness.Cancels, Is.EqualTo(1));
            Assert.That(harness.Starts, Is.EqualTo(1));
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo(outcome));
            Assert.That(harness.Runner.State.Report.Scenarios[1].Status, Is.EqualTo("skipped"));
            long childFinished = (long)harness.Runner.State.Report.Scenarios[0].Report["finished_unix_ms"];
            Assert.That(childFinished, Is.EqualTo(sampledNow + clockAdvance));
            Assert.That(harness.Runner.State.Report.FinishedUnixMs, Is.EqualTo(childFinished));
            suites.SaveReport(harness.Runner.State.Report);
            Assert.That(suites.GetReport(harness.Runner.State.Report.SuiteId).FinishedUnixMs, Is.EqualTo(childFinished));
        }

        [Test]
        public void LaterCancellationCannotMaskAnEarlierContinuedFailure()
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            harness.Complete("failed", "original failure");
            harness.Runner.Tick(1002, true);
            harness.Runner.Tick(1003, true);
            harness.Runner.RequestStop("cancelled", "later cancellation", 1004);
            harness.Complete("cancelled", "child cancelled");
            harness.Runner.Tick(1005, true);
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo("failed"));
            Assert.That(harness.Runner.State.Report.Error, Is.EqualTo("original failure"));
        }

        [Test]
        public void TotalDeadlineStopsQueueBeforeAnyNewChild()
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(harness.Runner.State.DeadlineUnixMs, true);
            Assert.That(harness.Starts, Is.Zero);
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo("timed_out"));
            Assert.That(harness.Runner.State.Report.Scenarios.All(child => child.Status == "skipped"), Is.True);
        }

        [Test]
        public void RestoreOfRunningReservationObservesChildWithoutReplayingStart()
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            string checkpoint = JsonConvert.SerializeObject(harness.Runner.State);
            PlayScenarioSuiteState restored = JsonConvert.DeserializeObject<PlayScenarioSuiteState>(checkpoint);
            int replayed = 0;
            var runner = new PlayScenarioSuiteRunner(
                restored,
                (definition, repeat, timeout, id, revision) =>
                {
                    replayed++;
                    return null;
                },
                id => harness.Snapshot,
                id => harness.Snapshot,
                () => { },
                () => false
            );
            runner.Tick(1002, true);
            Assert.That(replayed, Is.Zero);
            Assert.That(runner.State.Report.Scenarios[0].JobId, Is.EqualTo(harness.Runner.State.Report.Scenarios[0].JobId));
        }

        [Test]
        public void RestoredQueueRejectsAChangedDefinitionAgainstItsAdmissionHash()
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            PlayScenarioSuiteState restored = JsonConvert.DeserializeObject<PlayScenarioSuiteState>(JsonConvert.SerializeObject(harness.Runner.State));
            restored.Definitions[1].Steps[0].Scene = "Assets/ChangedAfterCheckpoint.unity";
            Assert.Throws<InvalidDataException>(() =>
                new PlayScenarioSuiteRunner(
                    restored,
                    (definition, repeat, timeout, id, revision) => null,
                    id => harness.Snapshot,
                    id => harness.Snapshot,
                    () => { },
                    () => false
                )
            );
        }

        [Test]
        public void ChildPersistenceFailureAndOversizedAggregateNeverPublishSuccess()
        {
            var harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            harness.Complete("succeeded", null);
            harness.Snapshot["report_path"] = null;
            harness.Snapshot["report_error"] = "disk is full";
            harness.Runner.Tick(1002, true);
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo("failed"));
            Assert.That(harness.Runner.State.Report.ReportError, Does.Contain("disk is full"));
            Assert.That(harness.Starts, Is.EqualTo(1));

            harness = new Harness(Suite("continue"));
            harness.Runner.Tick(1001, true);
            harness.Complete("succeeded", null);
            harness.Snapshot["oversized_test_value"] = new string('x', 2 * 1024 * 1024);
            harness.Runner.Tick(1002, true);
            Assert.That(harness.Runner.State.Report.Status, Is.EqualTo("failed"));
            Assert.That(harness.Runner.State.Report.ReportError, Does.Contain("aggregate limit"));
            Assert.That(harness.Runner.State.Report.Scenarios[0].Report, Is.Null);
            Assert.That(harness.Runner.State.Report.Scenarios[0].JobId, Is.Not.Empty);
        }

        [Test]
        public void ReportRetentionKeepsTwentyAndRejectsCrossChildIdentity()
        {
            for (int i = 0; i < 21; i++)
            {
                var harness = new Harness(
                    new PlayScenarioSuiteDefinition
                    {
                        Name = "suite",
                        Scenarios = new List<string> { "first" },
                    }
                );
                harness.Runner.Tick(1001, true);
                harness.Complete("succeeded", null);
                harness.Runner.Tick(1002 + i, true);
                suites.SaveReport(harness.Runner.State.Report);
            }
            Assert.That(suites.ListReports().Count, Is.EqualTo(20));
            PlayScenarioSuiteReport report = suites.ListReports()[0];
            report.Scenarios[0].Report["job_id"] = Guid.NewGuid().ToString("N");
            Assert.Throws<ArgumentException>(() => suites.SaveReport(report));
        }

        [TestCase(
            "{'action':'suite_run','suite_id':'0123456789abcdef0123456789abcdef','source_revision':'fixture-build','name':'operations-suite','repeat_count':2,'timeout_seconds':120}"
        )]
        [TestCase("{'action':'suite_run','suite_id':'438e091113cd44149c3978892be813e6','name':'operations-suite','repeat_count':1,'timeout_seconds':300}")]
        public void CapturedRealCliParametersAreAcceptedByNativeDispatch(string captured)
        {
            JObject request = JObject.Parse(captured);
            PlayScenarioSuiteDefinition suite = Suite("stop");
            suite.Name = (string)request["name"];
            var harness = new Harness(
                suite,
                (int)request["repeat_count"],
                (int)request["timeout_seconds"],
                (string)request["source_revision"],
                (string)request["suite_id"]
            );
            WithCurrentSuite(
                harness.Runner,
                false,
                () =>
                {
                    var response = ManagePlayScenario.HandleCommand(request) as SuccessResponse;
                    Assert.That(response, Is.Not.Null);
                    Assert.That((string)((JObject)response.Data)["suite_id"], Is.EqualTo((string)request["suite_id"]));
                    Assert.That(harness.Starts, Is.Zero);
                }
            );
        }

        [Test]
        public void CallerSuiteIdAndOriginalRequestMetadataArePersisted()
        {
            string id = Guid.NewGuid().ToString("N");
            PlayScenarioSuiteState state = PlayScenarioSuiteRunner.Create(
                Suite("stop"),
                new[] { Definition("first"), Definition("second") },
                2,
                45,
                "caller-label",
                1000,
                id
            );
            Assert.That(state.Report.SuiteId, Is.EqualTo(id));
            Assert.That(state.Report.RepeatCount, Is.EqualTo(2));
            Assert.That(state.Report.TimeoutSeconds, Is.EqualTo(45));
            PlayScenarioSuiteState restored = JsonConvert.DeserializeObject<PlayScenarioSuiteState>(JsonConvert.SerializeObject(state));
            Assert.That(restored.Report.SuiteId, Is.EqualTo(id));
            Assert.That(restored.Report.RepeatCount, Is.EqualTo(2));
            Assert.That(restored.Report.TimeoutSeconds, Is.EqualTo(45));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExactPythonSuiteRunWireReusesRunningOrCompletedOutcomeWithoutLaunchingAgain(bool completed)
        {
            var harness = new Harness(Suite("continue"));
            if (completed)
            {
                harness.Runner.Tick(1001, true);
                harness.Complete("succeeded", null);
                harness.Runner.Tick(1002, true);
                harness.Runner.Tick(1003, true);
                harness.Complete("succeeded", null);
                harness.Runner.Tick(1004, true);
            }
            // The admission queue/request survives a reload independently of the authored suite store.
            PlayScenarioSuiteState restored = JsonConvert.DeserializeObject<PlayScenarioSuiteState>(JsonConvert.SerializeObject(harness.Runner.State));
            var runner = new PlayScenarioSuiteRunner(
                restored,
                (definition, repeat, timeout, id, revision) =>
                {
                    Assert.Fail("A compatible retry must never admit a child.");
                    return null;
                },
                id => harness.Snapshot,
                id => harness.Snapshot,
                () => { },
                () => false
            );
            WithCurrentSuite(
                runner,
                completed,
                () =>
                {
                    JObject request = PythonRunWire(restored.Report);
                    var response = ManagePlayScenario.HandleCommand(request) as SuccessResponse;
                    Assert.That(response, Is.Not.Null);
                    JObject report = (JObject)response.Data;
                    Assert.That((string)report["suite_id"], Is.EqualTo(restored.Report.SuiteId));
                    Assert.That((string)report["status"], Is.EqualTo(completed ? "succeeded" : "running"));
                    Assert.That(
                        report["scenarios"].Select(child => (string)child["job_id"]),
                        Is.EqualTo(restored.Report.Scenarios.Select(child => child.JobId))
                    );
                    Assert.That(harness.Starts, Is.EqualTo(completed ? 2 : 0));
                }
            );
        }

        [TestCase("release\\candidate")]
        [TestCase("release/candidate")]
        [TestCase("boundary")]
        public void SuiteRevisionUsesTheSameLabelBoundaryAsStandaloneRuns(string label)
        {
            if (label == "boundary")
                label = new string('x', 128);
            PlayScenarioService.ValidateSourceRevision(label);
            var harness = new Harness(Suite("continue"), revision: label);
            WithCurrentSuite(
                harness.Runner,
                false,
                () =>
                {
                    var response = ManagePlayScenario.HandleCommand(PythonRunWire(harness.Runner.State.Report)) as SuccessResponse;
                    Assert.That(response, Is.Not.Null);
                    Assert.That((string)((JObject)response.Data)["source_revision"], Is.EqualTo(label));
                    Assert.That(harness.Starts, Is.Zero);
                }
            );
        }

        [TestCase("wrong_type")]
        [TestCase("null")]
        [TestCase("control")]
        [TestCase("blank")]
        [TestCase("overbound")]
        public void SuiteRevisionRejectsInvalidLabelTypesAndValues(string kind)
        {
            var harness = new Harness(Suite("continue"));
            WithCurrentSuite(
                harness.Runner,
                false,
                () =>
                {
                    JObject request = PythonRunWire(harness.Runner.State.Report);
                    switch (kind)
                    {
                        case "wrong_type":
                            request["source_revision"] = 42;
                            break;
                        case "null":
                            request["source_revision"] = JValue.CreateNull();
                            break;
                        case "control":
                            request["source_revision"] = "release\u0001candidate";
                            break;
                        case "blank":
                            request["source_revision"] = "   ";
                            break;
                        case "overbound":
                            request["source_revision"] = new string('x', 129);
                            break;
                    }
                    var response = ManagePlayScenario.HandleCommand(request) as ErrorResponse;
                    Assert.That(response, Is.Not.Null);
                    Assert.That(response.Error, Does.Contain("source_revision"));
                    Assert.That(harness.Starts, Is.Zero);
                }
            );
        }

        [TestCase("name", "other-suite")]
        [TestCase("repeat_count", "2")]
        [TestCase("timeout_seconds", "31")]
        [TestCase("source_revision", "different-label")]
        public void ReusingSuiteIdWithADifferentOriginalRequestIsRejected(string field, string replacement)
        {
            var harness = new Harness(Suite("continue"));
            WithCurrentSuite(
                harness.Runner,
                false,
                () =>
                {
                    JObject request = PythonRunWire(harness.Runner.State.Report);
                    request[field] = field == "repeat_count" || field == "timeout_seconds" ? new JValue(int.Parse(replacement)) : new JValue(replacement);
                    var response = ManagePlayScenario.HandleCommand(request) as ErrorResponse;
                    Assert.That(response, Is.Not.Null);
                    Assert.That(response.Error, Does.Contain("different run request"));
                    Assert.That(harness.Starts, Is.Zero);
                }
            );
        }

        private static JObject PythonRunWire(PlayScenarioSuiteReport report)
        {
            var request = new JObject
            {
                ["action"] = "suite_run",
                ["name"] = report.Suite.Name,
                ["suite_id"] = report.SuiteId,
                ["repeat_count"] = report.RepeatCount,
                ["timeout_seconds"] = report.TimeoutSeconds,
            };
            if (report.SourceRevision != null)
                request["source_revision"] = report.SourceRevision;
            return request;
        }

        private static void WithCurrentSuite(PlayScenarioSuiteRunner runner, bool finalized, Action test)
        {
            if (!PlayScenarioSuiteService.CanStartChild(null))
                Assert.Ignore("An active suite owns the editor.");
            Type service = typeof(PlayScenarioSuiteService);
            FieldInfo runnerField = service.GetField("_runner", BindingFlags.Static | BindingFlags.NonPublic);
            FieldInfo finalizedField = service.GetField("_finalized", BindingFlags.Static | BindingFlags.NonPublic);
            object previousRunner = runnerField.GetValue(null);
            object previousFinalized = finalizedField.GetValue(null);
            try
            {
                runnerField.SetValue(null, runner);
                finalizedField.SetValue(null, finalized);
                test();
            }
            finally
            {
                runnerField.SetValue(null, previousRunner);
                finalizedField.SetValue(null, previousFinalized);
            }
        }

        [TestCase("{'action':'suite_run','name':'suite','suite_id':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'}")]
        [TestCase("{'action':'suite_status','suite_id':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\n'}")]
        [TestCase("{'action':'suite_cancel','suite_id':null}")]
        [TestCase("{'action':'suite_reports','name':null}")]
        [TestCase("{'action':'suite_list','repeat_count':2}")]
        public void NativeSuiteActionsRejectWrongParameters(string input)
        {
            var response = (IMcpResponse)ManagePlayScenario.HandleCommand(JObject.Parse(input));
            Assert.That(response.Success, Is.False);
        }

        private static PlayScenarioDefinition Definition(string name) =>
            PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = name,
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "Menu",
                            ["action"] = "load_scene",
                            ["scene"] = "Assets/Menu.unity",
                        }
                    ),
                }
            );

        private static PlayScenarioSuiteDefinition Suite(string policy) =>
            new PlayScenarioSuiteDefinition
            {
                Name = "suite",
                Scenarios = new List<string> { "first", "second" },
                FailurePolicy = policy,
            };

        private sealed class Harness
        {
            internal PlayScenarioSuiteRunner Runner;
            internal JObject Snapshot = new JObject { ["status"] = "running" };
            internal int Starts;
            internal int Cancels;
            internal long? SynchronousCancelFinishedUnixMs;
            internal int Checkpoints;

            internal Harness(PlayScenarioSuiteDefinition suite, int repeats = 1, int timeout = 30, string revision = null, string suiteId = null)
            {
                PlayScenarioSuiteState state = PlayScenarioSuiteRunner.Create(
                    suite,
                    suite.Scenarios.Select(Definition).ToList(),
                    repeats,
                    timeout,
                    revision,
                    1000,
                    suiteId
                );
                Runner = new PlayScenarioSuiteRunner(
                    state,
                    (definition, repeats, timeout, id, revision) =>
                    {
                        Assert.That(state.Report.Scenarios[state.Cursor].Status, Is.EqualTo("running"));
                        Assert.That(Checkpoints, Is.GreaterThan(0), "Reservation must be persisted before Play entry.");
                        Starts++;
                        Snapshot = new JObject { ["status"] = "running" };
                        return Snapshot;
                    },
                    id => Snapshot,
                    id =>
                    {
                        Cancels++;
                        if (SynchronousCancelFinishedUnixMs.HasValue)
                        {
                            Complete("cancelled", "child cancelled");
                            Snapshot["finished_unix_ms"] = SynchronousCancelFinishedUnixMs.Value;
                        }
                        return Snapshot;
                    },
                    () => Checkpoints++,
                    () => false
                );
            }

            internal void Complete(string status, string error)
            {
                PlayScenarioSuiteChild child = Runner.Current;
                PlayScenarioRun run = PlayScenarioEngine.Create(Runner.State.Definitions[Runner.State.Cursor], child.JobId, 1, 30, 1000);
                run.Status = status;
                run.Phase = "finished";
                run.FinishedUnixMs = 1002;
                run.Error = error;
                run.ReportPath = "Library/MCPForUnity/PlayScenarioRuns/" + child.JobId + ".json";
                run.RunnerResourcesReleased = true;
                Snapshot = JObject.FromObject(run);
            }
        }
    }
}
