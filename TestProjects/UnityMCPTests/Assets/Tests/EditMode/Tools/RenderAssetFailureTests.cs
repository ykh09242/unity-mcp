using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.TestTools;
using Object = UnityEngine.Object;

namespace MCPForUnityTests.Editor.Tools
{
    public class RenderAssetFailureTests
    {
        private string _root;
        private string _texturePath;
        private Material _material;
        private GameObject _go;
        private string _generatedMaterialPath;

        [SetUp]
        public void SetUp()
        {
            string folderName = "RenderAssetFailure_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", folderName);
            _root = "Assets/" + folderName;
            _texturePath = _root + "/Source.png";
            var source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
            try
            {
                File.WriteAllBytes(AbsolutePath(_texturePath), source.EncodeToPNG());
            }
            finally
            {
                Object.DestroyImmediate(source);
            }
            AssetDatabase.ImportAsset(_texturePath, ImportAssetOptions.ForceSynchronousImport);
            _material = new Material(RenderPipelineUtility.ResolveShader("Standard"));
            AssetDatabase.CreateAsset(_material, _root + "/Source.mat");
            _go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            _go.name = folderName;
            _go.GetComponent<Renderer>().sharedMaterial = _material;

            string materialFolder = "Assets/Materials";
            if (!string.IsNullOrEmpty(_go.scene.path) && _go.scene.path.StartsWith("Assets/"))
                materialFolder = Path.GetDirectoryName(_go.scene.path).Replace('\\', '/') + "/Materials";
            _generatedMaterialPath = $"{materialFolder}/{_go.name}_{_go.GetInstanceIDCompat()}_mat.mat";
        }

        [TearDown]
        public void TearDown()
        {
            if (_go != null) Object.DestroyImmediate(_go);
            if (!string.IsNullOrEmpty(_generatedMaterialPath) && File.Exists(AbsolutePath(_generatedMaterialPath)))
                AssetDatabase.DeleteAsset(_generatedMaterialPath);
            if (!string.IsNullOrEmpty(_root)) AssetDatabase.DeleteAsset(_root);
        }

        private static string AbsolutePath(string assetPath)
        {
            return Path.Combine(Application.dataPath, assetPath.Substring("Assets/".Length));
        }

        private JObject SetRendererColor(string mode, int slot)
        {
            return JObject.FromObject(ManageMaterial.HandleCommand(new JObject
            {
                ["action"] = "set_renderer_color", ["target"] = _go.GetInstanceIDCompat(),
                ["searchMethod"] = "by_id", ["mode"] = mode, ["slot"] = slot,
                ["color"] = new JArray(1, 0, 0, 1)
            }));
        }

        [TestCase("instance", 2)]
        [TestCase("shared", -1)]
        [TestCase("create_unique", 2)]
        [TestCase("unknown", 0)]
        public void RejectedRendererColorPreservesMaterialAndCreatesNoAsset(string mode, int slot)
        {
            var renderer = _go.GetComponent<Renderer>();
            string property = MaterialOps.GetMainColorPropertyName(_material);
            Color originalColor = _material.GetColor(property);

            JObject response = SetRendererColor(mode, slot);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreSame(_material, renderer.sharedMaterial);
            Assert.AreEqual(originalColor, _material.GetColor(property));
            Assert.IsFalse(File.Exists(AbsolutePath(_generatedMaterialPath)));
        }

        [TestCase("shared", -1)]
        [TestCase("instance", 2)]
        [TestCase("unknown", 0)]
        public void RejectedRendererColorDoesNotPopulateEmptyRenderer(string mode, int slot)
        {
            var renderer = _go.GetComponent<Renderer>();
            renderer.sharedMaterials = new Material[0];

            JObject response = SetRendererColor(mode, slot);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsEmpty(renderer.sharedMaterials);
        }

        [Test]
        public void EmptyRendererDefaultSlotStillSucceeds()
        {
            var renderer = _go.GetComponent<Renderer>();
            renderer.sharedMaterials = new Material[0];
            try
            {
                JObject response = SetRendererColor("property_block", 0);
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual(1, renderer.sharedMaterials.Length);
            }
            finally
            {
                Material temporary = renderer.sharedMaterial;
                renderer.sharedMaterials = new Material[0];
                if (temporary != null && !EditorUtility.IsPersistent(temporary)) Object.DestroyImmediate(temporary);
            }
        }

        [Test]
        public void MissingShaderUpdateCreatesNoDirectory()
        {
            string directory = _root + "/MissingParent/Nested";
            var response = JObject.FromObject(ManageShader.HandleCommand(new JObject
            {
                ["action"] = "update", ["path"] = directory, ["name"] = "Missing",
                ["contents"] = "sentinel"
            }));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("not found", response.ToString().ToLowerInvariant());
            Assert.IsFalse(Directory.Exists(AbsolutePath(_root + "/MissingParent")));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void InvalidPixelDataDestroysTemporaryTextureWithoutWriting(bool modify)
        {
            AssetDatabase.LoadAssetAtPath<Texture2D>(_texturePath);
            byte[] original = File.ReadAllBytes(AbsolutePath(_texturePath));
            var before = new HashSet<Texture2D>(Resources.FindObjectsOfTypeAll<Texture2D>());
            string path = modify ? _texturePath : _root + "/Invalid.png";
            JObject parameters = new JObject { ["action"] = modify ? "modify" : "create", ["path"] = path };
            if (modify)
            {
                parameters["setPixels"] = new JObject
                {
                    ["width"] = 2, ["height"] = 2, ["pixels"] = "base64:!invalid"
                };
            }
            else
            {
                parameters["width"] = 2;
                parameters["height"] = 2;
                parameters["pixels"] = "base64:!invalid";
            }

            Texture2D[] leaked = null;
            try
            {
                var response = JObject.FromObject(ManageTexture.HandleCommand(parameters));
                leaked = Resources.FindObjectsOfTypeAll<Texture2D>()
                    .Where(texture => !before.Contains(texture) && !EditorUtility.IsPersistent(texture)).ToArray();
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.IsEmpty(leaked, "Rejected pixel data must release its editable texture.");
                CollectionAssert.AreEqual(original, File.ReadAllBytes(AbsolutePath(_texturePath)));
                if (!modify) Assert.IsFalse(File.Exists(AbsolutePath(path)));
            }
            finally
            {
                if (leaked != null)
                    foreach (Texture2D texture in leaked) Object.DestroyImmediate(texture);
            }
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ImportSettingsOnNonTextureReturnFailure(bool sprite)
        {
            string path = _root + "/Plain.txt";
            File.WriteAllText(AbsolutePath(path), "sentinel");
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            var parameters = new JObject { ["action"] = "set_import_settings", ["path"] = path };
            if (sprite) parameters["as_sprite"] = true;
            else parameters["import_settings"] = new JObject { ["isReadable"] = true };

            var response = JObject.FromObject(ManageTexture.HandleCommand(parameters));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains("TextureImporter", response.ToString());
            Assert.AreEqual("sentinel", File.ReadAllText(AbsolutePath(path)));
        }

        [Test]
        public void MissingStructuredTextureReturnsCallerErrorAndPreservesAssignment()
        {
            string property = MaterialOps.ResolvePropertyName(_material, "_BaseMap");
            Texture original = AssetDatabase.LoadAssetAtPath<Texture>(_texturePath);
            _material.SetTexture(property, original);
            string missingPath = _root + "/Missing.png";
            LogAssert.Expect(LogType.Error, new Regex("\\[ManageAsset\\] Action 'modify' failed.*Texture not found", RegexOptions.Singleline));

            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = "modify", ["path"] = _root + "/Source.mat",
                ["properties"] = new JObject
                {
                    ["texture"] = new JObject { ["name"] = property, ["path"] = missingPath }
                }
            }));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            StringAssert.Contains(missingPath, response.ToString());
            Assert.AreSame(original, _material.GetTexture(property));
        }

        [Test]
        public void ValidAndEmptyStructuredTexturePathsPreserveExistingContract()
        {
            string property = MaterialOps.ResolvePropertyName(_material, "_BaseMap");
            var properties = new JObject
            {
                ["texture"] = new JObject { ["name"] = property, ["path"] = _texturePath }
            };
            Assert.IsTrue(MaterialOps.ApplyProperties(_material, properties, UnityJsonSerializer.Instance));
            Texture assigned = _material.GetTexture(property);
            Assert.AreSame(AssetDatabase.LoadAssetAtPath<Texture>(_texturePath), assigned);

            properties["texture"]["path"] = "";
            Assert.IsFalse(MaterialOps.ApplyProperties(_material, properties, UnityJsonSerializer.Instance));
            Assert.AreSame(assigned, _material.GetTexture(property));
            Assert.IsFalse(MaterialOps.ApplyProperties(_material, new JObject(), UnityJsonSerializer.Instance));
            Assert.AreSame(assigned, _material.GetTexture(property));
        }
    }
}
