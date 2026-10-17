using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Resources.Editor
{
    /// <summary>
    /// Returns the enabled/disabled state of all discovered tools, grouped by group name.
    /// Used by the Python server (especially in stdio mode) to sync tool visibility.
    /// </summary>
    [McpForUnityResource("get_tool_states")]
    public static class ToolStates
    {
        private static ToolSettingsState _settingsStateOverride = null;

        public static object HandleCommand(JObject @params)
        {
            var diagnosticsFlag = @params?["diagnostics"];
            if (diagnosticsFlag != null && (diagnosticsFlag.Type != JTokenType.Boolean || diagnosticsFlag.Value<bool>()))
            {
                // This read boundary must never enter settings preparation/failure handling.
                try
                {
                    if (diagnosticsFlag.Type != JTokenType.Boolean)
                        return new ErrorResponse("Invalid tool diagnostics query.");
                    var nameToken = @params["tool_name"];
                    if (nameToken != null && nameToken.Type != JTokenType.Null && nameToken.Type != JTokenType.String)
                        return new ErrorResponse("Invalid tool diagnostics query.");
                    string name = nameToken?.Value<string>();
                    var limitToken = @params["limit"];
                    if (limitToken != null && limitToken.Type != JTokenType.Integer)
                        return new ErrorResponse("Invalid tool diagnostics query.");
                    int limit = limitToken == null ? (name == null ? 64 : 16) : limitToken.Value<int>();
                    if (!ToolDiscoveryDiagnostics.ValidName(name) || limit < 1 || limit > (name == null ? 64 : 16))
                        return new ErrorResponse("Invalid tool diagnostics query.");
                    var provider = MCPServiceLocator.ToolDiscovery as IToolDiscoveryDiagnostics;
                    if (provider == null)
                        return new ErrorResponse("Tool diagnostics unavailable.");
                    return new SuccessResponse("Retrieved tool diagnostics.", provider.GetDiagnostics(name, limit));
                }
                catch (Exception)
                {
                    return new ErrorResponse("Tool diagnostics unavailable.");
                }
            }
            try
            {
                var discovery = MCPServiceLocator.ToolDiscovery;
                var allTools = discovery.DiscoverAllTools();

                var toolsArray = new JArray();
                foreach (var tool in allTools)
                {
                    var paramsArray = new JArray();
                    if (tool.Parameters != null)
                    {
                        foreach (var p in tool.Parameters)
                        {
                            paramsArray.Add(
                                new JObject
                                {
                                    ["name"] = p.Name,
                                    ["description"] = p.Description,
                                    ["type"] = p.Type,
                                    ["required"] = p.Required,
                                    ["default_value"] = p.DefaultValue,
                                }
                            );
                        }
                    }

                    var descriptor = new JObject
                    {
                        ["name"] = tool.Name,
                        ["group"] = tool.Group ?? "core",
                        ["enabled"] = discovery.IsToolEnabled(tool.Name),
                        ["description"] = tool.Description,
                        ["auto_register"] = tool.AutoRegister,
                        ["is_built_in"] = tool.IsBuiltIn,
                        ["structured_output"] = tool.StructuredOutput,
                        ["requires_polling"] = tool.RequiresPolling,
                        ["poll_action"] = tool.PollAction ?? "status",
                        ["max_poll_seconds"] = tool.MaxPollSeconds,
                        ["parameters"] = paramsArray,
                    };
                    ToolSchemaBuilder.AppendSchema(descriptor, tool);
                    toolsArray.Add(descriptor);
                }

                var groups = toolsArray
                    .GroupBy(t => (string)t["group"])
                    .Select(g => new JObject
                    {
                        ["name"] = g.Key,
                        ["enabled_count"] = g.Count(t => (bool)t["enabled"]),
                        ["total_count"] = g.Count(),
                    });

                var enablement = new JObject(toolsArray.Select(tool => new JProperty((string)tool["name"], (bool)tool["enabled"])));
                var desired = (_settingsStateOverride ?? ToolSettingsState.Current).Capture(
                    new JArray(toolsArray.Where(tool => (bool)tool["enabled"])),
                    enablement
                );
                var result = new JObject
                {
                    ["tools"] = toolsArray,
                    ["groups"] = new JArray(groups),
                    ["settings_epoch"] = desired.Epoch,
                    ["desired_revision"] = desired.Revision,
                };

                return new SuccessResponse("Retrieved tool states.", result);
            }
            catch (Exception e)
            {
                (_settingsStateOverride ?? ToolSettingsState.Current).PreparationFailed(
                    ToolSettingsState.CaptureFailureMessage(e.GetBaseException().GetType().Name)
                );
                return new ErrorResponse($"Failed to retrieve tool states: {e.Message}");
            }
        }
    }
}
