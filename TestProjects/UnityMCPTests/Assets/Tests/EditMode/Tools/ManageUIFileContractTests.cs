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
    public class ManageUIFileContractTests
    {
        private string _folder;

        private string FullPath(string relative) => Path.Combine(Application.dataPath, relative.Substring("Assets/".Length));

        private string Folder => _folder ?? (_folder = "Assets/ui-file-contract-" + Guid.NewGuid().ToString("N"));

        private static JObject Send(JObject request) => JObject.FromObject(ManageUI.HandleCommand(request));

        [TearDown]
        public void TearDown()
        {
            if (_folder != null)
                AssetDatabase.DeleteAsset(_folder);
            _folder = null;
        }

        [TestCase("<ui:UXML><ui:Label></ui:UXML>")]
        [TestCase("")]
        public void InvalidUxmlCreate_DoesNotCreateItsDirectory(string contents)
        {
            string path = Folder + "/Nested/Invalid.uxml";
            var response = Send(
                new JObject
                {
                    ["action"] = "create",
                    ["path"] = path,
                    ["contents"] = contents,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"), response.ToString());
            Assert.IsFalse(Directory.Exists(FullPath(Folder)));
            Assert.IsFalse(File.Exists(FullPath(path)));
        }

        [TestCase("create")]
        [TestCase("update")]
        public void EncodedEmptyUss_IsAnExplicitEmptyFile(string action)
        {
            string path = Folder + "/Empty.uss";
            if (action == "update")
            {
                Directory.CreateDirectory(FullPath(Folder));
                File.WriteAllText(FullPath(path), ".old { color: red; }", new UTF8Encoding(false));
            }
            var response = Send(
                new JObject
                {
                    ["action"] = action,
                    ["path"] = path,
                    ["contentsEncoded"] = true,
                    ["encodedContents"] = "",
                }
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(File.Exists(FullPath(path)));
            Assert.AreEqual(0, File.ReadAllBytes(FullPath(path)).Length);
            var read = Send(new JObject { ["action"] = "read", ["path"] = path });
            Assert.IsTrue(read.Value<bool>("success"), read.ToString());
            Assert.AreEqual("", read["data"].Value<string>("encodedContents"));
        }

        [Test]
        public void ValidUxmlCreate_WritesImportsAndKeepsExistingFileGuard()
        {
            string path = Folder + "/Valid.uxml";
            var request = new JObject
            {
                ["action"] = "create",
                ["path"] = path,
                ["contents"] = "<ui:UXML xmlns:ui=\"UnityEngine.UIElements\"><ui:Label text=\"Hello\" /></ui:UXML>",
            };
            Assert.IsTrue(Send(request).Value<bool>("success"));
            string written = File.ReadAllText(FullPath(path));
            Assert.That(written, Does.Contain("Hello"));
            Assert.IsFalse(Send(request).Value<bool>("success"));
            Assert.AreEqual(written, File.ReadAllText(FullPath(path)));
        }

        [Test]
        public void MissingEncodedContents_RemainsAnError()
        {
            string path = Folder + "/Missing.uss";
            var response = Send(
                new JObject
                {
                    ["action"] = "create",
                    ["path"] = path,
                    ["contentsEncoded"] = true,
                }
            );
            Assert.IsFalse(response.Value<bool>("success"));
            Assert.IsFalse(Directory.Exists(FullPath(Folder)));
        }
    }
}
