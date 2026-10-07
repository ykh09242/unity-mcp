using System;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools.GameObjects;
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
    public class GameObjectLifecycleIntegrityTests
    {
        private Scene originalScene;
        private readonly PrefabTestSceneFixture sceneFixture = new PrefabTestSceneFixture();
        private Scene ownedScene;
        private Scene secondaryScene;
        private UnityEngine.Object[] originalSelection;
        private UnityEngine.Object originalActiveObject;
        private string prefix;
        private string assetRoot;
        private bool ownsAssetRoot;
        private string assetRootGuid;

        [OneTimeSetUp]
        public void OneTimeSetUp() => sceneFixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void OneTimeTearDown() => sceneFixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            originalScene = SceneManager.GetActiveScene();
            originalSelection = Selection.objects;
            originalActiveObject = Selection.activeObject;
            ownsAssetRoot = false;
            assetRootGuid = null;
            ownedScene = default;
            secondaryScene = default;
            prefix = "LifecycleIntegrity_" + Guid.NewGuid().ToString("N");
            assetRoot = "Assets/__McpGameObjectLifecycleIntegrity_" + Guid.NewGuid().ToString("N");
            ownedScene = sceneFixture.Create("McpGameObjectLifecycle_", Guid.NewGuid().ToString("N"));
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
                    Assert.AreEqual(assetRootGuid, AssetDatabase.AssetPathToGUID(assetRoot), "Only the folder owned by this fixture may be removed.");
                    Assert.IsTrue(AssetDatabase.DeleteAsset(assetRoot), "Only the exact folder created by this fixture is removed.");
                }
            }
            finally
            {
                if (originalScene.IsValid() && originalScene.isLoaded)
                    SceneManager.SetActiveScene(originalScene);
                if (secondaryScene.IsValid() && secondaryScene.isLoaded)
                    EditorSceneManager.CloseScene(secondaryScene, true);
                sceneFixture.Close();
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
            assetRootGuid = guid;
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

        [Test]
        public void NullVector_RemainsOptional()
        {
            var request = Create();
            request["position"] = JValue.CreateNull();
            var response = Call(request);
            Succeeded(response);
            Assert.AreEqual(Vector3.zero, ResponseObject(response).transform.localPosition);
        }

        private static System.Collections.Generic.IEnumerable<TestCaseData> InvalidMutationInputs()
        {
            foreach (string field in new[] { "position", "rotation", "scale" })
            foreach (string value in new[] { "[1]", "[1,true,3]", "[1,\"bad\",3]", "[1,1e100,3]", "{\"x\":1,\"y\":2}", "false" })
                yield return new TestCaseData(field, value);
            foreach (
                string value in new[]
                {
                    "true",
                    "{}",
                    "[\"BoxCollider\",42]",
                    "[\"BoxCollider\",{}]",
                    "[\"BoxCollider\",\"\"]",
                    "[{\"typeName\":true}]",
                    "[{\"typeName\":\"BoxCollider\",\"properties\":[]}]",
                }
            )
                yield return new TestCaseData("componentsToAdd", value);
            foreach (string value in new[] { "[]", "true", "{\"Transform\":null}", "{\"Transform\":[]}", "{\"\":{}}", "\"{bad\"", "\"[]\"" })
                yield return new TestCaseData("componentProperties", value);
        }

        [TestCaseSource(nameof(InvalidMutationInputs))]
        public void InvalidCreateMutationInput_PreservesObjectsAssetsAndSelection(string field, string json)
        {
            CreateAssetRoot();
            var existing = Owned("Existing");
            Selection.activeGameObject = existing;
            var before = Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();
            int dirtyCount = EditorUtility.GetDirtyCount(existing);
            var request = Create();
            request["saveAsPrefab"] = true;
            request["prefabPath"] = assetRoot + "/Nested/Rejected.prefab";
            request[field] = JToken.Parse(json);

            var response = Call(request);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(before, Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray());
            Assert.AreSame(existing, Selection.activeGameObject);
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(existing));
            Assert.IsFalse(AssetDatabase.IsValidFolder(assetRoot + "/Nested"), "Rejected creation must not leave even an empty asset folder.");
            Assert.IsFalse(System.IO.Directory.Exists(assetRoot + "/Nested"));
        }

        [TestCaseSource(nameof(InvalidMutationInputs))]
        [TestCase("componentsToRemove", "true")]
        [TestCase("componentsToRemove", "[\"BoxCollider\",{}]")]
        [TestCase("componentsToRemove", "[\"BoxCollider\",\"\"]")]
        public void InvalidModifyMutationInput_PreservesEarlierFields(string field, string json)
        {
            var target = Owned("Target");
            var parent = Owned("Parent");
            var collider = target.AddComponent<BoxCollider>();
            string originalName = target.name;
            Selection.activeGameObject = parent;
            int goDirty = EditorUtility.GetDirtyCount(target);
            int transformDirty = EditorUtility.GetDirtyCount(target.transform);
            var request = new JObject
            {
                ["action"] = "modify",
                ["target"] = target.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
                ["name"] = prefix + "Renamed",
                ["parent"] = parent.GetInstanceIDCompat(),
                ["setActive"] = false,
                ["position"] = new JArray(1, 2, 3),
            };
            request[field] = JToken.Parse(json);

            var response = Call(request);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(originalName, target.name);
            Assert.IsNull(target.transform.parent);
            Assert.IsTrue(target.activeSelf);
            Assert.AreEqual(Vector3.zero, target.transform.localPosition);
            Assert.AreEqual(Vector3.zero, target.transform.localEulerAngles);
            Assert.AreEqual(Vector3.one, target.transform.localScale);
            CollectionAssert.AreEqual(new Component[] { target.transform, collider }, target.GetComponents<Component>());
            Assert.AreEqual(goDirty, EditorUtility.GetDirtyCount(target));
            Assert.AreEqual(transformDirty, EditorUtility.GetDirtyCount(target.transform));
            Assert.AreSame(parent, Selection.activeGameObject);
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

        [TestCase("create", "null")]
        [TestCase("create", "empty")]
        [TestCase("create", "properties")]
        [TestCase("modify", "null")]
        [TestCase("modify", "empty")]
        [TestCase("modify", "properties")]
        public void ValidComponentParameterShapes_RemainSupported(string action, string kind)
        {
            GameObject target = action == "modify" ? Owned("Target") : null;
            var request =
                action == "create"
                    ? Create()
                    : new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                    };
            if (kind == "null")
            {
                request["componentsToAdd"] = JValue.CreateNull();
                request["componentsToRemove"] = JValue.CreateNull();
                request["componentProperties"] = JValue.CreateNull();
            }
            else if (kind == "empty")
            {
                request["componentsToAdd"] = new JArray();
                request["componentsToRemove"] = new JArray();
                request["componentProperties"] = new JObject();
            }
            else
            {
                request["componentsToAdd"] = new JArray(new JObject { ["typeName"] = "BoxCollider", ["properties"] = JValue.CreateNull() });
                request["componentProperties"] = "{\"BoxCollider\":{\"isTrigger\":true}}";
            }
            var response = Call(request);
            Succeeded(response);
            var result = target != null ? target : ResponseObject(response);
            if (kind == "properties")
                Assert.IsTrue(result.GetComponent<BoxCollider>().isTrigger);
            else
                CollectionAssert.AreEqual(new Component[] { result.transform }, result.GetComponents<Component>());
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

        [TestCase("omitted")]
        [TestCase("existing")]
        [TestCase("null")]
        [TestCase("empty")]
        [TestCase("missing")]
        public void DuplicateChild_PreservesWorldTransformForEveryParentMode(string parentMode)
        {
            var originalParent = Owned("OriginalParent");
            originalParent.transform.SetPositionAndRotation(new Vector3(10, 0, 0), Quaternion.Euler(0, 30, 0));
            originalParent.transform.localScale = Vector3.one * 2;
            var otherParent = Owned("OtherParent");
            otherParent.transform.SetPositionAndRotation(new Vector3(-4, 0, 5), Quaternion.Euler(0, 90, 0));
            otherParent.transform.localScale = Vector3.one * 0.5f;
            var source = Owned("Source");
            source.transform.SetParent(originalParent.transform, false);
            source.transform.SetPositionAndRotation(new Vector3(1, 2, 3), Quaternion.Euler(0, 45, 0));
            source.transform.localScale = new Vector3(0.5f, 1, 1.5f);
            var request = Duplicate(source);
            if (parentMode != "omitted")
                request["parent"] =
                    parentMode == "null" ? JValue.CreateNull()
                    : parentMode == "empty" ? new JValue("")
                    : parentMode == "existing" ? new JValue(otherParent.GetInstanceIDCompat())
                    : new JValue(prefix + "Missing");
            var response = Call(request);
            Succeeded(response);
            var clone = ResponseObject(response, true);
            Assert.AreEqual(
                parentMode == "omitted" ? originalParent.transform
                    : parentMode == "existing" ? otherParent.transform
                    : null,
                clone.transform.parent
            );
            Assert.Less(Vector3.Distance(source.transform.position, clone.transform.position), 0.0001f);
            Assert.Less(Quaternion.Angle(source.transform.rotation, clone.transform.rotation), 0.01f);
            Assert.Less(Vector3.Distance(source.transform.lossyScale, clone.transform.lossyScale), 0.0001f);
        }

        [Test]
        public void Duplicate_DirtiesCloneSceneInsteadOfUnrelatedActiveScene()
        {
            CreateAssetRoot();
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
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
