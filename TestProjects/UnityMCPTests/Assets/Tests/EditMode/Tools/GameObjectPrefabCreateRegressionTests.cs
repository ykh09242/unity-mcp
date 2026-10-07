using System;
using System.Linq;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class GameObjectPrefabCreateRegressionTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene ownedScene;
        private Object[] originalSelection;
        private Object originalActiveObject;
        private string assetRoot;
        private string assetRootGuid;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            ownedScene = default;
            assetRoot = null;
            assetRootGuid = null;
            var anchor = sceneFixture.Create("McpPrefabCreateAnchor_", Guid.NewGuid().ToString("N"));
            assetRoot = "Assets/PrefabCreateRegression_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            assetRootGuid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            Assert.IsNotEmpty(assetRootGuid);
            // A saved owned anchor allows the instance scene to close and reopen without touching user scenes.
            Assert.IsTrue(EditorSceneManager.SaveScene(anchor, assetRoot + "/" + anchor.name + ".unity"));
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(ownedScene);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                {
                    foreach (var root in ownedScene.GetRootGameObjects())
                    {
                        foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                        {
                            Undo.ClearUndo(transform.gameObject);
                            Undo.ClearUndo(transform);
                        }
                        Object.DestroyImmediate(root);
                    }
                    Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true));
                }
                sceneFixture.Close();
                if (!string.IsNullOrEmpty(assetRootGuid))
                {
                    StringAssert.StartsWith("Assets/PrefabCreateRegression_", assetRoot);
                    Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                }
            }
            finally
            {
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveObject;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreatePrefab_SetActive_OverridesAssetWithoutChangingSource(bool active)
        {
            var asset = CreatePrefab(!active);
            var request = Request(asset);
            request["setActive"] = active;
            var response = Call(request);
            var instance = ResponseObject(response);

            Assert.AreEqual(active, instance.activeSelf);
            Assert.AreEqual(active, response["data"].Value<bool>("activeSelf"));
            Assert.AreEqual(!active, asset.activeSelf);
            Assert.AreEqual(asset, PrefabUtility.GetCorrespondingObjectFromSource(instance));
            Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(instance));
            Assert.IsTrue(PrefabUtility.GetPropertyModifications(instance).Any(modification => modification.propertyPath == "m_IsActive"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreatePrefab_OmittedSetActive_PreservesAssetState(bool active)
        {
            var asset = CreatePrefab(active);
            var instance = ResponseObject(Call(Request(asset)));
            Assert.AreEqual(active, instance.activeSelf);
        }

        [TestCase(null)]
        [TestCase("Cube")]
        public void CreateNewObject_AppliesSetActive(string primitive)
        {
            var request = Request();
            request["primitiveType"] = primitive;
            request["setActive"] = false;
            var response = Call(request);
            var instance = ResponseObject(response);
            Assert.IsFalse(instance.activeSelf);
            Assert.IsFalse(response["data"].Value<bool>("activeSelf"));
            if (primitive != null)
                Assert.IsNotNull(instance.GetComponent<BoxCollider>());
        }

        [Test]
        public void CreatePrefab_InactiveInstance_StillAppliesOtherCreationProperties()
        {
            var asset = CreatePrefab(true);
            var parent = new GameObject("PrefabCreateParent");
            var request = Request(asset);
            request["parent"] = parent.GetInstanceIDCompat();
            request["setActive"] = false;
            request["position"] = new JArray(1, 2, 3);
            request["rotation"] = new JArray(0, 90, 0);
            request["scale"] = new JArray(2, 3, 4);
            request["tag"] = "Player";
            request["layer"] = "Ignore Raycast";
            request["componentsToAdd"] = new JArray("Rigidbody");
            request["componentProperties"] = new JObject { ["Rigidbody"] = new JObject { ["mass"] = 7f } };
            var instance = ResponseObject(Call(request));

            Assert.AreEqual("PrefabCreateCopy", instance.name);
            Assert.IsFalse(instance.activeSelf);
            Assert.AreEqual(parent.transform, instance.transform.parent);
            Assert.AreEqual(new Vector3(1, 2, 3), instance.transform.localPosition);
            Assert.Less(Quaternion.Angle(Quaternion.Euler(0, 90, 0), instance.transform.localRotation), 0.01f);
            Assert.AreEqual(new Vector3(2, 3, 4), instance.transform.localScale);
            Assert.AreEqual("Player", instance.tag);
            Assert.AreEqual(LayerMask.NameToLayer("Ignore Raycast"), instance.layer);
            Assert.AreEqual(7f, instance.GetComponent<Rigidbody>().mass);
            Assert.AreEqual(asset, PrefabUtility.GetCorrespondingObjectFromSource(instance));
        }

        [Test]
        public void CreatePrefab_UndoRedo_PreservesRequestedStateAndPrefabLink()
        {
            var asset = CreatePrefab(true);
            Undo.IncrementCurrentGroup();
            int group = Undo.GetCurrentGroup();
            var request = Request(asset);
            request["setActive"] = false;
            var response = Call(request);
            var instance = ResponseObject(response);
            Undo.FlushUndoRecordObjects();
            Undo.CollapseUndoOperations(group);
            Undo.PerformUndo();
            Assert.IsTrue(instance == null, "Undo must remove the created prefab instance.");
            Undo.PerformRedo();
            instance = ResponseObject(response);
            Assert.IsFalse(instance.activeSelf);
            Assert.AreEqual(asset, PrefabUtility.GetCorrespondingObjectFromSource(instance));
        }

        [Test]
        public void CreatePrefab_SceneReload_PreservesInactiveOverrideAndPrefabLink()
        {
            var asset = CreatePrefab(true);
            var request = Request(asset);
            request["setActive"] = false;
            Call(request);
            string scenePath = assetRoot + "/Instance.unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, scenePath));
            Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true));
            ownedScene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Additive);
            var instance = ownedScene.GetRootGameObjects().Single();
            Assert.IsFalse(instance.activeSelf);
            Assert.AreEqual(asset, PrefabUtility.GetCorrespondingObjectFromSource(instance));
        }

        [Test]
        public void CreateAndSavePrefab_StoresRequestedActiveStateInAsset()
        {
            var request = Request();
            string path = assetRoot + "/Saved.prefab";
            request["saveAsPrefab"] = true;
            request["prefabPath"] = path;
            request["setActive"] = false;
            var instance = ResponseObject(Call(request));
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.IsNotNull(asset);
            Assert.IsFalse(asset.activeSelf);
            Assert.AreEqual(asset, PrefabUtility.GetCorrespondingObjectFromSource(instance));
        }

        private GameObject CreatePrefab(bool active)
        {
            var source = new GameObject("PrefabCreateSource");
            source.SetActive(active);
            try
            {
                return PrefabUtility.SaveAsPrefabAsset(source, assetRoot + "/Source.prefab");
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
        }

        private static JObject Request(GameObject asset = null) =>
            new JObject
            {
                ["action"] = "create",
                ["name"] = "PrefabCreateCopy",
                ["prefabPath"] = asset == null ? null : AssetDatabase.GetAssetPath(asset),
            };

        private static JObject Call(JObject request)
        {
            var response = JObject.FromObject(ManageGameObject.HandleCommand(request));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return response;
        }

        private GameObject ResponseObject(JObject response) =>
            ownedScene
                .GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<Transform>(true))
                .Select(transform => transform.gameObject)
                .Single(go => go.GetInstanceIDCompat() == response["data"].Value<long>("instanceID"));
    }
}
