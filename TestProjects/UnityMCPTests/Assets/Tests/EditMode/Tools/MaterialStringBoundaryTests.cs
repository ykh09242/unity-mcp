using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.EditMode.Tools
{
    [Parallelizable(ParallelScope.None)]
    public class MaterialStringBoundaryTests
    {
        private string root;
        private string folderGuid;
        private bool ownsFolder;

        [SetUp]
        public void SetUp()
        {
            ownsFolder = false;
            root = "Assets/__McpMaterialStringBoundary_" + Guid.NewGuid().ToString("N");
            Assert.IsFalse(AssetDatabase.IsValidFolder(root));
            Assert.IsFalse(Directory.Exists(Absolute(root)));
            folderGuid = AssetDatabase.CreateFolder("Assets", Path.GetFileName(root));
            Assert.AreEqual(root, AssetDatabase.GUIDToAssetPath(folderGuid));
            ownsFolder = true;
        }

        [TearDown]
        public void TearDown()
        {
            if (!ownsFolder)
                return;
            Assert.AreEqual(root, AssetDatabase.GUIDToAssetPath(folderGuid));
            Assert.AreEqual(folderGuid, AssetDatabase.AssetPathToGUID(root));
            foreach (string guid in AssetDatabase.FindAssets("", new[] { root }))
            foreach (Object obj in AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(guid)))
                if (obj != null)
                    Undo.ClearUndo(obj);
            Assert.IsTrue(AssetDatabase.DeleteAsset(root));
            ownsFolder = false;
        }

        [TestCase("false")]
        [TestCase("0")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void MalformedShaderRejectsBeforeMaterialAllocationAndAssetWrite(string json)
        {
            string path = root + "/Rejected.mat";
            int before = Resources.FindObjectsOfTypeAll(typeof(Material)).Length;
            var response = Call(
                new JObject
                {
                    ["action"] = "create",
                    ["materialPath"] = path,
                    ["shader"] = JToken.Parse(json),
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'shader'", response.Value<string>("error"));
            Assert.AreEqual(before, Resources.FindObjectsOfTypeAll(typeof(Material)).Length);
            Assert.IsFalse(File.Exists(Absolute(path)));
            Assert.IsTrue(string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path)));
        }

        [TestCase("create", false)]
        [TestCase("create", true)]
        [TestCase("set_material_color", false)]
        [TestCase("set_material_color", true)]
        [TestCase("set_material_shader_property", false)]
        [TestCase("set_material_shader_property", true)]
        [TestCase("assign_material_to_renderer", false)]
        [TestCase("assign_material_to_renderer", true)]
        [TestCase("get_material_info", false)]
        [TestCase("get_material_info", true)]
        public void ContainerMaterialPathRejectsAtTheUsedParameterBoundary(string action, bool array)
        {
            // All serialized path text remains inside the owned fixture, even on older handlers.
            JToken path = array ? new JArray(root + "/Rejected.mat") : new JObject { ["ownedPath"] = root + "/Rejected.mat" };
            int before = Resources.FindObjectsOfTypeAll(typeof(Material)).Length;
            var response = Call(
                new JObject
                {
                    ["action"] = action,
                    ["materialPath"] = path,
                    ["property"] = "_Color",
                    ["color"] = new JArray(1, 0, 0, 1),
                    ["value"] = new JArray(1, 0, 0, 1),
                    ["target"] = "__MissingMaterialBoundaryTarget",
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("'materialPath'", response.Value<string>("error"));
            Assert.AreEqual(before, Resources.FindObjectsOfTypeAll(typeof(Material)).Length);
            Assert.IsFalse(File.Exists(Absolute(root + "/Rejected.mat")));
        }

        [TestCase("omitted")]
        [TestCase("null")]
        [TestCase("empty")]
        public void MissingMaterialPathKeepsTheExistingRequiredError(string form)
        {
            var request = new JObject { ["action"] = "create" };
            if (form != "omitted")
                request["materialPath"] = form == "null" ? JValue.CreateNull() : new JValue("");
            var response = Call(request);
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("materialPath is required", response.Value<string>("error"));
        }

        [Test]
        public void PingIgnoresUnusedMalformedCreationFields()
        {
            Assert.IsTrue(
                Call(
                        new JObject
                        {
                            ["action"] = "ping",
                            ["materialPath"] = false,
                            ["shader"] = new JArray(),
                        }
                    )
                    .Value<bool>("success")
            );
        }

        [TestCase("false")]
        [TestCase("0")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void ReadOnlyInfoRejectsMalformedScalarAndContainerPaths(string json)
        {
            var response = Call(new JObject { ["action"] = "get_material_info", ["materialPath"] = JToken.Parse(json) });
            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("'materialPath'", response.Value<string>("error"));
        }

        [Test]
        public void ValidOwnedPathSetterRetainsMaterialIdentityAndZeroColor()
        {
            string path = root + "/Fixture.mat";
            Material material = OwnedMaterial(path);
            string property = material.HasProperty("_BaseColor") ? "_BaseColor" : "_Color";
            if (!material.HasProperty(property))
                Assert.Ignore("The available fixture shader has no supported color property.");
            string guid = AssetDatabase.AssetPathToGUID(path);
            var response = Call(
                new JObject
                {
                    ["action"] = "set_material_color",
                    ["materialPath"] = path,
                    ["color"] = new JArray(0, 0, 0, 0),
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(material == AssetDatabase.LoadAssetAtPath<Material>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.AreEqual(new Color(0, 0, 0, 0), material.GetColor(property));
        }

        [Test]
        public void ValidOwnedCollisionRetainsIdentityGuidAndBytes()
        {
            string path = root + "/Fixture.mat";
            Material material = OwnedMaterial(path);
            AssetDatabase.SaveAssets();
            string guid = AssetDatabase.AssetPathToGUID(path);
            byte[] bytes = File.ReadAllBytes(Absolute(path));
            var response = Call(
                new JObject
                {
                    ["action"] = "create",
                    ["materialPath"] = path,
                    ["shader"] = material.shader.name,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("already exists", response.Value<string>("error"));
            Assert.IsTrue(material == AssetDatabase.LoadAssetAtPath<Material>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(Absolute(path)));
        }

        private Material OwnedMaterial(string path)
        {
            Shader shader = Shader.Find("Standard") ?? Shader.Find("Unlit/Color");
            if (shader == null)
                Assert.Ignore("A fixture shader is required.");
            var material = new Material(shader);
            try
            {
                AssetDatabase.CreateAsset(material, path);
                Assert.IsTrue(AssetDatabase.Contains(material));
                return material;
            }
            finally
            {
                if (!AssetDatabase.Contains(material))
                    Object.DestroyImmediate(material);
            }
        }

        private static JObject Call(JObject request) => JObject.FromObject(ManageMaterial.HandleCommand(request));

        private string Absolute(string path)
        {
            Assert.IsTrue(path == root || path.StartsWith(root + "/", StringComparison.Ordinal));
            return Path.Combine(Application.dataPath, path.Substring(7).Replace('/', Path.DirectorySeparatorChar));
        }
    }
}
