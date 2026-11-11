using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.ScriptQuality;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("manage_verification", AutoRegister = false, Group = "testing")]
    public static class ManageVerification
    {
        public static object HandleCommand(JObject parameters)
        {
            try
            {
                string action = parameters?["action"]?.Type == JTokenType.String ? (string)parameters["action"] : null;
                string[] fields = action switch
                {
                    "profiles" => new[] { "action" },
                    "set_profile" => new[] { "action", "profile_id", "profile", "expected_hash" },
                    _ => throw new ArgumentException("unsupported_native_verification_action"),
                };
                if (parameters.Properties().Any(p => !fields.Contains(p.Name)))
                    throw new ArgumentException("unknown_verification_parameter");
                if (action == "profiles")
                    return new SuccessResponse("Observed project verification profiles.", VerificationProfileStore.Read(ScriptAnalysisHost.ProjectRoot));
                if (parameters["profile_id"]?.Type != JTokenType.String || parameters["expected_hash"]?.Type != JTokenType.String)
                    throw new ArgumentException("profile_write_requires_identity_and_hash");
                return new SuccessResponse(
                    "Updated project verification profile.",
                    VerificationProfileStore.Write(
                        ScriptAnalysisHost.ProjectRoot,
                        (string)parameters["profile_id"],
                        parameters["profile"] as JObject,
                        (string)parameters["expected_hash"]
                    )
                );
            }
            catch (Exception error) when (error is ArgumentException || error is InvalidOperationException || error is IOException)
            {
                return new ErrorResponse("verification_profile_unavailable", new { reason = error.Message });
            }
        }
    }
}
