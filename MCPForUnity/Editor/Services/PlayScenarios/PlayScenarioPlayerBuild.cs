using System;
using System.IO;
using System.Linq;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Services.PlayScenarios
{
    public sealed class PlayScenarioPlayerBuildResult
    {
        public string BundlePath;
        public string ExecutablePath;
        public string DefinitionHash;
    }

    /// <summary>Explicit standalone scenario builds; restores the selected scripting backend after building.</summary>
    public static class PlayScenarioPlayerBuild
    {
        public static PlayScenarioPlayerBuildResult Build(
            string savedName,
            string outputDirectory,
            string buildSourceRevision = null,
            string target = "windows64",
            string backend = "mono"
        )
        {
            PlayScenarioReproduction.ValidateSourceRevision(buildSourceRevision);
            EnsureEditorReady();
            string project = Path.GetDirectoryName(Application.dataPath);
            PlayScenarioDefinition definition = new PlayScenarioStore(project).Get(savedName);
            definition = PlayScenarioParameters.Materialize(definition);
            PlayScenarioPlayerCapabilities.Validate(definition, MCPForUnity.Editor.Tools.Input.ManageInput.UguiBackend != null);
            string package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.version ?? "unknown";
            JObject manifest = PlayScenarioPlayerBundle.Create(definition, Application.unityVersion, package, buildSourceRevision: buildSourceRevision);
            return BuildFrozen(manifest, PlayScenarioPlayerBundle.Scenes(definition), outputDirectory, target, backend);
        }

        public static PlayScenarioPlayerBuildResult BuildFrozen(
            JObject manifest,
            string[] scenes,
            string outputDirectory,
            string target = "windows64",
            string backend = "mono"
        )
        {
            PlayScenarioPlayerPlatform.Validate(target, backend);
            BuildTarget buildTarget = Target(target);
            PreflightBackend(target, backend, buildTarget);
            bool hasUntitledScene = EnsureEditorReady();
            string project = Path.GetDirectoryName(Application.dataPath);
            string output = PlayScenarioPlayerFiles.CheckedAbsolute(outputDirectory);
            if (
                output.Equals(project, StringComparison.OrdinalIgnoreCase)
                || output.StartsWith(Application.dataPath + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || output.Equals(Application.dataPath, StringComparison.OrdinalIgnoreCase)
            )
                throw new ArgumentException("Player output must be an explicit empty directory outside Assets and the project root.");
            if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
                throw new IOException("Player output directory must be empty.");
            foreach (string path in scenes)
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(path) == null)
                    throw new ArgumentException("Scenario scene asset is unavailable: " + path);
            manifest = (JObject)manifest.DeepClone();
            manifest["build_target"] = target;
            manifest["scripting_backend"] = backend;
            manifest["executable"] = PlayScenarioPlayerPlatform.Executable(target);
            string manifestJson = manifest.ToString(Formatting.None);
            PlayScenarioPlayerBundle parsed = PlayScenarioPlayerBundle.Parse(manifestJson);
            if (!scenes.SequenceEqual(parsed.ScenePaths, StringComparer.Ordinal))
                throw new ArgumentException("Build scene inventory must match the frozen bundle.");
            string temporaryAssets = "Assets/__MCPScenarioBuild_" + Guid.NewGuid().ToString("N");
            string temporaryAbsolute = Path.Combine(project, temporaryAssets);
            Scene bootstrap = default;
            Scene previousActive = SceneManager.GetActiveScene();
#pragma warning disable CS0618
            ScriptingImplementation previousBackend = PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone);
            ScriptingImplementation selectedBackend = backend == "il2cpp" ? ScriptingImplementation.IL2CPP : ScriptingImplementation.Mono2x;
#pragma warning restore CS0618
            Directory.CreateDirectory(output);
            try
            {
#pragma warning disable CS0618
                if (previousBackend != selectedBackend)
                    PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, selectedBackend);
#pragma warning restore CS0618
                Directory.CreateDirectory(Path.Combine(temporaryAbsolute, "Resources"));
                File.WriteAllText(
                    Path.Combine(temporaryAbsolute, "link.xml"),
                    "<linker><assembly fullname=\"MCPForUnity.Runtime\" preserve=\"all\" /></linker>",
                    new System.Text.UTF8Encoding(false)
                );
                File.WriteAllText(
                    Path.Combine(temporaryAbsolute, "Resources", PlayScenarioPlayerBundle.ResourceName + ".json"),
                    manifestJson,
                    new System.Text.UTF8Encoding(false)
                );
                AssetDatabase.Refresh();
                bootstrap = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, hasUntitledScene ? NewSceneMode.Single : NewSceneMode.Additive);
                string bootstrapPath = temporaryAssets + "/Bootstrap.unity";
                if (!EditorSceneManager.SaveScene(bootstrap, bootstrapPath))
                    throw new IOException("Could not save the dedicated Player bootstrap scene.");
                EditorSceneManager.CloseScene(bootstrap, true);
                bootstrap = default;
                if (previousActive.IsValid() && previousActive.isLoaded)
                    SceneManager.SetActiveScene(previousActive);
                string executable = Path.Combine(output, PlayScenarioPlayerPlatform.Executable(target));
                BuildReport report = BuildPipeline.BuildPlayer(
                    new BuildPlayerOptions
                    {
                        scenes = new[] { bootstrapPath }.Concat(scenes).ToArray(),
                        locationPathName = Path.Combine(output, PlayScenarioPlayerPlatform.BundleLocation(target)),
                        target = buildTarget,
                        options = BuildOptions.None,
                        extraScriptingDefines = new[] { "MCP_FOR_UNITY_PLAY_SCENARIOS" },
                    }
                );
                if (report.summary.result != BuildResult.Succeeded || !File.Exists(executable))
                    throw new InvalidOperationException("Player scenario build failed: " + report.summary.result);
                PlayScenarioPlayerPayload.Attach(manifest, output);
                manifestJson = manifest.ToString(Formatting.None);
                PlayScenarioPlayerBundle.Parse(manifestJson);
                string bundlePath = Path.Combine(output, PlayScenarioPlayerBundle.ManifestName);
                PlayScenarioPlayerFiles.WriteNew(bundlePath, manifestJson, PlayScenarioPlayerBundle.Limit);
                return new PlayScenarioPlayerBuildResult
                {
                    BundlePath = bundlePath,
                    ExecutablePath = executable,
                    DefinitionHash = (string)(manifest["definition_hash"] ?? manifest["suite_hash"]),
                };
            }
            finally
            {
                try
                {
                    if (bootstrap.IsValid() && bootstrap.isLoaded)
                        EditorSceneManager.CloseScene(bootstrap, true);
                    if (previousActive.IsValid() && previousActive.isLoaded)
                        SceneManager.SetActiveScene(previousActive);
                }
                finally
                {
                    try
                    {
#pragma warning disable CS0618
                        if (PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone) == selectedBackend && previousBackend != selectedBackend)
                            PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, previousBackend);
#pragma warning restore CS0618
                    }
                    finally
                    {
                        AssetDatabase.DeleteAsset(temporaryAssets);
                        AssetDatabase.Refresh();
                    }
                }
            }
        }

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string name = Argument(args, "--mcp-scenario-name");
            string output = Argument(args, "--mcp-scenario-output");
            int revisionIndex = Array.IndexOf(args, "--mcp-scenario-source-revision");
            string revision = revisionIndex < 0 ? null : Argument(args, "--mcp-scenario-source-revision");
            PlayScenarioPlayerBuildResult result = Build(
                name,
                output,
                revision,
                OptionalArgument(args, "--mcp-scenario-target", "windows64"),
                OptionalArgument(args, "--mcp-scenario-backend", "mono")
            );
            Debug.Log("Built scenario Player bundle: " + result.BundlePath);
        }

        private static BuildTarget Target(string target)
        {
            switch (target)
            {
                case "windows64":
                    return BuildTarget.StandaloneWindows64;
                case "linux64":
                    return BuildTarget.StandaloneLinux64;
                case "macos":
                    return BuildTarget.StandaloneOSX;
                default:
                    throw new ArgumentException("Unsupported standalone target.");
            }
        }

        private static bool EnsureEditorReady()
        {
            if (
                PlayScenarioService.IsBusy
                || EditorApplication.isPlayingOrWillChangePlaymode
                || EditorApplication.isCompiling
                || EditorApplication.isUpdating
                || BuildPipeline.isBuildingPlayer
            )
                throw new InvalidOperationException("Wait for scenario execution, Play Mode, compilation, importing and builds to finish.");
            bool hasUntitledScene = false;
            bool pristineBatchStartup = Application.isBatchMode && SceneManager.sceneCount > 0;
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene scene = SceneManager.GetSceneAt(index);
                if (!scene.isLoaded)
                    continue;
                hasUntitledScene |= string.IsNullOrEmpty(scene.path);
                pristineBatchStartup &= string.IsNullOrEmpty(scene.path) && !scene.isDirty;
            }
            if (hasUntitledScene && !pristineBatchStartup)
                throw new InvalidOperationException(
                    "Save every loaded untitled scene before building a scenario Player; only an unmodified batch startup can be replaced."
                );
            return hasUntitledScene;
        }

        private static void PreflightBackend(string target, string backend, BuildTarget buildTarget)
        {
            if (!BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Standalone, buildTarget))
                throw new InvalidOperationException("Standalone build module is unavailable for " + target + ".");
            if (backend != "il2cpp")
                return;
            RuntimePlatform required =
                target == "windows64" ? RuntimePlatform.WindowsEditor
                : target == "macos" ? RuntimePlatform.OSXEditor
                : RuntimePlatform.LinuxEditor;
            if (Application.platform != required)
                throw new InvalidOperationException("IL2CPP scenario builds require a native " + target + " Editor and toolchain.");
            string module =
                target == "windows64" ? "WindowsStandaloneSupport"
                : target == "macos" ? "MacStandaloneSupport"
                : "LinuxStandaloneSupport";
            string variations = Path.Combine(EditorApplication.applicationContentsPath, "PlaybackEngines", module, "Variations");
            if (!Directory.Exists(variations) || !Directory.EnumerateDirectories(variations, "*il2cpp*", SearchOption.TopDirectoryOnly).Any())
                throw new InvalidOperationException(
                    "The IL2CPP standalone module is unavailable for " + target + "; install the matching Editor module before building."
                );
        }

        private static string OptionalArgument(string[] args, string flag, string fallback) => Array.IndexOf(args, flag) < 0 ? fallback : Argument(args, flag);

        private static string Argument(string[] args, string flag)
        {
            int index = Array.IndexOf(args, flag);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, flag) != index)
                throw new ArgumentException("Provide exactly one " + flag + ".");
            return args[index + 1];
        }
    }
}
