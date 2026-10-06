using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services;

// All Unity/process-wide boundaries are in-memory stand-ins. Both handler types
// (including their full static constructors) are the unchanged production sources.
namespace UnityEngine
{
    public static class Application { public static bool isBatchMode; }
}
namespace UnityEditor
{
    public sealed class InitializeOnLoadAttribute : Attribute { }
    public static class EditorPrefs
    {
        public static readonly Dictionary<string, bool> Values = new Dictionary<string, bool>();
        public static int Reads, Writes, Deletes;
        public static bool GetBool(string key, bool fallback) { Reads++; return Values.TryGetValue(key, out var value) ? value : fallback; }
        public static void SetBool(string key, bool value) { Writes++; Values[key] = value; }
        public static void DeleteKey(string key) { Deletes++; Values.Remove(key); }
    }
    public static class SessionState
    {
        public static readonly Dictionary<string, bool> Values = new Dictionary<string, bool>();
        public static int Reads, Writes, Deletes;
        public static bool GetBool(string key, bool fallback) { Reads++; return Values.TryGetValue(key, out var value) ? value : fallback; }
        public static void SetBool(string key, bool value) { Writes++; Values[key] = value; }
        public static void EraseBool(string key) { Deletes++; Values.Remove(key); }
    }
    public static class AssemblyReloadEvents
    {
        public static int BeforeSubscriptions, AfterSubscriptions;
        private static Action before, after;
        public static event Action beforeAssemblyReload { add { BeforeSubscriptions++; before += value; } remove { before -= value; } }
        public static event Action afterAssemblyReload { add { AfterSubscriptions++; after += value; } remove { after -= value; } }
        public static void RaiseBefore() => before?.Invoke();
        public static void RaiseAfter() => after?.Invoke();
    }
    public static class EditorApplication
    {
        public static bool isUpdating;
        public static int UpdateSubscriptions, QuittingSubscriptions, DelaySubscriptions;
        public static event Action update { add { UpdateSubscriptions++; } remove { } }
        public static event Action quitting { add { QuittingSubscriptions++; } remove { } }
        public static event Action delayCall { add { DelaySubscriptions++; } remove { } }
    }
}
namespace MCPForUnity.Editor.Constants
{
    public static class EditorPrefKeys { public const string ResumeStdioAfterReload = "MCPForUnity.ResumeStdioAfterReload"; }
}
namespace MCPForUnity.Editor.Helpers
{
    public static class McpLog { public static void Warn(string value) { } public static void Debug(string value) { } }
    public sealed class EditorConfigurationCache
    {
        public static readonly EditorConfigurationCache Instance = new EditorConfigurationCache();
        public bool UseHttpTransport = true;
    }
    public static class EditorStateCache { public static bool GetActualIsCompiling() => false; }
}
namespace MCPForUnity.Editor.Services.Transport
{
    public enum TransportMode { Http, Stdio }
    public sealed class State { public string Error; }
    public sealed class TransportManager
    {
        public static int Calls;
        public bool IsRunning(TransportMode mode) { Calls++; return false; }
        public void ForceStop(TransportMode mode) { Calls++; }
        public Task StopAsync(TransportMode mode) { Calls++; return Task.CompletedTask; }
        public Task<bool> StartAsync(TransportMode mode) { Calls++; return Task.FromResult(true); }
        public State GetState(TransportMode mode) { Calls++; return new State(); }
    }
}
namespace MCPForUnity.Editor.Services.Transport.Transports
{
    public static class StdioBridgeHost
    {
        public static bool IsRunning => false;
        public static void Stop() { }
        public static void WriteHeartbeat(bool running, string state) { }
    }
}
namespace MCPForUnity.Editor.Services
{
    public static class MCPServiceLocator
    {
        public static readonly Transport.TransportManager TransportManager = new Transport.TransportManager();
    }
}
namespace MCPForUnity.Editor.Windows
{
    public static class MCPForUnityEditorWindow { public static void RequestHealthVerification() { } }
}
public static class ReloadBatchIsolationHarness
{
    private const string HttpResume = "MCPForUnity.ResumeHttpAfterReload";
    private const string Migrated = "MCPForUnity.ResumeHttpAfterReload.Migrated";
    private const string StdioResume = "MCPForUnity.ResumeStdioAfterReload";
    private static int checks, failures;
    private static void Check(bool condition, string message)
    {
        checks++;
        if (!condition) { failures++; Console.WriteLine("FAIL: " + message); }
    }
    public static int Main(string[] args)
    {
        string scenario = args[0];
        bool batch = scenario.StartsWith("batch-", StringComparison.Ordinal);
        string allow = scenario.EndsWith("-empty", StringComparison.Ordinal) ? "" :
            scenario.EndsWith("-whitespace", StringComparison.Ordinal) ? " \t " :
            scenario.EndsWith("-optin", StringComparison.Ordinal) ? "1" :
            scenario.EndsWith("-zero", StringComparison.Ordinal) ? "0" : null;
        bool blocked = batch && string.IsNullOrWhiteSpace(allow);
        bool migrated = scenario == "interactive-migrated";
        UnityEngine.Application.isBatchMode = batch;
        Environment.SetEnvironmentVariable("UNITY_MCP_ALLOW_BATCH", allow);
        UnityEditor.EditorPrefs.Values[HttpResume] = true;
        UnityEditor.EditorPrefs.Values[StdioResume] = true;
        if (migrated) UnityEditor.SessionState.Values[Migrated] = true;

        RuntimeHelpers.RunClassConstructor(typeof(HttpBridgeReloadHandler).TypeHandle);
        // Assert HTTP in isolation so a future guard accidentally placed after migration
        // cannot hide behind the stdio constructor's subscription counts.
        Check(UnityEditor.EditorPrefs.Deletes == (blocked || migrated ? 0 : 1), "HTTP migration deletion policy");
        Check(UnityEditor.SessionState.Reads == (blocked ? 0 : 1), "HTTP session reads policy");
        Check(UnityEditor.SessionState.Writes == (blocked || migrated ? 0 : 1), "HTTP migration writes policy");
        Check(UnityEditor.AssemblyReloadEvents.BeforeSubscriptions == (blocked ? 0 : 1), "HTTP before-reload subscription");
        Check(UnityEditor.AssemblyReloadEvents.AfterSubscriptions == (blocked ? 0 : 1), "HTTP after-reload subscription");

        RuntimeHelpers.RunClassConstructor(typeof(StdioBridgeReloadHandler).TypeHandle);
        Check(UnityEditor.AssemblyReloadEvents.BeforeSubscriptions == (blocked ? 0 : 2), "combined before-reload subscriptions");
        Check(UnityEditor.AssemblyReloadEvents.AfterSubscriptions == (blocked ? 0 : 2), "combined after-reload subscriptions");
        Check(UnityEditor.EditorApplication.QuittingSubscriptions == (blocked ? 0 : 1), "stdio quitting subscription");
        Check(UnityEditor.EditorApplication.UpdateSubscriptions == 0 && UnityEditor.EditorApplication.DelaySubscriptions == 0, "no resume scheduling during construction");
        Check(UnityEditor.EditorPrefs.Reads == 0 && UnityEditor.EditorPrefs.Writes == 0, "no resume consumption during construction");
        Check(UnityEditor.EditorPrefs.Values.ContainsKey(HttpResume) == (blocked || migrated), "HTTP preference retained only when migration is skipped");
        Check(UnityEditor.EditorPrefs.Values.TryGetValue(StdioResume, out bool stdioPending) && stdioPending, "stdio interactive pending resume retained");
        Check(MCPForUnity.Editor.Services.Transport.TransportManager.Calls == 0, "no transport lifecycle during construction");
        if (blocked)
        {
            UnityEditor.AssemblyReloadEvents.RaiseBefore();
            UnityEditor.AssemblyReloadEvents.RaiseAfter();
            Check(UnityEditor.EditorPrefs.Deletes == 0 && UnityEditor.EditorPrefs.Reads == 0 && UnityEditor.EditorPrefs.Writes == 0,
                "blocked reload events leave interactive preferences untouched");
            Check(UnityEditor.SessionState.Reads == 0 && UnityEditor.SessionState.Writes == 0 && UnityEditor.SessionState.Deletes == 0,
                "blocked reload events leave session state untouched");
        }
        Console.WriteLine(scenario + ": " + (checks - failures) + "/" + checks + " checks passed");
        return failures == 0 ? 0 : 1;
    }
}
