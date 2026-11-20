using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Windows;
using Newtonsoft.Json.Linq;

namespace IndependentExtension
{
    [McpForUnityTool("extension_sample", Group = "core", Description = "Echo an extension value.", RequiresPolling = true, PollAction = "status")]
    public static class EchoTool
    {
        public class Parameters
        {
            [ToolParameter("Value to echo", Required = false)]
            public string Value { get; set; }
        }

        public static object HandleCommand(JObject parameters)
        {
            var request = PropertyConversion.ConvertTo<Parameters>(parameters);
            if (string.IsNullOrEmpty(request?.Value))
                return new ErrorResponse("A value is required");
            if (parameters["pending"].ReadScalar<bool?>() == true)
                return new PendingResponse("Waiting", pollIntervalSeconds: 0.5);
            return new SuccessResponse("Echo", request.Value);
        }

        public static bool ExerciseHelpers()
        {
            System.Func<JToken, Parameters> convert = PropertyConversion.ConvertTo<Parameters>;
            IMcpResponse result = new SuccessResponse("Echo", new JValue(7).ReadScalar<int>());
            return convert != null
                && result.Success
                && !new ErrorResponse("invalid").Success
                && new PendingResponse("pending", pollIntervalSeconds: 0.5).PollIntervalSeconds == 0.5
                && LegacyExtension.BindLegacyStore();
        }
    }

    [McpForUnityResource("extension_resource", Description = "Read an extension value.")]
    public static class EchoResource
    {
        public static object HandleCommand(JObject parameters) => parameters;
    }

    public static class WindowExtension
    {
        public static void Rebuild(MCPForUnityEditorWindow main, MCPSetupWindow setup, EditorPrefsWindow preferences)
        {
            main.CreateGUI();
            setup.CreateGUI();
            preferences.CreateGUI();
        }
    }
}
