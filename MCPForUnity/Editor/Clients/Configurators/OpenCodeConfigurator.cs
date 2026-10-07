using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Clients.Configurators
{
    /// <summary>
    /// Configurator for OpenCode (opencode.ai) - a Go-based terminal AI coding assistant.
    /// OpenCode uses ~/.config/opencode/opencode.json with a custom "mcp" format.
    /// </summary>
    public class OpenCodeConfigurator : McpClientConfiguratorBase
    {
        private const string ServerName = "unityMCP";
        private const string SchemaUrl = "https://opencode.ai/config.json";
        private const string RemoteType = "remote";
        private const string LocalType = "local";

        public OpenCodeConfigurator() : base(new McpClient
        {
            name = "OpenCode",
            windowsConfigPath = BuildConfigPath(),
            macConfigPath = BuildConfigPath(),
            linuxConfigPath = BuildConfigPath()
        })
        { }

        private static string BuildConfigPath()
        {
            string xdgConfigHome = Environment.GetEnvironmentVariable("XDG_CONFIG_HOME");
            string configBase = !string.IsNullOrEmpty(xdgConfigHome)
                ? xdgConfigHome
                : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".config");
            return Path.Combine(configBase, "opencode", "opencode.json");
        }

        public override string GetConfigPath() => CurrentOsPath();

        /// <summary>
        /// Attempts to load and parse the config file.
        /// Returns null only if the file doesn't exist.
        /// Returns parsed JObject if valid JSON found.
        /// Logs warning if file exists but contains malformed JSON.
        /// </summary>
        private JObject TryLoadConfig(string path)
        {
            if (!File.Exists(path))
                return null;

            string content;
            try
            {
                content = File.ReadAllText(path);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("Could not read the existing OpenCode configuration. It was not changed.", ex);
            }

            try
            {
                return JsonConvert.DeserializeObject<JObject>(content)
                    ?? throw new JsonException("The configuration must be an object.");
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException("The existing OpenCode configuration is not a JSON object. Fix it manually; it was not changed.", ex);
            }
        }

        public override McpStatus CheckStatus(bool attemptAutoRewrite = true)
        {
            client.configuredTransport = ConfiguredTransport.Unknown;
            try
            {
                string path = GetConfigPath();
                var config = TryLoadConfig(path);

                if (config == null)
                {
                    client.SetStatus(McpStatus.NotConfigured);
                    return client.status;
                }

                var unityMcp = config["mcp"]?[ServerName] as JObject;

                if (unityMcp == null)
                {
                    client.SetStatus(McpStatus.NotConfigured);
                    return client.status;
                }

                if (EntryMatchesCurrentTransport(unityMcp))
                {
                    client.SetStatus(McpStatus.Configured);
                    client.configuredTransport = HttpEndpointUtility.GetCurrentServerTransport();
                }
                else if (attemptAutoRewrite)
                {
                    Configure();
                }
                else
                {
                    client.SetStatus(McpStatus.IncorrectPath);
                }
            }
            catch (Exception ex)
            {
                client.SetStatus(McpStatus.Error, ex.Message);
            }

            return client.status;
        }

        public override void Configure()
        {
            try
            {
                string path = GetConfigPath();
                McpConfigurationHelper.EnsureConfigDirectoryExists(path);

                // Load existing config or start fresh, preserving all other properties and MCP servers
                var config = TryLoadConfig(path) ?? new JObject();

                // Only add $schema if creating a new file
                if (!File.Exists(path))
                {
                    config["$schema"] = SchemaUrl;
                }

                // Preserve existing mcp section and only update our server entry
                if (config["mcp"] != null && !(config["mcp"] is JObject))
                {
                    throw new InvalidOperationException("OpenCode 'mcp' must be an object. The existing configuration was not changed.");
                }
                var mcpSection = config["mcp"] as JObject;
                if (mcpSection == null)
                {
                    mcpSection = new JObject();
                    config["mcp"] = mcpSection;
                }

                mcpSection[ServerName] = BuildServerEntry();

                McpConfigurationHelper.WriteAtomicFile(path, JsonConvert.SerializeObject(config, Formatting.Indented));
                CheckStatus(attemptAutoRewrite: false);
            }
            catch (Exception ex)
            {
                client.SetStatus(McpStatus.Error, ex.Message);
            }
        }

        public override string GetManualSnippet()
        {
            var snippet = new JObject
            {
                ["mcp"] = new JObject { [ServerName] = BuildServerEntry() }
            };
            return JsonConvert.SerializeObject(snippet, Formatting.Indented);
        }

        public override IList<string> GetInstallationSteps() => new List<string>
        {
            "Install OpenCode (https://opencode.ai)",
            "Click Configure to add Unity MCP to ~/.config/opencode/opencode.json",
            "Restart OpenCode",
            "The Unity MCP server should be detected automatically"
        };

        private static JObject BuildServerEntry()
        {
            if (HttpEndpointUtility.GetCurrentServerTransport() == ConfiguredTransport.Stdio)
            {
                var (uvxPath, _, packageName) = AssetPathUtility.GetUvxCommandParts();
                if (string.IsNullOrWhiteSpace(uvxPath))
                {
                    throw new InvalidOperationException("uvx not found. Install uv/uvx or set the override in Advanced Settings.");
                }

                var command = new JArray { uvxPath };
                foreach (string value in AssetPathUtility.GetUvxDevFlagsList())
                {
                    command.Add(value);
                }
                foreach (string value in AssetPathUtility.GetBetaServerFromArgsList())
                {
                    command.Add(value);
                }
                command.Add(packageName);
                command.Add("--transport");
                command.Add("stdio");

                return new JObject
                {
                    ["type"] = LocalType,
                    ["command"] = command,
                    ["enabled"] = true
                };
            }

            return new JObject
            {
                ["type"] = RemoteType,
                ["url"] = HttpEndpointUtility.GetMcpRpcUrl(),
                ["headers"] = JObject.FromObject(HttpEndpointUtility.GetAuthHeaders()),
                ["enabled"] = true
            };
        }

        private bool EntryMatchesCurrentTransport(JObject entry)
        {
            if (entry["enabled"]?.Value<bool>() == false)
            {
                return false;
            }
            string entryType = entry["type"]?.ToString();
            ConfiguredTransport expected = HttpEndpointUtility.GetCurrentServerTransport();

            if (expected == ConfiguredTransport.Stdio)
            {
                if (!string.Equals(entryType, LocalType, StringComparison.OrdinalIgnoreCase)
                    || !(entry["command"] is JArray command) || command.Count == 0)
                {
                    return false;
                }
                string source = McpConfigurationHelper.ExtractUvxUrl(command.ToObject<string[]>());
                return McpConfigurationHelper.PathsEqual(source, GetExpectedPackageSourceForValidation());
            }

            var expectedHeaders = HttpEndpointUtility.GetAuthHeaders();
            return string.Equals(entryType, RemoteType, StringComparison.OrdinalIgnoreCase)
                && UrlsEqual(entry["url"]?.ToString(), HttpEndpointUtility.GetMcpRpcUrl())
                && (HttpEndpointUtility.IsRemoteScope() || expectedHeaders.ContainsKey(Constants.AuthConstants.LocalTokenHeader))
                && ConfigJsonBuilder.TryValidateAuthHeaders(entry["headers"], expectedHeaders, out _);
        }
    }
}
