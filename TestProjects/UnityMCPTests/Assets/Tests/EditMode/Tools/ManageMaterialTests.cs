using System;
using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageMaterialTests
    {
        private const string TempRoot = "Assets/Temp/ManageMaterialTests";
        private string _matPath;

        [SetUp]
        public void SetUp()
        {
            if (!AssetDatabase.IsValidFolder("Assets/Temp"))
            {
                AssetDatabase.CreateFolder("Assets", "Temp");
            }
            if (!AssetDatabase.IsValidFolder(TempRoot))
            {
                AssetDatabase.CreateFolder("Assets/Temp", "ManageMaterialTests");
            }

            string guid = Guid.NewGuid().ToString("N");
            _matPath = $"{TempRoot}/TestMat_{guid}.mat";
            
            // Create a basic material
            var material = new Material(Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard"));
            AssetDatabase.CreateAsset(material, _matPath);
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            if (AssetDatabase.IsValidFolder(TempRoot))
            {
                AssetDatabase.DeleteAsset(TempRoot);
            }
            
            // Clean up empty parent folders to avoid debris
            CleanupEmptyParentFolders(TempRoot);
        }

        [Test]
        public void SetMaterialShaderProperty_SetsColor()
        {
            // Arrange
            var color = new Color(1f, 1f, 0f, 1f); // Yellow
            var paramsObj = new JObject
            {
                ["action"] = "set_material_shader_property",
                ["materialPath"] = _matPath,
                ["property"] = "_BaseColor", // URP
                ["value"] = new JArray(color.r, color.g, color.b, color.a)
            };
            
            // Check if using Standard shader (fallback)
            var mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            if (mat.shader.name == "Standard")
            {
                paramsObj["property"] = "_Color";
            }

            // Act
            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            // Assert
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            
            mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath); // Reload
            var prop = mat.shader.name == "Standard" ? "_Color" : "_BaseColor";
            
            Assert.IsTrue(mat.HasProperty(prop), $"Material should have property {prop}");
            Assert.AreEqual(color, mat.GetColor(prop));
        }

        [Test]
        public void SetMaterialColor_SetsColorWithFallback()
        {
            // Arrange
            var color = new Color(0f, 1f, 0f, 1f); // Green
            var paramsObj = new JObject
            {
                ["action"] = "set_material_color",
                ["materialPath"] = _matPath,
                ["color"] = new JArray(color.r, color.g, color.b, color.a)
            };

            // Act
            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            // Assert
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            
            var mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            var prop = mat.HasProperty("_BaseColor") ? "_BaseColor" : "_Color";
            
            Assert.IsTrue(mat.HasProperty(prop), $"Material should have property {prop}");
            Assert.AreEqual(color, mat.GetColor(prop));
        }

        [Test]
        public void AssignMaterialToRenderer_Works()
        {
            // Arrange
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = "AssignTestCube";
            
            try
            {
                var paramsObj = new JObject
                {
                    ["action"] = "assign_material_to_renderer",
                    ["target"] = "AssignTestCube",
                    ["searchMethod"] = "by_name",
                    ["materialPath"] = _matPath,
                    ["slot"] = 0
                };

                // Act
                var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

                // Assert
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                
                var renderer = go.GetComponent<Renderer>();
                Assert.IsNotNull(renderer.sharedMaterial);
                // Compare names because objects might be different instances (loaded vs scene)
                var matName = Path.GetFileNameWithoutExtension(_matPath);
                Assert.AreEqual(matName, renderer.sharedMaterial.name);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }
        
        [TestCase(null)]
        [TestCase("shared")]
        [TestCase("instance")]
        public void AssignMaterialToRenderer_RespectsModeAndPreservesOtherSlots(string mode)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            var peer = GameObject.CreatePrimitive(PrimitiveType.Cube);
            Material instance = null;
            try
            {
                var material = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
                var renderer = go.GetComponent<Renderer>();
                var peerRenderer = peer.GetComponent<Renderer>();
                renderer.sharedMaterials = new[] { material, material };
                peerRenderer.sharedMaterial = material;
                var property = material.HasProperty("_BaseColor") ? "_BaseColor" : "_Color";
                var originalColor = material.GetColor(property);

                var request = new JObject
                {
                    ["action"] = "assign_material_to_renderer",
                    ["target"] = go.GetInstanceIDCompat().ToString(),
                    ["searchMethod"] = "by_id",
                    ["materialPath"] = _matPath,
                    ["slot"] = 1
                };
                if (mode != null) request["mode"] = mode;
                var result = ToJObject(ManageMaterial.HandleCommand(request));

                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.AreSame(material, renderer.sharedMaterials[0]);
                Assert.AreSame(material, peerRenderer.sharedMaterial);
                var selected = renderer.sharedMaterials[1];
                if (mode != "instance")
                {
                    Assert.AreSame(material, selected);
                }
                else
                {
                    if (selected != material) instance = selected;
                    Assert.AreNotSame(material, selected, "Instance assignment must clone the selected source material.");
                    Assert.IsFalse(AssetDatabase.Contains(selected));
                    Assert.AreSame(material.shader, selected.shader);
                    Assert.AreEqual(originalColor, selected.GetColor(property));
                    selected.SetColor(property, Color.magenta);
                    Assert.AreEqual(originalColor, material.GetColor(property));
                    Assert.AreEqual(originalColor, peerRenderer.sharedMaterial.GetColor(property));
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
                UnityEngine.Object.DestroyImmediate(peer);
                if (instance != null) UnityEngine.Object.DestroyImmediate(instance);
            }
        }

        [Test]
        public void AssignMaterialToRenderer_InstanceUndoRestoresSlotAndDestroysClone()
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            var renderer = go.GetComponent<Renderer>();
            var material = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            renderer.sharedMaterials = new[] { material, material };
            Material instance = null;
            Undo.IncrementCurrentGroup();
            int undoGroup = Undo.GetCurrentGroup();
            try
            {
                var result = ToJObject(ManageMaterial.HandleCommand(new JObject
                {
                    ["action"] = "assign_material_to_renderer",
                    ["target"] = go.GetInstanceIDCompat().ToString(),
                    ["searchMethod"] = "by_id",
                    ["materialPath"] = _matPath,
                    ["slot"] = 1,
                    ["mode"] = "instance"
                }));
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                if (renderer.sharedMaterials[1] != material) instance = renderer.sharedMaterials[1];
                Assert.IsNotNull(instance, "Instance assignment must create a clone.");

                Undo.FlushUndoRecordObjects();
                Undo.CollapseUndoOperations(undoGroup);
                Undo.PerformUndo();

                Assert.AreSame(material, renderer.sharedMaterials[0]);
                Assert.AreSame(material, renderer.sharedMaterials[1]);
                Assert.IsTrue(instance == null, "Undo must destroy the newly created material clone.");
                Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<Material>(_matPath));
            }
            finally
            {
                Undo.ClearUndo(renderer);
                if (instance != null)
                {
                    Undo.ClearUndo(instance);
                    UnityEngine.Object.DestroyImmediate(instance);
                }
                UnityEngine.Object.DestroyImmediate(go);
                Undo.IncrementCurrentGroup();
            }
        }

        [TestCase("invalid")]
        [TestCase("property_block")]
        [TestCase("create_unique")]
        public void AssignMaterialToRenderer_UnsupportedModeDoesNotChangeSlots(string mode)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            try
            {
                var renderer = go.GetComponent<Renderer>();
                var original = renderer.sharedMaterial;
                var materialCount = UnityEngine.Resources.FindObjectsOfTypeAll<Material>().Length;
                var result = ToJObject(ManageMaterial.HandleCommand(new JObject
                {
                    ["action"] = "assign_material_to_renderer",
                    ["target"] = go.GetInstanceIDCompat().ToString(),
                    ["searchMethod"] = "by_id",
                    ["materialPath"] = _matPath,
                    ["mode"] = mode
                }));

                Assert.IsFalse(result.Value<bool>("success"), result.ToString());
                StringAssert.Contains("mode", result.Value<string>("error"));
                Assert.AreSame(original, renderer.sharedMaterial);
                Assert.AreEqual(materialCount, UnityEngine.Resources.FindObjectsOfTypeAll<Material>().Length);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [TestCase("[1]")]
        [TestCase("5")]
        [TestCase("true")]
        public void CreateMaterial_UnsupportedPropertiesShapeDoesNotCreateAsset(string properties)
        {
            var path = $"{TempRoot}/Rejected.mat";
            var materialCount = UnityEngine.Resources.FindObjectsOfTypeAll<Material>().Length;
            var result = ToJObject(ManageMaterial.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["materialPath"] = path,
                ["properties"] = JToken.Parse(properties)
            }));

            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            StringAssert.Contains("properties", result.Value<string>("error"));
            Assert.IsNull(AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(path));
            Assert.IsFalse(File.Exists(path));
            Assert.AreEqual(materialCount, UnityEngine.Resources.FindObjectsOfTypeAll<Material>().Length);
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CreateMaterial_ObjectPropertiesStillCreatesAsset(bool jsonString)
        {
            var path = $"{TempRoot}/Valid.mat";
            var result = ToJObject(ManageMaterial.HandleCommand(new JObject
            {
                ["action"] = "create",
                ["materialPath"] = path,
                ["properties"] = jsonString ? (JToken)new JValue("{}") : new JObject()
            }));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<Material>(path));
        }

        [Test]
        public void SetRendererColor_PropertyBlock_Works()
        {
             // Arrange
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = "BlockTestCube";
            
            // Assign the material first so we have something valid
            var mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            go.GetComponent<Renderer>().sharedMaterial = mat;

            try
            {
                var color = new Color(1f, 0f, 0f, 1f); // Red
                var paramsObj = new JObject
                {
                    ["action"] = "set_renderer_color",
                    ["target"] = "BlockTestCube",
                    ["searchMethod"] = "by_name",
                    ["color"] = new JArray(color.r, color.g, color.b, color.a),
                    ["mode"] = "property_block"
                };

                // Act
                var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

                // Assert
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                
                var renderer = go.GetComponent<Renderer>();
                var block = new MaterialPropertyBlock();
                renderer.GetPropertyBlock(block, 0);
                
                var prop = mat.HasProperty("_BaseColor") ? "_BaseColor" : "_Color";
                Assert.AreEqual(color, block.GetColor(prop));
                
                // Verify material asset didn't change (it was originally white/gray from setup?)
                // We didn't check original color, but property block shouldn't affect shared material
                // We can check that sharedMaterial color is NOT red if we set it to something else first
                // But assuming test isolation, we can just verify the block is set.
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        [Test]
        public void GetMaterialInfo_ReturnsProperties()
        {
             // Arrange
            var paramsObj = new JObject
            {
                ["action"] = "get_material_info",
                ["materialPath"] = _matPath
            };

            // Act
            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            // Assert
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"] as JObject;
            Assert.IsNotNull(data, "Response should have data object");
            Assert.IsNotNull(data["properties"]);
            Assert.IsInstanceOf<JArray>(data["properties"]);
            var props = data["properties"] as JArray;
            Assert.IsTrue(props.Count > 0);
            
            // Check for standard properties
            bool foundColor = false;
            foreach(var p in props)
            {
                var name = p["name"]?.ToString();
                if (name == "_Color" || name == "_BaseColor") foundColor = true;
            }
            Assert.IsTrue(foundColor, "Should find color property");
        }
    }
}
