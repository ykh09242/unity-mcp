using System;
using System.Collections.Generic;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Tools
{
    [McpForUnityTool("execute_menu_item", AutoRegister = false, RequiresExplicitConsent = true)]
    /// <summary>
    /// Tool to execute a Unity Editor menu item by its path.
    /// </summary>
    public static class ExecuteMenuItem
    {
        // Basic blacklist to prevent execution of disruptive menu items.
        private static readonly HashSet<string> _menuPathBlacklist = new HashSet<string>(
            StringComparer.OrdinalIgnoreCase)
        {
            "File/Quit",
        };

        public static object HandleCommand(JObject @params)
        {
            McpLog.Info("[ExecuteMenuItem] Handling menu item command");
            if (!TryGetMenuPath(@params, out string menuPath, out ErrorResponse inputError))
                return inputError;

            if (_menuPathBlacklist.Contains(menuPath))
            {
                return new ErrorResponse($"Execution of menu item '{menuPath}' is blocked for safety reasons.");
            }

            try
            {
                bool executed = EditorApplication.ExecuteMenuItem(menuPath);
                if (!executed)
                {
                    McpLog.Error($"[MenuItemExecutor] Failed to execute menu item '{menuPath}'. It might be invalid, disabled, or context-dependent.");
                    return new ErrorResponse($"Failed to execute menu item '{menuPath}'. It might be invalid, disabled, or context-dependent.");
                }
                return new SuccessResponse($"Attempted to execute menu item: '{menuPath}'. Check Unity logs for confirmation or errors.");
            }
            catch (Exception e)
            {
                McpLog.Error($"[MenuItemExecutor] Failed to setup execution for '{menuPath}': {e}");
                return new ErrorResponse($"Error setting up execution for menu item '{menuPath}': {e.Message}");
            }
        }

        private static bool TryGetMenuPath(JObject @params, out string menuPath, out ErrorResponse error)
        {
            menuPath = null;
            error = null;
            JToken path = @params?["menu_path"] ?? @params?["menuPath"];
            if (path != null && path.Type != JTokenType.String && path.Type != JTokenType.Null)
            {
                error = new ErrorResponse("Parameter 'menu_path' or 'menuPath' must be a string.");
                return false;
            }

            menuPath = path?.Value<string>();
            if (string.IsNullOrWhiteSpace(menuPath))
            {
                error = new ErrorResponse("Required parameter 'menu_path' or 'menuPath' is missing or empty.");
                return false;
            }
            return true;
        }
    }
}
