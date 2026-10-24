#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using InputApi = UnityEngine.InputSystem.InputSystem;
using TouchPhase = UnityEngine.InputSystem.TouchPhase;

namespace MCPForUnityTests.EditMode.Tools.Input
{
    // Invokes the real packet handlers with owned state buffers. InputTestFixture
    // isolates devices/settings; no Play Mode, queued events or physical input.
    public class InputRecordingPacketTests : InputTestFixture
    {
        private IPlayScenarioInputRecordingSession session;
        private Mouse mouse;
        private Touchscreen screen;
        private Action<Mouse, InputEventPtr> mousePacket;
        private Action<Touchscreen, InputEventPtr> touchPacket;
        private readonly List<JObject> events = new List<JObject>();
        private readonly List<string> diagnostics = new List<string>();

        public override void Setup()
        {
            base.Setup();
            InputApi.settings.updateMode = InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
            events.Clear();
            diagnostics.Clear();
            session = new InputSystemScenarioRecordingBackend().Begin((value, _) => events.Add(value), diagnostics.Add);
            mouse = InputApi.AddDevice<Mouse>();
            screen = InputApi.AddDevice<Touchscreen>();
            mousePacket = Handler<Mouse>("MouseEvent");
            touchPacket = Handler<Touchscreen>("TouchEvent");
        }

        private Action<T, InputEventPtr> Handler<T>(string name) =>
            (Action<T, InputEventPtr>)
                session.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).CreateDelegate(typeof(Action<T, InputEventPtr>), session);

        public override void TearDown()
        {
            session?.Dispose();
            base.TearDown();
        }

        private void Mouse(Vector2 position, bool pressed)
        {
            using (StateEvent.From(mouse, out var packet))
            {
                mouse.position.WriteValueIntoEvent(position, packet);
                mouse.leftButton.WriteValueIntoEvent(pressed ? 1f : 0f, packet);
                mousePacket(mouse, packet);
            }
        }

        private void Touch(int id, TouchPhase phase, Vector2 position)
        {
            using (StateEvent.From(screen, out var packet))
            {
                screen.primaryTouch.WriteValueIntoEvent(
                    new TouchState
                    {
                        touchId = id,
                        phase = phase,
                        position = position,
                    },
                    packet
                );
                touchPacket(screen, packet);
            }
        }

        private static IEnumerable<Vector2> InvalidPositions()
        {
            yield return new Vector2(float.NaN, 20);
            yield return new Vector2(10, float.PositiveInfinity);
            yield return new Vector2(float.NegativeInfinity, 20);
            yield return new Vector2(-1, 20);
            yield return new Vector2(1000001, 20);
        }

        [TestCaseSource(nameof(InvalidPositions))]
        public void InvalidMouseReleaseUsesLastValidPositionAndDoesNotRepeat(Vector2 invalid)
        {
            Mouse(new Vector2(10, 20), true);
            Mouse(invalid, false);
            Mouse(new Vector2(30, 20), false);
            CollectionAssert.AreEqual(new[] { "press", "release", "move" }, events.Select(x => (string)x["state"]));
            Assert.AreEqual(new JArray(10f, 20f).ToString(), events[1]["position"].ToString());
            Assert.That(diagnostics.Any(x => x.Contains("last valid")), Is.True);
        }

        [TestCaseSource(nameof(InvalidPositions))]
        public void InvalidMousePressDoesNotManufactureLaterPress(Vector2 invalid)
        {
            Mouse(invalid, true);
            Mouse(new Vector2(10, 20), true);
            Mouse(new Vector2(10, 20), false);
            Assert.That(events.Any(x => (string)x["state"] == "press"), Is.False);
            Assert.That(events.Any(x => (string)x["state"] == "release"), Is.False);
        }

        [TestCaseSource(nameof(InvalidPositions))]
        public void InvalidTerminalTouchReleasesAndReusesSlot(Vector2 invalid)
        {
            foreach (TouchPhase terminal in new[] { TouchPhase.Ended, TouchPhase.Canceled })
            {
                events.Clear();
                Touch(21, TouchPhase.Began, new Vector2(10, 20));
                Touch(21, terminal, invalid);
                Touch(22, TouchPhase.Began, new Vector2(30, 20));
                Touch(22, TouchPhase.Ended, new Vector2(30, 20));
                CollectionAssert.AreEqual(new[] { "press", "release", "press", "release" }, events.Select(x => (string)x["state"]));
                Assert.That(events.All(x => (int)x["touch_id"] == 1), Is.True);
                Assert.AreEqual(new JArray(10f, 20f).ToString(), events[1]["position"].ToString());
                Assert.That(diagnostics.Any(x => x.Contains("last valid")), Is.True);
            }
        }

        [Test]
        public void InvalidBeginsAndUnknownTerminalsDoNotConsumeSlotsOrInventPresses()
        {
            for (int id = 1; id <= 12; id++)
                Touch(id, TouchPhase.Began, new Vector2(-1, 20));
            Touch(1, TouchPhase.Ended, new Vector2(10, 20));
            Touch(2, TouchPhase.Canceled, new Vector2(float.NaN, 20));
            Touch(3, TouchPhase.Moved, new Vector2(10, 20));
            Assert.That(events, Is.Empty);
            Touch(99, TouchPhase.Began, new Vector2(10, 20));
            Assert.AreEqual(1, events.Count);
            Assert.AreEqual(1, (int)events[0]["touch_id"]);
        }

        [Test]
        public void ExecutableOffWindowCoordinatesRemainObservable()
        {
            Vector2 position = new Vector2(999999, 999999);
            Mouse(position, true);
            Mouse(position, false);
            Touch(1, TouchPhase.Began, position);
            Touch(1, TouchPhase.Ended, position);
            Assert.AreEqual(4, events.Count);
            Assert.That(events.All(x => x["position"][0].Value<float>() == position.x), Is.True);
        }

        [Test]
        public void DuplicatePrimaryContactPreservesPacketOrderAndSeparateSessionScratch()
        {
            using (StateEvent.From(screen, out var packet))
            {
                var first = new TouchState
                {
                    touchId = 42,
                    phase = TouchPhase.Began,
                    position = new Vector2(10, 20),
                };
                screen.primaryTouch.WriteValueIntoEvent(first, packet);
                screen.touches[0].WriteValueIntoEvent(first, packet);
                screen
                    .touches[1]
                    .WriteValueIntoEvent(
                        new TouchState
                        {
                            touchId = 43,
                            phase = TouchPhase.Began,
                            position = new Vector2(30, 20),
                        },
                        packet
                    );
                touchPacket(screen, packet);
                Assert.AreEqual(2, events.Count);
                CollectionAssert.AreEqual(new[] { 1, 2 }, events.Select(x => (int)x["touch_id"]));
                var otherEvents = new List<JObject>();
                using (var other = new InputSystemScenarioRecordingBackend().Begin((value, _) => otherEvents.Add(value), _ => { }))
                {
                    var handler =
                        (Action<Touchscreen, InputEventPtr>)
                            other
                                .GetType()
                                .GetMethod("TouchEvent", BindingFlags.Instance | BindingFlags.NonPublic)
                                .CreateDelegate(typeof(Action<Touchscreen, InputEventPtr>), other);
                    handler(screen, packet);
                    Assert.AreEqual(2, otherEvents.Count, "Each recording session must own its scratch identities.");
                }
            }
        }

        [Test]
        public void DisposalDuringObservationDoesNotRepopulateContactState()
        {
            session.Dispose();
            session = new InputSystemScenarioRecordingBackend().Begin((_, __) => session.Dispose(), _ => { });
            touchPacket = Handler<Touchscreen>("TouchEvent");
            Touch(1, TouchPhase.Began, new Vector2(10, 20));
            Assert.AreEqual(
                0,
                ((IDictionary)session.GetType().GetField("touchPositions", BindingFlags.Instance | BindingFlags.NonPublic).GetValue(session)).Count
            );
        }

        [Test]
        public void DisposalClearsContactsAndRejectsLaterPackets()
        {
            Touch(1, TouchPhase.Began, new Vector2(10, 20));
            Mouse(new Vector2(10, 20), true);
            session.Dispose();
            int count = events.Count;
            Touch(1, TouchPhase.Ended, new Vector2(-1, 20));
            Mouse(new Vector2(-1, 20), false);
            Assert.AreEqual(count, events.Count);
            foreach (string name in new[] { "buttons", "touchSlots", "touchPositions" })
                Assert.AreEqual(0, ((IDictionary)session.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(session)).Count);
        }

        [Test]
        public void NestedTouchObservationPreservesOuterPacketDeduplication()
        {
            session.Dispose();
            bool nested = false;
            session = new InputSystemScenarioRecordingBackend().Begin(
                (value, _) =>
                {
                    events.Add(value);
                    if (!nested)
                    {
                        nested = true;
                        Touch(99, TouchPhase.Began, new Vector2(50, 20));
                    }
                },
                diagnostics.Add
            );
            touchPacket = Handler<Touchscreen>("TouchEvent");
            using (StateEvent.From(screen, out var packet))
            {
                screen.primaryTouch.WriteValueIntoEvent(
                    new TouchState
                    {
                        touchId = 42,
                        phase = TouchPhase.Began,
                        position = new Vector2(10, 20),
                    },
                    packet
                );
                // A conflicting duplicate must still be ignored after nested observation.
                screen
                    .touches[0]
                    .WriteValueIntoEvent(
                        new TouchState
                        {
                            touchId = 42,
                            phase = TouchPhase.Began,
                            position = new Vector2(11, 20),
                        },
                        packet
                    );
                screen
                    .touches[1]
                    .WriteValueIntoEvent(
                        new TouchState
                        {
                            touchId = 43,
                            phase = TouchPhase.Began,
                            position = new Vector2(30, 20),
                        },
                        packet
                    );
                touchPacket(screen, packet);
                CollectionAssert.AreEqual(new[] { 1, 2, 3 }, events.Select(x => (int)x["touch_id"]));
                CollectionAssert.AreEqual(new[] { 10f, 50f, 30f }, events.Select(x => x["position"][0].Value<float>()));
            }
        }

        [Test]
        public void AllMouseButtonsPreserveOrderThroughInvalidRelease()
        {
            using (StateEvent.From(mouse, out var packet))
            {
                mouse.position.WriteValueIntoEvent(new Vector2(10, 20), packet);
                mouse.leftButton.WriteValueIntoEvent(1f, packet);
                mouse.rightButton.WriteValueIntoEvent(1f, packet);
                mouse.middleButton.WriteValueIntoEvent(1f, packet);
                mousePacket(mouse, packet);
                mouse.position.WriteValueIntoEvent(new Vector2(float.NaN, 20), packet);
                mouse.leftButton.WriteValueIntoEvent(0f, packet);
                mouse.rightButton.WriteValueIntoEvent(0f, packet);
                mouse.middleButton.WriteValueIntoEvent(0f, packet);
                mousePacket(mouse, packet);
                CollectionAssert.AreEqual(new[] { "left", "right", "middle", "left", "right", "middle" }, events.Select(x => (string)x["button"]));
                CollectionAssert.AreEqual(new[] { "press", "press", "press", "release", "release", "release" }, events.Select(x => (string)x["state"]));
                Assert.That(events.All(x => x["position"][0].Value<float>() == 10), Is.True);
            }
        }
    }
}
#endif
