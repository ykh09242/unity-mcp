using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class SceneLifecycleIntegrityTests
    {
        private Scene originalActive;
        private Scene first;
        private Scene second;
        private List<Scene> ownedScenes;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveObject;
        private string assetRoot;
        private string sceneName;
        private string firstPath;
        private string secondPath;
        private bool ownsAssetRoot;
        private bool capturedState;

        [SetUp]
        public void SetUp()
        {
            capturedState = false;
            ownsAssetRoot = false;
            ownedScenes = new List<Scene>();
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("This fixture does not change an existing Prefab Stage or its lookup context.");
            originalActive = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            capturedState = true;
            assetRoot = "Assets/__McpSceneLifecycleIntegrity_" + Guid.NewGuid().ToString("N");
            sceneName = "SceneIntegrity_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsFalse(Directory.Exists(SystemPath(assetRoot)));
            string guid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            ownsAssetRoot = !string.IsNullOrEmpty(guid);
            Assert.IsTrue(ownsAssetRoot);
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(guid));
            Assert.IsTrue(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "First"));
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Second"));
            firstPath = assetRoot + "/First/" + sceneName + ".unity";
            secondPath = assetRoot + "/Second/" + sceneName + ".unity";
            first = NewOwnedScene();
            Assert.IsTrue(EditorSceneManager.SaveScene(first, firstPath));
            second = NewOwnedScene();
            Assert.IsTrue(EditorSceneManager.SaveScene(second, secondPath));
            Assert.AreEqual(first.name, second.name);
            Assert.AreNotEqual(first.path, second.path);
            Assert.Less(SceneIndex(first), SceneIndex(second));
            Assert.IsTrue(SceneManager.SetActiveScene(first));
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState) return;
            try
            {
                if (originalActive.IsValid() && originalActive.isLoaded)
                    SceneManager.SetActiveScene(originalActive);
                foreach (Scene scene in ownedScenes.AsEnumerable().Reverse())
                {
                    if (!scene.IsValid()) continue;
                    foreach (GameObject root in scene.isLoaded ? scene.GetRootGameObjects() : Array.Empty<GameObject>())
                    {
                        Undo.ClearUndo(root);
                        Undo.ClearUndo(root.transform);
                        UnityEngine.Object.DestroyImmediate(root);
                    }
                    Assert.IsTrue(EditorSceneManager.CloseScene(scene, true), "Only a captured owned additive scene is closed.");
                }
                if (ownsAssetRoot)
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpSceneLifecycleIntegrity_", StringComparison.Ordinal));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Only the exact successfully created fixture folder is deleted.");
                }
            }
            finally
            {
                if (originalActive.IsValid() && originalActive.isLoaded)
                    SceneManager.SetActiveScene(originalActive);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveObject;
            }
        }

        private Scene NewOwnedScene()
        {
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            ownedScenes.Add(scene);
            Assert.IsTrue(scene.IsValid() && scene.isLoaded);
            return scene;
        }

        private static string SystemPath(string assetPath) => Path.Combine(Application.dataPath, assetPath.Substring("Assets/".Length));
        private static JObject Call(JObject request) => JObject.FromObject(ManageScene.HandleCommand(request));
        private static void Success(JObject response) => Assert.IsTrue(response.Value<bool>("success"), response.ToString());
        private static int[] Handles() => Enumerable.Range(0, SceneManager.sceneCount).Select(i => SceneManager.GetSceneAt(i).handle).ToArray();
        private static int SceneIndex(Scene scene) => Array.IndexOf(Handles(), scene.handle);
        private JObject Select(string action) => new JObject { ["action"] = action, ["sceneName"] = sceneName, ["scenePath"] = secondPath };

        [Test]
        public void CombinedSelector_ActivatesExactLaterPath()
        {
            Success(Call(Select("set_active_scene")));
            Assert.AreEqual(second, SceneManager.GetActiveScene());
            Assert.IsTrue(first.isLoaded);
        }

        [Test]
        public void CombinedSelector_ClosesExactLaterPath()
        {
            JObject request = Select("close_scene");
            request["removeScene"] = true;
            Success(Call(request));
            Assert.IsTrue(first.IsValid() && first.isLoaded);
            Assert.IsFalse(second.IsValid() && second.isLoaded);
        }

        [Test]
        public void CombinedSelector_MovesOwnedRootToExactLaterPath()
        {
            var go = new GameObject("Owned_" + Guid.NewGuid().ToString("N"));
            Assert.AreEqual(first, go.scene);
            JObject request = Select("move_to_scene");
            request["target"] = go.GetInstanceID();
            Success(Call(request));
            Assert.AreEqual(second, go.scene);
            Assert.IsTrue(first.isLoaded);
        }

        [TestCase("set_active_scene")]
        [TestCase("close_scene")]
        [TestCase("move_to_scene")]
        public void MissingExplicitPath_DoesNotFallBackToMatchingName(string action)
        {
            var go = new GameObject("Owned_" + Guid.NewGuid().ToString("N"));
            Assert.AreEqual(first, go.scene);
            int[] before = Handles();
            JObject request = Select(action);
            request["scenePath"] = assetRoot + "/Missing.unity";
            request["target"] = go.GetInstanceID();
            Assert.IsFalse(Call(request).Value<bool>("success"));
            CollectionAssert.AreEqual(before, Handles());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
            Assert.AreEqual(first, go.scene);
            Assert.IsTrue(first.isLoaded && second.isLoaded);
        }

        [TestCase("omitted")]
        [TestCase("null")]
        [TestCase("empty")]
        public void NameOnlyAndEmptyPath_KeepFirstMatchingScene(string pathKind)
        {
            Assert.IsTrue(SceneManager.SetActiveScene(second));
            JObject request = new JObject { ["action"] = "set_active_scene", ["sceneName"] = sceneName };
            if (pathKind != "omitted") request["scenePath"] = pathKind == "null" ? JValue.CreateNull() : new JValue("");
            Success(Call(request));
            Assert.AreEqual(first, SceneManager.GetActiveScene());
        }

        [TestCase("missing_name")]
        [TestCase("unknown_template")]
        [TestCase("collision")]
        public void RejectedCreate_PerformsNoDirectoryOrScenePreparation(string kind)
        {
            string rejectedDir = assetRoot + "/Rejected";
            int[] before = Handles();
            JObject request = new JObject { ["action"] = "create", ["path"] = rejectedDir };
            if (kind == "unknown_template") { request["name"] = "New"; request["template"] = "unknown"; }
            if (kind == "collision") { request["name"] = sceneName; request["path"] = firstPath; }
            string guid = AssetDatabase.AssetPathToGUID(firstPath);
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Assert.IsFalse(Directory.Exists(SystemPath(rejectedDir)));
            CollectionAssert.AreEqual(before, Handles());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(firstPath));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FullFileSavePath_RoutesToRequestedDestination(bool backslashes)
        {
            string destination = assetRoot + "/Saved/Requested.unity";
            string originalGuid = AssetDatabase.AssetPathToGUID(firstPath);
            JObject response = Call(new JObject { ["action"] = "save", ["path"] = backslashes ? destination.Replace('/', '\\') : destination });
            Success(response);
            Assert.AreEqual(destination, first.path);
            Assert.AreEqual(destination, response["data"].Value<string>("path"));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(destination));
            Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(firstPath));
            Assert.AreEqual(first, SceneManager.GetActiveScene());
        }

        [Test]
        public void ExplicitName_KeepsPrecedenceOverPathFilename()
        {
            string destination = assetRoot + "/Named.unity";
            Success(Call(new JObject { ["action"] = "save", ["name"] = "Named", ["path"] = assetRoot + "/Other.unity" }));
            Assert.AreEqual(destination, first.path);
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(destination));
            Assert.IsFalse(File.Exists(SystemPath(assetRoot + "/Other.unity")));
        }

        [Test]
        public void UntitledOwnedAdditiveScene_SaveAsUsesFullPath()
        {
            Scene untitled = NewOwnedScene();
            Assert.IsEmpty(untitled.path);
            Assert.IsTrue(SceneManager.SetActiveScene(untitled));
            string destination = assetRoot + "/UntitledSaved.unity";
            Success(Call(new JObject { ["action"] = "save", ["path"] = destination }));
            Assert.AreEqual(destination, untitled.path);
            Assert.IsTrue(first.isLoaded && second.isLoaded);
        }

        [Test]
        public void SaveWithoutPath_KeepsCurrentFile()
        {
            string guid = AssetDatabase.AssetPathToGUID(firstPath);
            Success(Call(new JObject { ["action"] = "save" }));
            Assert.AreEqual(firstPath, first.path);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(firstPath));
        }

        [Test]
        public void AdditivePathLoad_PreservesAllExistingScenes()
        {
            Assert.IsTrue(EditorSceneManager.CloseScene(second, true));
            int[] before = Handles();
            JObject response = Call(new JObject { ["action"] = "load", ["path"] = secondPath, ["additive"] = true });
            Scene loaded = SceneManager.GetSceneByPath(secondPath);
            if (loaded.IsValid() && loaded.isLoaded) ownedScenes.Add(loaded);
            Success(response);
            Assert.IsTrue(loaded.IsValid() && loaded.isLoaded);
            CollectionAssert.IsSubsetOf(before, Handles());
            Assert.AreEqual(before.Length + 1, SceneManager.sceneCount);
            Assert.IsTrue(first.isLoaded);
        }

        [TestCase("load_additive")]
        [TestCase("close_dirty")]
        public void ExistingLoadAndDirtyCloseGuards_PreserveOwnedScenes(string kind)
        {
            int[] before = Handles();
            JObject request;
            if (kind == "load_additive") request = new JObject { ["action"] = "load", ["path"] = secondPath, ["additive"] = true };
            else
            {
                Assert.IsTrue(EditorSceneManager.MarkSceneDirty(second));
                request = Select("close_scene");
                request["removeScene"] = true;
            }
            Assert.IsFalse(Call(request).Value<bool>("success"));
            CollectionAssert.AreEqual(before, Handles());
            Assert.IsTrue(first.isLoaded && second.isLoaded);
        }

        [TestCase("negative")]
        [TestCase("null")]
        [TestCase("true")]
        [TestCase("false")]
        public void InvalidOrUnparsedBuildIndex_PerformsNoLoad(string kind)
        {
            JToken index = kind == "negative" ? new JValue(-1) : kind == "null" ? JValue.CreateNull() : new JValue(kind == "true");
            int[] before = Handles();
            Assert.IsFalse(Call(new JObject { ["action"] = "load", ["buildIndex"] = index, ["additive"] = true }).Value<bool>("success"));
            CollectionAssert.AreEqual(before, Handles());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
        }
    }
}
