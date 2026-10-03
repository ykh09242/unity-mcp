using System;
using System.IO;
using NUnit.Framework;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using MCPForUnity.Editor.Tools;

namespace MCPForUnity.Tests.EditMode.Tools
{
    /// <summary>
    /// Scenes that live under Packages/ used to be re-rooted under Assets/, so a valid
    /// package scene path was rewritten to "Assets/Packages/..." and could never resolve
    /// (issue #1197).
    /// </summary>
    [TestFixture]
    public class ManageScenePackagePathTests
    {
        [TestCase("Assets/Scenes/Main.unity", true)]
        [TestCase("assets/scenes/main.unity", true)]
        [TestCase("Packages/com.example.pkg/Samples/Demo.unity", true)]
        [TestCase("packages/com.example.pkg/Samples/Demo.unity", true)]
        [TestCase("Scenes/Main.unity", false)]
        [TestCase("com.example.pkg/Samples/Demo.unity", false)]
        [TestCase("", false)]
        [TestCase(null, false)]
        public void IsProjectRooted_RecognisesBothRoots(string path, bool expected)
        {
            Assert.AreEqual(expected, ManageScene.IsProjectRooted(path));
        }

        [Test]
        public void Load_MissingPackageScene_ReportsThePackagePath_NotAnAssetsRewrite()
        {
            var p = new JObject
            {
                ["action"] = "load",
                ["path"] = "Packages/com.example.doesnotexist/Samples/Demo.unity"
            };

            var r = ManageScene.HandleCommand(p) as JObject
                    ?? JObject.FromObject(ManageScene.HandleCommand(p));

            Assert.IsFalse(r.Value<bool>("success"), r.ToString());

            string message = r.Value<string>("message") ?? r.ToString();
            StringAssert.Contains("Packages/com.example.doesnotexist/Samples/Demo.unity", message);
            StringAssert.DoesNotContain("Assets/Packages", message);
        }

        [Test]
        public void SceneAssetExists_ReturnsFalse_ForUnknownPaths()
        {
            Assert.IsFalse(ManageScene.SceneAssetExists("Packages/com.example.doesnotexist/A.unity"));
            Assert.IsFalse(ManageScene.SceneAssetExists("Assets/DoesNotExist/A.unity"));
            Assert.IsFalse(ManageScene.SceneAssetExists(null));
        }

        [TestCase("create")]
        [TestCase("save")]
        public void PackageSceneWrites_AreRejectedAtThePublicEntry(string action)
        {
            var response = JObject.FromObject(ManageScene.HandleCommand(new JObject
            {
                ["action"] = action,
                ["path"] = "Packages/com.example.doesnotexist",
                ["name"] = "Rejected"
            }));
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("read-only", response.Value<string>("error"));
        }

        [TestCase("Assets/../Packages/com.example.doesnotexist/A.unity")]
        [TestCase("Packages/../Assets/A.unity")]
        [TestCase("/Assets/A.unity")]
        [TestCase("Assets/Bad?Directory/A.unity")]
        public void SceneAssetExists_RejectsMalformedPaths(string path)
        {
            Assert.IsFalse(ManageScene.SceneAssetExists(path));
        }

        /// <summary>
        /// The AssetDatabase does not know about a file written to disk until it is imported.
        /// Swapping File.Exists for an AssetDatabase lookup would have made such a scene
        /// unloadable, so the check accepts either answer.
        /// </summary>
        [Test]
        public void SceneAssetExists_FindsUnimportedFileOnDisk()
        {
            string dir = Path.Combine(Application.dataPath, "ManageScenePackagePathTests_Tmp");
            string relative = "Assets/ManageScenePackagePathTests_Tmp/NotImported.unity";
            string full = Path.Combine(dir, "NotImported.unity");

            Directory.CreateDirectory(dir);
            try
            {
                // Written directly, deliberately without AssetDatabase.Refresh().
                File.WriteAllText(full, "%YAML 1.1\n");

                Assert.IsNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(relative),
                    "sanity: the AssetDatabase must not know about this file yet");
                Assert.IsTrue(ManageScene.SceneAssetExists(relative),
                    "a scene present on disk must still be found");
            }
            finally
            {
                if (Directory.Exists(dir)) Directory.Delete(dir, recursive: true);
                string meta = dir + ".meta";
                if (File.Exists(meta)) File.Delete(meta);
                AssetDatabase.Refresh();
            }
        }

        [TestCase(null, true)]
        [TestCase(null, false)]
        [TestCase("empty", false)]
        [TestCase("default", false)]
        [TestCase("3d_basic", false)]
        [TestCase("2d_basic", false)]
        public void Create_PreservesUnsavedLoadedScene(string template, bool dirtyIsActive)
        {
            using (var fixture = new SceneReplacementFixture(dirtyIsActive))
            {
                var command = new JObject
                {
                    ["action"] = "create",
                    ["name"] = "Replacement",
                    ["path"] = fixture.Folder + "/Rejected"
                };
                if (template != null) command["template"] = template;

                var response = JObject.FromObject(ManageScene.HandleCommand(command));

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("unsaved changes", response.Value<string>("error"));
                fixture.AssertUnsavedScenePreserved();
                Assert.IsFalse(Directory.Exists(Path.Combine(Application.dataPath,
                    fixture.Folder.Substring("Assets/".Length), "Rejected")));
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LoadSingle_PreservesUnsavedInactiveScene(bool byBuildIndex)
        {
            using (var fixture = new SceneReplacementFixture(false))
            {
                var command = new JObject { ["action"] = "load" };
                if (byBuildIndex)
                {
                    EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(fixture.TargetPath, true) };
                    command["buildIndex"] = 0;
                }
                else command["path"] = fixture.TargetPath;

                var response = JObject.FromObject(ManageScene.HandleCommand(command));

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                StringAssert.Contains("unsaved changes", response.Value<string>("error"));
                fixture.AssertUnsavedScenePreserved();
                Assert.IsFalse(SceneManager.GetSceneByPath(fixture.TargetPath).isLoaded);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void LoadAdditive_AllowsUnsavedInactiveScene(bool byBuildIndex)
        {
            using (var fixture = new SceneReplacementFixture(false))
            {
                var command = new JObject { ["action"] = "load", ["additive"] = true };
                if (byBuildIndex)
                {
                    EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(fixture.TargetPath, true) };
                    command["buildIndex"] = 0;
                }
                else command["path"] = fixture.TargetPath;

                var response = JObject.FromObject(ManageScene.HandleCommand(command));

                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                fixture.AssertUnsavedScenePreserved();
                Assert.IsTrue(SceneManager.GetSceneByPath(fixture.TargetPath).isLoaded);
            }
        }

        private sealed class SceneReplacementFixture : IDisposable
        {
            private readonly SceneSetup[] _originalSetup;
            private readonly EditorBuildSettingsScene[] _originalBuildScenes;
            private readonly Scene _dirtyScene;
            private readonly GameObject _unsavedObject;
            public string Folder { get; }
            public string TargetPath => Folder + "/Target.unity";

            public SceneReplacementFixture(bool dirtyIsActive)
            {
                for (int i = 0; i < SceneManager.sceneCount; i++)
                    if (SceneManager.GetSceneAt(i).isDirty)
                        Assert.Ignore("Scene replacement fixtures require saved pre-existing scenes.");
                _originalSetup = EditorSceneManager.GetSceneManagerSetup();
                _originalBuildScenes = EditorBuildSettings.scenes;
                Folder = "Assets/SceneReplacementTests_" + Guid.NewGuid().ToString("N");
                try
                {
                    AssetDatabase.CreateFolder("Assets", Folder.Substring("Assets/".Length));
                    var clean = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    Assert.IsTrue(EditorSceneManager.SaveScene(clean, Folder + "/Clean.unity"));
                    var target = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    Assert.IsTrue(EditorSceneManager.SaveScene(target, TargetPath));
                    EditorSceneManager.CloseScene(target, true);
                    _dirtyScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                    SceneManager.SetActiveScene(_dirtyScene);
                    _unsavedObject = new GameObject("UnsavedObject");
                    EditorSceneManager.MarkSceneDirty(_dirtyScene);
                    SceneManager.SetActiveScene(dirtyIsActive ? _dirtyScene : clean);
                    Assert.IsFalse(clean.isDirty, "The inactive-scene regression requires a clean active scene.");
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            public void AssertUnsavedScenePreserved()
            {
                Assert.IsTrue(_dirtyScene.IsValid() && _dirtyScene.isLoaded && _dirtyScene.isDirty);
                Assert.IsTrue(_unsavedObject != null, "The unsaved object must survive the command.");
                Assert.AreEqual(_dirtyScene, _unsavedObject.scene);
            }

            public void Dispose()
            {
                EditorBuildSettings.scenes = _originalBuildScenes;
                try
                {
                    EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                    if (_originalSetup.Length > 0) EditorSceneManager.RestoreSceneManagerSetup(_originalSetup);
                }
                finally
                {
                    AssetDatabase.DeleteAsset(Folder);
                }
            }
        }
    }
}
