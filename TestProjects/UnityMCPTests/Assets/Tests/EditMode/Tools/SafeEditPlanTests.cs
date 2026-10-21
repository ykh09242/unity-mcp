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
            Assert.AreEqual(before, AssetDatabase.GetAssetDependencyHash(path).ToString());
            Assert.AreEqual(Vector3.zero, AssetDatabase.LoadAssetAtPath<GameObject>(path).transform.localPosition);
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
