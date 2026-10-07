using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Constants;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    internal static class ClaudeHttpAuth
    {
        internal const string UnsupportedMessage = "Automatic HTTP authentication requires a verified Claude Code CLI 2.1.193 or newer. "
            + "Update Claude Code (use its executable or npm command shim), or select stdio and configure again.";
        internal static Func<string, bool> SupportsHeadersHelper = DetectHeadersHelper;
        private static readonly object ProbeLock = new();
        private static string checkedPath;
        private static DateTime checkedAt;
        private static bool supported;

        private static bool DetectHeadersHelper(string executable)
        {
            if (string.IsNullOrWhiteSpace(executable) || executable.EndsWith(".ps1", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            lock (ProbeLock)
            {
                if (executable == checkedPath && DateTime.UtcNow - checkedAt < TimeSpan.FromMinutes(1))
                {
                    return supported;
                }
                checkedPath = executable;
                checkedAt = DateTime.UtcNow;
                supported = false;
                string directory = Path.Combine(Path.GetTempPath(), "unity-mcp-claude-capability-" + Guid.NewGuid().ToString("N"));
                try
                {
                    Directory.CreateDirectory(directory);
                    var environment = new Dictionary<string, string>
                    {
                        ["CLAUDE_CONFIG_DIR"] = directory,
                        ["DISABLE_AUTOUPDATER"] = "1",
                        ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
                    };
                    supported = ExecPath.TryRun(executable, "--version", directory, out string output, out _,
                        timeoutMs: 2500, environmentOverrides: environment) && RecognizesVersion(output);
                    return supported;
                }
                catch
                {
                    return false;
                }
                finally
                {
                    try
                    {
                        Directory.Delete(directory, recursive: true);
                    }
                    catch
                    {
                        // Only the isolated version probe can have written here.
                    }
                }
            }
        }

        internal static bool RecognizesVersion(string output)
        {
            var match = Regex.Match(output ?? "", @"\A\s*(\d+\.\d+\.\d+) \(Claude Code\)\s*\z");
            // The official 2.1.193 changelog adds helper refresh/reconnect on tool-call 401/403.
            return match.Success && Version.TryParse(match.Groups[1].Value, out var version)
                && version >= new Version(2, 1, 193);
        }

        internal static bool IsManagedHelper(JObject server)
        {
            if (server?["headersHelper"]?.Type != JTokenType.String)
            {
                return false;
            }
            try
            {
                return (string)server["headersHelper"] == LocalHttpAuth.CommandForEndpoint((string)server["url"]);
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

        internal static void EnsureManagedAuthentication(JObject existing)
        {
            if ((existing?["headersHelper"] != null && !IsManagedHelper(existing)) || existing?["oauth"] != null)
            {
                throw new InvalidOperationException("Claude Code has a custom authentication provider. Update it manually; the existing registration was not changed.");
            }
        }

        internal static JObject BuildLocalEntry(string url, string executable, JObject existing = null)
        {
            if (!SupportsHeadersHelper(executable))
            {
                throw new InvalidOperationException(UnsupportedMessage);
            }
            EnsureManagedAuthentication(existing);
            string helper = LocalHttpAuth.CommandForEndpoint(url);
            if (existing?["headers"] != null && !(existing["headers"] is JObject))
            {
                throw new FormatException("Claude Code headers must be an object.");
            }
            var headers = existing?["headers"]?.DeepClone() as JObject ?? new JObject();
            foreach (var property in headers.Properties().Where(p =>
                string.Equals(p.Name, AuthConstants.LocalTokenHeader, StringComparison.OrdinalIgnoreCase)
                || string.Equals(p.Name, AuthConstants.ApiKeyHeader, StringComparison.OrdinalIgnoreCase)).ToArray())
            {
                property.Remove();
            }
            var entry = new JObject
            {
                ["type"] = "http",
                ["url"] = url,
                ["headersHelper"] = helper
            };
            if (headers.HasValues)
            {
                entry["headers"] = headers;
            }
            return entry;
        }

        internal static string BuildRegistrationArguments(JObject entry)
        {
            // ProcessStartInfo.Arguments uses double-quote/backslash argument escaping, not shell single quotes.
            string json = JsonConvert.SerializeObject(entry, Formatting.None, new JsonSerializerSettings
            {
                StringEscapeHandling = StringEscapeHandling.EscapeNonAscii
            });
            // npm's Windows shim runs through cmd.exe. Keep shell metacharacters inside JSON escapes.
            foreach (char character in "%!^&|<>()")
            {
                json = json.Replace(character.ToString(), "\\u" + ((int)character).ToString("x4"));
            }
            string quoted = Regex.Replace(json, "(\\\\*)\"", "$1$1\\\"");
            return "mcp add-json --scope local UnityMCP \"" + quoted + "\"";
        }

        internal static bool TryValidateLocalEntry(JObject entry, string executable, out string reason)
        {
            reason = null;
            if (entry?["command"] != null || entry?["args"] != null || entry?["oauth"] != null)
            {
                reason = "Claude Code has conflicting transport or authentication settings. Review the entry manually.";
            }
            else if (!IsManagedHelper(entry))
            {
                reason = entry?["headersHelper"] != null
                    ? "Custom Claude Code authentication requires manual validation. The helper was not executed."
                    : "Claude Code uses a static launch token. Click Configure for automatic lookup, or select stdio for an older CLI.";
            }
            else if (entry["headers"] != null && !(entry["headers"] is JObject))
            {
                reason = "Claude Code headers must be an object.";
            }
            else if ((entry["headers"] as JObject)?.Properties().Any(p =>
                string.Equals(p.Name, AuthConstants.LocalTokenHeader, StringComparison.OrdinalIgnoreCase)
                || string.Equals(p.Name, AuthConstants.ApiKeyHeader, StringComparison.OrdinalIgnoreCase)) == true)
            {
                reason = "Remove the stale static authentication header by configuring Claude Code again.";
            }
            else if (!SupportsHeadersHelper(executable))
            {
                reason = UnsupportedMessage;
            }
            return reason == null;
        }
    }
}
