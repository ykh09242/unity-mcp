using System;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Server;
using MCPForUnity.Editor.Services.Transport;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Best-effort cleanup when the Unity Editor is quitting.
    /// - Stops active transports so clients don't see a "hung" session longer than necessary.
    /// - Keeps shared local HTTP servers alive by default; optional stop requires
    ///   project ownership and a verified absence of connected sessions.
    /// </summary>
    [InitializeOnLoad]
    internal static class McpEditorShutdownCleanup
    {
        static McpEditorShutdownCleanup()
        {
            // Guard against duplicate subscriptions across domain reloads.
            try
            {
                EditorApplication.quitting -= OnEditorQuitting;
            }
            catch { }
            EditorApplication.quitting += OnEditorQuitting;
        }

        // A batch/CI instance can share project preferences with an interactive editor.
        // Mirror HttpAutoStartHandler/StdioBridgeHost: skip unless explicitly opted in.
        internal static bool ShouldRunCleanup() => ShouldRunCleanup(Application.isBatchMode, Environment.GetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH"));

        internal static bool ShouldRunCleanup(bool isBatchMode, string allowBatchEnv) => !isBatchMode || !string.IsNullOrWhiteSpace(allowBatchEnv);

        internal static bool KeepLocalServerAlive
        {
            get => EditorPrefs.GetBool(PidFileManager.ProjectPreferenceKey(EditorPrefKeys.KeepLocalHttpServerAlive), true);
            set => EditorPrefs.SetBool(PidFileManager.ProjectPreferenceKey(EditorPrefKeys.KeepLocalHttpServerAlive), value);
        }

        private static void OnEditorQuitting()
        {
            if (!ShouldRunCleanup())
                return;

            // 1) Stop transports (best-effort, bounded wait).
            try
            {
                var transport = MCPServiceLocator.TransportManager;

                Task stopHttp = transport.StopAsync(TransportMode.Http);
                Task stopStdio = transport.StopAsync(TransportMode.Stdio);

                try
                {
                    Task.WaitAll(new[] { stopHttp, stopStdio }, 750);
                }
                catch { }
            }
            catch (Exception ex)
            {
                // Avoid hard failures on quit.
                McpLog.Warn($"Shutdown cleanup: failed to stop transports: {ex.Message}");
            }

            // Other editors and MCP clients may still use the same process.
            if (KeepLocalServerAlive)
                return;

            // Opting out still cannot kill another project's process or an active
            // shared session. The service fails closed if peer state is unavailable.
            try
            {
                var server = MCPServiceLocator.Server;
                if (!server.StopManagedLocalHttpServer() && server is ServerManagementService managed && !string.IsNullOrEmpty(managed.LastStopFailure))
                    McpLog.Info("Shutdown cleanup: local HTTP server remains running. " + managed.LastStopFailure);
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Shutdown cleanup: failed to stop local HTTP server: {ex.Message}");
            }
        }
    }
}
