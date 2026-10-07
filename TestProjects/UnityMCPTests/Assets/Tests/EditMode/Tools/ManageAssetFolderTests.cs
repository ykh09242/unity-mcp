using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageAssetFolderTests
    {
        private string _folder;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/AssetFolderTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(_folder);
        }

        [TestCase("create_folder")]
        [TestCase("create")]
        public void NestedFolder_CreatesMissingParents(string action)
        {
            var path = _folder + "/Parent/Child";
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["assetType"] = "Folder",
                        ["path"] = path,
                    }
                )
            );

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(AssetDatabase.IsValidFolder(_folder + "/Parent"));
            Assert.IsTrue(AssetDatabase.IsValidFolder(path));
            Assert.AreEqual(path, response["data"].Value<string>("path"));
        }

        [Test]
        public void DeepFolder_ReturnsOnlyRequestedFolderMetadata()
        {
            var path = _folder + "/One/Two/Three/Four/Five";
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "create_folder", ["path"] = path }));

            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.AreEqual(path, response["data"].Value<string>("path"));
            Assert.AreEqual(AssetDatabase.AssetPathToGUID(path), response["data"].Value<string>("guid"));
            Assert.IsTrue(response["data"].Value<bool>("isFolder"));
            Assert.IsTrue(AssetDatabase.IsValidFolder(path));
        }

        [Test]
        public void ExistingFolder_ReturnsSameGuid()
        {
            var guid = AssetDatabase.AssetPathToGUID(_folder);
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "create_folder", ["path"] = _folder }));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(guid, response["data"].Value<string>("guid"));
        }

        [Test]
        public void BackslashPath_CreatesCanonicalNestedFolder()
        {
            var path = _folder + "/Parent/Child";
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "create_folder", ["path"] = path.Replace('/', '\\') }));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(path, response["data"].Value<string>("path"));
            Assert.IsTrue(AssetDatabase.IsValidFolder(path));
        }

        [Test]
        public void ParentIsAnAsset_ReturnsErrorAndPreservesAsset()
        {
            var parentPath = _folder + "/parent.txt";
            File.WriteAllText(parentPath, "keep");
            AssetDatabase.ImportAsset(parentPath, ImportAssetOptions.ForceSynchronousImport);
            var guid = AssetDatabase.AssetPathToGUID(parentPath);
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "create_folder", ["path"] = parentPath + "/Child" }));

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual(guid, AssetDatabase.AssetPathToGUID(parentPath));
            Assert.AreEqual("keep", File.ReadAllText(parentPath));
        }
    }
}
