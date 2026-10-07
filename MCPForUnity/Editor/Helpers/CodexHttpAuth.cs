using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using Newtonsoft.Json.Linq;

namespace MCPForUnity.Editor.Helpers
{
    internal static class CodexHttpAuth
    {
        internal const string UnsupportedMessage =
            "Automatic HTTP authentication could not be confirmed for the installed Codex. "
            + "Update Codex and make its CLI available on PATH, or select stdio and configure again.";

        internal static Func<bool> SupportsHeadersHelper = DetectHeadersHelper;
        private static DateTime checkedAt;
        private static bool supported;

        private static bool DetectHeadersHelper()
        {
            if (DateTime.UtcNow - checkedAt < TimeSpan.FromMinutes(1))
            {
                return supported;
            }
            checkedAt = DateTime.UtcNow;
            supported = false;
            string directory = null;
            try
            {
                bool windows = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);
                string executable = windows
                    ? ExecPath.FindInPath("codex.exe") ?? ExecPath.FindInPath("codex.cmd")
                    : ExecPath.FindInPath("codex", "/opt/homebrew/bin:/usr/local/bin");
                if (string.IsNullOrEmpty(executable) && windows)
                {
                    string bundled = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        "Programs",
                        "OpenAI",
                        "Codex",
                        "bin",
                        "codex.exe"
                    );
                    if (File.Exists(bundled))
                    {
                        executable = bundled;
                    }
                }
                if (string.IsNullOrEmpty(executable))
                {
                    return false;
                }

                // mcp get parses but never executes this helper or opens an MCP connection.
                directory = Path.Combine(Path.GetTempPath(), "unity-mcp-codex-capability-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(directory);
                File.WriteAllText(
                    Path.Combine(directory, "config.toml"),
                    "[analytics]\nenabled = false\n[mcp_servers.unityMCP]\nurl = 'http://127.0.0.1:1/mcp'\n"
                        + "http_headers_helper = 'unity-mcp-capability-probe'\n"
                );
                var environment = new Dictionary<string, string> { ["CODEX_HOME"] = directory };
                if (
                    !ExecPath.TryRun(
                        executable,
                        "--no-daemon mcp get unityMCP --json",
                        directory,
                        out string output,
                        out _,
                        timeoutMs: 2500,
                        environmentOverrides: environment
                    )
                )
                {
                    return false;
                }
                supported = RecognizesHeadersHelper(output);
                return supported;
            }
            catch
            {
                return false;
            }
            finally
            {
                if (directory != null)
                {
                    try
                    {
                        Directory.Delete(directory, recursive: true);
                    }
                    catch
                    {
                        // A locked probe directory contains no credentials.
                    }
                }
            }
        }

        internal static bool RecognizesHeadersHelper(string output)
        {
            try
            {
                var transport = JObject.Parse(output)["transport"] as JObject;
                return (string)transport?["type"] == "streamable_http"
                    && transport?["http_headers_helper"]?.Type == JTokenType.String
                    && !string.IsNullOrWhiteSpace((string)transport["http_headers_helper"]);
            }
            catch
            {
                return false;
            }
        }
    }
}
