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
        private bool? _savedEnabled, _savedConsent;
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
            try { AssetDatabase.DeleteAsset(_folder); }
            finally
            {
                if (_savedEnabled.HasValue) EditorPrefs.SetBool(EnabledKey, _savedEnabled.Value);
                else EditorPrefs.DeleteKey(EnabledKey);
                if (_savedConsent.HasValue) EditorPrefs.SetBool(ConsentKey, _savedConsent.Value);
                else EditorPrefs.DeleteKey(ConsentKey);
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
            string hash;
            using (var sha = SHA256.Create())
                hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(Original))).Replace("-", string.Empty).ToLowerInvariant();
            return JObject.FromObject(ManageScript.HandleCommand(new JObject
            {
                ["action"] = "apply_text_edits",
                ["name"] = "ContractEditProbe",
                ["path"] = _folder,
                ["precondition_sha256"] = hash,
                ["edits"] = new JArray(new JObject
                {
                    ["startLine"] = 4, ["startCol"] = 1,
                    ["endLine"] = endLine, ["endCol"] = 1,
                    ["newText"] = newText
                }),
                ["options"] = new JObject { ["refresh"] = "deferred", ["validate"] = "syntax" }
            }));
        }
    }

    public class ManageScriptUnicodeColumnTests
    {
        [TestCase("😀x", 1, 1, 0)]
        [TestCase("😀x", 1, 2, 2)]
        [TestCase("😀x", 1, 3, 3)]
        [TestCase("a😀b\r\nc", 1, 3, 3)]
        [TestCase("a😀b\r\nc", 2, 1, 6)]
        [TestCase("é가x", 1, 3, 2)]
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
