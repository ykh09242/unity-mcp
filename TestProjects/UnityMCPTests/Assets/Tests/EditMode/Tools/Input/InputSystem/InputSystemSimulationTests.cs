#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System;
using System.Linq;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    /// <summary>InputTestFixture isolates all devices and restores the original runtime after each test.</summary>
    public class InputSystemSimulationTests : InputTestFixture
    {
        private InputSystemSimulationBackend _backend;

        public override void Setup()
        {
            base.Setup();
            InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
            _backend = new InputSystemSimulationBackend();
        }

        public override void TearDown()
        {
            _backend.ReleaseAll();
            base.TearDown();
        }

        private void Apply(string json) => _backend.Apply(InputSimulationRequest.Parse(JObject.Parse(json)));

        [Test]
        public void KeyRemainsPressedForRequestedUpdatesThenReleases()
        {
            // Given: a bounded two-update press on an owned virtual keyboard.
            Apply("{\"action\":\"key\",\"key\":\"A\",\"frames\":2}");
            Keyboard keyboard = Keyboard.current;
            // When: three game input updates are processed.
            InputApi.Update();
            bool first = keyboard.aKey.isPressed;
            InputApi.Update();
            bool second = keyboard.aKey.isPressed;
            InputApi.Update();
            // Then: gameplay observes two held updates followed by release.
            Assert.That(first, Is.True);
            Assert.That(second, Is.True);
            Assert.That(keyboard.aKey.isPressed, Is.False);
        }

        [Test]
        public void QueuedVirtualKeyTriggersRealInputActionAndCancellation()
        {
            // Given: a real enabled action listening for Space.
            using (var action = new InputAction(binding: "<Keyboard>/space"))
            {
                int performed = 0;
                int canceled = 0;
                action.performed += context => performed++;
                action.canceled += context => canceled++;
                action.Enable();
                // When: a one-update press completes through the actual event queue.
                Apply("{\"action\":\"key\",\"key\":\"Space\",\"frames\":1}");
                InputApi.Update();
                InputApi.Update();
                // Then: bindings see a press and its matching release.
                Assert.That(performed, Is.EqualTo(1));
                Assert.That(canceled, Is.EqualTo(1));
            }
        }

        [Test]
        public void ChordReleasePreservesTheOtherHeldKey()
        {
            // Given: two independently timed keys form a chord.
            Apply("{\"action\":\"key\",\"key\":\"LeftShift\",\"frames\":4}");
            Apply("{\"action\":\"key\",\"key\":\"A\",\"frames\":2}");
            // When: the shorter hold expires.
            InputApi.Update();
            InputApi.Update();
            InputApi.Update();
            // Then: the other key remains held.
            Assert.That(Keyboard.current.aKey.isPressed, Is.False);
            Assert.That(Keyboard.current.leftShiftKey.isPressed, Is.True);
        }

        [Test]
        public void MouseMovePreservesHeldButtonsUntilTheirOwnRelease()
        {
            // Given: a held left button on a virtual mouse.
            Apply("{\"action\":\"mouse\",\"button\":\"left\",\"frames\":3,\"position\":[10,20]}");
            // When: the pointer moves without releasing the button.
            Apply("{\"action\":\"mouse\",\"state\":\"move\",\"position\":[30,40]}");
            InputApi.Update();
            // Then: position changes while the left button remains pressed.
            Assert.That(Mouse.current.position.ReadValue(), Is.EqualTo(new Vector2(30, 40)));
            Assert.That(Mouse.current.leftButton.isPressed, Is.True);
        }

        [Test]
        public void TwoTouchesHaveIndependentIdsAndAutomaticEndings()
        {
            // Given: concurrent contacts with different bounded lifetimes.
            Apply("{\"action\":\"touch\",\"touch_id\":1,\"position\":[10,20],\"frames\":2}");
            Apply("{\"action\":\"touch\",\"touch_id\":2,\"position\":[30,40],\"frames\":4}");
            // When: contact 1 expires after two updates.
            InputApi.Update();
            int initial = Touchscreen.current.touches.Count(touch => touch.press.isPressed);
            InputApi.Update();
            InputApi.Update();
            // Then: contact 2 remains active with its original identity.
            Assert.That(initial, Is.EqualTo(2));
            var active = Touchscreen.current.touches.Where(touch => touch.press.isPressed).ToArray();
            Assert.That(active.Length, Is.EqualTo(1));
            Assert.That(active[0].touchId.ReadValue(), Is.EqualTo(2));
        }

        [Test]
        public void ReleasingSimulationRemovesOnlyOwnedDevicesAndRestoresCurrent()
        {
            // Given: an existing keyboard plus an MCP-owned virtual keyboard.
            Keyboard original = InputApi.AddDevice<Keyboard>("OriginalKeyboard");
            InputApi.QueueStateEvent(original, new KeyboardState(Key.B));
            InputApi.Update();
            Apply("{\"action\":\"key\",\"key\":\"A\",\"frames\":600}");
            Keyboard simulated = Keyboard.current;
            InputApi.Update();
            // When: the session is cleaned up, as on stop/reload/timeout.
            _backend.ReleaseAll();
            // Then: the original device and its independent state are preserved.
            Assert.That(simulated.added, Is.False);
            Assert.That(original.added, Is.True);
            Assert.That(original.bKey.isPressed, Is.True);
            Assert.That(Keyboard.current, Is.SameAs(original));
        }

        [Test]
        public void ManualUpdateModeRejectsInjectionBeforeCreatingDevices()
        {
            // Given: no automatic game input updates to guarantee frame-bounded release.
            InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsManually;
            int devices = InputApi.devices.Count;
            // When/Then: the adapter reports unsupported mode without adding a device.
            Assert.Throws<ArgumentException>(() => Apply("{\"action\":\"key\",\"key\":\"A\"}"));
            Assert.That(InputApi.devices.Count, Is.EqualTo(devices));
        }

        [Test]
        public void UnknownTouchMoveCannotAllocateAContactOrDevice()
        {
            // Given: no existing touch contact.
            int devices = InputApi.devices.Count;
            // When/Then: an invalid move cannot fabricate a new contact.
            Assert.Throws<ArgumentException>(() => Apply("{\"action\":\"touch\",\"state\":\"move\",\"touch_id\":2,\"position\":[1,2]}"));
            Assert.That(InputApi.devices.Count, Is.EqualTo(devices));
        }

        [Test]
        public void HardTimeoutRemovesHeldVirtualDevicesWithoutGameUpdates()
        {
            // Given: a long held key whose game loop might be stalled.
            Apply("{\"action\":\"key\",\"key\":\"A\",\"frames\":600}");
            Keyboard simulated = Keyboard.current;
            // When: the Editor lifetime observer sees more than the hard30-second lease.
            _backend.MaintainLifetime(EditorApplication.timeSinceStartup + 31, true);
            // Then: the held virtual device is removed even without a single game input update.
            Assert.That(simulated.added, Is.False);
        }

        [Test]
        public void StoppingPlayModeRemovesHeldDevicesBeforeTheDeadline()
        {
            // Given: an active virtual mouse lease.
            Apply("{\"action\":\"mouse\",\"frames\":600}");
            Mouse simulated = Mouse.current;
            // When: the Editor lifetime observer sees Play Mode stop.
            _backend.MaintainLifetime(EditorApplication.timeSinceStartup, false);
            // Then: cleanup is immediate rather than waiting for frame-based release.
            Assert.That(simulated.added, Is.False);
        }
    }
}
#endif
