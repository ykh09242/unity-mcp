using System;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace MCPForUnityTests.Editor.Tools
{
    public class AssetBundleInspectionTests
    {
        private string folder;
        private string bundle;

        [SetUp]
        public void SetUp()
        {
            string suffix = Guid.NewGuid().ToString("N");
            folder = "Assets/__McpBundleInspection_" + suffix;
            bundle = "mcp_bundle_inspection_" + suffix;
            AssetDatabase.CreateFolder("Assets", Path.GetFileName(folder));
            for (int index = 0; index < 3; index++)
            {
                string path = folder + "/item" + index + ".txt";
                File.WriteAllText(path, "bundle test " + index);
                AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceSynchronousImport);
                AssetImporter.GetAtPath(path).assetBundleName = bundle;
            }
            AssetDatabase.SaveAssets();
        }

        [TearDown]
        public void TearDown()
        {
            AssetDatabase.DeleteAsset(folder);
            // Remove only this test's assignment; do not prune unrelated unused names.
            AssetDatabase.RemoveAssetBundleName(bundle, true);
            AssetDatabase.RemoveAssetBundleName(bundle + "_texture", true);
        }

        [Test]
        public void ListsAssignedNamesAndPagesAssetsWithoutChangingAssignments()
        {
            JObject names = Inspect("list_asset_bundles", 100, 1);
            Assert.IsTrue(names.Value<bool>("success"), names.ToString());
            string[] expectedNames = AssetDatabase.GetAllAssetBundleNames().OrderBy(name => name, StringComparer.Ordinal).Take(100).ToArray();
            CollectionAssert.AreEqual(expectedNames, names["data"]["entries"].Values<string>().ToArray());

            var before = Enumerable.Range(0, 3).Select(index => AssetImporter.GetAtPath(folder + "/item" + index + ".txt").assetBundleName).ToArray();
            JObject assets = Inspect("get_bundle_assets", 2, 2);
            Assert.IsTrue(assets.Value<bool>("success"), assets.ToString());
            Assert.AreEqual(3, assets["data"].Value<int>("totalEntries"));
            CollectionAssert.AreEqual(new[] { folder + "/item2.txt" }, assets["data"]["entries"].Values<string>().ToArray());
            Assert.IsFalse(assets["data"].Value<bool>("hasMore"));
            CollectionAssert.AreEqual(
                before,
                Enumerable.Range(0, 3).Select(index => AssetImporter.GetAtPath(folder + "/item" + index + ".txt").assetBundleName)
            );
        }

        [Test]
        public void DependenciesMatchAssetDatabaseForDirectAndRecursiveModes()
        {
            Shader shader = Shader.Find("Unlit/Texture");
            if (shader == null)
                Assert.Ignore("Requires the built-in Unlit/Texture shader for a material dependency fixture.");
            var texture = new Texture2D(1, 1);
            var material = new Material(shader) { mainTexture = texture };
            AssetDatabase.CreateAsset(texture, folder + "/texture.asset");
            AssetDatabase.CreateAsset(material, folder + "/material.mat");
            AssetImporter.GetAtPath(folder + "/texture.asset").assetBundleName = bundle + "_texture";
            AssetImporter.GetAtPath(folder + "/material.mat").assetBundleName = bundle;
            AssetDatabase.SaveAssets();
            foreach (bool recursive in new[] { false, true })
            {
                var response = JObject.FromObject(
                    ManageAsset.HandleCommand(
                        new JObject
                        {
                            ["action"] = "get_bundle_dependencies",
                            ["bundleName"] = bundle,
                            ["recursive"] = recursive,
                        }
                    )
                );
                Assert.IsTrue(response.Value<bool>("success"), response.ToString());
                CollectionAssert.AreEqual(
                    AssetDatabase.GetAssetBundleDependencies(bundle, recursive).OrderBy(name => name, StringComparer.Ordinal),
                    response["data"]["entries"].Values<string>()
                );
                Assert.AreEqual(recursive, response["data"].Value<bool>("recursive"));
                CollectionAssert.Contains(response["data"]["entries"].Values<string>().ToArray(), bundle + "_texture");
            }
        }

        [Test]
        public void HugePageIsEmptyAndUnknownAssignmentIsAnError()
        {
            JObject empty = Inspect("get_bundle_assets", 100, int.MaxValue);
            Assert.IsTrue(empty.Value<bool>("success"));
            Assert.IsEmpty(empty["data"]["entries"]);
            Assert.IsFalse(empty["data"].Value<bool>("hasMore"));
            Assert.IsFalse(
                JObject
                    .FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "get_bundle_assets", ["bundleName"] = bundle + "_missing" }))
                    .Value<bool>("success")
            );
        }

        [TestCase(0, 1)]
        [TestCase(101, 1)]
        [TestCase(1, 0)]
        public void RejectsInvalidPageBounds(int size, int page)
        {
            Assert.IsFalse(Inspect("get_bundle_assets", size, page).Value<bool>("success"));
        }

        [TestCase("true")]
        [TestCase("1")]
        public void RejectsNonBooleanRecursiveFlag(string token)
        {
            var response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = "get_bundle_dependencies",
                        ["bundleName"] = bundle,
                        ["recursive"] = JToken.Parse(token == "true" ? "\"true\"" : token),
                    }
                )
            );
            Assert.IsFalse(response.Value<bool>("success"));
        }

        private JObject Inspect(string action, int size, int page) =>
            JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["bundleName"] = bundle,
                        ["pageSize"] = size,
                        ["pageNumber"] = page,
                    }
                )
            );
    }
}
