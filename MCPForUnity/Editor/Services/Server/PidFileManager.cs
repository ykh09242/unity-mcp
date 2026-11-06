using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Constants;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Server
{
    /// <summary>
    /// Manages PID files and handshake state for the local HTTP server.
    /// Handles persistence of server process information across Unity domain reloads.
    /// </summary>
    public class PidFileManager : IPidFileManager
    {
        private readonly string _projectRoot;
        private readonly string _projectIdentityHash;
        private readonly Func<int, DateTime?> _processCreationReader;

        public PidFileManager()
            : this(GetProjectRootPath()) { }

        internal PidFileManager(string projectRoot)
            : this(projectRoot, ReadProcessCreationUtc) { }

        internal PidFileManager(string projectRoot, Func<int, DateTime?> processCreationReader)
        {
            _projectRoot = Path.GetFullPath(projectRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string identity = Application.platform == RuntimePlatform.WindowsEditor ? _projectRoot.ToUpperInvariant() : _projectRoot;
            _projectIdentityHash = ComputeShortHash(identity);
            _processCreationReader = processCreationReader ?? ReadProcessCreationUtc;
        }

        internal string PreferenceKey(string key) => key + ".Project." + _projectIdentityHash;

        internal static string ProjectPreferenceKey(string key) => new PidFileManager().PreferenceKey(key);

        /// <inheritdoc/>
        public string GetPidDirectory()
        {
            return Path.Combine(_projectRoot, "Library", "MCPForUnity", "RunState");
        }

        /// <inheritdoc/>
        public string GetPidFilePath(int port)
        {
            string dir = GetPidDirectory();
            Directory.CreateDirectory(dir);
            return Path.Combine(dir, $"mcp_http_{port}.pid");
        }

        /// <inheritdoc/>
        public bool TryReadPid(string pidFilePath, out int pid)
        {
            pid = 0;
            try
            {
                if (string.IsNullOrEmpty(pidFilePath) || !File.Exists(pidFilePath))
                {
                    return false;
                }

                string text = File.ReadAllText(pidFilePath).Trim();
                if (int.TryParse(text, out pid))
                {
                    return pid > 0;
                }

                // Best-effort: tolerate accidental extra whitespace/newlines.
                var firstLine = text.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault();
                if (int.TryParse(firstLine, out pid))
                {
                    return pid > 0;
                }

                pid = 0;
                return false;
            }
            catch
            {
                pid = 0;
                return false;
            }
        }

        /// <inheritdoc/>
        public bool TryGetPortFromPidFilePath(string pidFilePath, out int port)
        {
            port = 0;
            if (string.IsNullOrEmpty(pidFilePath))
            {
                return false;
            }

            try
            {
                string fileName = Path.GetFileNameWithoutExtension(pidFilePath);
                if (string.IsNullOrEmpty(fileName))
                {
                    return false;
                }

                const string prefix = "mcp_http_";
                if (!fileName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }

                string portText = fileName.Substring(prefix.Length);
                return int.TryParse(portText, out port) && port > 0;
            }
            catch
            {
                port = 0;
                return false;
            }
        }

        /// <inheritdoc/>
        public void DeletePidFile(string pidFilePath)
        {
            try
            {
                if (!string.IsNullOrEmpty(pidFilePath) && File.Exists(pidFilePath))
                {
                    File.Delete(pidFilePath);
                }
            }
            catch { }
        }

        /// <inheritdoc/>
        public void StoreHandshake(string pidFilePath, string instanceToken)
        {
            try
            {
                if (!string.IsNullOrEmpty(pidFilePath))
                {
                    EditorPrefs.SetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidFilePath), pidFilePath);
                }
            }
            catch { }

            try
            {
                if (!string.IsNullOrEmpty(instanceToken))
                {
                    EditorPrefs.SetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerInstanceToken), instanceToken);
                }
            }
            catch { }
        }

        /// <inheritdoc/>
        public bool TryGetHandshake(out string pidFilePath, out string instanceToken)
        {
            pidFilePath = null;
            instanceToken = null;
            try
            {
                pidFilePath = EditorPrefs.GetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidFilePath), string.Empty);
                instanceToken = EditorPrefs.GetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerInstanceToken), string.Empty);
                if (string.IsNullOrEmpty(pidFilePath) || string.IsNullOrEmpty(instanceToken))
                {
                    pidFilePath = null;
                    instanceToken = null;
                    return false;
                }
                return true;
            }
            catch
            {
                pidFilePath = null;
                instanceToken = null;
                return false;
            }
        }

        /// <inheritdoc/>
        public void StoreTracking(int pid, int port, string argsHash = null)
        {
            try
            {
                EditorPrefs.SetInt(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPid), pid);
            }
            catch { }
            try
            {
                EditorPrefs.SetInt(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPort), port);
            }
            catch { }
            try
            {
                EditorPrefs.SetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerStartedUtc), DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture));
            }
            catch { }
            try
            {
                if (!string.IsNullOrEmpty(argsHash))
                {
                    EditorPrefs.SetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidArgsHash), argsHash);
                }
                else
                {
                    EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidArgsHash));
                }
            }
            catch { }
        }

        internal void StoreOwnedLaunchProcess(System.Diagnostics.Process process, int port, string instanceToken)
        {
            if (process != null)
                StoreOwnedLaunchProcess(process.Id, port, instanceToken, ReadProcessCreationUtc(process));
        }

        internal void StoreOwnedLaunchProcess(int pid, int port, string instanceToken, DateTime? creationUtc = null)
        {
            if (pid > 1 && !string.IsNullOrEmpty(instanceToken))
            {
                StoreTracking(pid, port, ComputeShortHash("managed-launch:" + instanceToken));
                StoreProcessCreation(EditorPrefKeys.LastLocalHttpServerLauncherCreation, pid, port, instanceToken, creationUtc);
            }
        }

        internal DateTime? GetProcessCreationUtc(int pid)
        {
            try
            {
                return _processCreationReader(pid)?.ToUniversalTime();
            }
            catch
            {
                return null;
            }
        }

        private static DateTime? ReadProcessCreationUtc(int pid)
        {
            try
            {
                using var process = System.Diagnostics.Process.GetProcessById(pid);
                return ReadProcessCreationUtc(process);
            }
            catch
            {
                return null;
            }
        }

        private static DateTime? ReadProcessCreationUtc(System.Diagnostics.Process process)
        {
            try
            {
                return process.StartTime.ToUniversalTime();
            }
            catch
            {
                // Missing access or a fast exit cannot establish a different process identity.
                return null;
            }
        }

        internal void RememberOwnedServerCreation(int pid, int port, string instanceToken, DateTime creationUtc)
        {
            // Once observed, an identity must not be replaced by a later process at this PID.
            if (!TryGetProcessCreation(EditorPrefKeys.LastLocalHttpServerCreation, pid, port, instanceToken, out _))
                StoreProcessCreation(EditorPrefKeys.LastLocalHttpServerCreation, pid, port, instanceToken, creationUtc);
        }

        internal bool HasOwnedServerCreation(int pid, int port, string instanceToken) =>
            TryGetProcessCreation(EditorPrefKeys.LastLocalHttpServerCreation, pid, port, instanceToken, out _);

        internal bool HasProcessCreationMismatch(int pid, int port, string instanceToken, bool launcher)
        {
            string key = launcher ? EditorPrefKeys.LastLocalHttpServerLauncherCreation : EditorPrefKeys.LastLocalHttpServerCreation;
            return TryGetProcessCreation(key, pid, port, instanceToken, out long ticks)
                && GetProcessCreationUtc(pid) is DateTime current
                && current.Ticks != ticks;
        }

        private void StoreProcessCreation(string key, int pid, int port, string instanceToken, DateTime? creationUtc)
        {
            try
            {
                if (!creationUtc.HasValue)
                    EditorPrefs.DeleteKey(PreferenceKey(key));
                else
                    EditorPrefs.SetString(
                        PreferenceKey(key),
                        ProcessCreationPrefix(pid, port, instanceToken) + creationUtc.Value.ToUniversalTime().Ticks.ToString(CultureInfo.InvariantCulture)
                    );
            }
            catch { }
        }

        private bool TryGetProcessCreation(string key, int pid, int port, string instanceToken, out long ticks)
        {
            ticks = 0;
            if (pid <= 1 || port <= 0 || string.IsNullOrEmpty(instanceToken))
                return false;
            try
            {
                string value = EditorPrefs.GetString(PreferenceKey(key), string.Empty);
                string prefix = ProcessCreationPrefix(pid, port, instanceToken);
                return value.StartsWith(prefix, StringComparison.Ordinal)
                    && long.TryParse(value.Substring(prefix.Length), NumberStyles.None, CultureInfo.InvariantCulture, out ticks)
                    && ticks > DateTime.MinValue.Ticks
                    && ticks <= DateTime.MaxValue.Ticks;
            }
            catch
            {
                return false;
            }
        }

        private string ProcessCreationPrefix(int pid, int port, string instanceToken) =>
            pid.ToString(CultureInfo.InvariantCulture)
            + ":"
            + port.ToString(CultureInfo.InvariantCulture)
            + ":"
            + ComputeShortHash("managed-launch:" + instanceToken)
            + ":";

        internal bool TryGetOwnedLaunchProcess(int expectedPort, string instanceToken, out int pid)
        {
            pid = 0;
            if (string.IsNullOrEmpty(instanceToken))
                return false;
            try
            {
                string expectedHash = ComputeShortHash("managed-launch:" + instanceToken);
                int storedPid = EditorPrefs.GetInt(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPid), 0);
                int storedPort = EditorPrefs.GetInt(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPort), 0);
                if (storedPid <= 1 || storedPort != expectedPort || string.IsNullOrEmpty(expectedHash) || GetStoredArgsHash() != expectedHash)
                    return false;
                // This token-bound record only prevents replacement of a live
                // launch. Do not expire ownership by the heuristic six-hour TTL.
                // It never authorizes termination or substitutes for server auth.
                pid = storedPid;
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <inheritdoc/>
        public bool TryGetStoredPid(int expectedPort, out int pid)
        {
            pid = 0;
            try
            {
                int storedPid = EditorPrefs.GetInt(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPid), 0);
                int storedPort = EditorPrefs.GetInt(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPort), 0);
                string storedUtc = EditorPrefs.GetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerStartedUtc), string.Empty);

                if (storedPid <= 0 || storedPort != expectedPort)
                {
                    return false;
                }

                // Only trust the stored PID for a short window to avoid PID reuse issues.
                // (We still verify the PID is listening on the expected port before killing.)
                if (
                    !string.IsNullOrEmpty(storedUtc)
                    && DateTime.TryParse(
                        storedUtc,
                        CultureInfo.InvariantCulture,
                        DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                        out var startedAt
                    )
                )
                {
                    if ((DateTime.UtcNow - startedAt) > TimeSpan.FromHours(6))
                    {
                        return false;
                    }
                }

                pid = storedPid;
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <inheritdoc/>
        public string GetStoredArgsHash()
        {
            try
            {
                return EditorPrefs.GetString(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidArgsHash), string.Empty);
            }
            catch
            {
                return string.Empty;
            }
        }

        /// <inheritdoc/>
        public void ClearTracking()
        {
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerLauncherCreation));
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerCreation));
            }
            catch { }
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPid));
            }
            catch { }
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPort));
            }
            catch { }
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerStartedUtc));
            }
            catch { }
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidArgsHash));
            }
            catch { }
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerPidFilePath));
            }
            catch { }
            try
            {
                EditorPrefs.DeleteKey(PreferenceKey(EditorPrefKeys.LastLocalHttpServerInstanceToken));
            }
            catch { }
        }

        /// <inheritdoc/>
        public string ComputeShortHash(string input)
        {
            if (string.IsNullOrEmpty(input))
                return string.Empty;
            try
            {
                using var sha = SHA256.Create();
                byte[] bytes = Encoding.UTF8.GetBytes(input);
                byte[] hash = sha.ComputeHash(bytes);
                // 8 bytes => 16 hex chars is plenty as a stable fingerprint for our purposes.
                var sb = new StringBuilder(16);
                for (int i = 0; i < 8 && i < hash.Length; i++)
                {
                    sb.Append(hash[i].ToString("x2"));
                }
                return sb.ToString();
            }
            catch
            {
                return string.Empty;
            }
        }

        private static string GetProjectRootPath()
        {
            try
            {
                // Application.dataPath is ".../<Project>/Assets"
                return Path.GetFullPath(Path.Combine(Application.dataPath, ".."));
            }
            catch
            {
                return Application.dataPath;
            }
        }
    }
}
