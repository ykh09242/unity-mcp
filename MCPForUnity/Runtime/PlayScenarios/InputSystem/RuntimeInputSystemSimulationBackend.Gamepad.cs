#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using UnityEngine.Experimental.Input;
using UnityEngine.Experimental.Input.LowLevel;

namespace MCPForUnity.Editor.Tools.Input
{
    public partial class RuntimeInputSystemSimulationBackend
    {
        private Gamepad _gamepad;
        private Gamepad _previousGamepad;
        private Vector2 _leftStick;
        private Vector2 _rightStick;
        private float _leftTrigger;
        private float _rightTrigger;
        private readonly Dictionary<string, long> _gamepadReleases = new Dictionary<string, long>();
        private static readonly Dictionary<string, GamepadButton> GamepadButtons = new Dictionary<string, GamepadButton>
        {
            ["buttonSouth"] = GamepadButton.South,
            ["buttonNorth"] = GamepadButton.North,
            ["buttonEast"] = GamepadButton.East,
            ["buttonWest"] = GamepadButton.West,
            ["leftShoulder"] = GamepadButton.LeftShoulder,
            ["rightShoulder"] = GamepadButton.RightShoulder,
            ["leftStickPress"] = GamepadButton.LeftStick,
            ["rightStickPress"] = GamepadButton.RightStick,
            ["start"] = GamepadButton.Start,
            ["select"] = GamepadButton.Select,
            ["dpadUp"] = GamepadButton.DpadUp,
            ["dpadDown"] = GamepadButton.DpadDown,
            ["dpadLeft"] = GamepadButton.DpadLeft,
            ["dpadRight"] = GamepadButton.DpadRight,
        };

        private Gamepad EnsureGamepad()
        {
            if (_gamepad == null || !_gamepad.added)
            {
                _gamepadReleases.Clear();
                ResetGamepadValues();
                _previousGamepad = Gamepad.current;
                _gamepad = InputSystem.AddDevice<Gamepad>("MCPVirtualGamepad");
            }
            return _gamepad;
        }

        private void ApplyGamepad(InputSimulationRequest request)
        {
            EnsureGamepad();
            bool pressed = request.State == "press";
            if (pressed)
                _gamepadReleases[request.Control] = _tick + request.Frames;
            else
                _gamepadReleases.Remove(request.Control);
            SetGamepadControl(request.Control, pressed ? request.ScalarValue ?? 1 : 0, pressed ? request.StickValue ?? Vector2.zero : Vector2.zero);
            QueueGamepad();
        }

        private void SetGamepadControl(string control, float scalar, Vector2 vector)
        {
            switch (control)
            {
                case "leftStick":
                    _leftStick = vector;
                    break;
                case "rightStick":
                    _rightStick = vector;
                    break;
                case "leftTrigger":
                    _leftTrigger = scalar;
                    break;
                case "rightTrigger":
                    _rightTrigger = scalar;
                    break;
                default:
                    if (!GamepadButtons.ContainsKey(control))
                        throw new ArgumentException("Unsupported gamepad control.");
                    break;
            }
        }

        private void ResetGamepadValues()
        {
            _leftStick = _rightStick = Vector2.zero;
            _leftTrigger = _rightTrigger = 0;
        }

        private void QueueGamepad()
        {
            // Some packages keep GamepadState internal. Fill the actual device layout,
            // including pending controls from earlier requests in the same input update.
            using (StateEvent.From(_gamepad, out var packet))
            {
                _gamepad.leftStick.WriteValueIntoEvent(_leftStick, packet);
                _gamepad.rightStick.WriteValueIntoEvent(_rightStick, packet);
                _gamepad.leftTrigger.WriteValueIntoEvent(_leftTrigger, packet);
                _gamepad.rightTrigger.WriteValueIntoEvent(_rightTrigger, packet);
                foreach (var button in GamepadButtons)
                    _gamepad[button.Value].WriteValueIntoEvent(_gamepadReleases.ContainsKey(button.Key) ? 1f : 0f, packet);
                InputSystem.QueueEvent(packet);
            }
        }

        private void ReleaseExpiredGamepadControls()
        {
            string[] expired = _gamepadReleases.Where(pair => pair.Value < _tick).Select(pair => pair.Key).ToArray();
            foreach (string control in expired)
            {
                _gamepadReleases.Remove(control);
                SetGamepadControl(control, 0, Vector2.zero);
            }
            if (expired.Length > 0 && _gamepad != null && _gamepad.added)
                QueueGamepad();
        }

        private void ReleaseGamepad()
        {
            bool restore = _gamepad != null && Gamepad.current == _gamepad;
            if (_gamepad != null && _gamepad.added)
                InputSystem.RemoveDevice(_gamepad);
            if (restore && _previousGamepad != null && _previousGamepad.added)
                _previousGamepad.MakeCurrent();
            _gamepad = null;
            _previousGamepad = null;
            ResetGamepadValues();
            _gamepadReleases.Clear();
        }
    }
}
#endif
