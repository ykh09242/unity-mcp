using System;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Resources.Editor
{
    /// <summary>
    /// Provides information about the currently active editor tool.
    /// </summary>
    [McpForUnityResource("get_active_tool")]
    public static class ActiveTool
    {
        public static object HandleCommand(JObject @params)
        {
            try
            {
                Tool currentTool = UnityEditor.Tools.current;
                string toolName = currentTool.ToString();
                bool customToolActive = currentTool == Tool.Custom;
                string activeToolName = customToolActive ? EditorTools.GetActiveToolName(currentTool) : toolName;
                var handleRotation = UnityEditor.Tools.handleRotation.eulerAngles;
                var handlePosition = UnityEditor.Tools.handlePosition;

                var toolInfo = new
                {
                    activeTool = activeToolName,
                    isCustom = customToolActive,
                    pivotMode = UnityEditor.Tools.pivotMode.ToString(),
                    pivotRotation = UnityEditor.Tools.pivotRotation.ToString(),
                    handleRotation = new
                    {
                        x = handleRotation.x,
                        y = handleRotation.y,
                        z = handleRotation.z,
                    },
                    handlePosition = new
                    {
                        x = handlePosition.x,
                        y = handlePosition.y,
                        z = handlePosition.z,
                    },
                };

                return new SuccessResponse("Retrieved active tool information.", toolInfo);
            }
            catch (Exception e)
            {
                return new ErrorResponse($"Error getting active tool: {e.Message}");
            }
        }
    }

    // Helper class for custom tool names
    internal static class EditorTools
    {
        public static string GetActiveToolName(Tool currentTool)
        {
            if (currentTool == Tool.Custom)
            {
                return "Unknown Custom Tool";
            }
            return currentTool.ToString();
        }
    }
}
