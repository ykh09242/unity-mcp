using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.EditMode.Tools.PlayScenarios
{
    public sealed class PlayScenarioRawRecordingTests
    {
        private const string Menu = "Assets/Scenes/Menu.unity";

        private static bool Observe(PlayScenarioRecordingDraft draft, JObject input, long frame)
        {
            MethodInfo method = typeof(PlayScenarioRecordingDraft).GetMethod("InputObserved", new[] { typeof(JObject), typeof(long) });
            Assert.IsNotNull(method, "The recorder does not yet accept observed executable raw input.");
            return (bool)method.Invoke(draft, new object[] { input, frame });
        }

        private static JObject Key(string state) =>
            new JObject
            {
                ["action"] = "key",
                ["key"] = "Space",
                ["state"] = state,
            };

        private static JObject Mouse(string state, int x) =>
            new JObject
            {
                ["action"] = "mouse",
                ["button"] = "left",
                ["state"] = state,
                ["position"] = new JArray(x, 20),
            };

        private static JArray Steps(PlayScenarioRecordingDraft draft) => (JArray)draft.Snapshot()["scenario"]["steps"];

        [Test]
        public void ObservedKeyHoldDragScrollAndScene_PreserveOrderAndInputUpdateFrames()
        {
            // Given observed game-input update numbers, including an overlapping key hold and drag.
            var draft = new PlayScenarioRecordingDraft("raw-order", Menu);
            Assert.IsTrue(Observe(draft, Key("press"), 10));
            Assert.IsTrue(Observe(draft, Mouse("press", 10), 12));
            Assert.IsTrue(Observe(draft, Mouse("move", 30), 14));
            Assert.IsTrue(Observe(draft, Mouse("release", 50), 16));
            Assert.IsTrue(Observe(draft, Key("release"), 18));
            Assert.IsTrue(Observe(draft, new JObject { ["action"] = "wheel", ["delta"] = new JArray(0, -120) }, 20));
            // When a later scene transition is actually observed, then no outcome assertions are inferred.
            Assert.IsTrue(draft.SceneChanged("Assets/Scenes/Game.unity"));
            JArray steps = Steps(draft);
            CollectionAssert.AreEqual(
                new[] { "load_scene", "input", "input", "input", "input", "input", "input", "wait_scene" },
                steps.Select(x => (string)x["action"])
            );
            Assert.AreEqual(8, steps[1]["input"].Value<int>("frames"));
            Assert.AreEqual(4, steps[2]["input"].Value<int>("frames"));
            Assert.AreEqual(2, steps[1].Value<int>("wait_frames"));
            Assert.AreEqual(2, steps[3].Value<int>("wait_frames"));
            Assert.AreEqual("release", (string)steps[4]["input"]["state"]);
            Assert.AreEqual(-120, steps[6]["input"]["delta"][1].Value<int>());
            Assert.IsTrue(draft.Definition().Draft);
            Assert.IsTrue(draft.Snapshot().Value<bool>("requires_review"));
            Assert.IsFalse(steps.OfType<JObject>().Any(x => x["equals"] != null || x["property"] != null || x["screenshot"] != null));
        }

        [Test]
        public void ObservedTouchContact_MovementDoesNotRestartHoldDeadline()
        {
            var draft = new PlayScenarioRecordingDraft("raw-touch", Menu);
            foreach (var value in new[] { ("press", 4L, 10), ("move", 7L, 20), ("release", 9L, 30) })
                Assert.IsTrue(
                    Observe(
                        draft,
                        new JObject
                        {
                            ["action"] = "touch",
                            ["touch_id"] = 2,
                            ["state"] = value.Item1,
                            ["position"] = new JArray(value.Item3, 40),
                        },
                        value.Item2
                    )
                );
            JArray steps = Steps(draft);
            Assert.AreEqual(5, steps[1]["input"].Value<int>("frames"));
            Assert.IsNull(steps[2]["input"]["frames"]);
            Assert.AreEqual(2, steps[3]["input"].Value<int>("touch_id"));
        }

        [Test]
        public void SameUpdateEvents_KeepArrivalOrderAndDiscloseMinimumReplayQuantization()
        {
            var draft = new PlayScenarioRecordingDraft("raw-same-update", Menu);
            Observe(draft, Key("press"), 4);
            Observe(draft, Mouse("press", 10), 4);
            Observe(draft, Key("release"), 4);
            JArray steps = Steps(draft);
            Assert.AreEqual("key", (string)steps[1]["input"]["action"]);
            Assert.AreEqual("mouse", (string)steps[2]["input"]["action"]);
            Assert.AreEqual("release", (string)steps[3]["input"]["state"]);
            Assert.AreEqual(1, steps[1].Value<int>("wait_frames"));
            Assert.IsTrue(draft.Snapshot()["diagnostics"].Any(x => ((string)x).Contains("same input update")));
        }

        [Test]
        public void EventCapsAndInterruptedDrafts_NeverAcceptLaterInputOrLoseReviewMarker()
        {
            var draft = new PlayScenarioRecordingDraft("raw-cap", Menu, 2);
            Observe(draft, Key("press"), 1);
            Observe(draft, Key("release"), 3);
            Assert.AreEqual("limit_reached", draft.Status);
            Assert.IsFalse(Observe(draft, Mouse("move", 5), 4));
            Assert.AreEqual(2, draft.Snapshot().Value<int>("event_count"));
            var reload = new PlayScenarioRecordingDraft("raw-reload", Menu);
            Observe(reload, Key("press"), 2);
            reload.Finish("interrupted", "assembly_reload");
            Assert.IsFalse(Observe(reload, Key("release"), 4));
            Assert.IsTrue(reload.Snapshot().Value<bool>("requires_review"));
            Assert.IsFalse(reload.Snapshot().Value<bool>("saved"));
        }

        [Test]
        public void InvalidOrUnobservedInput_IsOmittedWithoutManufacturingPressesOrDurations()
        {
            var draft = new PlayScenarioRecordingDraft("raw-invalid", Menu);
            Assert.IsFalse(Observe(draft, Key("release"), 1), "Release at recording start must not invent an earlier press.");
            Assert.IsFalse(
                Observe(
                    draft,
                    new JObject
                    {
                        ["action"] = "key",
                        ["key"] = "Space",
                        ["state"] = "press",
                        ["frames"] = 90,
                    },
                    2
                )
            );
            Assert.IsFalse(
                Observe(
                    draft,
                    new JObject
                    {
                        ["action"] = "mouse",
                        ["state"] = "move",
                        ["position"] = new JArray(true, 3),
                    },
                    3
                )
            );
            Assert.AreEqual(1, Steps(draft).Count);
            Assert.GreaterOrEqual(draft.Snapshot()["diagnostics"].Count(), 3);
        }

        [Test]
        public void ObservedFrameOverflow_StopsInsteadOfClampingAnUnrepresentableHold()
        {
            var draft = new PlayScenarioRecordingDraft("raw-overflow", Menu);
            Observe(draft, Key("press"), 1);
            Assert.IsFalse(Observe(draft, Key("release"), 36002));
            Assert.AreEqual("interrupted", draft.Status);
            Assert.AreEqual(1, draft.Snapshot().Value<int>("event_count"));
            Assert.IsTrue(draft.Snapshot()["diagnostics"].Any(x => ((string)x).Contains("36000")));
        }

        [UnityTearDown]
        public IEnumerator LeaveOwnedPlayMode()
        {
            if (Application.isPlaying)
                yield return new ExitPlayMode();
        }

        private sealed class RecordingSessionProbe : IPlayScenarioRecordingSession
        {
            internal bool Disposed;

            public void Refresh() { }

            public void Dispose()
            {
                Disposed = true;
            }
        }

        [Test]
        public void PauseAndReload_InterruptDraftAndDisposeObserverBeforeAnyLaterInput()
        {
            // Exercise the actual owner lifecycle with a test observer; this is not a physical Play Mode input claim.
            Type service = typeof(PlayScenarioRecordingService);
            FieldInfo draftField = service.GetField("draft", BindingFlags.NonPublic | BindingFlags.Static);
            FieldInfo sessionField = service.GetField("session", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNull(draftField.GetValue(null), "The owned native fixture must not replace an existing user recording.");
            Assert.IsNull(sessionField.GetValue(null));
            MethodInfo pause = service.GetMethod("PauseChanged", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(pause, "The recorder has no pause cleanup callback.");
            string previousSnapshot = SessionState.GetString("MCPForUnity.PlayScenarioRecording.Last", "");
            try
            {
                foreach (string reason in new[] { "editor_paused", "assembly_reload" })
                {
                    var draft = new PlayScenarioRecordingDraft("raw-lifecycle-" + reason, Menu);
                    Observe(draft, Key("press"), 1);
                    var observer = new RecordingSessionProbe();
                    draftField.SetValue(null, draft);
                    sessionField.SetValue(null, observer);
                    if (reason == "editor_paused")
                        pause.Invoke(null, new object[] { PauseState.Paused });
                    else
                        service.GetMethod("BeforeReload", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
                    Assert.IsTrue(observer.Disposed);
                    Assert.AreEqual("interrupted", draft.Status);
                    Assert.AreEqual(reason, draft.Reason);
                    Assert.IsFalse(Observe(draft, Key("release"), 3));
                    Assert.IsTrue(draft.Snapshot().Value<bool>("requires_review"));
                    Assert.IsFalse(draft.Saved);
                    draftField.SetValue(null, null);
                }
            }
            finally
            {
                service.GetMethod("Cleanup", BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, null);
                draftField.SetValue(null, null);
                sessionField.SetValue(null, null);
                SessionState.SetString("MCPForUnity.PlayScenarioRecording.Last", previousSnapshot);
            }
        }

        [UnityTest]
        public IEnumerator ActualMouseAndTouchPackets_ObserveDragWheelAndContactIdentity()
        {
            yield return new EnterPlayMode();
            Assert.IsTrue(Application.isPlaying, "Packet observation requires actual game input updates, not Editor-mode update injection.");
            ObserveMouseAndTouchPacketsAfterEntry();
        }

        private static object DiagnosticMember(object target, string name)
        {
            if (target == null)
                return "<null>";
            try
            {
                Type type = target as Type ?? target.GetType();
                BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | (target is Type ? BindingFlags.Static : BindingFlags.Instance);
                object instance = target is Type ? null : target;
                FieldInfo field = type.GetField(name, flags);
                if (field != null)
                    return field.GetValue(instance);
                PropertyInfo property = type.GetProperties(flags)
                    .FirstOrDefault(value => value.Name == name || value.Name.EndsWith("." + name, StringComparison.Ordinal));
                return property != null ? property.GetValue(instance) : "<unavailable>";
            }
            catch (Exception error)
            {
                return "<" + error.GetBaseException().GetType().Name + ">";
            }
        }

        private static JObject UpdateDiagnostic(Type api, object session, string stage)
        {
            var result = new JObject
            {
                ["stage"] = stage,
                ["application_playing"] = Application.isPlaying,
                ["editor_playing"] = EditorApplication.isPlaying,
                ["editor_paused"] = EditorApplication.isPaused,
                ["application_focused"] = Application.isFocused,
                ["session_type"] = session?.GetType().FullName,
            };
            void Values(string prefix, object target, params string[] names)
            {
                foreach (string name in names)
                {
                    object value = DiagnosticMember(target, name);
                    string text = value?.ToString();
                    result[prefix + name] = text == null ? JValue.CreateNull() : new JValue(text.Length > 200 ? text.Substring(0, 200) : text);
                }
            }
            object manager = DiagnosticMember(api, "s_Manager");
            object runtime = DiagnosticMember(manager, "m_Runtime");
            result["runtime_type"] = runtime?.GetType().FullName;
            Values("settings.", DiagnosticMember(api, "settings"), "updateMode", "backgroundBehavior", "editorInputBehaviorInPlayMode");
            Values("input_state.", api.Assembly.GetType("UnityEngine.InputSystem.LowLevel.InputState"), "currentUpdateType");
            Values(
                "manager.",
                manager,
                "defaultUpdateType",
                "gameIsPlaying",
                "gameHasFocus",
                "applicationHasFocus",
                "runPlayerUpdatesInEditMode",
                "updateMask",
                "m_CurrentUpdate"
            );
            Values("runtime.", runtime, "isInPlayMode", "isEditorPaused", "isPlayerFocused");
            Values("session.", session, "disposed", "UpdateCount", "<UpdateCount>k__BackingField");
            Values("session_filter.", session?.GetType(), "GameUpdate");
            return result;
        }

        private sealed class OwnedPacketRoutingLease : IDisposable
        {
            private readonly object manager;
            private readonly FieldInfo counter;
            private readonly MethodInfo stop;
            private readonly int originalCount;
            private bool disposed;

            internal OwnedPacketRoutingLease(Type api)
            {
                Assert.IsTrue(Application.isPlaying, "The temporary routing lease is restricted to the owned Play Mode packet fixture.");
                manager = DiagnosticMember(api, "s_Manager");
                Assert.IsNotNull(manager);
                Type type = manager.GetType();
                counter = type.GetField("m_EditorEventPassthroughCount", BindingFlags.Instance | BindingFlags.NonPublic);
                MethodInfo start = type.GetMethod("StartEditorEventPassthrough", BindingFlags.Static | BindingFlags.NonPublic);
                stop = type.GetMethod("StopEditorEventPassthrough", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(counter, "Installed package must expose its replay routing refcount for fixture restoration proof.");
                Assert.IsNotNull(start);
                Assert.IsNotNull(stop);
                originalCount = (int)counter.GetValue(manager);
                start.Invoke(null, null);
                try
                {
                    Assert.AreEqual(originalCount + 1, (int)counter.GetValue(manager));
                }
                catch
                {
                    stop.Invoke(null, null);
                    throw;
                }
            }

            public void Dispose()
            {
                if (disposed)
                    return;
                disposed = true;
                try
                {
                    stop.Invoke(null, null);
                }
                finally
                {
                    Assert.AreEqual(
                        originalCount,
                        (int)counter.GetValue(manager),
                        "Owned packet routing lease must restore the original process-wide refcount on success or failure."
                    );
                }
            }
        }

        private static void VerifyRoutingLeaseFailureRestoresRefcount(Type api)
        {
            Assert.Throws<InvalidOperationException>(() =>
            {
                using (var lease = new OwnedPacketRoutingLease(api))
                    throw new InvalidOperationException("Owned fixture routing cleanup probe.");
            });
        }

        private static Action DynamicDriver(Type api)
        {
            Assert.IsTrue(Application.isPlaying);
            Type updateType = api.Assembly.GetType("UnityEngine.InputSystem.LowLevel.InputUpdateType");
            Assert.IsNotNull(updateType);
            MethodInfo method = api.GetMethod("Update", BindingFlags.Static | BindingFlags.NonPublic, null, new[] { updateType }, null);
            Assert.IsNotNull(method, "The installed package's typed game-update driver must exist for the owned native fixture.");
            object dynamic = Enum.Parse(updateType, "Dynamic");
            return () => method.Invoke(null, new[] { dynamic });
        }

        private static void RequireObservedGameUpdate(Type api, object session, Action update)
        {
            var snapshots = new List<JObject> { UpdateDiagnostic(api, session, "before_dynamic_update") };
            int callbacks = 0;
            Action probe = () =>
            {
                if (++callbacks <= 2)
                    snapshots.Add(UpdateDiagnostic(api, session, "actual_onBeforeUpdate"));
            };
            EventInfo before = api.GetEvent("onBeforeUpdate", BindingFlags.Public | BindingFlags.Static);
            Assert.IsNotNull(before, "The installed public input update event must be available for the bounded diagnostic.");
            before.AddEventHandler(null, probe);
            try
            {
                update();
            }
            finally
            {
                before.RemoveEventHandler(null, probe);
            }
            JObject after = UpdateDiagnostic(api, session, "after_dynamic_update");
            after["diagnostic_callback_count"] = callbacks;
            if ((long)session.GetType().GetProperty("UpdateCount").GetValue(session) <= 0)
                foreach (JObject snapshot in snapshots.Concat(new[] { after }))
                    Debug.Log("[PLAY12 input diagnostic] " + snapshot.ToString(Newtonsoft.Json.Formatting.None));
            Assert.Greater(
                (long)session.GetType().GetProperty("UpdateCount").GetValue(session),
                0L,
                "The fixture must reach a real game Dynamic/Fixed update before injecting state. " + after.ToString(Newtonsoft.Json.Formatting.None)
            );
        }

        // Keep captured reflection/device/delegate locals out of the reload-spanning iterator state machine.
        private static void ObserveMouseAndTouchPacketsAfterEntry()
        {
            // Actual installed-package state-event delivery, using owned test-injected devices rather than hardware.
            Type api = AppDomain.CurrentDomain.GetAssemblies().Select(x => x.GetType("UnityEngine.InputSystem.InputSystem")).FirstOrDefault(x => x != null);
            Assert.IsNotNull(api, "Input System API type must be re-resolved after Play Mode entry.");
            Type backend = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(x => x.GetType("MCPForUnity.Editor.Tools.Input.InputSystemScenarioRecordingBackend"))
                .FirstOrDefault(x => x != null);
            Assert.IsNotNull(backend, "The optional raw recorder event observer is missing.");
            var draft = new PlayScenarioRecordingDraft("raw-native-pointer", Menu);
            MethodInfo begin = backend.GetMethod("Begin");
            Assert.IsNotNull(begin, "The re-resolved raw recorder backend must expose Begin after Play Mode entry.");
            object session = begin.Invoke(
                Activator.CreateInstance(backend),
                new object[] { (Action<JObject, long>)((input, frame) => Assert.IsTrue(Observe(draft, input, frame))), (Action<string>)draft.Diagnose }
            );
            Assert.IsNotNull(session, "The recorder must return an owned event session after Play Mode entry.");
            var devices = new List<object>();
            var previous = new List<object>();
            OwnedPacketRoutingLease routing = null;
            try
            {
                MethodInfo add = api.GetMethod("AddDevice", new[] { typeof(string), typeof(string), typeof(string) });
                Action update = DynamicDriver(api);
                RequireObservedGameUpdate(api, session, update);
                VerifyRoutingLeaseFailureRestoresRefcount(api);
                routing = new OwnedPacketRoutingLease(api);
                Action<object, object> queue = (device, state) =>
                {
                    MethodInfo method = api.GetMethods()
                        .Single(x => x.Name == "QueueStateEvent" && x.IsGenericMethodDefinition && x.GetParameters().Length == 3)
                        .MakeGenericMethod(state.GetType());
                    method.Invoke(null, new[] { device, state, (object)(-1d) });
                    update();
                };
                foreach (string layout in new[] { "Mouse", "Touchscreen" })
                {
                    Type type = api.Assembly.GetType("UnityEngine.InputSystem." + layout);
                    previous.Add(type.GetProperty("current").GetValue(null));
                    devices.Add(add.Invoke(null, new object[] { layout, "RecorderInjected_" + layout + Guid.NewGuid().ToString("N"), null }));
                }
                Type mouseState = api.Assembly.GetType("UnityEngine.InputSystem.LowLevel.MouseState");
                foreach (var value in new[] { (1, 10, 0), (1, 30, 0), (0, 50, 0), (0, 50, -120) })
                {
                    object state = Activator.CreateInstance(mouseState);
                    mouseState.GetField("position").SetValue(state, new Vector2(value.Item2, 20));
                    mouseState.GetField("buttons").SetValue(state, (ushort)value.Item1);
                    mouseState.GetField("scroll").SetValue(state, new Vector2(0, value.Item3));
                    queue(devices[0], state);
                }
                Type touchState = api.Assembly.GetType("UnityEngine.InputSystem.LowLevel.TouchState");
                Type phase = api.Assembly.GetType("UnityEngine.InputSystem.TouchPhase");
                foreach (var value in new[] { ("Began", 10), ("Moved", 30), ("Ended", 50) })
                {
                    object state = Activator.CreateInstance(touchState);
                    touchState.GetField("touchId").SetValue(state, 42);
                    touchState.GetField("position").SetValue(state, new Vector2(value.Item2, 40));
                    touchState.GetProperty("phase").SetValue(state, Enum.Parse(phase, value.Item1));
                    queue(devices[1], state);
                }
                JArray steps = Steps(draft);
                Assert.AreEqual(8, steps.Count, draft.Snapshot().ToString());
                CollectionAssert.AreEqual(
                    new[] { "mouse", "mouse", "mouse", "wheel", "touch", "touch", "touch" },
                    steps.Skip(1).Select(x => (string)x["input"]["action"])
                );
                Assert.AreEqual(2, steps[1]["input"].Value<int>("frames"));
                Assert.AreEqual(2, steps[5]["input"].Value<int>("frames"));
                CollectionAssert.AreEqual(
                    new[] { 1, 1, 1 },
                    steps.Skip(5).Select(x => x["input"].Value<int>("touch_id")),
                    "An arbitrary source contact ID must map to one stable executable slot."
                );
                Assert.IsTrue(draft.Snapshot().Value<bool>("requires_review"));
            }
            finally
            {
                try
                {
                    ((IDisposable)session).Dispose();
                    MethodInfo remove = api.GetMethod("RemoveDevice", new[] { api.Assembly.GetType("UnityEngine.InputSystem.InputDevice") });
                    foreach (object device in devices)
                        remove.Invoke(null, new[] { device });
                    foreach (object device in previous)
                        device?.GetType().GetMethod("MakeCurrent").Invoke(device, null);
                }
                finally
                {
                    routing?.Dispose();
                }
            }
        }

        [UnityTest]
        public IEnumerator ActualInputSystemEvents_ObserveInjectedStateAndDetachWithoutCapturingMcpSimulation()
        {
            yield return new EnterPlayMode();
            Assert.IsTrue(Application.isPlaying, "Packet observation requires actual game input updates, not Editor-mode update injection.");
            ObserveKeyboardPacketsAfterEntry();
        }

        // Keep captured reflection/device/delegate locals out of the reload-spanning iterator state machine.
        private static void ObserveKeyboardPacketsAfterEntry()
        {
            // This exercises actual package onEvent callbacks with explicitly test-injected devices; it is not physical hardware evidence.
            Type api = AppDomain.CurrentDomain.GetAssemblies().Select(x => x.GetType("UnityEngine.InputSystem.InputSystem")).FirstOrDefault(x => x != null);
            Assert.IsNotNull(api, "Root must enable the installed Input System package for native observation verification.");
            Type backend = AppDomain
                .CurrentDomain.GetAssemblies()
                .Select(x => x.GetType("MCPForUnity.Editor.Tools.Input.InputSystemScenarioRecordingBackend"))
                .FirstOrDefault(x => x != null);
            Assert.IsNotNull(backend, "The optional raw recorder event observer is missing.");
            var events = new List<JObject>();
            var diagnostics = new List<string>();
            MethodInfo begin = backend.GetMethod("Begin");
            Assert.IsNotNull(begin, "The re-resolved raw recorder backend must expose Begin after Play Mode entry.");
            object session = begin.Invoke(
                Activator.CreateInstance(backend),
                new object[] { (Action<JObject, long>)((input, frame) => events.Add((JObject)input.DeepClone())), (Action<string>)diagnostics.Add }
            );
            Assert.IsNotNull(session, "The recorder must return an owned event session after Play Mode entry.");
            object keyboard = null,
                excluded = null;
            Type keyboardType = api.Assembly.GetType("UnityEngine.InputSystem.Keyboard");
            object previousKeyboard = keyboardType.GetProperty("current").GetValue(null);
            OwnedPacketRoutingLease routing = null;
            try
            {
                MethodInfo add = api.GetMethod("AddDevice", new[] { typeof(string), typeof(string), typeof(string) });
                keyboard = add.Invoke(null, new object[] { "Keyboard", "RecorderInjected_" + Guid.NewGuid().ToString("N"), null });
                excluded = add.Invoke(null, new object[] { "Keyboard", "MCPVirtualKeyboard_RecorderTest", null });
                Type state = api.Assembly.GetType("UnityEngine.InputSystem.LowLevel.KeyboardState");
                Type key = api.Assembly.GetType("UnityEngine.InputSystem.Key");
                Array pressed = Array.CreateInstance(key, 1);
                pressed.SetValue(Enum.Parse(key, "Space"), 0);
                Array released = Array.CreateInstance(key, 0);
                MethodInfo queue = api.GetMethods()
                    .Single(x => x.Name == "QueueStateEvent" && x.IsGenericMethodDefinition && x.GetParameters().Length == 3)
                    .MakeGenericMethod(state);
                Action update = DynamicDriver(api);
                RequireObservedGameUpdate(api, session, update);
                VerifyRoutingLeaseFailureRestoresRefcount(api);
                routing = new OwnedPacketRoutingLease(api);
                queue.Invoke(null, new[] { keyboard, Activator.CreateInstance(state, new object[] { pressed }), (object)(-1d) });
                queue.Invoke(null, new[] { excluded, Activator.CreateInstance(state, new object[] { pressed }), (object)(-1d) });
                update();
                update();
                queue.Invoke(null, new[] { keyboard, Activator.CreateInstance(state, new object[] { released }), (object)(-1d) });
                update();
                Assert.AreEqual(2, events.Count, new JObject { ["observed"] = new JArray(events), ["diagnostics"] = new JArray(diagnostics) }.ToString());
                Assert.AreEqual("press", events[0].Value<string>("state"));
                Assert.AreEqual("release", events[1].Value<string>("state"));
                Assert.AreEqual("Space", events[0].Value<string>("key"));
                Assert.IsTrue(
                    diagnostics.Any(x => x.Contains("test-injected") || x.Contains("non-native")),
                    "Device provenance must not be represented as physical input proof."
                );
                ((IDisposable)session).Dispose();
                queue.Invoke(null, new[] { keyboard, Activator.CreateInstance(state, new object[] { pressed }), (object)(-1d) });
                update();
                Assert.AreEqual(2, events.Count, "Disposed recorder subscriptions must never capture more input.");
            }
            finally
            {
                try
                {
                    ((IDisposable)session).Dispose();
                    MethodInfo remove = api.GetMethod("RemoveDevice", new[] { api.Assembly.GetType("UnityEngine.InputSystem.InputDevice") });
                    if (keyboard != null)
                        remove.Invoke(null, new[] { keyboard });
                    if (excluded != null)
                        remove.Invoke(null, new[] { excluded });
                    previousKeyboard?.GetType().GetMethod("MakeCurrent").Invoke(previousKeyboard, null);
                }
                finally
                {
                    routing?.Dispose();
                }
            }
        }
    }
}
