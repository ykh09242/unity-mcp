using System;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageMaterialPropertiesTests
    {
        private const string TempRoot = "Assets/Temp/ManageMaterialPropertiesTests";
        private string _matPath;

        [Test]
        public void SetColor_ReturnsActualPropertyAndSharedOwnership()
        {
            var create = ToJObject(
                ManageMaterial.HandleCommand(
                    new JObject
                    {
                        ["action"] = "create",
                        ["materialPath"] = _matPath,
                        ["shader"] = "Standard",
                    }
                )
            );
            Assert.IsTrue(create.Value<bool>("success"), create.ToString());
            var result = ToJObject(
                ManageMaterial.HandleCommand(
                    new JObject
                    {
                        ["action"] = "set_material_color",
                        ["materialPath"] = _matPath,
                        ["property"] = MainColorProperty(),
                        ["color"] = new JArray(0, 1, 0, 1),
                    }
                )
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var verification = result["data"]["verification"];
            Assert.AreEqual("passed", verification.Value<string>("status"));
            Assert.AreEqual("shared", verification.Value<string>("ownership"));
            var retained = (JArray)verification["observed_values"]["properties"][MainColorProperty()];
            Assert.AreEqual(0f, retained[0].Value<float>());
            Assert.AreEqual(1f, retained[1].Value<float>());
            Assert.AreEqual(0f, retained[2].Value<float>());
            Assert.AreEqual(1f, retained[3].Value<float>());
        }

        [TestCase("shared")]
        [TestCase("instance")]
        public void RendererColor_UnsupportedShaderCannotPassVerification(string mode)
        {
            var shader = Shader.Find("Hidden/MCPForUnityTests/MaterialRenderState");
            Assert.IsNotNull(shader);
            var material = new Material(shader);
            AssetDatabase.CreateAsset(material, _matPath);
            var target = new GameObject("UnsupportedColorTarget");
            var renderer = target.AddComponent<MeshRenderer>();
            renderer.sharedMaterial = material;
            Material instance = null;
            try
            {
                var result = ToJObject(
                    ManageMaterial.HandleCommand(
                        new JObject
                        {
                            ["action"] = "set_renderer_color",
                            ["target"] = target.GetInstanceIDCompat().ToString(),
                            ["searchMethod"] = "by_id",
                            ["mode"] = mode,
                            ["color"] = new JArray(0, 1, 0, 1),
                        }
                    )
                );
                instance = renderer.sharedMaterial;
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.AreNotEqual("passed", result["data"]["verification"].Value<string>("status"));
                Assert.IsFalse(result["data"]["verification"].Value<bool>("complete"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(target);
                if (instance != null && instance != material && !EditorUtility.IsPersistent(instance))
                    UnityEngine.Object.DestroyImmediate(instance);
            }
        }

        [TestCase(false, 1)]
        [TestCase(true, 1)]
        [TestCase(false, 32)]
        [TestCase(false, 33)]
        public void SkyboxProperties_KeepFailuresAndObserveResolvedAliases(bool includeAlias, int failureCount)
        {
            var shader = Shader.Find("Hidden/MCPForUnityTests/MaterialRenderState");
            Assert.IsNotNull(shader);
            var material = new Material(shader);
            AssetDatabase.CreateAsset(material, _matPath);
            Material original = RenderSettings.skybox;
            try
            {
                RenderSettings.skybox = material;
                var properties = new JObject { ["definitely_missing"] = 1 };
                for (int i = 1; i < failureCount; i++)
                    properties["missing_" + i] = 1;
                if (includeAlias)
                    properties["Value"] = 2;
                var result = ToJObject(MCPForUnity.Editor.Tools.Graphics.SkyboxOps.SetMaterialProperties(new JObject { ["properties"] = properties }));
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                JToken verification = result["data"]["verification"];
                Assert.AreEqual("issues_found", verification.Value<string>("status"));
                Assert.IsTrue(verification.Value<bool>("further_fixes_required"));
                Assert.AreEqual(failureCount > 32, verification.Value<bool>("findings_truncated"));
                Assert.AreEqual(Math.Min(32, failureCount), ((JArray)verification["findings"]).Count);
                Assert.AreEqual("definitely_missing", verification["findings"][0].Value<string>("field"));
                if (includeAlias)
                    Assert.AreEqual(2f, verification["observed_values"]["properties"].Value<float>("_Value"));
            }
            finally
            {
                RenderSettings.skybox = original;
            }
        }

        // Built-in's lit shader (Standard) uses "_Color" as its main color property,
        // while URP/HDRP lit shaders use "_BaseColor". The tool aliases shader "Standard"
        // to the pipeline-appropriate lit shader, so the color property name must match.
        private static string MainColorProperty()
        {
            return RenderPipelineUtility.GetActivePipeline() == RenderPipelineUtility.PipelineKind.BuiltIn ? "_Color" : "_BaseColor";
        }

        [SetUp]
        public void SetUp()
        {
            if (!AssetDatabase.IsValidFolder("Assets/Temp"))
            {
                AssetDatabase.CreateFolder("Assets", "Temp");
            }
            if (!AssetDatabase.IsValidFolder(TempRoot))
            {
                AssetDatabase.CreateFolder("Assets/Temp", "ManageMaterialPropertiesTests");
            }
            _matPath = $"{TempRoot}/PropTest.mat";
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
        public void CreateMaterial_WithValidJsonStringArray_SetsProperty()
        {
            string colorProp = MainColorProperty();
            string jsonProps = "{\"" + colorProp + "\": [1.0, 0.0, 0.0, 1.0]}";
            var paramsObj = new JObject
            {
                ["action"] = "create",
                ["materialPath"] = _matPath,
                ["shader"] = "Standard",
                ["properties"] = jsonProps,
            };

            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            Assert.AreEqual(Color.red, mat.GetColor(colorProp));
        }

        [Test]
        public void CreateMaterial_WithJObjectArray_SetsProperty()
        {
            string colorProp = MainColorProperty();
            var props = new JObject();
            props[colorProp] = new JArray(0.0f, 1.0f, 0.0f, 1.0f);

            var paramsObj = new JObject
            {
                ["action"] = "create",
                ["materialPath"] = _matPath,
                ["shader"] = "Standard",
                ["properties"] = props,
            };

            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var mat = AssetDatabase.LoadAssetAtPath<Material>(_matPath);
            Assert.AreEqual(Color.green, mat.GetColor(colorProp));
        }

        [Test]
        public void CreateMaterial_WithEmptyProperties_Succeeds()
        {
            var paramsObj = new JObject
            {
                ["action"] = "create",
                ["materialPath"] = _matPath,
                ["shader"] = "Standard",
                ["properties"] = new JObject(),
            };

            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void CreateMaterial_WithInvalidJsonSyntax_ReturnsDetailedError()
        {
            // Missing closing brace
            string invalidJson = "{\"_Color\": [1,0,0,1]";

            var paramsObj = new JObject
            {
                ["action"] = "create",
                ["materialPath"] = _matPath,
                ["shader"] = "Standard",
                ["properties"] = invalidJson,
            };

            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            Assert.IsFalse(result.Value<bool>("success"));
            string msg = result.Value<string>("error");

            // Verify we get exception details
            Assert.IsTrue(msg.Contains("Invalid JSON"), "Should mention Invalid JSON");
            // Verify the message contains more than just the prefix (has exception details)
            Assert.IsTrue(msg.Length > "Invalid JSON".Length, $"Message should contain exception details. Got: {msg}");
        }

        [Test]
        public void CreateMaterial_WithNullProperty_HandlesGracefully()
        {
            var props = new JObject();
            props["_Color"] = null;

            var paramsObj = new JObject
            {
                ["action"] = "create",
                ["materialPath"] = _matPath,
                ["shader"] = "Standard",
                ["properties"] = props,
            };

            // Should probably succeed but warn or ignore, or fail gracefully
            var result = ToJObject(ManageMaterial.HandleCommand(paramsObj));

            // We accept either success (ignored) or specific error, but not crash
            // The new response format uses a bool "success" field
            var success = result.Value<bool?>("success");
            Assert.IsNotNull(success, "Response should have success field");
        }
    }
}
