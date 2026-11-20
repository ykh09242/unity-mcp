using System;
using Newtonsoft.Json.Linq;

namespace UnityEditor
{
    internal class InitializeOnLoadAttribute : Attribute { }

    internal class MenuItemAttribute : Attribute
    {
        public MenuItemAttribute(string path) { }
    }

    internal static class SessionState
    {
        public static string GetString(string name, string fallback) => throw new NotSupportedException();

        public static void SetString(string name, string value) => throw new NotSupportedException();
    }
}

namespace UnityEngine
{
    internal static class Debug
    {
        public static void Log(string message) => throw new NotSupportedException();
    }
}

namespace MCPForUnity.Editor.Tools
{
    internal static class CommandRegistry
    {
        internal static bool IsTrustedBuiltInHandler(string name) => throw new NotSupportedException();
    }
}

namespace MCPForUnity.Editor.Services.Transport
{
    internal static class NativeOperationBarriers
    {
        internal static bool TryGetBlocker(string name, JObject parameters, out NativeAdmissionBlocker reason) => throw new NotSupportedException();
    }
}
