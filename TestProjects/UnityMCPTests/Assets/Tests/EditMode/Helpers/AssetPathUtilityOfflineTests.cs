using System;
using System.IO;
using System.Reflection;
using Newtonsoft.Json.Linq;
using MCPForUnity.Editor.Migrations;
using NUnit.Framework;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Constants;
using UnityEditor;

namespace MCPForUnityTests.Editor.Helpers
{
    public class AssetPathUtilityOfflineTests
    {
        private const string PinnedSource = "git+https://github.com/ykh09242/unity-mcp.git@0123456789abcdef0123456789abcdef01234567#subdirectory=Server";
        private const string ArchiveSource = "https://github.com/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server";
        private bool _originalForceRefresh;

        [SetUp]
        public void SetUp()
        {
            _originalForceRefresh = EditorPrefs.GetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
        }

        [TearDown]
        public void TearDown()
        {
            EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, _originalForceRefresh);
        }

        [Test]
        public void ShouldUseUvxOffline_WhenForceRefreshEnabled_ReturnsFalse()
        {
            EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, true);
            Assert.IsFalse(AssetPathUtility.ShouldUseUvxOffline());
        }

        [Test]
        public void ShouldUseUvxOffline_DoesNotThrow()
        {
            EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
            Assert.DoesNotThrow(() => AssetPathUtility.ShouldUseUvxOffline());
        }

        [TestCase(PinnedSource)]
        [TestCase(ArchiveSource)]
        public void PinnedSource_IsIndependentOfPackageVersion(string source)
        {
            var package = new JObject { ["version"] = "10.3.1-beta.1", ["mcpServerSource"] = source };
            Assert.AreEqual(source, AssetPathUtility.GetPinnedServerSource(package));
            CollectionAssert.AreEqual(new[] { "--from", source }, AssetPathUtility.GetBetaServerFromArgsList(null, source));
            Assert.AreEqual("--from \"" + source + "\"", AssetPathUtility.GetBetaServerFromArgs(null, source, true));
        }

        [Test]
        public void CommitArchiveOverride_RemainsRemoteAndCacheable()
        {
            bool hadOverride = EditorPrefs.HasKey(EditorPrefKeys.GitUrlOverride);
            string originalOverride = EditorPrefs.GetString(EditorPrefKeys.GitUrlOverride, "");
            try
            {
                EditorPrefs.SetBool(EditorPrefKeys.DevModeForceServerRefresh, false);
                EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, ArchiveSource);
                Assert.AreEqual(ArchiveSource, AssetPathUtility.GetMcpServerPackageSource());
                Assert.IsFalse(AssetPathUtility.IsLocalServerPath());
                Assert.IsFalse(AssetPathUtility.ShouldForceUvxRefresh());
                Assert.IsNull(AssetPathUtility.GetLocalServerPath());
            }
            finally
            {
                if (hadOverride) EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, originalOverride);
                else EditorPrefs.DeleteKey(EditorPrefKeys.GitUrlOverride);
            }
        }

        [TestCase(null)]
        [TestCase("")]
        [TestCase("mcpforunityserver")]
        [TestCase("mcpforunityserver>=0.0.0a0")]
        [TestCase("git+https://github.com/ykh09242/unity-mcp.git@beta#subdirectory=Server")]
        [TestCase("git+https://github.com/ykh09242/unity-mcp.git@01234567#subdirectory=Server")]
        [TestCase("git+https://github.com/CoplayDev/unity-mcp.git@0123456789abcdef0123456789abcdef01234567#subdirectory=Server")]
        [TestCase("git+https://github.com/ykh09242/unity-mcp.git@0123456789abcdef0123456789abcdef01234567#subdirectory=Other")]
        [TestCase("git+https://github.com/ykh09242/unity-mcp.git@z123456789abcdef0123456789abcdef01234567#subdirectory=Server")]
        [TestCase("https://github.com/ykh09242/unity-mcp/archive/beta.zip#subdirectory=Server")]
        [TestCase("https://github.com/ykh09242/unity-mcp/archive/01234567.zip#subdirectory=Server")]
        [TestCase("https://github.com/ykh09242/unity-mcp/archive/z123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server")]
        [TestCase("https://github.com/CoplayDev/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server")]
        [TestCase("https://github.com/ykh09242/other/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server")]
        [TestCase("https://github.com.example/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server")]
        [TestCase("http://github.com/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Server")]
        [TestCase("https://github.com/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.tar.gz#subdirectory=Server")]
        [TestCase("https://github.com/ykh09242/unity-mcp/archive/0123456789abcdef0123456789abcdef01234567.zip#subdirectory=Other")]
        [TestCase(ArchiveSource + "&extra=true")]
        [TestCase(ArchiveSource + "\n")]
        [TestCase(" " + ArchiveSource)]
        public void InvalidDefault_FailsWithoutPyPiOrEmptyFromFallback(string source)
        {
            var package = new JObject { ["mcpServerSource"] = source };
            StringAssert.Contains("mcpServerSource", Assert.Throws<InvalidOperationException>(() => AssetPathUtility.GetPinnedServerSource(package)).Message);
            Assert.Throws<InvalidOperationException>(() => AssetPathUtility.GetBetaServerFromArgs(null, source));
            Assert.Throws<InvalidOperationException>(() => AssetPathUtility.GetBetaServerFromArgsList(null, source));
        }

        [Test]
        public void MissingOrNonStringMetadata_FailsClearly()
        {
            Assert.Throws<InvalidOperationException>(() => AssetPathUtility.GetPinnedServerSource(null));
            Assert.Throws<InvalidOperationException>(() => AssetPathUtility.GetPinnedServerSource(new JObject()));
            Assert.Throws<InvalidOperationException>(() => AssetPathUtility.GetPinnedServerSource(new JObject { ["mcpServerSource"] = 123 }));
        }

        [TestCase("mcpforunityserver==10.3.0")]
        [TestCase("git+https://github.com/example/development.git@main#subdirectory=Server")]
        [TestCase(ArchiveSource)]
        public void ExplicitCallerOverride_RemainsSupported(string source)
        {
            Assert.AreEqual("--from " + source, AssetPathUtility.GetBetaServerFromArgs(source, null));
            CollectionAssert.AreEqual(new[] { "--from", source }, AssetPathUtility.GetBetaServerFromArgsList(source, null));
        }

        [Test]
        public void LocalRepoOverride_IsCanonicalizedInBothBuilders()
        {
            string root = Path.Combine(Path.GetTempPath(), "unity-mcp-source-test-" + Guid.NewGuid().ToString("N"));
            string server = Path.Combine(root, "Server");
            Directory.CreateDirectory(server);
            bool hadOverride = EditorPrefs.HasKey(EditorPrefKeys.GitUrlOverride);
            string originalOverride = EditorPrefs.GetString(EditorPrefKeys.GitUrlOverride, "");
            try
            {
                File.WriteAllText(Path.Combine(server, "pyproject.toml"), "[project]\nname = 'test'\n");
                Assert.AreEqual("--from \"" + server + "\"", AssetPathUtility.GetBetaServerFromArgs(root, null, true));
                CollectionAssert.AreEqual(new[] { "--from", server }, AssetPathUtility.GetBetaServerFromArgsList(root, null));
                CollectionAssert.AreEqual(new[] { "--from", "file://" + server }, AssetPathUtility.GetBetaServerFromArgsList("file://" + root, null));
                EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, root);
                Assert.AreEqual(server, AssetPathUtility.GetMcpServerPackageSource());
                Assert.AreEqual(server, EditorPrefs.GetString(EditorPrefKeys.GitUrlOverride));
                CollectionAssert.AreEqual(new[] { "--from", server }, AssetPathUtility.GetBetaServerFromArgsList());
                Assert.AreEqual("--from \"" + server + "\"", AssetPathUtility.GetBetaServerFromArgs(true));
            }
            finally
            {
                if (hadOverride) EditorPrefs.SetString(EditorPrefKeys.GitUrlOverride, originalOverride);
                else EditorPrefs.DeleteKey(EditorPrefKeys.GitUrlOverride);
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void OfflineCache_InvalidatesWhenPinnedSourceChanges()
        {
            var flags = BindingFlags.NonPublic | BindingFlags.Static;
            var sourceField = typeof(AssetPathUtility).GetField("_offlineCacheSource", flags);
            var timeField = typeof(AssetPathUtility).GetField("_offlineCacheTimestamp", flags);
            object originalSource = sourceField.GetValue(null);
            object originalTime = timeField.GetValue(null);
            try
            {
                sourceField.SetValue(null, PinnedSource);
                timeField.SetValue(null, 100.0);
                Assert.IsTrue(AssetPathUtility.IsOfflineProbeCacheValid(PinnedSource, 101.0));
                Assert.IsFalse(AssetPathUtility.IsOfflineProbeCacheValid(PinnedSource.Replace("01234567#", "01234568#"), 101.0));
                Assert.IsFalse(AssetPathUtility.IsOfflineProbeCacheValid(PinnedSource, 131.0));
            }
            finally
            {
                sourceField.SetValue(null, originalSource);
                timeField.SetValue(null, originalTime);
            }
        }

        [Test]
        public void VersionMigration_TracksSourceChangesAtTheSamePackageVersion()
        {
            string first = StdIoVersionMigration.GetUpgradeIdentity("10.3.1-beta.1", PinnedSource);
            string second = StdIoVersionMigration.GetUpgradeIdentity("10.3.1-beta.1", PinnedSource.Replace("01234567#", "01234568#"));
            Assert.AreNotEqual(first, second);
            Assert.AreNotEqual("10.3.1-beta.1", first);
        }
    }
}
