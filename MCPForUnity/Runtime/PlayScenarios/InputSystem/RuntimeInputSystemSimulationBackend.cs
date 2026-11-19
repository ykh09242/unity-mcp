#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.Experimental.Input;
using UnityEngine.Experimental.Input.LowLevel;
using UnityEngine.Experimental.Input.Controls;
using UnityEngine.InputSystem.Controls;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Queues input only on MCP-owned virtual devices. No physical device state is changed.</summary>
    public partial class RuntimeInputSystemSimulationBackend
        : IScenarioInputSimulationBackend,
            IPlayerInputSimulationBackend,
            IPlayerInputSimulationCapabilities
    {
        private readonly Dictionary<Key, long> _keys = new Dictionary<Key, long>();
        private readonly Dictionary<int, long> _buttons = new Dictionary<int, long>();
        private readonly Dictionary<int, TouchContact> _touches = new Dictionary<int, TouchContact>();
        private Keyboard _keyboard;
        private Mouse _mouse;
        private Touchscreen _touchscreen;
        private Keyboard _previousKeyboard;
        private Mouse _previousMouse;
        private Touchscreen _previousTouchscreen;
        private Vector2 _mousePosition;
        private long _tick;
        private double _deadline;
        private double _maximumDeadline;
        private bool _subscribed;

        private sealed class TouchContact
        {
            public Vector2 Position;
            public long ReleaseAt;
        }

        public long UpdateCount => _tick;
        public bool LeaseActive => _subscribed && Clock < _deadline && Available;
        protected virtual double Clock => Time.realtimeSinceStartup;

        protected virtual void SubscribeLifetime() => Application.quitting += ReleaseAll;

        protected virtual void UnsubscribeLifetime() => Application.quitting -= ReleaseAll;

        public bool Available
        {
            get
            {
#if ENABLE_INPUT_SYSTEM
                return InputSystem.settings.updateMode != InputSettings.UpdateMode.ProcessEventsManually;
#else
                return false;
#endif
            }
        }

        public string UnavailableReason =>
#if ENABLE_INPUT_SYSTEM
            "Manual Input System update mode is unsupported. Select Process Events In Dynamic Update or Fixed Update for bounded input simulation.";
#else
            "Old Input Handling supports uGUI event dispatch and legacy input observation. UnityEngine.Input exposes no public raw-state injection API; virtual-device replay only affects Input System consumers. Use ui_click/click_ui for uGUI or review recorded raw steps for a compatible Input System consumer.";
#endif

        public object Status =>
            new Dictionary<string, object>
            {
                ["held_keys"] = _keys.Keys.Select(key => key.ToString()).OrderBy(key => key).ToArray(),
                ["held_mouse_buttons"] = _buttons.Keys.OrderBy(button => button).ToArray(),
                ["active_touch_ids"] = _touches.Keys.OrderBy(id => id).ToArray(),
                ["keyboard_device_id"] = InputSystemCompatibility.DeviceId(_keyboard),
                ["mouse_device_id"] = InputSystemCompatibility.DeviceId(_mouse),
                ["touchscreen_device_id"] = InputSystemCompatibility.DeviceId(_touchscreen),
                ["gamepad_device_id"] = InputSystemCompatibility.DeviceId(_gamepad),
                ["held_gamepad_controls"] = _gamepadReleases.Keys.OrderBy(control => control).ToArray(),
                ["paired_player_index"] = _pairedPlayer != null ? Index(_pairedPlayer) : null,
                ["player_diagnostics_available"] = CanInspectPlayer,
                ["player_pairing_available"] = CanPairPlayer,
                ["update_mode"] = InputSystem.settings.updateMode.ToString(),
#if UNITY_EDITOR
                ["editor_input_behavior"] = InputSystemCompatibility.Setting("editorInputBehaviorInPlayMode"),
#endif
                ["background_behavior"] = InputSystemCompatibility.Setting("backgroundBehavior"),
                ["focus_note"] =
                    "Input System project focus/background settings still apply. Inspect the focus and background options exposed by the installed Input System. This tool does not change project settings or focus windows.",
                ["cleanup_in_seconds"] = _subscribed ? Math.Max(0, _deadline - Clock) : 0,
            };

        public virtual object Apply(InputSimulationRequest request)
        {
            if (!Available)
                throw new ArgumentException(UnavailableReason);
            if (_subscribed && Clock >= _deadline)
                ReleaseAll();
            switch (request.Action)
            {
                case "key":
                    ApplyKey(request);
                    break;
                case "mouse":
                    ApplyMouse(request);
                    break;
                case "gamepad":
                    ApplyGamepad(request);
                    break;
                case "wheel":
                    EnsureMouse();
                    QueueMouse(scroll: request.WheelDelta.Value);
                    break;
                case "touch":
                    ApplyTouch(request);
                    break;
                default:
                    throw new ArgumentException("Unsupported raw input action.");
            }
            EnsureLease();
            return new Dictionary<string, object>
            {
                ["action"] = request.Action,
                ["state"] = request.State,
                ["frames"] = request.Action == "wheel" || request.State == "release" || request.State == "move" ? 0 : request.Frames,
                ["auto_cleanup_seconds"] = InputSimulationRequest.DeviceTimeoutSeconds,
                ["maximum_active_seconds"] = InputSimulationRequest.MaximumActiveSeconds,
                ["simulation"] = Status,
            };
        }

        private void EnsureLease()
        {
            if (!_subscribed)
            {
                // Commands alone cannot renew the inactivity lease; only actual held-input updates can.
                _deadline = Clock + InputSimulationRequest.DeviceTimeoutSeconds;
                _maximumDeadline = Clock + InputSimulationRequest.MaximumActiveSeconds;
                InputSystem.onBeforeUpdate += BeforeInputUpdate;
                SubscribeLifetime();
                _subscribed = true;
            }
        }

        private void ApplyKey(InputSimulationRequest request)
        {
            // TryParse also accepts numeric strings; require the named enum spelling.
            if (
                !Enum.TryParse(request.Key, true, out Key key)
                || key == Key.None
                || !Enum.GetNames(typeof(Key)).Any(name => string.Equals(name, request.Key, StringComparison.OrdinalIgnoreCase))
            )
                throw new ArgumentException("Unknown Input System Key name. Use names such as A, Space, Enter or LeftShift.");
            if (_keyboard == null || !_keyboard.added)
            {
                _keys.Clear();
                _previousKeyboard = Keyboard.current;
                _keyboard = InputSystem.AddDevice<Keyboard>("MCPVirtualKeyboard");
            }
            if (request.State == "release")
                _keys.Remove(key);
            else
                _keys[key] = _tick + request.Frames;
            QueueKeyboard();
        }

        private void EnsureMouse()
        {
            if (_mouse == null || !_mouse.added)
            {
                _buttons.Clear();
                _previousMouse = Mouse.current;
                _mousePosition = _previousMouse?.position.ReadValue() ?? Vector2.zero;
                _mouse = InputSystem.AddDevice<Mouse>("MCPVirtualMouse");
            }
        }

        private void ApplyMouse(InputSimulationRequest request)
        {
            EnsureMouse();
            Vector2 previousPosition = _mousePosition;
            if (request.Position.HasValue)
                _mousePosition = request.Position.Value;
            int button =
                request.Button == "left" ? 0
                : request.Button == "right" ? 1
                : 2;
            switch (request.State)
            {
                case "press":
                    _buttons[button] = _tick + request.Frames;
                    break;
                case "release":
                    _buttons.Remove(button);
                    break;
                case "move":
                    break;
            }
            QueueMouse(_mousePosition - previousPosition);
        }

        private void ApplyTouch(InputSimulationRequest request)
        {
            bool exists = _touches.TryGetValue(request.TouchId, out TouchContact contact);
            if (request.State == "press" && exists)
                throw new ArgumentException("touch_id is already pressed. Use state move or release.");
            if (request.State != "press" && !exists)
                throw new ArgumentException("touch_id is not active. Press the contact before moving or releasing it.");
            if (_touchscreen == null || !_touchscreen.added)
            {
                if (exists)
                    throw new InvalidOperationException("The MCP touchscreen was removed. Press the contact again.");
                _previousTouchscreen = Touchscreen.current;
                _touchscreen = InputSystem.AddDevice<Touchscreen>("MCPVirtualTouchscreen");
            }
            Vector2 position = request.Position ?? contact.Position;
            InputSystemCompatibility.TouchTransition phase;
            switch (request.State)
            {
                case "press":
                    phase = InputSystemCompatibility.TouchTransition.Press;
                    _touches.Add(request.TouchId, new TouchContact { Position = position, ReleaseAt = _tick + request.Frames });
                    break;
                case "move":
                    phase = InputSystemCompatibility.TouchTransition.Move;
                    contact.Position = position;
                    // Moving does not extend the originally bounded contact duration.
                    break;
                default:
                    phase = InputSystemCompatibility.TouchTransition.Release;
                    _touches.Remove(request.TouchId);
                    break;
            }
            QueueTouch(request.TouchId, phase, position);
        }

        private void QueueKeyboard()
        {
            using (StateEvent.From(_keyboard, out var packet))
            {
                foreach (var control in _keyboard.allControls)
                    if (control is KeyControl key)
                        key.WriteValueIntoEvent(_keys.ContainsKey(key.keyCode) ? 1f : 0f, packet);
                InputSystem.QueueEvent(packet);
            }
        }

        private void QueueMouse(Vector2 delta = default, Vector2 scroll = default)
        {
            using (StateEvent.From(_mouse, out var packet))
            {
                _mouse.position.WriteValueIntoEvent(_mousePosition, packet);
                _mouse.delta.WriteValueIntoEvent(delta, packet);
                _mouse.scroll.WriteValueIntoEvent(scroll, packet);
                _mouse.leftButton.WriteValueIntoEvent(_buttons.ContainsKey(0) ? 1f : 0f, packet);
                _mouse.rightButton.WriteValueIntoEvent(_buttons.ContainsKey(1) ? 1f : 0f, packet);
                _mouse.middleButton.WriteValueIntoEvent(_buttons.ContainsKey(2) ? 1f : 0f, packet);
                InputSystem.QueueEvent(packet);
            }
        }

        private void QueueTouch(int id, InputSystemCompatibility.TouchTransition phase, Vector2 position)
        {
            var state = new TouchState
            {
                touchId = id,
                position = position,
                pressure = phase == InputSystemCompatibility.TouchTransition.Release ? 0 : 1,
            };
            state.phase = InputSystemCompatibility.WriteTouchPhase(state.phase, phase);
            InputSystem.QueueStateEvent(_touchscreen, state);
        }

        private void BeforeInputUpdate() => BeforeInputUpdate(InputSystemCompatibility.CurrentUpdateType);

        private void BeforeInputUpdate(InputUpdateType update)
        {
            if (!InputSystemCompatibility.IsSimulationUpdate(update))
                return;
            if (Clock >= _deadline || !Available)
            {
                ReleaseAll();
                return;
            }
            _tick++;
            ReleaseExpiredGamepadControls();
            // Queue release before update N+1, after N complete updates observed the held state.
            // Queueing from onAfterUpdate can lose events when the runtime commits its event buffer.
            Key[] keys = _keys.Where(pair => pair.Value < _tick).Select(pair => pair.Key).ToArray();
            foreach (Key key in keys)
                _keys.Remove(key);
            if (keys.Length > 0 && _keyboard != null && _keyboard.added)
                QueueKeyboard();
            int[] buttons = _buttons.Where(pair => pair.Value < _tick).Select(pair => pair.Key).ToArray();
            foreach (int button in buttons)
                _buttons.Remove(button);
            if (buttons.Length > 0 && _mouse != null && _mouse.added)
                QueueMouse();
            foreach (int id in _touches.Where(pair => pair.Value.ReleaseAt < _tick).Select(pair => pair.Key).ToArray())
            {
                if (_touchscreen != null && _touchscreen.added)
                    QueueTouch(id, InputSystemCompatibility.TouchTransition.Release, _touches[id].Position);
                _touches.Remove(id);
            }
            if (_keys.Count > 0 || _buttons.Count > 0 || _touches.Count > 0 || _gamepadReleases.Count > 0)
                _deadline = Math.Min(_maximumDeadline, Clock + InputSimulationRequest.DeviceTimeoutSeconds);
        }

        /// <summary>Apply the editor lifetime observation; separate from the clock for deterministic verification.</summary>
        public void MaintainLifetime(double editorTime, bool playing)
        {
            if (!playing || editorTime >= _deadline || !Available)
                ReleaseAll();
        }

        public void ReleaseAll()
        {
            // Removing owned devices cancels bound actions immediately, even while paused or reloading.
            if (_subscribed)
            {
                InputSystem.onBeforeUpdate -= BeforeInputUpdate;
                UnsubscribeLifetime();
                _subscribed = false;
            }
            RestorePlayerPairing();
            ReleaseGamepad();
            bool restoreKeyboard = _keyboard != null && Keyboard.current == _keyboard;
            bool restoreMouse = _mouse != null && Mouse.current == _mouse;
            bool restoreTouchscreen = _touchscreen != null && Touchscreen.current == _touchscreen;
            if (_keyboard != null && _keyboard.added)
                InputSystem.RemoveDevice(_keyboard);
            if (_mouse != null && _mouse.added)
                InputSystem.RemoveDevice(_mouse);
            if (_touchscreen != null && _touchscreen.added)
                InputSystem.RemoveDevice(_touchscreen);
            if (restoreKeyboard && _previousKeyboard != null && _previousKeyboard.added)
                _previousKeyboard.MakeCurrent();
            if (restoreMouse && _previousMouse != null && _previousMouse.added)
                _previousMouse.MakeCurrent();
            if (restoreTouchscreen && _previousTouchscreen != null && _previousTouchscreen.added)
                _previousTouchscreen.MakeCurrent();
            _keyboard = null;
            _mouse = null;
            _touchscreen = null;
            _previousKeyboard = null;
            _previousMouse = null;
            _previousTouchscreen = null;
            _keys.Clear();
            _buttons.Clear();
            _touches.Clear();
            _tick = 0;
        }
    }
}
#endif
