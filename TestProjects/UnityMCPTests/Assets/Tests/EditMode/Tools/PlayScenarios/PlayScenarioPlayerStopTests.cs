using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
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

        private sealed class StopHost : IPlayScenarioHost
        {
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
                return new PlayScenarioObservation(false, "waiting");
            }

            public void Release() => Releases++;
        }
    }
}
