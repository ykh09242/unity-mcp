using System;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageScriptValidationReportTests
    {
        private readonly FieldInfo consent = typeof(ManageScript).GetField("_explicitConsentForTests", BindingFlags.Static | BindingFlags.NonPublic);
        private readonly FieldInfo enabled = typeof(ManageScript).GetField("_parentEnabledForTests", BindingFlags.Static | BindingFlags.NonPublic);
        private object previousConsent,
            previousEnabled;

        [SetUp]
        public void SetUp()
        {
            previousConsent = consent.GetValue(null);
            previousEnabled = enabled.GetValue(null);
            consent.SetValue(null, (Func<bool>)(() => true));
            enabled.SetValue(null, (Func<bool>)(() => true));
        }

        [TearDown]
        public void TearDown()
        {
            consent.SetValue(null, previousConsent);
            enabled.SetValue(null, previousEnabled);
        }

        private JObject ValidateReport(string contents, string level)
        {
            string name = "ValidationReport" + Guid.NewGuid().ToString("N");
            string file = Path.Combine(Application.dataPath, name + ".cs");
            try
            {
                File.WriteAllText(file, contents);
                return JObject.FromObject(
                    ManageScript.HandleCommand(
                        new JObject
                        {
                            ["action"] = "validate",
                            ["name"] = name,
                            ["path"] = "Assets",
                            ["level"] = level,
                        }
                    )
                );
            }
            finally
            {
                File.Delete(file);
            }
        }

        [Test]
        public void ValidationReport_Basic_ReportsStructuralScope()
        {
            // Given: balanced source whose missing external type is outside basic checks.
            string code = "partial class Example { MissingExternalType value; }";
            // When: the read-only endpoint performs basic validation.
            var report = ValidateReport(code, "basic");
            // Then: success identifies structural checks without claiming compilation.
            Assert.IsTrue((bool)report["success"]);
            var validation = report["data"]["validation"];
            Assert.IsNotNull(validation);
            Assert.AreEqual("basic", (string)validation["requestedLevel"]);
            Assert.AreEqual("basic_structural", (string)validation["effectiveLevel"]);
            Assert.AreEqual("single_source", (string)validation["context"]["scope"]);
            Assert.IsFalse((bool)validation["capabilities"]["syntax"]);
            Assert.IsFalse((bool)validation["capabilities"]["projectCompilation"]);
            Assert.IsFalse((bool)validation["context"]["partialTypes"]);
            Assert.IsNotEmpty((JArray)validation["limitations"]);
        }

        [TestCase("// GameObject.Find(\"x\"); FindObjectOfType<Example>();")]
        [TestCase("/* GameObject.Find(\"x\"); FindObjectOfType<Example>(); */")]
        [TestCase("string value = \"GameObject.Find FindObjectOfType\";")]
        public void ValidationReport_NonCodeCalls_DoNotWarn(string body)
        {
            // Given: expensive-call names in a comment or literal only.
            string code = "class Example { void Update() { " + body + "\n } }";
            // When: standard validation checks the script.
            var report = ValidateReport(code, "standard");
            // Then: non-executable text does not trigger performance warnings.
            Assert.IsFalse(report["data"]["diagnostics"].Any(d => ((string)d["message"]).Contains("in Update")));
        }

        [Test]
        public void ValidationReport_CallOutsideUpdate_DoesNotClaimUpdateCall()
        {
            // Given: a real expensive call in Start and an empty Update method.
            string code = "class Example { void Start() { GameObject.Find(\"x\"); } void Update() {} }";
            // When: standard validation checks performance rules.
            var report = ValidateReport(code, "standard");
            // Then: it does not attribute the Start call to Update.
            Assert.IsFalse(report["data"]["diagnostics"].Any(d => ((string)d["message"]).Contains("GameObject.Find in Update")));
        }

        [Test]
        public void ValidationReport_UpdateCall_ReportsSourceLocation()
        {
            // Given: a spaced Update declaration and a call at a hand-counted location.
            string code = "class Example\n{\n    void Update ()\n    {\n        GameObject.Find(\"x\");\n    }\n}";
            // When: standard validation finds the executable call.
            var report = ValidateReport(code, "standard");
            // Then: the warning points to the call, using one-based UTF-16 coordinates.
            var warning = report["data"]["diagnostics"].SingleOrDefault(d => (string)d["id"] == "MCPUNITY001");
            Assert.IsNotNull(warning, report.ToString());
            Assert.AreEqual(5, (int)warning["line"]);
            Assert.AreEqual(9, (int)warning["col"]);
            Assert.AreEqual("warning", (string)warning["severity"]);
            StringAssert.StartsWith("Assets/ValidationReport", (string)warning["file"]);
        }

        private JObject ValidateContextFixture(string level)
        {
            const string name = "ScriptValidationContextFixture";
            // The warm runner may move the fixture under its existing Assets layout.
            string file = Directory.GetFiles(Application.dataPath, name + ".cs", SearchOption.AllDirectories).Single();
            string relative = "Assets" + file.Substring(Application.dataPath.Length).Replace('\\', '/');
            return JObject.FromObject(
                ManageScript.HandleCommand(
                    new JObject
                    {
                        ["action"] = "validate",
                        ["name"] = name,
                        ["path"] = Path.GetDirectoryName(relative).Replace('\\', '/'),
                        ["level"] = level,
                    }
                )
            );
        }

        [Test]
        public void ValidationReport_Standard_DefinesDoNotClaimPartialSemantics()
        {
            // Given: an imported source with UNITY_EDITOR and a cross-file partial member.
            // When: standard validation uses only the owning assembly's syntax context.
            var report = ValidateContextFixture("standard");
            // Then: supported defines prevent the inactive #error without claiming compilation.
            var validation = report["data"]["validation"];
            Assert.IsNotNull(validation);
            Assert.IsFalse((bool)validation["capabilities"]["projectCompilation"]);
            Assert.IsFalse((bool)validation["context"]["partialTypes"]);
            if ((string)validation["engine"] == "roslyn")
            {
                Assert.IsTrue((bool)report["success"], report.ToString());
                Assert.IsTrue((bool)validation["context"]["defines"]);
                Assert.IsNotEmpty((string)validation["context"]["assembly"]);
            }
            else
            {
                Assert.AreEqual("standard_heuristic", (string)validation["effectiveLevel"]);
                Assert.IsFalse((bool)validation["context"]["defines"]);
            }
        }

        [Test]
        public void ValidationReport_Strict_UsesAssemblyPartialContextWhenAvailable()
        {
            // Given: a real asmdef-owned partial class split across two imported sources.
            // When: strict validation uses its existing compiler path when available.
            var report = ValidateContextFixture("strict");
            // Then: compilation distinguishes an owning-assembly context from heuristic fallback.
            var validation = report["data"]["validation"];
            Assert.IsNotNull(validation);
            Assert.IsFalse((bool)validation["capabilities"]["projectCompilation"]);
            if ((string)validation["engine"] == "roslyn")
            {
                Assert.IsTrue((bool)report["success"], report.ToString());
                Assert.AreEqual("strict_assembly_semantic", (string)validation["effectiveLevel"]);
                Assert.AreEqual("project_assembly", (string)validation["context"]["scope"]);
                Assert.IsTrue((bool)validation["context"]["defines"]);
                Assert.IsTrue((bool)validation["context"]["partialTypes"]);
                Assert.Greater((int)validation["context"]["sourceFileCount"], 1);
            }
            else
            {
                Assert.AreEqual("strict_heuristic", (string)validation["effectiveLevel"]);
                Assert.IsFalse((bool)validation["capabilities"]["semanticCompilation"]);
                Assert.IsFalse((bool)validation["context"]["partialTypes"]);
                Assert.IsNotEmpty((JArray)validation["limitations"]);
            }
        }

        [Test]
        public void ValidationReport_Strict_MissingExternalTypeHasStableLocationOrExplicitGap()
        {
            // Given: a missing external type in an otherwise balanced source.
            string code = "class Example\n{\n    MissingExternalType value;\n}";
            // When: strict validation attempts semantic checks.
            var report = ValidateReport(code, "strict");
            // Then: compiler-enabled validation locates the missing reference; fallback discloses its gap.
            var validation = report["data"]["validation"];
            Assert.IsNotNull(validation);
            if ((bool)validation["capabilities"]["semanticCompilation"])
            {
                Assert.IsFalse((bool)report["success"]);
                var diagnostic = report["data"]["diagnostics"].SingleOrDefault(d => (string)d["id"] == "CS0246");
                Assert.IsNotNull(diagnostic, report.ToString());
                Assert.AreEqual(3, (int)diagnostic["line"]);
                Assert.AreEqual(5, (int)diagnostic["col"]);
                Assert.AreEqual("error", (string)diagnostic["severity"]);
            }
            else
            {
                Assert.IsFalse((bool)validation["context"]["references"]);
                Assert.IsNotEmpty((JArray)validation["limitations"]);
            }
        }
    }
}
