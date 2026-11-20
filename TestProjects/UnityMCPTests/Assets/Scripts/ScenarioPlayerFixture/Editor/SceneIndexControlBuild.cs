using System;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class SceneIndexControlBuild
    {
        private const string Folder = "Assets/SceneIndexControl";
        private const string Source = "Assets/Scripts/ScenarioPlayerFixture/SceneIndexProbe.cs";
        private const string Settings = "ProjectSettings/EditorBuildSettings.asset";

        private static string Hash(string path)
        {
            using var hash = SHA256.Create();
            return BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(path))).Replace("-", "").ToLowerInvariant();
        }

        public static void Run()
        {
            string[] args = Environment.GetCommandLineArgs();
            string Arg(string key) => args[Array.IndexOf(args, key) + 1];
            string owner = Arg("--scene-owner");
            if (!Application.isBatchMode || !Guid.TryParseExact(owner, "N", out _) || File.ReadAllText(".scene-index-owner") != owner)
                throw new InvalidOperationException("An owned disposable project is required");
            string phase = Arg("--scene-phase");
            string output = Arg("--scene-output");
            string[] paths = { Folder + "/Start.unity", Folder + "/World.unity", Folder + "/Menu.unity" };
            if (phase == "before")
            {
                if (AssetDatabase.IsValidFolder(Folder) || string.IsNullOrEmpty(AssetDatabase.CreateFolder("Assets", "SceneIndexControl")))
                    throw new IOException("Scene index fixture folder already exists or could not be created");
                foreach (string path in paths)
                {
                    var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    if (path == paths[0])
                        new GameObject("Scene index loader").AddComponent<SceneIndexProbe>();
                    if (!EditorSceneManager.SaveScene(scene, path))
                        throw new IOException("Failed to save " + path);
                }
            }
            else if (phase == "after" || phase == "repaired")
                Array.Reverse(paths, 1, 2);
            else
                throw new ArgumentException("Unknown scene control phase");
            EditorBuildSettings.scenes = paths.Select(p => new EditorBuildSettingsScene(p, true)).ToArray();
            string[] enabled = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();
            string sourceHash = Hash(Source);
            string settingsHash = Hash(Settings);
            Directory.CreateDirectory(Path.Combine(output, "build"));
            var report = BuildPipeline.BuildPlayer(
                new BuildPlayerOptions
                {
                    scenes = enabled,
                    locationPathName = Path.Combine(output, "build", "SceneControl.exe"),
                    target = BuildTarget.StandaloneWindows64,
                    options = BuildOptions.None,
                }
            );
            if (report.summary.result != BuildResult.Succeeded || Hash(Source) != sourceHash || Hash(Settings) != settingsHash)
                throw new InvalidOperationException("Scene build failed or its source inputs changed");
            var receipt = new JObject
            {
                ["source"] = Source,
                ["source_sha256"] = sourceHash,
                ["settings_sha256"] = settingsHash,
                ["build_guid"] = report.summary.guid.ToString(),
                ["scenes"] = new JArray(enabled),
            };
            using var stream = new FileStream(Path.Combine(output, "build.json"), FileMode.CreateNew, FileAccess.Write);
            using var writer = new StreamWriter(stream);
            writer.WriteLine(receipt);
        }
    }
}
