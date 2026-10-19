using System;
using System.Globalization;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    public sealed partial class InputSimulationRequest
    {
        public string Control { get; private set; }
        public string Device { get; private set; }
        public Vector2? StickValue { get; private set; }
        public float? ScalarValue { get; private set; }
        public Vector2? WheelDelta { get; private set; }
        private static readonly string[] GamepadControls =
        {
            "leftStick",
            "rightStick",
            "leftTrigger",
            "rightTrigger",
            "buttonSouth",
            "buttonNorth",
            "buttonEast",
            "buttonWest",
            "leftShoulder",
            "rightShoulder",
            "leftStickPress",
            "rightStickPress",
            "start",
            "select",
            "dpadUp",
            "dpadDown",
            "dpadLeft",
            "dpadRight",
        };

        private void ParseExtended(JObject parameters)
        {
            Control = Text(parameters, "control", null);
            Device = Text(parameters, "device", "gamepad");
            switch (Action)
            {
                case "gamepad":
                    if (Array.IndexOf(GamepadControls, Control) < 0 || State == "move")
                        throw new ArgumentException("gamepad requires a supported control and state press or release.");
                    if (State == "release")
                    {
                        if (parameters["value"] != null && parameters["value"].Type != JTokenType.Null)
                            throw new ArgumentException("gamepad release must omit value; the control returns to neutral.");
                        break;
                    }
                    if (Control == "leftStick" || Control == "rightStick")
                        StickValue = Pair(parameters["value"], -1, 1, "stick value");
                    else if (Control == "leftTrigger" || Control == "rightTrigger")
                        ScalarValue = Number(parameters["value"], 0, 1, "trigger value");
                    else if (parameters["value"] != null && parameters["value"].Type != JTokenType.Null)
                        throw new ArgumentException("gamepad buttons use press/release; omit value.");
                    break;
                case "wheel":
                    WheelDelta = Pair(parameters["delta"], -10000, 10000, "wheel delta");
                    break;
                case "player_status":
                case "pair_player":
                case "release_player":
                    if (Target == null || Target.Type == JTokenType.Null)
                        throw new ArgumentException(Action + " requires an exact target PlayerInput GameObject.");
                    if (Device != "gamepad")
                        throw new ArgumentException("Explicit PlayerInput pairing currently supports only the MCP-owned gamepad.");
                    break;
            }
        }

        private static Vector2 Pair(JToken token, float minimum, float maximum, string name)
        {
            if (!(token is JArray pair) || pair.Count != 2)
                throw new ArgumentException(name + " must contain exactly two finite numbers.");
            return new Vector2(Number(pair[0], minimum, maximum, name), Number(pair[1], minimum, maximum, name));
        }

        private static float Number(JToken token, float minimum, float maximum, string name)
        {
            if (
                token == null
                || (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
                || !float.TryParse(token.ToString(Newtonsoft.Json.Formatting.None), NumberStyles.Float, CultureInfo.InvariantCulture, out float value)
                || float.IsNaN(value)
                || float.IsInfinity(value)
                || value < minimum
                || value > maximum
            )
                throw new ArgumentException(name + " must contain finite numbers from " + minimum + " to " + maximum + ".");
            return value;
        }
    }
}
