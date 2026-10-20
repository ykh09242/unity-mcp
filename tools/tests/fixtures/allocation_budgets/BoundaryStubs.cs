using System;
using System.Collections.Generic;

namespace UnityEditor.Compilation
{
    public enum CompilerMessageType
    {
        Error,
        Warning,
    }

    public struct CompilerMessage
    {
        public CompilerMessageType type;
        public string file,
            message;
        public int line,
            column;
    }
}

namespace UnityEditor
{
    public static class SessionState
    {
        private static readonly Dictionary<string, string> Data = new Dictionary<string, string>();

        public static string GetString(string key, string fallback) => Data.TryGetValue(key, out string value) ? value : fallback;

        public static void SetString(string key, string value) => Data[key] = value;

        public static void EraseString(string key) => Data.Remove(key);
    }
}

namespace UnityEngine
{
    public static class Application
    {
        public static string dataPath => Environment.CurrentDirectory;
    }
}

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioLogPolicy
    {
        public string Mode = "strict";
        public string[] AllowedMessages = Array.Empty<string>();
    }

    public sealed class PlayScenarioLog
    {
        public long TimestampUnixMs;
        public string Type,
            Message,
            StackTrace;
    }

    public sealed class PlayScenarioRun
    {
        public readonly List<PlayScenarioLog> Logs = new List<PlayScenarioLog>();
        public int DroppedLogCount,
            UnexpectedLogCount;
        public string UnexpectedLogError,
            LastUnexpectedLogError;
    }

    public static class PlayScenarioEngine
    {
        // Exact bounded-prefix semantics from PlayScenarioEngine.cs:910.
        internal static string Bounded(string value, int maxLength)
        {
            if (value == null || value.Length <= maxLength)
                return value;
            if (maxLength > 0 && char.IsHighSurrogate(value[maxLength - 1]) && char.IsLowSurrogate(value[maxLength]))
                maxLength--;
            return value.Substring(0, maxLength);
        }
    }
}
