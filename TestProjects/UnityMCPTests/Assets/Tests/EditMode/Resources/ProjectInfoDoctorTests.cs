using System;
using System.IO;
using System.Reflection;
using System.Text;
using MCPForUnity.Editor.Resources.Project;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Resources
{
    public class ProjectInfoDoctorTests
    {
        private const string Commit = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        private const string Pin = "https://github.com/ykh09242/unity-mcp/archive/" + Commit + ".zip#subdirectory=Server";

        private static JObject Capture(JObject package, string sourceOverride)
        {
            var method = typeof(ProjectInfo).GetMethod("BuildDoctorPackageMetadata", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method, "Doctor needs a read-only installed metadata projection.");
            return (JObject)method.Invoke(null, new object[] { package, sourceOverride });
        }

        private static JObject Package() =>
            new JObject
            {
                ["name"] = "com.ykh09242.unity-mcp",
                ["version"] = "1.2.1",
                ["mcpServerSource"] = Pin,
                ["mcpSkillRevision"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            };

        [Test]
        public void PackagePin_IndependentVersionsAndSkillIdentity()
        {
            JObject result = Capture(Package(), "");
            Assert.AreEqual("1.2.1", (string)result["version"]);
            Assert.AreEqual(Commit, (string)result["packageServerCommit"]);
            Assert.AreEqual(Commit, (string)result["effectiveServerCommit"]);
            Assert.AreEqual("package_pin", (string)result["effectiveSourceKind"]);
            Assert.AreEqual("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", (string)result["skillRevision"]);
            Assert.AreEqual(6, result.Count);
        }

        [Test]
        public void ExplicitImmutableOverride_PreservesPackageExpectation()
        {
            string other = new string('c', 40);
            var result = Capture(Package(), Pin.Replace(Commit, other));
            Assert.AreEqual(Commit, (string)result["packageServerCommit"]);
            Assert.AreEqual(other, (string)result["effectiveServerCommit"]);
            Assert.AreEqual("override_pin", (string)result["effectiveSourceKind"]);
        }

        [TestCase("C:/private/project")]
        [TestCase("git+https://github.com/ykh09242/unity-mcp.git@beta#subdirectory=Server")]
        [TestCase("https://user:secret@github.com/ykh09242/unity-mcp/archive/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.zip#subdirectory=Server")]
        [TestCase("https://github.com/other/repo/archive/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.zip#subdirectory=Server")]
        public void UncomparableOverride_NeverReturnsPrivateSource(string sourceOverride)
        {
            JObject result = Capture(Package(), sourceOverride);
            Assert.AreEqual("override_unpinned", (string)result["effectiveSourceKind"]);
            Assert.IsNull((string)result["effectiveServerCommit"]);
            StringAssert.DoesNotContain(sourceOverride, result.ToString());
            StringAssert.DoesNotContain("secret", result.ToString());
        }

        [Test]
        public void MissingPackage_ReturnsUnknownWithoutThrowing()
        {
            JObject result = Capture(null, "");
            Assert.IsNull((string)result["version"]);
            Assert.IsNull((string)result["effectiveServerCommit"]);
            Assert.AreEqual("unavailable", (string)result["effectiveSourceKind"]);
        }

        [TestCase("../private/version")]
        [TestCase("https://user:secret@host")]
        public void MalformedVersion_IsNotExposed(string version)
        {
            JObject package = Package();
            package["version"] = version;
            JObject result = Capture(package, "");
            Assert.IsNull((string)result["version"]);
            StringAssert.DoesNotContain(version, result.ToString());
        }

        [Test]
        public void SnapshotParser_DoesNotNormalizeOrModifyCallerMetadata()
        {
            JObject package = Package();
            string before = package.ToString();
            Capture(package, "C:/private/project");
            Assert.AreEqual(before, package.ToString());
        }

        private static JObject ReadMetadata(string root)
        {
            var method = typeof(ProjectInfo).GetMethod("ReadDoctorPackageMetadata", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(method, "Doctor must use scoped, bounded package metadata reads without AssetDatabase search.");
            return (JObject)method.Invoke(null, new object[] { root });
        }

        [TestCase("valid")]
        [TestCase("oversize")]
        [TestCase("malformed")]
        [TestCase("deep")]
        [TestCase("missing")]
        public void BoundedPackageFile_OnlyAcceptsSmallValidObject(string kind)
        {
            string root = Path.Combine(Path.GetTempPath(), "unity-mcp-doctor-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                string json = Package().ToString();
                if (kind == "oversize")
                    json = "{\"padding\":\"" + new string('x', 65536) + "\"}";
                else if (kind == "malformed")
                    json = "{ private-invalid-metadata";
                else if (kind == "deep")
                    json = "{\"a\":{\"a\":{\"a\":{\"a\":{\"a\":{\"a\":{\"a\":{\"a\":{\"a\":1}}}}}}}}}";
                if (kind != "missing")
                    File.WriteAllText(Path.Combine(root, "package.json"), json, new UTF8Encoding(false));
                JObject result = ReadMetadata(root);
                if (kind == "valid")
                    Assert.AreEqual("1.2.1", (string)result?["version"]);
                else
                    Assert.IsNull(result, "Unavailable metadata must not trigger a fallback package search.");
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("relative/package")]
        public void UnavailablePackageRoot_DoesNotSearchOrRead(string root) => Assert.IsNull(ReadMetadata(root));
    }
}
