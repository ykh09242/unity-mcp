using System;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace MCPForUnityTests.Editor.Helpers
{
    public class PackageTimestampTests
    {
        [TestCase("com.unity.test-framework")]
        [TestCase("com.ykh09242.unity-mcp")]
        public void RegisteredPackageMetadataUsesResolvedDiskTimestamp(string packageName)
        {
            var package = PackageInfo.GetAllRegisteredPackages().Single(p => p.name == packageName);
            string path = package.assetPath + "/package.json";
            DateTime expected = File.GetLastWriteTimeUtc(Path.Combine(package.resolvedPath, "package.json"));
            Assert.Greater(expected.Year, 2000);
            JObject response = JObject.FromObject(ManageAsset.HandleCommand(new JObject { ["action"] = "get_info", ["path"] = path }));
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            string timestamp = (string)response["data"]?["lastWriteTimeUtc"];
            Assert.AreEqual(expected, DateTime.Parse(timestamp, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind));
        }

        [TestCase("search")]
        [TestCase("search_metadata_v1")]
        public void PackageDateFilterRetainsAnExistingRecentAsset(string action)
        {
            var package = PackageInfo.GetAllRegisteredPackages().Single(p => p.name == "com.unity.test-framework");
            string path = AssetDatabase
                .FindAssets("t:MonoScript", new[] { package.assetPath })
                .Select(AssetDatabase.GUIDToAssetPath)
                .First(p => p.StartsWith(package.assetPath + "/", StringComparison.Ordinal) && p.Count(c => c == '/') >= 3);
            string physical = Path.Combine(package.resolvedPath, path.Substring(package.assetPath.Length + 1));
            DateTime cutoff = File.GetLastWriteTimeUtc(physical).AddDays(-1);
            JObject response = JObject.FromObject(
                ManageAsset.HandleCommand(
                    new JObject
                    {
                        ["action"] = action,
                        ["path"] = Path.GetDirectoryName(path).Replace('\\', '/'),
                        ["searchPattern"] = Path.GetFileNameWithoutExtension(path),
                        ["filterDateAfter"] = cutoff.ToString("o", CultureInfo.InvariantCulture),
                        ["pageSize"] = 100,
                    }
                )
            );
            Assert.IsTrue(response.Value<bool>("success"), response.ToString());
            Assert.IsTrue(response.Descendants().OfType<JProperty>().Any(p => p.Name == "path" && (string)p.Value == path), response.ToString());
        }

        [TestCase("Packages/embedded")]
        [TestCase("Library/PackageCache/registry@1.0")]
        [TestCase("LocalPackage")]
        public void PackagePhysicalRootMappingPreservesKnownTimestamp(string layout)
        {
            string root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "PackageMtime_" + Guid.NewGuid().ToString("N"));
            try
            {
                string physical = Path.Combine(root, layout);
                Directory.CreateDirectory(physical);
                string file = Path.Combine(physical, "owned.txt");
                File.WriteAllText(file, "fixture");
                File.SetLastWriteTimeUtc(file, new DateTime(2024, 1, 2, 3, 4, 5, DateTimeKind.Utc));
                MethodInfo resolver = typeof(AssetPathUtility).GetMethod("ResolvePackageFilePath", BindingFlags.Static | BindingFlags.NonPublic);
                Assert.IsNotNull(resolver, "Package paths need an explicit registered disk root.");
                string actual = (string)
                    resolver.Invoke(null, new object[] { "Packages/com.example.fixture/owned.txt", "Packages/com.example.fixture", physical });
                Assert.AreEqual(Path.GetFullPath(file), actual);
                Assert.AreEqual(new DateTime(2024, 1, 2, 3, 4, 5, DateTimeKind.Utc), File.GetLastWriteTimeUtc(actual));
                var error = Assert.Throws<TargetInvocationException>(() =>
                    resolver.Invoke(null, new object[] { "Packages/com.example.fixture/../../escape.txt", "Packages/com.example.fixture", physical })
                );
                Assert.IsInstanceOf<InvalidOperationException>(error.InnerException);
            }
            finally
            {
                if (Directory.Exists(root))
                    Directory.Delete(root, true);
            }
        }
    }
}
