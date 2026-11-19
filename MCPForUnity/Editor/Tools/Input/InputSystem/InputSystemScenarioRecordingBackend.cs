#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.Experimental.Input;
using UnityEngine.Experimental.Input.LowLevel;
using UnityEngine.Experimental.Input.Controls;
using UnityEngine.Experimental.Input.Utilities;
using UnityEngine.InputSystem.Utilities;

namespace UnityEngine.Experimental.Input.LowLevel { }

namespace UnityEngine.Experimental.Input.Controls { }

namespace UnityEngine.Experimental.Input.Utilities { }

namespace UnityEngine.InputSystem.LowLevel { }

namespace UnityEngine.InputSystem.Controls { }

namespace UnityEngine.InputSystem.Utilities { }

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Observes delivered Input System state packets; never queues input or modifies devices/settings.</summary>
    [InitializeOnLoad]
    public sealed class InputSystemScenarioRecordingBackend : IPlayScenarioInputRecordingBackend, IPlayScenarioInputRecordingCapabilities
    {
        static InputSystemScenarioRecordingBackend()
        {
#if ENABLE_INPUT_SYSTEM
            PlayScenarioRecordingService.InputBackend = new InputSystemScenarioRecordingBackend();
#endif
        }

        public JObject Capabilities =>
            new JObject
            {
                ["backend"] = "input_system",
                ["source"] = "UnityEngine.InputSystem",
                ["frame_clock"] = "game_input_update",
                ["keyboard"] = true,
                ["mouse"] = true,
                ["touch"] = true,
                ["wheel"] = true,
                ["gamepad"] = false,
                ["raw_replay_available"] = new RuntimeInputSystemSimulationBackend().Available,
                ["raw_replay_scope"] = "Input System consumers only; virtual devices do not change UnityEngine.Input in Both mode.",
            };

        public IPlayScenarioInputRecordingSession Begin(Action<JObject, long> observed, Action<string> diagnose)
        {
            if (InputSystem.settings.updateMode == InputSettings.UpdateMode.ProcessEventsManually)
                throw new InvalidOperationException("Raw recording requires game Dynamic/Fixed Input System updates; manual update mode is unsupported.");
            return new Session(observed, diagnose);
        }

        private sealed class Session : IPlayScenarioInputRecordingSession
        {
            private readonly Action<JObject, long> observed;
            private readonly Action<string> diagnose;
            private readonly Dictionary<InputControl, float> buttons = new Dictionary<InputControl, float>();
            private readonly HashSet<InputControl> observedMouseButtons = new HashSet<InputControl>();
            private readonly Dictionary<int, int> touchSlots = new Dictionary<int, int>();
            private readonly Dictionary<int, Vector2> touchPositions = new Dictionary<int, Vector2>();
            private readonly HashSet<InputDevice> described = new HashSet<InputDevice>();
            private static readonly Func<Touchscreen, ReadOnlyArray<TouchControl>> ReadTouches = BindTouches();

            private static Func<Touchscreen, ReadOnlyArray<TouchControl>> BindTouches()
            {
                var property = typeof(Touchscreen).GetProperty("touches") ?? typeof(Touchscreen).GetProperty("allTouchControls");
                return property?.PropertyType == typeof(ReadOnlyArray<TouchControl>)
                    ? (Func<Touchscreen, ReadOnlyArray<TouchControl>>)
                        property.GetGetMethod().CreateDelegate(typeof(Func<Touchscreen, ReadOnlyArray<TouchControl>>))
                    : null;
            }

            private readonly HashSet<int> visitedTouches = new HashSet<int>();
            private bool processingTouches;
            private readonly HashSet<string> deviceLimitNotices = new HashSet<string>();
            private Keyboard keyboard;
            private Mouse mouse;
            private Touchscreen touchscreen;
            private Vector2? mousePosition;
            private int eventsInUpdate;
            private bool disposed;
            public long UpdateCount { get; private set; }

            internal Session(Action<JObject, long> observed, Action<string> diagnose)
            {
                this.observed = observed ?? throw new ArgumentNullException(nameof(observed));
                this.diagnose = diagnose ?? throw new ArgumentNullException(nameof(diagnose));
                InputSystem.onBeforeUpdate += BeforeUpdate;
                InputSystem.onEvent += Event;
            }

            private bool gameUpdate;

            private void BeforeUpdate() => BeforeUpdate(InputSystemCompatibility.CurrentUpdateType);

            private void BeforeUpdate(InputUpdateType update)
            {
                gameUpdate = update == InputUpdateType.Dynamic || update == InputUpdateType.Fixed;
                if (disposed || !gameUpdate || EditorApplication.isPaused)
                    return;
                UpdateCount++;
                eventsInUpdate = 0;
            }

            private bool Select(InputDevice device)
            {
                if (device.name.StartsWith("MCPVirtual", StringComparison.Ordinal))
                    return false;
                if (device is Keyboard selectedKeyboard)
                {
                    if (keyboard != null && keyboard != selectedKeyboard)
                        return OtherDevice("keyboard");
                    keyboard = selectedKeyboard;
                }
                else if (device is Mouse selectedMouse)
                {
                    if (mouse != null && mouse != selectedMouse)
                        return OtherDevice("mouse");
                    mouse = selectedMouse;
                }
                else if (device is Touchscreen selectedTouchscreen)
                {
                    if (touchscreen != null && touchscreen != selectedTouchscreen)
                        return OtherDevice("touchscreen");
                    touchscreen = selectedTouchscreen;
                }
                else
                    return false;
                if (described.Add(device))
                    diagnose(
                        device.native
                            ? "Observed native Input System device state events; gameplay outcomes are not inferred."
                            : "Observed non-native/test-injected Input System device state events; this is not proof of physical hardware input."
                    );
                return true;
            }

            private bool OtherDevice(string kind)
            {
                if (deviceLimitNotices.Add(kind))
                    diagnose("Raw recording supports one " + kind + " device per session; additional devices were omitted without merging identities.");
                return false;
            }

            private void Emit(JObject value)
            {
                if (disposed)
                    return;
                if (++eventsInUpdate > 256)
                {
                    diagnose("Raw observation exceeded 256 transitions in one input update; additional transitions were omitted.");
                    return;
                }
                observed(value, UpdateCount);
            }

            private bool Changed(ButtonControl control, InputEventPtr packet, out bool pressed)
            {
                pressed = false;
                if (!control.ReadValueFromEvent(packet, out float value))
                    return false;
                bool previous = buttons.TryGetValue(control, out float old) ? old >= control.pressPointOrDefault : control.isPressed;
                pressed = value >= control.pressPointOrDefault;
                buttons[control] = value;
                return previous != pressed;
            }

            private static unsafe bool MatchesDeviceFormat(InputDevice device, InputEventPtr packet)
            {
                if (packet.IsA<StateEvent>())
                    return packet.sizeInBytes >= sizeof(StateEvent) && StateEvent.From(packet)->stateFormat == device.stateBlock.format;
                return packet.IsA<DeltaStateEvent>()
                    && packet.sizeInBytes >= sizeof(DeltaStateEvent)
                    && DeltaStateEvent.From(packet)->stateFormat == device.stateBlock.format;
            }

            private void Event(InputEventPtr packet) => Event(packet, InputSystem.GetDeviceById(packet.deviceId));

            private void Event(InputEventPtr packet, InputDevice device)
            {
                if (
                    disposed
                    || EditorApplication.isPaused
                    || !gameUpdate
                    || (!packet.IsA<StateEvent>() && !packet.IsA<DeltaStateEvent>())
                    || device == null
                    || !Select(device)
                )
                    return;
                if (device is Keyboard keys && MatchesDeviceFormat(keys, packet))
                {
                    foreach (var control in keys.allControls)
                    {
                        if (disposed)
                            return;
                        if (control is KeyControl key && Changed(key, packet, out bool pressed))
                            Emit(
                                new JObject
                                {
                                    ["action"] = "key",
                                    ["key"] = key.keyCode.ToString(),
                                    ["state"] = pressed ? "press" : "release",
                                }
                            );
                    }
                }
                else if (device is Mouse pointer && MatchesDeviceFormat(pointer, packet))
                    MouseEvent(pointer, packet);
                else if (device is Touchscreen touches)
                    TouchEvent(touches, packet);
            }

            private static JArray Position(Vector2 value) => new JArray(value.x, value.y);

            private static bool ValidPosition(Vector2 value) =>
                !float.IsNaN(value.x)
                && !float.IsInfinity(value.x)
                && !float.IsNaN(value.y)
                && !float.IsInfinity(value.y)
                && value.x >= 0
                && value.y >= 0
                && value.x <= 1000000
                && value.y <= 1000000;

            private void MouseEvent(Mouse pointer, InputEventPtr packet)
            {
                if (disposed)
                    return;
                Vector2 position = pointer.position.ReadValueFromEvent(packet, out Vector2 supplied)
                    ? supplied
                    : (mousePosition ?? pointer.position.ReadValue());
                bool valid = ValidPosition(position);
                Vector2? previousPosition = mousePosition;
                if (valid)
                    mousePosition = position;
                else
                {
                    diagnose("An observed mouse position is outside the executable screen-pixel range and was omitted.");
                }
                bool transition = false;

                for (int i = 0; i < 3 && !disposed; i++)
                {
                    ButtonControl control =
                        i == 0 ? pointer.leftButton
                        : i == 1 ? pointer.rightButton
                        : pointer.middleButton;
                    if (Changed(control, packet, out bool pressed))
                    {
                        transition = true;
                        if (pressed)
                        {
                            if (!valid)
                                continue;
                            observedMouseButtons.Add(control);
                        }
                        else if (!observedMouseButtons.Remove(control))
                            continue;
                        if (!valid)
                        {
                            if (!previousPosition.HasValue)
                                continue;
                            position = previousPosition.Value;
                            diagnose("An observed mouse release used the last valid position because its supplied position was invalid.");
                        }
                        Emit(
                            new JObject
                            {
                                ["action"] = "mouse",
                                ["button"] =
                                    i == 0 ? "left"
                                    : i == 1 ? "right"
                                    : "middle",
                                ["state"] = pressed ? "press" : "release",
                                ["position"] = Position(position),
                            }
                        );
                    }
                }
                if (valid && !transition && (!previousPosition.HasValue ? position != pointer.position.ReadValue() : position != previousPosition.Value))
                    Emit(
                        new JObject
                        {
                            ["action"] = "mouse",
                            ["button"] = "left",
                            ["state"] = "move",
                            ["position"] = Position(position),
                        }
                    );
                if (disposed)
                    return;
                if (pointer.scroll.ReadValueFromEvent(packet, out Vector2 scroll) && scroll != Vector2.zero)
                {
                    if (
                        !float.IsNaN(scroll.x)
                        && !float.IsInfinity(scroll.x)
                        && !float.IsNaN(scroll.y)
                        && !float.IsInfinity(scroll.y)
                        && Math.Abs(scroll.x) <= 10000
                        && Math.Abs(scroll.y) <= 10000
                    )
                        Emit(new JObject { ["action"] = "wheel", ["delta"] = Position(scroll) });
                    else
                        diagnose("Observed wheel delta exceeded the finite executable range and was omitted.");
                }
            }

            private unsafe void TouchEvent(Touchscreen device, InputEventPtr packet)
            {
                if (disposed)
                    return;
                // Single-contact packets use the SDK's TouchState layout. Older packages
                // throw if asked to read touchscreen controls from this different format.
                if (packet.IsA<StateEvent>() && packet.sizeInBytes >= sizeof(StateEvent))
                {
                    var stateEvent = StateEvent.From(packet);
                    if (stateEvent->stateFormat == new FourCC('T', 'O', 'U', 'C'))
                    {
                        if (stateEvent->stateSizeInBytes >= sizeof(TouchState))
                        {
                            TouchState state = *(TouchState*)stateEvent->state;
                            var transition = InputSystemCompatibility.ReadTouchPhase(state.phase);
                            if (state.touchId > 0 && transition != InputSystemCompatibility.TouchTransition.Ignore)
                                ObserveTouch(state.touchId, transition, state.position);
                        }
                        return;
                    }
                }
                if (!MatchesDeviceFormat(device, packet))
                    return;
                // Observers may synchronously re-enter with a packet. Only the
                // outer handler borrows the session scratch set.
                bool ownsScratch = !processingTouches;
                var visited = ownsScratch ? visitedTouches : new HashSet<int>();
                if (ownsScratch)
                    processingTouches = true;
                visited.Clear();
                try
                {
                    var touches = ReadTouches?.Invoke(device) ?? default;
                    int count = ReadTouches == null ? device.allControls.Count : touches.Count;
                    for (int index = -1; index < count; index++)
                    {
                        TouchControl touch =
                            index < 0 ? device.primaryTouch
                            : ReadTouches == null ? device.allControls[index] as TouchControl
                            : touches[index];
                        if (disposed)
                            return;
                        if (touch == null || (index >= 0 && touch == device.primaryTouch))
                            continue;
                        if (!touch.touchId.ReadValueFromEvent(packet, out int id) || id <= 0 || !touch.phase.ReadValueFromEvent(packet, out var phase))
                            continue;
                        var transition = InputSystemCompatibility.ReadTouchPhase(phase);
                        if (transition == InputSystemCompatibility.TouchTransition.Ignore || !visited.Add(id))
                            continue;
                        Vector2 position = touch.position.ReadValueFromEvent(packet, out Vector2 supplied) ? supplied : touch.position.ReadValue();
                        ObserveTouch(id, transition, position);
                    }
                }
                finally
                {
                    visited.Clear();
                    if (ownsScratch)
                        processingTouches = false;
                }
            }

            private void ObserveTouch(int id, InputSystemCompatibility.TouchTransition transition, Vector2 position)
            {
                bool end = transition == InputSystemCompatibility.TouchTransition.Release;
                if (!ValidPosition(position))
                {
                    diagnose("An observed touch position is outside the executable screen-pixel range and was omitted.");
                    if (!end)
                        return;
                    // Terminal identity cleanup must not depend on coordinates.
                    if (!touchPositions.TryGetValue(id, out position))
                    {
                        touchSlots.Remove(id);
                        return;
                    }
                    diagnose("An observed touch release used the last valid position because its supplied position was invalid.");
                }
                if (transition == InputSystemCompatibility.TouchTransition.Press && !touchSlots.ContainsKey(id))
                {
                    int slot = 1;
                    while (slot <= 10 && touchSlots.ContainsValue(slot))
                        slot++;
                    if (slot > 10)
                    {
                        diagnose("Observed touches exceeded ten executable contacts; additional contacts were omitted.");
                        return;
                    }
                    touchSlots.Add(id, slot);
                }
                if (!touchSlots.TryGetValue(id, out int selected))
                {
                    diagnose("A touch already active before observation was omitted; no initial press was invented.");
                    return;
                }
                bool changed = !touchPositions.TryGetValue(id, out Vector2 previous) || position != previous;
                // Update ownership before notifying a consumer, which may dispose
                // the session synchronously. Never repopulate state after disposal.
                if (end)
                {
                    touchSlots.Remove(id);
                    touchPositions.Remove(id);
                }
                else
                    touchPositions[id] = position;
                if (end || changed)
                    Emit(
                        new JObject
                        {
                            ["action"] = "touch",
                            ["touch_id"] = selected,
                            ["state"] =
                                end ? "release"
                                : transition == InputSystemCompatibility.TouchTransition.Press ? "press"
                                : "move",
                            ["position"] = Position(position),
                        }
                    );
            }

            public void Dispose()
            {
                if (disposed)
                    return;
                disposed = true;
                InputSystem.onBeforeUpdate -= BeforeUpdate;
                InputSystem.onEvent -= Event;
                buttons.Clear();
                observedMouseButtons.Clear();
                mousePosition = null;
                touchSlots.Clear();
                touchPositions.Clear();
                described.Clear();
                visitedTouches.Clear();
                deviceLimitNotices.Clear();
            }
        }
    }
}
#endif
