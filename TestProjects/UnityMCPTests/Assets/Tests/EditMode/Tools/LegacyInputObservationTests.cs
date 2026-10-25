using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.EditMode.Tools
{
    public sealed class LegacyInputObservationTests
    {
        private sealed class Source : ILegacyInputRecordingSource
        {
            public int FrameCount { get; set; }
            public bool AnyKey => Down.Count > 0;
            public bool AnyKeyDown { get; set; }
            public readonly HashSet<KeyCode> Down = new HashSet<KeyCode>();
            public readonly HashSet<KeyCode> Up = new HashSet<KeyCode>();
            public readonly HashSet<int> MouseDown = new HashSet<int>();
            public readonly HashSet<int> MouseUp = new HashSet<int>();
            public readonly List<LegacyInputTouch> Touches = new List<LegacyInputTouch>();
            public bool MousePresent { get; set; } = true;
            public Vector2 MousePosition { get; set; } = new Vector2(10, 20);
            public Vector2 MouseScrollDelta { get; set; }
            public bool SimulateMouseWithTouches { get; set; }
            public int TouchCount => Touches.Count;
            public int Reads { get; private set; }

            public bool GetKeyDown(KeyCode key)
            {
                Reads++;
                return Down.Contains(key);
            }

            public bool GetKeyUp(KeyCode key)
            {
                Reads++;
                return Up.Contains(key);
            }

            public bool GetMouseButtonDown(int button)
            {
                Reads++;
                return MouseDown.Contains(button);
            }

            public bool GetMouseButtonUp(int button)
            {
                Reads++;
                return MouseUp.Contains(button);
            }

            public LegacyInputTouch GetTouch(int index)
            {
                Reads++;
                return Touches[index];
            }
        }

        [Test]
        public void SampleUsesGameFramesAndMapsOnlyObservedKeyEdges()
        {
            var source = new Source { FrameCount = 10 };
            var events = new List<JObject>();
            var frames = new List<long>();
            using (
                var state = new LegacyInputObservation(
                    source,
                    (value, frame) =>
                    {
                        events.Add(value);
                        frames.Add(frame);
                    },
                    _ => { }
                )
            )
            {
                source.Down.Add(KeyCode.Alpha1);
                source.Down.Add(KeyCode.Return);
                state.Sample();
                Assert.That(events, Is.Empty, "A transition from before the session cannot become a press.");
                source.FrameCount = 11;
                state.Sample();
                state.Sample();
                CollectionAssert.AreEqual(new[] { "Digit1", "Enter" }, events.Select(value => (string)value["key"]));
                source.Down.Clear();
                source.Up.Add(KeyCode.Alpha1);
                source.Up.Add(KeyCode.Space);
                source.FrameCount = 14;
                state.Sample();
                Assert.That(events.Count, Is.EqualTo(3));
                Assert.That((string)events[2]["state"], Is.EqualTo("release"));
                CollectionAssert.AreEqual(new long[] { 1, 1, 4 }, frames);
                Assert.That(state.UpdateCount, Is.EqualTo(4));
            }
        }

        [Test]
        public void MouseDragWheelAndTouchKeepNativeUnitsAndContactIdentity()
        {
            var source = new Source();
            var events = new List<JObject>();
            using (var state = new LegacyInputObservation(source, (value, _) => events.Add(value), _ => { }))
            {
                source.MouseDown.Add(0);
                source.FrameCount++;
                state.Sample();
                source.MouseDown.Clear();
                source.MousePosition = new Vector2(30, 40);
                source.MouseScrollDelta = new Vector2(0, -1);
                source.FrameCount++;
                state.Sample();
                source.MouseUp.Add(0);
                source.MouseScrollDelta = Vector2.zero;
                source.FrameCount++;
                state.Sample();
                source.MouseUp.Clear();
                source.SimulateMouseWithTouches = true;
                source.MouseDown.Add(0);
                source.Touches.Add(new LegacyInputTouch(42, new Vector2(50, 60), TouchPhase.Began));
                source.FrameCount++;
                state.Sample();
                source.Touches[0] = new LegacyInputTouch(42, new Vector2(70, 80), TouchPhase.Moved);
                source.FrameCount++;
                state.Sample();
                source.Touches[0] = new LegacyInputTouch(42, new Vector2(float.NaN, 80), TouchPhase.Canceled);
                source.FrameCount++;
                state.Sample();
                CollectionAssert.AreEqual(
                    new[] { "mouse", "mouse", "wheel", "mouse", "touch", "touch", "touch" },
                    events.Select(value => (string)value["action"])
                );
                Assert.That(events[2]["delta"][1].Value<int>(), Is.EqualTo(-1));
                CollectionAssert.AreEqual(new[] { 1, 1, 1 }, events.Skip(4).Select(value => (int)value["touch_id"]));
                Assert.That((string)events[6]["state"], Is.EqualTo("release"));
                Assert.That(events[6]["position"][0].Value<int>(), Is.EqualTo(70));
            }
        }

        [Test]
        public void DisposingInsideObserverStopsTheCurrentSampleAndFuturePolling()
        {
            var source = new Source();
            source.Down.Add(KeyCode.A);
            source.Down.Add(KeyCode.B);
            int emitted = 0;
            LegacyInputObservation state = null;
            state = new LegacyInputObservation(
                source,
                (_, __) =>
                {
                    emitted++;
                    state.Dispose();
                },
                _ => { }
            );
            source.FrameCount++;
            state.Sample();
            int reads = source.Reads;
            source.FrameCount++;
            state.Sample();
            Assert.That(emitted, Is.EqualTo(1));
            Assert.That(source.Reads, Is.EqualTo(reads));
        }

        [Test]
        public void LegacyDraftRetainsSourceAndReplayLimitAfterObservationEnds()
        {
            var draft = new PlayScenarioRecordingDraft("legacy-recording", "Assets/Scenes/Menu.unity");
            draft.DescribeInputObservation(LegacyInputScenarioRecordingBackend.DescribeCapabilities());
            draft.Finish("stopped", "requested");
            JObject snapshot = draft.Snapshot();
            Assert.That((string)snapshot["input_observation"]["backend"], Is.EqualTo("legacy_input_manager"));
            Assert.That((bool)snapshot["input_observation"]["raw_replay_available"], Is.False);
            Assert.That((string)snapshot["input_observation"]["raw_replay_unavailable_reason"], Does.Contain("UnityEngine.Input"));
            CollectionAssert.Contains(snapshot["scenario"]["tags"].Values<string>().ToArray(), "recorded-legacy-input");
        }

        [Test]
        public void IdleFramesDoNotScanEveryKeyboardKeyAndTouchLimitDoesNotInventContacts()
        {
            var source = new Source { MousePresent = false };
            var events = new List<JObject>();
            var diagnostics = new List<string>();
            using (var state = new LegacyInputObservation(source, (value, _) => events.Add(value), diagnostics.Add))
            {
                source.FrameCount++;
                state.Sample();
                Assert.That(source.Reads, Is.Zero);
                for (int i = 0; i < 11; i++)
                    source.Touches.Add(new LegacyInputTouch(i, new Vector2(10, 20), TouchPhase.Began));
                source.FrameCount++;
                state.Sample();
                Assert.That(events.Count, Is.EqualTo(10));
                CollectionAssert.AreEqual(Enumerable.Range(1, 10), events.Select(value => (int)value["touch_id"]));
                Assert.That(diagnostics.Any(value => value.Contains("ten")), Is.True);
            }
        }
    }
}
