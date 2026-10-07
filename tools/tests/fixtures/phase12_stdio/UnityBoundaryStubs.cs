using System;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Models;
using Newtonsoft.Json.Linq;

// Complete production host and response types are compiled beside these in-memory
// Unity/service boundaries. No real editor, preferences, discovery or credentials.
namespace UnityEngine
{
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

    public static class EditorApplication
    {
        public static double timeSinceStartup = 20;
        public static Action update,
            quitting,
            delayCall;
        public static Action<int> playModeStateChanged;

        public static void QueuePlayerLoopUpdate() { }
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
    }
}

namespace MCPForUnity.Editor.Tools.Prefabs
{
    internal sealed class BoundaryMarker { }
}

namespace MCPForUnity.Editor.Services
{
    internal sealed class BoundaryMarker { }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal static class TransportCommandDispatcher
    {
        public static int Calls;
        public static Task Settlement = Task.CompletedTask;

        public static TransportCommandOperation ExecuteCommandAsync(Command command, CancellationToken cancel)
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
    internal sealed class StdioBridgeAuthentication : IDisposable
    {
        public StdioBridgeAuthentication(string token, bool publish)
        {
            throw new InvalidOperationException("Authentication forbidden");
        }

        public Task<object> AuthenticateAsync(System.IO.Stream stream, CancellationToken cancel) =>
            throw new InvalidOperationException("Authentication forbidden");

        public void Dispose() { }
    }
}
