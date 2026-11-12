using System;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
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

        [TestCase("create")]
        [TestCase("update")]
        [TestCase("link_stylesheet")]
        public void ImportFailure_RetainsSavedSourceReceipt(string action)
        {
            // Given valid source and a failure at native import after publication.
            Directory.CreateDirectory(FullPath(Folder));
            string path = Folder + (action == "link_stylesheet" ? "/Owned.uxml" : "/Owned.uss");
            string stylePath = Folder + "/Linked.uss";
            var request = new JObject
            {
                ["action"] = action,
                ["path"] = path,
                ["contents"] = ".title { color: blue; }",
            };
            if (action == "update")
                File.WriteAllText(FullPath(path), ".title { color: red; }");
            if (action == "link_stylesheet")
            {
                File.WriteAllText(FullPath(path), "<ui:UXML xmlns:ui=\"UnityEngine.UIElements\"><ui:Label text=\"Kept\" /></ui:UXML>");
                File.WriteAllText(FullPath(stylePath), ".title { color: blue; }");
                request["stylesheet"] = stylePath;
            }
            var hook = typeof(ManageUI).GetField("BeforeSourceImportForTests", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(hook);
            hook.SetValue(null, (Action<string>)(_ => throw new IOException("injected import failure")));
            try
            {
                // When the public authoring action saves successfully but import fails.
                JObject result = Send(request);
                // Then the receipt's digest identifies the retained bytes and refresh-only recovery.
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.AreEqual("issues_found", result["data"]["verification"].Value<string>("status"));
                Assert.IsFalse(result["data"]["verification"].Value<bool>("complete"));
                Assert.IsTrue(File.Exists(FullPath(path)));
                byte[] saved = File.ReadAllBytes(FullPath(path));
                using var sha = SHA256.Create();
                string digest = BitConverter.ToString(sha.ComputeHash(saved)).Replace("-", "").ToLowerInvariant();
                Assert.AreEqual(path, result["data"].Value<string>("path"));
                Assert.IsTrue(result["data"].Value<bool>("source_saved"));
                Assert.AreEqual(digest, result["data"].Value<string>("bytes_sha256"));
                Assert.AreEqual("import", result["data"].Value<string>("failed_stage"));
                Assert.AreEqual("refresh_only", result["data"].Value<string>("recovery_action"));
                Assert.IsFalse(result["data"].Value<bool>("retry_safe"));
                string text = Encoding.UTF8.GetString(saved);
                if (action == "link_stylesheet")
                {
                    Assert.That(text, Does.Contain("Linked.uss"));
                    Assert.That(text, Does.Contain("Kept"));
                }
                else
                    Assert.AreEqual(".title { color: blue; }", text);
            }
            finally
            {
                hook.SetValue(null, null);
            }
        }

        [Test]
        public void SavedSource_DefaultObservationHasNoImplicitLiveScope()
        {
            JObject result = Send(
                new JObject
                {
                    ["action"] = "create",
                    ["path"] = Folder + "/Source.uss",
                    ["contents"] = ".title { color: red; }",
                }
            );
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            Assert.AreEqual("saved_ui_source", result["data"]["verification"].Value<string>("scope"));
            Assert.AreEqual("not_requested", result["data"]["verification"].Value<string>("live_binding"));
        }

        [Test]
        public void SourcePatchImportFailure_RetainsSavedSuccessAndFollowupIssue()
        {
            string path = Folder + "/Patch.uss";
            Send(
                new JObject
                {
                    ["action"] = "create",
                    ["path"] = path,
                    ["contents"] = ".title { color: red; }",
                }
            );
            var request = new JObject
            {
                ["action"] = "patch_source",
                ["path"] = path,
                ["source_selector"] = ".title",
                ["source_changes"] = new JObject { ["color"] = "blue" },
            };
            var preview = Send(request);
            request["preview"] = false;
            request["expected_source_hash"] = preview["data"]["source_hash"];
            var hook = typeof(ManageUI).GetField("BeforeSourceImportForTests", BindingFlags.Static | BindingFlags.NonPublic);
            hook.SetValue(null, (Action<string>)(_ => throw new IOException("injected import failure")));
            try
            {
                JObject result = Send(request);
                Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                Assert.IsTrue(result["data"].Value<bool>("source_saved"));
                Assert.AreEqual("issues_found", result["data"]["verification"].Value<string>("status"));
                Assert.That(File.ReadAllText(FullPath(path)), Does.Contain("blue"));
            }
            finally
            {
                hook.SetValue(null, null);
            }
        }
    }
}
