using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioDefinition
    {
        [JsonProperty("name")]
        public string Name;

        [JsonProperty("poll_interval_ms")]
        public int PollIntervalMs = 250;

        [JsonProperty("steps")]
        public List<PlayScenarioStep> Steps = new List<PlayScenarioStep>();

        public static void ValidateName(string name)
        {
            if (name == null || !Regex.IsMatch(name, @"\A[a-z0-9][a-z0-9_-]{0,63}\z"))
                throw new ArgumentException("Scenario name must be a lowercase slug of 1-64 characters.");
        }

        public static PlayScenarioDefinition Parse(JObject value)
        {
            if (value == null)
                throw new ArgumentException("Scenario must be an object.");
            Fields(value, "name", "poll_interval_ms", "steps");
            string name = Text(value, "name", 64);
            ValidateName(name);
            int polling = Integer(value, "poll_interval_ms", 100, 2000, 250);
            if (!(value["steps"] is JArray steps) || steps.Count < 1 || steps.Count > 32)
                throw new ArgumentException("steps must contain 1-32 objects.");
            var result = new PlayScenarioDefinition { Name = name, PollIntervalMs = polling };
            foreach (JToken token in steps)
            {
                if (!(token is JObject step))
                    throw new ArgumentException("Each step must be an object.");
                Fields(step, "name", "action", "scene", "target", "timeout_seconds");
                var parsed = new PlayScenarioStep
                {
                    Name = Text(step, "name", 128),
                    Action = Text(step, "action", 32),
                    TimeoutSeconds = Integer(step, "timeout_seconds", 1, 120, 30),
                };
                switch (parsed.Action)
                {
                    case "load_scene":
                    case "wait_scene":
                        if (step.Property("target") != null)
                            throw new ArgumentException("Scene actions cannot specify target.");
                        parsed.Scene = Text(step, "scene", 4096);
                        ValidateScene(parsed.Scene);
                        break;
                    case "click_ui":
                    case "wait_object":
                        if (step.Property("scene") != null)
                            throw new ArgumentException("Object actions cannot specify scene.");
                        parsed.Target = Text(step, "target", 4096);
                        ValidateHierarchy(parsed.Target);
                        break;
                    default:
                        throw new ArgumentException("Unsupported step action.");
                }
                result.Steps.Add(parsed);
            }
            if (result.Steps[0].Action != "load_scene")
                throw new ArgumentException("The first step must load_scene.");
            return result;
        }

        private static void ValidateScene(string scene)
        {
            if (!scene.StartsWith("Assets/", StringComparison.Ordinal) || !scene.EndsWith(".unity", StringComparison.Ordinal))
                throw new ArgumentException("Scene must be a canonical Assets/*.unity path.");
            ValidateHierarchy(scene);
            foreach (string part in scene.Split('/'))
            {
                if (
                    part.Equals("GameData", StringComparison.OrdinalIgnoreCase)
                    || part.IndexOfAny(new[] { ':', '*', '?', '"', '<', '>', '|' }) >= 0
                    || part.EndsWith(".", StringComparison.Ordinal)
                    || part.EndsWith(" ", StringComparison.Ordinal)
                )
                    throw new ArgumentException("Scene path is not permitted.");
            }
        }

        private static void ValidateHierarchy(string target)
        {
            string[] parts = target.Split('/');
            if (target.IndexOf('\\') >= 0 || parts.Length > 128 || parts.Any(part => part.Length == 0 || part == "." || part == ".."))
                throw new ArgumentException("Target must be an exact relative hierarchy path with at most 128 levels.");
        }

        internal static void Fields(JObject value, params string[] allowed)
        {
            foreach (JProperty property in value.Properties())
                if (!allowed.Contains(property.Name, StringComparer.Ordinal))
                    throw new ArgumentException("Unknown field: " + property.Name);
        }

        internal static string Text(JObject value, string key, int maximum)
        {
            if (value[key]?.Type != JTokenType.String)
                throw new ArgumentException(key + " must be a string.");
            string text = (string)value[key];
            if (string.IsNullOrWhiteSpace(text) || text.Length > maximum || text.Any(char.IsControl) || text.IndexOf('\\') >= 0)
                throw new ArgumentException(key + " has an invalid length or character.");
            return text;
        }

        internal static int Integer(JObject value, string key, int minimum, int maximum, int fallback)
        {
            JToken token = value[key];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int number) || number < minimum || number > maximum)
                throw new ArgumentException(key + " must be an integer between " + minimum + " and " + maximum + ".");
            return number;
        }
    }

    public sealed class PlayScenarioStep
    {
        [JsonProperty("name")]
        public string Name;

        [JsonProperty("action")]
        public string Action;

        [JsonProperty("scene", NullValueHandling = NullValueHandling.Ignore)]
        public string Scene;

        [JsonProperty("target", NullValueHandling = NullValueHandling.Ignore)]
        public string Target;

        [JsonProperty("timeout_seconds")]
        public int TimeoutSeconds = 30;
    }
}
