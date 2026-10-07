using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Clients
{

    /// <summary>CLI-based configurator (Claude Code).</summary>
    public abstract class ClaudeCliMcpConfigurator : McpClientConfiguratorBase
    {
        public ClaudeCliMcpConfigurator(McpClient client) : base(client) { }

        public override bool SupportsAutoConfigure => true;
        internal bool ShouldUnregister => client.status == McpStatus.Configured
            && (HasClientProjectDirOverride || client.configuredTransport == HttpEndpointUtility.GetCurrentServerTransport());

        public override string GetConfigureActionLabel() => ShouldUnregister ? "Unregister" : "Configure";

        public override string GetConfigPath() => "Managed via Claude CLI";

        public override bool IsInstalled => MCPServiceLocator.Paths.IsClaudeCliDetected();

        /// <summary>
        /// Returns the project directory that CLI-based configurators will use as the working directory
        /// for `claude mcp add/remove --scope local`. Checks for an explicit override in EditorPrefs
        /// first, then falls back to the current Unity project directory.
        /// The override is useful when the Claude Code workspace is at a different path than the Unity project
        /// (e.g., plugin developers running CC from the repo root while Unity is open with a test project).
        /// MUST be called from the main Unity thread (accesses Application.dataPath and EditorPrefs).
        /// </summary>
        internal static string GetClientProjectDir()
        {
            string overrideDir = EditorPrefs.GetString(EditorPrefKeys.ClientProjectDirOverride, string.Empty);
            if (!string.IsNullOrEmpty(overrideDir) && Directory.Exists(overrideDir))
                return overrideDir;
            return Path.GetDirectoryName(Application.dataPath);
        }

        /// <summary>
        /// Returns true if a valid client project directory override is set.
        /// </summary>
        internal static bool HasClientProjectDirOverride
        {
            get
            {
                string overrideDir = EditorPrefs.GetString(EditorPrefKeys.ClientProjectDirOverride, string.Empty);
                return !string.IsNullOrEmpty(overrideDir) && Directory.Exists(overrideDir);
            }
        }
        /// Checks the Claude CLI registration status.
        /// MUST be called from the main Unity thread due to EditorPrefs and Application.dataPath access.
        /// </summary>
        public override McpStatus CheckStatus(bool attemptAutoRewrite = true)
        {
            // Capture main-thread-only values before delegating to thread-safe method
            string projectDir = GetClientProjectDir();
            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;
            // Resolve claudePath on the main thread (EditorPrefs access)
            string claudePath = MCPServiceLocator.Paths.GetClaudeCliPath();
            RuntimePlatform platform = Application.platform;
            bool isRemoteScope = HttpEndpointUtility.IsRemoteScope();
            // Get expected package source for the installed package version (matches what Register() would use)
            string expectedPackageSource = useHttpTransport ? null : GetExpectedPackageSourceForValidation();
            string expectedHttpUrl = useHttpTransport ? HttpEndpointUtility.GetMcpRpcUrl() : null;
            var expectedHeaders = useHttpTransport && isRemoteScope ? HttpEndpointUtility.GetAuthHeaders() : null;
            return CheckStatusWithProjectDir(projectDir, useHttpTransport, claudePath, platform, isRemoteScope,
                expectedPackageSource, attemptAutoRewrite, HasClientProjectDirOverride, expectedHttpUrl, expectedHeaders);
        }

        /// <summary>
        /// Internal thread-safe version of CheckStatus.
        /// Can be called from background threads because all main-thread-only values are passed as parameters.
        /// projectDir, useHttpTransport, claudePath, platform, isRemoteScope, and expectedPackageSource are REQUIRED
        /// (non-nullable where applicable) to enforce thread safety at compile time.
        /// NOTE: attemptAutoRewrite is NOT fully thread-safe because Configure() requires the main thread.
        /// When called from a background thread, pass attemptAutoRewrite=false and handle re-registration
        /// on the main thread based on the returned status.
        /// </summary>
        internal McpStatus CheckStatusWithProjectDir(
            string projectDir, bool useHttpTransport, string claudePath, RuntimePlatform platform,
            bool isRemoteScope, string expectedPackageSource,
            bool attemptAutoRewrite = false, bool hasProjectDirOverride = false,
            string expectedHttpUrl = null, IDictionary<string, string> expectedHttpHeaders = null)
        {
            try
            {
                if (string.IsNullOrEmpty(claudePath))
                {
                    client.SetStatus(McpStatus.NotConfigured, "Claude CLI not found");
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return client.status;
                }

                // projectDir is required - no fallback to Application.dataPath
                if (string.IsNullOrEmpty(projectDir))
                {
                    throw new ArgumentNullException(nameof(projectDir), "Project directory must be provided for thread-safe execution");
                }

                // Read Claude Code config directly from ~/.claude.json instead of using slow CLI
                // This is instant vs 15+ seconds for `claude mcp list` which does health checks
                var configResult = ReadClaudeCodeConfig(projectDir);
                if (configResult.error != null)
                {
                    client.SetStatus(McpStatus.NotConfigured, configResult.error);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return client.status;
                }

                if (configResult.serverConfig == null)
                {
                    // UnityMCP not found in config
                    client.SetStatus(McpStatus.NotConfigured);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return client.status;
                }

                // UnityMCP is registered - check transport and version
                bool currentUseHttp = useHttpTransport;
                var serverConfig = configResult.serverConfig;

                // Determine registered transport type
                string registeredType = serverConfig["type"]?.ToString()?.ToLowerInvariant() ?? "";
                bool registeredWithHttp = registeredType == "http";
                bool registeredWithStdio = registeredType == "stdio";

                // Set the configured transport based on what we detected
                if (registeredWithHttp)
                {
                    client.configuredTransport = isRemoteScope
                        ? Models.ConfiguredTransport.HttpRemote
                        : Models.ConfiguredTransport.Http;
                }
                else if (registeredWithStdio)
                {
                    client.configuredTransport = Models.ConfiguredTransport.Stdio;
                }
                else
                {
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    client.SetStatus(McpStatus.IncorrectPath, "Unknown Claude Code transport. Review the registration.");
                    return client.status;
                }

                // Check for transport mismatch.
                // When a project dir override is active, the local UseHttpTransport
                // GUI setting may legitimately differ from the registered transport
                // in the overridden project, so skip this check.
                bool hasTransportMismatch = !hasProjectDirOverride
                    && ((currentUseHttp && registeredWithStdio) || (!currentUseHttp && registeredWithHttp));

                // For stdio transport, also check package version
                bool hasVersionMismatch = false;
                string configuredPackageSource = null;
                string mismatchReason = null;
                if (registeredWithStdio)
                {
                    configuredPackageSource = ExtractPackageSourceFromConfig(serverConfig);
                    if (!string.IsNullOrEmpty(configuredPackageSource) && !string.IsNullOrEmpty(expectedPackageSource))
                    {
                        // Check for exact match first
                        if (!string.Equals(configuredPackageSource, expectedPackageSource, StringComparison.OrdinalIgnoreCase))
                        {
                            hasVersionMismatch = true;

                            mismatchReason = "Configured server source doesn't match the selected source. Re-configure to update.";
                        }
                    }
                }

                // If there's any mismatch and auto-rewrite is enabled, re-register
                if (hasTransportMismatch || hasVersionMismatch)
                {
                    // Configure() requires main thread (accesses EditorPrefs, Application.dataPath)
                    // Only attempt auto-rewrite if we're on the main thread
                    bool isMainThread = System.Threading.Thread.CurrentThread.ManagedThreadId == 1;
                    if (attemptAutoRewrite && isMainThread)
                    {
                        string reason = hasTransportMismatch
                            ? $"Transport mismatch (registered: {(registeredWithHttp ? "HTTP" : "stdio")}, expected: {(currentUseHttp ? "HTTP" : "stdio")})"
                            : mismatchReason ?? $"Package version mismatch";
                        McpLog.Info($"{reason}. Re-registering...");
                        try
                        {
                            // Mark the mismatch before idempotent synchronous re-registration.
                            client.SetStatus(McpStatus.IncorrectPath);
                            Configure();
                            return client.status;
                        }
                        catch (Exception ex)
                        {
                            McpLog.Warn($"Auto-reregister failed: {ex.Message}");
                            client.SetStatus(McpStatus.IncorrectPath, $"Configuration mismatch. Click Configure to re-register.");
                            return client.status;
                        }
                    }
                    else
                    {
                        if (hasTransportMismatch)
                        {
                            string errorMsg = $"Transport mismatch: Claude Code is registered with {(registeredWithHttp ? "HTTP" : "stdio")} but current setting is {(currentUseHttp ? "HTTP" : "stdio")}. Click Configure to re-register.";
                            client.SetStatus(McpStatus.Error, errorMsg);
                            McpLog.Warn(errorMsg);
                        }
                        else
                        {
                            client.SetStatus(McpStatus.VersionMismatch, mismatchReason);
                        }
                        return client.status;
                    }
                }

                if (registeredWithHttp && !hasProjectDirOverride)
                {
                    string reason = null;
                    if (!UrlsEqual((string)serverConfig["url"], expectedHttpUrl))
                    {
                        reason = "Claude Code is registered with a different HTTP endpoint. Click Configure to update it.";
                    }
                    else if (isRemoteScope)
                    {
                        if (serverConfig["headersHelper"] != null || serverConfig["oauth"] != null)
                        {
                            reason = "Custom Claude Code authentication requires manual validation.";
                        }
                        else
                        {
                            ConfigJsonBuilder.TryValidateAuthHeaders(serverConfig["headers"], expectedHttpHeaders, out reason);
                        }
                    }
                    else
                    {
                        ClaudeHttpAuth.TryValidateLocalEntry(serverConfig, claudePath, out reason);
                    }

                    if (reason != null)
                    {
                        client.SetStatus(McpStatus.IncorrectPath, reason);
                        return client.status;
                    }
                }

                client.SetStatus(McpStatus.Configured);
                return client.status;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[Claude Code] CheckStatus exception: {ex.GetType().Name}: {ex.Message}");
                client.SetStatus(McpStatus.Error, ex.Message);
                client.configuredTransport = Models.ConfiguredTransport.Unknown;
            }

            return client.status;
        }

        public override void Configure()
        {
            Register();
        }

        /// <summary>
        /// Thread-safe version of Configure that uses pre-captured main-thread values.
        /// All parameters must be captured on the main thread before calling this method.
        /// </summary>
        public void ConfigureWithCapturedValues(
            string projectDir, string claudePath, string pathPrepend,
            bool useHttpTransport, string httpUrl,
            string uvxPath, string fromArgs, string packageName, string uvxDevFlags,
            string apiKey,
            Models.ConfiguredTransport serverTransport, bool unregister)
        {
            if (unregister)
            {
                UnregisterWithCapturedValues(projectDir, claudePath, pathPrepend);
            }
            else
            {
                RegisterWithCapturedValues(projectDir, claudePath, pathPrepend,
                    useHttpTransport, httpUrl, uvxPath, fromArgs, packageName, uvxDevFlags,
                    apiKey, serverTransport);
            }
        }

        /// <summary>
        /// Thread-safe registration using pre-captured values.
        /// </summary>
        private void RegisterWithCapturedValues(
            string projectDir, string claudePath, string pathPrepend,
            bool useHttpTransport, string httpUrl,
            string uvxPath, string fromArgs, string packageName, string uvxDevFlags,
            string apiKey,
            Models.ConfiguredTransport serverTransport)
        {
            if (string.IsNullOrEmpty(claudePath))
            {
                throw new InvalidOperationException("Claude CLI not found. Please install Claude Code first.");
            }

            string args;
            if (useHttpTransport)
            {
                args = BuildHttpRegistrationArguments(projectDir, claudePath, httpUrl,
                    serverTransport == Models.ConfiguredTransport.HttpRemote, apiKey);
            }
            else
            {
                // Use --scope local to register in the project-local config, avoiding conflicts with user-level config (#664)
                GetExistingConfigForRegistration(projectDir);
                args = $"mcp add --scope local --transport stdio UnityMCP -- \"{uvxPath}\" {uvxDevFlags}{fromArgs} {packageName}";
            }

            // Remove any existing registrations from ALL scopes to prevent stale config conflicts (#664)
            McpLog.Info("Removing any existing UnityMCP registrations from all scopes before adding...");
            RemoveFromAllScopes(claudePath, projectDir, pathPrepend);

            // Now add the registration
            if (!ExecPath.TryRun(claudePath, args, projectDir, out var stdout, out var stderr, 15000, pathPrepend))
            {
                throw new InvalidOperationException("Failed to register with Claude Code. Check its CLI path, configuration permissions and scope.");
            }

            McpLog.Info($"Successfully registered with Claude Code using {(useHttpTransport ? "HTTP" : "stdio")} transport.");
            client.SetStatus(McpStatus.Configured);
            client.configuredTransport = serverTransport;
        }

        /// <summary>
        /// Thread-safe unregistration using pre-captured values.
        /// </summary>
        private void UnregisterWithCapturedValues(string projectDir, string claudePath, string pathPrepend)
        {
            if (string.IsNullOrEmpty(claudePath))
            {
                throw new InvalidOperationException("Claude CLI not found. Please install Claude Code first.");
            }

            // Remove from ALL scopes to ensure complete cleanup (#664)
            McpLog.Info("Removing all UnityMCP registrations from all scopes...");
            RemoveFromAllScopes(claudePath, projectDir, pathPrepend);

            McpLog.Info("MCP server successfully unregistered from Claude Code.");
            client.SetStatus(McpStatus.NotConfigured);
            client.configuredTransport = Models.ConfiguredTransport.Unknown;
        }

        private void Register()
        {
            var pathService = MCPServiceLocator.Paths;
            string claudePath = pathService.GetClaudeCliPath();
            if (string.IsNullOrEmpty(claudePath))
            {
                throw new InvalidOperationException("Claude CLI not found. Please install Claude Code first.");
            }

            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;
            string projectDir = GetClientProjectDir();

            string args;
            if (useHttpTransport)
            {
                string httpUrl = HttpEndpointUtility.GetMcpRpcUrl();
                // Use --scope local to register in the project-local config, avoiding conflicts with user-level config (#664)
                bool remote = HttpEndpointUtility.IsRemoteScope();
                string apiKey = remote ? EditorPrefs.GetString(EditorPrefKeys.ApiKey, string.Empty) : null;
                args = BuildHttpRegistrationArguments(projectDir, claudePath, httpUrl, remote, apiKey);
            }
            else
            {
                GetExistingConfigForRegistration(projectDir);
                var (uvxPath, _, packageName) = AssetPathUtility.GetUvxCommandParts();
                string devFlags = AssetPathUtility.GetUvxDevFlags();
                string fromArgs = AssetPathUtility.GetBetaServerFromArgs(quoteFromPath: true);
                // Use --scope local to register in the project-local config, avoiding conflicts with user-level config (#664)
                args = $"mcp add --scope local --transport stdio UnityMCP -- \"{uvxPath}\" {devFlags}{fromArgs} {packageName}";
            }

            string pathPrepend = null;
            if (Application.platform == RuntimePlatform.OSXEditor)
            {
                pathPrepend = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin";
            }
            else if (Application.platform == RuntimePlatform.LinuxEditor)
            {
                pathPrepend = "/usr/local/bin:/usr/bin:/bin";
            }

            try
            {
                string claudeDir = Path.GetDirectoryName(claudePath);
                if (!string.IsNullOrEmpty(claudeDir))
                {
                    pathPrepend = string.IsNullOrEmpty(pathPrepend)
                        ? claudeDir
                        : $"{claudeDir}:{pathPrepend}";
                }
            }
            catch { }

            // Remove any existing registrations from ALL scopes to prevent stale config conflicts (#664)
            McpLog.Info("Removing any existing UnityMCP registrations from all scopes before adding...");
            RemoveFromAllScopes(claudePath, projectDir, pathPrepend);

            // Now add the registration with the current transport mode
            if (!ExecPath.TryRun(claudePath, args, projectDir, out var stdout, out var stderr, 15000, pathPrepend))
            {
                throw new InvalidOperationException("Failed to register with Claude Code. Check its CLI path, configuration permissions and scope.");
            }

            McpLog.Info($"Successfully registered with Claude Code using {(useHttpTransport ? "HTTP" : "stdio")} transport.");

            // Set status to Configured immediately after successful registration
            // The UI will trigger an async verification check separately to avoid blocking
            client.SetStatus(McpStatus.Configured);
            client.configuredTransport = HttpEndpointUtility.GetCurrentServerTransport();
        }

        public override void Unregister()
        {
            var pathService = MCPServiceLocator.Paths;
            string claudePath = pathService.GetClaudeCliPath();

            if (string.IsNullOrEmpty(claudePath))
            {
                throw new InvalidOperationException("Claude CLI not found. Please install Claude Code first.");
            }

            string projectDir = GetClientProjectDir();
            string pathPrepend = null;
            if (Application.platform == RuntimePlatform.OSXEditor)
            {
                pathPrepend = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin";
            }
            else if (Application.platform == RuntimePlatform.LinuxEditor)
            {
                pathPrepend = "/usr/local/bin:/usr/bin:/bin";
            }

            // Remove from ALL scopes to ensure complete cleanup (#664)
            McpLog.Info("Removing all UnityMCP registrations from all scopes...");
            RemoveFromAllScopes(claudePath, projectDir, pathPrepend);

            McpLog.Info("MCP server successfully unregistered from Claude Code.");
            client.SetStatus(McpStatus.NotConfigured);
            client.configuredTransport = Models.ConfiguredTransport.Unknown;
        }

        public override string GetManualSnippet()
        {
            string uvxPath = MCPServiceLocator.Paths.GetUvxPath();
            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;

            if (useHttpTransport)
            {
                string httpUrl = HttpEndpointUtility.GetMcpRpcUrl();
                bool remote = HttpEndpointUtility.IsRemoteScope();
                if (!remote)
                {
                    var entry = ClaudeHttpAuth.BuildLocalEntry(httpUrl, MCPServiceLocator.Paths.GetClaudeCliPath());
                    return "# JSON argument for: claude mcp add-json --scope local UnityMCP <json>\n"
                        + entry.ToString(Formatting.Indented)
                        + "\n\n# Use Configure for automatic registration and shell-safe argument quoting.";
                }
                string apiKey = remote ? EditorPrefs.GetString(EditorPrefKeys.ApiKey, string.Empty) : null;
                string headerArg = BuildHttpAuthArgument(httpUrl, remote, apiKey);
                return "# Register the MCP server with Claude Code:\n" +
                       $"claude mcp add --scope local --transport http UnityMCP {httpUrl}{headerArg}\n\n" +
                       "# Unregister the MCP server (from all scopes to clean up any stale configs):\n" +
                       "claude mcp remove --scope local UnityMCP\n" +
                       "claude mcp remove --scope user UnityMCP\n" +
                       "claude mcp remove --scope project UnityMCP\n\n" +
                       "# List registered servers:\n" +
                       "claude mcp list";
            }

            if (string.IsNullOrEmpty(uvxPath))
            {
                return "# Error: Configuration not available - check paths in Advanced Settings";
            }

            string devFlags = AssetPathUtility.GetUvxDevFlags();
            string fromArgs = AssetPathUtility.GetBetaServerFromArgs(quoteFromPath: true);

            return "# Register the MCP server with Claude Code:\n" +
                   $"claude mcp add --scope local --transport stdio UnityMCP -- \"{uvxPath}\" {devFlags}{fromArgs} mcp-for-unity\n\n" +
                   "# Unregister the MCP server (from all scopes to clean up any stale configs):\n" +
                   "claude mcp remove --scope local UnityMCP\n" +
                   "claude mcp remove --scope user UnityMCP\n" +
                   "claude mcp remove --scope project UnityMCP\n\n" +
                   "# List registered servers:\n" +
                   "claude mcp list";
        }

        public override IList<string> GetInstallationSteps() => new List<string>
        {
            "Ensure Claude CLI is installed",
            "Use Configure to add UnityMCP (or run claude mcp add UnityMCP)",
            "Local HTTP uses automatic token-file lookup on verified Claude Code 2.1.193+. Older or unknown CLIs should use stdio.",
            "Approve the local helper when Claude Code requests project trust. Start the server, then reconnect from /mcp if necessary; do not copy rotating tokens.",
            "Restart Claude Code"
        };

        /// <summary>
        /// Removes UnityMCP registration from all Claude Code configuration scopes (local, user, project).
        /// Also removes legacy entries from ~/.claude.json that the CLI scoped removal can't touch.
        /// This ensures no stale or conflicting configurations remain across different scopes.
        /// Also handles legacy "unityMCP" naming convention.
        /// </summary>
        private static void RemoveFromAllScopes(string claudePath, string projectDir, string pathPrepend)
        {
            // Remove from all three scopes to prevent stale configs causing connection issues.
            // See GitHub issue #664 - conflicting configs at different scopes can cause
            // Claude Code to connect with outdated/incorrect configuration.
            string[] scopes = { "local", "user", "project" };
            string[] names = { "UnityMCP", "unityMCP" }; // Include legacy naming

            foreach (var scope in scopes)
            {
                foreach (var name in names)
                {
                    ExecPath.TryRun(claudePath, $"mcp remove --scope {scope} {name}", projectDir, out _, out _, 5000, pathPrepend);
                }
            }

            // Also remove legacy entries directly from ~/.claude.json.
            // Older versions and manual CLI commands without --scope wrote mcpServers entries
            // into the projects section of ~/.claude.json. The scoped `claude mcp remove` commands
            // above won't touch these, leaving stale/conflicting configs behind.
            RemoveLegacyUserConfigEntries(projectDir);
        }

        /// <summary>
        /// Removes UnityMCP entries from the projects section of ~/.claude.json.
        /// These are legacy entries that were created by older versions or manual commands
        /// that didn't use --scope. The scoped `claude mcp remove` commands don't clean these up.
        /// </summary>
        private static void RemoveLegacyUserConfigEntries(string projectDir)
        {
            try
            {
                string configPath = GetUserConfigPath();
                if (!File.Exists(configPath))
                    return;

                string json = File.ReadAllText(configPath);
                var config = JObject.Parse(json);
                var projects = config["projects"] as JObject;
                if (projects == null)
                    return;

                string normalizedProjectDir = NormalizePath(projectDir);
                bool modified = false;

                // Walk all project entries looking for ones that match our project path
                foreach (var project in projects.Properties())
                {
                    string normalizedKey = NormalizePath(project.Name);

                    // Match exact path or parent paths (same logic as ReadUserScopeConfig)
                    if (!string.Equals(normalizedKey, normalizedProjectDir, StringComparison.OrdinalIgnoreCase))
                    {
                        // Also check if projectDir is a child of this config entry
                        if (!normalizedProjectDir.StartsWith(normalizedKey + "/", StringComparison.OrdinalIgnoreCase))
                            continue;
                    }

                    var mcpServers = project.Value?["mcpServers"] as JObject;
                    if (mcpServers == null)
                        continue;

                    // Remove UnityMCP/unityMCP entries (case-insensitive)
                    var toRemove = new List<string>();
                    foreach (var server in mcpServers.Properties())
                    {
                        if (string.Equals(server.Name, "UnityMCP", StringComparison.OrdinalIgnoreCase))
                        {
                            toRemove.Add(server.Name);
                        }
                    }

                    foreach (var name in toRemove)
                    {
                        mcpServers.Remove(name);
                        modified = true;
                        McpLog.Info($"Removed legacy '{name}' entry from ~/.claude.json for project '{project.Name}'");
                    }
                }

                if (modified)
                {
                    File.WriteAllText(configPath, config.ToString(Formatting.Indented));
                }
            }
            catch (Exception ex)
            {
                McpLog.Warn($"Failed to clean up legacy ~/.claude.json entries: {ex.Message}");
            }
        }

        private static string BuildHttpRegistrationArguments(string projectDir, string claudePath, string httpUrl, bool remote, string apiKey)
        {
            // Validate capability/configuration before the CLI removal path can change existing registrations.
            if (!remote && !ClaudeHttpAuth.SupportsHeadersHelper(claudePath))
            {
                throw new InvalidOperationException(ClaudeHttpAuth.UnsupportedMessage);
            }
            var existing = GetExistingConfigForRegistration(projectDir);
            if (!remote)
            {
                return ClaudeHttpAuth.BuildRegistrationArguments(ClaudeHttpAuth.BuildLocalEntry(httpUrl, claudePath, existing));
            }
            return $"mcp add --scope local --transport http UnityMCP {httpUrl}{BuildHttpAuthArgument(httpUrl, true, apiKey)}";
        }

        private static JObject GetExistingConfigForRegistration(string projectDir)
        {
            JObject existing = null;
            // Registration removes stale entries in each scope, so validate shadowed providers too.
            var scopes = new[]
            {
                ReadUserScopeConfig(projectDir),
                ReadProjectScopeConfig(projectDir),
                ReadUserScopeConfig(projectDir, userOnly: true),
                ReadLocalScopeConfig(projectDir)
            };
            foreach (var scope in scopes)
            {
                if (scope.error != null)
                {
                    throw new InvalidOperationException(scope.error);
                }
                ClaudeHttpAuth.EnsureManagedAuthentication(scope.serverConfig);
                if (existing == null)
                {
                    existing = scope.serverConfig;
                }
            }
            return existing;
        }

        private static string BuildHttpAuthArgument(string httpUrl, bool remote, string apiKey)
        {
            string value = remote ? apiKey : HttpEndpointUtility.ReadLocalAuthToken(new Uri(httpUrl));
            if (string.IsNullOrEmpty(value))
            {
                return string.Empty;
            }
            string header = remote ? AuthConstants.ApiKeyHeader : AuthConstants.LocalTokenHeader;
            return $" --header \"{header}: {SanitizeShellHeaderValue(value)}\"";
        }

        /// <summary>
        /// Sanitizes a value for safe inclusion inside a double-quoted shell argument.
        /// Escapes characters that are special within double quotes (", \, `, $, !)
        /// to prevent shell injection or argument splitting.
        /// </summary>
        private static string SanitizeShellHeaderValue(string value)
        {
            if (string.IsNullOrEmpty(value))
                return value;

            var sb = new System.Text.StringBuilder(value.Length);
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"':
                    case '\\':
                    case '`':
                    case '$':
                    case '!':
                        sb.Append('\\');
                        sb.Append(c);
                        break;
                    default:
                        sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        /// <summary>
        /// Extracts the package source (--from argument value) from claude mcp get output.
        /// The output format includes args like: --from "mcpforunityserver==9.0.1"
        /// </summary>
        private static string ExtractPackageSourceFromCliOutput(string cliOutput)
        {
            if (string.IsNullOrEmpty(cliOutput))
                return null;

            // Look for --from followed by the package source
            // The CLI output may have it quoted or unquoted
            int fromIndex = cliOutput.IndexOf("--from", StringComparison.OrdinalIgnoreCase);
            if (fromIndex < 0)
                return null;

            // Move past "--from" and any whitespace
            int startIndex = fromIndex + 6;
            while (startIndex < cliOutput.Length && char.IsWhiteSpace(cliOutput[startIndex]))
                startIndex++;

            if (startIndex >= cliOutput.Length)
                return null;

            // Check if value is quoted
            char quoteChar = cliOutput[startIndex];
            if (quoteChar == '"' || quoteChar == '\'')
            {
                startIndex++;
                int endIndex = cliOutput.IndexOf(quoteChar, startIndex);
                if (endIndex > startIndex)
                    return cliOutput.Substring(startIndex, endIndex - startIndex);
            }
            else
            {
                // Unquoted - read until whitespace or end of line
                int endIndex = startIndex;
                while (endIndex < cliOutput.Length && !char.IsWhiteSpace(cliOutput[endIndex]))
                    endIndex++;

                if (endIndex > startIndex)
                    return cliOutput.Substring(startIndex, endIndex - startIndex);
            }

            return null;
        }

        /// <summary>
        /// Reads Claude Code configuration from both local-scope (.claude/mcp.json in the project)
        /// and user-scope (~/.claude.json). Local scope takes precedence, matching Claude Code's
        /// own config resolution order.
        /// This is much faster than running `claude mcp list` which does health checks on all servers.
        /// </summary>
        private static (JObject serverConfig, string error) ReadClaudeCodeConfig(string projectDir)
        {
            try
            {
                // Current CLI precedence: local entries in .claude.json, project .mcp.json, then user entries.
                var local = ReadUserScopeConfig(projectDir);
                if (local.serverConfig != null || local.error != null)
                {
                    return local;
                }
                var project = ReadProjectScopeConfig(projectDir);
                if (project.serverConfig != null || project.error != null)
                {
                    return project;
                }
                var user = ReadUserScopeConfig(projectDir, userOnly: true);
                if (user.serverConfig != null || user.error != null)
                {
                    return user;
                }
                return ReadLocalScopeConfig(projectDir);
            }
            catch (Exception ex)
            {
                return (null, $"Error reading Claude config: {ex.Message}");
            }
        }

        /// <summary>
        /// Reads the legacy {projectDir}/.claude/mcp.json location as a migration fallback.
        /// </summary>
        private static (JObject serverConfig, string error) ReadLocalScopeConfig(string projectDir)
        {
            try
            {
                if (string.IsNullOrEmpty(projectDir))
                    return (null, null);

                string localConfigPath = Path.Combine(projectDir, ".claude", "mcp.json");
                if (!File.Exists(localConfigPath))
                    return (null, null);

                string json = File.ReadAllText(localConfigPath);
                var config = JObject.Parse(json);
                return (FindUnityServer(config["mcpServers"]), null);
            }
            catch (Exception ex)
            {
                return (null, $"Error reading local Claude config: {ex.Message}");
            }
        }

        private static (JObject serverConfig, string error) ReadProjectScopeConfig(string projectDir)
        {
            try
            {
                var directory = string.IsNullOrEmpty(projectDir) ? null : new DirectoryInfo(projectDir);
                while (directory != null)
                {
                    string configPath = Path.Combine(directory.FullName, ".mcp.json");
                    if (File.Exists(configPath))
                    {
                        var config = JObject.Parse(File.ReadAllText(configPath));
                        return (FindUnityServer(config["mcpServers"]), null);
                    }
                    string gitPath = Path.Combine(directory.FullName, ".git");
                    if (Directory.Exists(gitPath) || File.Exists(gitPath))
                    {
                        break;
                    }
                    directory = directory.Parent;
                }
                return (null, null);
            }
            catch (Exception ex)
            {
                return (null, $"Error reading project Claude config: {ex.Message}");
            }
        }

        private static JObject FindUnityServer(JToken token)
        {
            if (token == null)
            {
                return null;
            }
            if (!(token is JObject servers))
            {
                throw new FormatException("Claude Code mcpServers must be an object.");
            }
            foreach (var server in servers.Properties())
            {
                if (string.Equals(server.Name, "UnityMCP", StringComparison.OrdinalIgnoreCase))
                {
                    return server.Value as JObject
                        ?? throw new FormatException("Claude Code UnityMCP registration must be an object.");
                }
            }
            return null;
        }

        /// <summary>
        /// Reads current local registrations from .claude.json's projects section, or its user-level entry.
        /// </summary>
        private static (JObject serverConfig, string error) ReadUserScopeConfig(string projectDir, bool userOnly = false)
        {
            try
            {
                string configPath = GetUserConfigPath();

                if (!File.Exists(configPath))
                    return (null, null);

                string configJson = File.ReadAllText(configPath);
                var config = JObject.Parse(configJson);
                if (userOnly)
                {
                    return (FindUnityServer(config["mcpServers"]), null);
                }

                var projects = config["projects"] as JObject ?? new JObject();

                // Build a dictionary of normalized paths for quick lookup.
                // Duplicate keys (forward/backslash variants of the same path)
                // are merged by preferring the entry that actually carries a
                // UnityMCP registration: JSON property order is not correlated
                // with recency across variants, so blind last-entry-wins lets a
                // stale variant without mcpServers shadow a real registration
                // and CheckStatus reports NotConfigured despite a working
                // `claude mcp add --scope local` setup.
                var normalizedProjects = new Dictionary<string, JObject>(StringComparer.OrdinalIgnoreCase);
                foreach (var project in projects.Properties())
                {
                    string normalizedPath = NormalizePath(project.Name);
                    var value = project.Value as JObject;
                    if (!normalizedProjects.TryGetValue(normalizedPath, out var existing)
                        || RegistrationRank(value) >= RegistrationRank(existing))
                    {
                        normalizedProjects[normalizedPath] = value;
                    }
                }

                // Walk up the directory tree to find a matching project config
                // Claude Code may be configured at a parent directory (e.g., repo root)
                // while Unity project is in a subdirectory (e.g., TestProjects/UnityMCPTests)
                var fromProjectDir = FindUnityServerFromWalk(normalizedProjects, NormalizePath(projectDir));
                if (fromProjectDir != null)
                    return (fromProjectDir, null);

                // `claude mcp add --scope local` keys the registration by the
                // git MAIN repo root. When the Unity project lives in a linked
                // worktree, that root is a sibling path the ancestor walk above
                // can never reach — retry from the parsed main root.
                string mainRoot = GetGitMainRepoRoot(projectDir);
                if (!string.IsNullOrEmpty(mainRoot)
                    && !string.Equals(mainRoot, NormalizePath(projectDir), StringComparison.OrdinalIgnoreCase))
                {
                    var fromMainRoot = FindUnityServerFromWalk(normalizedProjects, mainRoot);
                    if (fromMainRoot != null)
                        return (fromMainRoot, null);
                }

                return (null, null);
            }
            catch (Exception ex)
            {
                return (null, $"Error reading user Claude config: {ex.Message}");
            }
        }

        internal static string GetUserConfigPath()
        {
            string configDirectory = Environment.GetEnvironmentVariable("CLAUDE_CONFIG_DIR");
            if (string.IsNullOrWhiteSpace(configDirectory))
            {
                configDirectory = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            }
            return Path.Combine(configDirectory, ".claude.json");
        }

        /// <summary>
        /// Walks up from startDir (normalized) looking for a project entry with
        /// a UnityMCP registration. Stops at the first project entry found even
        /// if it lacks UnityMCP (a configured project boundary).
        /// </summary>
        private static JObject FindUnityServerFromWalk(Dictionary<string, JObject> normalizedProjects, string startDir)
        {
            string currentDir = startDir;
            while (!string.IsNullOrEmpty(currentDir))
            {
                if (normalizedProjects.TryGetValue(currentDir, out var projectConfig))
                {
                    // Found the project but no UnityMCP - don't continue walking up
                    return FindUnityServer(projectConfig?["mcpServers"]);
                }

                // Move up one directory
                int lastSlash = currentDir.LastIndexOf('/');
                if (lastSlash <= 0)
                    break;
                currentDir = currentDir.Substring(0, lastSlash);
            }
            return null;
        }

        /// <summary>
        /// For a linked git worktree, the main repo root parsed from the .git
        /// pointer file ("gitdir: &lt;root&gt;/.git/worktrees/&lt;name&gt;"),
        /// normalized. Null for a regular checkout (.git directory) or
        /// anything unparseable.
        /// </summary>
        private static string GetGitMainRepoRoot(string dir)
        {
            try
            {
                string gitPath = Path.Combine(dir, ".git");
                if (!File.Exists(gitPath)) return null;
                string line = File.ReadAllText(gitPath).Trim();
                if (!line.StartsWith("gitdir:", StringComparison.OrdinalIgnoreCase)) return null;
                string gitDir = line.Substring("gitdir:".Length).Trim();
                if (!Path.IsPathRooted(gitDir)) gitDir = Path.Combine(dir, gitDir);
                gitDir = NormalizePath(Path.GetFullPath(gitDir));
                int i = gitDir.LastIndexOf("/.git/", StringComparison.OrdinalIgnoreCase);
                return i > 0 ? gitDir.Substring(0, i) : null;
            }
            catch { return null; }
        }

        /// <summary>
        /// Ranks a ~/.claude.json project entry for duplicate-key merging:
        /// 2 = has a UnityMCP registration, 1 = has other mcpServers, 0 = none.
        /// </summary>
        private static int RegistrationRank(JObject projectConfig)
        {
            if (!(projectConfig?["mcpServers"] is JObject servers)) return 0;
            foreach (var server in servers.Properties())
            {
                if (string.Equals(server.Name, "UnityMCP", StringComparison.OrdinalIgnoreCase))
                    return 2;
            }
            return servers.HasValues ? 1 : 0;
        }

        /// <summary>
        /// Normalizes a file path for comparison (handles forward/back slashes, trailing slashes).
        /// </summary>
        private static string NormalizePath(string path)
        {
            if (string.IsNullOrEmpty(path))
                return path;

            // Replace backslashes with forward slashes and remove trailing slashes
            return path.Replace('\\', '/').TrimEnd('/');
        }

        /// <summary>
        /// Extracts the package source from Claude Code JSON config.
        /// For stdio servers, this is in the args array after "--from".
        /// </summary>
        private static string ExtractPackageSourceFromConfig(JObject serverConfig)
        {
            if (serverConfig == null)
                return null;

            var args = serverConfig["args"] as JArray;
            if (args == null)
                return null;

            // Look for --from argument (either "--from VALUE" or "--from=VALUE" format)
            bool foundFrom = false;
            foreach (var arg in args)
            {
                string argStr = arg?.ToString();
                if (argStr == null)
                    continue;

                if (foundFrom)
                {
                    // This is the package source following --from
                    return argStr;
                }

                if (argStr == "--from")
                {
                    foundFrom = true;
                }
                else if (argStr.StartsWith("--from=", StringComparison.OrdinalIgnoreCase))
                {
                    // Handle --from=VALUE format
                    return argStr.Substring(7).Trim('"', '\'');
                }
            }

            return null;
        }
    }
}
