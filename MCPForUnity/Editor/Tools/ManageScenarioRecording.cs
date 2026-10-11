using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool(
        "manage_scenario_recording",
        AutoRegister = false,
        Group = "testing",
        Description = "Record bounded uGUI Button pointer clicks and observed active-scene transitions into a saved scenario draft. Stop explicitly saves; cancel discards."
    )]
    public static class ManageScenarioRecording
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                string action = Text(parameters, "action");
                JObject result;
                switch (action)
                {
                    case "start":
                        Allow(parameters, "action", "name", "max_events", "duration_seconds");
                        result = PlayScenarioRecordingService.Start(
                            Text(parameters, "name"),
                            Integer(parameters, "max_events", 1, 31, 31),
                            Integer(parameters, "duration_seconds", 1, 600, 300)
                        );
                        break;
                    case "status":
                        Allow(parameters, "action", "recording_id");
                        result = PlayScenarioRecordingService.Status(Id(parameters, false));
                        break;
                    case "stop":
                        Allow(parameters, "action", "recording_id");
                        result = PlayScenarioRecordingService.Stop(Id(parameters, true));
                        break;
                    case "cancel":
                        Allow(parameters, "action", "recording_id");
                        result = PlayScenarioRecordingService.Cancel(Id(parameters, true));
                        break;
                    case "checkpoint":
                        Allow(parameters, "action", "recording_id", "checkpoint_name");
                        result = PlayScenarioRecordingService.Checkpoint(Id(parameters, true), Text(parameters, "checkpoint_name"));
                        break;
                    default:
                        throw new ArgumentException("action must be start, status, stop, cancel or checkpoint.");
                }
                return new SuccessResponse(
                    action == "stop" ? "Scenario draft saved. Review it before clearing draft and running." : "Scenario recording updated.",
                    result
                );
            }
            catch (Exception error) when (error is ArgumentException || error is InvalidOperationException || error is IOException || error is JsonException)
            {
                return new ErrorResponse(error.Message);
            }
        }

        private static void Allow(JObject value, params string[] allowed)
        {
            foreach (JProperty property in value.Properties())
                if (!allowed.Contains(property.Name, StringComparer.Ordinal))
                    throw new ArgumentException("Unexpected parameter for this action: " + property.Name);
        }

        private static string Text(JObject value, string key)
        {
            if (value?[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)value[key]))
                throw new ArgumentException(key + " must be a non-empty string.");
            return (string)value[key];
        }

        private static string Id(JObject value, bool required)
        {
            if (!required && value.Property("recording_id") == null)
                return null;
            string id = Text(value, "recording_id");
            if (!Regex.IsMatch(id, @"\A[a-f0-9]{32}\z"))
                throw new ArgumentException("recording_id must contain exactly 32 lowercase hexadecimal characters.");
            return id;
        }

        private static int Integer(JObject value, string key, int minimum, int maximum, int fallback)
        {
            JToken token = value[key];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int number) || number < minimum || number > maximum)
                throw new ArgumentException(key + " must be an integer between " + minimum + " and " + maximum + ".");
            return number;
        }
    }
}
