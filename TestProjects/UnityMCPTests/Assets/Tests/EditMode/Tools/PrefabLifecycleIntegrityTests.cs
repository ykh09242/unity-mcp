using System;
using System.Collections.Generic;
using System.IO;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class PrefabLifecycleIntegrityTests
    {
        private readonly List<GameObject> ownedObjects = new List<GameObject>();
        private string assetRoot;
        private string folderGuid;
        private Scene ownedScene;
        private Object[] originalSelection;
        private Object originalActiveSelection;
        private bool capturedState;
        private readonly PrefabTestSceneFixture testScene = new PrefabTestSceneFixture();

        [OneTimeSetUp]
        public void PrepareRunnerBootstrap() => testScene.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void RestoreRunnerBootstrap() => testScene.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            ownedScene = default;
            assetRoot = null;
            folderGuid = null;
            originalSelection = null;
            originalActiveSelection = null;
            capturedState = false;
            ownedObjects.Clear();
            originalSelection = Selection.objects;
            originalActiveSelection = Selection.activeObject;
            capturedState = true;
            string suffix = Guid.NewGuid().ToString("N");
            ownedScene = testScene.Create("McpPrefabLifecycleIntegrity_", suffix);
            assetRoot = "Assets/__McpPrefabLifecycleIntegrity_" + suffix;
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(assetRoot));
            Assert.IsEmpty(AssetDatabase.AssetPathToGUID(assetRoot, AssetPathToGUIDOptions.OnlyExistingAssets));
            folderGuid = AssetDatabase.CreateFolder("Assets", "__McpPrefabLifecycleIntegrity_" + suffix);
            Assert.IsNotEmpty(folderGuid);
            Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(assetRoot));
        }

        [TearDown]
        public void TearDown()
        {
            if (!capturedState)
                return;
            try
            {
                var stage = PrefabStageUtility.GetCurrentPrefabStage();
                if (stage != null && OwnsPath(stage.assetPath))
                {
                    // Discard only this fixture's temporary stage edits; never open a save/discard dialog.
                    Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    stage.ClearDirtiness();
                    StageUtility.GoToMainStage();
                    Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
                }
                foreach (GameObject go in ownedObjects)
                {
                    if (go == null || EditorUtility.IsPersistent(go))
                        continue;
                    Undo.ClearUndo(go);
                    Undo.ClearUndo(go.transform);
                    Object.DestroyImmediate(go);
                }
                ownedObjects.Clear();
                if (!string.IsNullOrEmpty(folderGuid))
                {
                    Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(assetRoot));
                    Assert.IsFalse(PrefabStageUtility.GetCurrentPrefabStage() != null && OwnsPath(PrefabStageUtility.GetCurrentPrefabStage().assetPath));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot));
                }
            }
            finally
            {
                try
                {
                    testScene.Close();
                }
                finally
                {
                    Selection.objects = originalSelection ?? Array.Empty<Object>();
                    Selection.activeObject = originalActiveSelection;
                    capturedState = false;
                }
            }
        }

        private bool OwnsPath(string path)
        {
            return !string.IsNullOrEmpty(assetRoot) && path != null && path.StartsWith(assetRoot + "/", StringComparison.Ordinal);
        }

        private GameObject Source()
        {
            var go = new GameObject("PrefabLifecycle_" + Guid.NewGuid().ToString("N"));
            ownedObjects.Add(go);
            SceneManager.MoveGameObjectToScene(go, ownedScene);
            Assert.AreEqual(ownedScene, go.scene);
            Assert.AreEqual(ownedScene, SceneManager.GetActiveScene());
            return go;
        }

        private string PathFor(string name)
        {
            return assetRoot + "/" + name + ".prefab";
        }

        private static JObject Send(string action, JObject options = null)
        {
            options = options ?? new JObject();
            options["action"] = action;
            return JObject.FromObject(ManagePrefabs.HandleCommand(options));
        }

        private JObject Create(GameObject source, string path, JObject options = null)
        {
            options = options ?? new JObject();
            options["target"] = source.name;
            options["prefabPath"] = path;
            return Send("create_from_gameobject", options);
        }

        private static void Success(JObject response)
        {
            Assert.IsTrue((bool)response["success"], response.ToString());
        }

        private static void Failure(JObject response)
        {
            Assert.IsFalse((bool)response["success"], response.ToString());
        }

        private string Seed(GameObject source, string name)
        {
            string path = PathFor(name);
            GameObject asset = PrefabUtility.SaveAsPrefabAssetAndConnect(source, path, InteractionMode.AutomatedAction, out bool saved);
            Assert.IsTrue(saved);
            Assert.IsNotNull(asset);
            Assert.IsTrue(EditorUtility.IsPersistent(asset));
            Assert.AreNotSame(source, asset);
            return path;
        }

        private static byte[] Bytes(string path)
        {
            Assert.IsTrue(path.StartsWith("Assets/", StringComparison.Ordinal));
            return File.ReadAllBytes(System.IO.Path.Combine(Application.dataPath, path.Substring("Assets/".Length)));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreateReturnsAndSelectsTheConnectedSceneSource(bool previouslyConnected)
        {
            GameObject source = Source();
            var child = new GameObject("OwnedChild");
            child.transform.SetParent(source.transform, false);
            if (previouslyConnected)
                Seed(source, "Original");
            string path = PathFor("Created");

            JObject response = Create(source, path, new JObject { ["unlinkIfInstance"] = previouslyConnected });

            Success(response);
            GameObject asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.IsNotNull(asset);
            Assert.IsTrue(EditorUtility.IsPersistent(asset));
            Assert.IsFalse(EditorUtility.IsPersistent(source));
            Assert.AreNotEqual(asset.GetInstanceIDCompat(), source.GetInstanceIDCompat());
            Assert.AreEqual(source.GetInstanceIDCompat(), (int)response["data"]["instanceId"]);
            Assert.AreEqual(source.name, (string)response["data"]["instanceName"]);
            Assert.AreEqual(source.GetComponents<Component>().Length, (int)response["data"]["componentCount"]);
            Assert.AreEqual(1, (int)response["data"]["childCount"]);
            Assert.AreEqual(previouslyConnected, (bool)response["data"]["wasUnlinked"]);
            Assert.AreSame(source, Selection.activeGameObject);
            Assert.AreEqual(path, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
        }

        [Test]
        public void ExistingConnectionWithoutUnlinkIsRejectedAndRetained()
        {
            GameObject source = Source();
            string original = Seed(source, "Original");
            Selection.activeGameObject = source;
            string requested = PathFor("Rejected");

            Failure(Create(source, requested, new JObject { ["unlinkIfInstance"] = false }));

            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(requested));
            Assert.AreEqual(original, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
            Assert.AreSame(source, Selection.activeGameObject);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingDestinationKeepsUniqueOrOverwritePolicy(bool overwrite)
        {
            string path = Seed(Source(), "Existing");
            string guid = AssetDatabase.AssetPathToGUID(path);
            byte[] original = Bytes(path);
            GameObject source = Source();

            JObject response = Create(source, path, new JObject { ["allowOverwrite"] = overwrite });

            Success(response);
            string resultPath = (string)response["data"]["prefabPath"];
            Assert.AreEqual(overwrite, (bool)response["data"]["wasReplaced"]);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            if (overwrite)
                Assert.AreEqual(path, resultPath);
            else
            {
                Assert.AreNotEqual(path, resultPath);
                CollectionAssert.AreEqual(original, Bytes(path));
            }
            Assert.AreEqual(source.GetInstanceIDCompat(), (int)response["data"]["instanceId"]);
            Assert.AreSame(source, Selection.activeGameObject);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InactiveSourceRetainsExplicitSearchPolicy(bool includeInactive)
        {
            GameObject source = Source();
            source.SetActive(false);
            string path = PathFor("Inactive");

            JObject response = Create(source, path, new JObject { ["searchInactive"] = includeInactive });

            Assert.AreEqual(includeInactive, (bool)response["success"], response.ToString());
            Assert.IsFalse(source.activeSelf);
            Assert.AreEqual(includeInactive, AssetDatabase.LoadMainAssetAtPath(path) != null);
        }

        [Test]
        public void MissingSourceDoesNotCreateAnAssetOrChangeSelection()
        {
            GameObject selected = Source();
            Selection.activeGameObject = selected;
            string path = PathFor("Missing");

            Failure(Send("create_from_gameobject", new JObject { ["target"] = "Missing_" + Guid.NewGuid().ToString("N"), ["prefabPath"] = path }));

            Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(path));
            Assert.AreSame(selected, Selection.activeGameObject);
        }

        [Test]
        public void HeadlessNoOpPreservesPrefabBytesAndPreviewScenes()
        {
            string path = Seed(Source(), "NoOp");
            byte[] original = Bytes(path);
            string guid = AssetDatabase.AssetPathToGUID(path);
            int previewScenes = EditorSceneManager.previewSceneCount;

            Success(Send("modify_contents", new JObject { ["prefabPath"] = path }));

            CollectionAssert.AreEqual(original, Bytes(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(previewScenes, EditorSceneManager.previewSceneCount);
        }

        [Test]
        public void MalformedHeadlessScalarDoesNotPersistAndUnloadsContents()
        {
            string path = Seed(Source(), "Scalar");
            byte[] original = Bytes(path);
            int previewScenes = EditorSceneManager.previewSceneCount;
            LogAssert.Expect(LogType.Error, new Regex("\\[ManagePrefabs\\] Action 'modify_contents' failed:"));

            Failure(
                Send(
                    "modify_contents",
                    new JObject
                    {
                        ["prefabPath"] = path,
                        ["name"] = "Changed",
                        ["setActive"] = "not-a-bool",
                    }
                )
            );

            CollectionAssert.AreEqual(original, Bytes(path));
            Assert.AreEqual(previewScenes, EditorSceneManager.previewSceneCount);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void InvalidHeadlessChildComponentDoesNotPersistAndUnloadsContents()
        {
            string path = Seed(Source(), "Child");
            byte[] original = Bytes(path);
            int previewScenes = EditorSceneManager.previewSceneCount;

            Failure(
                Send(
                    "modify_contents",
                    new JObject
                    {
                        ["prefabPath"] = path,
                        ["create_child"] = new JObject
                        {
                            ["name"] = "Temporary",
                            ["components_to_add"] = new JArray("Missing_" + Guid.NewGuid().ToString("N")),
                        },
                    }
                )
            );

            CollectionAssert.AreEqual(original, Bytes(path));
            Assert.AreEqual(previewScenes, EditorSceneManager.previewSceneCount);
        }

        [TestCase("position", "[1,2]", false)]
        [TestCase("rotation", "[1,2,3,4]", false)]
        [TestCase("scale", "{\"x\":1,\"y\":2}", false)]
        [TestCase("position", "true", false)]
        [TestCase("position", "[true,2,3]", false)]
        [TestCase("position", "[\"NaN\",2,3]", false)]
        [TestCase("position", "[1,null,3]", false)]
        [TestCase("position", "[1,2]", true)]
        [TestCase("rotation", "[1,2,3,4]", true)]
        [TestCase("scale", "{\"x\":1,\"y\":2}", true)]
        [TestCase("position", "[true,2,3]", true)]
        [TestCase("position", "[\"Infinity\",2,3]", true)]
        public void MalformedHeadlessVectorDoesNotPersistAndUnloadsContents(string field, string json, bool child)
        {
            var changes = new JObject { [field] = JToken.Parse(json) };
            if (child)
                changes["name"] = "Temporary";
            AssertHeadlessRejectedWithoutSaving(child ? new JObject { ["createChild"] = changes } : changes);
        }

        [TestCase("componentsToAdd", "{}", false)]
        [TestCase("componentsToAdd", "[{}]", false)]
        [TestCase("componentsToAdd", "[123]", false)]
        [TestCase("componentsToRemove", "{}", false)]
        [TestCase("componentsToRemove", "[{}]", false)]
        [TestCase("componentProperties", "[]", false)]
        [TestCase("componentProperties", "{\"Transform\":[]}", false)]
        [TestCase("componentsToAdd", "{}", true)]
        [TestCase("components_to_add", "{}", true)]
        public void MalformedHeadlessCollectionDoesNotPersistAndUnloadsContents(string field, string json, bool child)
        {
            var changes = new JObject { [field] = JToken.Parse(json) };
            if (child)
                changes["name"] = "Temporary";
            AssertHeadlessRejectedWithoutSaving(child ? new JObject { ["createChild"] = changes } : changes);
        }

        private void AssertHeadlessRejectedWithoutSaving(JObject changes)
        {
            string path = Seed(Source(), "Malformed");
            byte[] original = Bytes(path);
            int previewScenes = EditorSceneManager.previewSceneCount;
            changes["prefabPath"] = path;
            changes["name"] = "Changed";
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                Failure(Send("modify_contents", changes));
                CollectionAssert.AreEqual(original, Bytes(path));
                Assert.AreEqual(previewScenes, EditorSceneManager.previewSceneCount);
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void HeadlessOptionalNullVectorsAndCollectionsRemainNoOps(bool child)
        {
            string path = Seed(Source(), "OptionalNull");
            var optional = new JObject
            {
                ["position"] = JValue.CreateNull(),
                ["rotation"] = JValue.CreateNull(),
                ["scale"] = JValue.CreateNull(),
                ["componentsToAdd"] = JValue.CreateNull(),
            };
            var changes = child ? new JObject { ["createChild"] = optional } : optional;
            if (child)
                optional["name"] = "NullChild";
            changes["prefabPath"] = path;
            Success(Send("modify_contents", changes));
            var saved = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Transform target = child ? saved.transform.Find("NullChild") : saved.transform;
            Assert.IsNotNull(target);
            Assert.AreEqual(Vector3.zero, target.localPosition);
            Assert.AreEqual(Vector3.one, target.localScale);
        }

        [Test]
        public void OccupiedPrefabDirectoryIsRejectedBeforeUnlinkOrMaterialPersistence()
        {
            GameObject source = Source();
            string originalPath = Seed(source, "OriginalConnection");
            var renderer = source.AddComponent<MeshRenderer>();
            var runtimeMaterial = new Material(Shader.Find("Hidden/InternalErrorShader"));
            renderer.sharedMaterial = runtimeMaterial;
            string destination = PathFor("Occupied");
            Assert.IsNotEmpty(AssetDatabase.CreateFolder(assetRoot, "Occupied.prefab"));
            string sentinel = Path.Combine(Application.dataPath, destination.Substring("Assets/".Length), "Retained.txt");
            File.WriteAllText(sentinel, "retained");
            string folderGuid = AssetDatabase.AssetPathToGUID(destination);
            byte[] original = Bytes(originalPath);
            Selection.activeGameObject = source;
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                Failure(Create(source, destination, new JObject { ["allowOverwrite"] = true, ["unlinkIfInstance"] = true }));
                Assert.AreEqual(originalPath, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
                Assert.AreSame(runtimeMaterial, renderer.sharedMaterial);
                Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot + "/Materials"));
                Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(destination));
                Assert.AreEqual("retained", File.ReadAllText(sentinel));
                CollectionAssert.AreEqual(original, Bytes(originalPath));
                Assert.AreSame(source, Selection.activeGameObject);
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
                Object.DestroyImmediate(runtimeMaterial);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void HeadlessVectorObjectsAndNumericStringsRemainSupported(bool child)
        {
            string path = Seed(Source(), "VectorObject");
            var transform = new JObject
            {
                ["position"] = new JObject
                {
                    ["x"] = "0",
                    ["y"] = "2",
                    ["z"] = 3,
                },
                ["rotation"] = new JArray(0, 90, 0),
                ["scale"] = new JArray("1", "2", "3"),
            };
            var changes = child ? new JObject { ["createChild"] = transform } : transform;
            if (child)
                transform["name"] = "VectorChild";
            changes["prefabPath"] = path;
            Success(Send("modify_contents", changes));
            var saved = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Transform target = child ? saved.transform.Find("VectorChild") : saved.transform;
            Assert.IsNotNull(target);
            Assert.AreEqual(new Vector3(0, 2, 3), target.localPosition);
            Assert.AreEqual(new Vector3(1, 2, 3), target.localScale);
            Assert.Less(Quaternion.Angle(Quaternion.Euler(0, 90, 0), target.localRotation), 0.01f);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InvalidGeneratedMaterialPathIsRejectedBeforeUnlinkOrMaterialPersistence(bool laterRenderer)
        {
            GameObject source = Source();
            string originalPath = Seed(source, "OriginalMaterialConnection");
            var renderer = source.AddComponent<MeshRenderer>();
            var runtimeMaterial = new Material(Shader.Find("Hidden/InternalErrorShader"));
            renderer.sharedMaterial = runtimeMaterial;
            if (laterRenderer)
            {
                var child = new GameObject("Bad?Renderer", typeof(MeshRenderer));
                child.transform.SetParent(source.transform, false);
                child.GetComponent<MeshRenderer>().sharedMaterial = runtimeMaterial;
            }
            else
                source.name = "Bad?Renderer";
            string destination = assetRoot + "/New/Nested/Created.prefab";
            byte[] original = Bytes(originalPath);
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                Failure(Create(source, destination, new JObject { ["unlinkIfInstance"] = true }));
                Assert.AreEqual(originalPath, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
                Assert.AreSame(runtimeMaterial, renderer.sharedMaterial);
                Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot + "/New"));
                CollectionAssert.AreEqual(original, Bytes(originalPath));
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
                Object.DestroyImmediate(runtimeMaterial);
            }
        }

        [Test]
        public void MaterialFolderOccupiedByFileIsRejectedBeforeUnlinkOrMaterialPersistence()
        {
            GameObject source = Source();
            string originalPath = Seed(source, "OriginalFolderConnection");
            var renderer = source.AddComponent<MeshRenderer>();
            var runtimeMaterial = new Material(Shader.Find("Hidden/InternalErrorShader"));
            renderer.sharedMaterial = runtimeMaterial;
            string destination = PathFor("RejectedMaterialFolder");
            string occupiedPath = Path.Combine(Application.dataPath, assetRoot.Substring("Assets/".Length), "Materials");
            File.WriteAllText(occupiedPath, "retained");
            byte[] original = Bytes(originalPath);
            try
            {
                Failure(Create(source, destination, new JObject { ["unlinkIfInstance"] = true }));
                Assert.AreEqual(originalPath, PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(source));
                Assert.AreSame(runtimeMaterial, renderer.sharedMaterial);
                Assert.AreEqual("retained", File.ReadAllText(occupiedPath));
                Assert.IsFalse(Directory.Exists(occupiedPath));
                Assert.IsNull(AssetDatabase.LoadMainAssetAtPath(destination));
                CollectionAssert.AreEqual(original, Bytes(originalPath));
            }
            finally
            {
                Object.DestroyImmediate(runtimeMaterial);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void OwnedStageCloseVerifiesMainStageAndOptionalSave(bool save)
        {
            GameObject source = Source();
            var child = new GameObject("OwnedStageChild");
            child.transform.SetParent(source.transform, false);
            string path = Seed(source, "Stage");
            Success(Send("open_prefab_stage", new JObject { ["prefabPath"] = path }));
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            Assert.IsNotNull(stage);
            Assert.AreEqual(path, stage.assetPath);
            if (save)
            {
                stage.prefabContentsRoot.transform.GetChild(0).localPosition = new Vector3(1, 2, 3);
                EditorSceneManager.MarkSceneDirty(stage.scene);
            }
            else
            {
                Assert.IsFalse(stage.scene.isDirty, "The no-save close fixture requires a clean owned stage.");
            }

            Success(Send("close_prefab_stage", new JObject { ["saveBeforeClose"] = save }));

            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
            if (save)
                Assert.AreEqual(new Vector3(1, 2, 3), AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.GetChild(0).localPosition);
            LogAssert.NoUnexpectedReceived();
        }

        [Test]
        public void AlreadyMainStageCloseRemainsSuccessful()
        {
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
            Success(Send("close_prefab_stage", new JObject { ["saveBeforeClose"] = true }));
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
        }

        [Test]
        public void MissingStageSaveRemainsAnError()
        {
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
            Failure(Send("save_prefab_stage"));
            Assert.IsNull(PrefabStageUtility.GetCurrentPrefabStage());
        }
    }
}
