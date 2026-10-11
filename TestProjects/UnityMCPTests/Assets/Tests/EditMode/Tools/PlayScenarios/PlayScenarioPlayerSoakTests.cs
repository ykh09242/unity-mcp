using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public sealed class PlayScenarioPlayerSoakTests
    {
        private string directory;
        private PlayScenarioPlayerBundle bundle;
        private JObject requestJson;
        private const long Start = 1000;

        [SetUp]
        public void SetUp()
        {
            directory = Path.Combine(Path.GetTempPath(), "mcp-player-soak-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            var definition = new PlayScenarioDefinition
            {
                Name = "soak-test",
                CompletionStableMs = 0,
                Metrics = new PlayScenarioMetricsOptions { Enabled = true, WarmupIterations = 1 },
            };
            definition.Steps.Add(
                new PlayScenarioStep
                {
                    Name = "load",
                    Action = "load_scene",
                    Scene = "Assets/Test.unity",
                    TimeoutSeconds = 1,
                }
            );
            bundle = PlayScenarioPlayerBundle.Parse(PlayScenarioPlayerBundle.Create(definition, "test", "test", new string('b', 32)).ToString());
            requestJson = new JObject
            {
                ["schema_version"] = 2,
                ["job_id"] = new string('a', 32),
                ["scenario_name"] = bundle.Definition.Name,
                ["definition_hash"] = bundle.DefinitionHash,
                ["build_id"] = bundle.BuildId,
                ["build_source_revision"] = null,
                ["payload_hash"] = new string('c', 64),
                ["source_revision"] = null,
                ["repeat_count"] = 1,
                ["timeout_seconds"] = 60,
                ["soak"] = new JObject
                {
                    ["iterations"] = 1000,
                    ["max_runtime_seconds"] = 86400,
                    ["failure_policy"] = "continue",
                    ["retain_reports"] = 1,
                },
            };
        }

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(directory))
                Directory.Delete(directory, true);
        }

        [TestCase("iterations", 1)]
        [TestCase("iterations", 1001)]
        [TestCase("iterations", 2.5)]
        [TestCase("max_runtime_seconds", 0)]
        [TestCase("max_runtime_seconds", 86401)]
        [TestCase("retain_reports", 0)]
        [TestCase("retain_reports", 65)]
        public void RequestRejectsInvalidSoakBounds(string field, double value)
        {
            requestJson["soak"][field] = value;
            Assert.Throws<ArgumentException>(() => Request());
        }

        [Test]
        public void RequestRequiresVersionTwoAndOneRepeatWhileNormalRequestsRemainCompatible()
        {
            requestJson["repeat_count"] = 2;
            Assert.Throws<ArgumentException>(() => Request());
            requestJson.Remove("soak");
            Assert.That(Request().RepeatCount, Is.EqualTo(2));
            requestJson["schema_version"] = 1;
            requestJson.Remove("build_id");
            requestJson.Remove("build_source_revision");
            requestJson.Remove("payload_hash");
            requestJson["soak"] = new JObject();
            Assert.Throws<ArgumentException>(() => Request());
        }

        [TestCase(null)]
        [TestCase("fixture-build-revision")]
        public void NullableBuildRevisionSurvivesPersistedJournalRoundTrip(string revision)
        {
            bundle = PlayScenarioPlayerBundle.Parse(
                PlayScenarioPlayerBundle.Create(bundle.Definition, "test", "test", bundle.BuildId, revision).ToString(Formatting.None)
            );
            requestJson["build_source_revision"] = revision == null ? JValue.CreateNull() : new JValue(revision);
            requestJson["soak"]["iterations"] = 2;
            var journal = Journal();
            for (int iteration = 1; iteration <= 2; iteration++)
            {
                PlayScenarioRun run = Execute(iteration, new Host());
                Assert.That(journal.Append(run, Report(run)), Is.EqualTo(iteration < 2));
            }
            JObject summary = journal.Finish(null, null, true, Start + 3000);
            JObject persisted = JObject.Parse(File.ReadAllText(Path.Combine(directory, "soak.json")));
            Assert.That((string)summary["status"], Is.EqualTo("succeeded"));
            Assert.That((string)persisted["build_source_revision"], Is.EqualTo(revision));
            Assert.That(persisted["build_source_revision"].Type, Is.EqualTo(revision == null ? JTokenType.Null : JTokenType.String));
            Assert.That(JToken.DeepEquals(summary["build_source_revision"], persisted["build_source_revision"]), Is.True);
            foreach (string line in File.ReadLines(Path.Combine(directory, "iterations.jsonl")))
            {
                JObject row = JObject.Parse(line);
                Assert.That(JToken.DeepEquals(persisted["build_source_revision"], row["build_source_revision"]), Is.True);
            }
        }

        [Test]
        public void ThousandFreshEnginesRetainOneReportTenSamplesAndOneLocalLedgerPerRun()
        {
            PlayScenarioPlayerRequest request = Request();
            var journal = new PlayScenarioPlayerSoakJournal(directory, request, bundle, 123, Start);
            int releases = 0;
            for (int iteration = 1; iteration <= 1000; iteration++)
            {
                PlayScenarioRun run = Execute(iteration, new Host { OnRelease = () => releases++, MetricValue = iteration * 2000000L });
                Assert.That(run.Steps.Count, Is.EqualTo(1));
                Assert.That(run.IterationResults.Count, Is.EqualTo(1));
                Assert.That(run.IterationResults[0].Iteration, Is.EqualTo(1));
                Assert.That(journal.Append(run, Report(run)), Is.EqualTo(iteration < 1000));
                Assert.That(JObject.Parse(File.ReadAllText(Path.Combine(directory, "soak.json")))["journal_sha256"].Type, Is.EqualTo(JTokenType.Null));
                Assert.That(Directory.GetFiles(directory, "iteration-*.json").Length, Is.EqualTo(1));
            }
            JObject summary = journal.Finish(null, null, true, Start + 1000100);
            Assert.That((string)summary["status"], Is.EqualTo("succeeded"));
            Assert.That((int)summary["attempted_iterations"], Is.EqualTo(1000));
            Assert.That((int)summary["passed_iterations"], Is.EqualTo(1000));
            Assert.That((int)summary["skipped_iterations"], Is.Zero);
            Assert.That((int)summary["journal_rows"], Is.EqualTo(1000));
            Assert.That((long)summary["journal_bytes"], Is.LessThan(PlayScenarioPlayerSoakJournal.JournalLimit));
            Assert.That(summary["metrics_summary"]["bounded_samples"].Count(), Is.EqualTo(10));
            Assert.That((int)summary["metrics_summary"]["usable_samples"], Is.EqualTo(999));
            Assert.That(summary["metrics_summary"]["warnings"].Count(), Is.EqualTo(1));
            Assert.That(releases, Is.EqualTo(1000));
            Assert.That(
                (string)summary["journal_sha256"],
                Is.EqualTo(PlayScenarioPlayerPayload.HashText(File.ReadAllText(Path.Combine(directory, "iterations.jsonl"))))
            );
            Assert.That(journal.SelectedReport()["iteration_results"][0]["iteration"].Value<int>(), Is.EqualTo(1));
        }

        [TestCase("stop", 1)]
        [TestCase("continue", 3)]
        public void FirstActualFailureSurvivesLaterSuccessAndReportPruning(string policy, int expectedRows)
        {
            requestJson["soak"]["iterations"] = 3;
            requestJson["soak"]["failure_policy"] = policy;
            var journal = Journal();
            for (int iteration = 1; iteration <= 3; iteration++)
            {
                PlayScenarioRun run = Execute(iteration, new Host { Fail = iteration == 1 });
                if (!journal.Append(run, Report(run)))
                    break;
            }
            JObject summary = journal.Finish(null, null, true, Start + 4000);
            Assert.That((string)summary["status"], Is.EqualTo("failed"));
            Assert.That((int)summary["attempted_iterations"], Is.EqualTo(expectedRows));
            Assert.That((int)summary["first_failure_iteration"], Is.EqualTo(1));
            Assert.That((int)summary["selected_iteration"], Is.EqualTo(1));
            Assert.That((string)journal.SelectedReport()["failure"]["code"], Is.EqualTo("action_exception"));
            Assert.That((string)journal.SelectedReport()["status"], Is.EqualTo("failed"));
            Assert.That(Directory.GetFiles(directory, "iteration-*.json").Length, Is.EqualTo(1));
        }

        [Test]
        public void CancellationBeforeFirstEvaluationRecordsZeroAttemptsAndAllSkipped()
        {
            var journal = Journal();
            PlayScenarioRun run = NewRun(Start + 10);
            var engine = new PlayScenarioEngine(run, new Host());
            engine.Cancel(Start + 11);
            Assert.That(journal.Append(run, Report(run)), Is.False);
            JObject summary = journal.Finish("cancelled", "test cancellation", true, Start + 12);
            Assert.That((int)summary["attempted_iterations"], Is.Zero);
            Assert.That((int)summary["recorded_iterations"], Is.EqualTo(1));
            Assert.That((int)summary["skipped_iterations"], Is.EqualTo(1000));
            Assert.That(summary["selected_iteration"].Type, Is.EqualTo(JTokenType.Null));
            JObject row = JObject.Parse(File.ReadAllLines(Path.Combine(directory, "iterations.jsonl"))[0]);
            Assert.That(row["started_unix_ms"].Type, Is.EqualTo(JTokenType.Null));
            Assert.That((long?)row["finished_unix_ms"], Is.EqualTo(run.IterationResults[0].FinishedUnixMs));
        }

        [TestCase("cancelled")]
        [TestCase("timed_out")]
        public void UnstartedSecondEngineKeepsLastActualSelectedReportWithinRetentionBound(string stopStatus)
        {
            requestJson["soak"]["iterations"] = 2;
            requestJson["soak"]["max_runtime_seconds"] = 4;
            var journal = Journal();
            PlayScenarioRun first = Execute(1, new Host());
            Assert.That(journal.Append(first, Report(first)), Is.True);
            string original = File.ReadAllText(Path.Combine(directory, "iteration-0001.json"));

            PlayScenarioRun second = NewRun(Start + 3900);
            var engine = new PlayScenarioEngine(second, new Host());
            engine.EnteredPlayMode();
            long stopAt = stopStatus == "timed_out" ? journal.DeadlineUnixMs : Start + 3901;
            if (stopStatus == "timed_out")
                Assert.That(journal.DeadlineReached(stopAt), Is.True);
            engine.Cancel(stopAt);
            Assert.That(second.IterationResults[0].StartedUnixMs, Is.Null);
            Assert.That(journal.Append(second, Report(second)), Is.False);
            JObject summary = journal.Finish(stopStatus, "stopped before second evaluation", true, stopAt + 1);

            Assert.That((int)summary["recorded_iterations"], Is.EqualTo(2));
            Assert.That((int)summary["attempted_iterations"], Is.EqualTo(1));
            Assert.That((int)summary["passed_iterations"], Is.EqualTo(1));
            Assert.That((int)summary["skipped_iterations"], Is.EqualTo(1));
            Assert.That((int)summary["selected_iteration"], Is.EqualTo(1));
            Assert.That((string)summary["status"], Is.EqualTo(stopStatus));
            Assert.That(summary["retained_report_files"].Values<string>(), Is.EqualTo(new[] { "iteration-0001.json" }));
            Assert.That(Directory.GetFiles(directory, "iteration-*.json").Length, Is.EqualTo(1));
            Assert.That(File.Exists(Path.Combine(directory, "iteration-0002.json")), Is.False);
            Assert.That(File.ReadAllText(Path.Combine(directory, "iteration-0001.json")), Is.EqualTo(original));
            Assert.That((string)journal.SelectedReport()["status"], Is.EqualTo("succeeded"));
            Assert.That((long)journal.SelectedReport()["iteration_results"][0]["started_unix_ms"], Is.EqualTo(first.IterationResults[0].StartedUnixMs));
        }

        [Test]
        public void WholeDeadlineIsIndependentAndDoesNotEraseSuccessfulLocalIteration()
        {
            requestJson["soak"]["max_runtime_seconds"] = 1;
            var journal = Journal();
            PlayScenarioRun run = NewRun(Start + 100);
            var engine = new PlayScenarioEngine(run, new Host());
            engine.EnteredPlayMode();
            engine.Tick(Start + 101, true);
            engine.Tick(Start + 102, true);
            Assert.That(engine.Running, Is.False);
            Assert.That(journal.Append(run, Report(run)), Is.True);
            Assert.That(journal.DeadlineReached(Start + 999), Is.False);
            Assert.That(journal.DeadlineReached(Start + 1000), Is.True);
            JObject summary = journal.Finish("timed_out", "whole deadline", true, Start + 1000);
            Assert.That((string)summary["status"], Is.EqualTo("timed_out"));
            Assert.That((string)journal.SelectedReport()["status"], Is.EqualTo("succeeded"));
        }

        [TestCase(false, "cancelled")]
        [TestCase(true, "timed_out")]
        public void StartedCancellationAndIterationTimeoutKeepActualLedgerTimes(bool timeout, string expectedStatus)
        {
            var journal = Journal();
            PlayScenarioRun run = NewRun(Start + 100);
            var engine = new PlayScenarioEngine(run, new Host { Pending = true });
            engine.EnteredPlayMode();
            engine.Tick(Start + 101, true);
            if (timeout)
                engine.Tick(Start + 1101, true);
            else
                engine.Cancel(Start + 102);
            Assert.That(run.Status, Is.EqualTo(expectedStatus));
            Assert.That(journal.Append(run, Report(run)), Is.False);
            JObject summary = journal.Finish(expectedStatus, "test outcome", true, Start + 1200);
            Assert.That((int)summary["attempted_iterations"], Is.EqualTo(1));
            Assert.That((int)summary["failed_iterations"], Is.EqualTo(1));
            JObject row = JObject.Parse(File.ReadAllLines(Path.Combine(directory, "iterations.jsonl"))[0]);
            Assert.That((long)row["started_unix_ms"], Is.EqualTo(Start + 101));
            Assert.That((long)row["finished_unix_ms"], Is.GreaterThan(Start + 101));
        }

        [Test]
        public void MissingMetricsBreakGlobalGrowthIntervalsWithoutFabricatingMeasurements()
        {
            var metrics = new PlayScenarioPlayerSoakMetrics(
                new PlayScenarioMetricsOptions
                {
                    Enabled = true,
                    WarmupIterations = 1,
                    ConsecutiveIncreases = 2,
                }
            );
            for (int iteration = 1; iteration <= 5; iteration++)
            {
                var run = new PlayScenarioRun();
                if (iteration != 3)
                    run.Metrics.Add(new PlayScenarioMetricsSnapshot { ManagedBytes = iteration * 2000000L });
                metrics.Observe(metrics.Boundary(run, iteration), iteration);
            }
            JObject summary = metrics.Snapshot();
            Assert.That((int)summary["sample_count"], Is.EqualTo(4));
            Assert.That((int)summary["usable_samples"], Is.EqualTo(3));
            Assert.That(summary["warnings"].Count(), Is.Zero);
            Assert.That(summary["bounded_samples"][0]["allocated_bytes"].Type, Is.EqualTo(JTokenType.Null));
        }

        [Test]
        public void LockedJournalWriteNeverCountsAnUndurableAttempt()
        {
            using (PlayScenarioPlayerSoakJournal journal = Journal())
            using (var locked = new FileStream(Path.Combine(directory, "iterations.jsonl"), FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                PlayScenarioRun run = Execute(1, new Host());
                Assert.Throws<IOException>(() => journal.Append(run, Report(run)));
                Assert.That(journal.Attempted, Is.Zero);
                Assert.That(locked.Length, Is.Zero);
            }
        }

        [Test]
        public void OversizedCompactRowNeverWritesItsFullReportOrJournal()
        {
            using (PlayScenarioPlayerSoakJournal journal = Journal())
            {
                PlayScenarioRun run = Execute(1, new Host());
                run.Failure = new PlayScenarioFailure
                {
                    Code = new string('x', PlayScenarioPlayerSoakJournal.RowLimit),
                    Message = "injected oversized evidence",
                };
                Assert.Throws<IOException>(() => journal.Append(run, Report(run)));
                Assert.That(journal.Attempted, Is.Zero);
                Assert.That(Directory.GetFiles(directory, "iteration-*.json").Length, Is.Zero);
                Assert.That(new FileInfo(Path.Combine(directory, "iterations.jsonl")).Length, Is.Zero);
            }
        }

        [Test]
        public void ReleaseFailureStopsContinuePolicyWithoutRewritingNativeSuccess()
        {
            var journal = Journal();
            PlayScenarioRun run = Execute(1, new Host { ReleaseFails = true });
            Assert.That(run.Status, Is.EqualTo("succeeded"));
            Assert.That(journal.Append(run, Report(run)), Is.False);
            JObject summary = journal.Finish(null, null, false, Start + 2000);
            Assert.That((string)summary["status"], Is.EqualTo("failed"));
            Assert.That((int)summary["passed_iterations"], Is.Zero);
            Assert.That((bool)summary["runner_resources_released"], Is.False);
            Assert.That((string)journal.SelectedReport()["status"], Is.EqualTo("succeeded"));
        }

        [Test]
        public void JournalTamperingCannotFinalizeSuccess()
        {
            var journal = Journal();
            PlayScenarioRun run = Execute(1, new Host());
            journal.Append(run, Report(run));
            string path = Path.Combine(directory, "iterations.jsonl");
            string original = File.ReadAllText(path);
            File.WriteAllText(path, original.Replace("\"process_id\":123", "\"process_id\":124"), new System.Text.UTF8Encoding(false));
            Assert.Throws<IOException>(() => journal.Finish(null, null, true, Start + 2000));
            Assert.That((string)JObject.Parse(File.ReadAllText(Path.Combine(directory, "soak.json")))["finalization_state"], Is.EqualTo("running"));
        }

        [Test]
        public void ExistingCheckpointTemporaryFileFailsAfterDurableRowWithoutDoubleCounting()
        {
            var journal = Journal();
            File.WriteAllText(Path.Combine(directory, "soak.json.tmp"), "stale");
            PlayScenarioRun run = Execute(1, new Host());
            Assert.Throws<IOException>(() => journal.Append(run, Report(run)));
            Assert.That(journal.Attempted, Is.EqualTo(1));
            Assert.That(journal.CurrentIteration, Is.EqualTo(2));
            Assert.That(File.ReadAllLines(Path.Combine(directory, "iterations.jsonl")).Length, Is.EqualTo(1));
            Assert.Throws<IOException>(() => journal.Finish("failed", "persistence failed", true, Start + 2000));
        }

        private PlayScenarioPlayerRequest Request() => PlayScenarioPlayerRequest.Parse(requestJson.ToString(Formatting.None));

        private PlayScenarioPlayerSoakJournal Journal() => new PlayScenarioPlayerSoakJournal(directory, Request(), bundle, 123, Start);

        private PlayScenarioRun NewRun(long now) => PlayScenarioEngine.Create(bundle.Definition, Request().JobId, 1, 60, now);

        private PlayScenarioRun Execute(int iteration, Host host)
        {
            PlayScenarioRun run = NewRun(Start + iteration * 1000L);
            var engine = new PlayScenarioEngine(run, host);
            engine.EnteredPlayMode();
            engine.Tick(run.StartedUnixMs + 1, true);
            if (engine.Running)
                engine.Tick(run.StartedUnixMs + 2, true);
            Assert.That(engine.Running, Is.False);
            return run;
        }

        private JObject Report(PlayScenarioRun run)
        {
            JObject report = JObject.FromObject(run);
            Request().AddReproduction(report, bundle);
            report["finalization_state"] = "completed";
            report["exit_code"] = PlayScenarioPlayerLogDrain.ExitCode(run);
            report["timeout_seconds"] = 60;
            return report;
        }

        private sealed class Host : IPlayScenarioHost, IPlayScenarioMetricsHost
        {
            public bool Fail;
            public bool ReleaseFails;
            public bool Pending;
            public long MetricValue;
            public Action OnRelease;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll) =>
                Fail ? throw new InvalidOperationException("Injected first iteration failure.") : new PlayScenarioObservation(!Pending, "ready");

            public PlayScenarioMetricsSnapshot CaptureMetrics(int iteration, long now) => new PlayScenarioMetricsSnapshot { ManagedBytes = MetricValue };

            public void Release()
            {
                OnRelease?.Invoke();
                if (ReleaseFails)
                    throw new IOException("Injected release failure.");
            }
        }
    }
}
