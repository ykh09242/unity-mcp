using System;
using System.Collections;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Build;
using Newtonsoft.Json.Linq;
using UnityEditor;

// Executes full production command/runner/mapping/job/parameter sources. Unity native APIs
// are replaced by observable managed seams; update callbacks are never invoked.
internal static class BuildPreflightHarness
{
    private static int failures;
    private static int checks;
    private static string outputRoot;

    private static void Check(bool value, string name)
    {
        checks++;
        if (value)
            return;
        failures++;
        Console.WriteLine("FAIL: " + name);
    }

    private static IDictionary Store(string name) =>
        (IDictionary)typeof(BuildJobStore).GetField(name, BindingFlags.Static | BindingFlags.NonPublic).GetValue(null);

    private static void Reset()
    {
        Store("_buildJobs").Clear();
        Store("_batchJobs").Clear();
        EditorApplication.update = null;
        PlayerSettings.Writes = 0;
        EditorUserBuildSettings.Switches = 0;
        EditorUserBuildSettings.SwitchSucceeds = true;
        EditorUserBuildSettings.ThrowOnSwitch = false;
        BuildPipeline.TargetSupported = true;
        EditorUserBuildSettings.activeBuildTarget = BuildTarget.StandaloneWindows64;
        EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Player;
        EditorUserBuildSettings.SubtargetWrites = 0;
        BuildPipeline.Builds = 0;
    }

    private static void Reject(JObject request, string diagnostic)
    {
        Reset();
        request["output_path"] = Path.Combine(outputRoot, "Game.exe");
        request["output_dir"] = outputRoot;
        var response = JObject.FromObject(ManageBuild.HandleCommand(request));
        string name = request.ToString(Newtonsoft.Json.Formatting.None);
        Check(!response.Value<bool>("success"), name + " rejects");
        Check((response.Value<string>("error") ?? "").Contains(diagnostic), name + " diagnostic");
        Check(PlayerSettings.Writes == 0, name + " backend untouched");
        Check(Store("_buildJobs").Count == 0 && Store("_batchJobs").Count == 0, name + " inventory untouched");
        Check(EditorApplication.update == null, name + " no scheduled callbacks");
        Check(EditorUserBuildSettings.SubtargetWrites == 0, name + " no subtarget writes");
        Check(
            EditorUserBuildSettings.Switches == 0 && EditorUserBuildSettings.standaloneBuildSubtarget == StandaloneBuildSubtarget.Player,
            name + " platform untouched"
        );
        Check(BuildPipeline.Builds == 0 && !Directory.Exists(outputRoot), name + " no build/output folder");
    }

    public static int Main(string[] args)
    {
        outputRoot = Path.Combine(Path.GetFullPath(args[0]), "never-created-output");
        foreach (
            string json in new[]
            {
                "{options:'strict_mdoe'}",
                "{options:['strict_mode','invalid']}",
                "{options:['strict_mode',123]}",
                "{options:['strict_mode',null]}",
                "{options:['strict_mode','']}",
                "{options:['strict_mode',' ']}",
                "{options:{}}",
                "{options:123}",
                "{options:''}",
                "{options:' '}",
                "{options:[[['strict_mode']]]}",
            }
        )
        {
            foreach (string action in new[] { "build", "batch" })
            {
                var request = JObject.Parse(json);
                request["action"] = action;
                request["target"] = "windows64";
                request["targets"] = new JArray("windows64");
                request["scenes"] = new JArray();
                request["scripting_backend"] = "il2cpp";
                Reject(request, "options");
            }
#if UNITY_6000_0_OR_NEWER
            foreach (string action in new[] { "build", "batch" })
            {
                var request = JObject.Parse(json);
                request["action"] = action;
                request["profile"] = "Assets/Valid.asset";
                request["profiles"] = new JArray("Assets/Valid.asset");
                request["scripting_backend"] = "il2cpp";
                Reject(request, "options");
            }
#endif
        }
        foreach (JToken subtarget in new JToken[] { "sevrer", "123", "", " ", 123, new JObject(), new JArray("player") })
        foreach (string action in new[] { "build", "platform" })
        foreach (string target in new[] { "windows64", "android" })
            Reject(
                new JObject
                {
                    ["action"] = action,
                    ["target"] = target,
                    ["scenes"] = new JArray(),
                    ["subtarget"] = subtarget.DeepClone(),
                    ["scripting_backend"] = "il2cpp",
                },
                "subtarget"
            );

        foreach (string name in new[] { "strict_mdoe", "", " ", null })
        {
            bool rejected = false;
            try
            {
                BuildRunner.ParseBuildOptions(new[] { "strict_mode", name }, true);
            }
            catch (ArgumentException)
            {
                rejected = true;
            }
            catch (Exception error)
            {
                Console.WriteLine("UNEXPECTED: " + error.GetType().Name);
            }
            Check(rejected, "parser rejects invalid options: " + name);
        }
        foreach (string name in new[] { "sevrer", "123", "", " " })
        {
            bool rejected = false;
            try
            {
                BuildTargetMapping.ResolveSubtarget(name);
            }
            catch (ArgumentException)
            {
                rejected = true;
            }
            catch (Exception error)
            {
                Console.WriteLine("UNEXPECTED: " + error.GetType().Name);
            }
            Check(rejected, "parser rejects invalid subtarget: " + name);
        }
        Check(BuildTargetMapping.ResolveSubtarget(null) == (int)StandaloneBuildSubtarget.Player, "omitted subtarget default");
        Check(BuildTargetMapping.ResolveSubtarget("PLAYER") == (int)StandaloneBuildSubtarget.Player, "player case insensitive");
        Check(BuildTargetMapping.ResolveSubtarget("SERVER") == (int)StandaloneBuildSubtarget.Server, "server case insensitive");
        Check(BuildRunner.ParseBuildOptions(null, true) == BuildOptions.Development, "development default");
        Check(
            BuildRunner
                .ParseBuildOptions(new[] { "STRICT_MODE", "allow_debugging", "connect_profiler", "scripts_only", "show_player", "include_tests" }, false)
                .HasFlag(BuildOptions.StrictMode),
            "supported aliases"
        );

        foreach (
            string json in new[]
            {
                "{options:null}",
                "{options:[]}",
                "{options:'STRICT_MODE'}",
                "{options:['strict_mode']}",
                "{options:[['strict_mode']]}",
                "{options:'[\"strict_mode\"]'}",
                "{options:['[\"strict_mode\"]']}",
            }
        )
        foreach (string action in new[] { "build", "batch" })
        {
            Reset();
            var request = JObject.Parse(json);
            request["action"] = action;
            request["target"] = "windows64";
            request["targets"] = new JArray("windows64");
            request["scenes"] = new JArray();
            request["output_path"] = Path.Combine(outputRoot, "Game.exe");
            request["output_dir"] = outputRoot;
            request["subtarget"] = "SERVER";
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(response.Value<bool>("success") && response.Value<string>("_mcp_status") == "pending", "valid control schedules: " + action + json);
            Check(EditorApplication.update != null && Store("_buildJobs").Count == 1, "valid control registers/schedules");
            Check(!Directory.Exists(outputRoot) && BuildPipeline.Builds == 0, "valid control awaits update");
        }
#if UNITY_6000_0_OR_NEWER
        foreach (string action in new[] { "build", "batch" })
        {
            Reset();
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["profile"] = "Assets/Valid.asset",
                        ["profiles"] = new JArray("Assets/Valid.asset"),
                        ["options"] = new JArray("STRICT_MODE"),
                        ["output_path"] = Path.Combine(outputRoot, "Game.exe"),
                        ["output_dir"] = outputRoot,
                        ["scripting_backend"] = "il2cpp",
                    }
                )
            );
            Check(response.Value<bool>("success") && response.Value<string>("_mcp_status") == "pending", "valid profile control schedules: " + action);
            Check(EditorApplication.update != null && Store("_buildJobs").Count == 1, "valid profile registers/schedules");
            Check(PlayerSettings.Writes == (action == "build" ? 1 : 0), "valid profile backend semantics");
        }
#endif
        foreach (JToken subtarget in new JToken[] { JValue.CreateNull(), "PLAYER", "SERVER" })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Server;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = "android",
                        ["subtarget"] = subtarget,
                    }
                )
            );
            Check(response.Value<bool>("success") && EditorUserBuildSettings.Switches == 1, "valid platform switch");
            var expected =
                subtarget.Type == JTokenType.Null || subtarget.Value<string>() == "SERVER" ? StandaloneBuildSubtarget.Server : StandaloneBuildSubtarget.Player;
            Check(EditorUserBuildSettings.standaloneBuildSubtarget == expected, "valid platform subtarget/default preserved");
        }
        foreach (var previous in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        foreach (var requested in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = previous;
            EditorUserBuildSettings.SubtargetWrites = 0;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = "windows64",
                        ["subtarget"] = requested.ToString(),
                    }
                )
            );
            string name = "same target " + previous + " -> " + requested;
            Check(response.Value<bool>("success"), name + " succeeds");
            Check(EditorUserBuildSettings.standaloneBuildSubtarget == requested, name + " applies requested state");
            Check(EditorUserBuildSettings.Switches == (previous == requested ? 0 : 1), name + " switches only on change");
            Check(EditorUserBuildSettings.SubtargetWrites == (previous == requested ? 0 : 1), name + " writes only on change");
            if (previous != requested)
                Check(EditorUserBuildSettings.SubtargetAtSwitch == requested, name + " supplies subtarget to switch");
        }
        foreach (string target in new[] { "windows64", "linux64" })
        foreach (bool explicitNull in new[] { false, true })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = StandaloneBuildSubtarget.Server;
            EditorUserBuildSettings.SubtargetWrites = 0;
            var request = new JObject { ["action"] = "platform", ["target"] = target };
            if (explicitNull)
                request["subtarget"] = JValue.CreateNull();
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(response.Value<bool>("success"), "omitted/null platform subtarget succeeds");
            Check(
                EditorUserBuildSettings.standaloneBuildSubtarget == StandaloneBuildSubtarget.Server && EditorUserBuildSettings.SubtargetWrites == 0,
                "omitted/null preserves prior subtarget without writes"
            );
            Check(EditorUserBuildSettings.Switches == (target == "windows64" ? 0 : 1), "omitted/null preserves target switch behavior");
        }
        foreach (var previous in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        foreach (bool throwOnSwitch in new[] { false, true })
        foreach (string target in new[] { "windows64", "linux64" })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = previous;
            EditorUserBuildSettings.SubtargetWrites = 0;
            EditorUserBuildSettings.SwitchSucceeds = false;
            EditorUserBuildSettings.ThrowOnSwitch = throwOnSwitch;
            var requested = previous == StandaloneBuildSubtarget.Player ? StandaloneBuildSubtarget.Server : StandaloneBuildSubtarget.Player;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = target,
                        ["subtarget"] = requested.ToString(),
                    }
                )
            );
            string name = "failed switch " + target + " " + previous + " -> " + requested + " throws=" + throwOnSwitch;
            Check(!response.Value<bool>("success"), name + " rejects");
            Check(EditorUserBuildSettings.Switches == 1 && EditorUserBuildSettings.SubtargetAtSwitch == requested, name + " attempts requested switch");
            Check(EditorUserBuildSettings.standaloneBuildSubtarget == previous, name + " restores prior subtarget");
            Check(
                PlayerSettings.Writes == 0 && Store("_buildJobs").Count == 0 && Store("_batchJobs").Count == 0 && EditorApplication.update == null,
                name + " leaves unrelated state unchanged"
            );
            Check(BuildPipeline.Builds == 0 && !Directory.Exists(outputRoot), name + " creates no build/output");
        }
        foreach (var previous in new[] { StandaloneBuildSubtarget.Player, StandaloneBuildSubtarget.Server })
        foreach (bool explicitNull in new[] { false, true })
        foreach (bool throwOnSwitch in new[] { false, true })
        {
            Reset();
            EditorUserBuildSettings.standaloneBuildSubtarget = previous;
            EditorUserBuildSettings.SubtargetWrites = 0;
            EditorUserBuildSettings.SwitchSucceeds = false;
            EditorUserBuildSettings.ThrowOnSwitch = throwOnSwitch;
            var request = new JObject { ["action"] = "platform", ["target"] = "linux64" };
            if (explicitNull)
                request["subtarget"] = JValue.CreateNull();
            var response = JObject.FromObject(ManageBuild.HandleCommand(request));
            Check(!response.Value<bool>("success"), "failed switch with omitted/null subtarget rejects");
            Check(EditorUserBuildSettings.Switches == 1, "failed switch with omitted/null subtarget attempts target switch");
            Check(
                EditorUserBuildSettings.standaloneBuildSubtarget == previous && EditorUserBuildSettings.SubtargetWrites == 0,
                "failed switch with omitted/null subtarget preserves prior state without writes"
            );
        }
        foreach (string target in new[] { "windows64", "linux64", "invalid" })
        {
            Reset();
            BuildPipeline.TargetSupported = false;
            var response = JObject.FromObject(
                ManageBuild.HandleCommand(
                    new JObject
                    {
                        ["action"] = "platform",
                        ["target"] = target,
                        ["subtarget"] = "server",
                    }
                )
            );
            Check(!response.Value<bool>("success"), "unsupported/invalid target rejects before mutation");
            Check(
                EditorUserBuildSettings.Switches == 0 && EditorUserBuildSettings.SubtargetWrites == 0,
                "unsupported/invalid target has no switch/subtarget writes"
            );
        }
        Reset();
        Console.WriteLine("BUILD_PREFLIGHT: " + checks + " checks, " + failures + " failures");
        return failures == 0 ? 0 : 1;
    }
}

namespace UnityEngine
{
    public class Object { }

    public static class Application
    {
        public static string dataPath = Path.GetFullPath("Assets");
        public static string unityVersion = "synthetic";
    }

    public static class Debug
    {
        public static void Log(object value) { }

        public static void LogError(object value) { }
    }
}

namespace UnityEngine.SceneManagement
{
    public struct Scene
    {
        public bool isDirty;
        public string path;
        public string name;
    }

    public static class SceneManager
    {
        public static int sceneCount => 0;

        public static Scene GetSceneAt(int i) => default;
    }
}

namespace UnityEditor
{
    public enum BuildTarget
    {
        NoTarget = -2,
        StandaloneWindows,
        StandaloneWindows64,
        StandaloneOSX,
        StandaloneLinux64,
        Android,
        iOS,
        WebGL,
        WSAPlayer,
        tvOS,
        VisionOS,
    }

    public enum BuildTargetGroup
    {
        Unknown,
        Standalone,
        Android,
        iOS,
        WebGL,
        WSA,
        tvOS,
        VisionOS,
    }

    public enum StandaloneBuildSubtarget
    {
        Player,
        Server,
    }

    public enum ScriptingImplementation
    {
        Mono2x,
        IL2CPP,
    }

    [Flags]
    public enum BuildOptions
    {
        None = 0,
        Development = 1,
        CleanBuildCache = 2,
        AutoRunPlayer = 4,
        EnableDeepProfilingSupport = 8,
        CompressWithLz4 = 16,
        StrictMode = 32,
        DetailedBuildReport = 64,
        AllowDebugging = 128,
        ConnectWithProfiler = 256,
        BuildScriptsOnly = 512,
        ShowBuiltPlayer = 1024,
        IncludeTestAssemblies = 2048,
    }

    public struct BuildPlayerOptions
    {
        public BuildTarget target;
        public BuildTargetGroup targetGroup;
        public string locationPathName;
        public string[] scenes;
        public BuildOptions options;
        public int subtarget;
    }

    public struct BuildPlayerWithProfileOptions
    {
        public Build.Profile.BuildProfile buildProfile;
        public string locationPathName;
        public BuildOptions options;
    }

    public static class EditorApplication
    {
        public static Action update;
    }

    public static class EditorUserBuildSettings
    {
        public static BuildTarget activeBuildTarget = BuildTarget.StandaloneWindows64;
        private static StandaloneBuildSubtarget _standaloneBuildSubtarget;
        public static StandaloneBuildSubtarget standaloneBuildSubtarget
        {
            get => _standaloneBuildSubtarget;
            set
            {
                SubtargetWrites++;
                _standaloneBuildSubtarget = value;
            }
        }
        public static int SubtargetWrites;
        public static StandaloneBuildSubtarget SubtargetAtSwitch;
        public static bool buildAppBundle;
        public static int Switches;
        public static bool SwitchSucceeds = true;
        public static bool ThrowOnSwitch;

        public static bool SwitchActiveBuildTarget(BuildTargetGroup group, BuildTarget target)
        {
            Switches++;
            SubtargetAtSwitch = standaloneBuildSubtarget;
            if (ThrowOnSwitch)
                throw new InvalidOperationException("Synthetic platform switch failure");
            if (!SwitchSucceeds)
                return false;
            activeBuildTarget = target;
            return true;
        }
    }

    public static class PlayerSettings
    {
        public static string productName = "Game";
        public static int Writes;

        public static void SetScriptingBackend(Build.NamedBuildTarget target, ScriptingImplementation backend)
        {
            Writes++;
        }
    }

    public static class BuildPipeline
    {
        public static bool isBuildingPlayer;
        public static int Builds;

        public static bool TargetSupported = true;

        public static bool IsBuildTargetSupported(BuildTargetGroup group, BuildTarget target) => TargetSupported;

        public static BuildTargetGroup GetBuildTargetGroup(BuildTarget target) => MCPForUnity.Editor.Tools.Build.BuildTargetMapping.GetTargetGroup(target);

        public static Build.Reporting.BuildReport BuildPlayer(BuildPlayerOptions options)
        {
            Builds++;
            throw new InvalidOperationException("Native build forbidden");
        }

        public static Build.Reporting.BuildReport BuildPlayer(BuildPlayerWithProfileOptions options)
        {
            Builds++;
            throw new InvalidOperationException("Native profile build forbidden");
        }
    }

    public class SceneAsset : UnityEngine.Object { }

    public class EditorBuildSettingsScene
    {
        public string path;
        public bool enabled;
        public Guid guid;

        public EditorBuildSettingsScene(string p, bool e)
        {
            path = p;
            enabled = e;
        }
    }

    public static class EditorBuildSettings
    {
        public static EditorBuildSettingsScene[] scenes = Array.Empty<EditorBuildSettingsScene>();
    }

    public static class AssetDatabase
    {
        public static T LoadAssetAtPath<T>(string path)
            where T : UnityEngine.Object
        {
            if (typeof(T) == typeof(Build.Profile.BuildProfile) && path == "Assets/Valid.asset")
                return (T)(UnityEngine.Object)new Build.Profile.BuildProfile();
            return null;
        }

        public static void SaveAssets() { }

        public static string[] FindAssets(string filter) => Array.Empty<string>();

        public static string GetAssetPath(UnityEngine.Object obj) => "Assets/Valid.asset";
    }

    public sealed class SerializedObject : IDisposable
    {
        public SerializedObject(UnityEngine.Object obj) { }

        public SerializedProperty FindProperty(string name) =>
            new SerializedProperty { intValue = name == "m_BuildTarget" ? (int)BuildTarget.StandaloneWindows64 : (int)StandaloneBuildSubtarget.Player };

        public void Dispose() { }
    }

    public sealed class SerializedProperty
    {
        public int intValue;
    }
}

namespace UnityEditor.Build
{
    public struct NamedBuildTarget
    {
        public static NamedBuildTarget Server => default;

        public static NamedBuildTarget FromBuildTargetGroup(UnityEditor.BuildTargetGroup group) => default;
    }
}

namespace UnityEditor.Build.Profile
{
    public class BuildProfile : UnityEngine.Object
    {
        public UnityEditor.EditorBuildSettingsScene[] scenes = Array.Empty<UnityEditor.EditorBuildSettingsScene>();

        public static BuildProfile GetActiveBuildProfile() => null;

        public static void SetActiveBuildProfile(BuildProfile profile) { }
    }
}

namespace UnityEditor.Build.Reporting
{
    public enum BuildResult
    {
        Succeeded,
        Failed,
    }

    public class BuildSummary
    {
        public UnityEditor.BuildTarget platform;
        public string outputPath;
        public TimeSpan totalTime;
        public ulong totalSize;
        public int totalErrors;
        public int totalWarnings;
        public BuildResult result;
    }

    public class BuildReport
    {
        public static BuildReport GetLatestReport() => null;

        public BuildSummary summary;

        public string SummarizeErrors() => "synthetic failure";
    }
}

namespace UnityEditor.SceneManagement
{
    public static class EditorSceneManager
    {
        public static void SaveScene(UnityEngine.SceneManagement.Scene scene) { }
    }
}

namespace MCPForUnity.Editor.Helpers
{
    public static class McpLog
    {
        public static void Warn(string message) { }
    }

    public static class AssetPathUtility
    {
        public static string GetContainedAssetPath(string path) => path;

        public static string GetAssetPathFromGuid(string guid) => guid;
    }
}

namespace MCPForUnity.Editor.Tools
{
    [AttributeUsage(AttributeTargets.Class)]
    public sealed class McpForUnityToolAttribute : Attribute
    {
        public McpForUnityToolAttribute(string name) { }

        public bool AutoRegister { get; set; }
        public bool RequiresExplicitConsent { get; set; }
        public string Group { get; set; }
        public bool RequiresPolling { get; set; }
        public string PollAction { get; set; }
        public int MaxPollSeconds { get; set; }
    }
}

namespace MCPForUnity.Editor.Tools.Build
{
    public static class BuildOutputCleaner
    {
        public static object Clean(string root, string path, bool dryRun, bool busy) => throw new InvalidOperationException("Unused seam");
    }

    public static class BuildSettingsHelper
    {
        public static string[] ValidProperties = Array.Empty<string>();

        public static object ReadProperty(string property, UnityEditor.Build.NamedBuildTarget target) => throw new InvalidOperationException("Unused seam");

        public static string WriteProperty(string property, string value, UnityEditor.Build.NamedBuildTarget target) =>
            throw new InvalidOperationException("Unused seam");
    }
}
