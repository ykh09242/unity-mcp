using System;
using System.IO;
using System.Text;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageUISourcePatchTests
    {
        private string folder;

        private string Full(string path) => Path.Combine(Application.dataPath, path.Substring(7));

        private static JObject Send(JObject request) => JObject.FromObject(ManageUI.HandleCommand(request));

        private string Create(string extension, string source, bool bom = false, bool import = true)
        {
            folder = "Assets/ui-source-patch-" + Guid.NewGuid().ToString("N");
            Directory.CreateDirectory(Full(folder));
            string path = folder + "/Owned." + extension;
            File.WriteAllText(Full(path), source, new UTF8Encoding(bom));
            if (import)
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            return path;
        }

        private static JObject Request(string path, string selector, JObject changes, bool preview = true, string hash = null) =>
            new JObject
            {
                ["action"] = "patch_source",
                ["path"] = path,
                ["source_selector"] = selector,
                ["source_changes"] = changes,
                ["preview"] = preview,
                ["expected_source_hash"] = hash,
            };

        [TearDown]
        public void Cleanup()
        {
            if (folder != null)
                AssetDatabase.DeleteAsset(folder);
            folder = null;
        }

        [Test]
        public void Preview_LabelText_DoesNotWrite_AndApplyPersistsAfterImportWithSameGuid()
        {
            // Given
            const string source = "<ui:UXML xmlns:ui=\"UnityEngine.UIElements\">\r\n<!-- keep -->\r\n<ui:Label name='title' text='Before' />\r\n</ui:UXML>";
            string path = Create("uxml", source);
            byte[] before = File.ReadAllBytes(Full(path));
            string guid = AssetDatabase.AssetPathToGUID(path);
            // When
            var preview = Send(Request(path, "#title", new JObject { ["text"] = "After & more" }));
            // Then
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
            Assert.AreEqual("Before", preview["data"]["changes"][0].Value<string>("before"));
            Assert.AreEqual("After &amp; more", preview["data"]["changes"][0].Value<string>("after"));
            var applied = Send(Request(path, "#title", new JObject { ["text"] = "After & more" }, false, preview["data"].Value<string>("source_hash")));
            Assert.IsTrue(applied.Value<bool>("success"), applied.ToString());
            Assert.AreEqual(source.Replace("Before", "After &amp; more"), File.ReadAllText(Full(path)));
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            Assert.AreEqual("After & more", AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(path).CloneTree().Q<Label>("title").text);
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
        }

        [Test]
        public void Apply_UssDeclaration_PreservesBomCommentsAndOtherRules()
        {
            // Given
            const string source = "/* keep */\r\n.title { color: red; /* keep too */ width: 10px; }\r\n.other { color: green; }";
            string path = Create("uss", source, true);
            string guid = AssetDatabase.AssetPathToGUID(path);
            var preview = Send(Request(path, ".title", new JObject { ["color"] = "blue" }));
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            // When
            var result = Send(Request(path, ".title", new JObject { ["color"] = "blue" }, false, preview["data"].Value<string>("source_hash")));
            // Then
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            byte[] written = File.ReadAllBytes(Full(path));
            Assert.AreEqual(239, written[0]);
            Assert.AreEqual(187, written[1]);
            Assert.AreEqual(191, written[2]);
            Assert.AreEqual(source.Replace("color: red", "color: blue"), File.ReadAllText(Full(path)));
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
            Assert.IsNotNull(AssetDatabase.LoadAssetAtPath<StyleSheet>(path));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(path));
        }

        [Test]
        public void Apply_StaleHash_LeavesExternalWriterBytesUnchanged()
        {
            // Given
            string path = Create("uss", ".title { color: red; }");
            var preview = Send(Request(path, ".title", new JObject { ["color"] = "blue" }));
            Assert.IsTrue(preview.Value<bool>("success"), preview.ToString());
            File.WriteAllText(Full(path), ".title { color: green; }", new UTF8Encoding(false));
            byte[] external = File.ReadAllBytes(Full(path));
            // When
            var result = Send(Request(path, ".title", new JObject { ["color"] = "blue" }, false, preview["data"].Value<string>("source_hash")));
            // Then
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(external, File.ReadAllBytes(Full(path)));
        }

        [TestCase(".title { color: red; } .title { color: green; }", "blue")]
        [TestCase(".title { color: red; color: green; }", "blue")]
        [TestCase(".title { color: red; }", "blue; width: 50px")]
        [TestCase(".title { color: red; }", "url('unclosed)")]
        public void UnsupportedUssPatch_RejectsWithoutWriting(string source, string value)
        {
            // Given
            string path = Create("uss", source, import: false);
            byte[] before = File.ReadAllBytes(Full(path));
            // When
            var result = Send(Request(path, ".title", new JObject { ["color"] = value }));
            // Then
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
        }

        [Test]
        public void Apply_WithoutPreviewHash_RejectsWithoutWriting()
        {
            // Given
            string path = Create("uss", ".title { color: red; }");
            byte[] before = File.ReadAllBytes(Full(path));
            // When
            var result = Send(Request(path, ".title", new JObject { ["color"] = "blue" }, false));
            // Then
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
        }

        [TestCase("<ui:Label name='title' text='A'/><ui:Label name='title' text='B'/>", "#title")]
        [TestCase("<ui:Instance template='Card' name='title'/>", "#title")]
        public void UnsupportedOrAmbiguousOwnership_RejectsWithoutWriting(string body, string selector)
        {
            // Given
            string path = Create("uxml", "<ui:UXML xmlns:ui='UnityEngine.UIElements'>" + body + "</ui:UXML>", import: false);
            byte[] before = File.ReadAllBytes(Full(path));
            // When
            var result = Send(Request(path, selector, new JObject { ["text"] = "Changed" }));
            // Then
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            CollectionAssert.AreEqual(before, File.ReadAllBytes(Full(path)));
        }
    }
}
