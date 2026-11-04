using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class ComponentOwningSceneTests
    {
        [TestCase("add")]
        [TestCase("remove")]
        [TestCase("set_property")]
        public void ExplicitTargetInOtherScene_DoesNotDirtyCleanPrefabStage(string action)
        {
            NativeAuthoringPersistenceTests.RequireOwnedEditor(Application.isBatchMode, Environment.GetEnvironmentVariable("UNITY_MCP_OWNED_TRANSPORT_TESTS"));
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "Run in an owned Editor without an existing Prefab Stage.");
            string folder = "Assets/ComponentOwningScene_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            SceneManager.SetActiveScene(scene);
            try
            {
                var target = new GameObject("OtherSceneTarget");
                target.AddComponent<AudioSource>();
                var source = new GameObject("CleanStage");
                string prefabPath = folder + "/Stage.prefab";
                PrefabUtility.SaveAsPrefabAsset(source, prefabPath);
                UnityEngine.Object.DestroyImmediate(source);
                EditorSceneManager.SaveScene(scene, folder + "/Other.unity");
                var stage = PrefabStageUtility.OpenPrefab(prefabPath);
                Assert.IsFalse(stage.scene.isDirty);
                Assert.IsFalse(scene.isDirty);
                JObject response = JObject.FromObject(
                    ManageComponents.HandleCommand(
                        new JObject
                        {
                            ["action"] = action,
                            ["target"] = target.GetInstanceIDCompat().ToString(),
                            ["search_method"] = "by_id",
                            ["component_type"] = "AudioSource",
                            ["properties"] = new JObject { ["volume"] = 0.25f },
                        }
                    )
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.IsTrue(scene.isDirty, "The actual target scene must be marked dirty.");
                Assert.IsFalse(stage.scene.isDirty, "A clean unrelated Prefab Stage must stay clean.");
            }
            finally
            {
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                if (stage != null && stage.assetPath.StartsWith(folder + "/", StringComparison.Ordinal))
                    stage.ClearDirtiness();
                StageUtility.GoToMainStage();
                Undo.ClearAll();
                EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                AssetDatabase.DeleteAsset(folder);
            }
        }
    }
}
