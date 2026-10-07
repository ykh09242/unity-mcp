using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Editor.Tools.GameObjects;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using static MCPForUnityTests.Editor.TestUtilities;
#if UNITY_6000_0_OR_NEWER
using PhysicsMaterialType = UnityEngine.PhysicsMaterial;
#else
using PhysicsMaterialType = UnityEngine.PhysicMaterial;
#endif

namespace MCPForUnityTests.Editor.Tools
{
    public class MaterialParameterToolTests
    {
        private const string TempRoot = "Assets/Temp/MaterialParameterToolTests";
        private string _matPath; // unique per test run
        private GameObject _sphere;

        [SetUp]
        public void SetUp()
        {
            _matPath = $"{TempRoot}/BlueURP_{Guid.NewGuid().ToString("N")}.mat";
            if (!AssetDatabase.IsValidFolder("Assets/Temp"))
            {
                AssetDatabase.CreateFolder("Assets", "Temp");
            }
            if (!AssetDatabase.IsValidFolder(TempRoot))
            {
                AssetDatabase.CreateFolder("Assets/Temp", "MaterialParameterToolTests");
            }
            // Ensure any leftover material from previous runs is removed
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(_matPath) != null)
            {
                AssetDatabase.DeleteAsset(_matPath);
                AssetDatabase.Refresh();
            }
            // Hard-delete any stray files on disk (in case GUID lookup fails)
            var abs = Path.Combine(Directory.GetCurrentDirectory(), _matPath);
            try
            {
                if (File.Exists(abs))
                    File.Delete(abs);
                if (File.Exists(abs + ".meta"))
                    File.Delete(abs + ".meta");
            }
            catch { /* best-effort cleanup */ }
            AssetDatabase.Refresh();
        }

        [TearDown]
        public void TearDown()
        {
            if (_sphere != null)
            {
                UnityEngine.Object.DestroyImmediate(_sphere);
                _sphere = null;
            }
            if (AssetDatabase.LoadAssetAtPath<Material>(_matPath) != null)
            {
                AssetDatabase.DeleteAsset(_matPath);
            }

            // Clean up temp directory after each test
            if (AssetDatabase.IsValidFolder(TempRoot))
            {
                AssetDatabase.DeleteAsset(TempRoot);
            }

            // Clean up empty parent folders to avoid debris
            CleanupEmptyParentFolders(TempRoot);

            AssetDatabase.Refresh();
        }

        [Test]
        public void CreateMaterial_WithObjectProperties_SucceedsAndSetsColor()
        {
            // Ensure a clean state if a previous run left the asset behind (uses _matPath now)
            if (AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(_matPath) != null)
            {
                AssetDatabase.DeleteAsset(_matPath);
                AssetDatabase.Refresh();
            }
            var createParams = new JObject
            {
                ["action"] = "create",
                ["path"] = _matPath,
                ["assetType"] = "Material",
                ["properties"] = new JObject { ["shader"] = "Universal Render Pipeline/Lit", ["color"] = new JArray(0f, 0f, 1f, 1f) },
            };

            var result = ToJObject(ManageAsset.HandleCommand(createParams));
            Assert.IsTrue(result.Value<bool>("success"), result.Value<string>("error"));

            var mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            Assert.IsNotNull(mat, "Material should exist at path.");
            // Verify color if shader exposes _Color
            if (mat.HasProperty("_Color"))
            {
                Assert.AreEqual(Color.blue, mat.GetColor("_Color"));
            }
        }

        [TestCase(0f, false)]
        [TestCase(1f, false)]
        [TestCase(0f, true)]
        [TestCase(1f, true)]
        [TestCase(0.35f, true)]
        public void CreatePhysicsMaterial_NumericPropertiesPersist(float value, bool floatingPoint)
        {
            var path = $"{TempRoot}/Physics_{Guid.NewGuid():N}.physicMaterial";
            JValue numericValue = floatingPoint ? new JValue(value) : new JValue((int)value);
            Assert.AreEqual(floatingPoint ? JTokenType.Float : JTokenType.Integer, numericValue.Type);
            var response = ToJObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["path"] = path,
                        ["assetType"] = "PhysicsMaterial",
                        ["properties"] = new JObject
                        {
                            ["dynamicFriction"] = numericValue.DeepClone(),
                            ["staticFriction"] = numericValue.DeepClone(),
                            ["bounciness"] = numericValue.DeepClone(),
                        },
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            var material = AssetDatabase.LoadAssetAtPath<PhysicsMaterialType>(path);
            Assert.IsNotNull(material);
            Assert.AreEqual(value, material.dynamicFriction, 0.001f);
            Assert.AreEqual(value, material.staticFriction, 0.001f);
            Assert.AreEqual(value, material.bounciness, 0.001f);
        }

        [Test]
        public void AssignMaterial_ToSphere_UsingManageMaterial_Succeeds()
        {
            // Ensure material exists first
            CreateMaterial_WithObjectProperties_SucceedsAndSetsColor();

            // Create a sphere via handler
            var createGo = new JObject
            {
                ["action"] = "create",
                ["name"] = "ToolTestSphere",
                ["primitiveType"] = "Sphere",
            };
            var createGoResult = ToJObject(ManageGameObject.HandleCommand(createGo));
            Assert.IsTrue(createGoResult.Value<bool>("success"), createGoResult.Value<string>("error"));

            _sphere = GameObject.Find("ToolTestSphere");
            Assert.IsNotNull(_sphere, "Sphere should be created.");

            // Assign material via ManageMaterial tool
            var assignParams = new JObject
            {
                ["action"] = "assign_material_to_renderer",
                ["target"] = "ToolTestSphere",
                ["searchMethod"] = "by_name",
                ["materialPath"] = _matPath,
                ["slot"] = 0,
            };

            var assignResult = ToJObject(ManageMaterial.HandleCommand(assignParams));
            Assert.IsTrue(assignResult.Value<bool>("success"), assignResult.ToString());

            var renderer = _sphere.GetComponent<MeshRenderer>();
            Assert.IsNotNull(renderer, "Sphere should have MeshRenderer.");
            Assert.IsNotNull(renderer.sharedMaterial, "sharedMaterial should be assigned.");
            StringAssert.StartsWith("BlueURP_", renderer.sharedMaterial.name);
        }

        [Test]
        public void ReadRendererData_DoesNotInstantiateMaterial_AndIncludesSharedMaterial()
        {
            // Prepare object and assignment
            AssignMaterial_ToSphere_UsingManageMaterial_Succeeds();

            var renderer = _sphere.GetComponent<MeshRenderer>();
            int beforeId = renderer.sharedMaterial != null ? renderer.sharedMaterial.GetInstanceIDCompat() : 0;

            var data = MCPForUnity.Editor.Helpers.GameObjectSerializer.GetComponentData(renderer) as System.Collections.Generic.Dictionary<string, object>;
            Assert.IsNotNull(data, "Serializer should return data.");

            int afterId = renderer.sharedMaterial != null ? renderer.sharedMaterial.GetInstanceIDCompat() : 0;
            Assert.AreEqual(beforeId, afterId, "sharedMaterial instance must not change (no instantiation in EditMode).");

            if (data.TryGetValue("properties", out var propsObj) && propsObj is System.Collections.Generic.Dictionary<string, object> props)
            {
                Assert.IsTrue(
                    props.ContainsKey("sharedMaterial")
                        || props.ContainsKey("material")
                        || props.ContainsKey("sharedMaterials")
                        || props.ContainsKey("materials"),
                    "Serialized data should include material info."
                );
            }
        }
    }
}
