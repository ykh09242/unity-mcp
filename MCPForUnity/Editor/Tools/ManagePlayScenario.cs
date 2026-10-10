using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool(
        "manage_play_scenario",
        AutoRegister = false,
        Group = "testing",
        Description = "Save and repeat bounded Play Mode scenarios with condition waits, step results and failure logs."
    )]
    public static class ManagePlayScenario
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                string action = Text(parameters, "action");
                var store = new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath));
                switch (action)
                {
                    case "save":
                        Allow(parameters, "action", "scenario");
                        if (!(parameters["scenario"] is JObject definition))
                            throw new ArgumentException("scenario must be an object.");
                        PlayScenarioDefinition scenario = PlayScenarioDefinition.Parse(definition);
                        store.Save(scenario);
                        return new SuccessResponse("Scenario saved.", scenario);
                    case "get":
                        Allow(parameters, "action", "name");
                        return new SuccessResponse("Scenario definition.", store.Get(Name(parameters)));
                    case "list":
                        Allow(parameters, "action");
                        return new SuccessResponse("Saved scenario names.", new { scenarios = store.List() });
                    case "delete":
                        Allow(parameters, "action", "name");
                        return new SuccessResponse("Scenario deletion processed.", new { deleted = store.Delete(Name(parameters)) });
                    case "run":
                        Allow(parameters, "action", "name", "job_id", "repeat_count", "timeout_seconds");
                        return PlayScenarioService.Start(
                            Name(parameters),
                            Integer(parameters, "repeat_count", 1, 10, 1),
                            Integer(parameters, "timeout_seconds", 1, 1800, 300),
                            JobId(parameters, false)
                        );
                    case "status":
                        Allow(parameters, "action", "job_id");
                        return PlayScenarioService.Status(JobId(parameters, true));
                    case "cancel":
                        Allow(parameters, "action", "job_id");
                        return PlayScenarioService.Cancel(JobId(parameters, true));
                    default:
                        throw new ArgumentException("action must be save, get, list, delete, run, status or cancel.");
                }
            }
            catch (Exception exception)
                when (exception is ArgumentException
                    || exception is IOException
                    || exception is UnauthorizedAccessException
                    || exception is InvalidOperationException
                    || exception is JsonException
                )
            {
                return new ErrorResponse(exception.Message);
            }
        }

        private static void Allow(JObject value, params string[] fields)
        {
            foreach (JProperty property in value.Properties())
                if (!fields.Contains(property.Name))
                    throw new ArgumentException("Unexpected parameter for this action: " + property.Name);
        }

        private static string Text(JObject value, string key)
        {
            if (value?[key]?.Type != JTokenType.String || string.IsNullOrWhiteSpace((string)value[key]))
                throw new ArgumentException(key + " must be a non-empty string.");
            return (string)value[key];
        }

        private static string Name(JObject value)
        {
            string name = Text(value, "name");
            PlayScenarioDefinition.ValidateName(name);
            return name;
        }

        private static string JobId(JObject value, bool required)
        {
            if (!required && value["job_id"] == null)
                return null;
            string id = Text(value, "job_id");
            if (!Regex.IsMatch(id, @"\A[a-f0-9]{32}\z"))
                throw new ArgumentException("job_id must be 32 lowercase hexadecimal characters.");
            return id;
        }

        private static int Integer(JObject value, string key, int min, int max, int fallback)
        {
            JToken token = value[key];
            if (token == null)
                return fallback;
            if (token.Type != JTokenType.Integer || !int.TryParse(token.ToString(), out int result) || result < min || result > max)
                throw new ArgumentException(key + " must be an integer between " + min + " and " + max + ".");
            return result;
        }
    }
}
