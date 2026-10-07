using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services
{
    /// <summary>
    /// Implementation of path resolver service with override support
    /// </summary>
    public class PathResolverService : IPathResolverService
    {
        private bool _hasUvxPathFallback;
        private readonly object _validationLock = new object();
        private readonly Dictionary<string, (DateTime Started, Task<string> Result)> _validations = new();
        private readonly Func<string, string> _probe;

        public PathResolverService()
            : this(null) { }

        internal PathResolverService(Func<string, string> probe)
        {
            _probe = probe ?? ProbeUvxVersion;
        }

        public bool HasUvxPathOverride => !string.IsNullOrEmpty(EditorPrefs.GetString(EditorPrefKeys.UvxPathOverride, null));
        public bool HasClaudeCliPathOverride => !string.IsNullOrEmpty(EditorPrefs.GetString(EditorPrefKeys.ClaudeCliPathOverride, null));
        public bool HasUvxPathFallback => _hasUvxPathFallback;

        public string GetUvxPath()
        {
            // Reset fallback flag at the start of each resolution
            _hasUvxPathFallback = false;

            // Check override first using filesystem and cached validation results.
            if (HasUvxPathOverride)
            {
                string overridePath = EditorPrefs.GetString(EditorPrefKeys.UvxPathOverride, string.Empty);
                // Resolution must never start a process on focus/repaint. Validation is
                // asynchronous; a known failed probe enables the existing fallback behavior.
                bool knownInvalid = false;
                lock (_validationLock)
                {
                    if (
                        _validations.TryGetValue(overridePath, out var validation)
                        && DateTime.UtcNow - validation.Started < TimeSpan.FromSeconds(30)
                        && validation.Result.Status == TaskStatus.RanToCompletion
                    )
                        knownInvalid = validation.Result.Result == null;
                }
                if (File.Exists(overridePath) && !knownInvalid)
                {
                    return overridePath;
                }
                // Override is set but invalid - fall back to system discovery
                string fallbackPath = ResolveUvxFromSystem();
                if (!string.IsNullOrEmpty(fallbackPath))
                {
                    _hasUvxPathFallback = true;
                    return fallbackPath;
                }
                // Return null to indicate override is invalid and no system fallback found
                return null;
            }

            // No override set - try discovery (uvx first, then uv)
            string discovered = ResolveUvxFromSystem();
            if (!string.IsNullOrEmpty(discovered))
            {
                return discovered;
            }

            // Fallback to bare command
            return "uvx";
        }

        /// <summary>
        /// Resolves uv/uvx from system by trying both commands.
        /// Returns the full path if found, null otherwise.
        /// </summary>
        private static string ResolveUvxFromSystem()
        {
            try
            {
                // Try uvx first, then uv
                string[] commandNames = RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? new[] { "uvx.exe", "uv.exe" } : new[] { "uvx", "uv" };

                foreach (string commandName in commandNames)
                {
                    foreach (string candidate in EnumerateCommandCandidates(commandName))
                    {
                        if (!string.IsNullOrEmpty(candidate) && File.Exists(candidate))
                        {
                            return candidate;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                McpLog.Debug($"PathResolver error: {ex.Message}");
            }

            return null;
        }

        public string GetClaudeCliPath()
        {
            // Check override first - only validate if explicitly set
            if (HasClaudeCliPathOverride)
            {
                string overridePath = EditorPrefs.GetString(EditorPrefKeys.ClaudeCliPathOverride, string.Empty);
                // Validate the override - if invalid, don't fall back to discovery
                if (File.Exists(overridePath))
                {
                    return overridePath;
                }
                // Override is set but invalid - return null (no fallback)
                return null;
            }

            // No override: delegate to the shared discovery in ExecPath, which covers the
            // native-installer, npm, NVM and PATH-scan locations for every platform. Kept in
            // one place so both call sites (this and ExecPath's own callers) stay in sync.
            return ExecPath.ResolveClaude();
        }

        public bool IsPythonDetected()
        {
            return ExecPath.TryRun(RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? "python.exe" : "python3", "--version", null, out _, out _, 2000);
        }

        public bool IsClaudeCliDetected()
        {
            return !string.IsNullOrEmpty(GetClaudeCliPath());
        }

        public void SetUvxPathOverride(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                ClearUvxPathOverride();
                return;
            }

            if (!File.Exists(path))
            {
                throw new ArgumentException("The selected uvx executable does not exist");
            }

            EditorPrefs.SetString(EditorPrefKeys.UvxPathOverride, path);
            lock (_validationLock)
                _validations.Remove(path);
        }

        public void SetClaudeCliPathOverride(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                ClearClaudeCliPathOverride();
                return;
            }

            if (!File.Exists(path))
            {
                throw new ArgumentException("The selected Claude CLI executable does not exist");
            }

            EditorPrefs.SetString(EditorPrefKeys.ClaudeCliPathOverride, path);
        }

        public void ClearUvxPathOverride()
        {
            EditorPrefs.DeleteKey(EditorPrefKeys.UvxPathOverride);
        }

        public void ClearClaudeCliPathOverride()
        {
            EditorPrefs.DeleteKey(EditorPrefKeys.ClaudeCliPathOverride);
        }

        /// <summary>
        /// Validates the provided uv executable by running "--version" and parsing the output.
        /// </summary>
        /// <param name="uvxPath">Absolute or relative path to the uv/uvx executable.</param>
        /// <param name="version">Parsed version string if successful.</param>
        /// <returns>True when the executable runs and returns a uvx version string.</returns>
        public bool TryValidateUvxExecutable(string uvxPath, out string version)
        {
            version = ValidateUvxExecutableAsync(uvxPath).GetAwaiter().GetResult();
            return version != null;
        }

        public Task<string> ValidateUvxExecutableAsync(string uvxPath)
        {
            if (string.IsNullOrEmpty(uvxPath))
                return Task.FromResult<string>(null);
            lock (_validationLock)
            {
                if (
                    _validations.TryGetValue(uvxPath, out var cached)
                    && (!cached.Result.IsCompleted || DateTime.UtcNow - cached.Started < TimeSpan.FromSeconds(30))
                )
                    return cached.Result;
                if (_validations.Count >= 32)
                    foreach (string key in _validations.Where(pair => pair.Value.Result.IsCompleted).Select(pair => pair.Key).ToArray())
                        _validations.Remove(key);
                var task = Task.Run(() =>
                {
                    try
                    {
                        return _probe(uvxPath);
                    }
                    catch
                    {
                        return null;
                    }
                });
                _validations[uvxPath] = (DateTime.UtcNow, task);
                return task;
            }
        }

        private static string ProbeUvxVersion(string uvxPath)
        {
            string version = null;

            if (string.IsNullOrEmpty(uvxPath))
                return null;

            try
            {
                // Check if the path is just a command name (no directory separator)
                bool isBareCommand = !uvxPath.Contains('/') && !uvxPath.Contains('\\');

                if (isBareCommand)
                {
                    // For bare commands like "uvx" or "uv", use EnumerateCommandCandidates to find full path first
                    string fullPath = FindUvxExecutableInPath(uvxPath);
                    if (string.IsNullOrEmpty(fullPath))
                        return null;
                    uvxPath = fullPath;
                }

                // Use ExecPath.TryRun which properly handles async output reading and timeouts
                if (!ExecPath.TryRun(uvxPath, "--version", null, out string stdout, out string stderr, 5000))
                    return null;

                // Check stdout first, then stderr (some tools output to stderr)
                string versionOutput = !string.IsNullOrWhiteSpace(stdout) ? stdout.Trim() : stderr.Trim();

                // uv/uvx outputs "uv x.y.z" or "uvx x.y.z", extract version number
                if (versionOutput.StartsWith("uvx ") || versionOutput.StartsWith("uv "))
                {
                    // Extract version: "uv 0.9.18 (hash date)" -> "0.9.18"
                    int spaceIndex = versionOutput.IndexOf(' ');
                    if (spaceIndex >= 0)
                    {
                        string afterCommand = versionOutput.Substring(spaceIndex + 1).Trim();
                        // Version is up to the first space or parenthesis
                        int nextSpace = afterCommand.IndexOf(' ');
                        int parenIndex = afterCommand.IndexOf('(');
                        int endIndex = Math.Min(nextSpace >= 0 ? nextSpace : int.MaxValue, parenIndex >= 0 ? parenIndex : int.MaxValue);
                        version = endIndex < int.MaxValue ? afterCommand.Substring(0, endIndex).Trim() : afterCommand;
                        return string.IsNullOrEmpty(version) ? null : version;
                    }
                }
            }
            catch
            {
                // Ignore validation errors
            }

            return null;
        }

        private static string FindUvxExecutableInPath(string commandName)
        {
            try
            {
                // Generic search for any command in PATH and common locations
                foreach (string candidate in EnumerateCommandCandidates(commandName))
                {
                    if (!string.IsNullOrEmpty(candidate) && File.Exists(candidate))
                    {
                        return candidate;
                    }
                }
            }
            catch
            {
                // Ignore errors
            }

            return null;
        }

        /// <summary>
        /// Enumerates candidate paths for a generic command name.
        /// Searches PATH and common locations.
        /// </summary>
        private static IEnumerable<string> EnumerateCommandCandidates(string commandName)
        {
            string exeName = RuntimeInformation.IsOSPlatform(OSPlatform.Windows) && !commandName.EndsWith(".exe") ? commandName + ".exe" : commandName;

            // Search PATH first
            string pathEnv = Environment.GetEnvironmentVariable("PATH");
            if (!string.IsNullOrEmpty(pathEnv))
            {
                foreach (string rawDir in pathEnv.Split(Path.PathSeparator))
                {
                    if (string.IsNullOrWhiteSpace(rawDir))
                        continue;
                    string dir = rawDir.Trim();
                    yield return Path.Combine(dir, exeName);
                }
            }

            // User-local binary directories
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            if (!string.IsNullOrEmpty(home))
            {
                yield return Path.Combine(home, ".local", "bin", exeName);
                yield return Path.Combine(home, ".cargo", "bin", exeName);
            }

            // System directories (platform-specific)
            if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
            {
                yield return "/opt/homebrew/bin/" + exeName;
                yield return "/usr/local/bin/" + exeName;
            }
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux))
            {
                yield return "/usr/local/bin/" + exeName;
                yield return "/usr/bin/" + exeName;
            }
            else if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                string programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);

                if (!string.IsNullOrEmpty(localAppData))
                {
                    yield return Path.Combine(localAppData, "Programs", "uv", exeName);
                    // WinGet creates shim files in this location
                    yield return Path.Combine(localAppData, "Microsoft", "WinGet", "Links", exeName);
                }

                if (!string.IsNullOrEmpty(programFiles))
                {
                    yield return Path.Combine(programFiles, "uv", exeName);
                }
            }
        }
    }
}
