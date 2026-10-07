using System;
using System.Runtime.InteropServices;
using System.Text;

namespace MCPForUnity.Editor.Helpers
{
    internal static class LocalHttpAuth
    {
        internal static string CommandForEndpoint(string url)
        {
            if (!Uri.TryCreate(url, UriKind.Absolute, out var endpoint) || !endpoint.IsLoopback || (endpoint.Scheme != "http" && endpoint.Scheme != "https"))
            {
                throw new InvalidOperationException("Automatic local authentication requires a loopback HTTP endpoint.");
            }
            return BuildCommand(HttpEndpointUtility.GetLocalAuthTokenPath(endpoint), RuntimeInformation.IsOSPlatform(OSPlatform.Windows));
        }

        internal static string BuildCommand(string path, bool windows)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                throw new ArgumentException("A local token file path is required.", nameof(path));
            }
            if (windows)
            {
                string script =
                    "$ErrorActionPreference='Stop'; "
                    + "$token=[IO.File]::ReadAllText('"
                    + path.Replace("'", "''")
                    + "').Trim(); "
                    + "if ($token -notmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid local MCP token file' }; "
                    + "@{'X-Unity-MCP-Token'=$token}|ConvertTo-Json -Compress";
                // EncodedCommand avoids nested cmd/PowerShell quoting for user-profile paths.
                return "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + Convert.ToBase64String(Encoding.Unicode.GetBytes(script));
            }
            string body =
                "token=$(cat \"$1\") || exit 1; "
                + "case \"$token\" in ''|*[!A-Za-z0-9_-]*) exit 1;; esac; "
                + "printf '{\"X-Unity-MCP-Token\":\"%s\"}' \"$token\"";
            return "/bin/sh -c '" + body.Replace("'", "'\"'\"'") + "' unity-mcp-token-v1 '" + path.Replace("'", "'\"'\"'") + "'";
        }
    }
}
