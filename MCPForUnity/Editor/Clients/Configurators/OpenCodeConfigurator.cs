using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Models;
using MCPForUnity.Editor.Services;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;

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

        public OpenCodeConfigurator()
            : base(
                new McpClient
                {
                    name = "OpenCode",
                    windowsConfigPath = BuildConfigPath(),
                    macConfigPath = BuildConfigPath(),
                    linuxConfigPath = BuildConfigPath(),
                    ServerContainerKey = "mcp",
                }
            ) { }

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
                return JsonConvert.DeserializeObject<JObject>(content) ?? throw new JsonException("The configuration must be an object.");
            }
            catch (JsonException ex)
            {
                throw new InvalidOperationException("The existing OpenCode configuration is not a JSON object. Fix it manually; it was not changed.", ex);
            }
        }

        public override McpStatus CheckStatus(bool attemptAutoRewrite = true) => CheckStatus(attemptAutoRewrite, false).Value;

        internal override McpStatus? RefreshForStdioMigration() => CheckStatus(true, true);

        private McpStatus? CheckStatus(bool attemptAutoRewrite, bool migrationOnly)
        {
            client.configuredTransport = ConfiguredTransport.Unknown;
            try
            {
                string path = GetConfigPath();
                var config = TryLoadConfig(path);

                if (config == null)
                {
                    if (migrationOnly)
                        return null;
                    client.SetStatus(McpStatus.NotConfigured);
                    return client.status;
                }

                var unityMcp = config["mcp"]?[ServerName] as JObject;

                if (unityMcp == null)
                {
                    if (migrationOnly)
                        return null;
                    client.SetStatus(McpStatus.NotConfigured);
                    return client.status;
                }

                if (
                    HttpEndpointUtility.GetCurrentServerTransport() == ConfiguredTransport.Stdio
                    && GeneratedStdioEntry(unityMcp) != null
                    && JsonFileMcpConfigurator.GeneratedExecutableIssue((string)unityMcp["command"][0]) is string executableIssue
                )
                {
                    client.SetStatus(McpStatus.IncorrectPath, executableIssue);
                    return client.status;
                }

                var automatic = attemptAutoRewrite
                    ? GeneratedClientConfigOwnership.AutomaticRefresh.Capture("OpenCode", path, GeneratedStdioEntry(unityMcp), migrationOnly)
                    : null;
                if (migrationOnly && automatic == null)
                    return null;
                if (EntryMatchesCurrentTransport(unityMcp))
                {
                    client.SetStatus(McpStatus.Configured);
                    client.configuredTransport = HttpEndpointUtility.GetCurrentServerTransport();
                }
                else if (automatic != null)
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
            }

            return client.status;
        }

        public override void Configure() => WriteGeneratedConfiguration(GetConfigPath(), null);

        private void WriteGeneratedConfiguration(string path, GeneratedClientConfigOwnership.AutomaticRefresh automatic)
        {
            try
            {
                JObject writtenEntry = null;
                ClientConfigurationChangeService.Shared.MutateFile(
                    path,
                    document =>
                    {
                        JObject config;
                        try
                        {
                            config =
                                document == null
                                    ? new JObject()
                                    : JObject.Parse(document, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                        }
                        catch (JsonException ex)
                        {
                            throw new InvalidOperationException(
                                "The existing OpenCode configuration is not a JSON object. Fix it manually; it was not changed.",
                                ex
                            );
                        }
                        automatic?.ValidateEntry(path, GeneratedStdioEntry(config["mcp"]?[ServerName] as JObject));
                        ApplyConfiguration(config, null, document == null);
                        writtenEntry = (JObject)config["mcp"][ServerName];
                        return config.ToString(Formatting.Indented);
                    },
                    createIfMissing: true,
                    beforeCommit: automatic == null ? null : () => automatic.ValidateProposal((string)writtenEntry["url"])
                );
                GeneratedClientConfigOwnership.Record("OpenCode", path, GeneratedStdioEntry(writtenEntry));
                CheckStatus(attemptAutoRewrite: false);
            }
            catch (Exception ex)
            {
                client.SetStatus(McpStatus.Error, ex.Message);
                throw;
            }
        }

        internal static void ApplyConfiguration(JObject config, ClientConfigurationInputs inputs, bool addSchema)
        {
            if (config["mcp"] != null && config["mcp"] is not JObject)
                throw new InvalidOperationException("OpenCode 'mcp' must be an object. The existing configuration was not changed.");
            var servers = config["mcp"] as JObject;
            if (servers?[ServerName] != null && servers[ServerName] is not JObject)
                throw new InvalidOperationException("OpenCode 'mcp.unityMCP' must be an object. The existing configuration was not changed.");
            var existing = servers?[ServerName] as JObject;
            if (existing?["headers"] != null && existing["headers"] is not JObject)
                throw new InvalidOperationException("OpenCode 'headers' must be an object. The existing configuration was not changed.");
            if (existing?["headersHelper"] != null || existing?["http_headers_helper"] != null)
                throw new InvalidOperationException("Custom OpenCode authentication must be migrated manually. The existing configuration was not changed.");

            var generated = BuildServerEntry(inputs);
            var entry = existing?.DeepClone() as JObject ?? new JObject();
            bool remote = (string)generated["type"] == RemoteType;
            bool sameEndpoint =
                remote
                && string.Equals((string)existing?["type"], RemoteType, StringComparison.OrdinalIgnoreCase)
                && string.Equals((string)existing?["url"], (string)generated["url"], StringComparison.Ordinal);
            if (remote)
            {
                entry.Remove("command");
                entry.Remove("environment");
                // Unknown headers may contain credentials. Only retain them for the exact same recipient.
                var headers = sameEndpoint ? entry["headers"] as JObject ?? new JObject() : new JObject();
                foreach (
                    var property in headers
                        .Properties()
                        .Where(property =>
                            string.Equals(property.Name, AuthConstants.ApiKeyHeader, StringComparison.OrdinalIgnoreCase)
                            || string.Equals(property.Name, AuthConstants.LocalTokenHeader, StringComparison.OrdinalIgnoreCase)
                        )
                        .ToArray()
                )
                    property.Remove();
                foreach (var header in ((JObject)generated["headers"]).Properties())
                    headers[header.Name] = header.Value.DeepClone();
                generated["headers"] = headers;
                if (!sameEndpoint)
                    entry.Remove("oauth");
            }
            else
            {
                entry.Remove("url");
                entry.Remove("headers");
                entry.Remove("oauth");
            }
            foreach (var property in generated.Properties())
                entry[property.Name] = property.Value.DeepClone();
            if (servers == null)
                config["mcp"] = servers = new JObject();
            servers[ServerName] = entry;
            if (addSchema)
                config["$schema"] = SchemaUrl;
        }

        internal static string GeneratedStdioEntry(JObject entry)
        {
            if (
                entry?["type"]?.Value<string>() != LocalType
                || entry["enabled"]?.Type != JTokenType.Boolean
                || !(bool)entry["enabled"]
                || entry.Properties().Any(property => property.Name != "type" && property.Name != "command" && property.Name != "enabled")
                || entry["command"] is not JArray command
                || command.Count == 0
                || command.Any(token => token.Type != JTokenType.String)
            )
                return null;
            var launch = new JObject { ["command"] = command[0].DeepClone(), ["args"] = new JArray(command.Skip(1)) };
            return JsonFileMcpConfigurator.IsGeneratedStdioEntry(launch) ? entry.ToString(Formatting.None) : null;
        }

        public override string GetManualSnippet()
        {
            var snippet = new JObject { ["mcp"] = new JObject { [ServerName] = BuildServerEntry() } };
            return JsonConvert.SerializeObject(snippet, Formatting.Indented);
        }

        public override IList<string> GetInstallationSteps() =>
            new List<string>
            {
                "Install OpenCode (https://opencode.ai)",
                "Click Configure to add Unity MCP to ~/.config/opencode/opencode.json",
                "Restart OpenCode",
                "The Unity MCP server should be detected automatically",
            };

        private static JObject BuildServerEntry(ClientConfigurationInputs inputs = null)
        {
            if (inputs != null ? !inputs.UseHttp : HttpEndpointUtility.GetCurrentServerTransport() == ConfiguredTransport.Stdio)
            {
                var parts = inputs == null ? AssetPathUtility.GetUvxCommandParts() : (inputs.Command, (string)null, (string)null);
                string uvxPath = parts.Item1;
                if (string.IsNullOrWhiteSpace(uvxPath))
                {
                    throw new InvalidOperationException("uvx not found. Install uv/uvx or set the override in Advanced Settings.");
                }

                var command = new JArray { uvxPath };
                foreach (string value in inputs?.Args ?? ConfigJsonBuilder.BuildUvxArgs(null, parts.Item3).ToArray())
                {
                    command.Add(value);
                }

                return new JObject
                {
                    ["type"] = LocalType,
                    ["command"] = command,
                    ["enabled"] = true,
                };
            }

            return new JObject
            {
                ["type"] = RemoteType,
                ["url"] = inputs?.Endpoint ?? HttpEndpointUtility.GetMcpRpcUrl(),
                ["headers"] = JObject.FromObject(inputs?.Headers ?? HttpEndpointUtility.GetAuthHeaders()),
                ["enabled"] = true,
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
                if (!string.Equals(entryType, LocalType, StringComparison.OrdinalIgnoreCase) || !(entry["command"] is JArray command) || command.Count == 0)
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
