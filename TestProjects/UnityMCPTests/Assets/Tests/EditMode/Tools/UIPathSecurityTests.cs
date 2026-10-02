using System;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class UIPathSecurityTests
    {
        [Test]
        public void ValidFileOperationsRoundTripInsideAssets()
        {
            string folder = "Assets/UIPathSecurity_" + Guid.NewGuid().ToString("N");
            string path = folder + "/test.uss";
            try
            {
                foreach (string action in new[] { "create", "read", "update", "delete" })
                {
                    var result = JObject.FromObject(ManageUI.HandleCommand(new JObject
                    {
                        ["action"] = action, ["path"] = path,
                        ["contents"] = ".test { color: red; }"
                    }));
                    Assert.IsTrue(result.Value<bool>("success"), result.ToString());
                }
                Assert.IsFalse(File.Exists(AssetPathUtility.GetFullAssetPath(path)));
            }
            finally { AssetDatabase.DeleteAsset(folder); }
        }

        [Test]
        public void RootedRemaindersAreRejectedForEveryFileAction()
        {
            foreach (string action in new[] { "create", "read", "update", "delete", "link_stylesheet" })
                foreach (string path in new[] { "Assets//tmp/outside.uxml", "Assets/\\tmp/outside.uxml",
                    "Assets/C:/outside.uxml", "Assets/../outside.uxml", "/tmp/outside.uxml" })
                {
                    var result = JObject.FromObject(ManageUI.HandleCommand(new JObject
                    {
                        ["action"] = action, ["path"] = path, ["contents"] = "<UXML/>",
                        ["stylesheet"] = "Assets/UI/test.uss"
                    }));
                    Assert.IsFalse(result.Value<bool>("success"), action + ": " + path);
                }
        }

        [Test]
        public void CanonicalPathsRemainInsideAssets()
        {
            Assert.AreEqual("Assets/UI/Menu.uxml", AssetPathUtility.GetContainedAssetPath("assets\\UI\\Menu.uxml"));
            Assert.AreEqual(Path.GetFullPath(Path.Combine(Application.dataPath, "UI/Menu.uxml")),
                AssetPathUtility.GetFullAssetPath("assets\\UI\\Menu.uxml"));
        }

        [Test]
        public void LinkRejectsRootedStylesheetBeforeReadingUxml()
        {
            var result = JObject.FromObject(ManageUI.HandleCommand(new JObject
            {
                ["action"] = "link_stylesheet", ["path"] = "Assets/UI/Menu.uxml",
                ["stylesheet"] = "Assets/C:/outside.uss"
            }));
            Assert.IsFalse(result.Value<bool>("success"));
            StringAssert.Contains("Rooted", result.ToString());
        }
    }
}
