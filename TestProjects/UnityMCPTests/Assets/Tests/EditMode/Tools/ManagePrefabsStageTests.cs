using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using static MCPForUnityTests.Editor.TestUtilities;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManagePrefabsStageTests
    {
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private string tempDirectory;
        private string tempDirectoryGuid;
        private Object[] originalSelection;
        private Object originalActiveObject;
        private Scene originalActiveScene;
        private bool capturedState;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            capturedState = false;
            tempDirectory = null;
            tempDirectoryGuid = null;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("This fixture preserves the existing Prefab Stage, including unsaved changes.");
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            originalActiveScene = SceneManager.GetActiveScene();
            capturedState = true;
            Scene ownedScene = sceneFixture.Create("McpPrefabStage_", Guid.NewGuid().ToString("N"));
            tempDirectory = "Assets/ManagePrefabsStageTests_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(tempDirectory));
            Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath, tempDirectory.Substring("Assets/".Length))));
            tempDirectoryGuid = AssetDatabase.CreateFolder("Assets", tempDirectory.Substring("Assets/".Length));
            Assert.IsNotEmpty(tempDirectoryGuid);
            string scenePath = tempDirectory + "/" + ownedScene.name + ".unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, scenePath));
            Assert.AreEqual(scenePath, ownedScene.path);
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState)
                return;
            try
            {
                CloseOwnedPrefabStage();
                sceneFixture.Close();
                if (!string.IsNullOrEmpty(tempDirectoryGuid))
                {
                    StringAssert.StartsWith("Assets/ManagePrefabsStageTests_", tempDirectory);
                    Assert.AreEqual(tempDirectoryGuid, AssetDatabase.AssetPathToGUID(tempDirectory));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(tempDirectory));
                }
            }
            finally
            {
                if (originalActiveScene.IsValid() && originalActiveScene.isLoaded)
                    SceneManager.SetActiveScene(originalActiveScene);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveObject;
            }
        }

        private void CloseOwnedPrefabStage()
        {
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage == null)
                return;
            Assert.IsNotEmpty(tempDirectoryGuid);
            Assert.AreEqual(tempDirectoryGuid, AssetDatabase.AssetPathToGUID(tempDirectory));
            StringAssert.StartsWith(tempDirectory + "/", stage.assetPath, "A foreign Prefab Stage must never be closed by this fixture.");
            if (stage.scene.isDirty)
            {
                var saved = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "save_prefab_stage" }));
                Assert.IsTrue(saved.Value<bool>("success"), saved.ToString());
            }
            StageUtility.GoToMainStage();
        }

        [Test]
        public void OpenPrefabStage_RequiresPrefabPath()
        {
            var result = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "open_prefab_stage" }));

            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("prefabPath", result.Value<string>("error"));
        }

        [Test]
        public void OpenPrefabStage_RejectsNonPrefabPath()
        {
            var result = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "open_prefab_stage", ["prefabPath"] = "Assets/Temp/NotPrefab.txt" }));

            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains(".prefab", result.Value<string>("error"));
        }

        [Test]
        public void OpenPrefabStage_OpensPrefabStageAndReturnsStageData()
        {
            string prefabPath = CreateTestPrefab("OpenStageRoot");

            try
            {
                var result = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "open_prefab_stage", ["prefabPath"] = prefabPath }));

                Assert.IsTrue(result.Value<bool>("success"));
                Assert.AreEqual(prefabPath, result["data"].Value<string>("prefabPath"));
                Assert.AreEqual(prefabPath, result["data"].Value<string>("openedPrefabPath"));
                Assert.AreEqual("OpenStageRoot", result["data"].Value<string>("rootName"));

                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                Assert.IsNotNull(stage);
                Assert.AreEqual(prefabPath, stage.assetPath);

                var closeResult = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "close_prefab_stage" }));
                Assert.IsTrue(closeResult.Value<bool>("success"));
                Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
            }
            finally
            {
                CloseOwnedPrefabStage();
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void OpenPrefabStage_AcceptsPathAlias()
        {
            string prefabPath = CreateTestPrefab("AliasRoot");

            try
            {
                var result = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "open_prefab_stage", ["path"] = prefabPath }));

                Assert.IsTrue(result.Value<bool>("success"));
                Assert.AreEqual(prefabPath, result["data"].Value<string>("openedPrefabPath"));
                Assert.IsNotNull(PrefabStageUtility.GetCurrentPrefabStage());
            }
            finally
            {
                CloseOwnedPrefabStage();
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void OpenPrefabStage_PrefabPathTakesPrecedenceOverPath()
        {
            string prefabPath = CreateTestPrefab("PrefabPathRoot");
            string aliasPath = CreateTestPrefab("AliasPathRoot");

            try
            {
                var result = ToJObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "open_prefab_stage",
                            ["prefabPath"] = prefabPath,
                            ["path"] = aliasPath,
                        }
                    )
                );

                Assert.IsTrue(result.Value<bool>("success"));

                var currentStage = PrefabStageUtility.GetCurrentPrefabStage();
                Assert.IsNotNull(currentStage, "Expected a prefab stage to be open.");
                Assert.AreEqual(prefabPath, currentStage.assetPath, "prefabPath should take precedence over path when both are provided.");
            }
            finally
            {
                CloseOwnedPrefabStage();
                SafeDeleteAsset(prefabPath);
                SafeDeleteAsset(aliasPath);
            }
        }

        [Test]
        public void SavePrefabStage_PersistsPrefabContentChanges()
        {
            string prefabPath = CreateTestPrefab("SaveStageRoot");

            try
            {
                AssertOpenPrefabStage(prefabPath);

                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                Assert.IsNotNull(stage, "Expected prefab stage to be open.");

                var child = new GameObject("SavedChild");
                child.transform.SetParent(stage.prefabContentsRoot.transform, false);

                var saveResult = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "save_prefab_stage" }));

                Assert.IsTrue(saveResult.Value<bool>("success"), $"Expected save to succeed but got: {saveResult}");
                Assert.AreEqual(prefabPath, saveResult["data"].Value<string>("prefabPath"));

                CloseOwnedPrefabStage();

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(reloaded.transform.Find("SavedChild"), "Saved prefab should contain the new child after save_prefab_stage.");
            }
            finally
            {
                CloseOwnedPrefabStage();
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void ClosePrefabStage_SaveBeforeClose_PersistsChanges()
        {
            string prefabPath = CreateTestPrefab("CloseSaveRoot");

            try
            {
                AssertOpenPrefabStage(prefabPath);

                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                Assert.IsNotNull(stage, "Expected prefab stage to be open.");

                var child = new GameObject("CloseSavedChild");
                child.transform.SetParent(stage.prefabContentsRoot.transform, false);

                var closeResult = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "close_prefab_stage", ["saveBeforeClose"] = true }));

                Assert.IsTrue(closeResult.Value<bool>("success"), $"Expected close with save to succeed but got: {closeResult}");
                Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage(), "Prefab stage should be closed after close_prefab_stage.");

                GameObject reloaded = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                Assert.IsNotNull(
                    reloaded.transform.Find("CloseSavedChild"),
                    "Saved prefab should contain the new child after close_prefab_stage(saveBeforeClose: true)."
                );
            }
            finally
            {
                CloseOwnedPrefabStage();
                SafeDeleteAsset(prefabPath);
            }
        }

        [Test]
        public void FixtureCleanup_PreservesForeignFolderSceneAndSelection()
        {
            string sentinelPath = CreateTestPrefab("ForeignSentinel");
            string sentinelGuid = AssetDatabase.AssetPathToGUID(sentinelPath);
            byte[] sentinelBytes = File.ReadAllBytes(sentinelPath);
            var sentinel = AssetDatabase.LoadAssetAtPath<GameObject>(sentinelPath);
            Selection.activeObject = sentinel;
            Object[] selected = Selection.objects;
            Scene active = SceneManager.GetActiveScene();
            Scene[] scenes = Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).ToArray();
            var nested = new ManagePrefabsStageTests();
            try
            {
                nested.SetUp();
                Assert.AreNotEqual(tempDirectory, nested.tempDirectory);
                Assert.IsTrue(AssetDatabase.IsValidFolder(nested.tempDirectory));
            }
            finally
            {
                nested.TearDown();
            }
            Assert.IsFalse(AssetDatabase.IsValidFolder(nested.tempDirectory));
            Assert.AreEqual(sentinelGuid, AssetDatabase.AssetPathToGUID(sentinelPath));
            CollectionAssert.AreEqual(sentinelBytes, File.ReadAllBytes(sentinelPath));
            Assert.AreEqual("ForeignSentinel", AssetDatabase.LoadAssetAtPath<GameObject>(sentinelPath).name);
            CollectionAssert.AreEqual(scenes, Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).ToArray());
            Assert.AreEqual(active, SceneManager.GetActiveScene());
            CollectionAssert.AreEqual(selected, Selection.objects);
            Assert.AreEqual(sentinel, Selection.activeObject);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FixtureGuard_PreservesExistingPrefabStageAndUnsavedChanges(bool dirty)
        {
            string prefabPath = CreateTestPrefab("ForeignStage");
            byte[] sourceBytes = File.ReadAllBytes(prefabPath);
            AssertOpenPrefabStage(prefabPath);
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            Assert.IsNotNull(stage);
            if (dirty)
            {
                var child = new GameObject("UnsavedForeignChild");
                child.transform.SetParent(stage.prefabContentsRoot.transform, false);
                child.transform.localPosition = new Vector3(2, 3, 4);
                EditorSceneManager.MarkSceneDirty(stage.scene);
            }
            bool wasDirty = stage.scene.isDirty;
            Assert.AreEqual(dirty, wasDirty);
            string rootName = stage.prefabContentsRoot.name;
            Selection.activeObject = stage.prefabContentsRoot;
            Object[] selected = Selection.objects;
            Object activeObject = Selection.activeObject;
            Scene activeScene = SceneManager.GetActiveScene();
            Scene[] scenes = Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).ToArray();
            var nested = new ManagePrefabsStageTests();
            try
            {
                Assert.Throws<IgnoreException>(() => nested.SetUp(), "An existing foreign stage must be guarded before any fixture mutation.");
            }
            finally
            {
                nested.TearDown();
            }
            Assert.IsNull(nested.tempDirectory);
            Assert.AreSame(stage, PrefabStageUtility.GetCurrentPrefabStage());
            Assert.AreEqual(wasDirty, stage.scene.isDirty);
            Assert.AreEqual(rootName, stage.prefabContentsRoot.name);
            Transform unsavedChild = stage.prefabContentsRoot.transform.Find("UnsavedForeignChild");
            Assert.AreEqual(dirty, unsavedChild != null);
            if (dirty)
                Assert.AreEqual(new Vector3(2, 3, 4), unsavedChild.localPosition);
            CollectionAssert.AreEqual(sourceBytes, File.ReadAllBytes(prefabPath));
            CollectionAssert.AreEqual(selected, Selection.objects);
            Assert.AreEqual(activeObject, Selection.activeObject);
            Assert.AreEqual(activeScene, SceneManager.GetActiveScene());
            CollectionAssert.AreEqual(scenes, Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).ToArray());
        }

        private static void AssertOpenPrefabStage(string prefabPath)
        {
            var openResult = ToJObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "open_prefab_stage", ["prefabPath"] = prefabPath }));

            Assert.IsTrue(openResult.Value<bool>("success"), $"Expected open to succeed but got: {openResult}");
        }

        [Test]
        public void ModifyInactiveExactId_UsesPrefabStageAndRejectsSceneObject()
        {
            var outside = new GameObject("OutsidePrefabStage");
            outside.SetActive(false);
            int outsideId = outside.GetInstanceIDCompat();
            string prefabPath = CreateTestPrefab("InactiveStageRoot");
            AssertOpenPrefabStage(prefabPath);
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            var child = new GameObject("InactiveChild");
            child.transform.SetParent(stage.prefabContentsRoot.transform, false);
            child.SetActive(false);
            var insideResponse = JObject.FromObject(
                ManageGameObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = child.GetInstanceIDCompat(),
                        ["newName"] = "RenamedChild",
                    }
                )
            );
            Assert.IsTrue(insideResponse.Value<bool>("success"), insideResponse.ToString());
            Assert.AreEqual("RenamedChild", child.name);
            Assert.IsFalse(child.activeSelf);
            var outsideResponse = JObject.FromObject(
                ManageGameObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = outsideId,
                        ["newName"] = "Forbidden",
                    }
                )
            );
            Assert.IsFalse(outsideResponse.Value<bool>("success"), outsideResponse.ToString());
            Assert.AreEqual("OutsidePrefabStage", outside.name);
        }

        private string CreateTestPrefab(string rootName)
        {
            string prefabPath = Path.Combine(tempDirectory, $"{rootName}.prefab").Replace('\\', '/');
            var root = new GameObject(rootName);

            try
            {
                PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
            }
            finally
            {
                Object.DestroyImmediate(root, true);
            }

            return prefabPath;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ModifyContents_PreservesDirtyStageAndOnlyRejectsSameAsset(bool sameAsset)
        {
            string stagePath = CreateTestPrefab("DirtyStage");
            string targetPath = sameAsset ? stagePath : CreateTestPrefab("OtherAsset");
            AssertOpenPrefabStage(stagePath);
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            var autoSave = typeof(PrefabStage).GetProperty(
                "autoSave",
                System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic
            );
            Assert.IsNotNull(autoSave, "The native regression requires a controllable Auto Save setting.");
            bool wasAutoSave = (bool)autoSave.GetValue(stage);
            try
            {
                autoSave.SetValue(stage, false);
                var unsaved = new GameObject("UnsavedChild");
                unsaved.transform.SetParent(stage.prefabContentsRoot.transform, false);
                EditorSceneManager.MarkSceneDirty(stage.scene);
                Selection.activeGameObject = unsaved;
                byte[] stageBytes = File.ReadAllBytes(stagePath);
                byte[] targetBytes = File.ReadAllBytes(targetPath);
                string stageGuid = AssetDatabase.AssetPathToGUID(stagePath);
                string targetGuid = AssetDatabase.AssetPathToGUID(targetPath);
                var selected = Selection.objects;
                var response = JObject.FromObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "modify_contents",
                            ["prefabPath"] = targetPath,
                            ["position"] = new JArray(2, 3, 4),
                        }
                    )
                );
                Assert.AreEqual(!sameAsset, response.Value<bool>("success"), response.ToString());
                Assert.AreSame(stage, PrefabStageUtility.GetCurrentPrefabStage());
                Assert.IsTrue(stage.scene.isDirty);
                Assert.IsFalse((bool)autoSave.GetValue(stage));
                Assert.IsNotNull(stage.prefabContentsRoot.transform.Find("UnsavedChild"));
                CollectionAssert.AreEqual(selected, Selection.objects);
                CollectionAssert.AreEqual(stageBytes, File.ReadAllBytes(stagePath));
                if (sameAsset)
                    CollectionAssert.AreEqual(targetBytes, File.ReadAllBytes(targetPath));
                Assert.AreEqual(stageGuid, AssetDatabase.AssetPathToGUID(stagePath));
                Assert.AreEqual(targetGuid, AssetDatabase.AssetPathToGUID(targetPath));
                var saved = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "save_prefab_stage" }));
                Assert.IsTrue(saved.Value<bool>("success"), saved.ToString());
                Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(stagePath).transform.Find("UnsavedChild"));
                Assert.AreEqual(Vector3.zero, AssetDatabase.LoadAssetAtPath<GameObject>(stagePath).transform.localPosition);
                if (!sameAsset)
                    Assert.AreEqual(new Vector3(2, 3, 4), AssetDatabase.LoadAssetAtPath<GameObject>(targetPath).transform.localPosition);
            }
            finally
            {
                autoSave.SetValue(stage, wasAutoSave);
            }
        }
    }
}
