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
    /// <summary>JSON-file based configurator (Cursor, Windsurf, VS Code, etc.).</summary>
    public abstract class JsonFileMcpConfigurator : McpClientConfiguratorBase
    {
        public JsonFileMcpConfigurator(McpClient client)
            : base(client) { }

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

                string configJson = File.ReadAllText(path);
                string[] args = null;
                string configuredUrl = null;
                bool configExists = false;
                JObject serverConfig = null;

                {
                    var rootConfig = JsonConvert.DeserializeObject<JToken>(configJson) as JObject;
                    JToken unityToken = null;
                    if (rootConfig != null)
                    {
                        string containerKey = string.IsNullOrEmpty(client.ServerContainerKey) ? "mcpServers" : client.ServerContainerKey;
                        unityToken = client.IsVsCodeLayout
                            ? rootConfig["servers"]?["unityMCP"] ?? rootConfig["mcp"]?["servers"]?["unityMCP"]
                            : rootConfig[containerKey]?["unityMCP"];
                    }

                    if (unityToken is JObject unityObj)
                    {
                        configExists = true;
                        serverConfig = unityObj;

                        var argsToken = unityObj["args"];
                        if (argsToken is JArray)
                        {
                            args = argsToken.ToObject<string[]>();
                        }

                        // Clients diverge on the HTTP URL property name: "url" (Cursor/VSCode/Claude),
                        // "serverUrl" (Antigravity/Windsurf), "httpUrl" (Gemini CLI). Accept all three
                        // so CheckStatus matches what Configure() actually wrote.
                        var urlToken = unityObj["url"] ?? unityObj["serverUrl"] ?? unityObj["httpUrl"];
                        if (urlToken != null && urlToken.Type != JTokenType.Null)
                        {
                            configuredUrl = urlToken.ToString();
                        }
                    }
                }

                if (!configExists)
                {
                    client.SetStatus(McpStatus.MissingConfig);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return client.status;
                }

                // Determine and set the configured transport type
                if (args != null && args.Length > 0)
                {
                    client.configuredTransport = Models.ConfiguredTransport.Stdio;
                }
                else if (!string.IsNullOrEmpty(configuredUrl))
                {
                    // Distinguish HTTP Local from HTTP Remote by matching against both URLs
                    string localRpcUrl = HttpEndpointUtility.GetLocalMcpRpcUrl();
                    string remoteRpcUrl = HttpEndpointUtility.GetRemoteMcpRpcUrl();
                    if (!string.IsNullOrEmpty(remoteRpcUrl) && UrlsEqual(configuredUrl, remoteRpcUrl))
                    {
                        client.configuredTransport = Models.ConfiguredTransport.HttpRemote;
                    }
                    else
                    {
                        client.configuredTransport = Models.ConfiguredTransport.Http;
                    }
                }
                else
                {
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                }

                bool matches = false;
                bool hasVersionMismatch = false;
                string mismatchReason = null;
                bool expectsHttp = client.SupportsHttpTransport && EditorConfigurationCache.Instance.UseHttpTransport;

                if (args != null && args.Length > 0)
                {
                    // Use beta-aware expected package source for comparison
                    string expectedUvxUrl = GetExpectedPackageSourceForValidation();
                    string configuredUvxUrl = McpConfigurationHelper.ExtractUvxUrl(args);

                    if (!string.IsNullOrEmpty(configuredUvxUrl) && !string.IsNullOrEmpty(expectedUvxUrl))
                    {
                        if (McpConfigurationHelper.PathsEqual(configuredUvxUrl, expectedUvxUrl))
                        {
                            matches = !expectsHttp && string.IsNullOrEmpty(configuredUrl);
                        }
                        else
                        {
                            hasVersionMismatch = true;
                            mismatchReason = "Configured server source doesn't match the selected source. Re-configure to update.";
                        }
                    }
                }
                else if (!string.IsNullOrEmpty(configuredUrl))
                {
                    // Match against the active scope's URL
                    string expectedUrl = HttpEndpointUtility.GetMcpRpcUrl();
                    matches = expectsHttp && UrlsEqual(configuredUrl, expectedUrl) && serverConfig["command"] == null && serverConfig["args"] == null;
                }

                if (matches)
                {
                    string reason = null;
                    if (serverConfig["disabled"]?.Value<bool>() == true || serverConfig["enabled"]?.Value<bool>() == false)
                    {
                        reason = "The client entry is disabled. Enable it in the client configuration.";
                    }
                    else if (expectsHttp)
                    {
                        var expectedHeaders = HttpEndpointUtility.GetAuthHeaders();
                        if (serverConfig["headersHelper"] != null || serverConfig["http_headers_helper"] != null)
                        {
                            reason = "Custom HTTP authentication requires manual validation. The helper was not executed.";
                        }
                        else if (!HttpEndpointUtility.IsRemoteScope() && !expectedHeaders.ContainsKey(AuthConstants.LocalTokenHeader))
                        {
                            reason = "Start the local server before validating its current authentication token.";
                        }
                        else
                        {
                            ConfigJsonBuilder.TryValidateAuthHeaders(serverConfig["headers"], expectedHeaders, out reason);
                        }
                    }
                    if (reason != null)
                    {
                        client.SetStatus(McpStatus.IncorrectPath, reason);
                        return client.status;
                    }
                    client.SetStatus(McpStatus.Configured);
                    return client.status;
                }

                if (hasVersionMismatch)
                {
                    if (attemptAutoRewrite)
                    {
                        var result = McpConfigurationHelper.WriteMcpConfiguration(path, client);
                        if (result == "Configured successfully")
                        {
                            return CheckStatus(attemptAutoRewrite: false);
                        }
                        else
                        {
                            client.SetStatus(McpStatus.VersionMismatch, mismatchReason);
                        }
                    }
                    else
                    {
                        client.SetStatus(McpStatus.VersionMismatch, mismatchReason);
                    }
                }
                else if (attemptAutoRewrite)
                {
                    var result = McpConfigurationHelper.WriteMcpConfiguration(path, client);
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
            // Always idempotent-write. The per-client UI button routes through Unregister
            // when the user clicks while the client is already Configured; the bulk
            // "Configure All" path calls this directly and expects an unconditional write.
            string path = GetConfigPath();
            McpConfigurationHelper.EnsureConfigDirectoryExists(path);
            string result = McpConfigurationHelper.WriteMcpConfiguration(path, client);
            if (result == "Configured successfully")
            {
                CheckStatus(attemptAutoRewrite: false);
            }
            else
            {
                throw new InvalidOperationException(result);
            }
        }

        internal bool ShouldUnregister =>
            client.status == McpStatus.Configured
            && client.configuredTransport == (client.SupportsHttpTransport ? HttpEndpointUtility.GetCurrentServerTransport() : ConfiguredTransport.Stdio);

        public override string GetConfigureActionLabel() => ShouldUnregister ? "Unregister" : "Configure";

        /// <summary>
        /// Removes the unityMCP entry from the client's JSON config (VS Code-style
        /// `servers` / `mcp.servers` layouts, the standard `mcpServers` layout, or a
        /// client-specific container such as Kilo's `mcp`). Leaves the file in place so we
        /// don't clobber other servers the user has configured.
        /// </summary>
        public override void Unregister()
        {
            string path = GetConfigPath();
            try
            {
                if (!File.Exists(path))
                {
                    client.SetStatus(McpStatus.NotConfigured);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return;
                }

                var root = JsonConvert.DeserializeObject<JToken>(File.ReadAllText(path)) as JObject;
                if (root == null)
                {
                    client.SetStatus(McpStatus.NotConfigured);
                    client.configuredTransport = Models.ConfiguredTransport.Unknown;
                    return;
                }

                bool removed = false;
                if (client.IsVsCodeLayout)
                {
                    if ((root["servers"] as JObject)?.Remove("unityMCP") == true)
                        removed = true;
                    if ((root["mcp"]?["servers"] as JObject)?.Remove("unityMCP") == true)
                        removed = true;
                }
                else
                {
                    string containerKey = string.IsNullOrEmpty(client.ServerContainerKey) ? "mcpServers" : client.ServerContainerKey;
                    if ((root[containerKey] as JObject)?.Remove("unityMCP") == true)
                        removed = true;
                }

                if (removed)
                {
                    File.WriteAllText(path, root.ToString(Formatting.Indented));
                }

                client.SetStatus(McpStatus.NotConfigured);
                client.configuredTransport = Models.ConfiguredTransport.Unknown;
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException($"Failed to unregister: {ex.Message}", ex);
            }
        }

        public override string GetManualSnippet()
        {
            try
            {
                string uvx = client.SupportsHttpTransport && EditorConfigurationCache.Instance.UseHttpTransport ? null : GetUvxPathOrError();
                return ConfigJsonBuilder.BuildManualConfigJson(uvx, client);
            }
            catch (Exception ex)
            {
                var errorObj = new { error = ex.Message };
                return JsonConvert.SerializeObject(errorObj);
            }
        }

        public override IList<string> GetInstallationSteps() => new List<string> { "Configuration steps not available for this client." };
    }
}
