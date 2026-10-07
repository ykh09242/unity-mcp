using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnityTests.Editor.Tools
{
    public class GameObjectLifecycleIntegrityTests
    {
        private Scene originalScene;
        private Scene ownedScene;
        private Scene secondaryScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveObject;
        private string prefix;
        private string assetRoot;
        private bool ownsAssetRoot;

        [SetUp]
        public void SetUp()
        {
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            ownsAssetRoot = false;
            secondaryScene = default;
            prefix = "LifecycleIntegrity_" + Guid.NewGuid().ToString("N");
            assetRoot = "Assets/__McpGameObjectLifecycleIntegrity_" + Guid.NewGuid().ToString("N");
            ownedScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(ownedScene);
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                foreach (var scene in new[] { ownedScene, secondaryScene })
                {
                    if (!scene.IsValid() || !scene.isLoaded)
                        continue;
                    foreach (var root in scene.GetRootGameObjects())
                    {
                        foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                        {
                            Undo.ClearUndo(transform.gameObject);
                            Undo.ClearUndo(transform);
                        }
                        UnityEngine.Object.DestroyImmediate(root);
                    }
                }
                if (ownsAssetRoot)
                {
                    Assert.IsTrue(assetRoot.StartsWith("Assets/__McpGameObjectLifecycleIntegrity_", StringComparison.Ordinal));
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Only the exact folder created by this fixture is removed.");
                }
            }
            finally
            {
                if (originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                if (ownedScene.IsValid() && ownedScene.isLoaded)
                    EditorSceneManager.CloseScene(ownedScene, true);
                if (secondaryScene.IsValid() && secondaryScene.isLoaded)
                    EditorSceneManager.CloseScene(secondaryScene, true);
                Selection.objects = originalSelection;
                Selection.activeObject = originalActiveObject;
            }
        }

        private GameObject Owned(string label)
        {
            var go = new GameObject(prefix + label);
            Assert.AreEqual(ownedScene, go.scene);
            return go;
        }

        private GameObject[] Objects() =>
            new[] { ownedScene, secondaryScene }
                .Where(scene => scene.IsValid() && scene.isLoaded)
                .SelectMany(scene => scene.GetRootGameObjects())
                .SelectMany(root => root.GetComponentsInChildren<Transform>(true))
                .Select(transform => transform.gameObject)
                .ToArray();

        private static JObject Call(JObject request) => JObject.FromObject(ManageGameObject.HandleCommand(request));

        private static void Succeeded(JObject response) => Assert.IsTrue(response.Value<bool>("success"), response.ToString());

        private JObject Create(string name = null) => new JObject { ["action"] = "create", ["name"] = name ?? prefix + "Created" };

        private JObject Duplicate(GameObject source) =>
            new JObject
            {
                ["action"] = "duplicate",
                ["target"] = source.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
            };

        private GameObject ResponseObject(JObject response, bool duplicate = false)
        {
            var data = duplicate ? response["data"]["duplicatedObject"] : response["data"];
            return Objects().Single(go => go.GetInstanceIDCompat() == data.Value<int>("instanceID"));
        }

        private void CreateAssetRoot()
        {
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot));
            string guid = AssetDatabase.CreateFolder("Assets", assetRoot.Substring("Assets/".Length));
            ownsAssetRoot = !string.IsNullOrEmpty(guid);
            Assert.IsTrue(ownsAssetRoot && AssetDatabase.IsValidFolder(assetRoot));
            Assert.AreEqual(assetRoot, AssetDatabase.GUIDToAssetPath(guid));
        }

        [TestCase("missing")]
        [TestCase("empty")]
        [TestCase("null")]
        public void RejectedParent_PreservesExistingObjectsAndSelection(string kind)
        {
            var existing = Owned("Existing");
            Selection.activeGameObject = existing;
            var before = Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();
            var request = Create();
            request["parent"] = kind == "null" ? JValue.CreateNull() : new JValue(kind == "empty" ? "" : prefix + "Missing");
            var response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"));
            CollectionAssert.AreEqual(before, Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray());
            Assert.AreSame(existing, Selection.activeGameObject);
        }

        [Test]
        public void ParentCannotResolveObjectBeingCreated()
        {
            var request = Create();
            request["parent"] = request["name"].DeepClone();
            Assert.IsFalse(Call(request).Value<bool>("success"));
            Assert.IsEmpty(Objects());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ExistingSameNameParent_IsResolvedBeforeCreation(bool inactive)
        {
            var parent = Owned("Parent");
            parent.SetActive(!inactive);
            var request = Create(parent.name);
            request["parent"] = parent.name;
            var response = Call(request);
            Succeeded(response);
            var child = ResponseObject(response);
            Assert.AreNotSame(parent, child);
            Assert.AreSame(parent.transform, child.transform.parent);
        }

        [TestCase("omitted")]
        [TestCase("empty")]
        [TestCase("null")]
        public void MissingPrefabPath_FailsWithoutCreatingObjects(string kind)
        {
            var request = Create();
            request["saveAsPrefab"] = true;
            if (kind != "omitted")
                request["prefabPath"] = kind == "null" ? JValue.CreateNull() : new JValue("");
            var response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("prefabPath", response.Value<string>("error"));
            Assert.IsEmpty(Objects());
        }

        [TestCase("rooted")]
        [TestCase("traversal")]
        [TestCase("invalid_leaf")]
        [TestCase("invalid_ancestor")]
        public void HostilePrefabPath_IsRejectedBeforeObjectsTagsSelectionOrDirectoriesChange(string kind)
        {
            CreateAssetRoot();
            string relative =
                kind == "traversal" ? assetRoot + "/First/../Rejected/New.prefab"
                : kind == "invalid_leaf" ? assetRoot + "/Rejected/Bad?Name.prefab"
                : kind == "invalid_ancestor" ? assetRoot + "/Bad?Directory/New.prefab"
                : assetRoot + "/Rejected/New.prefab";
            string projectRoot = System.IO.Path.GetDirectoryName(Application.dataPath);
            string path = kind == "rooted" ? System.IO.Path.Combine(projectRoot, relative) : relative;
            var existing = Owned("Existing");
            Selection.activeGameObject = existing;
            var before = Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();
            string[] tags = UnityEditorInternal.InternalEditorUtility.tags;
            var request = Create();
            request["saveAsPrefab"] = true;
            request["prefabPath"] = path;
            request["tag"] = prefix + "UncreatedTag";
            var response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.StartsWith("Invalid prefab path:", response.Value<string>("error"));
            CollectionAssert.AreEqual(before, Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray());
            CollectionAssert.AreEqual(tags, UnityEditorInternal.InternalEditorUtility.tags);
            Assert.AreSame(existing, Selection.activeGameObject);
            Assert.IsFalse(System.IO.Directory.Exists(System.IO.Path.Combine(projectRoot, assetRoot, "Rejected")));
            Assert.IsFalse(System.IO.Directory.Exists(System.IO.Path.Combine(projectRoot, assetRoot, "First")));
        }

        [TestCase("null")]
        [TestCase("short")]
        [TestCase("malformed")]
        public void NullableVectorFallback_RemainsSupported(string kind)
        {
            var request = Create();
            request["position"] =
                kind == "null" ? JValue.CreateNull()
                : kind == "short" ? new JArray(1)
                : new JArray(1, "bad", 3);
            var response = Call(request);
            Succeeded(response);
            Assert.AreEqual(Vector3.zero, ResponseObject(response).transform.localPosition);
        }

        [Test]
        public void ValidVector_DefaultZeroAndExtraArrayElementsRemainSupported()
        {
            var request = Create();
            request["position"] = new JArray("0", -1, 2, 99);
            request["scale"] = new JObject
            {
                ["x"] = 0,
                ["y"] = 1,
                ["z"] = 2,
            };
            var response = Call(request);
            Succeeded(response);
            var go = ResponseObject(response);
            Assert.AreEqual(new Vector3(0, -1, 2), go.transform.localPosition);
            Assert.AreEqual(new Vector3(0, 1, 2), go.transform.localScale);
        }

        [Test]
        public void InitialComponents_BelongToNewSameNameObject()
        {
            var old = Owned("SameName");
            var request = Create(old.name);
            request["componentsToAdd"] = new JArray("BoxCollider", "Rigidbody");
            var response = Call(request);
            Succeeded(response);
            var created = ResponseObject(response);
            Assert.AreNotSame(old, created);
            Assert.IsNull(old.GetComponent<BoxCollider>());
            Assert.IsNull(old.GetComponent<Rigidbody>());
            Assert.IsNotNull(created.GetComponent<BoxCollider>());
            Assert.IsNotNull(created.GetComponent<Rigidbody>());
        }

        [Test]
        public void UnknownLaterComponent_DestroysOnlyNewObject()
        {
            var old = Owned("SameName");
            var request = Create(old.name);
            request["componentsToAdd"] = new JArray("BoxCollider", prefix + "MissingComponent");
            Assert.IsFalse(Call(request).Value<bool>("success"));
            CollectionAssert.AreEqual(new[] { old }, Objects());
            Assert.IsNull(old.GetComponent<BoxCollider>());
        }

        [Test]
        public void SavedPrefab_ReturnsConnectedSceneInstanceAndAssetPath()
        {
            CreateAssetRoot();
            string path = assetRoot + "/Fixture.prefab";
            var request = Create();
            request["saveAsPrefab"] = true;
            request["prefabPath"] = path;
            var response = Call(request);
            Succeeded(response);
            var instance = ResponseObject(response);
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.IsNotNull(asset);
            Assert.IsTrue(AssetDatabase.Contains(asset));
            Assert.IsFalse(AssetDatabase.Contains(instance));
            Assert.AreNotEqual(asset.GetInstanceIDCompat(), instance.GetInstanceIDCompat());
            Assert.AreEqual(ownedScene, instance.scene);
            Assert.AreSame(instance, Selection.activeGameObject);
            Assert.AreEqual(PrefabInstanceStatus.Connected, PrefabUtility.GetPrefabInstanceStatus(instance));
            Assert.AreSame(asset, PrefabUtility.GetCorrespondingObjectFromSource(instance));
            StringAssert.Contains(path, response.Value<string>("message"));

            var instantiate = Create(prefix + "Instantiated");
            instantiate["prefabPath"] = path;
            var instantiatedResponse = Call(instantiate);
            Succeeded(instantiatedResponse);
            var secondInstance = ResponseObject(instantiatedResponse);
            Assert.AreNotSame(instance, secondInstance);
            Assert.AreSame(asset, PrefabUtility.GetCorrespondingObjectFromSource(secondInstance));
            Assert.AreSame(secondInstance, Selection.activeGameObject);
        }

        [TestCase("missing")]
        [TestCase("null")]
        [TestCase("empty")]
        public void DuplicateExplicitParentFallback_RemainsAtRoot(string kind)
        {
            var source = Owned("Source");
            var request = Duplicate(source);
            request["parent"] = kind == "null" ? JValue.CreateNull() : new JValue(kind == "empty" ? "" : prefix + "Missing");
            var response = Call(request);
            Succeeded(response);
            Assert.IsNull(ResponseObject(response, true).transform.parent);
        }

        [Test]
        public void DuplicateParentLookup_CannotSelectNewClone()
        {
            var source = Owned("Source");
            var request = Duplicate(source);
            request["new_name"] = prefix + "Copy";
            request["parent"] = request["new_name"].DeepClone();
            var response = Call(request);
            Succeeded(response);
            Assert.IsNull(ResponseObject(response, true).transform.parent);
        }

        [Test]
        public void DuplicateExistingSameNameParent_RetainsIdentityAndWorldPosition()
        {
            var source = Owned("Source");
            source.transform.position = new Vector3(1, 2, 3);
            var parent = Owned("Copy");
            parent.transform.position = new Vector3(10, 0, 0);
            var request = Duplicate(source);
            request["new_name"] = parent.name;
            request["parent"] = parent.name;
            request["offset"] = new JArray(0, -1, 2);
            var response = Call(request);
            Succeeded(response);
            var clone = ResponseObject(response, true);
            Assert.AreSame(parent.transform, clone.transform.parent);
            Assert.AreEqual(new Vector3(1, 1, 5), clone.transform.position);
            Assert.IsTrue(clone.scene.isDirty);
        }

        [Test]
        public void DuplicateOmittedParent_PreservesOriginalParentAndWorldPosition()
        {
            var parent = Owned("Parent");
            parent.transform.position = new Vector3(10, 0, 0);
            var source = Owned("Source");
            source.transform.SetParent(parent.transform, true);
            source.transform.position = new Vector3(1, 2, 3);
            var response = Call(Duplicate(source));
            Succeeded(response);
            var clone = ResponseObject(response, true);
            Assert.AreSame(parent.transform, clone.transform.parent);
            Assert.AreEqual(source.transform.position, clone.transform.position);
        }

        [Test]
        public void Duplicate_DirtiesCloneSceneInsteadOfUnrelatedActiveScene()
        {
            var parent = Owned("Parent");
            var source = Owned("Source");
            source.transform.SetParent(parent.transform, true);
            secondaryScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(secondaryScene);
            string path = "/" + parent.name + "/" + source.name;
            Assert.AreSame(source, GameObject.Find(path), "Explicit path must find the owned source outside the active scene.");

            var clear = typeof(EditorSceneManager).GetMethod("ClearSceneDirtiness", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(clear, "This future native control requires the internal scene dirtiness reset API.");
            clear.Invoke(null, new object[] { ownedScene });
            clear.Invoke(null, new object[] { secondaryScene });
            Assert.IsFalse(ownedScene.isDirty);
            Assert.IsFalse(secondaryScene.isDirty);

            var request = Duplicate(source);
            request["target"] = path;
            request["searchMethod"] = "by_path";
            var response = Call(request);
            Succeeded(response);
            var clone = ResponseObject(response, true);
            Assert.AreSame(parent.transform, clone.transform.parent);
            Assert.AreEqual(ownedScene, clone.scene, "The inherited owned parent must place the clone outside the active scene for this regression.");
            Assert.AreNotEqual(secondaryScene, clone.scene);
            Assert.IsTrue(clone.scene.isDirty);
            Assert.IsFalse(secondaryScene.isDirty, "An unrelated active scene must remain clean.");
        }

        [Test]
        public void DeleteExplicitNumericName_PreservesObjectWithMatchingId()
        {
            var idObject = Owned("Other");
            var numericName = Owned("Numeric");
            Assert.IsNull(GameObject.Find(idObject.GetInstanceIDCompat().ToString()), "Numeric-name collision must be created only by this fixture.");
            numericName.name = idObject.GetInstanceIDCompat().ToString();
            var response = Call(
                new JObject
                {
                    ["action"] = "delete",
                    ["target"] = numericName.name,
                    ["searchMethod"] = "by_name",
                }
            );
            Succeeded(response);
            Assert.IsTrue(numericName == null);
            CollectionAssert.AreEqual(new[] { idObject }, Objects());
        }
    }
}
