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
            public string name;
            public string version;
        }

        [Serializable]
        private class ExpectedInstallation
        {
            public string packageName;
            public string manifestSha256;
            public Dependency[] dependencies;
        }

        [Test]
        public void OriginalManifestImportsAndResolvesDependencies()
        {
            var project = Directory.GetParent(Application.dataPath).FullName;
            var expected = JsonUtility.FromJson<ExpectedInstallation>(File.ReadAllText(Path.Combine(project, ".upm-smoke.json")));
            var registered = PackageInfo.GetAllRegisteredPackages();
            var package = registered.Single(item => item.name == expected.packageName);
            Assert.That(package.source, Is.EqualTo(PackageSource.Local));
            Assert.That(Path.GetFullPath(package.resolvedPath), Is.EqualTo(Path.GetFullPath(Path.Combine(project, "../package"))));
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
