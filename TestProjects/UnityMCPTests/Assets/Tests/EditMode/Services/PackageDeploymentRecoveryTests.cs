using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using MCPForUnity.Editor.Constants;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services;
using Microsoft.Win32.SafeHandles;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    // All files and preferences are owned fixture data; the active project package is never replaced.
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

        [Test]
        public void RestoreRejectsLegacyForeignTargetWithoutChangingEitherProject()
        {
            var prefs = new Dictionary<string, string>();
            string foreign = MakePackage("Foreign/Package", "foreign");
            string current = MakePackage("Current/Package", "current");
            string backup = MakePackage("Foreign/Library/MCPForUnityDeployBackups/old", "backup");
            prefs[EditorPrefKeys.PackageDeployLastBackupPath] = backup;
            prefs[EditorPrefKeys.PackageDeployLastTargetPath] = foreign;
            var service = Service("Current", () => current, prefs);
            Assert.That(service.RestoreLastBackup().Success, Is.False);
            Assert.That(File.ReadAllText(OwnedRelative(foreign, "Editor/marker.txt")), Is.EqualTo("foreign"));
            Assert.That(File.ReadAllText(OwnedRelative(current, "Editor/marker.txt")), Is.EqualTo("current"));
        }

        [Test]
        public void ProjectsKeepIndependentSourcesAndBackupsAndRestoreOnlyTheirOwnTarget()
        {
            var prefs = new Dictionary<string, string>();
            string a = MakePackage("A/Package", "a-old");
            string b = MakePackage("B/Package", "b-old");
            string sourceA = MakePackage("SourceA", "a-new");
            string sourceB = MakePackage("SourceB", "b-new");
            var serviceA = Service("A", () => a, prefs);
            var serviceB = Service("B", () => b, prefs);
            serviceA.SetStoredSourcePath(sourceA);
            serviceB.SetStoredSourcePath(sourceB);
            Assert.That(serviceA.DeployFromStoredSource().Success, Is.True);
            Assert.That(serviceB.DeployFromStoredSource().Success, Is.True);
            Assert.That(serviceA.GetStoredSourcePath(), Is.EqualTo(Path.GetFullPath(sourceA)));
            Assert.That(serviceB.RestoreLastBackup().Success, Is.True);
            Assert.That(File.ReadAllText(OwnedRelative(a, "Editor/marker.txt")), Is.EqualTo("a-new"));
            Assert.That(File.ReadAllText(OwnedRelative(b, "Editor/marker.txt")), Is.EqualTo("b-old"));
            Assert.That(serviceA.RestoreLastBackup().Success, Is.True);
            Assert.That(File.ReadAllText(OwnedRelative(a, "Editor/marker.txt")), Is.EqualTo("a-old"));
        }

        [Test]
        public void RestoreRejectsBackupAfterInstalledPackageLocationChanges()
        {
            var prefs = new Dictionary<string, string>();
            string original = MakePackage("A/Package", "old");
            string target = original;
            var service = Service("A", () => target, prefs);
            service.SetStoredSourcePath(MakePackage("Source", "new"));
            Assert.That(service.DeployFromStoredSource().Success, Is.True);
            target = MakePackage("A/Replacement", "replacement");
            Assert.That(service.HasBackup(), Is.False);
            Assert.That(service.RestoreLastBackup().Success, Is.False);
            Assert.That(File.ReadAllText(OwnedRelative(original, "Editor/marker.txt")), Is.EqualTo("new"));
            Assert.That(File.ReadAllText(OwnedRelative(target, "Editor/marker.txt")), Is.EqualTo("replacement"));
        }

        [Test]
        public void FailedPayloadPreparationPreservesBothInstalledSubtrees()
        {
            string source = Owned("Incomplete");
            WriteMarker(source, "new");
            string target = MakePackage("Target", "old");
            var method = typeof(PackageDeploymentService).GetMethod("CopyCoreFolders", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.Throws<TargetInvocationException>(() =>
                method.Invoke(Service("Project", () => target, new Dictionary<string, string>()), new object[] { source, target })
            );
            Assert.That(File.ReadAllText(OwnedRelative(target, "Editor/marker.txt")), Is.EqualTo("old"));
            Assert.That(File.ReadAllText(OwnedRelative(target, "Runtime/marker.txt")), Is.EqualTo("old"));
        }

        [Test]
        public void DeploymentAndRestoreKeepMetadataAndBothCodeTreesTogether()
        {
            string source = MakePackage("Source", "new");
            string target = MakePackage("Target", "old");
            string oldMetadata = File.ReadAllText(OwnedRelative(target, "package.json"));
            string newMetadata =
                "{\"name\":\"com.ykh09242.unity-mcp\",\"version\":\"2.0.0\",\"mcpServerSource\":\"https://github.com/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server\",\"dependencies\":{\"example\":\"2.0.0\"}}";
            File.WriteAllText(OwnedRelative(source, "package.json"), newMetadata);
            var service = Service("Project", () => target, new Dictionary<string, string>());
            service.SetStoredSourcePath(source);
            Assert.That(service.DeployFromStoredSource().Success, Is.True);
            Assert.That(File.ReadAllText(OwnedRelative(target, "package.json")), Is.EqualTo(newMetadata));
            Assert.That(File.ReadAllText(OwnedRelative(target, "Runtime/marker.txt")), Is.EqualTo("new"));
            Assert.That(service.RestoreLastBackup().Success, Is.True);
            Assert.That(File.ReadAllText(OwnedRelative(target, "package.json")), Is.EqualTo(oldMetadata));
            Assert.That(File.ReadAllText(OwnedRelative(target, "Editor/marker.txt")), Is.EqualTo("old"));
        }

        [Test]
        public void CopyFailureAfterOneStagedSubtreePreservesCompleteTarget()
        {
            string target = MakePackage("Target", "old");
            var original = Directory.GetFiles(target, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            Assert.Throws<IOException>(() =>
                GuardedFileSystem.ReplaceDirectory(
                    target,
                    stage =>
                    {
                        Directory.CreateDirectory(Path.Combine(stage, "Editor"));
                        File.WriteAllText(Path.Combine(stage, "Editor/marker.txt"), "new");
                        throw new IOException("Runtime copy failed after Editor was staged");
                    }
                )
            );
            foreach (var pair in original)
                CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key));
            Assert.IsEmpty(Directory.GetDirectories(_root, ".mcp-stage-*"));
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string path, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);

        [Test]
        public void BlockedFinalDirectoryRenameRollsBackCompleteTarget()
        {
            if (Environment.OSVersion.Platform != PlatformID.Win32NT)
                Assert.Ignore("Windows directory sharing semantics are required.");
            string target = MakePackage("Target", "old");
            string source = MakePackage("Source", "new");
            var original = Directory.GetFiles(target, "*", SearchOption.AllDirectories).ToDictionary(path => path, File.ReadAllBytes);
            SafeFileHandle held = null;
            try
            {
                Assert.Throws<IOException>(() =>
                    GuardedFileSystem.ReplaceDirectory(
                        target,
                        stage =>
                        {
                            GuardedFileSystem.CopyDirectory(source, stage);
                            // Keep the fully staged directory readable, but deny its final rename.
                            held = CreateFileW(stage, 0x80000000, 3, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero);
                            Assert.IsFalse(held.IsInvalid);
                        }
                    )
                );
                foreach (var pair in original)
                    CollectionAssert.AreEqual(pair.Value, File.ReadAllBytes(pair.Key));
                Assert.IsEmpty(Directory.GetDirectories(_root, ".mcp-previous-*"), "The previous target must have been restored.");
            }
            finally
            {
                held?.Dispose();
            }
        }

        private PackageDeploymentService Service(string project, Func<string> target, Dictionary<string, string> prefs) =>
            new PackageDeploymentService(
                Owned(project),
                target,
                key => prefs.TryGetValue(key, out string value) ? value : "",
                (key, value) => prefs[key] = value,
                key => prefs.Remove(key)
            );

        private string MakePackage(string relative, string label)
        {
            string target = Owned(relative);
            WriteMarker(target, label);
            Directory.CreateDirectory(OwnedRelative(target, "Runtime"));
            File.WriteAllText(OwnedRelative(target, "Runtime/marker.txt"), label);
            File.WriteAllText(
                OwnedRelative(target, "package.json"),
                "{\"name\":\"com.ykh09242.unity-mcp\",\"version\":\"1.0.0\",\"mcpServerSource\":\"https://github.com/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server\"}"
            );
            return target;
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
