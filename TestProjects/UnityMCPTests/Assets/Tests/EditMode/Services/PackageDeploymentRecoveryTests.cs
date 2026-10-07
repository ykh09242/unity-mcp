using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    // Public deployment/restore use persistent user prefs; these native tests deliberately exercise
    // only the actual private path/backup helpers with an owned synthetic filesystem root.
    public class PackageDeploymentRecoveryTests
    {
        private string _root;
        private string _tempRoot;

        [SetUp]
        public void SetUp()
        {
            _tempRoot = Path.GetFullPath(Path.GetTempPath());
            _root = Path.GetFullPath(Path.Combine(_tempRoot, "UnityMCPPackageRecovery_" + Guid.NewGuid().ToString("N")));
            RequireOwnedPath(_root);
            Directory.CreateDirectory(_root);
        }

        [TearDown]
        public void TearDown()
        {
            if (!string.IsNullOrEmpty(_root) && Directory.Exists(_root))
            {
                RequireOwnedPath(_root);
                Directory.Delete(_root, true);
            }
        }

        [TestCase("Package", "Package", true)]
        [TestCase("Package", "Package/Editor/Incoming", true)]
        [TestCase("Package/Runtime/Incoming", "Package", true)]
        [TestCase("Package", "PackageExtra", false)]
        [TestCase("Package", "OtherPackage", false)]
        [TestCase("Package/.", "Package/Editor/../", true)]
        public void OverlapUsesCanonicalDirectoryBoundaries(string a, string b, bool expected)
        {
            var method = typeof(PackageDeploymentService).GetMethod("PathsOverlap", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.That(method, Is.Not.Null);
            Assert.That((bool)method.Invoke(null, new object[] { Owned(a), Owned(b) }), Is.EqualTo(expected));
        }

        [Test]
        public void RepeatedBackupsPreserveEarlierContentsAndUnrelatedRecoveryFolders()
        {
            string target = Owned("Target");
            string backups = Owned("Backups");
            WriteMarker(target, "original");
            string legacy = Owned("Backups/backup_" + DateTime.Now.ToString("yyyyMMdd_HHmmss"));
            WriteMarker(legacy, "legacy");

            string first = Backup(target, backups);
            WriteMarker(target, "updated");
            string second = Backup(target, backups);

            Assert.That(first, Is.Not.EqualTo(second));
            Assert.That(File.ReadAllText(OwnedRelative(first, "Editor/marker.txt")), Is.EqualTo("original"));
            Assert.That(File.ReadAllText(OwnedRelative(second, "Editor/marker.txt")), Is.EqualTo("updated"));
            Assert.That(File.ReadAllText(OwnedRelative(legacy, "Editor/marker.txt")), Is.EqualTo("legacy"));
        }

        [Test]
        public void BackupCopiesEntirePackageWithoutChangingTarget()
        {
            string target = Owned("Target");
            WriteMarker(target, "editor");
            string runtime = OwnedRelative(target, "Runtime");
            Directory.CreateDirectory(runtime);
            File.WriteAllText(OwnedRelative(runtime, "marker.txt"), "runtime");
            File.WriteAllText(OwnedRelative(target, "package.json"), "manifest");

            string backup = Backup(target, Owned("Backups"));

            Assert.That(File.ReadAllText(OwnedRelative(backup, "Runtime/marker.txt")), Is.EqualTo("runtime"));
            Assert.That(File.ReadAllText(OwnedRelative(backup, "package.json")), Is.EqualTo("manifest"));
            Assert.That(File.ReadAllText(OwnedRelative(target, "Editor/marker.txt")), Is.EqualTo("editor"));
        }

        private string Backup(string target, string backups)
        {
            RequireOwnedPath(target);
            RequireOwnedPath(backups);
            var method = typeof(PackageDeploymentService).GetMethod("CreateBackup", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(method, Is.Not.Null);
            string path = (string)method.Invoke(new PackageDeploymentService(), new object[] { target, backups });
            RequireOwnedPath(path);
            return path;
        }

        private void WriteMarker(string target, string content)
        {
            string editor = OwnedRelative(target, "Editor");
            Directory.CreateDirectory(editor);
            File.WriteAllText(OwnedRelative(editor, "marker.txt"), content);
        }

        private string Owned(string relative) => OwnedRelative(_root, relative);

        private string OwnedRelative(string parent, string relative)
        {
            string path = Path.GetFullPath(Path.Combine(parent, relative)).Replace('\\', '/');
            RequireOwnedPath(path);
            return path;
        }

        private void RequireOwnedPath(string path)
        {
            string resolved = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string root = Path.GetFullPath(_root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string temp = Path.GetFullPath(_tempRoot).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            Assert.That(root.StartsWith(temp + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase), Is.True);
            Assert.That(Path.GetFileName(root).StartsWith("UnityMCPPackageRecovery_", StringComparison.Ordinal), Is.True);
            Assert.That(
                resolved.Equals(root, StringComparison.OrdinalIgnoreCase)
                    || resolved.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase),
                Is.True,
                "All native fixture filesystem effects must stay under the unique owned root."
            );
        }
    }
}
