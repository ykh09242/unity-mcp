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
            if (args == null)
                return;
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
                : new[] { "bearer_token_env_var", "bearer_token", "http_headers_helper", "env_http_headers", "oauth", "oauth_resource", "auth" };
            bool managedHelper = IsManagedHttpHelper(unityMcp);
            foreach (var key in incompatibleKeys)
            {
                if (key == "http_headers_helper" && managedHelper)
                    continue;
                if (unityMcp.TryGetNode(key, out _))
                    throw IncompatibleTransportSetting(key, useHttpTransport);
            }
            if (useHttpTransport && unityMcp.TryGetNode("env", out var envNode))
            {
                if (((TomlTable)envNode).Keys.Any(key => key != "SystemRoot"))
                    throw IncompatibleTransportSetting("env", true);
                unityMcp.Delete("env");
            }
            if (
                !useHttpTransport
                && unityMcp.TryGetNode("http_headers", out var existingHeaders)
                && ((TomlTable)existingHeaders).Keys.Any(key => !IsManagedAuthHeader(key))
            )
                throw IncompatibleTransportSetting("http_headers", false);

            if (useHttpTransport)
            {
                ValidateOptionalTable(unityMcp, "env_http_headers", "mcp_servers.unityMCP.env_http_headers");
                if (
                    (unityMcp.TryGetNode("http_headers_helper", out _) && !managedHelper)
                    || (TryGetTable(unityMcp, "env_http_headers", out var envHeaders) && envHeaders.Keys.Any(IsManagedAuthHeader))
                )
                    throw new FormatException("Custom Codex HTTP authentication is preserved. " + "Update it manually before using automatic configuration.");
            }

            var generated = CreateUnityMcpTable(uvPath);
            if (managedHelper)
                unityMcp.Delete("http_headers_helper");
            unityMcp.Delete(useHttpTransport ? "command" : "url");
            if (useHttpTransport)
                unityMcp.Delete("args");

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
            if (unityMcp.TryGetNode("http_headers", out headersNode) && !((TomlTable)headersNode).Keys.Any())
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

        internal static string RemoveCodexServerBlock(string document)
        {
            if (string.IsNullOrWhiteSpace(document))
                return document;
            var expected = TryParseToml(document) ?? throw new FormatException("Existing Codex configuration is invalid TOML; it was not changed.");
            bool found = false;
            foreach (string container in new[] { "mcp_servers", "mcpServers" })
            {
                if (!expected.TryGetNode(container, out var node))
                    continue;
                if (node is not TomlTable servers)
                    throw new FormatException($"Codex {container} must be a table; configuration was not changed.");
                if (!servers.TryGetNode("unityMCP", out var owned))
                    continue;
                if (owned is not TomlTable)
                    throw new FormatException($"Codex {container}.unityMCP must be a table; configuration was not changed.");
                // Explicit unregister owns this exact reserved entry, including its customized
                // fields. Custom server names and shared token files are never owned by it.
                servers.Delete("unityMCP");
                if (!servers.Keys.Any())
                    expected.Delete(container);
                found = true;
            }
            if (!found)
                return document;

            var removals = new List<(int start, int length)>();
            var context = new List<string>();
            foreach (var range in TomlStatements(document))
            {
                string statement = document.Substring(range.start, range.length);
                string trimmed = statement.TrimStart();
                if (trimmed.Length == 0 || trimmed.StartsWith("#", StringComparison.Ordinal))
                    continue;
                List<string> path;
                if (trimmed[0] == '[')
                {
                    path = TomlKeyPath(statement, true);
                    context = path;
                }
                else
                {
                    int separator = KeySeparator(statement);
                    path = new List<string>(context);
                    path.AddRange(TomlKeyPath(statement.Substring(0, separator) + " = 0", false));
                }
                if (path.Count >= 2 && (path[0] == "mcp_servers" || path[0] == "mcpServers") && path[1] == "unityMCP")
                    removals.Add(range);
            }
            string updated = document;
            foreach (var range in removals.OrderByDescending(range => range.start))
                updated = updated.Remove(range.start, range.length);
            var actual = TryParseToml(updated) ?? (string.IsNullOrWhiteSpace(updated) ? new TomlTable() : null);
            if (actual != null)
                foreach (string container in new[] { "mcp_servers", "mcpServers" })
                    if (actual.TryGetNode(container, out var node) && node is TomlTable empty && !empty.Keys.Any())
                        actual.Delete(container);
            if (actual == null || !SameToml(expected, actual))
                throw new FormatException(
                    "Codex unityMCP uses a TOML layout that cannot be removed without changing neighboring settings. "
                        + "Remove only that entry manually or expand it into [mcp_servers.unityMCP]; configuration was not changed."
                );
            return updated;
        }

        private static bool SameToml(TomlNode left, TomlNode right)
        {
            if (left is TomlTable table && right is TomlTable other)
            {
                var keys = table.Keys.ToArray();
                var otherKeys = other.Keys.ToArray();
                return keys.OrderBy(key => key).SequenceEqual(otherKeys.OrderBy(key => key)) && keys.All(key => SameToml(table[key], other[key]));
            }
            if (left is TomlArray array && right is TomlArray otherArray)
                return array.ChildrenCount == otherArray.ChildrenCount
                    && Enumerable.Range(0, array.ChildrenCount).All(index => SameToml(array[index], otherArray[index]));
            return left.GetType() == right.GetType() && left.ToInlineToml() == right.ToInlineToml();
        }

        private static List<string> TomlKeyPath(string statement, bool header)
        {
            var parsed = TryParseToml(statement) ?? throw new FormatException("Codex TOML entry could not be located safely; configuration was not changed.");
            var path = new List<string>();
            TomlNode node = parsed;
            while (node is TomlTable table && table.Keys.Count() == 1)
            {
                string key = table.Keys.Single();
                path.Add(key);
                node = table[key];
                if (header && node is TomlArray array && array.IsTableArray && array.ChildrenCount == 1)
                    node = array[0];
            }
            return path;
        }

        private static int KeySeparator(string statement)
        {
            char quote = '\0';
            for (int i = 0; i < statement.Length; i++)
            {
                char c = statement[i];
                if (quote == '"' && c == '\\')
                    i++;
                else if (quote != '\0' && c == quote)
                    quote = '\0';
                else if (quote == '\0' && (c == '"' || c == '\''))
                    quote = c;
                else if (quote == '\0' && c == '=')
                    return i;
            }
            throw new FormatException("Codex TOML key could not be located safely; configuration was not changed.");
        }

        private static IEnumerable<(int start, int length)> TomlStatements(string document)
        {
            int start = 0,
                depth = 0;
            char quote = '\0';
            bool multiline = false,
                comment = false;
            for (int i = 0; i < document.Length; i++)
            {
                char c = document[i];
                if (comment)
                {
                    if (c != '\n')
                        continue;
                    comment = false;
                }
                else if (quote != '\0')
                {
                    if (quote == '"' && c == '\\')
                    {
                        i++;
                        continue;
                    }
                    if (c != quote)
                        continue;
                    if (!multiline)
                        quote = '\0';
                    else if (i + 2 < document.Length && document[i + 1] == quote && document[i + 2] == quote)
                    {
                        i += 2;
                        // Tommy accepts up to two content quotes next to a closing triple.
                        // Consume the full run so a fourth quote cannot open another string
                        // and absorb a following standalone comment into this statement.
                        for (int extra = 0; extra < 2 && i + 1 < document.Length && document[i + 1] == quote; extra++)
                            i++;
                        quote = '\0';
                        multiline = false;
                    }
                    continue;
                }
                else if (c == '#')
                    comment = true;
                else if (c == '"' || c == '\'')
                {
                    quote = c;
                    multiline = i + 2 < document.Length && document[i + 1] == c && document[i + 2] == c;
                    if (multiline)
                        i += 2;
                }
                else if (c == '[' || c == '{')
                    depth++;
                else if (c == ']' || c == '}')
                    depth--;
                if (c == '\n' && depth == 0 && quote == '\0')
                {
                    yield return (start, i + 1 - start);
                    start = i + 1;
                }
            }
            if (start < document.Length)
                yield return (start, document.Length - start);
        }

        public static bool TryParseCodexServer(string toml, out string command, out string[] args, out string url)
        {
            command = null;
            args = null;
            url = null;

            var root = TryParseToml(toml);
            if (root == null)
                return false;

            if (!TryGetTable(root, "mcp_servers", out var servers) && !TryGetTable(root, "mcpServers", out servers))
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
            if (string.IsNullOrWhiteSpace(toml))
                return null;

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
                server["http_headers_helper"] = new TomlString { Value = LocalHttpAuth.CommandForEndpoint(GetTomlString(server, "url")) };
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
            if (
                root == null
                || !root.TryGetNode("mcp_servers", out var serversNode)
                || serversNode is not TomlTable servers
                || !servers.TryGetNode("unityMCP", out var serverNode)
                || serverNode is not TomlTable server
            )
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
            if ((hasHelper && !managedHelper) || (TryGetTable(server, "env_http_headers", out var envHeaders) && envHeaders.Keys.Any(IsManagedAuthHeader)))
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
                if (
                    keys == null
                    || keys.Length != 1
                    || headers[keys[0]] is not TomlString value
                    || !string.Equals(value.Value, expected.Value, StringComparison.Ordinal)
                )
                {
                    return false;
                }
            }
            if (
                headers != null
                && headers
                    .Keys.Where(IsManagedAuthHeader)
                    .Any(key => !expectedHeaders.Keys.Any(expected => string.Equals(key, expected, StringComparison.OrdinalIgnoreCase)))
            )
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
            return new FormatException(
                $"Existing Codex setting 'mcp_servers.unityMCP.{key}' is incompatible with {transport}. "
                    + "Remove or migrate this setting manually before changing transport."
            );
        }

        private static TomlTable GetOrCreateTable(TomlTable parent, string key, string path)
        {
            ValidateOptionalTable(parent, key, path);
            if (parent.TryGetNode(key, out var node))
                return (TomlTable)node;
            var table = new TomlTable();
            parent[key] = table;
            return table;
        }

        private static bool TryGetTable(TomlTable parent, string key, out TomlTable table)
        {
            table = null;
            if (parent == null)
                return false;

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
                if (node is TomlString str)
                    return str.Value;
                if (node.HasValue)
                    return node.ToString();
            }
            return null;
        }

        private static string[] GetTomlStringArray(TomlTable table, string key)
        {
            if (table == null)
                return null;
            if (!table.TryGetNode(key, out var node))
                return null;

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
