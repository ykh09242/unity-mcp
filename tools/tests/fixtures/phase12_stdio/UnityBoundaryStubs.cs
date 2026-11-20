using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json.Linq;

// Complete production host and response types are compiled beside these in-memory
// Unity/service boundaries. No real editor, preferences, discovery or credentials.
namespace UnityEngine
{
    public static class Debug
    {
        public static void Log(string message) { }
    }

    public static class Application
    {
        public static bool isBatchMode = true;
        public static string dataPath => "OwnedFixture/Assets";
        public static string unityVersion => "fixture";
        public static string platform => "fixture";
    }
}

namespace UnityEditor
{
    public sealed class InitializeOnLoadAttribute : Attribute { }

    public sealed class MenuItemAttribute : Attribute
    {
        public MenuItemAttribute(string path) { }
    }

    public static class EditorApplication
    {
        public delegate void CallbackFunction();
        public static double timeSinceStartup = 20;
        public static CallbackFunction update,
            quitting,
            delayCall;
        public static Action<int> playModeStateChanged;

        public static void QueuePlayerLoopUpdate() { }
    }

    public static class SessionState
    {
        public static bool GetBool(string key, bool fallback) => fallback;

        public static string GetString(string key, string fallback) => fallback;

        public static void SetBool(string key, bool value) => throw new InvalidOperationException("Session state writes are forbidden");

        public static void SetString(string key, string value) => throw new InvalidOperationException("Session state writes are forbidden");
    }

    public static class AssemblyReloadEvents
    {
        public static Action beforeAssemblyReload;
    }

    public static class EditorPrefs
    {
        public static bool GetBool(string key, bool fallback) => fallback;

        public static void SetInt(string key, int value)
        {
            throw new InvalidOperationException("Real prefs path forbidden");
        }
    }
}

namespace MCPForUnity.Editor.Constants
{
    public static class EditorPrefKeys
    {
        public const string DebugLogs = "fixture-debug";
        public const string UnitySocketPort = "fixture-port";
        public const string ProjectScopedToolsLocalHttp = "fixture-tools";
    }
}

namespace MCPForUnity.Editor.Helpers
{
    public static class McpLog
    {
        public static void Info(string text, bool always = true) { }

        public static void Warn(string text) { }

        public static void Error(string text) { }
    }

    internal static class McpLogRecord
    {
        internal static bool IsEnabled => false;

        internal static void LogTiming(JObject entry) => throw new InvalidOperationException("Timing logs are disabled");
    }

    public sealed class EditorConfigurationCache
    {
        public static readonly EditorConfigurationCache Instance = new EditorConfigurationCache();
        public bool UseHttpTransport = true;
    }

    public static class EditorStateCache
    {
        public static bool GetActualIsCompiling() => false;
    }

    public static class PortManager
    {
        public static int GetPortWithFallback() => throw new InvalidOperationException("Discovery forbidden");

        public static int DiscoverNewPort() => throw new InvalidOperationException("Discovery forbidden");

        public static bool ShouldAbandonBusyPort(double seconds) => false;

        public const double BusyPortFallbackWindowSeconds = 5;
    }

    public static class TelemetryHelper
    {
        public static void RecordBridgeStartup() { }

        public static void RecordBridgeConnection(bool status, string message) { }
    }

    public static class AssetPathUtility
    {
        public static string GetPackageVersion() => "fixture";
    }

    public sealed class ToolParams
    {
        private readonly JObject value;

        public ToolParams(JObject value)
        {
            this.value = value;
        }

        public int? GetInt(string key, int fallback) => (int?)value[key] ?? fallback;
    }
}

namespace MCPForUnity.Editor.Tools
{
    public static class CommandRegistry
    {
        public static void Initialize() { }

        public static bool IsTrustedBuiltInHandler(string name) => false;
    }

    internal static class ToolOperationPolicy
    {
        internal static (string Reason, bool Retryable) Resolve(string name, JObject parameters, bool trusted) =>
            throw new InvalidOperationException("Resume admission is outside this fixture");
    }
}

namespace MCPForUnity.Editor.Tools.Prefabs
{
    internal sealed class BoundaryMarker { }
}

namespace MCPForUnity.Editor.Services
{
    internal static class EditorMainThreadWakeup
    {
        internal static Action Pump;

        internal static void Request() { }
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal readonly struct NativeAdmissionBlocker
    {
        internal NativeAdmissionBlocker(string reason, bool retryable)
        {
            Reason = reason;
            Retryable = retryable;
        }

        internal string Reason { get; }
        internal bool Retryable { get; }
        internal string RecoveryAction => null;
        internal object Recovery => null;
    }

    internal sealed class NativeCommandArbitrator
    {
        internal const string Capability = "native_command_arbitration_v1";
        internal static readonly NativeCommandArbitrator Shared = new();
        internal string Generation => "fixture-generation";
    }

    internal enum TransportMode
    {
        Stdio,
    }

    internal static class TransportCommandDispatcher
    {
        public static int Calls;
        public static Task Settlement = Task.CompletedTask;

        public static Task<CommandResumeJournal.Admission> AdmitResumeAsync(Command command, CancellationToken cancel) =>
            throw new InvalidOperationException("Resume admission is outside this fixture");

        public static TransportCommandOperation ExecuteTimedCommandAsync(
            Command command,
            CancellationToken cancel,
            TransportMode mode,
            CommandPhaseTiming timing,
            CommandProgressBuffer progress,
            CancellationToken admission,
            CancellationToken owner,
            Func<Task> complete = null
        )
        {
            Calls++;
            cancel.ThrowIfCancellationRequested();
            return new TransportCommandOperation(
                Task.FromResult(TransportCommandResponse.FromObject(new { status = "success", result = command.type })),
                Settlement,
                Task.FromResult("unused")
            );
        }
    }
}

namespace MCPForUnity.Editor.Services.Transport.Transports
{
    internal sealed class StdioLaunchCredential : IDisposable
    {
        public StdioLaunchCredential(string generation, string token)
        {
            throw new InvalidOperationException("Credential publication is forbidden");
        }

        public void Dispose() { }
    }
}
