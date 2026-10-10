using System;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public class PlayScenarioEngineTests
    {
        private sealed class Host : IPlayScenarioHost
        {
            public int Calls;
            public int Loads;
            public int Clicks;
            public int Releases;
            public Func<PlayScenarioStep, bool, PlayScenarioObservation> EvaluateStep;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                Calls++;
                if (step.Action == "load_scene" && first)
                    Loads++;
                if (step.Action == "click_ui")
                    Clicks++;
                return EvaluateStep?.Invoke(step, first) ?? new PlayScenarioObservation(true, "ready");
            }

            public void Release() => Releases++;
        }

        private static PlayScenarioDefinition Definition() =>
            PlayScenarioDefinition.Parse(
                JObject.Parse(
                    @"{
            'name':'menu-start', 'poll_interval_ms':250,
            'steps':[
                {'name':'Menu','action':'load_scene','scene':'Assets/Scenes/Menu.unity','timeout_seconds':2},
                {'name':'Start','action':'click_ui','target':'Canvas/Start','timeout_seconds':2},
                {'name':'Game','action':'wait_scene','scene':'Assets/Scenes/Game.unity','timeout_seconds':2},
                {'name':'Player','action':'wait_object','target':'Player','timeout_seconds':2}
            ]}"
                )
            );

        private static PlayScenarioEngine Engine(Host host, int repeats = 1, int timeout = 30)
        {
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('a', 32), repeats, timeout, 1000), host);
            engine.EnteredPlayMode();
            return engine;
        }

        [Test]
        public void RepeatsResetTheMenuAndClickOncePerIteration()
        {
            var host = new Host { EvaluateStep = (step, first) => new PlayScenarioObservation(step.Action != "load_scene" || !first, "observed") };
            var engine = Engine(host, 3);
            for (long now = 1000; now < 10000 && engine.Running; now += 250)
                engine.Tick(now, true);

            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(host.Loads, Is.EqualTo(3));
            Assert.That(host.Clicks, Is.EqualTo(3));
            Assert.That(engine.State.Steps.Count, Is.EqualTo(12));
            Assert.That(engine.State.Steps.All(step => step.Status == "passed"), Is.True);
            Assert.That(engine.State.Steps.Select(step => step.Iteration).Distinct(), Is.EqualTo(new[] { 1, 2, 3 }));
            engine.Tick(20000, true);
            engine.Cancel(20000);
            engine.Release();
            Assert.That(host.Calls, Is.EqualTo(15));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void ConditionWaitDoesNotQueryAgainBeforeItsInterval()
        {
            var host = new Host { EvaluateStep = (_, __) => new PlayScenarioObservation(false, "waiting for menu") };
            var engine = Engine(host);
            engine.Tick(1000, true);
            for (long now = 1001; now < 1250; now++)
                engine.Tick(now, true);
            Assert.That(host.Calls, Is.EqualTo(1));
            engine.Tick(1250, true);
            Assert.That(host.Calls, Is.EqualTo(2));
            Assert.That(host.Loads, Is.EqualTo(1));
            Assert.That(engine.State.Steps[0].PollCount, Is.EqualTo(2));
        }

        [Test]
        public void StepTimeoutPreservesObservationAndSkipsRemainingSteps()
        {
            var host = new Host { EvaluateStep = (_, __) => new PlayScenarioObservation(false, "player is not present") };
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(3000, true);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Error, Does.Contain("player is not present"));
            Assert.That(engine.State.Steps[0].Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Steps.Skip(1).All(step => step.Status == "skipped"), Is.True);
            Assert.That(host.Calls, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void PausedOrUnreadyEditorMakesNoQueriesButStillTimesOut()
        {
            var host = new Host();
            var engine = Engine(host, timeout: 1);
            engine.Tick(1000, false);
            engine.Tick(2000, false);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(host.Calls, Is.Zero);
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void FailureAfterDispatchNeverRetriesClick()
        {
            var host = new Host
            {
                EvaluateStep = (step, _) =>
                    step.Action == "click_ui"
                        ? throw new InvalidOperationException("listener failed after dispatch")
                        : new PlayScenarioObservation(true, "loaded"),
            };
            var engine = Engine(host);
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            for (int i = 0; i < 10; i++)
                engine.Tick(1500 + i * 250, true);
            Assert.That(host.Clicks, Is.EqualTo(1));
            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Error, Does.Contain("listener failed after dispatch"));
            Assert.That(engine.State.Steps[1].Status, Is.EqualTo("failed"));
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void CancellationFromClickListenerCannotBeOverwrittenBySuccessfulDispatch()
        {
            var host = new Host();
            var engine = Engine(host);
            host.EvaluateStep = (step, _) =>
            {
                if (step.Action == "click_ui")
                    engine.Cancel(1250);
                return new PlayScenarioObservation(true, "callback returned");
            };
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
            Assert.That(engine.State.Steps[1].Status, Is.EqualTo("cancelled"));
            Assert.That(engine.State.Cursor, Is.EqualTo(1));
            Assert.That(host.Clicks, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
            Assert.That(engine.State.Steps[1].Detail, Does.Contain("Cancelled"));
        }

        [Test]
        public void CancelBeforePlayEntryRemovesAllPendingWork()
        {
            var host = new Host();
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('b', 32), 1, 30, 1000), host);
            engine.Cancel(1100);
            engine.EnteredPlayMode();
            engine.Tick(1250, true);
            Assert.That(host.Calls, Is.Zero);
            Assert.That(engine.State.Status, Is.EqualTo("cancelled"));
            Assert.That(engine.State.Steps.All(step => step.Status == "skipped"), Is.True);
            Assert.That(host.Releases, Is.EqualTo(1));
        }

        [Test]
        public void StartupCheckpointCanResumeWithoutKeepingRuntimeReferences()
        {
            PlayScenarioRun run = PlayScenarioEngine.Create(Definition(), new string('c', 32), 1, 30, 1000);
            run.PlayRequested = true;
            var restored = JsonConvert.DeserializeObject<PlayScenarioRun>(JsonConvert.SerializeObject(run));
            var host = new Host();
            var engine = new PlayScenarioEngine(restored, host);
            engine.Tick(1100, true);
            Assert.That(host.Calls, Is.Zero);
            engine.EnteredPlayMode();
            engine.Tick(1250, true);
            Assert.That(host.Loads, Is.EqualTo(1));
            Assert.That(engine.State.PlayRequested, Is.True);
        }

        [Test]
        public void StartingJobAlsoHasAWallClockDeadline()
        {
            var host = new Host();
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('d', 32), 1, 1, 1000), host);
            engine.Tick(2000, false);
            Assert.That(engine.State.Status, Is.EqualTo("timed_out"));
            Assert.That(host.Calls, Is.Zero);
        }

        [Test]
        public void LogBufferBoundsMessagesAndRejectsLateCallbacksAfterClose()
        {
            var buffer = new PlayScenarioLogBuffer();
            var run = new PlayScenarioRun();
            for (int i = 0; i < 80; i++)
                buffer.Add(i, "Error", new string('m', 2000), new string('s', 3000));
            buffer.DrainTo(run);
            Assert.That(run.Logs.Count, Is.EqualTo(50));
            Assert.That(run.Logs[0].TimestampUnixMs, Is.EqualTo(30));
            Assert.That(run.DroppedLogCount, Is.EqualTo(30));
            Assert.That(run.Logs[0].Message.Length, Is.EqualTo(1024));
            Assert.That(run.Logs[0].StackTrace.Length, Is.EqualTo(2048));
            buffer.Add(80, "Warning", "last", "");
            buffer.Close();
            buffer.Add(81, "Error", "late", "");
            buffer.DrainTo(run);
            Assert.That(run.Logs.Last().Message, Is.EqualTo("last"));
            Assert.That(run.DroppedLogCount, Is.EqualTo(31));
        }
    }
}
