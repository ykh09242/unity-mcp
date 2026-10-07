#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using InputApi = UnityEngine.InputSystem.InputSystem;
using InputTouchPhase = UnityEngine.InputSystem.TouchPhase;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Queues input only on MCP-owned virtual devices. No physical device state is changed.</summary>
    [InitializeOnLoad]
    public sealed class InputSystemSimulationBackend : IInputSimulationBackend
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
        private bool _subscribed;

        private sealed class TouchContact
        {
            public Vector2 Position;
            public long ReleaseAt;
        }

        static InputSystemSimulationBackend() => ManageInput.InputBackend = new InputSystemSimulationBackend();

        public bool Available
        {
            get
            {
#if ENABLE_INPUT_SYSTEM
                return InputApi.settings.updateMode != InputSettings.UpdateMode.ProcessEventsManually;
#else
                return false;
#endif
            }
        }

        public string UnavailableReason =>
#if ENABLE_INPUT_SYSTEM
            "Manual Input System update mode is unsupported. Select Process Events In Dynamic Update or Fixed Update for bounded input simulation.";
#else
            "Enable Input System Package or Both in Project Settings > Player > Active Input Handling. Legacy UnityEngine.Input cannot be injected.";
#endif

        public object Status =>
            new
            {
                held_keys = _keys.Keys.Select(key => key.ToString()).OrderBy(key => key).ToArray(),
                held_mouse_buttons = _buttons.Keys.OrderBy(button => button).ToArray(),
                active_touch_ids = _touches.Keys.OrderBy(id => id).ToArray(),
                keyboard_device_id = _keyboard?.deviceId,
                mouse_device_id = _mouse?.deviceId,
                touchscreen_device_id = _touchscreen?.deviceId,
                update_mode = InputApi.settings.updateMode.ToString(),
                editor_input_behavior = InputApi.settings.editorInputBehaviorInPlayMode.ToString(),
                background_behavior = InputApi.settings.backgroundBehavior.ToString(),
                focus_note = "Input System project focus/background settings still apply. For unattended input configure Ignore Focus and All Device Input Always Goes To Game View. This tool does not change project settings or focus windows.",
                cleanup_in_seconds = _subscribed ? Math.Max(0, _deadline - EditorApplication.timeSinceStartup) : 0,
            };

        public object Apply(InputSimulationRequest request)
        {
            if (!Available)
                throw new ArgumentException(UnavailableReason);
            if (_subscribed && EditorApplication.timeSinceStartup >= _deadline)
                ReleaseAll();
            switch (request.Action)
            {
                case "key":
                    ApplyKey(request);
                    break;
                case "mouse":
                    ApplyMouse(request);
                    break;
                case "touch":
                    ApplyTouch(request);
                    break;
                default:
                    throw new ArgumentException("Unsupported raw input action.");
            }
            if (!_subscribed)
            {
                // A hard lease cannot be extended by repeated commands while the game loop is stalled.
                _deadline = EditorApplication.timeSinceStartup + InputSimulationRequest.DeviceTimeoutSeconds;
                InputApi.onBeforeUpdate += BeforeInputUpdate;
                EditorApplication.update += CheckTimeout;
                _subscribed = true;
            }
            return new SuccessResponse(
                "Queued simulated input for the next game input update.",
                new
                {
                    action = request.Action,
                    state = request.State,
                    frames = request.State == "release" || request.State == "move" ? 0 : request.Frames,
                    auto_cleanup_seconds = InputSimulationRequest.DeviceTimeoutSeconds,
                    simulation = Status,
                }
            );
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
                _keyboard = InputApi.AddDevice<Keyboard>("MCPVirtualKeyboard");
            }
            if (request.State == "release")
                _keys.Remove(key);
            else
                _keys[key] = _tick + request.Frames;
            QueueKeyboard();
        }

        private void ApplyMouse(InputSimulationRequest request)
        {
            if (_mouse == null || !_mouse.added)
            {
                _buttons.Clear();
                _previousMouse = Mouse.current;
                _mousePosition = _previousMouse?.position.ReadValue() ?? Vector2.zero;
                _mouse = InputApi.AddDevice<Mouse>("MCPVirtualMouse");
            }
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
                _touchscreen = InputApi.AddDevice<Touchscreen>("MCPVirtualTouchscreen");
            }
            Vector2 position = request.Position ?? contact.Position;
            InputTouchPhase phase;
            switch (request.State)
            {
                case "press":
                    phase = InputTouchPhase.Began;
                    _touches.Add(request.TouchId, new TouchContact { Position = position, ReleaseAt = _tick + request.Frames });
                    break;
                case "move":
                    phase = InputTouchPhase.Moved;
                    contact.Position = position;
                    // Moving does not extend the originally bounded contact duration.
                    break;
                default:
                    phase = InputTouchPhase.Ended;
                    _touches.Remove(request.TouchId);
                    break;
            }
            QueueTouch(request.TouchId, phase, position);
        }

        private void QueueKeyboard() => InputApi.QueueStateEvent(_keyboard, new KeyboardState(_keys.Keys.ToArray()));

        private void QueueMouse(Vector2 delta = default)
        {
            ushort buttons = 0;
            foreach (int button in _buttons.Keys)
                buttons |= (ushort)(1 << button);
            InputApi.QueueStateEvent(
                _mouse,
                new MouseState
                {
                    position = _mousePosition,
                    delta = delta,
                    buttons = buttons,
                }
            );
        }

        private void QueueTouch(int id, InputTouchPhase phase, Vector2 position) =>
            InputApi.QueueStateEvent(
                _touchscreen,
                new TouchState
                {
                    touchId = id,
                    phase = phase,
                    position = position,
                    pressure = phase == InputTouchPhase.Ended ? 0 : 1,
                }
            );

        private void BeforeInputUpdate()
        {
            InputUpdateType expected =
                InputApi.settings.updateMode == InputSettings.UpdateMode.ProcessEventsInFixedUpdate ? InputUpdateType.Fixed : InputUpdateType.Dynamic;
            if (InputState.currentUpdateType != expected)
                return;
            _tick++;
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
                    QueueTouch(id, InputTouchPhase.Ended, _touches[id].Position);
                _touches.Remove(id);
            }
        }

        private void CheckTimeout()
        {
            MaintainLifetime(EditorApplication.timeSinceStartup, EditorApplication.isPlaying);
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
                InputApi.onBeforeUpdate -= BeforeInputUpdate;
                EditorApplication.update -= CheckTimeout;
                _subscribed = false;
            }
            bool restoreKeyboard = _keyboard != null && Keyboard.current == _keyboard;
            bool restoreMouse = _mouse != null && Mouse.current == _mouse;
            bool restoreTouchscreen = _touchscreen != null && Touchscreen.current == _touchscreen;
            if (_keyboard != null && _keyboard.added)
                InputApi.RemoveDevice(_keyboard);
            if (_mouse != null && _mouse.added)
                InputApi.RemoveDevice(_mouse);
            if (_touchscreen != null && _touchscreen.added)
                InputApi.RemoveDevice(_touchscreen);
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
