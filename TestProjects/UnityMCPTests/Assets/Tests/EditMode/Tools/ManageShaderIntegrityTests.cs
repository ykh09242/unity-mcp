using System;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageShaderIntegrityTests
    {
        private string assetRoot;
        private string shaderName;

        [SetUp]
        public void SetUp()
        {
            string suffix = Guid.NewGuid().ToString("N");
            assetRoot = "Assets/__McpShaderIntegrity_" + suffix;
            shaderName = "ShaderIntegrity_" + suffix;
            Assert.IsFalse(Directory.Exists(FullAssetRoot));
        }

        [TearDown]
        public void TearDown()
        {
            Assert.IsTrue(assetRoot.StartsWith("Assets/__McpShaderIntegrity_", StringComparison.Ordinal));
            Assert.AreEqual(32, assetRoot.Substring("Assets/__McpShaderIntegrity_".Length).Length);
            string resolved = Path.GetFullPath(FullAssetRoot);
            string assets = Path.GetFullPath(Application.dataPath).TrimEnd(Path.DirectorySeparatorChar);
            Assert.IsTrue(resolved.StartsWith(assets + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase));
            if (Directory.Exists(resolved))
            {
                AssetDatabase.DeleteAsset(assetRoot);
                if (Directory.Exists(resolved)) Directory.Delete(resolved, true);
            }
        }

        [Test]
        public void NullRequestReturnsStructuredError()
        {
            AssertFailure(null);
            Assert.IsFalse(Directory.Exists(FullAssetRoot));
        }

        [TestCase("\"garbage\"")]
        [TestCase("{}")]
        [TestCase("[]")]
        [TestCase("null")]
        public void InvalidEncodingFlagReturnsErrorWithoutCreatingFolder(string token)
        {
            var request = Request("create");
            request["contentsEncoded"] = JToken.Parse(token);
            AssertFailure(request);
            Assert.IsFalse(Directory.Exists(FullAssetRoot));
        }

        [TestCase("create", "/w==")]
        [TestCase("create", "wyg=")]
        [TestCase("create", "4oI=")]
        [TestCase("update", "/w==")]
        [TestCase("update", "wyg=")]
        [TestCase("update", "4oI=")]
        public void MalformedUtf8RejectsBeforeFolderCreationOrExistingFileReplacement(string action, string encoded)
        {
            byte[] original = Encoding.UTF8.GetBytes("// original shader bytes 한글");
            if (action == "update") Seed(original);
            var request = Request(action);
            request["contentsEncoded"] = true;
            request["encodedContents"] = encoded;

            AssertFailure(request);

            if (action == "create") Assert.IsFalse(Directory.Exists(FullAssetRoot));
            else CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));
        }

        [TestCase("create")]
        [TestCase("update")]
        public void UnpairedSurrogateRejectsBeforeFolderCreationOrFileTruncation(string action)
        {
            byte[] original = Encoding.UTF8.GetBytes("// original bytes 한글");
            if (action == "update") Seed(original);
            var request = Request(action);
            request["contents"] = "invalid" + '\ud800';

            AssertFailure(request);

            if (action == "create") Assert.IsFalse(Directory.Exists(FullAssetRoot));
            else CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));
        }

        [Test]
        public void ExistingShaderNameRejectsBeforePreparingARequestedFolder()
        {
            if (Shader.Find("Standard") == null) Assert.Ignore("Requires the installed Standard shader for a deterministic name conflict.");
            var request = Request("create");
            request["name"] = "Standard";

            JObject response = AssertFailure(request);

            StringAssert.Contains("already exists", (string)response["error"]);
            Assert.IsFalse(Directory.Exists(FullAssetRoot));
        }

        private string FullAssetRoot => Path.Combine(Application.dataPath, assetRoot.Substring("Assets/".Length));
        private string ShaderPath => Path.Combine(FullAssetRoot, shaderName + ".shader");

        private void Seed(byte[] contents)
        {
            Directory.CreateDirectory(FullAssetRoot);
            File.WriteAllBytes(ShaderPath, contents);
        }

        private JObject Request(string action) => new JObject
        {
            ["action"] = action,
            ["name"] = shaderName,
            ["path"] = assetRoot
        };

        private static JObject AssertFailure(JObject request)
        {
            var response = JObject.FromObject(ManageShader.HandleCommand(request));
            Assert.IsFalse((bool)response["success"], response.ToString());
            Assert.IsNotEmpty((string)response["error"]);
            return response;
        }
    }
}
