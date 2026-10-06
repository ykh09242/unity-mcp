using System;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Dependencies;
using MCPForUnity.Editor.Dependencies.Models;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Windows;
using UnityEditor;

namespace MCPForUnity.Editor.Setup
{
    /// <summary>
    /// Opens setup only in response to an explicit user action.
    /// </summary>
    public static class SetupWindowService
    {
        private const string SETUP_COMPLETED_KEY = EditorPrefKeys.SetupCompleted;
        private const string SETUP_DISMISSED_KEY = EditorPrefKeys.SetupDismissed;

        /// <summary>
        /// Show the setup window
        /// </summary>
        public static void ShowSetupWindow(DependencyCheckResult dependencyResult = null)
        {
            try
            {
                dependencyResult ??= DependencyManager.CheckAllDependencies();
                MCPSetupWindow.ShowWindow(dependencyResult);
            }
            catch (Exception ex)
            {
                McpLog.Error($"Error showing setup window: {ex.Message}");
            }
        }

        /// <summary>
        /// Mark setup as completed
        /// </summary>
        public static void MarkSetupCompleted()
        {
            EditorPrefs.SetBool(SETUP_COMPLETED_KEY, true);
            McpLog.Info("Setup marked as completed");
        }

        /// <summary>
        /// Mark setup as dismissed
        /// </summary>
        public static void MarkSetupDismissed()
        {
            EditorPrefs.SetBool(SETUP_DISMISSED_KEY, true);
            McpLog.Info("Setup marked as dismissed");
        }
    }
}
