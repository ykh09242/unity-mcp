using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Services;
using MCPForUnity.External.Tommy;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    /// <summary>
    /// Codex CLI specific configuration helpers. Handles TOML snippet
    /// generation and lightweight parsing so Codex can join the auto-setup
    /// flow alongside JSON-based clients.
    /// </summary>
    public static class CodexConfigHelper
    {
        private static void AddUvxModeFlags(TomlArray args)
        {
            if (args == null) return;
            foreach (var flag in AssetPathUtility.GetUvxDevFlagsList())
                args.Add(new TomlString { Value = flag });
        }

        public static string BuildCodexServerBlock(string uvPath)
        {
            var table = new TomlTable();
            var mcpServers = new TomlTable();
            var unityMCP = new TomlTable();

            // Check transport preference
            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;

            if (useHttpTransport)
            {
                // HTTP mode: Use url field
                string httpUrl = HttpEndpointUtility.GetMcpRpcUrl();
                unityMCP["url"] = new TomlString { Value = httpUrl };
                AddHttpAuthHeaders(unityMCP);
            }
            else
            {
                // Stdio mode: Use command and args
                var (uvxPath, _, packageName) = AssetPathUtility.GetUvxCommandParts();

                unityMCP["command"] = uvxPath;

                var args = new TomlArray();
                AddUvxModeFlags(args);
                // Use centralized helper for beta server / prerelease args
                foreach (var arg in AssetPathUtility.GetBetaServerFromArgsList())
                {
                    args.Add(new TomlString { Value = arg });
                }
                args.Add(new TomlString { Value = packageName });
                args.Add(new TomlString { Value = "--transport" });
                args.Add(new TomlString { Value = "stdio" });

                unityMCP["args"] = args;

                // Add Windows-specific environment configuration for stdio mode
                var platformService = MCPServiceLocator.Platform;
                if (platformService.IsWindows())
                {
                    var envTable = new TomlTable { IsInline = true };
                    envTable["SystemRoot"] = new TomlString { Value = platformService.GetSystemRoot() };
                    unityMCP["env"] = envTable;
                }

                // Allow extra time for uvx to download packages on first run
                unityMCP["startup_timeout_sec"] = new TomlInteger { Value = 60 };
            }

            mcpServers["unityMCP"] = unityMCP;
            table["mcp_servers"] = mcpServers;

            using var writer = new StringWriter();
            table.WriteTo(writer);
            return writer.ToString();
        }

        public static string UpsertCodexServerBlock(string existingToml, string uvPath)
        {
            var root = TryParseToml(existingToml);
            if (root == null && !string.IsNullOrWhiteSpace(existingToml))
                throw new FormatException("Existing Codex configuration is invalid TOML.");
            root ??= new TomlTable();

            var mcpServers = GetOrCreateTable(root, "mcp_servers", "mcp_servers");
            var unityMcp = GetOrCreateTable(mcpServers, "unityMCP", "mcp_servers.unityMCP");
            // Refuse malformed nested settings rather than erase user configuration.
            ValidateOptionalTable(unityMcp, "env", "mcp_servers.unityMCP.env");
            ValidateOptionalTable(unityMcp, "http_headers", "mcp_servers.unityMCP.http_headers");

            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;

            // Codex rejects fields belonging to the other transport. Refuse custom
            // settings rather than discard them or serialize an unusable configuration.
            var incompatibleKeys = useHttpTransport
                ? new[] { "env_vars", "cwd", "bearer_token" }
                : new[] { "bearer_token_env_var", "bearer_token", "http_headers_helper",
                    "env_http_headers", "oauth", "oauth_resource", "auth" };
            bool managedHelper = IsManagedHttpHelper(unityMcp);
            foreach (var key in incompatibleKeys)
            {
                if (key == "http_headers_helper" && managedHelper) continue;
                if (unityMcp.TryGetNode(key, out _))
                    throw IncompatibleTransportSetting(key, useHttpTransport);
            }
            if (useHttpTransport && unityMcp.TryGetNode("env", out var envNode))
            {
                if (((TomlTable)envNode).Keys.Any(key => key != "SystemRoot"))
                    throw IncompatibleTransportSetting("env", true);
                unityMcp.Delete("env");
            }
            if (!useHttpTransport && unityMcp.TryGetNode("http_headers", out var existingHeaders)
                && ((TomlTable)existingHeaders).Keys.Any(key => !IsManagedAuthHeader(key)))
                throw IncompatibleTransportSetting("http_headers", false);

            if (useHttpTransport)
            {
                ValidateOptionalTable(unityMcp, "env_http_headers", "mcp_servers.unityMCP.env_http_headers");
                if ((unityMcp.TryGetNode("http_headers_helper", out _) && !managedHelper)
                    || (TryGetTable(unityMcp, "env_http_headers", out var envHeaders)
                        && envHeaders.Keys.Any(IsManagedAuthHeader)))
                    throw new FormatException("Custom Codex HTTP authentication is preserved. "
                        + "Update it manually before using automatic configuration.");
            }

            var generated = CreateUnityMcpTable(uvPath);
            if (managedHelper) unityMcp.Delete("http_headers_helper");
            unityMcp.Delete(useHttpTransport ? "command" : "url");
            if (useHttpTransport) unityMcp.Delete("args");

            // Header names are case-insensitive; remove only credentials generated by us.
            if (unityMcp.TryGetNode("http_headers", out var headersNode))
            {
                var headers = (TomlTable)headersNode;
                foreach (var key in headers.Keys.Where(IsManagedAuthHeader).ToArray())
                    headers.Delete(key);
            }
            foreach (var key in generated.Keys)
            {
                if (key == "env" || key == "http_headers")
                {
                    var destination = GetOrCreateTable(unityMcp, key, "mcp_servers.unityMCP." + key);
                    foreach (var entry in ((TomlTable)generated[key]).RawTable)
                        destination[entry.Key] = entry.Value;
                }
                else if (key != "startup_timeout_sec" || !unityMcp.TryGetNode(key, out _))
                    unityMcp[key] = generated[key];
            }
            if (unityMcp.TryGetNode("http_headers", out headersNode)
                && !((TomlTable)headersNode).Keys.Any())
                unityMcp.Delete("http_headers");

            // Serialize back to TOML
            using var writer = new StringWriter();
            root.WriteTo(writer);
            return writer.ToString();
        }

        public static bool TryParseCodexServer(string toml, out string command, out string[] args)
        {
            return TryParseCodexServer(toml, out command, out args, out _);
        }

        public static bool TryParseCodexServer(string toml, out string command, out string[] args, out string url)
        {
            command = null;
            args = null;
            url = null;

            var root = TryParseToml(toml);
            if (root == null) return false;

            if (!TryGetTable(root, "mcp_servers", out var servers)
                && !TryGetTable(root, "mcpServers", out servers))
            {
                return false;
            }

            if (!TryGetTable(servers, "unityMCP", out var unity))
            {
                return false;
            }

            // Check for HTTP mode (url field)
            url = GetTomlString(unity, "url");
            if (!string.IsNullOrEmpty(url))
            {
                // HTTP mode detected - return true with url
                return true;
            }

            // Check for stdio mode (command + args)
            command = GetTomlString(unity, "command");
            args = GetTomlStringArray(unity, "args");

            return !string.IsNullOrEmpty(command) && args != null;
        }

        /// <summary>
        /// Safely parses TOML string, returning null on failure
        /// </summary>
        private static TomlTable TryParseToml(string toml)
        {
            if (string.IsNullOrWhiteSpace(toml)) return null;

            try
            {
                using var reader = new StringReader(toml);
                return TOML.Parse(reader);
            }
            catch (TomlParseException)
            {
                return null;
            }
            catch (TomlSyntaxException)
            {
                return null;
            }
            catch (FormatException)
            {
                return null;
            }
        }

        /// <summary>
        /// Creates a TomlTable for the unityMCP server configuration
        /// </summary>
        /// <param name="uvPath">Path to uv executable (used as fallback if uvx is not available)</param>
        private static TomlTable CreateUnityMcpTable(string uvPath)
        {
            var unityMCP = new TomlTable();

            // Check transport preference
            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;

            if (useHttpTransport)
            {
                // HTTP mode: Use url field
                string httpUrl = HttpEndpointUtility.GetMcpRpcUrl();
                unityMCP["url"] = new TomlString { Value = httpUrl };
                AddHttpAuthHeaders(unityMCP);
            }
            else
            {
                // Stdio mode: Use command and args
                var (uvxPath, _, packageName) = AssetPathUtility.GetUvxCommandParts();

                unityMCP["command"] = new TomlString { Value = uvxPath };

                var argsArray = new TomlArray();
                AddUvxModeFlags(argsArray);
                // Use centralized helper for beta server / prerelease args
                foreach (var arg in AssetPathUtility.GetBetaServerFromArgsList())
                {
                    argsArray.Add(new TomlString { Value = arg });
                }
                argsArray.Add(new TomlString { Value = packageName });
                argsArray.Add(new TomlString { Value = "--transport" });
                argsArray.Add(new TomlString { Value = "stdio" });
                unityMCP["args"] = argsArray;

                // Add Windows-specific environment configuration for stdio mode
                var platformService = MCPServiceLocator.Platform;
                if (platformService.IsWindows())
                {
                    var envTable = new TomlTable { IsInline = true };
                    envTable["SystemRoot"] = new TomlString { Value = platformService.GetSystemRoot() };
                    unityMCP["env"] = envTable;
                }

                // Allow extra time for uvx to download packages on first run
                unityMCP["startup_timeout_sec"] = new TomlInteger { Value = 60 };
            }

            return unityMCP;
        }

        private static void AddHttpAuthHeaders(TomlTable server)
        {
            if (!HttpEndpointUtility.IsRemoteScope())
            {
                if (!CodexHttpAuth.SupportsHeadersHelper())
                {
                    throw new InvalidOperationException(CodexHttpAuth.UnsupportedMessage);
                }
                server["http_headers_helper"] = new TomlString
                {
                    Value = LocalHttpAuth.CommandForEndpoint(GetTomlString(server, "url"))
                };
                return;
            }
            var headers = HttpEndpointUtility.GetAuthHeaders();
            if (headers.Count == 0)
            {
                return;
            }
            var table = new TomlTable { IsInline = true };
            foreach (var header in headers)
            {
                table[header.Key] = new TomlString { Value = header.Value };
            }
            server["http_headers"] = table;
        }

        internal static bool TryValidateHttpServer(string toml, IDictionary<string, string> expectedHeaders, out string reason)
        {
            reason = "Invalid Codex HTTP configuration. Click Configure to update.";
            var root = TryParseToml(toml);
            if (root == null || !root.TryGetNode("mcp_servers", out var serversNode)
                || serversNode is not TomlTable servers || !servers.TryGetNode("unityMCP", out var serverNode)
                || serverNode is not TomlTable server)
            {
                return false;
            }

            if (server.TryGetNode("enabled", out var enabled) && (enabled is not TomlBoolean flag || !flag.Value))
            {
                reason = "Codex unityMCP is disabled or has an invalid enabled setting. Enable it in the config file.";
                return false;
            }
            foreach (var key in new[] { "command", "args", "env", "env_vars", "cwd", "bearer_token" })
            {
                if (server.TryGetNode(key, out _))
                {
                    return false;
                }
            }

            bool hasHelper = server.TryGetNode("http_headers_helper", out _);
            bool managedHelper = IsManagedHttpHelper(server);
            if ((hasHelper && !managedHelper)
                || (TryGetTable(server, "env_http_headers", out var envHeaders)
                    && envHeaders.Keys.Any(IsManagedAuthHeader)))
            {
                reason = "Custom Codex HTTP authentication requires manual validation in Codex; it has not been changed.";
                return false;
            }
            if (server.TryGetNode("env_http_headers", out var envNode) && envNode is not TomlTable)
            {
                return false;
            }

            TomlTable headers = null;
            if (server.TryGetNode("http_headers", out var headersNode))
            {
                if (headersNode is not TomlTable table)
                {
                    return false;
                }
                headers = table;
            }
            if (managedHelper)
            {
                if (headers != null && headers.Keys.Any(IsManagedAuthHeader))
                {
                    return false;
                }
                reason = CodexHttpAuth.SupportsHeadersHelper() ? null : CodexHttpAuth.UnsupportedMessage;
                return reason == null;
            }
            if (expectedHeaders == null)
            {
                expectedHeaders = HttpEndpointUtility.GetAuthHeaders();
                if (!HttpEndpointUtility.IsRemoteScope())
                {
                    reason = "Codex uses a static local token. Click Configure for automatic token lookup, or select stdio for an older Codex.";
                    return false;
                }
            }
            reason = "Codex HTTP authentication is missing or outdated. Click Configure, then restart Codex.";
            foreach (var expected in expectedHeaders)
            {
                var keys = headers?.Keys.Where(key => string.Equals(key, expected.Key, StringComparison.OrdinalIgnoreCase)).ToArray();
                if (keys == null || keys.Length != 1 || headers[keys[0]] is not TomlString value
                    || !string.Equals(value.Value, expected.Value, StringComparison.Ordinal))
                {
                    return false;
                }
            }
            if (headers != null && headers.Keys.Where(IsManagedAuthHeader).Any(key =>
                !expectedHeaders.Keys.Any(expected => string.Equals(key, expected, StringComparison.OrdinalIgnoreCase))))
            {
                return false;
            }

            reason = null;
            return true;
        }

        private static bool IsManagedHttpHelper(TomlTable server)
        {
            string helper = GetTomlString(server, "http_headers_helper");
            if (string.IsNullOrEmpty(helper))
            {
                return false;
            }
            try
            {
                return helper == LocalHttpAuth.CommandForEndpoint(GetTomlString(server, "url"));
            }
            catch (ArgumentException)
            {
                return false;
            }
            catch (InvalidOperationException)
            {
                return false;
            }
        }

        private static void ValidateOptionalTable(TomlTable parent, string key, string path)
        {
            if (parent.TryGetNode(key, out var node) && node is not TomlTable)
                throw new FormatException($"Existing Codex configuration '{path}' must be a TOML table.");
        }

        private static bool IsManagedAuthHeader(string key)
        {
            return string.Equals(key, AuthConstants.ApiKeyHeader, StringComparison.OrdinalIgnoreCase)
                || string.Equals(key, AuthConstants.LocalTokenHeader, StringComparison.OrdinalIgnoreCase);
        }

        private static FormatException IncompatibleTransportSetting(string key, bool useHttpTransport)
        {
            string transport = useHttpTransport ? "HTTP" : "stdio";
            return new FormatException($"Existing Codex setting 'mcp_servers.unityMCP.{key}' is incompatible with {transport}. "
                + "Remove or migrate this setting manually before changing transport.");
        }

        private static TomlTable GetOrCreateTable(TomlTable parent, string key, string path)
        {
            ValidateOptionalTable(parent, key, path);
            if (parent.TryGetNode(key, out var node)) return (TomlTable)node;
            var table = new TomlTable();
            parent[key] = table;
            return table;
        }

        private static bool TryGetTable(TomlTable parent, string key, out TomlTable table)
        {
            table = null;
            if (parent == null) return false;

            if (parent.TryGetNode(key, out var node))
            {
                if (node is TomlTable tbl)
                {
                    table = tbl;
                    return true;
                }

                if (node is TomlArray array)
                {
                    var firstTable = array.Children.OfType<TomlTable>().FirstOrDefault();
                    if (firstTable != null)
                    {
                        table = firstTable;
                        return true;
                    }
                }
            }

            return false;
        }

        private static string GetTomlString(TomlTable table, string key)
        {
            if (table != null && table.TryGetNode(key, out var node))
            {
                if (node is TomlString str) return str.Value;
                if (node.HasValue) return node.ToString();
            }
            return null;
        }

        private static string[] GetTomlStringArray(TomlTable table, string key)
        {
            if (table == null) return null;
            if (!table.TryGetNode(key, out var node)) return null;

            if (node is TomlArray array)
            {
                List<string> values = new List<string>();
                foreach (TomlNode element in array.Children)
                {
                    if (element is TomlString str)
                    {
                        values.Add(str.Value);
                    }
                    else if (element.HasValue)
                    {
                        values.Add(element.ToString());
                    }
                }

                return values.Count > 0 ? values.ToArray() : Array.Empty<string>();
            }

            if (node is TomlString single)
            {
                return new[] { single.Value };
            }

            return null;
        }
    }
}
