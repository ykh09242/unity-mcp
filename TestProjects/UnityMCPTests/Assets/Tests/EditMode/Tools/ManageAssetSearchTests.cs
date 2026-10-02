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

        [TestCase("missing-folder")]
        [TestCase("item0.txt")]
        public void InvalidScope_ReturnsErrorInsteadOfSearchingEntireProject(string child)
        {
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = "search",
                ["path"] = _folder + "/" + child,
                ["filterType"] = "TextAsset"
            }));

            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("not a valid folder", response.Value<string>("error"));
        }

        [Test]
        public void InvalidDate_ReturnsErrorInsteadOfDroppingFilter()
        {
            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = "search",
                ["path"] = _folder,
                ["filterDateAfter"] = "not-a-date"
            }));

            Assert.IsFalse(response.Value<bool>("success"));
            StringAssert.Contains("filterDateAfter", response.Value<string>("error"));
        }

        [Test]
        public void DateFilter_AppliesBeforePagingAndTotalCount()
        {
            var cutoff = DateTime.UtcNow.AddDays(-1);
            File.SetLastWriteTimeUtc(_folder + "/item0.txt", cutoff.AddDays(-1));
            for (int i = 1; i < 5; i++)
                File.SetLastWriteTimeUtc(_folder + "/item" + i + ".txt", cutoff.AddHours(1));
            var expectedPaths = AssetDatabase.FindAssets("t:TextAsset", new[] { _folder })
                .Select(AssetDatabase.GUIDToAssetPath)
                .Where(path => File.GetLastWriteTimeUtc(path) > cutoff).Skip(2).Take(2).ToArray();

            var response = JObject.FromObject(ManageAsset.HandleCommand(new JObject
            {
                ["action"] = "search",
                ["path"] = _folder,
                ["filterType"] = "TextAsset",
                ["filterDateAfter"] = cutoff.ToString("o"),
                ["pageSize"] = 2,
                ["pageNumber"] = 2
            }));

            Assert.IsTrue(response.Value<bool>("success"));
            Assert.AreEqual(4, response["data"]["totalAssets"].Value<int>());
            CollectionAssert.AreEqual(expectedPaths, response["data"]["assets"].Select(a => a["path"].Value<string>()).ToArray());
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
