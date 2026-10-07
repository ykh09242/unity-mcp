using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;

namespace MCPForUnity.Editor.Clients
{

    /// <summary>Codex (TOML) configurator.</summary>
    public abstract class CodexMcpConfigurator : McpClientConfiguratorBase
    {
        public CodexMcpConfigurator(McpClient client) : base(client) { }

        public override string GetConfigPath() => CurrentOsPath();

        public override bool IsInstalled => ParentDirectoryExists(GetConfigPath());

        public override McpStatus CheckStatus(bool attemptAutoRewrite = true)
        {
            try
            {
                string path = GetConfigPath();
                if (!File.Exists(path))
                {
                    client.SetStatus(McpStatus.NotConfigured);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return client.status;
                }

                string toml = File.ReadAllText(path);
                if (CodexConfigHelper.TryParseCodexServer(toml, out _, out var args, out var url))
                {
                    // Determine and set the configured transport type
                    if (!string.IsNullOrEmpty(url))
                    {
                        // Distinguish HTTP Local from HTTP Remote
                        string remoteRpcUrl = HttpEndpointUtility.GetRemoteMcpRpcUrl();
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

                    bool matches = false;
                    bool hasVersionMismatch = false;
                    string mismatchReason = null;

                    if (client.configuredTransport != HttpEndpointUtility.GetCurrentServerTransport())
                    {
                        if (!attemptAutoRewrite)
                        {
                            client.SetStatus(McpStatus.IncorrectPath,
                                "Codex transport does not match the selected server transport. Click Configure to update.");
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
                        if (attemptAutoRewrite)
                        {
                            string result = McpConfigurationHelper.ConfigureCodexClient(path, client);
                            if (result == "Configured successfully")
                            {
                                return CheckStatus(attemptAutoRewrite: false);
                            }
                        }
                        client.SetStatus(McpStatus.VersionMismatch, mismatchReason);
                        return client.status;
                    }
                }
                else
                {
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                }

                if (attemptAutoRewrite)
                {
                    string result = McpConfigurationHelper.ConfigureCodexClient(path, client);
                    if (result == "Configured successfully")
                    {
                        return CheckStatus(attemptAutoRewrite: false);
                    }
                    else
                    {
                        client.SetStatus(McpStatus.IncorrectPath);
                    }
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

        public override void Configure()
        {
            string path = GetConfigPath();
            McpConfigurationHelper.EnsureConfigDirectoryExists(path);
            string result = McpConfigurationHelper.ConfigureCodexClient(path, client);
            if (result == "Configured successfully")
            {
                CheckStatus(attemptAutoRewrite: false);
            }
            else
            {
                throw new InvalidOperationException(result);
            }
        }

        public override string GetManualSnippet()
        {
            try
            {
                string uvx = EditorConfigurationCache.Instance.UseHttpTransport ? null : GetUvxPathOrError();
                return CodexConfigHelper.BuildCodexServerBlock(uvx);
            }
            catch (Exception ex)
            {
                return $"# error: {ex.Message}";
            }
        }

        public override IList<string> GetInstallationSteps() => new List<string>
        {
            "Open the config file at the path above",
            "Merge the TOML into the existing unityMCP entry, replacing fields from the previous transport",
            "Current Codex does not need rmcp_client. Existing legacy feature settings are preserved",
            "Local HTTP uses automatic token-file lookup when Codex support is confirmed. Update Codex or use stdio if unavailable",
            "Start the local server before connecting. Token rotation does not require reconfiguration; reconnect if the client does not recover",
            "Save and restart Codex"
        };
    }
}
