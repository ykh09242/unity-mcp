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
