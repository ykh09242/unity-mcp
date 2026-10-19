using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Root-invoked native evidence builder; no test or import automatically builds a Player.</summary>
    public static class ScenarioPlayerSuiteFixtureBuild
    {
        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string output = Argument(args, "--mcp-scenario-output");
            string target = Optional(args, "--mcp-scenario-target") ?? "windows64";
            string backend = Optional(args, "--mcp-scenario-backend") ?? "mono";
            string revision = Optional(args, "--mcp-scenario-source-revision");
            ScenarioPlayerFixtureBuild.EnsureSavedFixtureBaseline(args);
            string project = Path.GetDirectoryName(Application.dataPath);
            string folder = "Assets/MCPScenarioSuiteFixtures_" + Guid.NewGuid().ToString("N");
            Scene previous = SceneManager.GetActiveScene();
            Scene owned = default;
            var children = new JArray();
            try
            {
                Directory.CreateDirectory(Path.Combine(project, folder));
                AssetDatabase.Refresh();
                foreach (string label in new[] { "first", "second", "third" })
                {
                    owned = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    SceneManager.SetActiveScene(owned);
                    var owner = new GameObject("Suite " + label);
                    owner.AddComponent<ScenarioPlayerSuiteProbe>().Label = label;
                    string scenePath = folder + "/" + label + ".unity";
                    if (!EditorSceneManager.SaveScene(owned, scenePath))
                        throw new IOException("Could not save the owned Player suite scene.");
                    EditorSceneManager.CloseScene(owned, true);
                    owned = default;
                    children.Add(
                        new JObject
                        {
                            ["definition"] = new JObject
                            {
                                ["name"] = "suite-" + label,
                                ["steps"] = new JArray(
                                    new JObject
                                    {
                                        ["name"] = "load-" + label,
                                        ["action"] = "load_scene",
                                        ["scene"] = scenePath,
                                    },
                                    new JObject
                                    {
                                        ["name"] = "cold-process-" + label,
                                        ["action"] = "wait_state",
                                        ["state_id"] = "suite-cold-process",
                                        ["state_equals"] = 1,
                                        ["timeout_seconds"] = 120,
                                    }
                                ),
                            },
                        }
                    );
                }
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                JObject manifest = PlayScenarioPlayerSuite.Create(
                    new JObject
                    {
                        ["name"] = "native-three-frozen",
                        ["children"] = children,
                        ["unity_version"] = Application.unityVersion,
                        ["package_version"] =
                            UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(PlayScenarioPlayerBuild).Assembly)?.version ?? "unknown",
                        ["build_source_revision"] = revision,
                        ["failure_policy"] = "continue",
                        ["isolation"] = "fresh-process",
                    }
                );
                PlayScenarioPlayerBuildResult result = PlayScenarioPlayerBuild.BuildFrozen(
                    manifest,
                    PlayScenarioPlayerBundle.Parse(manifest.ToString()).ScenePaths,
                    output,
                    target,
                    backend
                );
                Debug.Log("PLAYER_SUITE_BUILD_COMPLETED:" + result.BundlePath);
            }
            finally
            {
                if (owned.IsValid() && owned.isLoaded)
                    EditorSceneManager.CloseScene(owned, true);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                AssetDatabase.DeleteAsset(folder);
            }
        }

        private static string Optional(string[] args, string flag) => Array.IndexOf(args, flag) < 0 ? null : Argument(args, flag);

        private static string Argument(string[] args, string flag)
        {
            int index = Array.IndexOf(args, flag);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, flag) != index)
                throw new ArgumentException("Provide exactly one " + flag + ".");
            return args[index + 1];
        }
    }
}
