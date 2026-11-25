using System;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Resources.Project;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor.PackageManager;

namespace MCPForUnityTests.Editor.Resources
{
    public class ProjectPackagesTests
    {
        [Test]
        public void InventoryMatchesEveryRegisteredNameAndVersion()
        {
            var expected = PackageInfo.GetAllRegisteredPackages().OrderBy(package => package.name, StringComparer.Ordinal).ToArray();
            var result = JObject.FromObject(ProjectPackages.HandleCommand(new JObject()));
            Assert.IsTrue((bool)result["success"], result.ToString());
            Assert.AreEqual(expected.Length, (int)result["data"]["count"]);
            var rows = (JArray)result["data"]["packages"];
            CollectionAssert.AreEqual(expected.Select(p => p.name), rows.Select(p => (string)p["name"]));
            CollectionAssert.AreEqual(expected.Select(p => p.version), rows.Select(p => (string)p["version"]));
        }

        [Test]
        public void DetailsMatchRegisteredVersionAndOriginForEachInstalledSource()
        {
            var samples = PackageInfo
                .GetAllRegisteredPackages()
                .GroupBy(info => new { info.source, registry = info.registry?.url })
                .Select(group => group.First());
            foreach (var info in samples)
            {
                var result = JObject.FromObject(ProjectPackages.HandleCommand(new JObject { ["package"] = info.name }));
                Assert.IsTrue((bool)result["success"], result.ToString());
                var data = result["data"];
                Assert.AreEqual(info.name, (string)data["name"]);
                Assert.AreEqual(info.version, (string)data["version"]);
                Assert.AreEqual(info.displayName, (string)data["display_name"]);
                Assert.AreEqual(PackageSourceSecurity.Redact(info.description), (string)data["description"]);
                string author = string.IsNullOrWhiteSpace(info.author?.name) ? null : PackageSourceSecurity.Redact(info.author.name);
                Assert.AreEqual(author, (string)data["author"]);
                Assert.AreEqual(info.source.ToString(), (string)data["source"]);
                Assert.AreEqual(info.isDirectDependency, (bool)data["is_direct_dependency"]);
                if (info.source != PackageSource.Registry || info.registry == null)
                    Assert.AreEqual(JTokenType.Null, data["registry"].Type);
                else
                {
                    Assert.AreEqual(PackageSourceSecurity.Redact(info.registry.name), (string)data["registry"]["name"]);
                    string expectedUrl = PackageSourceSecurity.IsValidRegistryUrl(info.registry.url) ? info.registry.url : null;
                    Assert.AreEqual(expectedUrl, (string)data["registry"]["url"]);
                }
            }
        }

        [TestCase(PackageSource.Local)]
        [TestCase(PackageSource.Git)]
        [TestCase(PackageSource.Embedded)]
        public void ConfiguredRegistryIsNotTheOriginOfAnUnregisteredSource(PackageSource source)
        {
            var configured = UnityEngine.JsonUtility.FromJson<RegistryInfo>("{}");
            Assert.IsNotNull(configured);
            Assert.IsNull(PackageSourceSecurity.RegistryMetadata(source, configured));
        }

        [TestCase("")]
        [TestCase("com.example.mcp.missing-package")]
        public void MissingPackageDoesNotReturnTheInventory(string name)
        {
            var result = JObject.FromObject(ProjectPackages.HandleCommand(new JObject { ["package"] = name }));
            Assert.IsFalse((bool)result["success"]);
            Assert.IsNull(result["data"]);
        }
    }
}
