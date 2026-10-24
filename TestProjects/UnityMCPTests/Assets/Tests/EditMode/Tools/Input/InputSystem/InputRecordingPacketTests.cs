#if MCP_INPUT_SYSTEM && ENABLE_INPUT_SYSTEM
using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Editor.Tools.Input;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using Unity.Profiling;
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

        [Test, Explicit("Run unchanged against baseline and candidate in the same warm native Editor; compare allocation, latency and output checksum.")]
        public void ProfilePacketsAt125And1000Hz()
        {
            MethodInfo allocated = typeof(GC).GetMethod("GetAllocatedBytesForCurrentThread", Type.EmptyTypes);
            var allocatedBytes = allocated == null ? (Func<long>)(() => 0L) : (Func<long>)allocated.CreateDelegate(typeof(Func<long>));
            long beforeCalibration = allocatedBytes();
            GC.KeepAlive(new byte[4096]);
            bool bytesAvailable = allocatedBytes() - beforeCalibration >= 4096;
            int calibrationEvents = CountAllocationEvents(() =>
            {
                for (int i = 0; i < 32; i++)
                    GC.KeepAlive(new byte[1024]);
            });
            Assert.That(calibrationEvents, Is.GreaterThanOrEqualTo(32), "Native GC.Alloc recorder must observe the known allocations.");
            TestContext.WriteLine(
                "allocation_calibration "
                    + new JObject
                    {
                        ["byte_counter_available"] = bytesAvailable,
                        ["known_allocation_events"] = 32,
                        ["recorded_allocation_events"] = calibrationEvents,
                    }.ToString(Newtonsoft.Json.Formatting.None)
            );
            foreach (int rate in new[] { 125, 1000 })
            foreach (string workload in new[] { "mouse-idle", "mouse-motion", "touch-idle", "touch-motion" })
            {
                session.Dispose();
                long emitted = 0;
                double checksum = 0;
                session = new InputSystemScenarioRecordingBackend().Begin(
                    (value, _) =>
                    {
                        emitted++;
                        checksum += value["position"][0].Value<double>();
                    },
                    _ => { }
                );
                mousePacket = Handler<Mouse>("MouseEvent");
                touchPacket = Handler<Touchscreen>("TouchEvent");
                FieldInfo budget = session.GetType().GetField("eventsInUpdate", BindingFlags.Instance | BindingFlags.NonPublic);
                bool touch = workload.StartsWith("touch", StringComparison.Ordinal);
                bool motion = workload.EndsWith("motion", StringComparison.Ordinal);
                InputDevice device = touch ? (InputDevice)screen : mouse;
                using (StateEvent.From(device, out var packet))
                {
                    if (touch)
                    {
                        for (int contact = 0; contact < 5; contact++)
                            screen
                                .touches[contact]
                                .WriteValueIntoEvent(
                                    new TouchState
                                    {
                                        touchId = contact + 1,
                                        phase = TouchPhase.Began,
                                        position = new Vector2(10, 20),
                                    },
                                    packet
                                );
                        touchPacket(screen, packet);
                        for (int contact = 0; contact < 5; contact++)
                            screen.touches[contact].phase.WriteValueIntoEvent(TouchPhase.Moved, packet);
                    }
                    else
                        mouse.position.WriteValueIntoEvent(new Vector2(10, 20), packet);
                    void ObservePacket(int sequence)
                    {
                        Vector2 position = new Vector2(motion ? 10 + sequence % 2 : 10, 20);
                        if (touch)
                        {
                            for (int contact = 0; contact < 5; contact++)
                                screen.touches[contact].position.WriteValueIntoEvent(position, packet);
                            touchPacket(screen, packet);
                        }
                        else
                        {
                            mouse.position.WriteValueIntoEvent(position, packet);
                            mousePacket(mouse, packet);
                        }
                    }
                    var samples = new List<double>();
                    long bytes = 0;
                    // Each sample models one second of 60 game updates. Resetting
                    // the transition budget and measurement bookkeeping are untimed.
                    for (int sample = -2; sample < 31; sample++)
                    {
                        long ticks = 0;
                        long sampleBytes = 0;
                        for (int update = 0; update < 60; update++)
                        {
                            budget.SetValue(session, 0);
                            int count = (update + 1) * rate / 60 - update * rate / 60;
                            long beforeBytes = allocatedBytes();
                            long start = Stopwatch.GetTimestamp();
                            for (int index = 0; index < count; index++)
                                ObservePacket((sample + 2) * rate + update * 20 + index);
                            ticks += Stopwatch.GetTimestamp() - start;
                            sampleBytes += allocatedBytes() - beforeBytes;
                        }
                        if (sample >= 0)
                        {
                            samples.Add(ticks * 1000000d / Stopwatch.Frequency / rate);
                            bytes += sampleBytes;
                        }
                    }
                    // Profile allocation event counts separately so profiler
                    // instrumentation cannot skew the latency comparison.
                    long allocationEvents = 0;
                    for (int update = 0; update < 60; update++)
                    {
                        budget.SetValue(session, 0);
                        int count = (update + 1) * rate / 60 - update * rate / 60;
                        int sequenceStart = 33 * rate + update * 20;
                        allocationEvents += CountAllocationEvents(() =>
                        {
                            for (int index = 0; index < count; index++)
                                ObservePacket(sequenceStart + index);
                        });
                    }
                    samples.Sort();
                    TestContext.WriteLine(
                        new JObject
                        {
                            ["workload"] = workload,
                            ["rate_hz"] = rate,
                            ["contacts"] = touch ? 5 : 0,
                            ["samples"] = samples.Count,
                            ["bytes_per_packet"] = bytesAvailable ? new JValue(bytes / (31d * rate)) : JValue.CreateNull(),
                            ["allocation_events_per_packet"] = allocationEvents / (double)rate,
                            ["mean_us_per_packet"] = samples.Average(),
                            ["p95_us_per_packet"] = samples[(int)Math.Ceiling(samples.Count * 0.95) - 1],
                            ["emitted"] = emitted,
                            ["position_checksum"] = checksum,
                        }.ToString(Newtonsoft.Json.Formatting.None)
                    );
                }
            }
        }

        private static int CountAllocationEvents(Action action)
        {
            using var recorder = new ProfilerRecorder(ProfilerCategory.Memory, "GC.Alloc", 32768, ProfilerRecorderOptions.CollectOnlyOnCurrentThread);
            Assert.That(recorder.Valid, Is.True, "Native GC.Alloc instrumentation is unavailable.");
            recorder.Start();
            action();
            recorder.Stop();
            Assert.That(recorder.Count, Is.LessThan(recorder.Capacity), "Native allocation capture overflowed; never treat truncated samples as complete.");
            return recorder.Count;
        }
    }
}
#endif
