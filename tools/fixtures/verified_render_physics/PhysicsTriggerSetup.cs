using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Physics;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace VerifiedWorkflows
{
    public static class PhysicsTriggerSetup
    {
        private const string ScenePath = "Assets/TriggerMatrixFixture.unity";
        private const string Scenario = "fixture-trigger-matrix";
        private const string Trigger = "FixtureTrigger";
        private const string Mover = "FixtureMover";

        public static void Configure()
        {
            string output = FixtureRun.OpenOutput("physics");
            string mode = FixtureRun.Argument("--fixture-matrix");
            bool collide = mode switch
            {
                "blocked" => false,
                "repaired" => true,
                _ => throw new ArgumentException("Choose blocked or repaired for --fixture-matrix."),
            };

            if (AssetDatabase.LoadAssetAtPath<SceneAsset>(ScenePath) == null)
            {
                SetLayerNames();
                var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                var trigger = new GameObject(Trigger);
                trigger.layer = PhysicsTriggerProbe.TriggerLayer;
                trigger.AddComponent<BoxCollider>().isTrigger = true;
                var mover = new GameObject(Mover);
                mover.layer = PhysicsTriggerProbe.MoverLayer;
                mover.transform.position = new Vector3(-3, 0, 0);
                mover.AddComponent<BoxCollider>();
                var body = mover.AddComponent<Rigidbody>();
                body.isKinematic = true;
                body.useGravity = false;
                trigger.AddComponent<PhysicsTriggerProbe>().Mover = body;
                if (!EditorSceneManager.SaveScene(scene, ScenePath))
                    throw new InvalidOperationException("Could not save the physics fixture.");
                EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };
                SaveScenario();
            }

            EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            PhysicsCommand(
                new JObject
                {
                    ["action"] = "set_collision_matrix",
                    ["layer_a"] = PhysicsTriggerProbe.TriggerLayer,
                    ["layer_b"] = PhysicsTriggerProbe.MoverLayer,
                    ["collide"] = collide,
                }
            );
            AssetDatabase.SaveAssets();
            var diagnostics = PhysicsCommand(
                new JObject
                {
                    ["action"] = "diagnose_pair",
                    ["target"] = Trigger,
                    ["search_method"] = "by_name",
                    ["other_target"] = Mover,
                    ["other_search_method"] = "by_name",
                }
            );
            if (
                diagnostics.SelectToken("data.configuration.layer_ignored")?.Value<bool>() != !collide
                || diagnostics.SelectToken("data.configuration.interaction")?.Value<string>() != "trigger"
            )
                throw new InvalidOperationException("Unexpected physics diagnostic: " + diagnostics);
            File.WriteAllText(
                Path.Combine(output, "editor-pair.json"),
                new JObject
                {
                    ["unity_version"] = Application.unityVersion,
                    ["scene"] = ScenePath,
                    ["matrix"] = mode,
                    ["expected_trigger_enter_count"] = 1,
                    ["evidence"] = "editor_configuration",
                    ["diagnostics"] = diagnostics,
                }.ToString()
            );
        }

        private static void SetLayerNames()
        {
            var settings = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/TagManager.asset");
            using var tags = new SerializedObject(settings[0]);
            var layers = tags.FindProperty("layers");
            layers.GetArrayElementAtIndex(PhysicsTriggerProbe.TriggerLayer).stringValue = PhysicsTriggerProbe.TriggerLayerName;
            layers.GetArrayElementAtIndex(PhysicsTriggerProbe.MoverLayer).stringValue = PhysicsTriggerProbe.MoverLayerName;
            tags.ApplyModifiedPropertiesWithoutUndo();
        }

        private static JObject PhysicsCommand(JObject parameters)
        {
            var result = JObject.FromObject(ManagePhysics.HandleCommand(parameters));
            if (!result.Value<bool>("success"))
                throw new InvalidOperationException("Physics fixture command failed: " + result);
            return result;
        }

        private static void SaveScenario()
        {
            var matrix = EqualityStep("Read whether the layer pair is ignored", PhysicsTriggerProbe.MatrixId, 0);
            matrix.Remove("state_equals");
            matrix["state_compare"] = new JObject
            {
                ["operator"] = "range",
                ["min"] = 0,
                ["max"] = 1,
            };
            var result = JObject.FromObject(
                ManagePlayScenario.HandleCommand(
                    new JObject
                    {
                        ["action"] = "save",
                        ["scenario"] = new JObject
                        {
                            ["name"] = Scenario,
                            ["steps"] = new JArray(
                                new JObject
                                {
                                    ["name"] = "Load controlled pair",
                                    ["action"] = "load_scene",
                                    ["scene"] = ScenePath,
                                },
                                EqualityStep("Check controlled inputs", PhysicsTriggerProbe.SettingsId, PhysicsTriggerProbe.ExpectedSettings),
                                matrix,
                                EqualityStep("Require one trigger entry", PhysicsTriggerProbe.CountId, 1)
                            ),
                        },
                    }
                )
            );
            if (!result.Value<bool>("success"))
                throw new InvalidOperationException("Could not save the physics scenario: " + result);
        }

        private static JObject EqualityStep(string name, string id, JToken expected) =>
            new JObject
            {
                ["name"] = name,
                ["action"] = "wait_state",
                ["state_id"] = id,
                ["state_equals"] = expected,
                ["timeout_seconds"] = 2,
            };
    }
}
