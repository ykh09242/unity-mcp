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
    public class ManageScriptPreviewTests
    {
        private bool? _savedEnabled,
            _savedConsent;
        private const string EnabledKey = "MCPForUnity.ToolEnabled.manage_script";
        private const string ConsentKey = "MCPForUnity.ToolEnabled.ExplicitConsent.manage_script";
        private string _folder;
        private string _path;
        private const string Original = "namespace Demo\r\n{\r\n  public class PreviewProbe\r\n  {\r\n    public void Anchor() { }\r\n  }\r\n}\r\n";

        [SetUp]
        public void SetUp()
        {
            _savedEnabled = EditorPrefs.HasKey(EnabledKey) ? EditorPrefs.GetBool(EnabledKey) : (bool?)null;
            _savedConsent = EditorPrefs.HasKey(ConsentKey) ? EditorPrefs.GetBool(ConsentKey) : (bool?)null;
            EditorPrefs.SetBool(EnabledKey, true);
            EditorPrefs.SetBool(ConsentKey, true);
            _folder = "Assets/ScriptPreviewTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            _path = _folder + "/PreviewProbe.cs";
            Write(Original);
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

        [TestCase("replace_method")]
        [TestCase("insert_method")]
        [TestCase("replace_class")]
        public void StructuralPreview_IsReadOnlyAndReturnsCompleteValidatedProposal(string mode)
        {
            var request = Structural(mode);
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            File.WriteAllText(_path + ".tmp", "keep tmp");
            File.WriteAllText(_path + ".bak", "keep bak");
            var response = Send(request);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertUnchanged(before, mtime);
            AssertProposal(response["data"], Original, false, 1);
            Assert.AreEqual("keep tmp", File.ReadAllText(_path + ".tmp"));
            Assert.AreEqual("keep bak", File.ReadAllText(_path + ".bak"));
        }

        [Test]
        public void TextPreview_IsReadOnlyAndKeepsExactLiteralPayload()
        {
            var request = Text("    public void Anchor() { /* exact */ }\r\n");
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(request);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertUnchanged(before, mtime);
            AssertProposal(response["data"], Original, false, 1);
            Assert.AreEqual(
                Original.Replace("    public void Anchor() { }\r\n", "    public void Anchor() { /* exact */ }\r\n"),
                response["data"].Value<string>("new_contents")
            );
            AssertUnchanged(before, mtime);
            Assert.IsFalse(File.Exists(_path + ".tmp"));
            Assert.IsFalse(File.Exists(_path + ".bak"));
        }

        [TestCase("edit", false)]
        [TestCase("apply_text_edits", false)]
        [TestCase("edit", true)]
        [TestCase("apply_text_edits", true)]
        public void PreviewNoOp_IsReadOnlyAndExplicitlyPreparesNoEdits(string action, bool bom)
        {
            Write(Original, bom);
            var request = action == "edit" ? Structural("replace_method", "    public void Anchor() { }") : Text("    public void Anchor() { }\r\n");
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(request);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertProposal(response["data"], Original, true, 0);
            AssertUnchanged(before, mtime);
        }

        [TestCase("edit")]
        [TestCase("apply_text_edits")]
        public void PreviewCandidate_EqualsDirectWriteIncludingUtf8BomPolicy(string action)
        {
            Write(Original, true);
            var request = action == "edit" ? Structural("replace_method") : Text("    public void Anchor() { /* exact */ }\r\n");
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var proposal = Send(request);
            Assert.IsTrue(proposal.Value<bool>("success"), proposal.ToString());
            AssertUnchanged(before, mtime);
            AssertProposal(proposal["data"], Original, false, 1);
            string candidate = proposal["data"].Value<string>("new_contents");
            request["options"]["preview"] = false;
            var applied = Send(request);
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            Assert.AreEqual(candidate, File.ReadAllText(_path));
            CollectionAssert.AreEqual(new UTF8Encoding(false).GetBytes(candidate), File.ReadAllBytes(_path));
            Assert.AreEqual(proposal["data"].Value<string>("candidate_sha256"), applied["data"].Value<string>("sha256"));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PreviewBatch_UsesSameAtomicAndSequentialFormattingAsDirectEdit(bool sequential)
        {
            var request = Structural("insert_method", "public void Added() { }");
            request["options"]["applyMode"] = sequential ? "sequential" : "atomic";
            if (sequential)
                ((JArray)request["edits"]).Add(
                    new JObject
                    {
                        ["mode"] = "replace_method",
                        ["className"] = "PreviewProbe",
                        ["methodName"] = "Added",
                        ["replacement"] = "public void Added() { int n = 1; }",
                    }
                );
            else
                ((JArray)request["edits"]).Add(
                    new JObject
                    {
                        ["mode"] = "insert_method",
                        ["className"] = "PreviewProbe",
                        ["replacement"] = "public void Second() { }",
                    }
                );
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var proposal = Send(request);
            Assert.IsTrue(proposal.Value<bool>("success"), proposal.ToString());
            AssertProposal(proposal["data"], Original, false, 2);
            AssertUnchanged(before, mtime);
            request["options"]["preview"] = false;
            var applied = Send(request);
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            Assert.AreEqual(proposal["data"].Value<string>("new_contents"), File.ReadAllText(_path));
        }

        [TestCase("missing")]
        [TestCase("false")]
        public void DirectEdit_DefaultBehaviorStillWrites(string option)
        {
            var request = Structural("replace_method");
            if (option == "missing")
                ((JObject)request["options"]).Remove("preview");
            else
                request["options"]["preview"] = false;
            var response = Send(request);
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreNotEqual(Original, File.ReadAllText(_path));
            Assert.AreEqual(1, response["data"].Value<int>("editsApplied"));
            Assert.IsNull(response["data"]["preview"]);
        }

        [Test]
        public void PreviewFailure_RejectsUnbalancedCandidateWithoutSideEffects()
        {
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(Structural("replace_method", "public void Anchor() {"));
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("unbalanced_braces", response["data"].Value<string>("status"));
            AssertUnchanged(before, mtime);
        }

        [Test]
        public void PreviewFailure_RetainsTextShaDriftGuard()
        {
            var request = Text("    public void Anchor() { /* exact */ }\r\n");
            request["precondition_sha256"] = new string('0', 64);
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(request);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("stale_file", response["data"].Value<string>("status"));
            AssertUnchanged(before, mtime);
        }

        [TestCase("edit")]
        [TestCase("apply_text_edits")]
        public void PreviewOverBound_IsReadOnlyAndReturnsNoPartialProposal(string action)
        {
            string original = "// " + new string('가', 180000) + "\r\n" + Original;
            Write(original);
            var request = action == "edit" ? Structural("replace_method") : Text("    public void Anchor() { /* exact */ }\r\n");
            if (action == "apply_text_edits")
            {
                request["precondition_sha256"] = Hash(original);
                request["edits"][0]["startLine"] = 6;
                request["edits"][0]["endLine"] = 7;
            }
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(request);
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("too_large", response["data"].Value<string>("status"));
            Assert.IsNull(response["data"]["new_contents"]);
            Assert.IsNull(response["data"]["original_contents"]);
            AssertUnchanged(before, mtime);
        }

        [Test]
        public void PreviewAtBound_IsReadOnlyAndComplete()
        {
            string original = "/*" + new string('x', 524288 - Encoding.UTF8.GetByteCount(Original) - 4) + "*/" + Original;
            Write(original);
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(Structural("replace_method", "    public void Anchor() { }"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertProposal(response["data"], original, true, 0);
            AssertUnchanged(before, mtime);
        }

        [TestCase("preview_edit", false)]
        [TestCase("preview_text_edits", false)]
        [TestCase("preview_edit", true)]
        [TestCase("preview_text_edits", true)]
        public void ReadOnlyAlias_ForcesPreviewWithoutChangingCallerOptions(string action, bool missing)
        {
            var request = action == "preview_edit" ? Structural("replace_method") : Text("    public void Anchor() { /* exact */ }\r\n");
            request["action"] = action;
            if (missing)
                ((JObject)request["options"]).Remove("preview");
            else
                request["options"]["preview"] = false;
            var before = File.ReadAllBytes(_path);
            var mtime = File.GetLastWriteTimeUtc(_path);
            var response = Send(request);
            // Old dispatchers reject these aliases before mutation. Assert the safety
            // invariant first so baseline evidence also exercises that protection.
            AssertUnchanged(before, mtime);
            Assert.IsFalse(File.Exists(_path + ".tmp"));
            Assert.IsFalse(File.Exists(_path + ".bak"));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            AssertProposal(response["data"], Original, false, 1);
            if (missing)
                Assert.IsNull(request["options"]["preview"]);
            else
                Assert.IsFalse(request["options"].Value<bool>("preview"));
        }

        private JObject Structural(string mode, string replacement = null)
        {
            replacement =
                replacement
                ?? (
                    mode == "replace_class"
                        ? "public class PreviewProbe\n{\n    public void Changed() { }\n}"
                        : "public void " + (mode == "insert_method" ? "Added" : "Anchor") + "()\n{\n    var s = @\"a\r\nb\";\n}"
                );
            return new JObject
            {
                ["action"] = "edit",
                ["name"] = "PreviewProbe",
                ["path"] = _folder,
                ["edits"] = new JArray(
                    new JObject
                    {
                        ["mode"] = mode,
                        ["className"] = "PreviewProbe",
                        ["methodName"] = "Anchor",
                        ["replacement"] = replacement,
                    }
                ),
                ["options"] = Options(),
            };
        }

        private JObject Text(string replacement)
        {
            return new JObject
            {
                ["action"] = "apply_text_edits",
                ["name"] = "PreviewProbe",
                ["path"] = _folder,
                ["precondition_sha256"] = Hash(Original),
                ["edits"] = new JArray(
                    new JObject
                    {
                        ["startLine"] = 5,
                        ["startCol"] = 1,
                        ["endLine"] = 6,
                        ["endCol"] = 1,
                        ["newText"] = replacement,
                    }
                ),
                ["options"] = Options(),
            };
        }

        private static JObject Options()
        {
            return new JObject
            {
                ["preview"] = true,
                ["refresh"] = "immediate",
                ["validate"] = "basic",
            };
        }

        private static JObject Send(JObject request)
        {
            return JObject.FromObject(ManageScript.HandleCommand(request));
        }

        private void AssertProposal(JToken data, string original, bool noOp, int prepared)
        {
            Assert.IsTrue(data.Value<bool>("preview"));
            Assert.AreEqual(noOp, data.Value<bool>("no_op"));
            Assert.AreEqual(0, data.Value<int>("editsApplied"));
            Assert.AreEqual(prepared, data.Value<int>("editsPrepared"));
            Assert.IsFalse(data.Value<bool>("scheduledRefresh"));
            Assert.IsTrue(data.Value<bool>("complete"));
            Assert.IsFalse(data.Value<bool>("truncated"));
            Assert.AreEqual(_path, data.Value<string>("path"));
            Assert.AreEqual(Path.GetFullPath(_path), Path.GetFullPath(data.Value<string>("absolute_path")));
            Assert.AreEqual(Path.GetFullPath("."), Path.GetFullPath(data.Value<string>("project_root")));
            Assert.AreEqual(Hash(original), data.Value<string>("original_sha256"));
            Assert.AreEqual(original, data.Value<string>("original_contents"));
            Assert.AreEqual(Hash(original), data.Value<string>("sha256"));
            Assert.AreEqual(Hash(data.Value<string>("new_contents")), data.Value<string>("candidate_sha256"));
            Assert.AreEqual(data.Value<string>("candidate_sha256"), data.Value<string>("candidate_bytes_sha256"));
            Assert.AreEqual("utf-8", data.Value<string>("encoding"));
            Assert.IsFalse(data.Value<bool>("bom"));
        }

        private void AssertUnchanged(byte[] bytes, DateTime mtime)
        {
            CollectionAssert.AreEqual(bytes, File.ReadAllBytes(_path));
            Assert.AreEqual(mtime, File.GetLastWriteTimeUtc(_path));
        }

        private void Write(string text, bool bom = false)
        {
            File.WriteAllText(_path, text, new UTF8Encoding(bom));
            File.SetLastWriteTimeUtc(_path, new DateTime(2001, 1, 1, 0, 0, 0, DateTimeKind.Utc));
        }

        private static string Hash(string text)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", string.Empty).ToLowerInvariant();
        }
    }
}
