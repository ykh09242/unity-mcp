using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Tools.PlayScenarios
{
    public sealed class PlayScenarioPlayerStopTests
    {
        [TestCase(false, "timed_out")]
        [TestCase(true, "cancelled")]
        public void FirstStopObservationSurvivesLaterCauseDuringCleanup(bool cancellationFirst, string expected)
        {
            var definition = new PlayScenarioDefinition { Name = "stop-test", PollIntervalMs = 100 };
            definition.Steps.Add(
                new PlayScenarioStep
                {
                    Name = "main",
                    Action = "wait_object",
                    TimeoutSeconds = 10,
                }
            );
            definition.CleanupSteps.Add(
                new PlayScenarioStep
                {
                    Name = "cleanup",
                    Action = "wait_object",
                    TimeoutSeconds = 10,
                }
            );
            PlayScenarioRun run = PlayScenarioEngine.Create(definition, "stop-test", 1, 60, 1000);
            var host = new StopHost();
            var engine = new PlayScenarioEngine(run, host);
            string status = null;
            string error = null;
            engine.EnteredPlayMode();
            engine.Tick(1001, true);
            Assert.That(host.MainDispatches, Is.EqualTo(1));

            PlayScenarioPlayerStop.Observe(cancellationFirst, !cancellationFirst, engine, 1002, ref status, ref error);
            Assert.That(run.Phase, Is.EqualTo("cleaning"));
            engine.Tick(1003, true);
            Assert.That(host.CleanupPolls, Is.EqualTo(1));
            PlayScenarioPlayerStop.Observe(!cancellationFirst, cancellationFirst, engine, 1004, ref status, ref error);
            Assert.That(status, Is.EqualTo(expected));
            Assert.That(error, Is.EqualTo(cancellationFirst ? "Cancelled by caller." : "Player soak exceeded its whole-session wall-clock deadline."));
            engine.Tick(1103, true);
            Assert.That(engine.Running, Is.False);
            Assert.That(host.MainDispatches, Is.EqualTo(1));
            Assert.That(host.CleanupPolls, Is.EqualTo(2));
            Assert.That(host.CleanupDispatches, Is.EqualTo(1));
            Assert.That(run.Steps[1].Status, Is.EqualTo("passed"));
            engine.Release();
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [TestCase(false, true)]
        [TestCase(false, false)]
        [TestCase(true, true)]
        [TestCase(true, false)]
        public void ExistingIterationOutcomeWinsOverStopDuringCleanup(bool timeout, bool cancel)
        {
            var definition = new PlayScenarioDefinition { Name = "stop-test", PollIntervalMs = 100 };
            definition.Steps.Add(
                new PlayScenarioStep
                {
                    Name = "main",
                    Action = "wait_object",
                    TimeoutSeconds = 1,
                }
            );
            definition.CleanupSteps.Add(
                new PlayScenarioStep
                {
                    Name = "cleanup",
                    Action = "wait_object",
                    TimeoutSeconds = 10,
                }
            );
            PlayScenarioRun run = PlayScenarioEngine.Create(definition, "stop-test", 1, 60, 1000);
            var host = new StopHost { FailMain = !timeout };
            var engine = new PlayScenarioEngine(run, host);
            engine.EnteredPlayMode();
            engine.Tick(1001, true);
            if (timeout)
                engine.Tick(2001, true);
            Assert.That(run.Phase, Is.EqualTo("cleaning"));
            string originalStatus = run.PendingStatus;
            string originalError = run.PendingError;
            Assert.That(originalStatus, Is.EqualTo(timeout ? "timed_out" : "failed"));
            string status = null;
            string error = null;
            PlayScenarioPlayerStop.Observe(cancel, !cancel, engine, 2002, ref status, ref error);
            engine.Tick(2003, true);
            engine.Tick(2103, true);
            Assert.That(run.Status, Is.EqualTo(originalStatus));
            Assert.That(status, Is.EqualTo(cancel ? "cancelled" : "timed_out"));
            PlayScenarioPlayerStop.CompleteIteration(run, false, true, ref status, ref error);
            Assert.That(status, Is.EqualTo(originalStatus));
            Assert.That(error, Is.EqualTo(originalError));
            Assert.That(host.MainDispatches, Is.EqualTo(1));
            Assert.That(host.CleanupDispatches, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [TestCase("stop", false, true, "failed", false, false)]
        [TestCase("continue", false, true, "timed_out", false, false)]
        [TestCase("continue", false, false, "cancelled", false, false)]
        [TestCase("stop", true, true, "timed_out", false, false)]
        [TestCase("stop", true, true, "succeeded", true, false)]
        [TestCase("continue", false, false, "cancelled", false, true)]
        [TestCase("stop", false, false, "failed", false, true)]
        public void BoundaryUsesActualJournalContinuationPolicy(
            string policy,
            bool success,
            bool deadline,
            string expected,
            bool lastIteration,
            bool stopDuringCleanup
        )
        {
            string directory = Path.Combine(Path.GetTempPath(), "mcp-player-stop-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(directory);
            try
            {
                var definition = new PlayScenarioDefinition { Name = "stop-test", CompletionStableMs = 0 };
                definition.Steps.Add(
                    new PlayScenarioStep
                    {
                        Name = "main",
                        Action = "load_scene",
                        Scene = "Assets/Test.unity",
                        TimeoutSeconds = 10,
                    }
                );
                if (stopDuringCleanup)
                    definition.CleanupSteps.Add(
                        new PlayScenarioStep
                        {
                            Name = "cleanup",
                            Action = "wait_object",
                            Target = "cleanup",
                            TimeoutSeconds = 10,
                        }
                    );
                var bundle = PlayScenarioPlayerBundle.Parse(PlayScenarioPlayerBundle.Create(definition, "test", "test", new string('b', 32)).ToString());
                var json = new JObject
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
                        ["iterations"] = 2,
                        ["max_runtime_seconds"] = 60,
                        ["failure_policy"] = policy,
                        ["retain_reports"] = 1,
                    },
                };
                var request = PlayScenarioPlayerRequest.Parse(json.ToString());
                var host = new BoundaryHost { Fail = !success };
                PlayScenarioRun run = PlayScenarioEngine.Create(bundle.Definition, request.JobId, 1, 60, 1000);
                var engine = new PlayScenarioEngine(run, host);
                engine.EnteredPlayMode();
                engine.Tick(1001, true);
                string status = null;
                string error = null;
                if (stopDuringCleanup)
                {
                    Assert.That(run.Phase, Is.EqualTo("cleaning"));
                    PlayScenarioPlayerStop.Observe(true, false, engine, 1002, ref status, ref error);
                }
                if (engine.Running)
                    engine.Tick(1003, true);
                Assert.That(engine.Running, Is.False);
                using (var journal = new PlayScenarioPlayerSoakJournal(directory, request, bundle, 123, 1000))
                {
                    if (lastIteration)
                    {
                        Assert.That(journal.Append(run, JObject.FromObject(run)), Is.True);
                        run = PlayScenarioEngine.Create(bundle.Definition, request.JobId, 1, 60, 2000);
                        engine = new PlayScenarioEngine(run, host);
                        engine.EnteredPlayMode();
                        engine.Tick(2001, true);
                        engine.Tick(2002, true);
                    }
                    bool continuation = journal.Append(run, JObject.FromObject(run));
                    if (!deadline && !stopDuringCleanup)
                        PlayScenarioPlayerStop.Observe(true, false, engine, 1003, ref status, ref error);
                    PlayScenarioPlayerStop.CompleteIteration(run, continuation, deadline, ref status, ref error);
                    JObject summary = journal.Finish(status, error, true, 1004);
                    Assert.That((string)summary["status"], Is.EqualTo(expected));
                    Assert.That((int)summary["attempted_iterations"], Is.EqualTo(lastIteration ? 2 : 1));
                }
                Assert.That(host.Dispatches, Is.EqualTo(lastIteration ? 2 : 1));
                Assert.That(host.CleanupDispatches, Is.EqualTo(stopDuringCleanup ? 1 : 0));
            }
            finally
            {
                Directory.Delete(directory, true);
            }
        }

        private sealed class BoundaryHost : IPlayScenarioHost
        {
            public bool Fail;
            public int Dispatches;
            public int CleanupDispatches;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
            {
                if (step.Name == "cleanup")
                {
                    CleanupDispatches++;
                    return new PlayScenarioObservation(true, "cleaned");
                }
                Dispatches++;
                if (Fail)
                    throw new InvalidOperationException("main failed");
                return new PlayScenarioObservation(true, "ready");
            }

            public void Release() { }
        }

        private sealed class StopHost : IPlayScenarioHost
        {
            public bool FailMain;
            public int MainDispatches;
            public int CleanupPolls;
            public int CleanupDispatches;
            public int Releases;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool firstPoll)
            {
                if (step.Name == "cleanup")
                {
                    if (firstPoll)
                        CleanupDispatches++;
                    return new PlayScenarioObservation(++CleanupPolls > 1, "cleanup");
                }
                MainDispatches++;
                if (FailMain)
                    throw new InvalidOperationException("main failed");
                return new PlayScenarioObservation(false, "waiting");
            }

            public void Release() => Releases++;
        }
    }
}
