using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using MCPForUnity.External.Tommy;
using Newtonsoft.Json.Linq;
using UnityEditor;

namespace MCPForUnity.Editor.Clients
{
    /// <summary>Codex (TOML) configurator.</summary>
    public abstract class CodexMcpConfigurator : McpClientConfiguratorBase
    {
        public CodexMcpConfigurator(McpClient client)
            : base(client) { }

        public override string GetConfigPath() => CurrentOsPath();

        public override bool IsInstalled => ParentDirectoryExists(GetConfigPath());

        public override McpStatus CheckStatus(bool attemptAutoRewrite = true) => CheckStatus(attemptAutoRewrite, false).Value;

        internal override McpStatus? RefreshForStdioMigration() => CheckStatus(true, true);

        private McpStatus? CheckStatus(bool attemptAutoRewrite, bool migrationOnly)
        {
            try
            {
                string path = GetConfigPath();
                if (!File.Exists(path))
                {
                    if (migrationOnly)
                        return null;
                    client.SetStatus(McpStatus.NotConfigured);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return client.status;
                }

                string toml = File.ReadAllText(path);
                var automatic = attemptAutoRewrite
                    ? GeneratedClientConfigOwnership.AutomaticRefresh.Capture("Codex", path, GeneratedStdioEntry(toml), migrationOnly)
                    : null;
                if (migrationOnly && automatic == null)
                    return null;
                if (CodexConfigHelper.TryParseCodexServer(toml, out var command, out var args, out var url))
                {
                    // Determine and set the configured transport type
                    if (!string.IsNullOrEmpty(url))
                    {
                        // Distinguish HTTP Local from HTTP Remote
                        string remoteRpcUrl = HttpEndpointUtility.GetStoredRemoteMcpRpcUrl();
                        if (!string.IsNullOrEmpty(remoteRpcUrl) && UrlsEqual(url, remoteRpcUrl))
                        {
                            client.configuredTransport = Models.ConfiguredTransport.HttpRemote;
                        }
                        else
                        {
                            client.configuredTransport = Models.ConfiguredTransport.Http;
                        }
                    }
                    else if (args != null && args.Length > 0)
                    {
                        client.configuredTransport = Models.ConfiguredTransport.Stdio;
                    }
                    else
                    {
                        client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    }

                    if (!CodexConfigHelper.TryValidateServerEnabled(toml, out var activationReason))
                    {
                        client.SetStatus(McpStatus.IncorrectPath, activationReason);
                        return client.status;
                    }

                    bool matches = false;
                    bool hasVersionMismatch = false;
                    string mismatchReason = null;

                    if (
                        client.configuredTransport == Models.ConfiguredTransport.Stdio
                        && !EditorConfigurationCache.Instance.UseHttpTransport
                        && GeneratedStdioEntry(toml) != null
                        && JsonFileMcpConfigurator.GeneratedExecutableIssue(command) is string executableIssue
                    )
                    {
                        client.SetStatus(McpStatus.IncorrectPath, executableIssue);
                        return client.status;
                    }

                    if (client.configuredTransport != HttpEndpointUtility.GetCurrentServerTransport())
                    {
                        if (automatic == null)
                        {
                            client.SetStatus(
                                McpStatus.IncorrectPath,
                                "Codex transport does not match the selected server transport. Click Configure to update."
                            );
                            return client.status;
                        }
                    }
                    else if (!string.IsNullOrEmpty(url))
                    {
                        matches = UrlsEqual(url, HttpEndpointUtility.GetMcpRpcUrl());
                        if (matches && !CodexConfigHelper.TryValidateHttpServer(toml, null, out var reason))
                        {
                            client.SetStatus(McpStatus.IncorrectPath, reason);
                            return client.status;
                        }
                    }
                    else if (args != null && args.Length > 0)
                    {
                        // Use beta-aware expected package source for comparison
                        string expected = GetExpectedPackageSourceForValidation();
                        string configured = McpConfigurationHelper.ExtractUvxUrl(args);

                        if (!string.IsNullOrEmpty(configured) && !string.IsNullOrEmpty(expected))
                        {
                            if (McpConfigurationHelper.PathsEqual(configured, expected))
                            {
                                matches = true;
                            }
                            else
                            {
                                hasVersionMismatch = true;
                                mismatchReason = "Configured server source doesn't match the selected source. Re-configure to update.";
                            }
                        }
                    }

                    if (matches)
                    {
                        client.SetStatus(McpStatus.Configured);
                        return client.status;
                    }

                    if (hasVersionMismatch)
                    {
                        if (automatic != null)
                        {
                            WriteGeneratedConfiguration(path, automatic);
                            return client.status;
                        }
                        client.SetStatus(McpStatus.VersionMismatch, mismatchReason);
                        return client.status;
                    }
                }
                else
                {
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                }

                if (automatic != null)
                {
                    WriteGeneratedConfiguration(path, automatic);
                }
                else
                {
                    client.SetStatus(McpStatus.IncorrectPath);
                }
            }
            catch (Exception ex)
            {
                client.SetStatus(McpStatus.Error, ex.Message);
                client.configuredTransport = Models.ConfiguredTransport.Unknown;
            }

            return client.status;
        }

        public override void Configure() => WriteGeneratedConfiguration(GetConfigPath(), null);

        private void WriteGeneratedConfiguration(string path, GeneratedClientConfigOwnership.AutomaticRefresh automatic)
        {
            string result = McpConfigurationHelper.ConfigureCodexClient(path, client, null, automatic, recordOwnership: true);
            if (result == "Configured successfully")
            {
                CheckStatus(attemptAutoRewrite: false);
            }
            else
            {
                throw new InvalidOperationException(result);
            }
        }

        internal static string GeneratedStdioEntry(string document)
        {
            var root = CodexConfigHelper.ParseConfiguration(document);
            if (
                root.TryGetNode("mcpServers", out _)
                || !root.TryGetNode("mcp_servers", out var serversNode)
                || serversNode is not TomlTable servers
                || !servers.TryGetNode("unityMCP", out var entryNode)
                || entryNode is not TomlTable entry
                || entry.Keys.Any(key => key != "command" && key != "args" && key != "env" && key != "startup_timeout_sec" && key != "enabled")
                || !entry.TryGetNode("command", out var commandNode)
                || commandNode is not TomlString command
                || !entry.TryGetNode("args", out var argsNode)
                || argsNode is not TomlArray args
                || args.Children.Any(arg => arg is not TomlString)
            )
                return null;
            if (entry.TryGetNode("enabled", out var enabled) && (enabled is not TomlBoolean flag || !flag.Value))
                return null;
            if (entry.TryGetNode("startup_timeout_sec", out var timeout) && (timeout is not TomlInteger seconds || seconds.Value != 300))
                return null;
            if (
                entry.TryGetNode("env", out var environment)
                && (environment is not TomlTable env || env.Keys.Any(key => key != "SystemRoot" || env[key] is not TomlString))
            )
                return null;
            var launch = new JObject { ["command"] = command.Value, ["args"] = new JArray(args.Children.Select(arg => ((TomlString)arg).Value)) };
            return JsonFileMcpConfigurator.IsGeneratedStdioEntry(launch) ? entry.ToInlineToml() : null;
        }

        public override void Unregister()
        {
            string path = GetConfigPath();
            try
            {
                ClientConfigurationChangeService.Shared.MutateFile(path, CodexConfigHelper.RemoveCodexServerBlock);
                GeneratedClientConfigOwnership.Record("Codex", path, null);
                client.SetStatus(McpStatus.NotConfigured);
                client.configuredTransport = Models.ConfiguredTransport.Unknown;
            }
            catch (Exception ex)
            {
                // Token files belong to the shared endpoint and other client registrations.
                // Unregister removes only the reserved unityMCP TOML entry.
                throw new InvalidOperationException("Failed to unregister Codex unityMCP: " + ex.Message, ex);
            }
        }

        public override string GetManualSnippet()
        {
            string uvx = EditorConfigurationCache.Instance.UseHttpTransport ? null : GetUvxPathOrError();
            return CodexConfigHelper.BuildCodexServerBlock(uvx);
        }

        public override IList<string> GetInstallationSteps() =>
            new List<string>
            {
                "Open the config file at the path above",
                "Merge the TOML into the existing unityMCP entry, replacing fields from the previous transport",
                "Current Codex does not need rmcp_client. Existing legacy feature settings are preserved",
                "Local HTTP uses automatic token-file lookup when Codex support is confirmed. Update Codex or use stdio if unavailable",
                "Start the local server before connecting. Token rotation does not require reconfiguration; reconnect if the client does not recover",
                "Save and restart Codex",
            };
    }
}
