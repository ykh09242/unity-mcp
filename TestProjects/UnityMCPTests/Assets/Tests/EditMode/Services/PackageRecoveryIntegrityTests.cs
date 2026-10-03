using System;
using System.IO;
using System.Reflection;
using MCPForUnity.Editor.Services;
using NUnit.Framework;
using UnityEditor.PackageManager;

namespace MCPForUnityTests.Editor.Services
{
    // These tests never initialize PackageJobManager, query installed packages, or invoke Client.
    public class PackageRecoveryIntegrityTests
    {
        private static PackageInfo Info(PackageSource source, string identifier, string version = "1.0.0", string path = null)
        {
            var constructor = typeof(PackageInfo).GetConstructor(BindingFlags.Instance | BindingFlags.NonPublic,
                null, Type.EmptyTypes, null);
            Assert.NotNull(constructor, "PackageInfo's managed constructor must be available.");
            var info = (PackageInfo)constructor.Invoke(null);
            Set(info, "m_Name", "com.fixture.tool");
            Set(info, "m_Version", version);
            Set(info, "m_Source", source);
            Set(info, "m_PackageId", identifier);
            Set(info, "m_ResolvedPath", path);
            return info;
        }

        private static void Set(PackageInfo info, string name, object value)
        {
            var field = typeof(PackageInfo).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.NotNull(field, "PackageInfo metadata layout changed: " + name);
            field.SetValue(info, value);
        }

        [TestCase("com.fixture.tool@2.0.0", "1.0.0", false)]
        [TestCase("com.fixture.tool@2.0.0", "2.0.0", true)]
        [TestCase("com.fixture.tool@2.0.0-preview.3", "2.0.0-preview.3", true)]
        [TestCase("com.fixture.tool", "9.4.0", true)]
        [TestCase("com.fixture.tool@", "9.4.0", true)]
        public void NamedVersionRecoveryMatchesRequestedVersion(string request, string installed, bool expected)
        {
            var info = Info(PackageSource.Registry, "com.fixture.tool@" + installed, installed);
            Assert.AreEqual(expected, PackageRecoveryIdentity.MatchesVersion(info, request));
        }

        [TestCase("https://fixture.example/repo.git#main", "https://fixture.example/repo.git#main", true)]
        [TestCase("https://fixture.example/repo.git#main", "https://fixture.example/repo.git#main-other", false)]
        [TestCase("https://fixture.example/repo.git?path=/Package#main", "https://fixture.example/repo.git?path=/Package#main", true)]
        [TestCase("https://fixture.example/repo.git?path=/Package#main", "https://fixture.example/repo.git?path=/Package#main-other", false)]
        [TestCase("ssh://git@fixture.example/repo.git#main", "ssh://git@fixture.example/repo.git#main", true)]
        [TestCase("git+https://fixture.example/repo.git#main", "https://fixture.example/repo.git#main", true)]
        [TestCase("file:///fixture/repo.git#main", "file:///fixture/repo.git#main", true)]
        [TestCase("git+file:///fixture/repo.git#main", "file:///fixture/repo.git#main", true)]
        [TestCase("file:///fixture/repo.git#main", "file:///fixture/repo.git#main-other", false)]
        [TestCase("git+file://fixture.example/repo", "file://fixture.example/repo", true)]
        [TestCase("git+file:///fixture/repo", "file:///fixture/repo", true)]
        [TestCase("file:///fixture/repo.git?path=/Package#main", "file:///fixture/repo.git?path=/Package#main", true)]
        public void GitRecoveryMatchesCompleteSource(string request, string installed, bool expected)
        {
            var info = Info(PackageSource.Git, "com.fixture.tool@" + installed);
            Assert.AreEqual(expected, PackageRecoveryIdentity.MatchesSource(info, request, null));
        }

        [TestCase("file:../Fixture", "Fixture", true)]
        [TestCase("file:../Fixture/", "Fixture", true)]
        [TestCase("file:..\\Fixture", "Fixture", true)]
        [TestCase("file:../Fixture", "Fixture/", true)]
        [TestCase("file:../Fixture", "Fixture-other", false)]
        [TestCase("file:../Fixture", "Other/Fixture", false)]
        public void LocalRecoveryUsesCompletePackagesRelativePath(string request, string installed, bool expected)
        {
            // All paths are synthetic strings; no directory or file is created/read.
            string root = Path.Combine(Path.GetTempPath(), "McpPackageRecovery_" + Guid.NewGuid().ToString("N"));
            string packages = Path.Combine(root, "Packages");
            string path = Path.Combine(root, installed);
            var info = Info(PackageSource.Local, "com.fixture.tool@file:" + path, path: path);
            Assert.AreEqual(expected, PackageRecoveryIdentity.MatchesSource(info, request, packages));
        }

        [TestCase("file:../Fixture.tgz", true)]
        [TestCase("file:../Other.tgz", false)]
        public void TarballRecoveryMatchesArchiveIdentifier(string installed, bool expected)
        {
            var info = Info(PackageSource.LocalTarball, "com.fixture.tool@" + installed,
                path: Path.Combine(Path.GetTempPath(), "Fixture.tgz"));
            Assert.AreEqual(expected, PackageRecoveryIdentity.MatchesSource(info, "file:../Fixture.tgz", null));
        }

        [Test]
        public void GitIdentifierCannotBeProvedByRegistryMetadata()
        {
            const string source = "https://fixture.example/repo.git#main";
            var info = Info(PackageSource.Registry, "com.fixture.tool@" + source);
            Assert.IsFalse(PackageRecoveryIdentity.MatchesSource(info, source, null));
        }

        [TestCase("file:///fixture/Folder", true)]
        [TestCase("file:///fixture/Folder-other", false)]
        public void FileUrlCanRetainLocalPackageIdentity(string installed, bool expected)
        {
            var info = Info(PackageSource.Local, "com.fixture.tool@" + installed);
            Assert.AreEqual(expected, PackageRecoveryIdentity.MatchesSource(info, "file:///fixture/Folder", null));
        }
    }
}
