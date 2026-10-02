using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;

namespace MCPForUnityTests.Editor.Tools
{
    public class ManageAssetSearchTests
    {
        private string _folder;

        [SetUp]
        public void SetUp()
        {
            _folder = "Assets/AssetSearchTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(_folder));
            for (int i = 0; i < 5; i++)
                File.WriteAllText(_folder + "/item" + i + ".txt", "test " + i);
            AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(_folder);
        }

        [Test]
        public void Search_ReturnsRequestedPageWithTotalMatchingCount()
        {
            var expectedPaths = AssetDatabase.FindAssets("t:TextAsset", new[] { _folder })
                .Select(AssetDatabase.GUIDToAssetPath).Skip(2).Take(2).ToArray();

            var response = Search(2, 2);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(5, response["data"]["totalAssets"].Value<int>());
            CollectionAssert.AreEqual(expectedPaths, response["data"]["assets"].Select(a => a["path"].Value<string>()).ToArray());
        }

        [Test]
        public void HugePageNumber_ReturnsEmptyPageWithoutWrapping()
        {
            var response = Search(50, int.MaxValue);

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(5, response["data"]["totalAssets"].Value<int>());
            Assert.IsEmpty(response["data"]["assets"]);
        }

        [TestCase(0, 1)]
        [TestCase(-1, 1)]
        [TestCase(1, 0)]
        [TestCase(1, -1)]
        public void InvalidPagination_ReturnsError(int pageSize, int pageNumber)
        {
            var response = Search(pageSize, pageNumber);

            Assert.IsFalse(response.Value<bool>("success"));
        }

        private JObject Search(int pageSize, int pageNumber)
            => JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = "search",
                ["path"] = _folder,
                ["filterType"] = "TextAsset",
                ["pageSize"] = pageSize,
                ["pageNumber"] = pageNumber
            }));
    }
}
