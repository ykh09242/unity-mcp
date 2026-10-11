using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioInputTests
    {
        private sealed class Backend : IScenarioInputSimulationBackend
        {
            public bool Available { get; set; } = true;
            public string UnavailableReason => "Unavailable test backend.";
            public object Status => null;
            public long UpdateCount { get; set; }
            public bool LeaseActive { get; set; } = true;
            public int Applies;
            public int Releases;
            public bool ThrowOnApply;
            public Action DuringApply;
            public Action DuringRelease;

            public object Apply(InputSimulationRequest request)
            {
                Applies++;
                LeaseActive = true;
                DuringApply?.Invoke();
                if (ThrowOnApply)
                    throw new InvalidOperationException("Device dispatch failed.");
                return null;
            }

            public void ReleaseAll()
            {
                Releases++;
                LeaseActive = false;
                DuringRelease?.Invoke();
            }
        }

        private sealed class Host : IPlayScenarioHost, IPlayScenarioInputHost
        {
            private readonly PlayScenarioInputSession session = new PlayScenarioInputSession();
            public bool WaitForObject;
            public int Releases;
            public int CleanupEvaluations;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first)
            {
                if (step.Name == "Cleanup")
                    CleanupEvaluations++;
                return step.Action == "input"
                    ? session.Evaluate(step, first)
                    : new PlayScenarioObservation(!WaitForObject || step.Action != "wait_object", "ready");
            }

            public void MaintainInput() => session.Maintain();

            public void ReleaseInput() => session.Release();

            public void Release()
            {
                Releases++;
                ReleaseInput();
            }
        }

        private Func<IScenarioInputSimulationBackend> previousFactory;
        private Backend backend;

        [SetUp]
        public void SetUp()
        {
            previousFactory = PlayScenarioPlayerInput.CreateInputBackend;
            backend = new Backend();
            PlayScenarioPlayerInput.CreateInputBackend = () => backend;
        }

        [TearDown]
        public void TearDown() => PlayScenarioPlayerInput.CreateInputBackend = previousFactory;

        private static PlayScenarioDefinition Definition() =>
            PlayScenarioDefinition.Parse(
                JObject.Parse(
                    @"{
            'name':'input-run', 'completion_stable_ms':0,
            'steps':[
                {'name':'Load','action':'load_scene','scene':'Assets/Test.unity'},
                {'name':'Press','action':'input','input':{'action':'key','key':'Space','frames':5},'wait_frames':2}
            ]}"
                )
            );

        [Test]
        public void EditorAndPlayerWaitForActualUpdatesWithoutReplayingDispatch()
        {
            var editor = new UnityPlayScenarioHost();
            var player = new PlayScenarioPlayerHost(new string[0]);
            var step = Definition().Steps[1];
            Assert.That(editor.Evaluate(step, true).Ready, Is.False);
            Assert.That(player.Evaluate(step, true).Ready, Is.False);
            backend.UpdateCount = 1;
            Assert.That(editor.Evaluate(step, false).Ready, Is.False);
            backend.UpdateCount = 2;
            Assert.That(editor.Evaluate(step, false).Ready, Is.True);
            Assert.That(player.Evaluate(step, false).Ready, Is.True);
            Assert.That(backend.Applies, Is.EqualTo(2));
            editor.Release();
            player.Release();
            Assert.That(backend.Releases, Is.EqualTo(2));
        }

        [Test]
        public void UnavailableBackendFailsExplicitlyAndReleasesOwnedDevices()
        {
            backend.Available = false;
            var session = new PlayScenarioInputSession();
            var exception = Assert.Throws<PlayScenarioException>(() => session.Evaluate(Definition().Steps[1], true));
            Assert.That(exception.Failure.Code, Is.EqualTo("capability_unavailable"));
            Assert.That(backend.Applies, Is.Zero);
            Assert.That(backend.Releases, Is.EqualTo(1));
        }

        [Test]
        public void ExpiredLifetimeFailsWithoutRedispatchingTheInput()
        {
            var session = new PlayScenarioInputSession();
            Assert.That(session.Evaluate(Definition().Steps[1], true).Ready, Is.False);
            backend.LeaseActive = false;
            var exception = Assert.Throws<PlayScenarioException>(() => session.Evaluate(Definition().Steps[1], false));
            Assert.That(exception.Failure.Code, Is.EqualTo("input_lease_expired"));
            Assert.That(backend.Applies, Is.EqualTo(1));
            Assert.That(backend.Releases, Is.EqualTo(1));
        }

        [Test]
        public void DispatchFailureReleasesDevicesImmediately()
        {
            backend.ThrowOnApply = true;
            var session = new PlayScenarioInputSession();
            Assert.Throws<InvalidOperationException>(() => session.Evaluate(Definition().Steps[1], true));
            session.Release();
            Assert.That(backend.Releases, Is.EqualTo(1));
        }

        [Test]
        public void CancellationDuringDeviceDispatchReleasesLateAllocatedDevices()
        {
            var session = new PlayScenarioInputSession();
            backend.DuringApply = session.Release;
            Assert.That(session.Evaluate(Definition().Steps[1], true).Ready, Is.False);
            Assert.That(backend.Applies, Is.EqualTo(1));
            Assert.That(backend.Releases, Is.EqualTo(2));
        }

        [Test]
        public void RenewedBackendLeaseUsesItsPostDispatchUpdateCounter()
        {
            backend.UpdateCount = 20;
            backend.DuringApply = () => backend.UpdateCount = 0;
            var session = new PlayScenarioInputSession();
            Assert.That(session.Evaluate(Definition().Steps[1], true).Ready, Is.False);
            backend.UpdateCount = 2;
            Assert.That(session.Evaluate(Definition().Steps[1], false).Ready, Is.True);
            session.Release();
        }

        [Test]
        public void ExpiredDeviceLeaseIsReleasedDuringLaterObjectWaitWithoutInputUpdates()
        {
            var definition = Definition();
            definition.Steps.Add(
                new PlayScenarioStep
                {
                    Name = "Wait",
                    Action = "wait_object",
                    Target = "Player",
                }
            );
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(definition, new string('a', 32), 1, 120, 1000), new Host { WaitForObject = true });
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            backend.UpdateCount = 2;
            engine.Tick(1500, true);
            engine.Tick(1750, true);
            backend.LeaseActive = false;
            engine.Tick(1800, false);
            Assert.That(backend.Releases, Is.EqualTo(1));
            Assert.That(engine.Running, Is.True);
            engine.Cancel(1850);
        }

        [TestCase("cancel")]
        [TestCase("failure")]
        [TestCase("interrupt")]
        public void EndingAnInputStepReleasesItsDevices(string outcome)
        {
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('a', 32), 1, 30, 1000), new Host());
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            Assert.That(backend.Applies, Is.EqualTo(1));
            if (outcome == "cancel")
                engine.Cancel(1300);
            else if (outcome == "failure")
                engine.RequestFailure("Unexpected error.", 1300);
            else
                engine.Interrupt("Play Mode exited.", 1300);
            Assert.That(backend.Releases, Is.EqualTo(1));
            Assert.That(engine.Running, Is.False);
        }

        [TestCase("cancel", false)]
        [TestCase("cancel", true)]
        [TestCase("interrupt", false)]
        [TestCase("interrupt", true)]
        public void FailureDuringInputReleaseKeepsItsOriginalOutcome(string callback, bool cleanup)
        {
            var definition = Definition();
            if (cleanup)
                definition.CleanupSteps.Add(
                    new PlayScenarioStep
                    {
                        Name = "Cleanup",
                        Action = "wait_object",
                        Target = "Player",
                    }
                );
            var host = new Host();
            int captures = 0;
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, 1000),
                host,
                onFailure: (run, step, now) => captures++
            );
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            backend.DuringRelease = () =>
            {
                if (callback == "cancel")
                    engine.Cancel(1300);
                else
                    engine.Interrupt("Play Mode exited during release.", 1300);
            };

            engine.RequestFailure("Original failure.", 1300);
            for (long now = 1500; now <= 2500 && engine.Running; now += 250)
                engine.Tick(now, true);

            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Phase, Is.EqualTo("finished"));
            Assert.That(engine.State.PendingStatus, Is.EqualTo("failed"));
            Assert.That(engine.State.PendingError, Is.EqualTo("Original failure."));
            Assert.That(engine.State.Error, Is.EqualTo("Original failure."));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("unexpected_log"));
            Assert.That(engine.State.Failure.Message, Is.EqualTo("Original failure."));
            Assert.That(engine.State.Steps[1].Detail, Is.EqualTo("Original failure."));
            Assert.That(host.CleanupEvaluations, Is.EqualTo(cleanup && callback == "cancel" ? 1 : 0));
            Assert.That(captures, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
            Assert.That(backend.Releases, Is.EqualTo(1));
            engine.Cancel(2750);
            engine.Interrupt("Late interruption.", 2750);
            engine.Release();
            Assert.That(host.Releases, Is.EqualTo(1));
            Assert.That(backend.Releases, Is.EqualTo(1));
        }

        [TestCase("cancel")]
        [TestCase("interrupt")]
        public void CleanupTimeoutDuringInputReleaseKeepsItsOriginalOutcome(string callback)
        {
            var definition = Definition();
            var cleanup = definition.Steps[1];
            definition.Steps.RemoveAt(1);
            cleanup.Name = "Cleanup";
            cleanup.TimeoutSeconds = 1;
            definition.CleanupSteps.Add(cleanup);
            var host = new Host();
            int captures = 0;
            var engine = new PlayScenarioEngine(
                PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, 1000),
                host,
                onFailure: (run, step, now) => captures++
            );
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            engine.Tick(1500, true);
            backend.DuringRelease = () =>
            {
                if (callback == "cancel")
                    engine.Cancel(2500);
                else
                    engine.Interrupt("Play Mode exited during cleanup release.", 2500);
            };

            engine.Tick(2500, true);

            Assert.That(engine.State.Status, Is.EqualTo("failed"));
            Assert.That(engine.State.Phase, Is.EqualTo("finished"));
            Assert.That(engine.State.PendingStatus, Is.EqualTo("failed"));
            Assert.That(engine.State.Error, Does.StartWith("Step timed out: Cleanup."));
            Assert.That(engine.State.PendingError, Is.EqualTo(engine.State.Error));
            Assert.That(engine.State.Failure.Code, Is.EqualTo("step_timeout"));
            Assert.That(engine.State.Failure.Message, Is.EqualTo(engine.State.Error));
            Assert.That(engine.State.CleanupError, Does.Contain("Step timed out: Cleanup."));
            Assert.That(engine.State.Steps[1].Status, Is.EqualTo("timed_out"));
            Assert.That(engine.State.Steps[1].Detail, Is.EqualTo(engine.State.Error));
            Assert.That(engine.State.Steps[1].FinishedUnixMs, Is.EqualTo(2500));
            Assert.That(engine.State.IterationResults[0].Status, Is.EqualTo("failed"));
            Assert.That(host.CleanupEvaluations, Is.EqualTo(1));
            Assert.That(backend.Applies, Is.EqualTo(1));
            Assert.That(captures, Is.EqualTo(1));
            Assert.That(host.Releases, Is.EqualTo(1));
            Assert.That(backend.Releases, Is.EqualTo(1));
        }

        [Test]
        public void SuccessfulIterationReleasesBoundedPressBeforeFinishing()
        {
            var engine = new PlayScenarioEngine(PlayScenarioEngine.Create(Definition(), new string('a', 32), 1, 30, 1000), new Host());
            engine.EnteredPlayMode();
            engine.Tick(1000, true);
            engine.Tick(1250, true);
            backend.UpdateCount = 2;
            for (long now = 1500; now <= 2500 && engine.Running; now += 250)
                engine.Tick(now, true);
            Assert.That(engine.State.Status, Is.EqualTo("succeeded"));
            Assert.That(backend.Releases, Is.EqualTo(1));
        }

        [Test]
        public void RecordedDraftCannotExecuteAndOldDefinitionOmitsDraft()
        {
            var definition = Definition();
            Assert.That(JObject.FromObject(definition).Property("draft"), Is.Null);
            definition.Draft = true;
            Assert.Throws<ArgumentException>(() => PlayScenarioEngine.Create(definition, new string('a', 32), 1, 30, 1000));
        }

        [TestCase("{'action':'key','key':'Space','frames':601}")]
        [TestCase("{'action':'touch','state':'move'}")]
        [TestCase("{'action':'mouse','position':[1,2,3]}")]
        [TestCase("{'action':'release_all','frames':1}")]
        [TestCase("{'action':'key','key':'Space','state':'release','frames':2}")]
        public void InvalidPersistedInputPayloadIsRejected(string payload) =>
            Assert.Throws<ArgumentException>(() => InputSimulationRequest.ParseScenario(JObject.Parse(payload)));
    }
}
