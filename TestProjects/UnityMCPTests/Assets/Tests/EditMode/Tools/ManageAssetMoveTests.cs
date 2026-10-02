using System;
using System.IO;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageAssetMoveTests
    {
        private string _folder;
        private string _source;
        private string _destination;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/MoveContractTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            _source = _folder + "/source.txt";
            _destination = _folder + "/destination.txt";
            File.WriteAllText(_source, "move fixture");
            AssetDatabase.ImportAsset(_source, ImportAssetOptions.ForceSynchronousImport);
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(_folder);
        }

        [TestCase("move")]
        [TestCase("rename")]
        public void MoveOrRename_ReportsSuccessAndPreservesGuid(string action)
        {
            string originalGuid = AssetDatabase.AssetPathToGUID(_source);

            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = action,
                ["path"] = _source,
                ["destination"] = _destination
            }));

            Assert.IsTrue(response.Value<bool>("success"), response["error"]?.ToString());
            Assert.IsFalse(File.Exists(_source));
            Assert.AreEqual("move fixture", File.ReadAllText(_destination));
            Assert.AreEqual(originalGuid, AssetDatabase.AssetPathToGUID(_destination));
        }

        [Test]
        public void ExistingDestination_ReturnsErrorAndPreservesBothAssets()
        {
            File.WriteAllText(_destination, "destination fixture");
            AssetDatabase.ImportAsset(_destination, ImportAssetOptions.ForceSynchronousImport);

            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = "move",
                ["path"] = _source,
                ["destination"] = _destination
            }));

            Assert.IsFalse(response.Value<bool>("success"));
            Assert.AreEqual("move fixture", File.ReadAllText(_source));
            Assert.AreEqual("destination fixture", File.ReadAllText(_destination));
        }
    }
}
