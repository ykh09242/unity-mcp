using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace UnityMcpLifecycleSample.Editor
{
    public static class LifecycleSampleSetup
    {
        public const string Root = "Assets/Generated/LifecycleSample";
        public const string Menu = Root + "/Menu.unity";
        public const string Game = Root + "/Game.unity";
        public const string Config = Root + "/SessionConfig.asset";

        [MenuItem("Lifecycle Sample/Create scenes and saved scenarios")]
        public static void Create()
        {
            string marker = Path.Combine(Path.GetDirectoryName(Application.dataPath), "ProjectSettings/LifecycleSample.json");
            if (!File.Exists(marker) || (string)JObject.Parse(File.ReadAllText(marker))["sample"] != "play-scenario-lifecycle")
                throw new InvalidOperationException("Run this builder only in its generated lifecycle sample project.");
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("Stop Play Mode before preparing the sample.");
            for (int index = 0; index < SceneManager.sceneCount; index++)
                if (SceneManager.GetSceneAt(index).isDirty)
                    throw new InvalidOperationException("Save or discard dirty sample scenes before preparing them.");
            if (!AssetDatabase.IsValidFolder("Assets/Generated"))
                AssetDatabase.CreateFolder("Assets", "Generated");
            if (!AssetDatabase.IsValidFolder(Root))
                AssetDatabase.CreateFolder("Assets/Generated", "LifecycleSample");
            var source = AssetDatabase.LoadAssetAtPath<SessionConfig>(Config);
            if (source == null)
            {
                source = ScriptableObject.CreateInstance<SessionConfig>();
                AssetDatabase.CreateAsset(source, Config);
            }
            CreateScene(Menu, LifecycleScene.SceneKind.Menu, source);
            CreateScene(Game, LifecycleScene.SceneKind.Game, source);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(Menu, true), new EditorBuildSettingsScene(Game, true) };
            foreach (
                string name in new[]
                {
                    "sample-normal",
                    "sample-failure",
                    "sample-cancel",
                    "sample-retained",
                    "sample-release",
                    "sample-teardown",
                    "sample-retained-teardown",
                }
            )
            {
                object response = ManagePlayScenario.HandleCommand(new JObject { ["action"] = "save", ["scenario"] = Definition(name) });
                if (!(response is SuccessResponse))
                    throw new InvalidOperationException("Sample scenario save failed: " + JObject.FromObject(response));
            }
            AssetDatabase.SaveAssets();
            EditorSceneManager.OpenScene(Menu, OpenSceneMode.Single);
            Debug.Log("LIFECYCLE_SAMPLE_PREPARED " + Menu);
        }

        private static void CreateScene(string path, LifecycleScene.SceneKind kind, SessionConfig source)
        {
            if (AssetDatabase.LoadAssetAtPath<SceneAsset>(path) != null)
                return;
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var controller = new GameObject("Lifecycle").AddComponent<LifecycleScene>();
            controller.Kind = kind;
            controller.Source = source;
            controller.MenuScenePath = Menu;
            controller.GameScenePath = Game;
            if (!EditorSceneManager.SaveScene(scene, path))
                throw new InvalidOperationException("Could not save sample scene: " + path);
        }

        private static JObject Definition(string name)
        {
            var steps = new JArray();
            var cleanup = new JArray();
            if (name == "sample-release")
            {
                steps.Add(ObjectStep("Release retained", "click_ui", "Lifecycle/Canvas/ReleaseRetained"));
                steps.Add(Ready("Menu ready", 250));
            }
            else
            {
                steps.Add(
                    ObjectStep(
                        "Start session",
                        "click_ui",
                        "Lifecycle/Canvas/" + (name.StartsWith("sample-retained", StringComparison.Ordinal) ? "StartRetained" : "Start")
                    )
                );
                steps.Add(
                    new JObject
                    {
                        ["name"] = "Game loaded",
                        ["action"] = "wait_scene",
                        ["scene"] = Game,
                    }
                );
                steps.Add(Ready("Game ready", name == "sample-cancel" ? 10000 : 500));
                if (name == "sample-failure")
                {
                    steps.Add(ObjectStep("Remove Player deliberately", "click_ui", "Lifecycle/Canvas/RemovePlayer"));
                    JObject missing = ObjectStep("Player must exist", "wait_object", "Player");
                    missing["timeout_seconds"] = 1;
                    steps.Add(missing);
                }
                if (name.EndsWith("-teardown", StringComparison.Ordinal))
                    cleanup.Add(
                        new JObject
                        {
                            ["name"] = "Unload game directly",
                            ["action"] = "load_scene",
                            ["scene"] = Menu,
                        }
                    );
                else
                    cleanup.Add(ObjectStep("Return to menu", "click_ui", "Lifecycle/Canvas/Return"));
                cleanup.Add(
                    new JObject
                    {
                        ["name"] = "Menu restored",
                        ["action"] = "wait_scene",
                        ["scene"] = Menu,
                        ["stable_for_ms"] = 300,
                    }
                );
                cleanup.Add(Ready("Menu ready", 250));
            }
            return new JObject
            {
                ["name"] = name,
                ["tags"] = new JArray("lifecycle-sample"),
                ["poll_interval_ms"] = 100,
                ["setup_steps"] = new JArray(
                    new JObject
                    {
                        ["name"] = "Load menu",
                        ["action"] = "load_scene",
                        ["scene"] = Menu,
                    }
                ),
                ["steps"] = steps,
                ["cleanup_steps"] = cleanup,
                ["resources"] = new JObject { ["enabled"] = true },
                ["diagnostics"] = new JObject { ["record_timeline"] = true },
                ["cleanup_timeout_seconds"] = 10,
            };
        }

        private static JObject ObjectStep(string name, string action, string target) =>
            new JObject
            {
                ["name"] = name,
                ["action"] = action,
                ["target"] = target,
                ["timeout_seconds"] = 5,
            };

        private static JObject Ready(string name, int stable)
        {
            JObject step = ObjectStep(name, "wait_object", "Lifecycle");
            step["component"] = typeof(LifecycleScene).FullName;
            step["property"] = new JObject { ["path"] = "Ready", ["equals"] = true };
            step["stable_for_ms"] = stable;
            step["timeout_seconds"] = stable >= 5000 ? 15 : 5;
            return step;
        }
    }
}
