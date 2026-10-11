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
            }
        }

        private sealed class Host : IPlayScenarioHost, IPlayScenarioInputHost
        {
            private readonly PlayScenarioInputSession session = new PlayScenarioInputSession();
            public bool WaitForObject;

            public PlayScenarioObservation Evaluate(PlayScenarioStep step, bool first) =>
                step.Action == "input" ? session.Evaluate(step, first) : new PlayScenarioObservation(!WaitForObject || step.Action != "wait_object", "ready");

            public void MaintainInput() => session.Maintain();

            public void ReleaseInput() => session.Release();

            public void Release() => ReleaseInput();
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
