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
using UnityEngine.TestTools;
#if UNITY_6000_0_OR_NEWER
using Material3D = UnityEngine.PhysicsMaterial;
#else
using Material3D = UnityEngine.PhysicMaterial;
#endif

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

        [TestCase("by_id", true)]
        [TestCase("by_name", true)]
        [TestCase("by_path", true)]
        [TestCase("by_id", false)]
        [TestCase("by_name", false)]
        [TestCase("by_path", false)]
        public void DeleteInactiveChildResolvesWithoutActivationAndUndoes(string method, bool inactiveParent)
        {
            var parent = Owned("InactiveDeleteParent");
            var child = Owned("InactiveDeleteChild");
            child.transform.SetParent(parent.transform);
            child.AddComponent<InactiveDeleteEnableProbe>();
            if (inactiveParent)
                parent.SetActive(false);
            else
                child.SetActive(false);
            InactiveDeleteEnableProbe.EnableCalls = 0;
            int id = child.GetInstanceIDCompat();
            string childName = child.name;
            var request = new JObject
            {
                ["action"] = "delete",
                ["searchMethod"] = method,
                ["target"] =
                    method == "by_id" ? (JToken)id
                    : method == "by_path" ? parent.name + "/" + child.name
                    : child.name,
            };

            var result = Call(request);
            Succeeded(result);
            Assert.That(((JArray)result["data"]).Select(item => item.Value<int>("instanceID")), Does.Contain(id));
            Assert.IsTrue(child == null);
            Assert.AreEqual(!inactiveParent, parent.activeSelf);
            Assert.AreEqual(0, InactiveDeleteEnableProbe.EnableCalls, "Lookup must not activate an inactive target or ancestor.");
            Undo.PerformUndo();
            var restored = parent.transform.Cast<Transform>().Single(t => t.name == childName).gameObject;
            Assert.IsFalse(restored.activeInHierarchy);
            Assert.AreEqual(inactiveParent, restored.activeSelf);
            Assert.AreEqual(0, InactiveDeleteEnableProbe.EnableCalls);
        }

        [Test]
        public void DeleteByNameRetainsBulkScopeAcrossActiveAndInactiveMatches()
        {
            var active = Owned("BulkDelete");
            var inactive = Owned("BulkDelete");
            inactive.SetActive(false);
            var other = Owned("BulkDeleteOther");
            int[] ids = { active.GetInstanceIDCompat(), inactive.GetInstanceIDCompat() };
            var result = Call(
                new JObject
                {
                    ["action"] = "delete",
                    ["target"] = active.name,
                    ["searchMethod"] = "by_name",
                }
            );
            Succeeded(result);
            CollectionAssert.AreEquivalent(ids, ((JArray)result["data"]).Select(item => item.Value<int>("instanceID")).ToArray());
            Assert.IsTrue(active == null && inactive == null);
            Assert.IsTrue(other != null);
            Undo.PerformUndo();
            var restored = Objects().Where(go => go.name == prefix + "BulkDelete").ToArray();
            Assert.AreEqual(2, restored.Length);
            Assert.AreEqual(1, restored.Count(go => !go.activeSelf));
        }

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

        [Test]
        public void RejectedParent_PreservesExistingObjectsAndSelection()
        {
            var existing = Owned("Existing");
            Selection.activeGameObject = existing;
            var before = Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();
            var request = Create();
            request["parent"] = prefix + "Missing";
            var response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"));
            CollectionAssert.AreEqual(before, Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray());
            Assert.AreSame(existing, Selection.activeGameObject);
        }

        [TestCase("omitted")]
        [TestCase("empty")]
        [TestCase("null")]
        public void EmptyOrNullParent_CreatesAtRoot(string kind)
        {
            var existing = Owned("Existing");
            var request = Create();
            if (kind != "omitted")
                request["parent"] = kind == "null" ? JValue.CreateNull() : new JValue("");
            var response = Call(request);
            Succeeded(response);
            var created = ResponseObject(response);
            Assert.IsNull(created.transform.parent);
            Assert.AreEqual(ownedScene, created.scene);
            Assert.AreSame(created, Selection.activeGameObject);
            Assert.IsTrue(existing != null);
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
        [TestCase("componentsToAdd", "[\"NoSuchComponent_ValidationAudit\"]")]
        [TestCase("componentsToAdd", "[\"BoxCollider\",\"NoSuchComponent_ValidationAudit\"]")]
        [TestCase("componentsToAdd", "[\"Transform\"]")]
        [TestCase("componentsToAdd", "[\"MCPForUnityTests.Editor.Tools.LifecycleAbstractComponent\"]")]
        [TestCase("componentsToRemove", "[\"Transform\"]")]
        [TestCase("componentsToRemove", "[\"BoxCollider\",\"Transform\"]")]
        [TestCase("componentsToRemove", "[\"NoSuchComponent_ValidationAudit\"]")]
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

        [TestCase("material.NoSuchField_ValidationAudit", "1")]
        [TestCase("material._NoSuchShaderProperty_ValidationAudit", "1")]
        [TestCase("material._Color", "\"invalid_color\"")]
        [TestCase("materials[0]._NoSuchShaderProperty_ValidationAudit", "1")]
        [TestCase("materials[0]._Color", "\"invalid_color\"")]
        [TestCase("materials[99].color", "{\"r\":1,\"g\":0,\"b\":0,\"a\":1}")]
        public void InvalidNestedMaterialProperty_DoesNotInstantiateMaterial(string property, string json)
        {
            var target = Owned("MaterialTarget");
            var renderer = target.AddComponent<MeshRenderer>();
            var source = new Material(Shader.Find("Sprites/Default"));
            renderer.sharedMaterial = source;
            try
            {
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentProperties"] = new JObject { ["MeshRenderer"] = new JObject { [property] = JToken.Parse(json) } },
                    }
                );

                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreSame(source, renderer.sharedMaterial);
            }
            finally
            {
                if (renderer.sharedMaterial != source && renderer.sharedMaterial != null)
                    UnityEngine.Object.DestroyImmediate(renderer.sharedMaterial);
                renderer.sharedMaterial = null;
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [TestCase("NoSuchProperty_ValidationAudit")]
        [TestCase("m_NoSuchProperty_ValidationAudit")]
        [TestCase("transform.NoSuchProperty_ValidationAudit")]
        public void InvalidAddedComponentProperty_DoesNotLeaveRequiredDependencies(string property)
        {
            var target = Owned("DependencyTarget");
            var before = target.GetComponents<Component>();

            var response = Call(
                new JObject
                {
                    ["action"] = "modify",
                    ["target"] = target.GetInstanceIDCompat(),
                    ["searchMethod"] = "by_id",
                    ["componentsToAdd"] = new JArray(
                        new JObject
                        {
                            ["typeName"] = "HingeJoint",
                            ["properties"] = new JObject { [property] = 1 },
                        }
                    ),
                }
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(before, target.GetComponents<Component>());
        }

        [TestCase("mesh.NoSuchProperty_ValidationAudit", "1")]
        [TestCase("mesh.vertices", "[[1,2]]")]
        [TestCase("mesh.vertices[999].x", "1")]
        public void InvalidNestedMeshProperty_DoesNotInstantiateMesh(string property, string json)
        {
            var target = Owned("BorrowedMesh");
            var filter = target.AddComponent<MeshFilter>();
            var source = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up }, triangles = new[] { 0, 1, 2 } };
            filter.sharedMesh = source;
            Mesh[] before = UnityEngine.Resources.FindObjectsOfTypeAll<Mesh>();
            bool ignoreLogs = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentProperties"] = new JObject { ["MeshFilter"] = new JObject { [property] = JToken.Parse(json) } },
                    }
                );
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(source, filter.sharedMesh);
                CollectionAssert.AreEquivalent(before, UnityEngine.Resources.FindObjectsOfTypeAll<Mesh>());
            }
            finally
            {
                LogAssert.ignoreFailingMessages = ignoreLogs;
                if (filter.sharedMesh != null && filter.sharedMesh != source)
                    UnityEngine.Object.DestroyImmediate(filter.sharedMesh);
                filter.sharedMesh = null;
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void ValidNestedMeshEditStillCreatesOwnedInstance()
        {
            var target = Owned("EditableMesh");
            var filter = target.AddComponent<MeshFilter>();
            var source = new Mesh { vertices = new[] { Vector3.zero, Vector3.right, Vector3.up }, triangles = new[] { 0, 1, 2 } };
            filter.sharedMesh = source;
            try
            {
                LogAssert.Expect(
                    LogType.Error,
                    "Instantiating mesh due to calling MeshFilter.mesh during edit mode. This will leak meshes. Please use MeshFilter.sharedMesh instead."
                );
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentProperties"] = new JObject
                        {
                            ["MeshFilter"] = new JObject { ["mesh.vertices"] = new JArray(new JArray(1, 0, 0), new JArray(0, 1, 0), new JArray(0, 0, 1)) },
                        },
                    }
                );
                Succeeded(response);
                Assert.AreNotEqual(source, filter.sharedMesh);
                Assert.AreEqual(Vector3.right, filter.sharedMesh.vertices[0]);
                Assert.AreEqual(Vector3.zero, source.vertices[0]);
            }
            finally
            {
                if (filter.sharedMesh != null && filter.sharedMesh != source)
                    UnityEngine.Object.DestroyImmediate(filter.sharedMesh);
                filter.sharedMesh = null;
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [TestCase("create")]
        [TestCase("modify")]
        public void NewComponentReference_CanTargetEarlierPlannedComponent(string action)
        {
            var target = action == "modify" ? Owned("SelfReference") : null;
            string name = prefix + "SelfReference";
            var request =
                action == "create"
                    ? Create(name)
                    : new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                    };
            request["componentsToAdd"] = new JArray(
                "Rigidbody",
                new JObject
                {
                    ["typeName"] = typeof(LifecycleReferenceComponent).FullName,
                    ["properties"] = new JObject
                    {
                        ["Body"] = new JObject { ["name"] = name, ["component"] = "Rigidbody" },
                    },
                }
            );
            var response = Call(request);
            Succeeded(response);
            if (action == "create")
                target = ResponseObject(response);
            Assert.AreEqual(target.GetComponent<Rigidbody>(), target.GetComponent<LifecycleReferenceComponent>().Body);
        }

        [TestCase("modify")]
        [TestCase("create")]
        public void InvalidNewComponentValue_PreservesEarlierFieldsAndTags(string action)
        {
            var target = Owned("BeforeRejectedAddition");
            string originalName = target.name;
            Component[] components = target.GetComponents<Component>();
            string[] tags = UnityEditorInternal.InternalEditorUtility.tags;
            string newTag = prefix + "RejectedTag";
            var request =
                action == "create"
                    ? Create()
                    : new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["name"] = prefix + "Renamed",
                        ["position"] = new JArray(4, 5, 6),
                    };
            request["tag"] = newTag;
            request["componentsToAdd"] = new JArray(
                new JObject
                {
                    ["typeName"] = "HingeJoint",
                    ["properties"] = new JObject { ["breakForce"] = "invalid_float" },
                }
            );
            bool ignoreLogs = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                var response = Call(request);
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(originalName, target.name);
                Assert.AreEqual(Vector3.zero, target.transform.localPosition);
                CollectionAssert.AreEqual(components, target.GetComponents<Component>());
                CollectionAssert.AreEqual(new[] { target }, Objects());
                CollectionAssert.AreEqual(tags, UnityEditorInternal.InternalEditorUtility.tags);
            }
            finally
            {
                LogAssert.ignoreFailingMessages = ignoreLogs;
                if (UnityEditorInternal.InternalEditorUtility.tags.Contains(newTag))
                    UnityEditorInternal.InternalEditorUtility.RemoveTag(newTag);
            }
        }

        [TestCase("existing_physics")]
        [TestCase("planned_physics")]
        [TestCase("required_physics")]
        [TestCase("existing_disallowed")]
        [TestCase("planned_disallowed")]
        public void RejectedAdditionPlan_PreservesEarlierFieldsAndComponents(string kind)
        {
            var target = Owned("AdditionPlan");
            string originalName = target.name;
            if (kind == "existing_physics" || kind == "required_physics")
                target.AddComponent<Rigidbody2D>();
            if (kind == "existing_disallowed")
                target.AddComponent<LifecycleSingleComponent>();
            var additions =
                kind == "planned_physics" ? new JArray("Rigidbody2D", "BoxCollider")
                : kind == "required_physics" ? new JArray("HingeJoint")
                : kind == "existing_physics" ? new JArray("BoxCollider")
                : kind == "planned_disallowed" ? new JArray(typeof(LifecycleSingleComponent).FullName, typeof(LifecycleSingleComponent).FullName)
                : new JArray(typeof(LifecycleSingleComponent).FullName);
            var components = target.GetComponents<Component>();
            var request = new JObject
            {
                ["action"] = "modify",
                ["target"] = target.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id",
                ["name"] = prefix + "Renamed",
                ["position"] = new JArray(4, 5, 6),
                ["componentsToAdd"] = additions,
            };
            bool ignoreLogs = LogAssert.ignoreFailingMessages;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                Assert.IsFalse(Call(request).Value<bool>("success"));
                Assert.AreEqual(originalName, target.name);
                Assert.AreEqual(Vector3.zero, target.transform.localPosition);
                CollectionAssert.AreEqual(components, target.GetComponents<Component>());
            }
            finally
            {
                LogAssert.ignoreFailingMessages = ignoreLogs;
            }
        }

        [Test]
        public void InvalidNativeAliasValue_DoesNotLeaveRequiredDependencies()
        {
            var target = Owned("NativeAliasTarget");
            var before = target.GetComponents<Component>();
            bool ignoreLogs = LogAssert.ignoreFailingMessages;
            JObject response;
            try
            {
                // Reflection conversion and native SerializedProperty validation log differently.
                // The contract here is the rejected value and exact component inventory.
                LogAssert.ignoreFailingMessages = true;
                response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentsToAdd"] = new JArray(
                            new JObject
                            {
                                ["typeName"] = "HingeJoint",
                                ["properties"] = new JObject { ["m_BreakForce"] = "invalid_float" },
                            }
                        ),
                    }
                );
            }
            finally
            {
                LogAssert.ignoreFailingMessages = ignoreLogs;
            }
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(before, target.GetComponents<Component>());
        }

        [Test]
        public void InvalidDeferredNativeProperty_DoesNotWriteEarlierBorrowedTransform()
        {
            var target = Owned("DeferredNativeProperty");
            CreateAssetRoot();
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
            Assert.IsFalse(ownedScene.isDirty);
            var before = target.GetComponents<Component>();
            var position = target.transform.position;
            int undoGroup = Undo.GetCurrentGroup();
            string originalName = target.name;
            string[] tags = UnityEditorInternal.InternalEditorUtility.tags;
            string newTag = prefix + "RejectedOpaqueFieldTag";
            int targetDirty = EditorUtility.GetDirtyCount(target);
            int transformDirty = EditorUtility.GetDirtyCount(target.transform);
            try
            {
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["name"] = prefix + "RejectedRename",
                        ["tag"] = newTag,
                        ["componentsToAdd"] = new JArray(
                            new JObject
                            {
                                ["typeName"] = "HingeJoint",
                                ["properties"] = new JObject { ["transform.position"] = new JArray(9, 8, 7), ["m_NoSuchProperty_ValidationAudit"] = 1 },
                            }
                        ),
                    }
                );
                TestContext.WriteLine(
                    $"Opaque native schema rejection: sceneDirty={ownedScene.isDirty}, undoGroupBefore={undoGroup}, undoGroupAfter={Undo.GetCurrentGroup()}."
                );
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(originalName, target.name);
                Assert.AreEqual(position, target.transform.position);
                CollectionAssert.AreEqual(before, target.GetComponents<Component>());
                CollectionAssert.AreEqual(tags, UnityEditorInternal.InternalEditorUtility.tags);
                Assert.IsFalse(ownedScene.isDirty);
                Assert.AreEqual(targetDirty, EditorUtility.GetDirtyCount(target));
                Assert.AreEqual(transformDirty, EditorUtility.GetDirtyCount(target.transform));
                Assert.AreEqual(undoGroup, Undo.GetCurrentGroup());
            }
            finally
            {
                if (UnityEditorInternal.InternalEditorUtility.tags.Contains(newTag))
                    UnityEditorInternal.InternalEditorUtility.RemoveTag(newTag);
            }
        }

        [Test]
        public void BorrowedNativeReference_UsesActualTransformSubtype()
        {
            var target = Owned("BorrowedProbeAnchor");
            var renderer = target.AddComponent<MeshRenderer>();
            var anchor = new GameObject(prefix + "RectAnchor", typeof(RectTransform)).GetComponent<RectTransform>();
            renderer.probeAnchor = anchor;
            var response = Call(
                new JObject
                {
                    ["action"] = "modify",
                    ["target"] = target.GetInstanceIDCompat(),
                    ["searchMethod"] = "by_id",
                    ["componentProperties"] = new JObject
                    {
                        ["MeshRenderer"] = new JObject
                        {
                            ["probeAnchor.anchoredPosition"] = new JObject { ["x"] = 12, ["y"] = 34 },
                        },
                    },
                }
            );
            Succeeded(response);
            Assert.AreEqual(new Vector2(12, 34), anchor.anchoredPosition);
            Assert.AreEqual(anchor, renderer.probeAnchor);
        }

        [Test]
        public void InvalidNestedColliderMaterial_PreservesSharedBindingAndInventory()
        {
            var target = Owned("BorrowedPhysicsMaterial");
            var first = target.AddComponent<BoxCollider>();
            var second = Owned("OtherPhysicsMaterialOwner").AddComponent<BoxCollider>();
            var source = new Material3D(prefix + "PhysicsMaterial");
            first.sharedMaterial = second.sharedMaterial = source;
            var before = UnityEngine.Resources.FindObjectsOfTypeAll<Material3D>();
            try
            {
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentProperties"] = new JObject { ["BoxCollider"] = new JObject { ["material.NoSuchField_ValidationAudit"] = 1 } },
                    }
                );
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(source, first.sharedMaterial);
                Assert.AreEqual(source, second.sharedMaterial);
                CollectionAssert.AreEquivalent(before, UnityEngine.Resources.FindObjectsOfTypeAll<Material3D>());
            }
            finally
            {
                if (first.sharedMaterial != null && first.sharedMaterial != source)
                    UnityEngine.Object.DestroyImmediate(first.sharedMaterial);
                first.sharedMaterial = second.sharedMaterial = null;
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void ValidNestedColliderMaterialEdit_RetainsOwnedCloneBehavior()
        {
            var target = Owned("EditablePhysicsMaterial");
            var first = target.AddComponent<BoxCollider>();
            var second = Owned("SharedPhysicsMaterialOwner").AddComponent<BoxCollider>();
            var source = new Material3D(prefix + "PhysicsMaterial") { dynamicFriction = 0.6f };
            first.sharedMaterial = second.sharedMaterial = source;
            try
            {
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["componentProperties"] = new JObject { ["BoxCollider"] = new JObject { ["material.dynamicFriction"] = 0.25f } },
                    }
                );
                Succeeded(response);
                Assert.AreNotEqual(source, first.sharedMaterial);
                Assert.AreEqual(0.25f, first.sharedMaterial.dynamicFriction);
                Assert.AreEqual(0.6f, source.dynamicFriction);
                Assert.AreEqual(source, second.sharedMaterial);
            }
            finally
            {
                if (first.sharedMaterial != null && first.sharedMaterial != source)
                    UnityEngine.Object.DestroyImmediate(first.sharedMaterial);
                first.sharedMaterial = second.sharedMaterial = null;
                UnityEngine.Object.DestroyImmediate(source);
            }
        }

        [Test]
        public void InvalidAddedComponentNestedOwner_PreservesEarlierFieldsAndCleanScene()
        {
            var target = Owned("NullAddedOwner");
            string originalName = target.name;
            CreateAssetRoot();
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
            var before = target.GetComponents<Component>();
            int group = Undo.GetCurrentGroup();
            var response = Call(
                new JObject
                {
                    ["action"] = "modify",
                    ["target"] = target.GetInstanceIDCompat(),
                    ["searchMethod"] = "by_id",
                    ["name"] = prefix + "RejectedOwnerRename",
                    ["componentsToAdd"] = new JArray(
                        new JObject
                        {
                            ["typeName"] = "MeshRenderer",
                            ["properties"] = new JObject { ["transform.position"] = new JArray(9, 8, 7), ["probeAnchor.name"] = "RejectedAnchorName" },
                        }
                    ),
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(originalName, target.name);
            Assert.AreEqual(Vector3.zero, target.transform.position);
            CollectionAssert.AreEqual(before, target.GetComponents<Component>());
            Assert.IsFalse(ownedScene.isDirty);
            Assert.AreEqual(group, Undo.GetCurrentGroup());
        }

        [Test]
        public void AddedComponentNestedOwner_UsesEarlierResolvedReference()
        {
            var target = Owned("ResolvedAddedOwner");
            var anchor = Owned("AssignedProbeAnchor").transform;
            var response = Call(
                new JObject
                {
                    ["action"] = "modify",
                    ["target"] = target.GetInstanceIDCompat(),
                    ["searchMethod"] = "by_id",
                    ["componentsToAdd"] = new JArray(
                        new JObject
                        {
                            ["typeName"] = "MeshRenderer",
                            ["properties"] = new JObject
                            {
                                ["probeAnchor"] = new JObject { ["instanceID"] = anchor.GetInstanceIDCompat() },
                                ["probeAnchor.name"] = prefix + "AcceptedAnchorName",
                            },
                        }
                    ),
                }
            );
            Succeeded(response);
            Assert.AreEqual(anchor, target.GetComponent<MeshRenderer>().probeAnchor);
            Assert.AreEqual(prefix + "AcceptedAnchorName", anchor.name);
        }

        [Test]
        public void AddedComponentIndexedOwner_UsesEarlierArrayAssignment()
        {
            AssertAddedComponentIndexedOwner(0, true);
        }

        [Test]
        public void InvalidAddedComponentIndexedOwner_PreservesEarlierFieldsAndCleanScene()
        {
            AssertAddedComponentIndexedOwner(1, false);
        }

        private void AssertAddedComponentIndexedOwner(int index, bool accepted)
        {
            var target = Owned("IndexedAddedOwner");
            var material = new Material(Shader.Find("Sprites/Default")) { name = prefix + "OriginalMaterial" };
            try
            {
                string originalName = target.name;
                CreateAssetRoot();
                Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, assetRoot + "/" + ownedScene.name + ".unity"));
                var before = target.GetComponents<Component>();
                var materialsBefore = UnityEngine.Resources.FindObjectsOfTypeAll<Material>();
                int group = Undo.GetCurrentGroup();
                var response = Call(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = target.GetInstanceIDCompat(),
                        ["searchMethod"] = "by_id",
                        ["name"] = prefix + "IndexedOwnerRename",
                        ["componentsToAdd"] = new JArray(
                            new JObject
                            {
                                ["typeName"] = "MeshRenderer",
                                ["properties"] = new JObject
                                {
                                    ["transform.position"] = new JArray(9, 8, 7),
                                    ["sharedMaterials"] = new JArray(new JObject { ["instanceID"] = material.GetInstanceIDCompat() }),
                                    [$"sharedMaterials[{index}].name"] = prefix + "AcceptedMaterialName",
                                },
                            }
                        ),
                    }
                );
                Assert.AreEqual(accepted, response.Value<bool>("success"), response.ToString());
                CollectionAssert.AreEquivalent(materialsBefore, UnityEngine.Resources.FindObjectsOfTypeAll<Material>());
                if (accepted)
                {
                    Assert.AreEqual(material, target.GetComponent<MeshRenderer>().sharedMaterials[0]);
                    Assert.AreEqual(prefix + "AcceptedMaterialName", material.name);
                    Assert.AreEqual(new Vector3(9, 8, 7), target.transform.position);
                }
                else
                {
                    Assert.AreEqual(originalName, target.name);
                    Assert.AreEqual(Vector3.zero, target.transform.position);
                    Assert.AreEqual(prefix + "OriginalMaterial", material.name);
                    CollectionAssert.AreEqual(before, target.GetComponents<Component>());
                    Assert.IsFalse(ownedScene.isDirty);
                    Assert.AreEqual(group, Undo.GetCurrentGroup());
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(material);
            }
        }

        [Test]
        public void ValidNativeAliasValue_RemainsSupportedOnAddedComponent()
        {
            var target = Owned("ValidAliasTarget");

            var response = Call(
                new JObject
                {
                    ["action"] = "modify",
                    ["target"] = target.GetInstanceIDCompat(),
                    ["searchMethod"] = "by_id",
                    ["componentsToAdd"] = new JArray(
                        new JObject
                        {
                            ["typeName"] = "HingeJoint",
                            ["properties"] = new JObject { ["m_BreakForce"] = 12.5 },
                        }
                    ),
                }
            );

            Succeeded(response);
            Assert.AreEqual(12.5f, target.GetComponent<HingeJoint>().breakForce);
            Assert.IsNotNull(target.GetComponent<Rigidbody>());
        }

        [Test]
        public void UnprovenNativeField_DoesNotReplaceExistingTransform()
        {
            var target = Owned("TransformIdentityTarget");
            var before = target.transform;

            var response = Call(
                new JObject
                {
                    ["action"] = "modify",
                    ["target"] = target.GetInstanceIDCompat(),
                    ["searchMethod"] = "by_id",
                    ["componentsToAdd"] = new JArray(
                        new JObject
                        {
                            ["typeName"] = "RectTransform",
                            ["properties"] = new JObject { ["m_NoSuchProperty_ValidationAudit"] = 1 },
                        }
                    ),
                }
            );

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(before, target.transform);
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

        [TestCase("position", "false")]
        [TestCase("position", "[1,2]")]
        [TestCase("position", "{\"x\":1,\"y\":2}")]
        [TestCase("offset", "false")]
        [TestCase("offset", "[1,2]")]
        [TestCase("offset", "{}")]
        [TestCase("new_name", "false")]
        [TestCase("new_name", "0")]
        [TestCase("new_name", "[]")]
        [TestCase("new_name", "{}")]
        public void DuplicateMalformedInputRejectsBeforeCloneSelectionAndSceneChange(string field, string json)
        {
            var source = Owned("Source");
            source.transform.position = new Vector3(1, 2, 3);
            var originalIds = Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray();
            var originalPosition = source.transform.position;
            var active = Selection.activeObject;
            int dirtyCount = EditorUtility.GetDirtyCount(source);
            bool dirtyScene = ownedScene.isDirty;
            var request = Duplicate(source);
            request[field] = JToken.Parse(json);
            if (field == "offset")
                request["position"] = new JArray(5, 6, 7);
            var response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(originalIds, Objects().Select(go => go.GetInstanceIDCompat()).OrderBy(id => id).ToArray());
            Assert.AreEqual(originalPosition, source.transform.position);
            Assert.IsTrue(active == Selection.activeObject);
            Assert.AreEqual(dirtyCount, EditorUtility.GetDirtyCount(source));
            Assert.AreEqual(dirtyScene, ownedScene.isDirty);
        }

        [TestCase("[0,0,0]", "[1,2,3]", 0, 0, 0)]
        [TestCase("null", "[\"1\",2,3,null]", 2, 4, 6)]
        [TestCase("null", "null", 1, 2, 3)]
        [TestCase("{\"x\":null,\"y\":2,\"z\":3}", "null", 0, 2, 3)]
        public void DuplicateOptionalVectorsRetainDefaultsCoordinatesAndPositionPrecedence(string position, string offset, float x, float y, float z)
        {
            var source = Owned("Source");
            source.transform.position = new Vector3(1, 2, 3);
            var request = Duplicate(source);
            request["position"] = JToken.Parse(position);
            request["offset"] = JToken.Parse(offset);
            request["new_name"] = JValue.CreateNull();
            var response = Call(request);
            Succeeded(response);
            var clone = ResponseObject(response, duplicate: true);
            Assert.AreEqual(source.name + "_Copy", clone.name);
            Assert.AreEqual(new Vector3(x, y, z), clone.transform.position);
        }

        private GameObject CreateDuplicatePrefab(out string prefabPath)
        {
            CreateAssetRoot();
            prefabPath = assetRoot + "/Duplicate.prefab";
            var template = Owned("PrefabTemplate");
            template.AddComponent<BoxCollider>().size = new Vector3(2, 3, 4);
            var child = Owned("PrefabChild");
            child.transform.SetParent(template.transform, false);
            child.AddComponent<SphereCollider>().radius = 2;
            var prefab = PrefabUtility.SaveAsPrefabAsset(template, prefabPath);
            Assert.IsNotNull(prefab);
            UnityEngine.Object.DestroyImmediate(template);
            return prefab;
        }

        private static void AssertDuplicatePrefab(GameObject clone, string prefabPath, bool overridden)
        {
            Assert.AreEqual(PrefabInstanceStatus.Connected, PrefabUtility.GetPrefabInstanceStatus(clone));
            Assert.AreEqual(prefabPath, AssetDatabase.GetAssetPath(PrefabUtility.GetCorrespondingObjectFromSource(clone)));
            Assert.AreEqual(overridden ? new Vector3(7, 8, 9) : new Vector3(2, 3, 4), clone.GetComponent<BoxCollider>().size);
            Assert.AreEqual(2, clone.transform.GetChild(0).GetComponent<SphereCollider>().radius);
            if (overridden)
            {
                Assert.AreEqual(13, clone.GetComponent<Rigidbody>().mass);
                Assert.IsNotNull(clone.transform.Find("InstanceOnlyChild"));
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DuplicatePrefabPreservesConnectionOverridesAndSourceUpdates(bool overridden)
        {
            var prefab = CreateDuplicatePrefab(out string path);
            var source = (GameObject)PrefabUtility.InstantiatePrefab(prefab, ownedScene);
            if (overridden)
            {
                source.GetComponent<BoxCollider>().size = new Vector3(7, 8, 9);
                PrefabUtility.RecordPrefabInstancePropertyModifications(source.GetComponent<BoxCollider>());
                source.AddComponent<Rigidbody>().mass = 13;
                var added = Owned("Added");
                added.name = "InstanceOnlyChild";
                added.transform.SetParent(source.transform, false);
            }
            var response = Call(Duplicate(source));
            Succeeded(response);
            var clone = ResponseObject(response, true);
            AssertDuplicatePrefab(clone, path, overridden);

            var contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.GetComponent<BoxCollider>().size = new Vector3(10, 11, 12);
                contents.transform.GetChild(0).GetComponent<SphereCollider>().radius = 5;
                Assert.IsNotNull(PrefabUtility.SaveAsPrefabAsset(contents, path));
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            Assert.AreEqual(overridden ? new Vector3(7, 8, 9) : new Vector3(10, 11, 12), clone.GetComponent<BoxCollider>().size);
            Assert.AreEqual(5, clone.transform.GetChild(0).GetComponent<SphereCollider>().radius);
            Assert.AreEqual(PrefabInstanceStatus.Connected, PrefabUtility.GetPrefabInstanceStatus(source));
        }

        [Test]
        public void DuplicatePrefabPreservesTextClipboardAndDuplicatesOnlyRequestedSelection()
        {
            var prefab = CreateDuplicatePrefab(out string path);
            var source = (GameObject)PrefabUtility.InstantiatePrefab(prefab, ownedScene);
            var unrelated = Owned("UnrelatedSelection");
            Selection.objects = new UnityEngine.Object[] { source, unrelated };
            string clipboard = EditorGUIUtility.systemCopyBuffer;
            string sentinel = prefix + "Clipboard";
            try
            {
                EditorGUIUtility.systemCopyBuffer = sentinel;
                var response = Call(Duplicate(source));
                Succeeded(response);
                var clone = ResponseObject(response, true);
                AssertDuplicatePrefab(clone, path, false);
                Assert.AreEqual(sentinel, EditorGUIUtility.systemCopyBuffer);
                CollectionAssert.AreEquivalent(new[] { source, unrelated, clone }, ownedScene.GetRootGameObjects());
                CollectionAssert.AreEqual(new[] { clone }, Selection.gameObjects);
            }
            finally
            {
                EditorGUIUtility.systemCopyBuffer = clipboard;
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DuplicatePrefabUndoRedoAndSaveReopenPreserveRequestedPlacement(bool prefabSource)
        {
            GameObject source;
            string path = null;
            if (prefabSource)
            {
                var prefab = CreateDuplicatePrefab(out path);
                source = (GameObject)PrefabUtility.InstantiatePrefab(prefab, ownedScene);
                source.GetComponent<BoxCollider>().size = new Vector3(7, 8, 9);
                PrefabUtility.RecordPrefabInstancePropertyModifications(source.GetComponent<BoxCollider>());
                source.AddComponent<Rigidbody>().mass = 13;
                var added = Owned("Added");
                added.name = "InstanceOnlyChild";
                added.transform.SetParent(source.transform, false);
            }
            else
            {
                CreateAssetRoot();
                source = Owned("RegularSource");
            }
            var originalParent = Owned("OriginalParent");
            source.transform.SetParent(originalParent.transform, true);
            source.transform.position = new Vector3(1, 2, 3);
            if (prefabSource)
                PrefabUtility.RecordPrefabInstancePropertyModifications(source.transform);
            var requestedParent = Owned("RequestedParent");
            requestedParent.transform.position = new Vector3(20, 30, 40);
            string name = prefix + "RequestedCopy";
            var request = Duplicate(source);
            request["new_name"] = name;
            request["parent"] = requestedParent.GetInstanceIDCompat();
            request["position"] = new JArray(4, 5, 6);
            request["offset"] = new JArray(100, 100, 100);
            Selection.objects = new UnityEngine.Object[] { source, originalParent };
            var response = Call(request);
            Succeeded(response);
            var clone = ResponseObject(response, true);
            Assert.AreSame(clone, Selection.activeGameObject);
            Assert.AreEqual(originalParent.transform, source.transform.parent);
            Assert.AreEqual(name, clone.name);
            Assert.AreEqual(requestedParent.transform, clone.transform.parent);
            Assert.AreEqual(new Vector3(4, 5, 6), clone.transform.position);
            if (prefabSource)
                AssertDuplicatePrefab(clone, path, true);
            Undo.FlushUndoRecordObjects();
            Undo.PerformUndo();
            Assert.IsTrue(clone == null);
            Assert.IsTrue(source != null);
            Undo.PerformRedo();
            clone = Objects().Single(go => go.name == name);
            Assert.AreEqual(requestedParent.transform, clone.transform.parent);
            Assert.AreEqual(new Vector3(4, 5, 6), clone.transform.position);
            if (prefabSource)
                AssertDuplicatePrefab(clone, path, true);
            else
                Assert.AreEqual(PrefabInstanceStatus.NotAPrefab, PrefabUtility.GetPrefabInstanceStatus(clone));

            string scenePath = assetRoot + "/" + ownedScene.name + ".unity";
            Assert.IsTrue(EditorSceneManager.SaveScene(ownedScene, scenePath));
            // Reopen only the exact saved fixture scene additively; never replace another scene.
            ownedScene = sceneFixture.ReopenSavedScene();
            clone = Objects().Single(go => go.name == name);
            Assert.AreEqual(prefix + "RequestedParent", clone.transform.parent.name);
            Assert.AreEqual(new Vector3(4, 5, 6), clone.transform.position);
            if (prefabSource)
                AssertDuplicatePrefab(clone, path, true);
        }

        [TestCase("ko-KR", "2026-10-09T11:12:13Z")]
        [TestCase("en-US", "2026-10-09T11:12:13+09:00")]
        public void DuplicateParsedDateNameRetainsLegacyStringConversion(string culture, string name)
        {
            var previous = System.Globalization.CultureInfo.CurrentCulture;
            var previousUi = System.Globalization.CultureInfo.CurrentUICulture;
            try
            {
                System.Globalization.CultureInfo.CurrentCulture = System.Globalization.CultureInfo.GetCultureInfo(culture);
                System.Globalization.CultureInfo.CurrentUICulture = System.Globalization.CultureInfo.GetCultureInfo(culture);
                var source = Owned("Source");
                var request = Duplicate(source);
                request["new_name"] = name;
                var command = Newtonsoft.Json.JsonConvert.DeserializeObject<MCPForUnity.Editor.Models.Command>(
                    new JObject { ["type"] = "manage_gameobject", ["params"] = request }.ToString()
                );
                Assert.AreEqual(JTokenType.Date, command.@params["new_name"].Type);
                string expected = command.@params["new_name"].ToString();
                var response = Call(command.@params);
                Succeeded(response);
                Assert.AreEqual(expected, ResponseObject(response, duplicate: true).name);
            }
            finally
            {
                System.Globalization.CultureInfo.CurrentCulture = previous;
                System.Globalization.CultureInfo.CurrentUICulture = previousUi;
            }
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

        [TestCase("omitted", false)]
        [TestCase("existing", false)]
        [TestCase("null", false)]
        [TestCase("empty", false)]
        [TestCase("missing", false)]
        [TestCase("omitted", true)]
        [TestCase("existing", true)]
        [TestCase("null", true)]
        [TestCase("empty", true)]
        [TestCase("missing", true)]
        public void DuplicateChild_PreservesWorldTransformForEveryParentMode(string parentMode, bool prefabSource)
        {
            var originalParent = Owned("OriginalParent");
            originalParent.transform.SetPositionAndRotation(new Vector3(10, 0, 0), Quaternion.Euler(0, 30, 0));
            originalParent.transform.localScale = Vector3.one * 2;
            var otherParent = Owned("OtherParent");
            otherParent.transform.SetPositionAndRotation(new Vector3(-4, 0, 5), Quaternion.Euler(0, 90, 0));
            otherParent.transform.localScale = Vector3.one * 0.5f;
            var source = prefabSource ? (GameObject)PrefabUtility.InstantiatePrefab(CreateDuplicatePrefab(out _), ownedScene) : Owned("Source");
            source.transform.SetParent(originalParent.transform, false);
            source.transform.SetPositionAndRotation(new Vector3(1, 2, 3), Quaternion.Euler(0, 45, 0));
            source.transform.localScale = new Vector3(0.5f, 1, 1.5f);
            if (prefabSource)
                PrefabUtility.RecordPrefabInstancePropertyModifications(source.transform);
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
            if (prefabSource)
                Assert.AreEqual(PrefabInstanceStatus.Connected, PrefabUtility.GetPrefabInstanceStatus(clone));
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

    [DisallowMultipleComponent]
    public class LifecycleSingleComponent : MonoBehaviour { }

    public class LifecycleReferenceComponent : MonoBehaviour
    {
        public Rigidbody Body;
    }

    public abstract class LifecycleAbstractComponent : MonoBehaviour { }

    public class InactiveDeleteEnableProbe : MonoBehaviour
    {
        public static int EnableCalls;

        private void OnEnable() => EnableCalls++;
    }
}
