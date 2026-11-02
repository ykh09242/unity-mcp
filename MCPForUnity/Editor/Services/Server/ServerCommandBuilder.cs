using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Server
{
    /// <summary>
    /// Builds uvx/server command strings for starting the MCP HTTP server.
    /// Handles platform-specific command construction.
    /// </summary>
    public class ServerCommandBuilder : ILiteralServerCommandBuilder
    {
        /// <inheritdoc/>
        public bool TryBuildCommand(out string fileName, out string arguments, out string displayCommand, out string error)
        {
            arguments = null;
            displayCommand = null;
            if (!TryBuildLaunchCommand(out fileName, out var literalArguments, out error))
                return false;
            arguments = string.Join(" ", literalArguments.Select(QuoteIfNeeded));
            displayCommand = $"{QuoteIfNeeded(fileName)} {arguments}";
            return true;
        }

        /// <inheritdoc/>
        public bool TryBuildLaunchCommand(out string fileName, out IReadOnlyList<string> arguments, out string error)
        {
            fileName = null;
            arguments = null;
            error = null;

            bool useHttpTransport = EditorConfigurationCache.Instance.UseHttpTransport;
            if (!useHttpTransport)
            {
                error = $"HTTP transport is disabled. Enable it in the {ProductInfo.ProductName} window first.";
                return false;
            }

            string httpUrl = HttpEndpointUtility.GetLocalBaseUrl();
            if (!HttpEndpointUtility.IsHttpLocalUrlAllowedForLaunch(httpUrl, out string localUrlError))
            {
                error = string.IsNullOrEmpty(localUrlError)
                    ? $"The configured URL ({httpUrl}) is not allowed for HTTP Local launch."
                    : $"{localUrlError} (configured URL: {httpUrl})";
                return false;
            }

            string uvxPath,
                fromUrl,
                packageName;
            try
            {
                (uvxPath, fromUrl, packageName) = AssetPathUtility.GetUvxCommandParts();
            }
            catch (InvalidOperationException ex)
            {
                error = ex.Message;
                return false;
            }
            if (string.IsNullOrEmpty(uvxPath))
            {
                error = "uv is not installed or found in PATH. Install it or set an override in Advanced Settings.";
                return false;
            }

            var args = new List<string>(AssetPathUtility.GetUvxDevFlagsList());
            args.AddRange(AssetPathUtility.GetBetaServerFromArgsList());
            args.AddRange(new[] { packageName, "--transport", "http", "--http-url", httpUrl });
            bool projectScopedTools = EditorPrefs.GetBool(EditorPrefKeys.ProjectScopedToolsLocalHttp, true);
            if (projectScopedTools)
                args.Add("--project-scoped-tools");
            string profile = StartupToolProfile.Selection;
            if (!string.IsNullOrEmpty(profile))
                args.AddRange(new[] { "--tool-profile", profile });

            fileName = uvxPath;
            arguments = args;
            return true;
        }

        /// <inheritdoc/>
        public string BuildUvPathFromUvx(string uvxPath)
        {
            if (string.IsNullOrWhiteSpace(uvxPath))
            {
                return uvxPath;
            }

            string directory = Path.GetDirectoryName(uvxPath);
            string extension = Path.GetExtension(uvxPath);
            string uvFileName = "uv" + extension;

            return string.IsNullOrEmpty(directory) ? uvFileName : Path.Combine(directory, uvFileName);
        }

        /// <inheritdoc/>
        public string GetPlatformSpecificPathPrepend()
        {
            if (Application.platform == RuntimePlatform.OSXEditor)
            {
                return string.Join(Path.PathSeparator.ToString(), new[] { "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin" });
            }

            if (Application.platform == RuntimePlatform.LinuxEditor)
            {
                return string.Join(Path.PathSeparator.ToString(), new[] { "/usr/local/bin", "/usr/bin", "/bin" });
            }

            if (Application.platform == RuntimePlatform.WindowsEditor)
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);

                return string.Join(
                    Path.PathSeparator.ToString(),
                    new[]
                    {
                        !string.IsNullOrEmpty(localAppData) ? Path.Combine(localAppData, "Programs", "uv") : null,
                        !string.IsNullOrEmpty(programFiles) ? Path.Combine(programFiles, "uv") : null,
                    }
                        .Where(p => !string.IsNullOrEmpty(p))
                        .ToArray()
                );
            }

            return null;
        }

        /// <inheritdoc/>
        public string QuoteIfNeeded(string input)
        {
            if (string.IsNullOrEmpty(input))
                return input;
            return input.All(c => char.IsLetterOrDigit(c) || "_-./:\\@#=+,".IndexOf(c) >= 0) ? input : TerminalLauncher.QuoteWindowsArgument(input);
        }
    }
}
