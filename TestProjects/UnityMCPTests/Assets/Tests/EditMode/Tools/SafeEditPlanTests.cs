using System;
using System.Linq;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.Prefabs;
using MCPForUnity.Runtime.Helpers;
using MCPForUnityTests.Editor.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;
#if UNITY_6000_0_OR_NEWER
using TestPhysicsMaterial = UnityEngine.PhysicsMaterial;
#else
using TestPhysicsMaterial = UnityEngine.PhysicMaterial;
#endif

namespace MCPForUnityTests.Editor.Tools
{
    public class SafeEditPlanTests
    {
        private readonly PrefabTestSceneFixture fixture = new PrefabTestSceneFixture();
        private GameObject root;
        private string folder;

        [OneTimeSetUp]
        public void Bootstrap() => fixture.PrepareRunnerBootstrap();

        [OneTimeTearDown]
        public void Restore() => fixture.RestoreRunnerBootstrap();

        [SetUp]
        public void SetUp()
        {
            fixture.Create("McpSafeEditPlans_", Guid.NewGuid().ToString("N"));
            root = new GameObject("PlanRoot");
            folder = "Assets/__McpSafeEditPlans_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folder.Substring(7));
        }

        [TearDown]
        public void TearDown()
        {
            Undo.ClearAll();
            fixture.Close();
            if (AssetDatabase.IsValidFolder(folder))
                AssetDatabase.DeleteAsset(folder);
        }

        private static JObject Scene(JObject request) => JObject.FromObject(ManageSceneEdits.HandleCommand(request));

        private static JObject Prefab(JObject request) => JObject.FromObject(ManagePrefabs.HandleCommand(request));

        private JObject Preview(params JObject[] operations) =>
            Scene(
                new JObject
                {
                    ["action"] = "preview_structure",
                    ["target"] = root.GetInstanceIDCompat(),
                    ["operations"] = new JArray(operations),
                }
            );

        private static string Token(JObject response)
        {
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            return response["data"].Value<string>("preview_id");
        }

        private static JObject Apply(string token) => Scene(new JObject { ["action"] = "apply_structure", ["preview_id"] = token });

        [Test]
        public void LargePrefabSourceHashUsesBoundedMemoryAndDetectsLateBytes()
        {
            string path = folder + "/HashProbe.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            Type plans = typeof(ManagePrefabs).Assembly.GetType("MCPForUnity.Editor.Tools.Prefabs.PrefabSourcePlans");
            var method = plans.GetMethod("SourceHash", System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static);
            string Hash() => (string)method.Invoke(null, new object[] { path });
            string original = Hash();
            using (var stream = new System.IO.FileStream(path, System.IO.FileMode.Append, System.IO.FileAccess.Write))
            {
                byte[] padding = System.Text.Encoding.ASCII.GetBytes("#" + new string('x', 8190) + "\n");
                for (int index = 0; index < 2176; index++)
                    stream.Write(padding, 0, padding.Length);
            }
            long before = GC.GetAllocatedBytesForCurrentThread();
            string extended = Hash();
            long allocated = GC.GetAllocatedBytesForCurrentThread() - before;
            Assert.AreNotEqual(original, extended);
            Assert.Less(allocated, 1024 * 1024, "Hashing must not allocate the whole 17 MiB source.");
            using (var stream = new System.IO.FileStream(path, System.IO.FileMode.Open, System.IO.FileAccess.Write))
            {
                stream.Seek(-2, System.IO.SeekOrigin.End);
                stream.WriteByte((byte)'y');
            }
            Assert.AreNotEqual(extended, Hash(), "The final bytes must participate in the revision.");
        }

        [Test]
        public void LargePlainPrefabSourcePlanPreservesAllChildren()
        {
            for (int index = 0; index < 300; index++)
                new GameObject("Node" + index).transform.SetParent(root.transform, false);
            string path = folder + "/Large.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            string token = Token(
                Prefab(
                    new JObject
                    {
                        ["action"] = "preview_modify_contents",
                        ["prefabPath"] = path,
                        ["position"] = new JArray(1, 2, 3),
                    }
                )
            );
            JObject applied = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            GameObject saved = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.AreEqual(300, saved.transform.childCount);
            Assert.AreEqual(new Vector3(1, 2, 3), saved.transform.localPosition);
        }

        [Test]
        public void OrderedTemporaryParents_CreateReparentDelete_AreOneUndo()
        {
            var child = new GameObject("Existing");
            child.transform.SetParent(root.transform, false);
            var obsolete = new GameObject("Obsolete");
            obsolete.transform.SetParent(root.transform, false);
            string token = Token(
                Preview(
                    new JObject
                    {
                        ["op"] = "create",
                        ["temp_id"] = "$parent",
                        ["name"] = "Created",
                        ["parent"] = root.GetInstanceIDCompat(),
                    },
                    new JObject
                    {
                        ["op"] = "reparent",
                        ["target"] = child.GetInstanceIDCompat(),
                        ["parent"] = "$parent",
                    },
                    new JObject { ["op"] = "delete", ["target"] = obsolete.GetInstanceIDCompat() }
                )
            );
            Assert.AreEqual(2, root.transform.childCount);
            var applied = Apply(token);
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            Assert.AreEqual("Created", child.transform.parent.name);
            Assert.IsTrue(obsolete == null);
            Undo.PerformUndo();
            Assert.AreEqual(root.transform, child.transform.parent);
            Assert.IsNotNull(root.transform.Find("Obsolete"));
            Assert.IsNull(root.transform.Find("Created"));
        }

        [Test]
        public void HierarchyOrValueWriterAfterPreview_RejectsBeforeCreation()
        {
            string token = Token(
                Preview(
                    new JObject
                    {
                        ["op"] = "create",
                        ["temp_id"] = "$new",
                        ["name"] = "Created",
                        ["parent"] = root.GetInstanceIDCompat(),
                    }
                )
            );
            root.transform.localPosition = Vector3.one;
            int undo = Undo.GetCurrentGroup();
            Assert.IsFalse(Apply(token).Value<bool>("success"));
            Assert.IsNull(root.transform.Find("Created"));
            Assert.AreEqual(Vector3.one, root.transform.localPosition);
            Assert.AreEqual(undo, Undo.GetCurrentGroup());
            Assert.IsFalse(Apply(token).Value<bool>("success"));
        }

        [Test]
        public void LateInvalidOrRequireComponentTarget_RejectsEntireStructurePlan()
        {
            root.AddComponent<Rigidbody>();
            var result = Preview(new JObject { ["op"] = "delete", ["target"] = root.GetInstanceIDCompat() });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsNotNull(root);
            Object.DestroyImmediate(root.GetComponent<Rigidbody>());
            result = Preview(
                new JObject
                {
                    ["op"] = "create",
                    ["temp_id"] = "$new",
                    ["name"] = "Created",
                    ["parent"] = root.GetInstanceIDCompat(),
                },
                new JObject
                {
                    ["op"] = "reparent",
                    ["target"] = "$new",
                    ["parent"] = "$new",
                }
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(0, root.transform.childCount);
        }

        [Test]
        public void RequiredAndCustomComponents_AreRefusedBeforeStructuralMutation()
        {
            var component = root.AddComponent<SafeEditRequiredComponent>();
            Assert.IsNotNull(root.GetComponent<BoxCollider>());
            JObject rejected = Preview(
                new JObject
                {
                    ["op"] = "create",
                    ["temp_id"] = "$child",
                    ["name"] = "Child",
                    ["parent"] = root.GetInstanceIDCompat(),
                }
            );
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(0, root.transform.childCount);
            Assert.IsNotNull(component);
            Assert.IsNotNull(root.GetComponent<BoxCollider>());
        }

        [Test]
        public void CapsuleNativeProperty_ExpectedOldAndUndo()
        {
            var capsule = root.AddComponent<CapsuleCollider>();
            var edit = new JObject
            {
                ["target"] = root.GetInstanceIDCompat(),
                ["component_type"] = "CapsuleCollider",
                ["property"] = "m_Radius",
                ["value"] = 2,
                ["expected_old"] = capsule.radius,
            };
            JObject result = Scene(new JObject { ["action"] = "apply", ["edits"] = new JArray(edit) });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual(2f, capsule.radius);
            Undo.PerformUndo();
            Assert.AreEqual(.5f, capsule.radius);
        }

        [Test]
        public void GeneralComponentReportsOrderedBestEffortIncludingLaterSuccess()
        {
            var collider = root.AddComponent<BoxCollider>();
            var result = JObject.FromObject(
                ManageComponents.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_property",
                        ["target"] = root.GetInstanceIDCompat(),
                        ["componentType"] = "BoxCollider",
                        ["properties"] = new JObject
                        {
                            ["isTrigger"] = true,
                            ["NoSuchSafePlanProperty"] = 1,
                            ["enabled"] = false,
                        },
                    }
                )
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.IsTrue(collider.isTrigger);
            Assert.IsFalse(collider.enabled);
            Assert.AreEqual("best_effort", result["data"].Value<string>("mode"));
            CollectionAssert.AreEqual(new[] { "applied", "failed", "applied" }, result["data"]["results"].Select(t => t.Value<string>("status")));
        }

        [Test]
        public void ScriptablePatchesReportOrderedBestEffortWithoutChangingLegacySuccess()
        {
            var asset = ScriptableObject.CreateInstance<MCPForUnityTests.Editor.Tools.Fixtures.ManageScriptableObjectTestDefinition>();
            string path = folder + "/Definition.asset";
            AssetDatabase.CreateAsset(asset, path);
            JObject result = JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = new JObject { ["path"] = path },
                        ["patches"] = new JArray(
                            new JObject { ["propertyPath"] = "displayName", ["value"] = "Kept" },
                            new JObject { ["propertyPath"] = "MissingSafeEditField", ["value"] = 2 },
                            new JObject { ["propertyPath"] = "displayName", ["value"] = "LaterSuccess" },
                            new JObject
                            {
                                ["propertyPath"] = "materials",
                                ["op"] = "array_resize",
                                ["value"] = 0,
                            }
                        ),
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("LaterSuccess", asset.DisplayName);
            Assert.AreEqual("best_effort", result["data"].Value<string>("mode"));
            CollectionAssert.AreEqual(new[] { "applied", "failed", "applied", "skipped" }, result["data"]["results"].Select(t => t.Value<string>("status")));
        }

        [Test]
        public void PrefabPreviewAcceptedRootEditIsTokenBound()
        {
            string path = folder + "/Source.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            var preview = Prefab(
                new JObject
                {
                    ["action"] = "preview_modify_contents",
                    ["prefabPath"] = path,
                    ["position"] = new JArray(1, 2, 3),
                }
            );
            string token = Token(preview);
            Assert.IsNotNull(preview["data"]["source_hash"]);
            Assert.IsNotNull(preview["data"]["impact"]);
            Assert.AreEqual(Vector3.zero, AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
            JObject applied = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            Assert.AreEqual(new Vector3(1, 2, 3), AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
            Assert.IsFalse(Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token }).Value<bool>("success"));
        }

        [Test]
        public void PrefabSourceWriterAfterPreview_RejectsBeforeSave()
        {
            string path = folder + "/Source.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            string token = Token(
                Prefab(
                    new JObject
                    {
                        ["action"] = "preview_modify_contents",
                        ["prefabPath"] = path,
                        ["position"] = new JArray(4, 5, 6),
                    }
                )
            );
            GameObject contents = PrefabUtility.LoadPrefabContents(path);
            try
            {
                contents.transform.localPosition = Vector3.one;
                PrefabUtility.SaveAsPrefabAsset(contents, path);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(contents);
            }
            JObject result = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(Vector3.one, AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
        }

        [Test]
        public void NestedPrefabStructuralPlans_AreRefusedWithoutUnpacking()
        {
            string path = folder + "/Source.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(path));
            instance.transform.SetParent(root.transform, false);
            Assert.IsFalse(Preview(new JObject { ["op"] = "delete", ["target"] = instance.GetInstanceIDCompat() }).Value<bool>("success"));
            Assert.IsTrue(PrefabUtility.IsPartOfPrefabInstance(instance));
        }

        [Test]
        public void CubePrefabRootEditPreservesNativeMeshAndMaterialReferences()
        {
            var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.transform.SetParent(root.transform, false);
            string path = folder + "/Cube.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            var source = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Mesh mesh = source.GetComponentInChildren<MeshFilter>().sharedMesh;
            Material material = source.GetComponentInChildren<MeshRenderer>().sharedMaterial;
            byte[] before = System.IO.File.ReadAllBytes(path);
            string token = PreviewRootScale(path);
            CollectionAssert.AreEqual(before, System.IO.File.ReadAllBytes(path), "Preview must not save the source.");
            var result = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            source = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            Assert.AreEqual(new Vector3(1.25f, 1, 1), source.transform.localScale);
            Assert.AreSame(mesh, source.GetComponentInChildren<MeshFilter>().sharedMesh);
            Assert.AreSame(material, source.GetComponentInChildren<MeshRenderer>().sharedMaterial);
        }

        [Test]
        public void MeshPrefabRootEditPreservesSharedAssetBytesIdentitiesAndAssetInventory()
        {
            var assets = CreateMeshPrefab();
            string meshId = AssetIdentity(assets.mesh),
                materialId = AssetIdentity(assets.material);
            string[] assetPaths = { assets.meshPath, assets.meshPath + ".meta", assets.materialPath, assets.materialPath + ".meta" };
            var before = assetPaths.ToDictionary(path => path, System.IO.File.ReadAllBytes);
            string[] inventory = AssetDatabase.FindAssets("", new[] { folder }).OrderBy(guid => guid, StringComparer.Ordinal).ToArray();
            string token = PreviewRootScale(assets.prefabPath);
            var result = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var source = AssetDatabase.LoadAssetAtPath<GameObject>(assets.prefabPath);
            Assert.AreEqual(new Vector3(1.25f, 1, 1), source.transform.localScale);
            Assert.AreSame(assets.mesh, source.GetComponent<MeshFilter>().sharedMesh);
            Assert.AreSame(assets.material, source.GetComponent<MeshRenderer>().sharedMaterial);
            Assert.AreSame(assets.mesh, source.transform.Find("SharedBorrower").GetComponent<MeshFilter>().sharedMesh);
            Assert.AreSame(assets.material, source.transform.Find("SharedBorrower").GetComponent<MeshRenderer>().sharedMaterial);
            Assert.AreEqual(meshId, AssetIdentity(source.GetComponent<MeshFilter>().sharedMesh));
            Assert.AreEqual(materialId, AssetIdentity(source.GetComponent<MeshRenderer>().sharedMaterial));
            foreach (string path in assetPaths)
                CollectionAssert.AreEqual(before[path], System.IO.File.ReadAllBytes(path), path);
            CollectionAssert.AreEqual(inventory, AssetDatabase.FindAssets("", new[] { folder }).OrderBy(guid => guid, StringComparer.Ordinal).ToArray());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void MeshOrMaterialDependencyWriterRejectsPrefabRootEdit(bool editMaterial)
        {
            var assets = CreateMeshPrefab();
            string token = PreviewRootScale(assets.prefabPath);
            if (editMaterial)
            {
                assets.material.color = Color.red;
                EditorUtility.SetDirty(assets.material);
            }
            else
            {
                assets.mesh.vertices = new[] { Vector3.zero, Vector3.right * 2, Vector3.up };
                EditorUtility.SetDirty(assets.mesh);
            }
            AssetDatabase.SaveAssets();
            byte[] prefabBefore = System.IO.File.ReadAllBytes(assets.prefabPath);
            var result = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("drifted", result.Value<string>("error"));
            CollectionAssert.AreEqual(prefabBefore, System.IO.File.ReadAllBytes(assets.prefabPath));
            Assert.AreEqual(Vector3.one, AssetDatabase.LoadAssetAtPath<GameObject>(assets.prefabPath).transform.localScale);
        }

        private string PreviewRootScale(string path) =>
            Token(
                Prefab(
                    new JObject
                    {
                        ["action"] = "preview_modify_contents",
                        ["prefabPath"] = path,
                        ["scale"] = new JArray(1.25, 1, 1),
                    }
                )
            );

        private (Mesh mesh, Material material, string meshPath, string materialPath, string prefabPath) CreateMeshPrefab()
        {
            var mesh = new Mesh
            {
                name = "SharedMesh",
                vertices = new[] { Vector3.zero, Vector3.right, Vector3.up },
                triangles = new[] { 0, 1, 2 },
            };
            string meshPath = folder + "/SharedMesh.asset";
            AssetDatabase.CreateAsset(mesh, meshPath);
            var shader = Shader.Find("Sprites/Default");
            Assert.IsNotNull(shader, "The native fixture requires an installed built-in Sprite shader.");
            var material = new Material(shader) { name = "SharedMaterial" };
            string materialPath = folder + "/SharedMaterial.mat";
            AssetDatabase.CreateAsset(material, materialPath);
            root.AddComponent<MeshFilter>().sharedMesh = mesh;
            root.AddComponent<MeshRenderer>().sharedMaterial = material;
            var borrower = new GameObject("SharedBorrower");
            borrower.transform.SetParent(root.transform, false);
            borrower.AddComponent<MeshFilter>().sharedMesh = mesh;
            borrower.AddComponent<MeshRenderer>().sharedMaterial = material;
            string prefabPath = folder + "/Mesh.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, prefabPath);
            return (mesh, material, meshPath, materialPath, prefabPath);
        }

        private static string AssetIdentity(Object value)
        {
            Assert.IsNotNull(value);
            Assert.IsTrue(AssetDatabase.TryGetGUIDAndLocalFileIdentifier(value, out string guid, out long localId));
            return guid + ":" + localId;
        }

        [Test]
        public void PrefabDependencyWriterAfterPreview_RejectsRootEdit()
        {
            var material = new TestPhysicsMaterial("Original");
            string materialPath = folder + "/Physics.physicMaterial";
            AssetDatabase.CreateAsset(material, materialPath);
            root.AddComponent<BoxCollider>().sharedMaterial = material;
            string path = folder + "/Source.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            string token = Token(
                Prefab(
                    new JObject
                    {
                        ["action"] = "preview_modify_contents",
                        ["prefabPath"] = path,
                        ["position"] = new JArray(1, 2, 3),
                    }
                )
            );
            material.dynamicFriction = .9f;
            EditorUtility.SetDirty(material);
            AssetDatabase.SaveAssets();
            JObject rejected = Prefab(new JObject { ["action"] = "apply_preview", ["preview_id"] = token });
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
        }

        [Test]
        public void StructuralMidApplyFailure_CompensatesEarlierCreation()
        {
            string token = Token(
                Preview(
                    new JObject
                    {
                        ["op"] = "create",
                        ["temp_id"] = "$first",
                        ["name"] = "First",
                        ["parent"] = root.GetInstanceIDCompat(),
                    },
                    new JObject
                    {
                        ["op"] = "create",
                        ["temp_id"] = "$second",
                        ["name"] = "Second",
                        ["parent"] = root.GetInstanceIDCompat(),
                    }
                )
            );
            Type helper = typeof(ManageSceneEdits).Assembly.GetType("MCPForUnity.Editor.Tools.SceneEdits.SceneStructurePlans", true);
            var cache = (System.Collections.IDictionary)
                helper.GetField("Previews", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic).GetValue(null);
            object plan = cache[token];
            var operations = (JArray)
                plan.GetType().GetField("Operations", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).GetValue(plan);
            // Exercise actual native Undo compensation without a production fault injection API.
            operations[1]["op"] = "injected-write-failure";
            JObject rejected = Apply(token);
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.IsTrue(rejected["data"].Value<bool>("rollback_verified"), rejected.ToString());
            Assert.AreEqual(0, root.transform.childCount);
        }

        [Test]
        public void CapsuleLateInvalidEdit_PrevalidationKeepsEarlierTransform()
        {
            var capsule = root.AddComponent<CapsuleCollider>();
            JObject result = Scene(
                new JObject
                {
                    ["action"] = "preview",
                    ["edits"] = new JArray(
                        new JObject
                        {
                            ["target"] = root.GetInstanceIDCompat(),
                            ["component_type"] = "Transform",
                            ["property"] = "localPosition",
                            ["value"] = new JArray(1, 2, 3),
                        },
                        new JObject
                        {
                            ["target"] = root.GetInstanceIDCompat(),
                            ["component_type"] = "CapsuleCollider",
                            ["property"] = "m_Radius",
                            ["value"] = -1,
                        }
                    ),
                }
            );
            Assert.IsFalse(result.Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            Assert.AreEqual(.5f, capsule.radius);
        }

        [Test]
        public void CapsuleWriterAfterPreview_RejectsBeforeEarlierTransformWrite()
        {
            var capsule = root.AddComponent<CapsuleCollider>();
            string token = Token(
                Scene(
                    new JObject
                    {
                        ["action"] = "preview",
                        ["edits"] = new JArray(
                            new JObject
                            {
                                ["target"] = root.GetInstanceIDCompat(),
                                ["component_type"] = "Transform",
                                ["property"] = "localPosition",
                                ["value"] = new JArray(1, 2, 3),
                            },
                            new JObject
                            {
                                ["target"] = root.GetInstanceIDCompat(),
                                ["component_type"] = "CapsuleCollider",
                                ["property"] = "m_Radius",
                                ["value"] = 2,
                            }
                        ),
                    }
                )
            );
            capsule.radius = .75f;
            JObject rejected = Scene(new JObject { ["action"] = "apply", ["preview_id"] = token });
            Assert.IsFalse(rejected.Value<bool>("success"));
            Assert.AreEqual(Vector3.zero, root.transform.localPosition);
            Assert.AreEqual(.75f, capsule.radius);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PrefabNestedOrCustomSourcePreview_RefusesWithoutSaving(bool nested)
        {
            string path = folder + "/Unsafe.prefab";
            if (nested)
            {
                string childPath = folder + "/Child.prefab";
                PrefabUtility.SaveAsPrefabAsset(root, childPath);
                var instance = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(childPath));
                instance.transform.SetParent(root.transform, false);
            }
            else
                root.AddComponent<SafeEditRequiredComponent>();
            PrefabUtility.SaveAsPrefabAsset(root, path);
            string before = AssetDatabase.GetAssetDependencyHash(path).ToString();
            JObject rejected = Prefab(
                new JObject
                {
                    ["action"] = "preview_modify_contents",
                    ["prefabPath"] = path,
                    ["position"] = new JArray(1, 2, 3),
                }
            );
            Assert.IsFalse(rejected.Value<bool>("success"));
            string error = rejected.Value<string>("error");
            StringAssert.Contains(nested ? "UnityEngine.Transform" : typeof(SafeEditRequiredComponent).FullName, error);
            StringAssert.Contains(nested ? "Unsafe/Child" : "Unsafe", error);
            StringAssert.Contains(nested ? "nested prefab instance" : "unsupported component type", error);
            Assert.AreEqual(before, AssetDatabase.GetAssetDependencyHash(path).ToString());
            Assert.AreEqual(Vector3.zero, AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
        }

        [Test]
        public void PrefabUnsupportedNativeComponent_ReportsExactChildWithoutSaving()
        {
            var child = new GameObject("UnsupportedChild");
            child.transform.SetParent(root.transform, false);
            child.AddComponent<Camera>();
            string path = folder + "/Native.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            Assert.IsNotNull(
                AssetDatabase.LoadAssetAtPath<GameObject>(path).GetComponentInChildren<Camera>(),
                "The excluded native component must be persisted in the source fixture."
            );
            byte[] before = System.IO.File.ReadAllBytes(path);
            JObject rejected = Prefab(
                new JObject
                {
                    ["action"] = "preview_modify_contents",
                    ["prefabPath"] = path,
                    ["position"] = new JArray(1, 2, 3),
                }
            );
            Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
            string error = rejected.Value<string>("error");
            StringAssert.Contains(typeof(Camera).FullName, error);
            StringAssert.Contains("Native/UnsupportedChild", error);
            StringAssert.Contains("unsupported component type", error);
            CollectionAssert.AreEqual(before, System.IO.File.ReadAllBytes(path));
        }

        [Test]
        public void PrefabMissingScript_ReportsExactChildWithoutSaving()
        {
            var child = new GameObject("BrokenChild");
            child.transform.SetParent(root.transform, false);
            var component = child.AddComponent<SafeEditRequiredComponent>();
            string scriptPath = AssetDatabase.GetAssetPath(MonoScript.FromMonoBehaviour(component));
            string scriptGuid = AssetDatabase.AssetPathToGUID(scriptPath);
            Assert.IsNotEmpty(scriptGuid);
            string path = folder + "/Missing.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            string serialized = System.IO.File.ReadAllText(path);
            StringAssert.Contains("guid: " + scriptGuid, serialized);
            System.IO.File.WriteAllText(path, serialized.Replace("guid: " + scriptGuid, "guid: " + Guid.NewGuid().ToString("N")));
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            byte[] before = System.IO.File.ReadAllBytes(path);
            JObject rejected = Prefab(
                new JObject
                {
                    ["action"] = "preview_modify_contents",
                    ["prefabPath"] = path,
                    ["position"] = new JArray(1, 2, 3),
                }
            );
            Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
            string error = rejected.Value<string>("error");
            StringAssert.Contains("<missing script>", error);
            StringAssert.Contains("Missing/BrokenChild", error);
            StringAssert.Contains("missing component script at component index", error);
            CollectionAssert.AreEqual(before, System.IO.File.ReadAllBytes(path));
        }

        [Test]
        public void PrefabPreviewRootName_IsRefusedWithoutSaving()
        {
            string path = folder + "/Source.prefab";
            PrefabUtility.SaveAsPrefabAsset(root, path);
            string before = AssetDatabase.GetAssetDependencyHash(path).ToString();
            JObject rejected = Prefab(
                new JObject
                {
                    ["action"] = "preview_modify_contents",
                    ["prefabPath"] = path,
                    ["name"] = "RetainedRootName",
                }
            );
            Assert.IsFalse(rejected.Value<bool>("success"), rejected.ToString());
            StringAssert.Contains("normalizes", rejected.Value<string>("error"));
            Assert.AreEqual(before, AssetDatabase.GetAssetDependencyHash(path).ToString());
            GameObject reloaded = PrefabUtility.LoadPrefabContents(path);
            try
            {
                Assert.AreEqual("Source", reloaded.name);
                Assert.AreEqual(Vector3.zero, reloaded.transform.localPosition);
            }
            finally
            {
                PrefabUtility.UnloadPrefabContents(reloaded);
            }
        }

        [Test]
        public void ScriptableNoOpScalarAndNullReference_SkipWhileArrayGrowthApplies()
        {
            var asset = ScriptableObject.CreateInstance<MCPForUnityTests.Editor.Tools.Fixtures.ManageScriptableObjectTestDefinition>();
            string path = folder + "/NoOp.asset";
            AssetDatabase.CreateAsset(asset, path);
            JObject result = JObject.FromObject(
                ManageScriptableObject.HandleCommand(
                    new JObject
                    {
                        ["action"] = "modify",
                        ["target"] = new JObject { ["path"] = path },
                        ["patches"] = new JArray(
                            new JObject { ["propertyPath"] = "baseNumber", ["value"] = 1 },
                            new JObject { ["propertyPath"] = "materials[0]", ["value"] = JValue.CreateNull() },
                            new JObject { ["propertyPath"] = "materials[0]", ["value"] = JValue.CreateNull() }
                        ),
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(new[] { "skipped", "applied", "skipped" }, result["data"]["results"].Select(t => t.Value<string>("status")));
            Assert.AreEqual(1, asset.BaseNumber);
            Assert.AreEqual(1, asset.Materials.Count);
            Assert.IsNull(asset.Materials[0]);
        }
    }
}
