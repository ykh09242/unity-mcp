using System;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageScriptFormattingTests
    {
        private string _folder;
        private string _path;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/ScriptFormattingTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            _path = _folder + "/FormattingProbe.cs";
        }

        [TearDown]
        public void TearDown() { AssetDatabase.DeleteAsset(_folder); }

        [TestCase("\n", "  ")]
        [TestCase("\r\n", "  ")]
        [TestCase("\n", "    ")]
        [TestCase("\r\n", "    ")]
        [TestCase("\n", "\t")]
        [TestCase("\r\n", "\t")]
        public void MethodReplacement_AdaptsLocalStyleAndPreservesOutsideText(string eol, string unit)
        {
            string prefix = "// prefix" + eol + "namespace Demo" + eol + "{" + eol + unit + "public class FormattingProbe" + eol + unit + "{" + eol;
            string suffix = eol + unit + "}" + eol + "}" + eol + "// suffix" + eol;
            string before = prefix + unit + unit + "public void Anchor() { }" + suffix;
            Write(before);
            Edit("replace_method", "public void Anchor()\n{\n    int value = 1;\n}\n");
            Assert.AreEqual(prefix + unit + unit + "public void Anchor()" + eol + unit + unit + "{" + eol + unit + unit + unit + "int value = 1;" + eol + unit + unit + "}" + suffix, Read());
        }

        [TestCase("start")]
        [TestCase("end")]
        [TestCase("after")]
        [TestCase("before")]
        public void MethodInsertion_UsesExistingLineBoundariesWithoutGrowingBlankLines(string position)
        {
            const string prefix = "public class FormattingProbe\r\n{\r\n";
            const string anchor = "  public void Anchor() { }\r\n";
            const string suffix = "\r\n}\r\n// suffix\r\n";
            Write(prefix + anchor + suffix);
            Edit("insert_method", "\npublic void Added()\n{\n    int n = 1;\n}\n\n", position);
            string added = "  public void Added()\r\n  {\r\n    int n = 1;\r\n  }\r\n";
            string expected = position == "start" || position == "before" ? prefix + added + anchor + suffix : position == "after" ? prefix + anchor + added + suffix : prefix + anchor + "\r\n" + added + "}\r\n// suffix\r\n";
            Assert.AreEqual(expected, Read());
        }

        [TestCase("@\"a\r\nb\"")]
        [TestCase("$@\"a\r\n{string.Join(\"x\", vals)}\r\nb\"")]
        [TestCase("\"\"\"\n    a\n    b\n    \"\"\"")]
        [TestCase("$$\"\"\"\n    a{{value}}\n    \"\"\"")]
        [TestCase("@\"a\"\"b\r\nc\"")]
        [TestCase("\"\"\"\"\n    a\"\"\"b\n    \"\"\"\"")]
        [TestCase("$@\"a\r\n{1 /* } \"\"\" */ + 2}\r\nb\"")]
        [TestCase("$$\"\"\"\n    a{{1 /* } \"\"\" */ + 2}}\n    \"\"\"")]
        [TestCase("$$\"\"\"\n    a{{ @\"a\\\"\"b\" }}\n    \"\"\"")]
        [TestCase("$@\"a\r\n{ \"\"\"x\"\"\" }\r\nb\"")]
        public void StructuralReplacement_PreservesEveryMultilineLiteralByte(string literal)
        {
            Write("public class FormattingProbe\r\n{\r\n\tpublic void Anchor() { }\r\n}\r\n");
            string snippet = "public void Anchor()\n{\n    var s = " + literal + ";\n}";
            Edit("replace_method", snippet);
            string expected = "public class FormattingProbe\r\n{\r\n\tpublic void Anchor()\r\n\t{\r\n\t\tvar s = " + literal + ";\r\n\t}\r\n}\r\n";
            Assert.AreEqual(expected, Read());
        }

        [Test]
        public void AnchorInsertAndReplace_UseExactLiteralPayloads()
        {
            const string original = "public class FormattingProbe\r\n{\r\n    // anchor\r\n}\r\n";
            Write(original);
            var insert = new JObject { ["mode"] = "anchor_insert", ["anchor"] = "// anchor", ["text"] = "/* x */", ["position"] = "before" };
            Apply(insert);
            Assert.AreEqual(original.Replace("// anchor", "/* x */// anchor"), Read());
            Apply(new JObject { ["mode"] = "anchor_replace", ["anchor"] = "/\\* x \\*/", ["text"] = "/* a\r\nb */" });
            Assert.AreEqual(original.Replace("// anchor", "/* a\r\nb */// anchor"), Read());
        }

        [Test]
        public void EmptyClassInsertion_UsesDeterministicFourSpaceFallback()
        {
            Write("public class FormattingProbe\r\n{\r\n}\r\n");
            Edit("insert_method", "public void Added() { }", "end");
            Assert.AreEqual("public class FormattingProbe\r\n{\r\n    public void Added() { }\r\n}\r\n", Read());
        }

        [Test]
        public void AlreadyIndentedReplacement_IsStableAcrossRepeatedEdits()
        {
            const string original = "public class FormattingProbe\n{\n  public void Anchor()\n  {\n    int n = 1;\n  }\n}\n";
            Write(original);
            string snippet = "  public void Anchor()\n  {\n    int n = 1;\n  }\n";
            Edit("replace_method", snippet);
            Assert.AreEqual(original, Read());
            Edit("replace_method", snippet);
            Assert.AreEqual(original, Read());
        }

        [Test]
        public void ClassReplacement_AdaptsNamespaceIndentAndLeavesSurroundingBytes()
        {
            Write("// prefix\r\nnamespace Demo\r\n{\r\n  public class FormattingProbe\r\n  {\r\n    public void Anchor() { }\r\n  }\r\n}\r\n// suffix\r\n");
            Apply(new JObject { ["mode"] = "replace_class", ["className"] = "FormattingProbe", ["replacement"] = "public class FormattingProbe\n{\n    public void Changed() { }\n}\n" });
            Assert.AreEqual("// prefix\r\nnamespace Demo\r\n{\r\n  public class FormattingProbe\r\n  {\r\n    public void Changed() { }\r\n  }\r\n}\r\n// suffix\r\n", Read());
        }

        [Test]
        public void BeforeInsertion_KeepsDocumentationAndAttributeWithAnchor()
        {
            const string prefix = "public class FormattingProbe\n{\n";
            const string anchor = "  /// <summary>Keep with Anchor.</summary>\n  [Obsolete]\n  public void Anchor() { }\n}\n";
            Write(prefix + anchor);
            Edit("insert_method", "public void Added() { }", "before");
            Assert.AreEqual(prefix + "  public void Added() { }\n" + anchor, Read());
        }

        [Test]
        public void NestedClassInsertion_PreservesClosingIndentAndNoFinalNewline()
        {
            const string prefix = "namespace Demo\r\n{\r\n  public class Outer\r\n  {\r\n    public class FormattingProbe\r\n    {\r\n      public void Anchor() { }\r\n";
            const string suffix = "    }\r\n  }\r\n}";
            Write(prefix + suffix);
            Edit("insert_method", "public void Added() { }", "end");
            Assert.AreEqual(prefix + "      public void Added() { }\r\n" + suffix, Read());
        }

        [Test]
        public void CommentsAndDirectives_DoNotConfuseLiteralDetectionOrIndentInference()
        {
            Write("public class FormattingProbe\r\n{\r\n  public void Anchor() { }\r\n}\r\n");
            Edit("replace_method", "public void Anchor()\n{\n#if DEBUG\n    // @\" and \"\"\" are only comment markers\n    /* \"quoted\"\n       continuation */\n    int n = 1;\n#endif\n}");
            Assert.AreEqual("public class FormattingProbe\r\n{\r\n  public void Anchor()\r\n  {\r\n#if DEBUG\r\n    // @\" and \"\"\" are only comment markers\r\n    /* \"quoted\"\r\n       continuation */\r\n    int n = 1;\r\n#endif\r\n  }\r\n}\r\n", Read());
        }

        [Test]
        public void NestedVerbatimInterpolation_PreservesLiteralBytes()
        {
            const string prefix = "public class FormattingProbe\r\n{\r\n";
            const string suffix = "\r\n}\r\n";
            const string literal = "$@\"first\r\n{ @\"a\\\"\"b\" }\r\nlast\"";
            Write(prefix + "  public void Anchor() { }" + suffix);
            Edit("replace_method", "public void Anchor()\n{\n    var s = " + literal + ";\n}");
            StringAssert.Contains(literal, Read());
            Assert.IsTrue(Read().StartsWith(prefix, StringComparison.Ordinal));
            Assert.IsTrue(Read().EndsWith(suffix, StringComparison.Ordinal));
        }

        [TestCase("replace_method")]
        [TestCase("insert_method")]
        public void FullyUnindentedMethod_AddsLocalNestedCodeIndent(string mode)
        {
            Write("namespace Demo\r\n{\r\n\tpublic class FormattingProbe\r\n\t{\r\n\t\tpublic void Anchor() { }\r\n\t}\r\n}\r\n");
            string method = mode == "insert_method" ? "Added" : "Anchor";
            Edit(mode, "public void " + method + "()\n{\nif (true)\n{\nCall();\n}\n}", "end");
            StringAssert.Contains("\t\tpublic void " + method + "()\r\n\t\t{\r\n\t\t\tif (true)\r\n\t\t\t{\r\n\t\t\t\tCall();\r\n\t\t\t}\r\n\t\t}", Read());
        }

        [Test]
        public void UniformlyIndentedFlatMethod_StillAddsNestedBodyIndent()
        {
            Write("public class FormattingProbe\n{\n  public void Anchor() { }\n}\n");
            Edit("replace_method", "    public void Anchor()\n    {\n    if (true)\n    {\n    Call();\n    }\n    }");
            Assert.AreEqual("public class FormattingProbe\n{\n  public void Anchor()\n  {\n    if (true)\n    {\n      Call();\n    }\n  }\n}\n", Read());
        }

        [Test]
        public void RelativeInitializerAlignment_DoesNotBecomeTheIndentUnit()
        {
            Write("public class FormattingProbe\n{\n  public void Anchor() { }\n}\n");
            Edit("replace_method", "public void Anchor()\n{\n    var xs = new[] {\n      1,\n      2\n    };\n}");
            Assert.AreEqual("public class FormattingProbe\n{\n  public void Anchor()\n  {\n    var xs = new[] {\n      1,\n      2\n    };\n  }\n}\n", Read());
        }

        [Test]
        public void SourceInitializerAlignment_DoesNotBecomeTheLocalIndentUnit()
        {
            Write("public class FormattingProbe\n{\n    public void Anchor()\n    {\n        var xs = new[] {\n          1,\n          2\n        };\n    }\n}\n");
            Edit("replace_method", "public void Anchor()\n{\n    int n = 1;\n}");
            Assert.AreEqual("public class FormattingProbe\n{\n    public void Anchor()\n    {\n        int n = 1;\n    }\n}\n", Read());
        }

        [TestCase("end")]
        [TestCase("before")]
        public void RepeatedInsertions_AddNoBlankLines(string position)
        {
            Write("public class FormattingProbe\r\n{\r\n  public void Anchor() { }\r\n}\r\n");
            Edit("insert_method", "public void A() { }", position);
            Edit("insert_method", "public void B() { }", position);
            string anchor = "  public void Anchor() { }\r\n";
            string added = "  public void A() { }\r\n  public void B() { }\r\n";
            Assert.AreEqual("public class FormattingProbe\r\n{\r\n" + (position == "end" ? anchor + added : added + anchor) + "}\r\n", Read());
        }

        [Test]
        public void LiteralRangeEdit_PreservesExactWhitespaceAndNewlinePayload()
        {
            const string original = "public class FormattingProbe\n{\n    // anchor\n}\n";
            const string payload = "\t // literal\r\n";
            Write(original);
            string hash;
            using (var sha = SHA256.Create()) hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(original))).Replace("-", string.Empty).ToLowerInvariant();
            var response = JObject.FromObject(ManageScript.HandleCommand(new JObject
            {
                ["action"] = "apply_text_edits", ["name"] = "FormattingProbe", ["path"] = _folder,
                ["precondition_sha256"] = hash,
                ["edits"] = new JArray(new JObject { ["startLine"] = 3, ["startCol"] = 1, ["endLine"] = 3, ["endCol"] = 14, ["newText"] = payload }),
                ["options"] = new JObject { ["refresh"] = "deferred", ["validate"] = "syntax" }
            }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("public class FormattingProbe\n{\n" + payload + "\n}\n", Read());
        }

        [Test]
        public void BeforeInsertion_DoesNotMovePastPreviousMethodInlineComment()
        {
            const string prefix = "public class FormattingProbe\n{\n  public void Previous() { } /* previous comment */\n";
            const string suffix = "  public void Anchor() { }\n}\n";
            Write(prefix + suffix);
            Edit("insert_method", "public void Added() { }", "before");
            Assert.AreEqual(prefix + "  public void Added() { }\n" + suffix, Read());
        }

        [Test]
        public void FullyUnindentedClass_AddsLocalNestedCodeIndent()
        {
            Write("namespace Demo\r\n{\r\n  public class FormattingProbe\r\n  {\r\n    public void Anchor() { }\r\n  }\r\n}\r\n");
            Apply(new JObject { ["mode"] = "replace_class", ["className"] = "FormattingProbe", ["replacement"] = "public class FormattingProbe\n{\npublic void Anchor()\n{\nCall();\n}\n}" });
            Assert.AreEqual("namespace Demo\r\n{\r\n  public class FormattingProbe\r\n  {\r\n    public void Anchor()\r\n    {\r\n      Call();\r\n    }\r\n  }\r\n}\r\n", Read());
        }

        [TestCase("end")]
        [TestCase("before")]
        public void AtomicInsertionsAtSameBoundary_KeepOrderingWithoutExtraBlankLine(string position)
        {
            Write(position == "end" ? "public class FormattingProbe {}" : "public class FormattingProbe\n{\n    public void Anchor() { }\n}");
            var a = new JObject { ["mode"] = "insert_method", ["className"] = "FormattingProbe", ["position"] = position, ["beforeMethodName"] = "Anchor", ["replacement"] = "public void A() { }" };
            var b = (JObject)a.DeepClone(); b["replacement"] = "public void B() { }";
            Apply(a, b);
            string expected = position == "end" ? "public class FormattingProbe {\n    public void B() { }\n    public void A() { }\n}" : "public class FormattingProbe\n{\n    public void B() { }\n    public void A() { }\n    public void Anchor() { }\n}";
            Assert.AreEqual(expected, Read());
        }

        private void Edit(string mode, string snippet, string position = null)
        {
            Apply(new JObject { ["mode"] = mode, ["className"] = "FormattingProbe", ["methodName"] = "Anchor", ["replacement"] = snippet, ["position"] = position, ["afterMethodName"] = "Anchor", ["beforeMethodName"] = "Anchor" });
        }

        private void Apply(params JObject[] edits)
        {
            var response = JObject.FromObject(ManageScript.HandleCommand(new JObject { ["action"] = "edit", ["name"] = "FormattingProbe", ["path"] = _folder, ["edits"] = new JArray(edits), ["options"] = new JObject { ["refresh"] = "deferred", ["validate"] = "basic" } }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
        }

        private void Write(string text) { File.WriteAllText(_path, text, new UTF8Encoding(false)); }
        private string Read() { return File.ReadAllText(_path); }
    }
}
