using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class ManageComponentsPrefabPersistenceTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene instanceScene;
        private string assetRoot;
        private string assetRootGuid;
        private UnityEngine.Object[] previousSelection;
        private UnityEngine.Object previousActiveObject;
        private bool capturedState;
        private GameObject instance;

        [OneTimeSetUp]
        public void PrepareRunner() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunner() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            capturedState = false;
            assetRootGuid = null;
            instanceScene = default;
            instance = null;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("This fixture preserves an existing Prefab Stage.");
            previousSelection = Selection.objects;
            previousActiveObject = Selection.activeObject;
            capturedState = true;
            string suffix = Guid.NewGuid().ToString("N");
            Scene sourceScene = sceneFixture.Create("McpComponentPrefab_", suffix);
            assetRoot = "Assets/__McpComponentPrefab_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, assetRoot.Substring(7))));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring(7));
            Assert.IsNotEmpty(assetRootGuid);
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(assetRootGuid));

            var source = new GameObject("ComponentPrefabInstance");
            Assert.AreEqual(sourceScene, source.scene);
            source.AddComponent<BoxCollider>();
            source.AddComponent<BoxCollider>();
            GameObject prefab;
            try
            {
                prefab = PrefabUtility.SaveAsPrefabAsset(source, assetRoot + "/Component.prefab");
                Assert.IsNotNull(prefab);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
            // Keep the fixture scene loaded while the separate owned instance scene is reopened.
            instanceScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab, instanceScene);
            Assert.IsNotNull(instance);
            Assert.IsTrue(EditorSceneManager.SaveScene(instanceScene, assetRoot + "/Instance.unity"));
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState)
                return;
            try
            {
                ClearInstanceUndo();
                if (instanceScene.IsValid() && instanceScene.isLoaded)
                    Assert.IsTrue(EditorSceneManager.CloseScene(instanceScene, true));
                sceneFixture.Close();
                if (!string.IsNullOrEmpty(assetRootGuid))
                {
                    StringAssert.StartsWith("Assets/__McpComponentPrefab_", assetRoot);
                    Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                }
            }
            finally
            {
                Selection.objects = previousSelection;
                Selection.activeObject = previousActiveObject;
            }
        }

        [TestCase("ordinary", false)]
        [TestCase("ordinary", true)]
        [TestCase("selected_index", false)]
        [TestCase("selected_index", true)]
        [TestCase("selected_id", false)]
        [TestCase("selected_id", true)]
        public void DirectWrites_SurviveSceneSaveAndReopen(string route, bool partialFailure)
        {
            Apply(route, partialFailure);
            AssertValues(route, true);
            Assert.IsTrue(instanceScene.isDirty);
            Assert.IsTrue(EditorSceneManager.SaveScene(instanceScene));
            string scenePath = instanceScene.path;
            ClearInstanceUndo();
            Assert.IsTrue(EditorSceneManager.CloseScene(instanceScene, true));
            instance = null;
            instanceScene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Additive);
            instance = instanceScene.GetRootGameObjects().Single();

            Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(instance));
            AssertValues(route, true);
            GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(assetRoot + "/Component.prefab");
            Assert.AreEqual(Vector3.one, prefab.transform.localScale);
            Assert.IsTrue(prefab.GetComponents<BoxCollider>().All(component => component.size == Vector3.one));
        }

        [TestCase("ordinary", false)]
        [TestCase("ordinary", true)]
        [TestCase("selected_index", false)]
        [TestCase("selected_index", true)]
        [TestCase("selected_id", false)]
        [TestCase("selected_id", true)]
        public void DirectWrites_PreserveUndoAndRedo(string route, bool partialFailure)
        {
            Undo.IncrementCurrentGroup();
            Apply(route, partialFailure);
            Undo.FlushUndoRecordObjects();
            Undo.IncrementCurrentGroup();
            AssertValues(route, true);

            Undo.PerformUndo();
            AssertValues(route, false);
            Undo.PerformRedo();
            AssertValues(route, true);
        }

        private void Apply(string route, bool partialFailure)
        {
            bool ordinary = route == "ordinary";
            string property = ordinary ? "localScale" : "size";
            var request = new JObject
            {
                ["action"] = "set_property",
                ["target"] = instance.GetInstanceIDCompat(),
                ["search_method"] = "by_id",
                ["component_type"] = ordinary ? "UnityEngine.Transform" : "UnityEngine.Collider",
                ["properties"] = new JObject { [property] = new JArray(2f, 3f, 4f) },
            };
            if (!ordinary)
            {
                request["strict_selection"] = true;
                if (route == "selected_index")
                    request["component_index"] = 1;
                else
                    request["component_instance_id"] = instance.GetComponents<BoxCollider>()[1].GetInstanceIDCompat();
            }
            if (partialFailure)
                request["properties"]["NoSuchProperty_PrefabPersistence"] = 1;

            JObject response = JObject.FromObject(ManageComponents.HandleCommand(request));

            Assert.AreEqual(!partialFailure, response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("applied", response.SelectToken("data.results[0].status").Value<string>());
            if (partialFailure)
                Assert.AreEqual("failed", response.SelectToken("data.results[1].status").Value<string>());
        }

        private void AssertValues(string route, bool applied)
        {
            Vector3 expected = applied ? new Vector3(2f, 3f, 4f) : Vector3.one;
            Assert.AreEqual(route == "ordinary" ? expected : Vector3.one, instance.transform.localScale);
            BoxCollider[] colliders = instance.GetComponents<BoxCollider>();
            Assert.AreEqual(2, colliders.Length);
            Assert.AreEqual(Vector3.one, colliders[0].size, "Only the selected actual component may change.");
            Assert.AreEqual(route == "ordinary" ? Vector3.one : expected, colliders[1].size);
        }

        private void ClearInstanceUndo()
        {
            if (instance == null)
                return;
            foreach (Component component in instance.GetComponents<Component>())
                Undo.ClearUndo(component);
            Undo.ClearUndo(instance);
        }
    }
}
