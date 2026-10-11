#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.InputSystem;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public sealed class PersistedInputSimulationTests : InputTestFixture
    {
        private sealed class TimedBackend : RuntimeInputSystemSimulationBackend
        {
            public double Now;
            protected override double Clock => Now;
        }

        private Func<IScenarioInputSimulationBackend> previousFactory;
        private PlayScenarioInputSession session;
        private RuntimeInputSystemSimulationBackend direct;

        public override void Setup()
        {
            base.Setup();
            InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
            previousFactory = PlayScenarioPlayerInput.CreateInputBackend;
            PlayScenarioPlayerInput.CreateInputBackend = () => new RuntimeInputSystemSimulationBackend();
            session = new PlayScenarioInputSession();
            direct = new RuntimeInputSystemSimulationBackend();
        }

        public override void TearDown()
        {
            session.Release();
            direct.ReleaseAll();
            PlayScenarioPlayerInput.CreateInputBackend = previousFactory;
            base.TearDown();
        }

        private static PlayScenarioStep Step(string command) =>
            new PlayScenarioStep
            {
                Action = "input",
                Input = JObject.Parse(command),
                WaitFrames = 1,
            };

        [Test]
        public void ExpiredLeaseRemovesOwnedDeviceWithoutAnotherInputUpdate()
        {
            var backend = new TimedBackend();
            PlayScenarioPlayerInput.CreateInputBackend = () => backend;
            session.Evaluate(Step("{'action':'key','key':'Space','frames':600}"), true);
            Keyboard keyboard = Keyboard.current;
            InputApi.Update();
            Assert.That(keyboard.spaceKey.isPressed, Is.True);
            backend.Now = InputSimulationRequest.DeviceTimeoutSeconds;
            int updates = 0;
            Action countUpdate = () => updates++;
            InputApi.onBeforeUpdate += countUpdate;
            try
            {
                session.Maintain();
            }
            finally
            {
                InputApi.onBeforeUpdate -= countUpdate;
            }
            Assert.That(updates, Is.Zero);
            Assert.That(keyboard.added, Is.False);
            Assert.That(backend.LeaseActive, Is.False);
        }

        [Test]
        public void SavedMousePressMoveReleaseFormsAContinuousDrag()
        {
            var press = Step("{'action':'mouse','position':[10,20],'frames':20}");
            Assert.That(session.Evaluate(press, true).Ready, Is.False);
            Mouse mouse = Mouse.current;
            InputApi.Update();
            Assert.That(session.Evaluate(press, false).Ready, Is.True);
            Assert.That(mouse.leftButton.isPressed, Is.True);
            var move = Step("{'action':'mouse','state':'move','position':[80,90]}");
            Assert.That(session.Evaluate(move, true).Ready, Is.False);
            InputApi.Update();
            Assert.That(session.Evaluate(move, false).Ready, Is.True);
            Assert.That(mouse.leftButton.isPressed, Is.True);
            Assert.That(mouse.position.ReadValue(), Is.EqualTo(new Vector2(80, 90)));
            var release = Step("{'action':'mouse','state':'release'}");
            session.Evaluate(release, true);
            InputApi.Update();
            Assert.That(session.Evaluate(release, false).Ready, Is.True);
            Assert.That(mouse.leftButton.isPressed, Is.False);
            session.Release();
            Assert.That(mouse.added, Is.False);
        }

        [Test]
        public void ScenarioCleanupDoesNotRemoveDirectInputDevices()
        {
            direct.Apply(InputSimulationRequest.Parse(JObject.Parse("{'action':'key','key':'A','frames':10}")));
            Keyboard directKeyboard = Keyboard.current;
            session.Evaluate(Step("{'action':'key','key':'Space','frames':10}"), true);
            Keyboard scenarioKeyboard = Keyboard.current;
            Assert.That(scenarioKeyboard, Is.Not.SameAs(directKeyboard));
            InputApi.Update();
            Assert.That(directKeyboard.aKey.isPressed, Is.True);
            Assert.That(scenarioKeyboard.spaceKey.isPressed, Is.True);
            session.Release();
            Assert.That(scenarioKeyboard.added, Is.False);
            Assert.That(directKeyboard.added, Is.True);
            Assert.That(directKeyboard.aKey.isPressed, Is.True);
            Assert.That(Keyboard.current, Is.SameAs(directKeyboard));
        }

        [Test]
        public void SavedTouchMoveRetainsContactUntilExplicitRelease()
        {
            var press = Step("{'action':'touch','touch_id':2,'position':[10,20],'frames':20}");
            session.Evaluate(press, true);
            Touchscreen screen = Touchscreen.current;
            InputApi.Update();
            var move = Step("{'action':'touch','state':'move','touch_id':2,'position':[30,40]}");
            session.Evaluate(move, true);
            InputApi.Update();
            Assert.That(screen.primaryTouch.press.isPressed, Is.True);
            Assert.That(screen.primaryTouch.position.ReadValue(), Is.EqualTo(new Vector2(30, 40)));
            var release = Step("{'action':'touch','state':'release','touch_id':2}");
            session.Evaluate(release, true);
            InputApi.Update();
            Assert.That(screen.primaryTouch.press.isPressed, Is.False);
        }
    }
}
#endif
