using System;
using System.IO;
using MCPForUnity.Editor.Constants;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Helpers
{
    internal static class McpLogRecord
    {
        private static readonly string LogDir = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library", "MCPForUnity", "Logs"));
        private static readonly string UnityVersion = Application.unityVersion;
        private static readonly object _logLock = new();
        private static volatile bool _isEnabledCached;
        private static bool stopped;
        private static BufferedLogWriter writer;

        [InitializeOnLoadMethod]
        private static void RefreshFromPrefs()
        {
            _isEnabledCached = EditorPrefs.GetBool(EditorPrefKeys.LogRecordEnabled, false);
            AssemblyReloadEvents.beforeAssemblyReload -= Shutdown;
            AssemblyReloadEvents.beforeAssemblyReload += Shutdown;
            EditorApplication.quitting -= Shutdown;
            EditorApplication.quitting += Shutdown;
        }

        internal static bool IsEnabled
        {
            get => _isEnabledCached;
            set
            {
                EditorPrefs.SetBool(EditorPrefKeys.LogRecordEnabled, value);
                _isEnabledCached = value;
            }
        }

        private static BufferedLogWriter Writer()
        {
            lock (_logLock)
            {
                if (stopped)
                    return null;
                if (writer == null)
                {
                    writer = new BufferedLogWriter(LogDir, 1024, 1024 * 1024);
                    writer.Enqueue(
                        JsonConvert.SerializeObject(
                            new
                            {
                                ts = DateTime.UtcNow,
                                @event = "session_start",
                                unity = UnityVersion,
                            }
                        ),
                        false
                    );
                }
                return writer;
            }
        }

        internal static string BufferStatus
        {
            get
            {
                lock (_logLock)
                    return writer == null
                        ? "Log buffer idle"
                        : $"Log buffer: {writer.PendingCount} pending, {writer.DroppedCount} dropped, {writer.FailureCount} failed";
            }
        }

        internal static void Log(string commandType, JObject parameters, string type, string status, long durationMs, string error = null)
        {
            if (!IsEnabled)
                return;
            try
            {
                var buffer = Writer();
                if (buffer == null)
                    return;
                // Never retain parameters, payloads, action strings or error text.
                string line = JsonConvert.SerializeObject(
                    new
                    {
                        ts = DateTime.UtcNow,
                        tool = Bound(commandType, 256),
                        type = Bound(type, 32),
                        status = Bound(status, 32),
                        ms = durationMs,
                        dropped = buffer.DroppedCount,
                        failed = buffer.FailureCount,
                    }
                );
                buffer.Enqueue(line, status == "ERROR");
            }
            catch { /* Diagnostics must not fail a tool or recursively log a disk error. */ }
        }

        internal static void LogTiming(JObject entry)
        {
            if (!IsEnabled)
                return;
            try
            {
                Writer()?.Enqueue(entry.ToString(Formatting.None), false);
            }
            catch { }
        }

        private static string Bound(string value, int limit) => value == null || value.Length <= limit ? value : value.Substring(0, limit);

        internal static bool Flush(int timeoutMs = 2000)
        {
            BufferedLogWriter current;
            lock (_logLock)
                current = writer;
            return current == null || current.Flush(timeoutMs);
        }

        private static void Shutdown()
        {
            BufferedLogWriter current;
            lock (_logLock)
            {
                stopped = true;
                current = writer;
            }
            current?.Dispose();
        }
    }
}
