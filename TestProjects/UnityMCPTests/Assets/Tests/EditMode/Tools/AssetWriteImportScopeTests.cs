using System;
using System.Diagnostics;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnityTests.Editor.Tools.Fixtures;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Tools
{
    public class AssetWriteImportScopeTests
    {
        private string assetRoot;
        private Scene originalActive;
        private Scene ownedScene;
        private bool ownsAssetRoot;
        private bool autoRefreshDisabled;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveObject;
        private PrefabStage ownedPrefabStage;
        private SceneSetup[] originalSceneSetup;
        private bool restoreSceneSetup;

        [SetUp]
        public void SetUp()
        {
            ownsAssetRoot = false;
            autoRefreshDisabled = false;
            originalActive = default;
            ownedScene = default;
            originalSelection = null;
            ownedPrefabStage = null;
            restoreSceneSetup = false;
            if (PrefabStageUtility.GetCurrentPrefabStage() != null)
                Assert.Ignore("The fixture preserves an existing Prefab Stage.");
            for (int i = 0; i < SceneManager.sceneCount; i++)
                if (string.IsNullOrEmpty(SceneManager.GetSceneAt(i).path))
                    Assert.Ignore("Save untitled scenes before running this additive fixture.");

            originalActive = SceneManager.GetActiveScene();
            originalSceneSetup = EditorSceneManager.GetSceneManagerSetup();
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            assetRoot = "Assets/__McpAssetWriteScope_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(Directory.Exists(FullPath(assetRoot)));
            string guid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            ownsAssetRoot = !string.IsNullOrEmpty(guid);
            Assert.IsTrue(ownsAssetRoot);
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(guid));
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            if (SceneManager.GetActiveScene() != ownedScene)
                Assert.IsTrue(SceneManager.SetActiveScene(ownedScene));
            Assert.AreEqual(ownedScene, SceneManager.GetActiveScene());
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                if (ownedPrefabStage != null && PrefabStageUtility.GetCurrentPrefabStage() == ownedPrefabStage)
                    StageUtility.GoToMainStage();
                if (restoreSceneSetup)
                    EditorSceneManager.RestoreSceneManagerSetup(originalSceneSetup);
                if (originalActive.IsValid() && originalActive.isLoaded)
                    SceneManager.SetActiveScene(originalActive);
                if (ownedScene.IsValid())
                    Assert.IsTrue(EditorSceneManager.CloseScene(ownedScene, true));
                if (ownsAssetRoot)
                {
                    StringAssert.StartsWith("Assets/__McpAssetWriteScope_", assetRoot);
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                }
            }
            finally
            {
                if (originalSelection != null)
                {
                    Selection.objects = originalSelection;
                    Selection.activeObject = originalActiveObject;
                }
                if (autoRefreshDisabled)
                {
                    AssetDatabase.AllowAutoRefresh();
                    autoRefreshDisabled = false;
                }
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void SaveScene_UpdatesAssetWithoutImportingUnrelatedPendingFile(bool saveAs)
        {
            string originalPath = assetRoot + "/Original.unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, originalPath));
            string originalGuid = AssetDatabase.AssetPathToGUID(originalPath);
            Assert.IsNotEmpty(originalGuid);
            var marker = new GameObject("PersistedBySave");
            if (marker.scene != ownedScene)
                SceneManager.MoveGameObjectToScene(marker, ownedScene);
            EditorSceneManager.MarkSceneDirty(ownedScene);
            Assert.IsTrue(ownedScene.isDirty);
            string destination = saveAs ? assetRoot + "/NewFolder/Nested/SavedAs.unity" : originalPath;
            string pending = CreatePendingUnrelatedFile();
            var request = new JObject { ["action"] = "save" };
            if (saveAs)
                request["path"] = destination;

            var timer = Stopwatch.StartNew();
            JObject response = JObject.FromObject(ManageScene.HandleCommand(request));
            timer.Stop();
            TestContext.Progress.WriteLine($"Scene save (saveAs={saveAs}): {timer.Elapsed.TotalMilliseconds:F3} ms");

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(destination, response["data"].Value<string>("path"));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(destination));
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(destination));
            Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(originalPath));
            Assert.IsFalse(ownedScene.isDirty);
            StringAssert.Contains("PersistedBySave", File.ReadAllText(FullPath(destination)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending), "Saving one scene must not refresh and import unrelated filesystem changes.");
        }

        [TestCase(null)]
        [TestCase("empty")]
        public void CreateScene_RegistersCleanActiveSceneWithoutImportingUnrelatedPendingFile(string template)
        {
            if (!Application.isBatchMode)
                Assert.Ignore("Single-scene replacement is exercised only in an isolated batch test Editor.");
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/BeforeCreate.unity"));
            string directory = assetRoot + "/Scenes/Nested";
            string destination = directory + "/Created.unity";
            string pending = CreatePendingUnrelatedFile();
            restoreSceneSetup = true;

            JObject response = JObject.FromObject(
                ManageScene.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["name"] = "Created",
                        ["path"] = directory,
                        ["template"] = template,
                    }
                )
            );
            ownedScene = SceneManager.GetActiveScene();

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(destination, response["data"].Value<string>(template == null ? "path" : "scenePath"));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<SceneAsset>(destination));
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(destination));
            Assert.IsTrue(ownedScene.IsValid() && ownedScene.isLoaded);
            Assert.AreEqual(destination, ownedScene.path);
            Assert.IsFalse(ownedScene.isDirty);
            Assert.AreEqual(1, SceneManager.sceneCount, "Create replaces the saved test scene set with one clean scene.");
            StringAssert.Contains("RenderSettings:", File.ReadAllText(FullPath(destination)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending), "Creating one scene must not import unrelated pending filesystem changes.");
        }

        [Test]
        public void ModifyPrefab_UpdatesSavedAssetWithoutImportingUnrelatedPendingFile()
        {
            string prefabPath = assetRoot + "/Probe.prefab";
            var source = new GameObject("Probe", typeof(Canvas));
            if (source.scene != ownedScene)
                SceneManager.MoveGameObjectToScene(source, ownedScene);
            PrefabUtility.SaveAsPrefabAsset(source, prefabPath, out bool saved);
            Assert.IsTrue(saved);
            UnityEngine.Object.DestroyImmediate(source);
            string guid = AssetDatabase.AssetPathToGUID(prefabPath);
            Assert.IsNotEmpty(guid);
            string pending = CreatePendingUnrelatedFile();

            var timer = Stopwatch.StartNew();
            JObject response = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify_contents",
                        ["prefabPath"] = prefabPath,
                        ["componentProperties"] = new JObject { ["Canvas"] = new JObject { ["sortingOrder"] = 12 } },
                    }
                )
            );
            timer.Stop();
            TestContext.Progress.WriteLine($"Prefab modify: {timer.Elapsed.TotalMilliseconds:F3} ms");

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(response["data"].Value<bool>("modified"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(prefabPath));
            var loaded = PrefabUtility.LoadPrefabContents(prefabPath);
            try
            {
                Assert.AreEqual(12, loaded.GetComponent<Canvas>().sortingOrder);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(loaded);
            }
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending), "Saving one prefab must not refresh and import unrelated filesystem changes.");
        }

        [TestCase(false, false)]
        [TestCase(false, true)]
        [TestCase(true, false)]
        [TestCase(true, true)]
        public void ShaderWrite_ImportsOnlyRequestedShaderAndPreservesDiagnostics(bool update, bool invalid)
        {
            string name = "McpScopeShader_" + Guid.NewGuid().ToString("N");
            string shaderDirectory = assetRoot + "/Shaders/Nested";
            string shaderPath = shaderDirectory + "/" + name + ".shader";
            if (update)
            {
                JObject created = ShaderWrite("create", name, ValidShader(name), shaderDirectory);
                Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            }
            string initialGuid = AssetDatabase.AssetPathToGUID(shaderPath);
            string contents = invalid
                ? "Shader \"" + name + "\" { SubShader { Pass { DefinitelyInvalidSyntax } } }"
                : ValidShader(name) + "\n// Imported requested revision";
            string pending = CreatePendingUnrelatedFile();
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            JObject response;
            try
            {
                // Intentionally invalid ShaderLab must preserve importer diagnostics.
                if (invalid)
                    LogAssert.ignoreFailingMessages = true;
                response = ShaderWrite(update ? "update" : "create", name, contents, shaderDirectory);
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
            }

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(shaderPath, response["data"].Value<string>("path"));
            var shader = AssetDatabase.LoadAssetAtPath<Shader>(shaderPath);
            Assert.IsNotNull(shader, "The requested shader must be imported before the handler returns.");
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(shaderPath));
            if (update)
                Assert.AreEqual(initialGuid, AssetDatabase.AssetPathToGUID(shaderPath));
            Assert.AreEqual(contents, File.ReadAllText(FullPath(shaderPath)));
            Assert.AreEqual(invalid, ShaderUtil.ShaderHasError(shader), "Synchronous targeted import must retain valid/invalid shader diagnostic behavior.");
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending), "Shader import must not discover unrelated pending filesystem changes.");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreatePrefab_RegistersSavedAssetWithoutImportingUnrelatedPendingFile(bool replace)
        {
            string prefabPath = assetRoot + (replace ? "/Created.prefab" : "/Prefabs/Nested/Created.prefab");
            if (replace)
            {
                var previous = new GameObject("PreviousRoot");
                try
                {
                    PrefabUtility.SaveAsPrefabAsset(previous, prefabPath, out bool saved);
                    Assert.IsTrue(saved);
                }
                finally
                {
                    UnityEngine.Object.DestroyImmediate(previous);
                }
            }
            string originalGuid = AssetDatabase.AssetPathToGUID(prefabPath);
            var source = new GameObject("CreateScope_" + Guid.NewGuid().ToString("N"), typeof(Canvas));
            source.GetComponent<Canvas>().sortingOrder = 17;
            var child = new GameObject("SavedChild");
            child.transform.SetParent(source.transform, false);
            string pending = CreatePendingUnrelatedFile();

            JObject response = JObject.FromObject(
                ManagePrefabs.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create_from_gameobject",
                        ["target"] = source.name,
                        ["prefabPath"] = prefabPath,
                        ["allowOverwrite"] = replace,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(prefabPath, response["data"].Value<string>("prefabPath"));
            Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(source));
            Assert.AreEqual(prefabPath, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
            var savedAsset = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            Assert.IsNotNull(savedAsset);
            Assert.AreEqual(17, savedAsset.GetComponent<Canvas>().sortingOrder);
            Assert.IsNotNull(savedAsset.transform.Find("SavedChild"));
            Assert.IsNotEmpty(AssetDatabase.AssetPathToGUID(prefabPath));
            if (replace)
                Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(prefabPath));
            StringAssert.Contains("SavedChild", File.ReadAllText(FullPath(prefabPath)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending), "Native prefab creation must not discover unrelated pending filesystem changes.");
        }

        [Test]
        public void SavePrefabStage_KeepsStageOpenAndCleanWithoutImportingUnrelatedPendingFile()
        {
            string prefabPath = assetRoot + "/Stage.prefab";
            var source = new GameObject("StageRoot");
            try
            {
                PrefabUtility.SaveAsPrefabAsset(source, prefabPath, out bool saved);
                Assert.IsTrue(saved);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(source);
            }
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/Owned.unity"));
            string guid = AssetDatabase.AssetPathToGUID(prefabPath);
            JObject opened = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "open_prefab_stage", ["prefabPath"] = prefabPath }));
            ownedPrefabStage = PrefabStageUtility.GetCurrentPrefabStage();
            Assert.IsTrue(opened.Value<bool>("success"), opened.ToString());
            Assert.IsNotNull(ownedPrefabStage);
            var child = new GameObject("StageSavedChild");
            child.transform.SetParent(ownedPrefabStage.prefabContentsRoot.transform, false);
            EditorSceneManager.MarkSceneDirty(ownedPrefabStage.scene);
            Assert.IsTrue(ownedPrefabStage.scene.isDirty);
            string pending = CreatePendingUnrelatedFile();

            JObject response = JObject.FromObject(ManagePrefabs.HandleCommand(new JObject { ["action"] = "save_prefab_stage" }));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(ownedPrefabStage, PrefabStageUtility.GetCurrentPrefabStage());
            Assert.IsFalse(ownedPrefabStage.scene.isDirty, "Successful stage save must clear stage dirtiness.");
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(prefabPath));
            var savedAsset = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
            Assert.IsNotNull(savedAsset);
            Assert.IsNotNull(savedAsset.transform.Find("StageSavedChild"));
            StringAssert.Contains("StageSavedChild", File.ReadAllText(FullPath(prefabPath)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending), "Saving a prefab stage must not discover unrelated pending filesystem changes.");
        }

        [TestCase("create")]
        [TestCase("apply_gradient")]
        [TestCase("apply_noise")]
        public void TextureWrite_CreatesNestedFoldersWithoutImportingUnrelatedPendingFile(string action)
        {
            string destination = assetRoot + "/Textures/Nested/Created.png";
            string pending = CreatePendingUnrelatedFile();
            var response = JObject.FromObject(
                ManageTexture.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["path"] = destination,
                        ["width"] = 2,
                        ["height"] = 2,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(assetRoot + "/Textures/Nested"));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<Texture2D>(destination));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [Test]
        public void CreateFolder_KeepsRequestedEmptyHierarchyWithoutImportingUnrelatedPendingFile()
        {
            string destination = assetRoot + "/Requested/Nested/Empty";
            string pending = CreatePendingUnrelatedFile();
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "create_folder", ["path"] = destination }));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(destination));
            Assert.IsTrue(Directory.Exists(FullPath(destination)));
            Assert.IsEmpty(Directory.GetFileSystemEntries(FullPath(destination)));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void UIWrite_CreatesNestedFoldersWithoutImportingUnrelatedPendingFile(bool panelSettings)
        {
            string destination = assetRoot + "/UI/Nested/" + (panelSettings ? "Panel.asset" : "Styles.uss");
            string pending = CreatePendingUnrelatedFile();
            var request = new JObject { ["action"] = panelSettings ? "create_panel_settings" : "create", ["path"] = destination };
            if (!panelSettings)
                request["contents"] = "Label { color: red; }";
            var response = JObject.FromObject(ManageUI.HandleCommand(request));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(assetRoot + "/UI/Nested"));
            Assert.IsNotNull(AssetDatabase.LoadMainAssetAtPath(destination));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [Test]
        public void ScriptableObjectWrite_CreatesNestedFoldersWithoutImportingUnrelatedPendingFile()
        {
            string folder = assetRoot + "/Definitions/Nested";
            string pending = CreatePendingUnrelatedFile();
            var response = JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["folderPath"] = folder,
                        ["assetName"] = "Definition",
                        ["typeName"] = typeof(ScriptableObjectContractDefinition).FullName,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(folder));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<ScriptableObjectContractDefinition>(folder + "/Definition.asset"));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [TestCase("duplicate")]
        [TestCase("move")]
        public void AssetTransfer_CreatesNestedFoldersWithoutImportingUnrelatedPendingFile(string action)
        {
            string source = assetRoot + "/Source.txt";
            File.WriteAllText(FullPath(source), "source payload");
            AssetDatabase.ImportAsset(source, ImportAssetOptions.ForceSynchronousImport);
            string sourceGuid = AssetDatabase.AssetPathToGUID(source);
            string destination = assetRoot + "/Transferred/Nested/Copy.txt";
            string pending = CreatePendingUnrelatedFile();
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["path"] = source,
                        ["destination"] = destination,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("source payload", File.ReadAllText(FullPath(destination)));
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<TextAsset>(destination));
            if (action == "move")
                Assert.AreEqual(sourceGuid, AssetDatabase.AssetPathToGUID(destination));
            else
                Assert.AreEqual(sourceGuid, AssetDatabase.AssetPathToGUID(source));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [Test]
        public void GameObjectPrefabWrite_CreatesNestedFoldersWithoutImportingUnrelatedPendingFile()
        {
            string destination = assetRoot + "/GameObjects/Nested/Created.prefab";
            string pending = CreatePendingUnrelatedFile();
            var response = JObject.FromObject(
                GameObjectCreate.Handle(
                    new JObject
                    {
                        ["name"] = "NestedPrefab",
                        ["saveAsPrefab"] = true,
                        ["prefabPath"] = destination,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<GameObject>(destination));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [Test]
        public void PrefabWrite_PersistsRuntimeMaterialInNestedFolderWithoutImportingUnrelatedPendingFile()
        {
            Shader shader = RenderPipelineUtility.ResolveShader("Standard");
            if (shader == null)
                Assert.Ignore("Requires an available project-compatible shader.");
            var source = GameObject.CreatePrimitive(PrimitiveType.Cube);
            source.name = "MaterialScope_" + Guid.NewGuid().ToString("N");
            var runtimeMaterial = new Material(shader);
            source.GetComponent<Renderer>().sharedMaterial = runtimeMaterial;
            string destination = assetRoot + "/MaterialPrefabs/Nested/Created.prefab";
            string pending = CreatePendingUnrelatedFile();
            try
            {
                var response = JObject.FromObject(
                    ManagePrefabs.HandleCommand(
                        new JObject
                        {
                            ["action"] = "create_from_gameobject",
                            ["target"] = source.name,
                            ["prefabPath"] = destination,
                        }
                    )
                );

                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(1, response["data"].Value<int>("materialsPersisted"));
                var saved = AssetDatabase.LoadAssetAtPath<GameObject>(destination);
                Assert.IsNotNull(saved);
                var persisted = saved.GetComponent<Renderer>().sharedMaterial;
                Assert.IsTrue(EditorUtility.IsPersistent(persisted));
                StringAssert.StartsWith(assetRoot + "/MaterialPrefabs/Nested/Materials/", AssetDatabase.GetAssetPath(persisted));
                Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(runtimeMaterial);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void FailedMaterialCreate_RemovesOnlyRequestOwnedEmptyFolders(bool preexistingParent)
        {
            if (RenderPipelineUtility.ResolveShader("Standard") == null)
                Assert.Ignore("Requires an available project-compatible shader.");
            string parent = assetRoot + "/Failure";
            if (preexistingParent)
                Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Failure"));
            string parentGuid = AssetDatabase.AssetPathToGUID(parent);
            string pending = CreatePendingUnrelatedFile();
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["path"] = parent + "/Nested/Rejected.mat",
                        ["assetType"] = "Material",
                        ["properties"] = new JObject
                        {
                            ["shader"] = "Standard",
                            ["texture"] = new JObject { ["name"] = "_MainTex", ["path"] = assetRoot + "/Missing.png" },
                        },
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(Directory.Exists(FullPath(parent + "/Nested")));
            Assert.IsFalse(AssetDatabase.IsValidFolder(parent + "/Nested"));
            Assert.AreEqual(preexistingParent, Directory.Exists(FullPath(parent)));
            Assert.AreEqual(parentGuid, AssetDatabase.AssetPathToGUID(parent));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [Test]
        public void AbortedFolderScope_PreservesNonemptyOwnedFolderAndPreexistingParent()
        {
            string parent = assetRoot + "/Existing";
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Existing"));
            string parentGuid = AssetDatabase.AssetPathToGUID(parent);
            string populated = parent + "/Populated";
            string populatedGuid = null;
            string pending = CreatePendingUnrelatedFile();

            Assert.Throws<InvalidOperationException>(() =>
            {
                using var folders = new AssetFolderScope();
                folders.EnsureFolder(populated);
                populatedGuid = AssetDatabase.AssetPathToGUID(populated);
                File.WriteAllText(FullPath(populated + "/Retained.txt"), "keep this content");
                folders.EnsureFolder(parent + "/Empty/Nested");
                throw new InvalidOperationException("Abort after preparing output folders.");
            });

            Assert.AreEqual(parentGuid, AssetDatabase.AssetPathToGUID(parent));
            Assert.AreEqual(populatedGuid, AssetDatabase.AssetPathToGUID(populated));
            Assert.AreEqual("keep this content", File.ReadAllText(FullPath(populated + "/Retained.txt")));
            Assert.IsFalse(Directory.Exists(FullPath(parent + "/Empty")));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        [Test]
        public void AbortedFolderScope_PreservesPreexistingUnregisteredFolder()
        {
            string pending = CreatePendingUnrelatedFile();
            string existing = assetRoot + "/PhysicalOnly";
            Directory.CreateDirectory(FullPath(existing));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(existing));
            using (var folders = new AssetFolderScope())
            {
                folders.EnsureFolder(existing + "/Owned/Nested");
                Assert.IsTrue(AssetDatabase.IsValidFolder(existing));
            }

            Assert.IsTrue(Directory.Exists(FullPath(existing)));
            Assert.IsTrue(AssetDatabase.IsValidFolder(existing));
            Assert.IsFalse(Directory.Exists(FullPath(existing + "/Owned")));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(pending));
        }

        private JObject ShaderWrite(string action, string name, string contents, string directory = null) =>
            JObject.FromObject(
                ManageShader.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["name"] = name,
                        ["path"] = directory ?? assetRoot,
                        ["contents"] = contents,
                    }
                )
            );

        private static string ValidShader(string name) => "Shader \"" + name + "\" { SubShader { Pass {} } }";

        private string CreatePendingUnrelatedFile()
        {
            AssetDatabase.DisallowAutoRefresh();
            autoRefreshDisabled = true;
            string path = assetRoot + "/UnrelatedPending.txt";
            File.WriteAllText(FullPath(path), "This file is unrelated to the requested asset write.");
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(path), "The sentinel starts unimported.");
            return path;
        }

        private static string FullPath(string assetPath) => Path.Combine(Application.dataPath, assetPath.Substring("Assets/".Length));
    }
}
