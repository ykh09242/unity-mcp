using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageScriptTextEditContractTests
    {
        private bool? _savedEnabled,
            _savedConsent;
        private const string EnabledKey = "MCPForUnity.ToolEnabled.manage_script";
        private const string ConsentKey = "MCPForUnity.ToolEnabled.ExplicitConsent.manage_script";
        private string _folder;
        private string _path;
        private const string Original = "using System;\npublic class ContractEditProbe\n{\n    public void A() { }\n    public void B() { }\n}\n";

        [SetUp]
        public void SetUp()
        {
            _savedEnabled = EditorPrefs.HasKey(EnabledKey) ? EditorPrefs.GetBool(EnabledKey) : (bool?)null;
            _savedConsent = EditorPrefs.HasKey(ConsentKey) ? EditorPrefs.GetBool(ConsentKey) : (bool?)null;
            EditorPrefs.SetBool(EnabledKey, true);
            EditorPrefs.SetBool(ConsentKey, true);
            _folder = "Assets/TextEditContractTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            _path = _folder + "/ContractEditProbe.cs";
            File.WriteAllText(_path, Original, new UTF8Encoding(false));
        }

        [TearDown]
        public void TearDown()
        {
            try
            {
                AssetDatabase.DeleteAsset(_folder);
            }
            finally
            {
                if (_savedEnabled.HasValue)
                    EditorPrefs.SetBool(EnabledKey, _savedEnabled.Value);
                else
                    EditorPrefs.DeleteKey(EnabledKey);
                if (_savedConsent.HasValue)
                    EditorPrefs.SetBool(ConsentKey, _savedConsent.Value);
                else
                    EditorPrefs.DeleteKey(ConsentKey);
            }
        }

        [Test]
        public void RangeAcrossMethods_RemovesAllRequestedText()
        {
            var response = Apply("    public void A() { }\n", endLine: 6);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual("using System;\npublic class ContractEditProbe\n{\n    public void A() { }\n}\n", File.ReadAllText(_path));
        }

        [Test]
        public void MethodHeaderEdit_PreservesDeferredRefreshOption()
        {
            var response = Apply("    public void A() { int n = 1; }\n", endLine: 5);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.IsTrue(response["data"]["scheduledRefresh"].Value<bool>());
            StringAssert.Contains("int n = 1;", File.ReadAllText(_path));
        }

        [Test]
        public void OversizedMethodReplacement_ReturnsTooLargeWithoutWriting()
        {
            var response = Apply("    public void A() { /*" + new string('x', 65536) + "*/ }\n", endLine: 5);

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("too_large", response["data"]["status"].Value<string>());
            Assert.AreEqual(Original, File.ReadAllText(_path));
        }

        private JObject Apply(string newText, int endLine)
        {
            return Apply(
                new JArray(
                    new JObject
                    {
                        ["startLine"] = 4,
                        ["startCol"] = 1,
                        ["endLine"] = endLine,
                        ["endCol"] = 1,
                        ["newText"] = newText,
                    }
                )
            );
        }

        [TestCase("validate_edit")]
        [TestCase("edit")]
        [TestCase("preview_edit")]
        public void InvalidStructuredInput_RejectsWithoutWriting(string action)
        {
            var malformed = new JToken[]
            {
                new JObject
                {
                    ["op"] = "anchor_replace",
                    ["anchor"] = "[",
                    ["text"] = "x",
                },
                new JObject
                {
                    ["op"] = "ANCHOR_REPLACE",
                    ["anchor"] = "[",
                    ["text"] = "x",
                },
                new JObject { ["op"] = "unknown" },
                new JObject
                {
                    ["op"] = "delete_method",
                    ["className"] = "ContractEditProbe",
                    ["methodName"] = 12,
                },
                new JObject
                {
                    ["op"] = "anchor_replace",
                    ["anchor"] = "A",
                    ["text"] = new JArray("x"),
                },
                new JObject
                {
                    ["op"] = "insert_method",
                    ["className"] = "ContractEditProbe",
                    ["replacementBase64"] = "!",
                },
                new JValue("not an edit"),
            };
            var bytes = File.ReadAllBytes(_path);
            var modified = File.GetLastWriteTimeUtc(_path);
            foreach (var invalid in malformed)
            {
                var response = Apply(new JArray(invalid.DeepClone()), action);
                Assert.IsFalse(response.Value<bool>("success"), response.ToString());
                Assert.AreEqual("invalid_edit", response.Value<string>("code"), response.ToString());
                CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
                Assert.AreEqual(modified, File.GetLastWriteTimeUtc(_path));
            }
        }

        [Test]
        public void StructuredPreflight_UsesDotNetSyntaxWithoutMatchingOrReadingScript()
        {
            File.Delete(_path);
            var response = Apply(
                new JArray(
                    new JObject
                    {
                        ["op"] = "AnChOr_RePlAcE",
                        ["anchor"] = @"(?<added>created)\k<added>",
                        ["text"] = "done",
                    }
                ),
                "validate_edit"
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(File.Exists(_path));
        }

        [TestCase("replace_class", "not a class", false)]
        [TestCase("replace_class", "not a class", true)]
        [TestCase("replace_class", "public class ContractEditProbe {", false)]
        [TestCase("replace_method", "public void A() {", false)]
        [TestCase("replace_method", "public void A() {", true)]
        [TestCase("insert_method", "public void Added() {", false)]
        public void StructuredSnippetPreflight_RejectsInvalidReplacementWithoutWriting(string op, string snippet, bool encoded)
        {
            var bytes = File.ReadAllBytes(_path);
            var modified = File.GetLastWriteTimeUtc(_path);
            var edit = new JObject
            {
                ["op"] = op,
                ["className"] = "ContractEditProbe",
                ["methodName"] = "A",
                [encoded ? "replacementBase64" : "replacement"] = encoded ? Convert.ToBase64String(Encoding.UTF8.GetBytes(snippet)) : snippet,
            };
            var response = Apply(new JArray(edit), "validate_edit");

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
            Assert.AreEqual(modified, File.GetLastWriteTimeUtc(_path));
        }

        [TestCase("replace_class", "public class ContractEditProbe { public void Added() { } }")]
        [TestCase("replace_method", "public void Added() { var value = @\"{ literal }\"; }")]
        [TestCase("insert_method", "public void Added() { var value = @\"{ literal }\"; }")]
        [TestCase("replace_method", "public void Added() { /* } unmatched comment ] */ }")]
        [TestCase("insert_method", "public void Added() { var value = $\"value: {1}\"; }")]
        public void StructuredSnippetPreflight_AcceptsValidReplacementWithoutLookingUpTargets(string op, string snippet)
        {
            File.Delete(_path);
            var response = Apply(
                new JArray(
                    new JObject
                    {
                        ["op"] = op,
                        ["className"] = "ContractEditProbe",
                        ["methodName"] = "Added",
                        ["replacement"] = snippet,
                    }
                ),
                "validate_edit"
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(File.Exists(_path));
        }

        [TestCase("apply_text_edits", false)]
        [TestCase("apply_text_edits", true)]
        [TestCase("preview_text_edits", false)]
        [TestCase("preview_text_edits", true)]
        public void MissingOrEmptyTextEdits_KeepOriginalErrorWithoutWriting(string action, bool missing)
        {
            var bytes = File.ReadAllBytes(_path);
            var modified = File.GetLastWriteTimeUtc(_path);
            var response = Apply(missing ? null : new JArray(), action);

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual("No edits provided.", response.Value<string>("error"));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
            Assert.AreEqual(modified, File.GetLastWriteTimeUtc(_path));
        }

        [TestCase("startLine", false)]
        [TestCase("startCol", false)]
        [TestCase("endLine", false)]
        [TestCase("endCol", false)]
        [TestCase("newText", false)]
        [TestCase("startLine", true)]
        [TestCase("startCol", true)]
        [TestCase("endLine", true)]
        [TestCase("endCol", true)]
        [TestCase("newText", true)]
        public void MissingOrNullTextEditField_RejectsEntireBatchWithoutWriting(string missingField, bool explicitNull)
        {
            var invalid = new JObject
            {
                ["startLine"] = 4,
                ["startCol"] = 1,
                ["endLine"] = 5,
                ["endCol"] = 1,
                ["newText"] = "    public void A() { int n = 1; }\n",
            };
            if (explicitNull)
                invalid[missingField] = JValue.CreateNull();
            else
                invalid.Remove(missingField);
            var bytes = File.ReadAllBytes(_path);
            var modified = File.GetLastWriteTimeUtc(_path);
            var response = Apply(
                new JArray(
                    new JObject
                    {
                        ["startLine"] = 5,
                        ["startCol"] = 1,
                        ["endLine"] = 5,
                        ["endCol"] = 1,
                        ["newText"] = "    // valid first edit\n",
                    },
                    invalid
                )
            );

            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("requires startLine/startCol/endLine/endCol", response.Value<string>("error"));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
            Assert.AreEqual(modified, File.GetLastWriteTimeUtc(_path));
        }

        [TestCase("null")]
        [TestCase("true")]
        [TestCase("123")]
        [TestCase("{}")]
        [TestCase("[]")]
        public void NonStringReplacement_RejectsWithoutWriting(string json)
        {
            var edit = new JObject
            {
                ["startLine"] = 4,
                ["startCol"] = 1,
                ["endLine"] = 5,
                ["endCol"] = 1,
                ["newText"] = JToken.Parse(json),
            };
            var response = Apply(new JArray(edit));

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(Original, File.ReadAllText(_path));
        }

        [Test]
        public void EmptyReplacement_DeletesRequestedRange()
        {
            var response = Apply(string.Empty, endLine: 5);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(Original.Replace("    public void A() { }\n", string.Empty), File.ReadAllText(_path));
        }

        [TestCase("basic")]
        [TestCase("standard")]
        public void PublicValidation_RejectsCrossedDelimiters(string level)
        {
            File.WriteAllText(_path, Original.Replace("public void A() { }", "public void A() { ([)] }"), new UTF8Encoding(false));
            var bytes = File.ReadAllBytes(_path);
            var response = JObject.FromObject(
                ManageScript.HandleCommand(
                    new JObject
                    {
                        ["action"] = "validate",
                        ["name"] = "ContractEditProbe",
                        ["path"] = _folder,
                        ["level"] = level,
                    }
                )
            );

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("error", response["data"]["diagnostics"][0].Value<string>("severity"));
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
        }

        [TestCase("apply_text_edits")]
        [TestCase("preview_text_edits")]
        public void CrossedDelimiters_RejectWithoutWriting(string action)
        {
            var bytes = File.ReadAllBytes(_path);
            var modified = File.GetLastWriteTimeUtc(_path);
            var response = Apply(
                new JArray(
                    new JObject
                    {
                        ["startLine"] = 4,
                        ["startCol"] = 1,
                        ["endLine"] = 4,
                        ["endCol"] = 1,
                        ["newText"] = "    ([)]\n",
                    }
                ),
                action
            );

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("unbalanced_braces", response["data"]["status"].Value<string>());
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
            Assert.AreEqual(modified, File.GetLastWriteTimeUtc(_path));
        }

        [TestCase("\"{\\\"preview\\\":true}\"")]
        [TestCase("[]")]
        [TestCase("{\"refresh\":{},\"validate\":[]}")]
        [TestCase("{\"refresh\":123}")]
        [TestCase("{\"validate\":false}")]
        [TestCase("{\"applyMode\":{}}")]
        [TestCase("{\"apply_mode\":[]}")]
        [TestCase("{\"preview\":[]}")]
        public void MalformedOptions_RejectWithoutWritingOrScheduling(string json)
        {
            foreach (string action in new[] { "apply_text_edits", "edit", "preview_text_edits", "preview_edit" })
            {
                var request = BoundaryRequest(action);
                request["options"] = JToken.Parse(json);
                AssertRejectedWithoutChanges(request, "invalid_options");
            }
        }

        [TestCase("null")]
        [TestCase("{\"preview\":\"TrUe\",\"refresh\":\"sync\",\"validate\":\"syntax\",\"applyMode\":\"sequential\"}")]
        [TestCase("{\"refresh\":null,\"validate\":null,\"applyMode\":null}")]
        [TestCase("{\"refresh\":\"\",\"validate\":\"legacy\",\"applyMode\":\"legacy\",\"extension\":{}}")]
        public void ValidOptions_PreservePreviewAndLegacyDefaults(string json)
        {
            foreach (string action in new[] { "preview_text_edits", "preview_edit" })
            {
                var request = BoundaryRequest(action);
                request["options"] = JToken.Parse(json);
                var callbacks = EditorApplication.delayCall;
                var response = JObject.FromObject(ManageScript.HandleCommand(request));

                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                Assert.IsTrue(response["data"].Value<bool>("preview"));
                Assert.AreEqual(Original, File.ReadAllText(_path));
                Assert.AreSame(callbacks, EditorApplication.delayCall);
            }
        }

        [TestCase("missing")]
        [TestCase("null")]
        [TestCase("1234")]
        [TestCase("{}")]
        public void EncodedCreateRequiresStringPayloadBeforeCreatingFolders(string json)
        {
            string folder = _folder + "/Rejected";
            var request = new JObject
            {
                ["action"] = "create",
                ["name"] = "ContractEditProbe",
                ["path"] = folder,
                ["contentsEncoded"] = true,
            };
            if (json != "missing")
                request["encodedContents"] = JToken.Parse(json);
            AssertRejectedWithoutChanges(request, "invalid_contents");
            Assert.IsFalse(Directory.Exists(folder));
            Assert.IsFalse(File.Exists(folder + ".meta"));
        }

        [Test]
        public void MalformedUtf8CreateRejectsBeforeCreatingFolders()
        {
            string folder = _folder + "/Rejected";
            AssertRejectedWithoutChanges(
                new JObject
                {
                    ["action"] = "create",
                    ["name"] = "ContractEditProbe",
                    ["path"] = folder,
                    ["contentsEncoded"] = true,
                    ["encodedContents"] = InvalidUtf8Replacement(),
                }
            );
            Assert.IsFalse(Directory.Exists(folder));
            Assert.IsFalse(File.Exists(folder + ".meta"));
        }

        [TestCase("replace_class")]
        [TestCase("anchor_replace")]
        [TestCase("anchor_insert")]
        public void MalformedUtf8StructuredReplacementRejectsWithoutWritingOrScheduling(string operation)
        {
            var request = BoundaryRequest("edit");
            request["edits"] = new JArray(
                new JObject
                {
                    ["op"] = operation,
                    ["className"] = "ContractEditProbe",
                    ["anchor"] = "public class ContractEditProbe",
                    ["replacementBase64"] = InvalidUtf8Replacement(),
                }
            );
            AssertRejectedWithoutChanges(request, "invalid_edit");
        }

        [TestCase(0xD800)]
        [TestCase(0xDC00)]
        public void InvalidUnicodeRejectsBeforeCreateOrEditEffects(int surrogate)
        {
            string replacement = "public class ContractEditProbe { /* " + (char)surrogate + " */ }";
            var structured = BoundaryRequest("edit");
            structured["edits"][0]["replacement"] = replacement;
            AssertRejectedWithoutChanges(structured, "invalid_edit");

            var text = BoundaryRequest("apply_text_edits");
            text["edits"][0]["newText"] = "// " + (char)surrogate + "\n";
            AssertRejectedWithoutChanges(text);

            string folder = _folder + "/Rejected";
            AssertRejectedWithoutChanges(
                new JObject
                {
                    ["action"] = "create",
                    ["name"] = "ContractEditProbe",
                    ["path"] = folder,
                    ["contents"] = replacement,
                },
                "invalid_contents"
            );
            Assert.IsFalse(Directory.Exists(folder));
            Assert.IsFalse(File.Exists(folder + ".meta"));
        }

        private static string InvalidUtf8Replacement()
        {
            byte[] bytes = Encoding.ASCII.GetBytes("public class ContractEditProbe { /* X */ }");
            bytes[Array.IndexOf(bytes, (byte)'X')] = 0xFF;
            return Convert.ToBase64String(bytes);
        }

        private JObject BoundaryRequest(string action)
        {
            string hash;
            using (var sha = SHA256.Create())
                hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Original))).Replace("-", string.Empty).ToLowerInvariant();
            bool structured = action == "edit" || action == "preview_edit";
            return new JObject
            {
                ["action"] = action,
                ["name"] = "ContractEditProbe",
                ["path"] = _folder,
                ["precondition_sha256"] = hash,
                ["edits"] = structured
                    ? new JArray(
                        new JObject
                        {
                            ["op"] = "replace_class",
                            ["className"] = "ContractEditProbe",
                            ["replacement"] = "public class ContractEditProbe { int X; }",
                        }
                    )
                    : new JArray(
                        new JObject
                        {
                            ["startLine"] = 2,
                            ["startCol"] = 1,
                            ["endLine"] = 2,
                            ["endCol"] = 1,
                            ["newText"] = "//ok\n",
                        }
                    ),
            };
        }

        private void AssertRejectedWithoutChanges(JObject request, string code = null)
        {
            byte[] bytes = File.ReadAllBytes(_path);
            var modified = File.GetLastWriteTimeUtc(_path);
            var entries = Directory.GetFileSystemEntries(_folder);
            var callbacks = EditorApplication.delayCall;

            var response = JObject.FromObject(ManageScript.HandleCommand(request));

            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            if (code != null)
                Assert.AreEqual(code, response.Value<string>("code"), response.ToString());
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
            Assert.AreEqual(modified, File.GetLastWriteTimeUtc(_path));
            CollectionAssert.AreEquivalent(entries, Directory.GetFileSystemEntries(_folder));
            Assert.AreSame(callbacks, EditorApplication.delayCall);
        }

        private JObject Apply(JArray edits, string action = "apply_text_edits")
        {
            string hash;
            using (var sha = SHA256.Create())
                hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Original))).Replace("-", string.Empty).ToLowerInvariant();
            return JObject.FromObject(
                ManageScript.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["name"] = "ContractEditProbe",
                        ["path"] = _folder,
                        ["precondition_sha256"] = hash,
                        ["edits"] = edits,
                        ["options"] = new JObject { ["refresh"] = "deferred", ["validate"] = "syntax" },
                    }
                )
            );
        }
    }

    public class ManageScriptUnicodeColumnTests
    {
        [TestCase("😀x", 1, 1, 0)]
        [TestCase("😀x", 1, 2, 2)]
        [TestCase("😀x", 1, 3, 3)]
        [TestCase("a😀b\r\nc", 1, 3, 3)]
        [TestCase("a😀b\r\nc", 2, 1, 6)]
        [TestCase("é\uAC00x", 1, 3, 2)]
        [TestCase("😀", 1, 3, -1)]
        public void LineColumn_UsesUnicodeCodepoints(string text, int line, int column, int expectedIndex)
        {
            var method = typeof(ManageScript).GetMethod("TryIndexFromLineCol", BindingFlags.Static | BindingFlags.NonPublic);
            var args = new object[] { text, line, column, 0 };

            bool found = (bool)method.Invoke(null, args);

            Assert.AreEqual(expectedIndex >= 0, found);
            Assert.AreEqual(expectedIndex, (int)args[3]);
        }
    }
}
