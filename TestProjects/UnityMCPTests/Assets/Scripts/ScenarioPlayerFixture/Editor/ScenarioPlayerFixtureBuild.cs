using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Services.PlayScenarios;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.PlayScenarios.Player
{
    /// <summary>Generates only dedicated synthetic assets in the prepared test project.</summary>
    public static class ScenarioPlayerFixtureBuild
    {
        private static void EnsureSavedFixtureBaseline(string[] args)
        {
            bool untitled = false;
            bool pristine = true;
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene loaded = SceneManager.GetSceneAt(index);
                if (!loaded.isLoaded || !string.IsNullOrEmpty(loaded.path))
                    continue;
                untitled = true;
                pristine &= loaded.rootCount == 0 && !loaded.isDirty;
            }
            if (!untitled)
                return;
            bool prepare = Array.IndexOf(args, "--mcp-fixture-prepare-baseline") >= 0;
            if (!Application.isBatchMode || (!pristine && !prepare))
                throw new InvalidOperationException(
                    "Fixture builds require saved scenes. Only an owned isolated batch test project may opt in with --mcp-fixture-prepare-baseline."
                );
            const string folder = "Assets/MCPScenarioPlayerFixtures";
            Directory.CreateDirectory(Path.Combine(Path.GetDirectoryName(Application.dataPath), folder));
            AssetDatabase.Refresh();
            if (prepare)
            {
                Scene baseline = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                string path = folder + "/BuildBaseline_" + Guid.NewGuid().ToString("N") + ".unity";
                if (!EditorSceneManager.SaveScene(baseline, path))
                    throw new IOException("Could not save the dedicated empty fixture baseline.");
                return;
            }
            for (int index = 0; index < SceneManager.sceneCount; index++)
            {
                Scene loaded = SceneManager.GetSceneAt(index);
                if (!loaded.isLoaded || !string.IsNullOrEmpty(loaded.path))
                    continue;
                string path = folder + "/BuildBaseline_" + Guid.NewGuid().ToString("N") + ".unity";
                if (!EditorSceneManager.SaveScene(loaded, path))
                    throw new IOException("Could not save the pristine empty fixture baseline.");
            }
        }

        public static void BuildOrdinaryFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            int index = Array.IndexOf(args, "--mcp-scenario-output");
            if (index < 0 || index + 1 >= args.Length)
                throw new ArgumentException("--mcp-scenario-output is required.");
            string output = MCPForUnity.Runtime.PlayScenarios.PlayScenarioPlayerFiles.CheckedAbsolute(args[index + 1]);
            if (Directory.Exists(output) && Directory.EnumerateFileSystemEntries(output).Any())
                throw new IOException("Ordinary fixture output must be empty.");
            EnsureSavedFixtureBaseline(args);
            Directory.CreateDirectory(output);
            const string folder = "Assets/MCPScenarioPlayerFixtures";
            Directory.CreateDirectory(Path.Combine(Path.GetDirectoryName(Application.dataPath), folder));
            AssetDatabase.Refresh();
            Scene previous = SceneManager.GetActiveScene();
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            try
            {
                var probe = new GameObject("Ordinary Player Probe");
                SceneManager.MoveGameObjectToScene(probe, scene);
                probe.AddComponent<ScenarioOrdinaryPlayerProbe>();
                const string path = folder + "/Ordinary.unity";
                if (!EditorSceneManager.SaveScene(scene, path))
                    throw new IOException("Could not save ordinary fixture scene.");
                EditorSceneManager.CloseScene(scene, true);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                BuildReport report = BuildPipeline.BuildPlayer(
                    new BuildPlayerOptions
                    {
                        scenes = new[] { path },
                        locationPathName = Path.Combine(output, "MCPOrdinaryPlayer.exe"),
                        target = BuildTarget.StandaloneWindows64,
                        options = BuildOptions.None,
                    }
                );
                if (report.summary.result != BuildResult.Succeeded)
                    throw new InvalidOperationException("Ordinary fixture build failed: " + report.summary.result);
                Debug.Log("ORDINARY_PLAYER_FIXTURE_BUILD_COMPLETED:" + output);
            }
            finally
            {
                if (scene.IsValid() && scene.isLoaded)
                    EditorSceneManager.CloseScene(scene, true);
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
            }
        }

        public static void BuildFromCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            int outputIndex = Array.IndexOf(args, "--mcp-scenario-output");
            if (outputIndex < 0 || outputIndex + 1 >= args.Length)
                throw new ArgumentException("--mcp-scenario-output is required.");
            int clickIndex = Array.IndexOf(args, "--mcp-fixture-click-mode");
            string clickMode = clickIndex >= 0 && clickIndex + 1 < args.Length ? args[clickIndex + 1] : "direct";
            if (clickMode != "direct" && clickMode != "raycast")
                throw new ArgumentException("Fixture click mode must be direct or raycast.");
            EnsureSavedFixtureBaseline(args);
            const string folder = "Assets/MCPScenarioPlayerFixtures";
            const string menu = folder + "/Menu.unity";
            const string game = folder + "/Game.unity";
            Directory.CreateDirectory(Path.Combine(Path.GetDirectoryName(Application.dataPath), folder));
            AssetDatabase.Refresh();
            Scene previous = SceneManager.GetActiveScene();
            try
            {
                foreach (bool isMenu in new[] { true, false })
                {
                    Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    try
                    {
                        var owner = new GameObject("FixtureBootstrap");
                        SceneManager.MoveGameObjectToScene(owner, scene);
                        ScenarioPlayerFixture component = owner.AddComponent<ScenarioPlayerFixture>();
                        component.IsMenu = isMenu;
                        component.GameScene = game;
                        if (!EditorSceneManager.SaveScene(scene, isMenu ? menu : game))
                            throw new IOException("Could not save fixture scene.");
                    }
                    finally
                    {
                        EditorSceneManager.CloseScene(scene, true);
                    }
                }
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
                var definition = PlayScenarioDefinition.Parse(
                    new JObject
                    {
                        ["name"] = "player-native-flow",
                        ["poll_interval_ms"] = 100,
                        ["completion_stable_ms"] = 100,
                        ["diagnostics"] = new JObject { ["record_timeline"] = true },
                        ["query_budget"] = new JObject
                        {
                            ["enabled"] = true,
                            ["max_target_searches"] = 1000,
                            ["max_hierarchy_visits"] = 10000,
                        },
                        ["setup_steps"] = new JArray(
                            new JObject
                            {
                                ["name"] = "menu",
                                ["action"] = "load_scene",
                                ["scene"] = menu,
                            },
                            new JObject
                            {
                                ["name"] = "isolate",
                                ["action"] = "reset_state",
                                ["reset_ids"] = new JArray("fixture-state"),
                            }
                        ),
                        ["steps"] = new JArray(
                            new JObject
                            {
                                ["name"] = "start",
                                ["action"] = "click_ui",
                                ["target_id"] = "fixture-start",
                                ["click_mode"] = clickMode,
                                ["timeout_seconds"] = 3,
                            },
                            new JObject
                            {
                                ["name"] = "game",
                                ["action"] = "wait_scene",
                                ["scene"] = game,
                            },
                            new JObject
                            {
                                ["name"] = "ready",
                                ["action"] = "wait_state",
                                ["state_id"] = "fixture-ready",
                                ["state_equals"] = true,
                                ["timeout_seconds"] = 3,
                            },
                            new JObject
                            {
                                ["name"] = "player",
                                ["action"] = "wait_object",
                                ["target_id"] = "fixture-player",
                                ["timeout_seconds"] = 3,
                            }
                        ),
                        ["cleanup_steps"] = new JArray(
                            new JObject
                            {
                                ["name"] = "restore-menu",
                                ["action"] = "load_scene",
                                ["scene"] = menu,
                            }
                        ),
                    }
                );
                new PlayScenarioStore(Path.GetDirectoryName(Application.dataPath)).Save(definition);
                if (Array.IndexOf(args, "--mcp-fixture-pristine-build-startup") >= 0)
                {
                    if (!Application.isBatchMode)
                        throw new InvalidOperationException("Pristine build startup is restricted to the owned isolated batch fixture.");
                    Scene pristine = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    if (!string.IsNullOrEmpty(pristine.path) || pristine.isDirty || pristine.rootCount != 0)
                        throw new InvalidOperationException("The synthetic pristine batch startup was not established.");
                    Debug.Log("PLAYER_FIXTURE_PRISTINE_BUILD_STARTUP_ESTABLISHED");
                }
                int revisionIndex = Array.IndexOf(args, "--mcp-scenario-source-revision");
                if (revisionIndex >= 0 && revisionIndex + 1 >= args.Length)
                    throw new ArgumentException("--mcp-scenario-source-revision requires a label.");
                string revision = revisionIndex >= 0 ? args[revisionIndex + 1] : null;
                PlayScenarioPlayerBuildResult result = PlayScenarioPlayerBuild.Build(definition.Name, args[outputIndex + 1], revision);
                Debug.Log("PLAYER_FIXTURE_BUILD_COMPLETED:" + result.BundlePath);
            }
            finally
            {
                if (previous.IsValid() && previous.isLoaded)
                    SceneManager.SetActiveScene(previous);
            }
        }
    }
}
