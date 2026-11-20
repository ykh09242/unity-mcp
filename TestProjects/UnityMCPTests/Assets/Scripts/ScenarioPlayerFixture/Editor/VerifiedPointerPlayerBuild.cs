using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class VerifiedPointerPlayerBuild
    {
        private const string FixtureScene = "Assets/VerifiedPointerFixture.unity";
        private const string Scenario = "verified-pointer-player";

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string project = Path.GetDirectoryName(Application.dataPath);
            string ownership = Path.Combine(project, "Assets", "Tests", "Raycast", "VerifiedPointerOverlayTests.cs");
            if (!Application.isBatchMode || !File.Exists(ownership))
                throw new InvalidOperationException("Build the pointer fixture only in its prepared batch test project.");
            string output = PlayScenarioPlayerFiles.CheckedAbsolute(Argument(args, "--mcp-pointer-output"));
            string revision = Argument(args, "--mcp-pointer-revision");
            if (Directory.Exists(output) || File.Exists(output))
                throw new IOException("The pointer fixture requires a new build output path.");
            if (File.Exists(Path.Combine(project, FixtureScene)))
                throw new IOException("The pointer fixture scene path is already occupied.");

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            new GameObject("VerifiedPointerProbe").AddComponent<VerifiedPointerPlayerProbe>();
            if (!EditorSceneManager.SaveScene(scene, FixtureScene))
                throw new IOException("Could not save the owned pointer scene.");
            var definition = PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = Scenario,
                    ["poll_interval_ms"] = 100,
                    ["diagnostics"] = new JObject { ["record_timeline"] = true },
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "Load pointer fixture",
                            ["action"] = "load_scene",
                            ["scene"] = FixtureScene,
                        },
                        State("Initially closed", false),
                        new JObject
                        {
                            ["name"] = "Click at button center",
                            ["action"] = "click_ui",
                            ["click_mode"] = "raycast",
                            ["target_id"] = VerifiedPointerPlayerProbe.TargetId,
                            ["timeout_seconds"] = 2,
                        },
                        State("Panel opens", true)
                    ),
                }
            );
            new PlayScenarioStore(project).Save(definition);
            var build = PlayScenarioPlayerBuild.Build(Scenario, output, revision, backend: "mono");
            Debug.Log("VERIFIED_POINTER_BUILD_COMPLETED:" + build.BundlePath);
        }

        private static JObject State(string name, bool opened) =>
            new JObject
            {
                ["name"] = name,
                ["action"] = "wait_state",
                ["state_id"] = VerifiedPointerPlayerProbe.StateId,
                ["state_equals"] = opened,
            };

        private static string Argument(string[] args, string name)
        {
            int index = Array.IndexOf(args, name);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, name) != index)
                throw new ArgumentException("Expected one value for " + name);
            return args[index + 1];
        }
    }
}
