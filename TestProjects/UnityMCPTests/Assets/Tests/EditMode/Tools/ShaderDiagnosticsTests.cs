using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ShaderDiagnosticsTests
    {
        private string directory;
        private string path;

        [SetUp]
        public void SetUp()
        {
            directory = "Assets/__McpShaderDiagnostics_" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(directory);
            path = directory + "/Fixture.shader";
            File.WriteAllText(
                path,
                "Shader \"Hidden/"
                    + Guid.NewGuid().ToString("N")
                    + "\" { SubShader { Pass { HLSLPROGRAM\n"
                    + "#pragma vertex Vert\n#pragma fragment Frag\nfloat4 Vert(float4 p:POSITION):SV_POSITION { return p; }\n"
                    + "half4 Frag():SV_Target { return half4(1,1,1,1); }\nENDHLSL\n} } }"
            );
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
        }

        [TearDown]
        public void TearDown() => AssetDatabase.DeleteAsset(directory);

        private JObject Call(string action, JObject variant = null)
        {
            var args = new JObject { ["action"] = action, ["path"] = path };
            if (variant != null)
                args["variant"] = variant;
            return JObject.FromObject(ManageShader.HandleCommand(args));
        }

        private static JObject Variant() =>
            new JObject
            {
                ["shader_type"] = "Vertex",
                ["platform"] = "D3D",
                ["build_target"] = "StandaloneWindows64",
                ["keywords"] = new JArray(),
            };

        [Test]
        public void DiagnosticsPreserveSourceAndTarget()
        {
            var before = File.ReadAllBytes(path);
            var target = EditorUserBuildSettings.activeBuildTarget;
            var result = Call("diagnostics");
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("observed_shader_messages", result["data"]["compilation"].Value<string>("scope"));
            Assert.AreEqual(false, result["data"]["compilation"].Value<bool>("all_variants_checked"));
            Assert.That(result["data"]["passes"].HasValues);
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.AreEqual(target, EditorUserBuildSettings.activeBuildTarget);
        }

        [Test]
        public void CompileVariantReportsOnlySelectedTarget()
        {
            var before = File.ReadAllBytes(path);
            var target = EditorUserBuildSettings.activeBuildTarget;
            var result = Call("compile_variant", Variant());
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("success", result["data"].Value<string>("outcome"), result.ToString());
            Assert.Greater(result["data"].Value<int>("bytecode_size"), 0);
            Assert.AreEqual("D3D", result["data"]["variant"].Value<string>("platform"));
            Assert.AreEqual(false, result["data"]["compilation"].Value<bool>("all_platforms_checked"));
            CollectionAssert.AreEqual(before, File.ReadAllBytes(path));
            Assert.AreEqual(target, EditorUserBuildSettings.activeBuildTarget);
        }

        [TestCase("platform", "123")]
        [TestCase("shader_type", "Bogus")]
        [TestCase("build_target", "NoTarget")]
        [TestCase("unexpected", "x")]
        public void InvalidVariantFieldsAreRejected(string key, string value)
        {
            var variant = Variant();
            variant[key] = value;
            Assert.IsFalse(Call("compile_variant", variant).Value<bool>("success"));
        }

        [Test]
        public void MissingProgramCannotClaimSuccessfulCompilation()
        {
            var variant = Variant();
            variant["shader_type"] = "Geometry";
            var result = Call("compile_variant", variant);
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("no_program", result["data"].Value<string>("outcome"), result.ToString());
            Assert.AreEqual(0, result["data"].Value<int>("bytecode_size"));
        }
    }
}
