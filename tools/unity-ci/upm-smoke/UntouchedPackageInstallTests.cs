using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using NUnit.Framework;
using UnityEditor.PackageManager;
using UnityEngine;

namespace UntouchedUpmSmoke
{
    public class UntouchedPackageInstallTests
    {
        [Serializable]
        private class Dependency
        {
            public string name = null;
            public string version = null;
        }

        [Serializable]
        private class ExpectedInstallation
        {
            public string packageName = null;
            public string manifestSha256 = null;
            public string gitRevision = null;
            public Dependency[] dependencies = null;
        }

        private static string ManifestDigest(byte[] bytes)
        {
            using (var sha = SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
        }

        private static bool MatchesCommittedManifest(byte[] imported, string digest, bool git)
        {
            if (ManifestDigest(imported) == digest)
                return true;
            if (!git)
                return false;

            // Some UPM versions append this property when materializing a Git package in Library/PackageCache.
            // Remove only the appended 40-hex field and compare the remaining bytes with the original Git blob.
            var text = Encoding.UTF8.GetString(imported);
            var source = Regex.Replace(text, @",\s*""_fingerprint""\s*:\s*""[0-9a-f]{40}""(?=\s*}\s*\z)", string.Empty);
            return source != text && ManifestDigest(Encoding.UTF8.GetBytes(source)) == digest;
        }

        [Test]
        public void GitCacheFingerprintCannotMaskAChangedManifestProperty()
        {
            var original = "{\n  \"version\": \"1.0.0\"\n}\n";
            var imported = original.Replace("\"1.0.0\"", "\"1.0.0\",\n  \"_fingerprint\": \"" + new string('a', 40) + "\"");
            var digest = ManifestDigest(Encoding.UTF8.GetBytes(original));
            Assert.That(MatchesCommittedManifest(Encoding.UTF8.GetBytes(imported), digest, true), Is.True);
            Assert.That(MatchesCommittedManifest(Encoding.UTF8.GetBytes(imported), digest, false), Is.False);
            Assert.That(MatchesCommittedManifest(Encoding.UTF8.GetBytes(imported.Replace("1.0.0", "2.0.0")), digest, true), Is.False);
        }

        [Test]
        public void OriginalManifestImportsAndResolvesDependencies()
        {
            var project = Directory.GetParent(Application.dataPath).FullName;
            var expected = JsonUtility.FromJson<ExpectedInstallation>(File.ReadAllText(Path.Combine(project, ".upm-smoke.json")));
            var registered = PackageInfo.GetAllRegisteredPackages();
            var package = registered.Single(item => item.name == expected.packageName);
            if (string.IsNullOrEmpty(expected.gitRevision))
            {
                Assert.That(package.source, Is.EqualTo(PackageSource.Local));
                Assert.That(Path.GetFullPath(package.resolvedPath), Is.EqualTo(Path.GetFullPath(Path.Combine(project, "../package"))));
            }
            else
            {
                Assert.That(package.source, Is.EqualTo(PackageSource.Git));
                Assert.That(package.git.hash, Is.EqualTo(expected.gitRevision));
            }
            var bytes = File.ReadAllBytes(Path.Combine(package.resolvedPath, "package.json"));
            File.WriteAllBytes(Path.Combine(project, ".upm-imported-package.json"), bytes);
            Assert.That(
                MatchesCommittedManifest(bytes, expected.manifestSha256, package.source == PackageSource.Git),
                Is.True,
                "Imported manifest must match the committed SHA256 after accounting for UPM's generated Git cache fingerprint"
            );
            foreach (var dependency in expected.dependencies)
            {
                Assert.That(registered.Any(item => item.name == dependency.name), Is.True, "UPM did not resolve " + dependency.name + "@" + dependency.version);
            }
            var editor = Assembly.Load("MCPForUnity.Editor");
            Assert.That(PackageInfo.FindForAssembly(editor).name, Is.EqualTo(expected.packageName));
            Assert.That(editor.GetTypes().Length, Is.GreaterThan(0), "Package Editor types and their dependencies must load");
        }
    }
}
