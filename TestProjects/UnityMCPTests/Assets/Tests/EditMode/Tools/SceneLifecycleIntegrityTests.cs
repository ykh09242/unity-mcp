using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using MCPForUnity.Runtime.Helpers;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class SceneLifecycleIntegrityTests
    {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.I1)]
        private static extern bool CreateSymbolicLinkW(string link, string target, int flags);
        [DllImport("libc", SetLastError = true)]
        private static extern int symlink(string target, string link);
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
        private static Scene[] LoadedScenes() => Enumerable.Range(0, SceneManager.sceneCount).Select(SceneManager.GetSceneAt).ToArray();
        private static int SceneIndex(Scene scene) => Array.IndexOf(LoadedScenes(), scene);
        private JObject Select(string action) => new JObject { ["action"] = action, ["sceneName"] = sceneName, ["scenePath"] = secondPath };

        [Test]
        public void SaveFailurePreservesPreexistingOutputDirectoryAndContent()
        {
            string directory = assetRoot + "/Existing";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Existing"));
            string directoryGuid = AssetDatabase.AssetPathToGUID(directory);
            string occupiedPath = directory + "/Occupied.unity";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(directory, "Occupied.unity"));
            string occupiedGuid = AssetDatabase.AssetPathToGUID(occupiedPath);
            string sentinel = SystemPath(occupiedPath + "/Retained.txt");
            File.WriteAllText(sentinel, "retain existing output");
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            JObject response;
            try
            {
                // Unity emits a native save diagnostic when a directory occupies the destination file.
                LogAssert.ignoreFailingMessages = true;
                response = Call(new JObject { ["action"] = "save", ["path"] = occupiedPath });
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
            }

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(directoryGuid, AssetDatabase.AssetPathToGUID(directory));
            Assert.AreEqual(occupiedGuid, AssetDatabase.AssetPathToGUID(occupiedPath));
            Assert.AreEqual("retain existing output", File.ReadAllText(sentinel));
        }

        [TestCase("create", "rooted")]
        [TestCase("create", "traversal")]
        [TestCase("create", "invalid_leaf")]
        [TestCase("create", "invalid_ancestor")]
        [TestCase("save", "rooted")]
        [TestCase("save", "traversal")]
        [TestCase("save", "invalid_leaf")]
        [TestCase("save", "invalid_ancestor")]
        [TestCase("load", "rooted")]
        [TestCase("load", "traversal")]
        [TestCase("load", "invalid_leaf")]
        [TestCase("load", "invalid_ancestor")]
        public void HostileScenePath_IsRejectedBeforeSceneOrFilesystemChanges(string action, string kind)
        {
            string path = kind == "rooted" ? SystemPath(assetRoot + "/First")
                : kind == "traversal" ? assetRoot + "/Second/../First"
                : kind == "invalid_ancestor" ? assetRoot + "/Bad?Directory"
                : assetRoot + "/First";
            string name = kind == "invalid_leaf" ? sceneName + "?" : sceneName;
            Scene[] before = LoadedScenes();
            string[] files = Directory.GetFiles(SystemPath(assetRoot), "*", SearchOption.AllDirectories);
            bool wasDirty = first.isDirty;

            var response = Call(new JObject { ["action"] = action, ["path"] = path, ["name"] = name });

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.StartsWith("Invalid scene path:", response.Value<string>("error"));
            CollectionAssert.AreEqual(before, LoadedScenes());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
            Assert.AreEqual(wasDirty, first.isDirty);
            CollectionAssert.AreEquivalent(files, Directory.GetFiles(SystemPath(assetRoot), "*", SearchOption.AllDirectories));
            Assert.IsFalse(Directory.Exists(SystemPath(assetRoot + "/Bad?Directory")));
        }

        [TestCase("create")]
        [TestCase("save")]
        [TestCase("load")]
        public void SceneNameWithDirectory_IsRejectedBeforeMutation(string action)
        {
            Scene[] before = LoadedScenes();
            var response = Call(new JObject { ["action"] = action, ["path"] = assetRoot + "/First", ["name"] = "../Rejected" });
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.StartsWith("Invalid scene path:", response.Value<string>("error"));
            CollectionAssert.AreEqual(before, LoadedScenes());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
            Assert.IsFalse(File.Exists(SystemPath(assetRoot + "/Rejected.unity")));
        }

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
        public void Unload_RetainsHierarchyEntryButReportsOnlyLoadedScenes()
        {
            int loadedBefore = LoadedScenes().Count(scene => scene.isLoaded);
            Scene[] before = LoadedScenes();
            JObject response = Call(Select("close_scene"));
            Success(response);
            CollectionAssert.AreEqual(before, LoadedScenes());
            Assert.IsTrue(first.isLoaded);
            Assert.IsTrue(second.IsValid());
            Assert.IsFalse(second.isLoaded);
            Assert.AreEqual(loadedBefore - 1, response["data"].Value<int>("loadedSceneCount"));

            JObject query = Call(new JObject { ["action"] = "get_loaded_scenes" });
            Success(query);
            Assert.AreEqual($"{loadedBefore - 1} scene(s) loaded.", query.Value<string>("message"));
            JToken unloaded = query["data"]["scenes"].Single(scene => scene.Value<string>("path") == secondPath);
            Assert.IsFalse(unloaded.Value<bool>("isLoaded"));
            Assert.AreEqual(0, unloaded.Value<int>("rootCount"));
        }

        [Test]
        public void RemovePreviouslyUnloadedScene_PreservesLoadedScenes()
        {
            Assert.IsTrue(EditorSceneManager.CloseScene(second, false));
            Scene[] loadedBefore = LoadedScenes().Where(scene => scene.isLoaded).ToArray();
            JObject request = Select("close_scene");
            request["removeScene"] = true;
            JObject response = Call(request);
            Success(response);
            CollectionAssert.AreEqual(loadedBefore, LoadedScenes().Where(scene => scene.isLoaded).ToArray());
            Assert.AreEqual(loadedBefore.Length, response["data"].Value<int>("loadedSceneCount"));
            Assert.IsFalse(second.IsValid());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
        }

        [Test]
        public void AdditiveLoad_WithRetainedUnloadedScene_ReportsOnlyLoadedScenes()
        {
            Assert.IsTrue(EditorSceneManager.CloseScene(second, false));
            Scene third = NewOwnedScene();
            string thirdPath = assetRoot + "/Third.unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(third, thirdPath));
            Assert.IsTrue(EditorSceneManager.CloseScene(third, true));
            int loadedBefore = LoadedScenes().Count(scene => scene.isLoaded);
            JObject response = Call(new JObject { ["action"] = "load", ["path"] = thirdPath, ["additive"] = true });
            Scene loaded = SceneManager.GetSceneByPath(thirdPath);
            if (loaded.IsValid() && loaded.isLoaded) ownedScenes.Add(loaded);
            Success(response);
            Assert.IsTrue(loaded.IsValid() && loaded.isLoaded);
            Assert.IsTrue(first.isLoaded);
            Assert.IsTrue(second.IsValid() && !second.isLoaded);
            Assert.AreEqual(loadedBefore + 1, response["data"].Value<int>("loadedSceneCount"));
        }

        [TestCase(false, true)]
        [TestCase(true, true)]
        [TestCase(false, false)]
        [TestCase(true, false)]
        public void BuildSettingsIndices_CountOnlyEnabledScenes(bool firstEnabled, bool secondEnabled)
        {
            EditorBuildSettingsScene[] original = EditorBuildSettings.scenes;
            Scene[] before = LoadedScenes();
            try
            {
                EditorBuildSettings.scenes = new[]
                {
                    new EditorBuildSettingsScene(firstPath, firstEnabled),
                    new EditorBuildSettingsScene(secondPath, secondEnabled)
                };
                JObject response = Call(new JObject { ["action"] = "get_build_settings" });
                Success(response);
                var rows = (JArray)response["data"];
                Assert.AreEqual(2, rows.Count);
                Assert.AreEqual(firstPath, rows[0].Value<string>("path"));
                Assert.AreEqual(secondPath, rows[1].Value<string>("path"));
                Assert.AreEqual(firstEnabled, rows[0].Value<bool>("enabled"));
                Assert.AreEqual(secondEnabled, rows[1].Value<bool>("enabled"));
                Assert.AreEqual(firstEnabled ? 0 : -1, rows[0].Value<int>("buildIndex"));
                Assert.AreEqual(secondEnabled ? (firstEnabled ? 1 : 0) : -1, rows[1].Value<int>("buildIndex"));
                CollectionAssert.AreEqual(before, LoadedScenes());
                Assert.AreEqual(first, SceneManager.GetActiveScene());
            }
            finally
            {
                EditorBuildSettings.scenes = original;
            }
        }

        [Test]
        public void CombinedSelector_MovesOwnedRootToExactLaterPath()
        {
            var go = new GameObject("Owned_" + Guid.NewGuid().ToString("N"));
            Assert.AreEqual(first, go.scene);
            JObject request = Select("move_to_scene");
            request["target"] = go.GetInstanceIDCompat();
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
            Scene[] before = LoadedScenes();
            JObject request = Select(action);
            request["scenePath"] = assetRoot + "/Missing.unity";
            request["target"] = go.GetInstanceIDCompat();
            Assert.IsFalse(Call(request).Value<bool>("success"));
            CollectionAssert.AreEqual(before, LoadedScenes());
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
            Scene[] before = LoadedScenes();
            JObject request = new JObject { ["action"] = "create", ["path"] = rejectedDir };
            if (kind == "unknown_template") { request["name"] = "New"; request["template"] = "unknown"; }
            if (kind == "collision") { request["name"] = sceneName; request["path"] = firstPath; }
            string guid = AssetDatabase.AssetPathToGUID(firstPath);
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Assert.IsFalse(Directory.Exists(SystemPath(rejectedDir)));
            CollectionAssert.AreEqual(before, LoadedScenes());
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
        public void SaveCurrentScene_RejectsAncestorChangedToLinkBeforeOverwriting()
        {
            string owner = Path.GetFullPath(SystemPath(assetRoot)) + Path.DirectorySeparatorChar;
            string original = SystemPath(assetRoot + "/First");
            string retained = SystemPath(assetRoot + "/RetainedFirst");
            string target = SystemPath(assetRoot + "/OwnedOutside");
            foreach (string path in new[] { original, retained, target })
                StringAssert.StartsWith(owner, Path.GetFullPath(path));
            Directory.CreateDirectory(target);
            string sentinel = Path.Combine(target, sceneName + ".unity");
            File.WriteAllText(sentinel, "owned sentinel");
            Directory.Move(original, retained);
            bool linked = false;
            try
            {
                linked = Application.platform == RuntimePlatform.WindowsEditor
                    ? CreateSymbolicLinkW(original, target, 1 | 2)
                    : symlink(target, original) == 0;
                if (!linked) Assert.Ignore("Owned symbolic-link creation unavailable: " + Marshal.GetLastWin32Error());
                Scene[] before = LoadedScenes();
                var response = Call(new JObject { ["action"] = "save" });
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("symbolic links or junctions", response.Value<string>("error"));
                Assert.AreEqual("owned sentinel", File.ReadAllText(sentinel));
                CollectionAssert.AreEqual(before, LoadedScenes());
                Assert.AreEqual(first, SceneManager.GetActiveScene());
            }
            finally
            {
                // Remove only the link itself, then restore the exact captured owned directory.
                if (linked) Directory.Delete(original);
                Directory.Move(retained, original);
            }
        }

        [Test]
        public void AdditivePathLoad_PreservesAllExistingScenes()
        {
            Assert.IsTrue(EditorSceneManager.CloseScene(second, true));
            Scene[] before = LoadedScenes();
            JObject response = Call(new JObject { ["action"] = "load", ["path"] = secondPath, ["additive"] = true });
            Scene loaded = SceneManager.GetSceneByPath(secondPath);
            if (loaded.IsValid() && loaded.isLoaded) ownedScenes.Add(loaded);
            Success(response);
            Assert.IsTrue(loaded.IsValid() && loaded.isLoaded);
            CollectionAssert.IsSubsetOf(before, LoadedScenes());
            Assert.AreEqual(before.Length + 1, SceneManager.sceneCount);
            Assert.IsTrue(first.isLoaded);
        }

        [TestCase("load_additive")]
        [TestCase("close_dirty")]
        public void ExistingLoadAndDirtyCloseGuards_PreserveOwnedScenes(string kind)
        {
            Scene[] before = LoadedScenes();
            JObject request;
            if (kind == "load_additive") request = new JObject { ["action"] = "load", ["path"] = secondPath, ["additive"] = true };
            else
            {
                Assert.IsTrue(EditorSceneManager.MarkSceneDirty(second));
                request = Select("close_scene");
                request["removeScene"] = true;
            }
            Assert.IsFalse(Call(request).Value<bool>("success"));
            CollectionAssert.AreEqual(before, LoadedScenes());
            Assert.IsTrue(first.isLoaded && second.isLoaded);
        }

        [TestCase("negative")]
        [TestCase("null")]
        [TestCase("true")]
        [TestCase("false")]
        public void InvalidOrUnparsedBuildIndex_PerformsNoLoad(string kind)
        {
            JToken index = kind == "negative" ? new JValue(-1) : kind == "null" ? JValue.CreateNull() : new JValue(kind == "true");
            Scene[] before = LoadedScenes();
            Assert.IsFalse(Call(new JObject { ["action"] = "load", ["buildIndex"] = index, ["additive"] = true }).Value<bool>("success"));
            CollectionAssert.AreEqual(before, LoadedScenes());
            Assert.AreEqual(first, SceneManager.GetActiveScene());
        }
    }
}
