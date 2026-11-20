using System;
using System.IO;
using MCPForUnity.Editor.Services.PlayScenarios;
using MCPForUnity.Runtime.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace MCPForUnityTests.PlayScenarios.Player
{
    public static class SaveUpgradePlayerBuild
    {
        private const string Bootstrap = "Assets/SaveUpgradeBootstrap.unity";
        private const string Fixture = "Assets/SaveUpgradeFixture.unity";
        private const string Scenario = "verified-save-upgrade";

        public static void PrepareFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string project = RequireOwnedProject(args);
            if (
                !Enum.TryParse(Argument(args, "--mcp-save-upgrade-variant"), out SaveUpgradePlayerProbe.Variant variant)
                || !Enum.IsDefined(typeof(SaveUpgradePlayerProbe.Variant), variant)
            )
                throw new ArgumentException("Unknown save upgrade fixture variant.");
            string fixturePath = Path.Combine(project, Fixture);
            if (!File.Exists(fixturePath) && variant != SaveUpgradePlayerProbe.Variant.Previous)
                throw new IOException("Prepare the previous fixture before its candidate variants.");
            if (!File.Exists(fixturePath))
            {
                var bootstrap = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                if (File.Exists(Path.Combine(project, Bootstrap)) || !EditorSceneManager.SaveScene(bootstrap, Bootstrap))
                    throw new IOException("Could not create a new save upgrade bootstrap scene.");
                var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                new GameObject("Owned Save Upgrade Fixture").AddComponent<SaveUpgradePlayerProbe>().Version = variant;
                if (!EditorSceneManager.SaveScene(scene, Fixture))
                    throw new IOException("Could not create a new save upgrade fixture scene.");
                return;
            }
            var fixture = EditorSceneManager.OpenScene(Fixture, OpenSceneMode.Single);
            GameObject[] roots = fixture.GetRootGameObjects();
            if (roots.Length != 1 || !roots[0].TryGetComponent(out SaveUpgradePlayerProbe probe))
                throw new IOException("The save upgrade fixture scene is already occupied.");
            probe.Version = variant;
            EditorSceneManager.MarkSceneDirty(fixture);
            if (!EditorSceneManager.SaveScene(fixture))
                throw new IOException("Could not save the fixture variant.");
        }

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            string project = RequireOwnedProject(args);
            string output = PlayScenarioPlayerFiles.CheckedAbsolute(Argument(args, "--mcp-scenario-output"));
            string revision = Argument(args, "--mcp-scenario-source-revision");
            if (Directory.Exists(output) || File.Exists(output))
                throw new IOException("The save upgrade fixture requires a new build output directory.");
            EditorSceneManager.OpenScene(Bootstrap, OpenSceneMode.Single);
            var definition = PlayScenarioDefinition.Parse(
                new JObject
                {
                    ["name"] = Scenario,
                    ["steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "Load save fixture",
                            ["action"] = "load_scene",
                            ["scene"] = Fixture,
                        },
                        new JObject
                        {
                            ["name"] = "Save checkpoint",
                            ["action"] = "wait_state",
                            ["state_id"] = SaveUpgradePlayerProbe.StateId,
                            ["state_equals"] = true,
                            ["timeout_seconds"] = 3,
                        }
                    ),
                    ["setup_steps"] = new JArray(
                        new JObject
                        {
                            ["name"] = "Load empty bootstrap",
                            ["action"] = "load_scene",
                            ["scene"] = Bootstrap,
                        }
                    ),
                }
            );
            new PlayScenarioStore(project).Save(definition);
            var build = PlayScenarioPlayerBuild.Build(Scenario, output, revision, backend: "mono");
            Debug.Log("SAVE_UPGRADE_BUILD_COMPLETED:" + build.BundlePath);
        }

        private static string RequireOwnedProject(string[] args)
        {
            string project = Path.GetDirectoryName(Application.dataPath);
            const string fixtureSource = "Assets/Scripts/ScenarioPlayerFixture/SaveUpgradePlayerProbe.cs";
            if (!Application.isBatchMode || Array.IndexOf(args, "--mcp-save-upgrade-owned-project") < 0 || !File.Exists(Path.Combine(project, fixtureSource)))
                throw new InvalidOperationException("Use an owned batch test project for the save upgrade fixture.");
            return project;
        }

        private static string Argument(string[] args, string name)
        {
            int index = Array.IndexOf(args, name);
            if (index < 0 || index + 1 >= args.Length || Array.LastIndexOf(args, name) != index)
                throw new ArgumentException("Expected one value for " + name);
            return args[index + 1];
        }
    }
}
