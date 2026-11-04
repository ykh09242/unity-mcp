using System;
using System.Linq;
using MCPForUnity.Editor.Clients;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Migrations
{
    /// <summary>
    /// Keeps stdio MCP clients in sync with the current package version by rewriting their configs when the package updates.
    /// </summary>
    [InitializeOnLoad]
    internal static class StdIoVersionMigration
    {
        private const string LastUpgradeKey = EditorPrefKeys.LastStdIoUpgradeVersion;

        static StdIoVersionMigration()
        {
            if (Application.isBatchMode)
                return;

            EditorApplication.delayCall += RunMigrationIfNeeded;
        }

        private static void RunMigrationIfNeeded()
        {
            EditorApplication.delayCall -= RunMigrationIfNeeded;

            string currentVersion = AssetPathUtility.GetPackageVersion();
            if (string.IsNullOrEmpty(currentVersion) || string.Equals(currentVersion, "unknown", StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            string upgradeIdentity;
            try
            {
                upgradeIdentity = GetUpgradeIdentity(currentVersion, AssetPathUtility.GetMcpServerPackageSource());
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Cannot refresh stdio configs: {ex.Message}");
                return;
            }

            string lastUpgradeVersion = string.Empty;
            try
            {
                lastUpgradeVersion = EditorPrefs.GetString(LastUpgradeKey, string.Empty);
            }
            catch { }

            if (string.Equals(lastUpgradeVersion, upgradeIdentity, StringComparison.Ordinal))
            {
                return; // Already refreshed for this package version and server source.
            }

            bool hadFailures = false;
            bool touchedAny = false;

            var configurators = McpClientRegistry.All.OfType<McpClientConfiguratorBase>().ToList();
            foreach (var configurator in configurators)
            {
                try
                {
                    if (!configurator.SupportsAutoConfigure)
                        continue;

                    // A source/version migration must preserve the stored transport. Normal
                    // status refresh may follow a selected transport, but migration never does.
                    if (EditorConfigurationCache.Instance.UseHttpTransport || !configurator.CanAutomaticallyRefresh())
                        continue;

                    if (configurator.CheckStatus(attemptAutoRewrite: true) == McpStatus.Configured)
                        touchedAny = true;
                    else
                        hadFailures = true;
                }
                catch (Exception ex)
                {
                    hadFailures = true;
                    McpLog.Warn($"Failed to refresh stdio config for {configurator.DisplayName}: {ex.Message}");
                }
            }

            if (hadFailures)
            {
                McpLog.Warn("Stdio MCP upgrade encountered errors; will retry next session.");
                return;
            }

            if (!touchedAny)
            {
                // No eligible entries needed refreshing; still avoid rerunning each launch.
                try
                {
                    EditorPrefs.SetString(LastUpgradeKey, upgradeIdentity);
                }
                catch { }
                return;
            }

            try
            {
                EditorPrefs.SetString(LastUpgradeKey, upgradeIdentity);
            }
            catch { }

            McpLog.Info($"Updated stdio MCP configs to package version {currentVersion}.");
        }

        internal static string GetUpgradeIdentity(string version, string source) => version + "|" + source;
    }
}
