#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    public class InputAnalogSimulationTests : InputTestFixture
    {
        private RuntimeInputSystemSimulationBackend backend;

        public override void Setup()
        {
            base.Setup();
            InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
            backend = new RuntimeInputSystemSimulationBackend();
        }

        public override void TearDown()
        {
            backend.ReleaseAll();
            base.TearDown();
        }

        private void Apply(string json) => backend.Apply(InputSimulationRequest.Parse(JObject.Parse(json)));

        [Test]
        public void StickInputReachesActualActionThenReturnsToNeutral()
        {
            using var action = new InputAction(type: InputActionType.Value, binding: "<Gamepad>/leftStick");
            action.Enable();
            Apply("{\"action\":\"gamepad\",\"control\":\"leftStick\",\"value\":[0.25,0.75],\"frames\":2}");
            Gamepad owned = Gamepad.current;
            InputApi.Update();
            Assert.AreEqual(new Vector2(0.25f, 0.75f), owned.leftStick.ReadUnprocessedValue());
            Assert.Greater(action.ReadValue<Vector2>().magnitude, 0.5f);
            InputApi.Update();
            Assert.Greater(action.ReadValue<Vector2>().magnitude, 0.5f);
            InputApi.Update();
            Assert.AreEqual(Vector2.zero, action.ReadValue<Vector2>());
        }

        [Test]
        public void TriggerAndButtonHaveIndependentReleaseDeadlines()
        {
            using var trigger = new InputAction(type: InputActionType.Value, binding: "<Gamepad>/leftTrigger");
            using var button = new InputAction(type: InputActionType.Button, binding: "<Gamepad>/buttonSouth");
            trigger.Enable();
            button.Enable();
            Apply("{\"action\":\"gamepad\",\"control\":\"leftTrigger\",\"value\":0.6,\"frames\":3}");
            Apply("{\"action\":\"gamepad\",\"control\":\"buttonSouth\",\"frames\":1}");
            InputApi.Update();
            Assert.That(trigger.ReadValue<float>(), Is.EqualTo(0.6f).Within(0.001f));
            Assert.IsTrue(button.IsPressed());
            InputApi.Update();
            Assert.IsFalse(button.IsPressed());
            Assert.Greater(trigger.ReadValue<float>(), 0.5f);
            InputApi.Update();
            InputApi.Update();
            Assert.AreEqual(0f, trigger.ReadValue<float>());
        }

        [Test]
        public void WheelDispatchesOnceAndPreservesHeldMouseButton()
        {
            using var action = new InputAction(type: InputActionType.Value, binding: "<Mouse>/scroll");
            int performed = 0;
            action.performed += _ => performed++;
            action.Enable();
            Apply("{\"action\":\"mouse\",\"frames\":4}");
            Apply("{\"action\":\"wheel\",\"delta\":[0,120]}");
            InputApi.Update();
            Assert.AreNotEqual(Vector2.zero, action.ReadValue<Vector2>());
            Assert.IsTrue(Mouse.current.leftButton.isPressed);
            InputApi.Update();
            Assert.AreEqual(Vector2.zero, action.ReadValue<Vector2>());
            Assert.AreEqual(1, performed);
            Assert.IsTrue(Mouse.current.leftButton.isPressed);
        }

        [Test]
        public void CleanupPreservesUnrelatedGamepadStateAndCurrentIdentity()
        {
            Gamepad original = InputApi.AddDevice<Gamepad>("UnrelatedGamepad");
            InputApi.QueueStateEvent(original, new GamepadState { rightTrigger = 0.8f });
            InputApi.Update();
            Apply("{\"action\":\"gamepad\",\"control\":\"rightStick\",\"value\":[-0.5,0.7],\"frames\":600}");
            Gamepad owned = Gamepad.current;
            InputApi.Update();
            backend.ReleaseAll();
            Assert.IsFalse(owned.added);
            Assert.IsTrue(original.added);
            Assert.AreEqual(original, Gamepad.current);
            Assert.That(original.rightTrigger.ReadValue(), Is.EqualTo(0.8f).Within(0.001f));
        }

        [Test]
        public void ScenarioParserAcceptsBoundedAnalogAndOneShotWheel()
        {
            var analog = InputSimulationRequest.ParseScenario(
                JObject.Parse("{\"action\":\"gamepad\",\"control\":\"rightTrigger\",\"value\":0.3,\"frames\":2}")
            );
            Assert.AreEqual("gamepad", analog.Action);
            var wheel = InputSimulationRequest.ParseScenario(JObject.Parse("{\"action\":\"wheel\",\"delta\":[1,-120]}"));
            Assert.AreEqual("wheel", wheel.Action);
            int count = InputApi.devices.Count;
            Assert.Throws<ArgumentException>(() => Apply("{\"action\":\"gamepad\",\"control\":\"leftStick\",\"value\":[2,0]}"));
            Assert.AreEqual(count, InputApi.devices.Count);
        }
    }
}
#endif
