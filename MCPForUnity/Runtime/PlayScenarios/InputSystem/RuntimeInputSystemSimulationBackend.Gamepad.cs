#if MCP_INPUT_SYSTEM
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using InputApi = UnityEngine.InputSystem.InputSystem;

namespace MCPForUnity.Editor.Tools.Input
{
    public partial class RuntimeInputSystemSimulationBackend
    {
        private Gamepad _gamepad;
        private Gamepad _previousGamepad;
        private GamepadState _gamepadState;
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
                _gamepadState = default;
                _previousGamepad = Gamepad.current;
                _gamepad = InputApi.AddDevice<Gamepad>("MCPVirtualGamepad");
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
            InputApi.QueueStateEvent(_gamepad, _gamepadState);
        }

        private void SetGamepadControl(string control, float scalar, Vector2 vector)
        {
            switch (control)
            {
                case "leftStick":
                    _gamepadState.leftStick = vector;
                    break;
                case "rightStick":
                    _gamepadState.rightStick = vector;
                    break;
                case "leftTrigger":
                    _gamepadState.leftTrigger = scalar;
                    break;
                case "rightTrigger":
                    _gamepadState.rightTrigger = scalar;
                    break;
                default:
                    if (!GamepadButtons.TryGetValue(control, out GamepadButton button))
                        throw new ArgumentException("Unsupported gamepad control.");
                    _gamepadState = _gamepadState.WithButton(button, scalar > 0);
                    break;
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
                InputApi.QueueStateEvent(_gamepad, _gamepadState);
        }

        private void ReleaseGamepad()
        {
            bool restore = _gamepad != null && Gamepad.current == _gamepad;
            if (_gamepad != null && _gamepad.added)
                InputApi.RemoveDevice(_gamepad);
            if (restore && _previousGamepad != null && _previousGamepad.added)
                _previousGamepad.MakeCurrent();
            _gamepad = null;
            _previousGamepad = null;
            _gamepadState = default;
            _gamepadReleases.Clear();
        }
    }
}
#endif
