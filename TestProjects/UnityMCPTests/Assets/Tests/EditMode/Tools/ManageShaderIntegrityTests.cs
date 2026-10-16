using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEditor.Rendering;
using UnityEngine;
using UnityEngine.TestTools;

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

        [TestCase(false)]
        [TestCase(true)]
        public void SavedShaderReportsObservedDiagnosticsAndRetainsSourceAndGuid(bool invalid)
        {
            // Given an owned valid imported shader and a requested replacement revision.
            var create = Request("create");
            create["contents"] = ValidShader();
            var created = JObject.FromObject(ManageShader.HandleCommand(create));
            Assert.IsTrue(created.Value<bool>("success"), created.ToString());
            string path = assetRoot + "/" + shaderName + ".shader";
            string guid = AssetDatabase.AssetPathToGUID(path);
            string source = invalid
                ? "Shader \"" + shaderName + "\" { SubShader { Pass { DefinitelyInvalidSyntax } } }\r\n// Preserve source 한글"
                : ValidShader() + "\r\n// Preserve source 한글";
            var update = Request("update");
            update["contents"] = source;

            // When the handler saves and synchronously imports the requested shader.
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            JObject response;
            try
            {
                LogAssert.ignoreFailingMessages = invalid;
                response = JObject.FromObject(ManageShader.HandleCommand(update));
            }
            finally
            {
                LogAssert.ignoreFailingMessages = previousIgnore;
            }

            // Then saving is distinct from compiler observations and source/GUID survive errors.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(new UTF8Encoding(false).GetBytes(source), File.ReadAllBytes(ShaderPath));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
            Assert.IsTrue(response["data"].Value<bool>("saved"));
            Assert.IsTrue(response["data"].Value<bool>("imported"));
            var diagnostics = response["data"]["compilation"];
            Assert.IsNotNull(diagnostics, "Import success must include the actual compiler observation.");
            Assert.AreEqual(invalid, diagnostics.Value<bool>("has_errors"));
            Assert.IsFalse(diagnostics.Value<bool>("all_variants_checked"));
            Assert.IsFalse(diagnostics.Value<bool>("all_platforms_checked"));
            Assert.AreEqual(EditorUserBuildSettings.activeBuildTarget.ToString(), diagnostics.Value<string>("active_build_target"));
            if (invalid)
            {
                Assert.AreEqual("errors_reported", diagnostics.Value<string>("status"));
                Assert.Greater(diagnostics.Value<int>("error_count"), 0);
                var error = diagnostics["messages"].First(message => message.Value<string>("severity") == "error");
                Assert.IsNotEmpty(error.Value<string>("message"));
                Assert.IsNotNull(error["file"]);
                Assert.IsNotNull(error["line"]);
                Assert.IsNotNull(error["platform"]);
                StringAssert.Contains("errors", response.Value<string>("message").ToLowerInvariant());
            }
            else
                Assert.That(diagnostics.Value<string>("status"), Is.EqualTo("no_errors_reported").Or.EqualTo("pending"));
        }

        [Test]
        public void CreatedShaderReportsSaveImportAndObservationLimits()
        {
            // Given a valid ShaderLab source.
            var request = Request("create");
            request["contents"] = ValidShader();

            // When the shader is created through the production tool.
            var response = JObject.FromObject(ManageShader.HandleCommand(request));

            // Then the create path reports saving/importing without claiming all variants compiled.
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(response["data"].Value<bool>("saved"));
            Assert.IsTrue(response["data"].Value<bool>("imported"));
            Assert.IsNotNull(response["data"]["compilation"]);
            Assert.IsFalse(response["data"]["compilation"].Value<bool>("all_variants_checked"));
            Assert.IsNotEmpty(response["data"]["compilation"].Value<string>("limitations"));
        }

        [Test]
        public void DiagnosticBoundsRetainFullSeverityCountsAndPendingIsExplicit()
        {
            // Given more observed messages than the response limit, including a late error.
            var messages = Enumerable.Range(0, 150).Select(_ => new ShaderMessage(new string('x', 6000), ShaderCompilerMessageSeverity.Warning)).ToList();
            messages.Add(new ShaderMessage("late observed error", ShaderCompilerMessageSeverity.Error));
            var builder = typeof(ManageShader).GetMethod("BuildCompilationEvidence", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(builder);

            // When the production evidence builder bounds serialized diagnostic output.
            var report = JObject.FromObject(builder.Invoke(null, new object[] { messages.ToArray(), false, true, null }));
            var pending = JObject.FromObject(builder.Invoke(null, new object[] { Array.Empty<ShaderMessage>(), false, true, null }));
            var unavailable = JObject.FromObject(builder.Invoke(null, new object[] { Array.Empty<ShaderMessage>(), false, false, "Owned unavailable query" }));

            // Then truncation never hides an observed error or invents completed compilation.
            Assert.AreEqual("errors_reported", report.Value<string>("status"));
            Assert.AreEqual(1, report.Value<int>("error_count"));
            Assert.AreEqual(150, report.Value<int>("warning_count"));
            Assert.AreEqual(151, report.Value<int>("total_messages"));
            Assert.IsTrue(report.Value<bool>("truncated"));
            Assert.LessOrEqual(((JArray)report["messages"]).Count, 100);
            Assert.IsTrue(report["messages"].All(message => message.Value<string>("message").Length <= 2048));
            Assert.AreEqual("pending", pending.Value<string>("status"));
            Assert.AreEqual("unavailable", unavailable.Value<string>("status"));
            Assert.AreEqual(JTokenType.Null, unavailable["has_errors"].Type);
        }

        [Test]
        public void ObservedPlatformCompilerErrorPreservesPlatformMetadata()
        {
            // Given a native D3D11 compiler and a diagnostic specific to that backend.
            if (SystemInfo.graphicsDeviceType != UnityEngine.Rendering.GraphicsDeviceType.Direct3D11)
                Assert.Ignore("The platform-specific fixture requires an active native D3D11 device.");
            var request = Request("create");
            request["contents"] =
                "Shader \""
                + shaderName
                + "\" { SubShader { Pass { HLSLPROGRAM\n"
                + "#pragma vertex vert\n#pragma fragment frag\n"
                + "float4 vert(float4 p : POSITION) : SV_POSITION { return p; }\n"
                + "float4 frag() : SV_Target {\n#if defined(SHADER_API_D3D11)\n#error SPEC02_PLATFORM_DIAGNOSTIC\n#endif\nreturn 1; }\n"
                + "ENDHLSL } } }";
            string path = assetRoot + "/" + shaderName + ".shader";
            bool previousIgnore = LogAssert.ignoreFailingMessages;
            Material material = null;
            try
            {
                LogAssert.ignoreFailingMessages = true;
                var response = JObject.FromObject(ManageShader.HandleCommand(request));
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                var shader = AssetDatabase.LoadAssetAtPath<Shader>(path);
                Assert.IsNotNull(shader);
                material = new Material(shader);
                // Only the fixture requests native pass compilation; the tool reports observations.
                ShaderUtil.CompilePass(material, 0, true);
                var observed = ShaderUtil.GetShaderMessages(shader).Where(message => message.platform != ShaderCompilerPlatform.None).ToArray();
                if (observed.Length == 0)
                    Assert.Ignore("This Editor did not expose platform-tagged diagnostics after native pass compilation.");

                // When the production diagnostic collector reads those observed compiler messages.
                var collector = typeof(ManageShader).GetMethod("CollectCompilationEvidence", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(collector);
                var evidence = JObject.FromObject(collector.Invoke(null, new object[] { path }));

                // Then the actual backend tag and error severity survive serialization.
                Assert.AreEqual("errors_reported", evidence.Value<string>("status"));
                Assert.That(evidence["messages"].Values<string>("platform"), Does.Contain(observed[0].platform.ToString()));
                Assert.That(evidence["messages"].Values<string>("severity"), Does.Contain("error"));
            }
            finally
            {
                if (material != null)
                    UnityEngine.Object.DestroyImmediate(material);
                LogAssert.ignoreFailingMessages = previousIgnore;
            }
        }

        private string ValidShader() => "Shader \"" + shaderName + "\" { SubShader { Pass {} } }";

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
