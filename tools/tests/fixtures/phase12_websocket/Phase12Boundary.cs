using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace UnityEditor
{
    public sealed class MenuItemAttribute : Attribute
    {
        public MenuItemAttribute(string path) { }
    }

    public static class AssemblyReloadEvents
    {
        public static Action beforeAssemblyReload;
    }

    public static class SessionState
    {
        public static string GetString(string key, string fallback) => fallback;

        public static void SetString(string key, string value) => throw new InvalidOperationException("Session writes are outside this fixture");
    }
}

namespace UnityEngine
{
    public static class Debug
    {
        public static void Log(string message) { }

        public static void LogException(Exception error) => Console.Error.WriteLine(error);
    }
}

namespace MCPForUnity.Editor.Services
{
    internal static class ToolConsentThread
    {
        private static int mainThread;

        internal static void Capture() => mainThread = Thread.CurrentThread.ManagedThreadId;

        internal static bool IsMainThread => Thread.CurrentThread.ManagedThreadId == mainThread;
    }

    internal static class ToolSchemaBuilder
    {
        internal static void AppendSchema(JObject descriptor, ToolMetadata tool) => throw new NotSupportedException("Tool catalog fixture has no tools");
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal enum TransportMode
    {
        WebSocket,
        Stdio,
    }

    internal static class NativeOperationBarriers
    {
        internal static bool TryGetBlocker(string name, JObject parameters, out NativeAdmissionBlocker blocker)
        {
            blocker = default;
            return false;
        }
    }
}

// The registry uses these types to identify built-in handlers. The fixture registers
// only its own handlers, so a call into any built-in validator is an unexpected path.
namespace MCPForUnity.Editor.Tools
{
    internal static class ToolCapabilityCatalog
    {
        internal static Func<JObject, BatchActionValidation> GetBatchValidator(Type tool) => throw new NotSupportedException("No built-ins are registered");
    }

    internal sealed class ManageScene
    {
        internal static BatchActionValidation ValidateBatchAction(JObject parameters) => throw new NotSupportedException();
    }

    internal sealed class ManageAsset { }

    internal sealed class PrefabContents { }

    internal static class ManageUGUI
    {
        internal static JObject NormalizeParameterAliases(JObject source) => throw new NotSupportedException();
    }

    internal static class BatchTargetPreview
    {
        internal static object Prepare(JArray commands) => throw new NotSupportedException();

        internal static Task<object> PrepareAsync(JArray commands, CancellationToken token) => throw new NotSupportedException();

        internal static Task<Observation> VerifyAsync(JArray commands, JObject preview, CancellationToken token) => throw new NotSupportedException();

        internal static Task<Observation> MatchAsync(JObject command, JObject preview, int index, SynchronousHashBudget budget, CancellationToken token) =>
            throw new NotSupportedException();

        internal sealed class Observation : IDisposable
        {
            internal bool IsCurrent(JToken value) => throw new NotSupportedException();

            public void Dispose() { }
        }

        internal sealed class SynchronousHashBudget
        {
            internal static SynchronousHashBudget ForEditor() => throw new NotSupportedException();
        }
    }
}

namespace MCPForUnity.Editor.Tools.Prefabs
{
    internal sealed class ManagePrefabs { }
}

namespace MCPForUnity.Editor.Tools.Capabilities
{
    internal sealed class AssetEdit { }

    internal sealed class GameobjectEdit { }
}

namespace MCPForUnity.Editor.Tools.GameObjects
{
    internal sealed class ManageGameObject
    {
        internal static BatchActionValidation ValidateBatchAction(JObject parameters) => throw new NotSupportedException();

        internal static JObject NormalizeParameterAliases(JObject source) => throw new NotSupportedException();

        internal static void ResolveParameterAlias(JObject parameters, string canonical, string alias) => throw new NotSupportedException();
    }

    internal sealed class ManageComponents
    {
        internal static BatchActionValidation ValidateBatchAction(JObject parameters) => throw new NotSupportedException();
    }
}

namespace MCPForUnity.Editor.Tools.Blender
{
    internal sealed class BlenderBridgeTool { }
}
