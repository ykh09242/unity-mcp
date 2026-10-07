using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

// Only Unity and external service boundaries are replaced. Transport, dispatcher,
// registry, response projection and large-result writer compile from production sources.
namespace UnityEditor
{
    public sealed class InitializeOnLoadAttribute : Attribute { }

    public static class EditorApplication
    {
        public delegate void CallbackFunction();
        public static CallbackFunction update;
        public static CallbackFunction delayCall;

        public static void QueuePlayerLoopUpdate() { }

        public static void Pump()
        {
            update?.Invoke();
            var callbacks = delayCall;
            delayCall = null;
            callbacks?.Invoke();
        }
    }

    public static class EditorPrefs
    {
        private static readonly Dictionary<string, string> Values = new Dictionary<string, string>();

        public static bool HasKey(string key) => Values.ContainsKey(key);

        public static string GetString(string key, string fallback = "") => Values.TryGetValue(key, out var value) ? value : fallback;

        public static void SetString(string key, string value) => Values[key] = value;

        public static void DeleteKey(string key) => Values.Remove(key);

        public static int GetInt(string key, int fallback = 0) => Values.TryGetValue(key, out var value) ? int.Parse(value) : fallback;
    }

    public static class AssetDatabase
    {
        public static bool IsAssetImportWorkerProcess() => false;
    }

    public static class TypeCache
    {
        public static IEnumerable<Type> GetTypesWithAttribute<T>() => Array.Empty<Type>();
    }
}

namespace UnityEngine
{
    public enum LogType
    {
        Log,
    }

    public static class Application
    {
        public delegate void LogCallback(string message, string trace, LogType type);
        public static event LogCallback logMessageReceivedThreaded;
        public static string dataPath => "Fixture/Assets";
        public static string unityVersion => "6000.0.69f1";

        public static void Emit(string text) => logMessageReceivedThreaded?.Invoke(text, "", LogType.Log);
    }
}

namespace MCPForUnity.Editor.Constants
{
    public static class AuthConstants
    {
        public const string ApiKeyHeader = "fixture-api";
        public const string LocalTokenHeader = "fixture-local";
    }

    public static class EditorPrefKeys
    {
        public const string ApiKey = "fixture-key";
        public const string SessionId = "fixture-session";
        public const string BatchExecuteMaxCommands = "fixture-batch";
    }
}

namespace MCPForUnity.Runtime.Helpers
{
    public sealed class BoundaryMarker { }
}

namespace MCPForUnity.Editor.Resources
{
    public sealed class McpForUnityResourceAttribute : Attribute
    {
        public string ResourceName { get; set; }
    }
}

namespace MCPForUnity.Editor.Tools
{
    public sealed class McpForUnityToolAttribute : Attribute
    {
        public string CommandName { get; set; }
        public bool AutoRegister { get; set; }
        public bool RequiresExplicitConsent { get; set; }

        public McpForUnityToolAttribute(string name = null)
        {
            CommandName = name;
        }
    }
}

namespace MCPForUnity.Editor.Helpers
{
    public static class McpLog
    {
        private static bool _debugEnabled = false;

        public static void Info(string text, bool enabled = true) { }

        public static void Warn(string text) { }

        public static void Error(string text) { }

        public static void Debug(string text)
        {
            if (_debugEnabled)
                UnityEngine.Application.Emit(text);
        }
    }

    public static class McpLogRecord
    {
        private static bool? _isEnabledCached = false;
        public static bool IsEnabled => _isEnabledCached ?? false;

        public static void Log(string name, JObject parameters, string type, string status, long elapsed, string error) { }
    }

    public static class ProjectIdentityUtility
    {
        public static string GetProjectName() => "Fixture";

        public static string GetProjectHash() => "fixture-hash";

        public static void PersistSessionIdOnMainThread(string id) => UnityEditor.EditorPrefs.SetString("fixture-session_fixture-hash", id);
    }

    public static class HttpEndpointUtility
    {
        public static bool IsRemoteScope() => false;

        public static bool IsCurrentRemoteUrlAllowed(out string error)
        {
            error = null;
            return true;
        }

        public static string GetBaseUrl() => "http://127.0.0.1:1";

        public static string ReadLocalAuthToken(Uri endpoint) => "fixture";
    }
}

namespace MCPForUnity.Editor.Services
{
    public static class ToolDiscoveryService
    {
        public static IEnumerable<Type> InRegistrationOrder(IEnumerable<Type> types) => types;
    }

    public sealed class FixtureDiscovery
    {
        public ToolMetadata GetToolMetadata(string name) => null;

        public ToolMetadata GetResourceMetadata(string name) => null;

        public bool IsToolEnabled(string name) => true;

        public bool IsResourceEnabled(string name) => true;
    }

    public static class MCPServiceLocator
    {
        public static readonly FixtureDiscovery ToolDiscovery = new FixtureDiscovery();
        public static readonly FixtureDiscovery ResourceDiscovery = new FixtureDiscovery();
    }

    internal sealed class EditorStatePublisher : IDisposable
    {
        internal const string Capability = "editor_state_v1";
        internal static int Starts;
        internal static int Disposals;
        private bool _disposed;

        internal static EditorStatePublisher Start(Func<JObject, CancellationToken, Task> send, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            Starts++;
            return new EditorStatePublisher();
        }

        public void Dispose()
        {
            if (!_disposed)
            {
                Disposals++;
                _disposed = true;
            }
        }
    }
}
