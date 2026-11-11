using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.ScriptQuality;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("manage_script_quality", AutoRegister = false, Group = "testing")]
    public static class ManageScriptQuality
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                string action = parameters?["action"]?.Type == JTokenType.String ? (string)parameters["action"] : null;
                string[] allowed = action switch
                {
                    "capabilities" => new[] { "action" },
                    "analyze" or "capture_baseline" => new[] { "action", "job_id", "paths", "change_set_id", "include_suppressed", "timeout_seconds" },
                    "status" => new[] { "action", "job_id", "page_size", "cursor" },
                    "cancel" => new[] { "action", "job_id" },
                    "impact" => new[] { "action", "script_path", "script_guid", "source_hash", "assets", "members" },
                    _ => throw new ArgumentException("unsupported_script_quality_action"),
                };
                if (parameters.Properties().Any(p => !allowed.Contains(p.Name)))
                    throw new ArgumentException("unknown_script_quality_parameter");
                if (action == "capabilities")
                    return new SuccessResponse("Observed optional script diagnostics capability.", ScriptAnalysisHost.Capabilities());
                if (action == "impact")
                    return new SuccessResponse("Observed serialized script references without loading assets.", SerializedScriptImpact.Read(parameters));
                string id = parameters["job_id"]?.Type == JTokenType.String ? (string)parameters["job_id"] : null;
                if (!ScriptDiagnosticJobs.ValidId(id))
                    throw new ArgumentException("invalid_analysis_job_id");
                JObject result;
                if (action == "status")
                {
                    int size = Integer(parameters, "page_size", 100, 500);
                    string cursor = null;
                    if (parameters["cursor"] != null)
                    {
                        if (
                            parameters["cursor"].Type != JTokenType.String
                            || ((string)parameters["cursor"]).Length < 1
                            || ((string)parameters["cursor"]).Length > 256
                        )
                            throw new ArgumentException("invalid_analysis_cursor");
                        cursor = (string)parameters["cursor"];
                    }
                    result = ScriptDiagnosticJobs.Status(id, size, cursor);
                }
                else if (action == "cancel")
                    result = ScriptDiagnosticJobs.Cancel(id);
                else
                {
                    Integer(parameters, "timeout_seconds", 120, 300);
                    if (parameters["include_suppressed"] != null && parameters["include_suppressed"].Type != JTokenType.Boolean)
                        throw new ArgumentException("include_suppressed_must_be_boolean");
                    result = ScriptDiagnosticJobs.Start(id, parameters);
                }
                return new SuccessResponse("Observed script diagnostic job.", result);
            }
            catch (Exception error) when (error is ArgumentException || error is InvalidOperationException)
            {
                return new ErrorResponse("script_quality_unavailable", new { reason = error.Message });
            }
        }

        private static int Integer(JObject parameters, string name, int fallback, int maximum)
        {
            if (parameters[name] == null)
                return fallback;
            if (parameters[name].Type != JTokenType.Integer || !int.TryParse(parameters[name].ToString(), out int value) || value < 1 || value > maximum)
                throw new ArgumentException("invalid_" + name);
            return value;
        }
    }
}
