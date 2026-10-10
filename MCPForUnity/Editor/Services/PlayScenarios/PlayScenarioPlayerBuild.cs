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

    /// <summary>Explicit Windows x64 Mono scenario builds; never changes global scripting defines.</summary>
    public static class PlayScenarioPlayerBuild
    {
        public static PlayScenarioPlayerBuildResult Build(string savedName, string outputDirectory)
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
            if (!BuildPipeline.IsBuildTargetSupported(BuildTargetGroup.Standalone, BuildTarget.StandaloneWindows64))
                throw new InvalidOperationException("Windows x64 standalone support is unavailable.");
#pragma warning disable CS0618
            if (PlayerSettings.GetScriptingBackend(BuildTargetGroup.Standalone) != ScriptingImplementation.Mono2x)
                throw new InvalidOperationException("Player scenarios currently require the project's Standalone scripting backend to be Mono.");
#pragma warning restore CS0618
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
            PlayScenarioDefinition definition = new PlayScenarioStore(project).Get(savedName);
            definition = PlayScenarioDefinition.Parse(JObject.FromObject(definition));
            PlayScenarioPlayerCapabilities.Validate(definition, MCPForUnity.Editor.Tools.Input.ManageInput.UguiBackend != null);
            string[] scenes = PlayScenarioPlayerBundle.Scenes(definition);
            foreach (string path in scenes)
                if (AssetDatabase.LoadAssetAtPath<SceneAsset>(path) == null)
                    throw new ArgumentException("Scenario scene asset is unavailable: " + path);
            string package = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.version ?? "unknown";
            JObject manifest = PlayScenarioPlayerBundle.Create(definition, Application.unityVersion, package);
            string manifestJson = manifest.ToString(Formatting.None);
            PlayScenarioPlayerBundle.Parse(manifestJson);
            string temporaryAssets = "Assets/__MCPScenarioBuild_" + Guid.NewGuid().ToString("N");
            string temporaryAbsolute = Path.Combine(project, temporaryAssets);
            Scene bootstrap = default;
            Scene previousActive = SceneManager.GetActiveScene();
            Directory.CreateDirectory(output);
            try
            {
                Directory.CreateDirectory(Path.Combine(temporaryAbsolute, "Resources"));
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
                string executable = Path.Combine(output, PlayScenarioPlayerBundle.ExecutableName);
                BuildReport report = BuildPipeline.BuildPlayer(
                    new BuildPlayerOptions
                    {
                        scenes = new[] { bootstrapPath }.Concat(scenes).ToArray(),
                        locationPathName = executable,
                        target = BuildTarget.StandaloneWindows64,
                        options = BuildOptions.None,
                        extraScriptingDefines = new[] { "MCP_FOR_UNITY_PLAY_SCENARIOS" },
                    }
                );
                if (report.summary.result != BuildResult.Succeeded || !File.Exists(executable))
                    throw new InvalidOperationException("Player scenario build failed: " + report.summary.result);
                string bundlePath = Path.Combine(output, PlayScenarioPlayerBundle.ManifestName);
                PlayScenarioPlayerFiles.WriteNew(bundlePath, manifestJson, PlayScenarioPlayerBundle.Limit);
                return new PlayScenarioPlayerBuildResult
                {
                    BundlePath = bundlePath,
                    ExecutablePath = executable,
                    DefinitionHash = (string)manifest["definition_hash"],
                };
            }
            finally
            {
                if (bootstrap.IsValid() && bootstrap.isLoaded)
                    EditorSceneManager.CloseScene(bootstrap, true);
                if (previousActive.IsValid() && previousActive.isLoaded)
                    SceneManager.SetActiveScene(previousActive);
                AssetDatabase.DeleteAsset(temporaryAssets);
                AssetDatabase.Refresh();
            }
        }

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string name = Argument(args, "--mcp-scenario-name");
            string output = Argument(args, "--mcp-scenario-output");
            PlayScenarioPlayerBuildResult result = Build(name, output);
            Debug.Log("Built scenario Player bundle: " + result.BundlePath);
        }

        private static string Argument(string[] args, string flag)
        {
            int index = Array.IndexOf(args, flag);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, flag) != index)
                throw new ArgumentException("Provide exactly one " + flag + ".");
            return args[index + 1];
        }
    }
}
