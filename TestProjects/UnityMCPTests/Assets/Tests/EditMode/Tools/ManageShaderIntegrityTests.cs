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
        private bool shadersDirectoryWasMissing;

        [SetUp]
        public void SetUp()
        {
            string suffix = Guid.NewGuid().ToString("N");
            assetRoot = "Assets/__McpShaderIntegrity_" + suffix;
            shaderName = "ShaderIntegrity_" + suffix;
            Assert.IsFalse(Directory.Exists(FullAssetRoot));
            Assert.IsFalse(File.Exists(RootShaderPath));
            Assert.IsFalse(File.Exists(SiblingShaderPath));
            string shadersDirectory = Path.GetDirectoryName(SiblingShaderPath);
            shadersDirectoryWasMissing = !Directory.Exists(shadersDirectory) && !File.Exists(shadersDirectory + ".meta");
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
                if (Directory.Exists(resolved))
                    Directory.Delete(resolved, true);
            }
            foreach (string relativePath in new[] { "Assets/" + shaderName + ".shader", "Assets/Shaders/" + shaderName + ".shader" })
            {
                string full = Path.Combine(Application.dataPath, relativePath.Substring("Assets/".Length));
                if (!File.Exists(full))
                    continue;
                AssetDatabase.DeleteAsset(relativePath);
                if (File.Exists(full))
                    File.Delete(full);
            }
            string shadersDirectory = Path.GetDirectoryName(SiblingShaderPath);
            if (shadersDirectoryWasMissing && Directory.Exists(shadersDirectory) && Directory.GetFileSystemEntries(shadersDirectory).Length == 0)
            {
                AssetDatabase.DeleteAsset("Assets/Shaders");
                if (Directory.Exists(shadersDirectory))
                    Directory.Delete(shadersDirectory);
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
            byte[] original = Encoding.UTF8.GetBytes("// original shader bytes \uD55C\uAE00");
            if (action == "update")
                Seed(original);
            var request = Request(action);
            request["contentsEncoded"] = true;
            request["encodedContents"] = encoded;

            AssertFailure(request);

            if (action == "create")
                Assert.IsFalse(Directory.Exists(FullAssetRoot));
            else
                CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));
        }

        [TestCase("create")]
        [TestCase("update")]
        public void UnpairedSurrogateRejectsBeforeFolderCreationOrFileTruncation(string action)
        {
            byte[] original = Encoding.UTF8.GetBytes("// original bytes \uD55C\uAE00");
            if (action == "update")
                Seed(original);
            var request = Request(action);
            request["contents"] = "invalid" + '\ud800';

            AssertFailure(request);

            if (action == "create")
                Assert.IsFalse(Directory.Exists(FullAssetRoot));
            else
                CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));
        }

        [Test]
        public void ExistingShaderNameRejectsBeforePreparingARequestedFolder()
        {
            if (Shader.Find("Standard") == null)
                Assert.Ignore("Requires the installed Standard shader for a deterministic name conflict.");
            var request = Request("create");
            request["name"] = "Standard";

            JObject response = AssertFailure(request);

            StringAssert.Contains("already exists", (string)response["error"]);
            Assert.IsFalse(Directory.Exists(FullAssetRoot));
        }

        [TestCase("wyg=")]
        [TestCase("77u/wyg=")]
        [TestCase("//4A2A==")]
        [TestCase("/v/YAA==")]
        [TestCase("//4AAAAAEQA=")]
        [TestCase("AAD+/wARAAA=")]
        public void MalformedDiskEncodingReturnsErrorWithoutReplacingFile(string encoded)
        {
            byte[] original = Convert.FromBase64String(encoded);
            Seed(original);

            JObject response = AssertFailure(Request("read"));

            StringAssert.Contains("read shader", (string)response["error"]);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));
            Assert.IsNull(response["data"]);
        }

        [TestCase("utf8")]
        [TestCase("utf8_bom")]
        [TestCase("utf16le")]
        [TestCase("utf16be")]
        [TestCase("utf32le")]
        [TestCase("utf32be")]
        public void DiskReadPreservesValidUnicodeAndBom(string kind)
        {
            Encoding encoding;
            switch (kind)
            {
                case "utf8":
                    encoding = new UTF8Encoding(false);
                    break;
                case "utf8_bom":
                    encoding = new UTF8Encoding(true);
                    break;
                case "utf16le":
                    encoding = new UnicodeEncoding(false, true);
                    break;
                case "utf16be":
                    encoding = new UnicodeEncoding(true, true);
                    break;
                case "utf32le":
                    encoding = new UTF32Encoding(false, true);
                    break;
                case "utf32be":
                    encoding = new UTF32Encoding(true, true);
                    break;
                default:
                    throw new ArgumentException("Unknown test encoding.", nameof(kind));
            }
            byte[] preamble = encoding.GetPreamble();
            // Preserve an intentional second U+FEFF, as well as a valid replacement character.
            string text = (preamble.Length > 0 ? "\uFEFF" : "") + "// \uD55C\uAE00 😀 \uFFFD\r\n";
            byte[] payload = encoding.GetBytes(text);
            byte[] original = new byte[preamble.Length + payload.Length];
            Array.Copy(preamble, original, preamble.Length);
            Array.Copy(payload, 0, original, preamble.Length, payload.Length);
            Seed(original);

            var response = JObject.FromObject(ManageShader.HandleCommand(Request("read")));

            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual(text, (string)response["data"]["contents"]);
            Assert.IsFalse((bool)response["data"]["contentsEncoded"]);
            Assert.AreEqual(JTokenType.Null, response["data"]["encodedContents"].Type);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));

            Seed(preamble); // Empty files, with or without a BOM, remain valid.
            response = JObject.FromObject(ManageShader.HandleCommand(Request("read")));
            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual("", (string)response["data"]["contents"]);
            CollectionAssert.AreEqual(preamble, File.ReadAllBytes(ShaderPath));
        }

        [TestCase(10000)]
        [TestCase(10001)]
        public void DiskReadPreservesLargeContentEncodingThreshold(int length)
        {
            string text = new string('\uD55C', length);
            byte[] original = Encoding.UTF8.GetBytes(text);
            Seed(original);

            var response = JObject.FromObject(ManageShader.HandleCommand(Request("read")));

            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual(text, (string)response["data"]["contents"]);
            Assert.AreEqual(length > 10000, (bool)response["data"]["contentsEncoded"]);
            if (length > 10000)
                Assert.AreEqual(text, Encoding.UTF8.GetString(Convert.FromBase64String((string)response["data"]["encodedContents"])));
            else
                Assert.AreEqual(JTokenType.Null, response["data"]["encodedContents"].Type);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(ShaderPath));
        }

        [TestCase("create", "Assets")]
        [TestCase("read", "Assets")]
        [TestCase("update", "Assets")]
        [TestCase("delete", "Assets")]
        [TestCase("create", "Assets/")]
        [TestCase("read", "Assets/")]
        [TestCase("update", "Assets/")]
        [TestCase("delete", "Assets/")]
        [TestCase("create", "Assets\\")]
        [TestCase("read", "Assets\\")]
        [TestCase("update", "Assets\\")]
        [TestCase("delete", "Assets\\")]
        public void ExplicitAssetsRootNeverTargetsSameNameShadersSibling(string action, string path)
        {
            string original = "Shader \"" + shaderName + "_Root\" { SubShader { Pass {} } }";
            string sibling = "Shader \"" + shaderName + "_Sibling\" { SubShader { Pass {} } }";
            string replacement = "Shader \"" + shaderName + "_Changed\" { SubShader { Pass {} } }";
            Directory.CreateDirectory(Path.GetDirectoryName(SiblingShaderPath));
            File.WriteAllText(SiblingShaderPath, sibling, new UTF8Encoding(false));
            AssetDatabase.ImportAsset("Assets/Shaders/" + shaderName + ".shader");
            if (action != "create")
            {
                File.WriteAllText(RootShaderPath, original, new UTF8Encoding(false));
                AssetDatabase.ImportAsset("Assets/" + shaderName + ".shader");
            }
            var request = Request(action);
            request["path"] = path;
            request["contents"] = replacement;

            var response = JObject.FromObject(ManageShader.HandleCommand(request));

            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual(sibling, File.ReadAllText(SiblingShaderPath));
            if (action == "delete")
                Assert.IsFalse(File.Exists(RootShaderPath));
            else if (action == "read")
                Assert.AreEqual(original, (string)response["data"]["contents"]);
            else
                Assert.AreEqual(replacement, File.ReadAllText(RootShaderPath));
            if (action != "delete")
                Assert.AreEqual("Assets/" + shaderName + ".shader", (string)response["data"]["path"]);
        }

        [TestCase(null)]
        [TestCase("")]
        public void OmittedOrEmptyPathKeepsShadersDefault(string path)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(SiblingShaderPath));
            File.WriteAllText(SiblingShaderPath, "// default shader", new UTF8Encoding(false));
            var request = Request("read");
            if (path == null)
                request.Remove("path");
            else
                request["path"] = path;

            var response = JObject.FromObject(ManageShader.HandleCommand(request));

            Assert.IsTrue((bool)response["success"], response.ToString());
            Assert.AreEqual("Assets/Shaders/" + shaderName + ".shader", (string)response["data"]["path"]);
            Assert.AreEqual("// default shader", (string)response["data"]["contents"]);
        }

        private string RootShaderPath => Path.Combine(Application.dataPath, shaderName + ".shader");
        private string SiblingShaderPath => Path.Combine(Application.dataPath, "Shaders", shaderName + ".shader");
        private string FullAssetRoot => Path.Combine(Application.dataPath, assetRoot.Substring("Assets/".Length));
        private string ShaderPath => Path.Combine(FullAssetRoot, shaderName + ".shader");

        private void Seed(byte[] contents)
        {
            Directory.CreateDirectory(FullAssetRoot);
            File.WriteAllBytes(ShaderPath, contents);
        }

        private JObject Request(string action) =>
            new JObject
            {
                ["action"] = action,
                ["name"] = shaderName,
                ["path"] = assetRoot,
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
