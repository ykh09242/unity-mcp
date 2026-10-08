using System;
using System.Globalization;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Input
{
    /// <summary>Validated input command shared with optional package adapters.</summary>
    public sealed class InputSimulationRequest
    {
        public const int MaxFrames = 600;
        public const double DeviceTimeoutSeconds = 30;
        public string Action { get; private set; }
        public string State { get; private set; }
        public string Key { get; private set; }
        public string Button { get; private set; }
        public int Frames { get; private set; }
        public int TouchId { get; private set; }
        public Vector2? Position { get; private set; }
        public JToken Target { get; private set; }

        public static InputSimulationRequest Parse(JObject parameters)
        {
            if (parameters == null)
                throw new ArgumentException("Parameters cannot be null.");
            var request = new InputSimulationRequest
            {
                Action = Text(parameters, "action", null),
                State = Text(parameters, "state", "press"),
                Key = Text(parameters, "key", null),
                Button = Text(parameters, "button", "left"),
                Frames = Integer(parameters, "frames", 1, 1, MaxFrames),
                TouchId = Integer(parameters, "touch_id", 1, 1, 10),
                Target = parameters["target"],
            };
            switch (request.Action)
            {
                case "status":
                case "release_all":
                case "ui_click":
                case "key":
                case "mouse":
                case "touch":
                    break;
                default:
                    throw new ArgumentException("action must be status, ui_click, key, mouse, touch or release_all.");
            }
            if (request.State != "press" && request.State != "release" && request.State != "move")
                throw new ArgumentException("state must be press, release or move.");
            if (request.Button != "left" && request.Button != "right" && request.Button != "middle")
                throw new ArgumentException("button must be left, right or middle.");
            JToken position = parameters["position"];
            if (position != null && position.Type != JTokenType.Null)
            {
                if (!(position is JArray coordinates) || coordinates.Count != 2)
                    throw new ArgumentException("position must contain exactly two finite numbers in screen pixels.");
                request.Position = new Vector2(Coordinate(coordinates[0]), Coordinate(coordinates[1]));
            }
            if (request.Action == "key" && (string.IsNullOrWhiteSpace(request.Key) || request.State == "move"))
                throw new ArgumentException("key requires a Key enum name and state press or release.");
            if (request.Action == "mouse" && request.State == "move" && !request.Position.HasValue)
                throw new ArgumentException("mouse move requires position.");
            if (request.Action == "touch" && request.State != "release" && !request.Position.HasValue)
                throw new ArgumentException("touch press/move requires position.");
            return request;
        }

        private static string Text(JObject parameters, string name, string fallback)
        {
            JToken token = parameters[name];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (token.Type != JTokenType.String)
                throw new ArgumentException(name + " must be a string.");
            return token.Value<string>();
        }

        private static int Integer(JObject parameters, string name, int fallback, int minimum, int maximum)
        {
            JToken token = parameters[name];
            if (token == null || token.Type == JTokenType.Null)
                return fallback;
            if (
                token.Type != JTokenType.Integer
                || !int.TryParse(token.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out int value)
                || value < minimum
                || value > maximum
            )
                throw new ArgumentException($"{name} must be an integer from {minimum} to {maximum}.");
            return value;
        }

        private static float Coordinate(JToken token)
        {
            if (
                (token.Type != JTokenType.Integer && token.Type != JTokenType.Float)
                || !float.TryParse(token.ToString(Newtonsoft.Json.Formatting.None), NumberStyles.Float, CultureInfo.InvariantCulture, out float value)
                || float.IsNaN(value)
                || float.IsInfinity(value)
                || value < 0
                || value > 1000000
            )
                throw new ArgumentException("position coordinates must be finite numbers from 0 to 1000000.");
            return value;
        }
    }
}
