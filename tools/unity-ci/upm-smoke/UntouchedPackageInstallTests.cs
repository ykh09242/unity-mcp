using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
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
            using (var sha = SHA256.Create())
            {
                var bytes = File.ReadAllBytes(Path.Combine(package.resolvedPath, "package.json"));
                var actual = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
                Assert.That(actual, Is.EqualTo(expected.manifestSha256), "UPM must import the original distributed manifest bytes");
            }
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
