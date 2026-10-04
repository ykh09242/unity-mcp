using System;
using System.Reflection;
using System.Collections.Generic;
using NUnit.Framework;
using UnityEditor;
using MCPForUnity.Editor.Services;
using MCPForUnity.Editor.Constants;

namespace MCPForUnityTests.Editor.Services
{
    public class PackageUpdateServiceTests
    {
        private PackageUpdateService _service;
        private readonly Dictionary<string, string> _originalPrefs = new Dictionary<string, string>();
        private const string TestLastCheckDateKey = EditorPrefKeys.LastUpdateCheck + ".ykh09242";
        private const string TestCachedVersionKey = EditorPrefKeys.LatestKnownVersion + ".ykh09242";
        private const string TestAssetStoreLastCheckDateKey = EditorPrefKeys.LastAssetStoreUpdateCheck + ".ykh09242";
        private const string TestAssetStoreCachedVersionKey = EditorPrefKeys.LatestKnownAssetStoreVersion + ".ykh09242";

        [SetUp]
        public void SetUp()
        {
            _service = new TestablePackageUpdateService();

            _originalPrefs.Clear();
            foreach (string key in CacheKeys())
                _originalPrefs[key] = EditorPrefs.HasKey(key) ? EditorPrefs.GetString(key) : null;

            // Clean up any existing test data
            CleanupEditorPrefs();
        }

        private static IEnumerable<string> CacheKeys() => new[]
        {
            TestLastCheckDateKey, TestCachedVersionKey,
            TestLastCheckDateKey + ".beta", TestCachedVersionKey + ".beta",
            TestAssetStoreLastCheckDateKey, TestAssetStoreCachedVersionKey
        };

        [TearDown]
        public void TearDown()
        {
            // Clean up test data
            CleanupEditorPrefs();
            foreach (var pref in _originalPrefs)
                if (pref.Value != null) EditorPrefs.SetString(pref.Key, pref.Value);
        }

        private void CleanupEditorPrefs()
        {
            foreach (string key in CacheKeys()) EditorPrefs.DeleteKey(key);
        }

        [Test]
        public void IsNewerVersion_ReturnsTrue_WhenMajorVersionIsNewer()
        {
            bool result = _service.IsNewerVersion("2.0.0", "1.0.0");
            Assert.IsTrue(result, "2.0.0 should be newer than 1.0.0");
        }

        [Test]
        public void IsNewerVersion_ReturnsTrue_WhenMinorVersionIsNewer()
        {
            bool result = _service.IsNewerVersion("1.2.0", "1.1.0");
            Assert.IsTrue(result, "1.2.0 should be newer than 1.1.0");
        }

        [Test]
        public void IsNewerVersion_ReturnsTrue_WhenPatchVersionIsNewer()
        {
            bool result = _service.IsNewerVersion("1.0.2", "1.0.1");
            Assert.IsTrue(result, "1.0.2 should be newer than 1.0.1");
        }

        [Test]
        public void IsNewerVersion_ReturnsFalse_WhenVersionsAreEqual()
        {
            bool result = _service.IsNewerVersion("1.0.0", "1.0.0");
            Assert.IsFalse(result, "Same versions should return false");
        }

        [Test]
        public void IsNewerVersion_ReturnsFalse_WhenVersionIsOlder()
        {
            bool result = _service.IsNewerVersion("1.0.0", "2.0.0");
            Assert.IsFalse(result, "1.0.0 should not be newer than 2.0.0");
        }

        [Test]
        public void IsNewerVersion_HandlesVersionPrefix_v()
        {
            bool result = _service.IsNewerVersion("v2.0.0", "v1.0.0");
            Assert.IsTrue(result, "Should handle 'v' prefix correctly");
        }

        [Test]
        public void IsNewerVersion_HandlesVersionPrefix_V()
        {
            bool result = _service.IsNewerVersion("V2.0.0", "V1.0.0");
            Assert.IsTrue(result, "Should handle 'V' prefix correctly");
        }

        [Test]
        public void IsNewerVersion_HandlesMixedPrefixes()
        {
            bool result = _service.IsNewerVersion("v2.0.0", "1.0.0");
            Assert.IsTrue(result, "Should handle mixed prefixes correctly");
        }

        [Test]
        public void IsNewerVersion_ComparesCorrectly_WhenMajorDiffers()
        {
            bool result1 = _service.IsNewerVersion("10.0.0", "9.0.0");
            bool result2 = _service.IsNewerVersion("2.0.0", "10.0.0");

            Assert.IsTrue(result1, "10.0.0 should be newer than 9.0.0");
            Assert.IsFalse(result2, "2.0.0 should not be newer than 10.0.0");
        }

        [Test]
        public void IsNewerVersion_ReturnsFalse_OnInvalidVersionFormat()
        {
            // Service should handle errors gracefully
            bool result = _service.IsNewerVersion("invalid", "1.0.0");
            Assert.IsFalse(result, "Should return false for invalid version format");
        }

        [Test]
        public void CheckForUpdate_ReturnsCachedVersion_WhenCacheIsValid()
        {
            // Arrange: Set up valid cache
            string today = DateTime.Now.ToString("yyyy-MM-dd");
            string cachedVersion = "5.5.5";
            EditorPrefs.SetString(TestLastCheckDateKey, today);
            EditorPrefs.SetString(TestCachedVersionKey, cachedVersion);

            // Act
            var result = _service.CheckForUpdate("5.0.0");

            // Assert
            Assert.IsTrue(result.CheckSucceeded, "Check should succeed with valid cache");
            Assert.AreEqual(cachedVersion, result.LatestVersion, "Should return cached version");
            Assert.IsTrue(result.UpdateAvailable, "Update should be available (5.5.5 > 5.0.0)");
        }

        [Test]
        public void CheckForUpdate_DetectsUpdateAvailable_WhenNewerVersionCached()
        {
            // Arrange
            string today = DateTime.Now.ToString("yyyy-MM-dd");
            EditorPrefs.SetString(TestLastCheckDateKey, today);
            EditorPrefs.SetString(TestCachedVersionKey, "6.0.0");

            // Act
            var result = _service.CheckForUpdate("5.0.0");

            // Assert
            Assert.IsTrue(result.UpdateAvailable, "Should detect update is available");
            Assert.AreEqual("6.0.0", result.LatestVersion);
        }

        [Test]
        public void CheckForUpdate_DetectsNoUpdate_WhenVersionsMatch()
        {
            // Arrange
            string today = DateTime.Now.ToString("yyyy-MM-dd");
            EditorPrefs.SetString(TestLastCheckDateKey, today);
            EditorPrefs.SetString(TestCachedVersionKey, "5.0.0");

            // Act
            var result = _service.CheckForUpdate("5.0.0");

            // Assert
            Assert.IsFalse(result.UpdateAvailable, "Should detect no update needed");
            Assert.AreEqual("5.0.0", result.LatestVersion);
        }

        [Test]
        public void CheckForUpdate_DetectsNoUpdate_WhenCurrentVersionIsNewer()
        {
            // Arrange
            string today = DateTime.Now.ToString("yyyy-MM-dd");
            EditorPrefs.SetString(TestLastCheckDateKey, today);
            EditorPrefs.SetString(TestCachedVersionKey, "5.0.0");

            // Act
            var result = _service.CheckForUpdate("6.0.0");

            // Assert
            Assert.IsFalse(result.UpdateAvailable, "Should detect no update when current is newer");
            Assert.AreEqual("5.0.0", result.LatestVersion);
        }

        [Test]
        public void CheckForUpdate_IgnoresExpiredCache_AndAttemptsFreshFetch()
        {
            // Arrange: Set cache from yesterday (expired)
            string yesterday = DateTime.Now.AddDays(-1).ToString("yyyy-MM-dd");
            string cachedVersion = "4.0.0";
            EditorPrefs.SetString(TestLastCheckDateKey, yesterday);
            EditorPrefs.SetString(TestCachedVersionKey, cachedVersion);

            // Act
            var result = _service.CheckForUpdate("5.0.0");

            // Assert
            Assert.IsNotNull(result, "Should return a result");
            
            // If the check succeeded (network available), verify it didn't use the expired cache
            if (result.CheckSucceeded)
            {
                Assert.AreNotEqual(cachedVersion, result.LatestVersion, 
                    "Should not return expired cached version when fresh fetch succeeds");
                Assert.IsNotNull(result.LatestVersion, "Should have fetched a new version");
            }
            else
            {
                // If offline, check should fail (not succeed with cached data)
                Assert.IsFalse(result.UpdateAvailable, 
                    "Should not report update available when fetch fails and cache is expired");
            }
        }

        [Test]
        public void CheckForUpdate_IgnoresAssetStoreCache_ForLocalInstall()
        {
            // Arrange: Set up valid Asset Store cache
            string today = DateTime.Now.ToString("yyyy-MM-dd");
            string cachedVersion = "9.0.1";
            EditorPrefs.SetString(TestAssetStoreLastCheckDateKey, today);
            EditorPrefs.SetString(TestAssetStoreCachedVersionKey, cachedVersion);

            var mockService = new TestablePackageUpdateService
            {
                IsGitInstallationResult = false,
                AssetStoreFetchResult = "9.9.9"
            };

            // Act
            var result = mockService.CheckForUpdate("9.0.0");

            // Assert
            Assert.IsFalse(result.CheckSucceeded);
            Assert.IsNull(result.LatestVersion);
            Assert.IsFalse(result.UpdateAvailable);
            Assert.IsFalse(mockService.AssetStoreFetchCalled);
            Assert.IsFalse(mockService.GitFetchCalled);
            StringAssert.Contains("require a Git Package Manager installation", result.Message);
        }

        [Test]
        public void CheckForUpdate_DoesNotFetchAssetStoreJson_WhenCacheExpired()
        {
            // Arrange: Set expired Asset Store cache and a valid Git cache to ensure separation
            string yesterday = DateTime.Now.AddDays(-1).ToString("yyyy-MM-dd");
            EditorPrefs.SetString(TestAssetStoreLastCheckDateKey, yesterday);
            EditorPrefs.SetString(TestAssetStoreCachedVersionKey, "9.0.0");
            EditorPrefs.SetString(TestLastCheckDateKey, DateTime.Now.ToString("yyyy-MM-dd"));
            EditorPrefs.SetString(TestCachedVersionKey, "99.0.0");

            var mockService = new TestablePackageUpdateService
            {
                IsGitInstallationResult = false,
                AssetStoreFetchResult = "9.1.0"
            };

            // Act
            var result = mockService.CheckForUpdate("9.0.0");

            // Assert
            Assert.IsFalse(result.CheckSucceeded);
            Assert.IsNull(result.LatestVersion);
            Assert.IsFalse(mockService.AssetStoreFetchCalled);
            Assert.IsFalse(mockService.GitFetchCalled);
        }

        [Test]
        public void CheckForUpdate_ReturnsAssetStoreFailureMessage_WhenFetchFails()
        {
            // Arrange
            var mockService = new TestablePackageUpdateService
            {
                IsGitInstallationResult = false,
                AssetStoreFetchResult = null
            };

            // Act
            var result = mockService.CheckForUpdate("9.0.0");

            // Assert
            Assert.IsFalse(result.CheckSucceeded, "Check should fail when Asset Store fetch fails");
            Assert.IsFalse(result.UpdateAvailable, "No update should be reported when fetch fails");
            StringAssert.Contains("Update local copies manually from https://github.com/ykh09242/unity-mcp", result.Message);
            Assert.IsNull(result.LatestVersion, "Latest version should be null when fetch fails");
        }

        [Test]
        public void ClearCache_RemovesAllCachedData()
        {
            // Arrange: Set up cache
            EditorPrefs.SetString(TestLastCheckDateKey, DateTime.Now.ToString("yyyy-MM-dd"));
            EditorPrefs.SetString(TestCachedVersionKey, "5.0.0");
            EditorPrefs.SetString(TestAssetStoreLastCheckDateKey, DateTime.Now.ToString("yyyy-MM-dd"));
            EditorPrefs.SetString(TestAssetStoreCachedVersionKey, "9.0.0");

            // Verify cache exists
            Assert.IsTrue(EditorPrefs.HasKey(TestLastCheckDateKey), "Cache should exist before clearing");
            Assert.IsTrue(EditorPrefs.HasKey(TestCachedVersionKey), "Cache should exist before clearing");
            Assert.IsTrue(EditorPrefs.HasKey(TestAssetStoreLastCheckDateKey), "Asset Store cache should exist before clearing");
            Assert.IsTrue(EditorPrefs.HasKey(TestAssetStoreCachedVersionKey), "Asset Store cache should exist before clearing");

            // Act
            _service.ClearCache();

            // Assert
            Assert.IsFalse(EditorPrefs.HasKey(TestLastCheckDateKey), "Date cache should be cleared");
            Assert.IsFalse(EditorPrefs.HasKey(TestCachedVersionKey), "Version cache should be cleared");
            Assert.IsFalse(EditorPrefs.HasKey(TestAssetStoreLastCheckDateKey), "Asset Store date cache should be cleared");
            Assert.IsFalse(EditorPrefs.HasKey(TestAssetStoreCachedVersionKey), "Asset Store version cache should be cleared");
        }

        [Test]
        public void ClearCache_DoesNotThrow_WhenNoCacheExists()
        {
            // Ensure no cache exists
            CleanupEditorPrefs();

            // Act & Assert - should not throw
            Assert.DoesNotThrow(() => _service.ClearCache(), "Should not throw when clearing non-existent cache");
        }

        [Test]
        public void UpdateMetadataUrls_TargetOnlyTheFork()
        {
            var flags = BindingFlags.Static | BindingFlags.NonPublic;
            Assert.AreEqual("https://raw.githubusercontent.com/ykh09242/unity-mcp/main/MCPForUnity/package.json",
                typeof(PackageUpdateService).GetField("MainPackageJsonUrl", flags).GetRawConstantValue());
            Assert.AreEqual("https://raw.githubusercontent.com/ykh09242/unity-mcp/beta/MCPForUnity/package.json",
                typeof(PackageUpdateService).GetField("BetaPackageJsonUrl", flags).GetRawConstantValue());
        }

        [TestCase("main")]
        [TestCase("beta")]
        public void UpdateCache_IsIsolatedFromUpstreamAndOtherChannel(string branch)
        {
            string suffix = branch == "beta" ? ".beta" : string.Empty;
            string upstreamDate = EditorPrefKeys.LastUpdateCheck + suffix;
            string upstreamVersion = EditorPrefKeys.LatestKnownVersion + suffix;
            bool hadDate = EditorPrefs.HasKey(upstreamDate);
            bool hadVersion = EditorPrefs.HasKey(upstreamVersion);
            string originalDate = EditorPrefs.GetString(upstreamDate, "");
            string originalVersion = EditorPrefs.GetString(upstreamVersion, "");
            try
            {
                EditorPrefs.SetString(upstreamDate, DateTime.Now.ToString("yyyy-MM-dd"));
                EditorPrefs.SetString(upstreamVersion, "99.0.0");
                var service = new TestablePackageUpdateService { UpdateBranch = branch, GitFetchResult = "10.3.2" };
                var result = service.CheckForUpdate("10.3.1-beta.1");
                Assert.IsTrue(service.GitFetchCalled);
                Assert.AreEqual("10.3.2", result.LatestVersion);
                Assert.AreEqual("10.3.2", EditorPrefs.GetString(TestCachedVersionKey + suffix));
                Assert.AreEqual("99.0.0", EditorPrefs.GetString(upstreamVersion));
                Assert.IsFalse(EditorPrefs.HasKey(TestCachedVersionKey + (branch == "beta" ? "" : ".beta")));
            }
            finally
            {
                if (hadDate) EditorPrefs.SetString(upstreamDate, originalDate); else EditorPrefs.DeleteKey(upstreamDate);
                if (hadVersion) EditorPrefs.SetString(upstreamVersion, originalVersion); else EditorPrefs.DeleteKey(upstreamVersion);
                EditorPrefs.DeleteKey(TestLastCheckDateKey + ".beta");
                EditorPrefs.DeleteKey(TestCachedVersionKey + ".beta");
            }
        }
    }

    /// <summary>
    /// Testable implementation that allows forcing install type and fetch results.
    /// </summary>
    internal class TestablePackageUpdateService : PackageUpdateService
    {
        public bool IsGitInstallationResult { get; set; } = true;
        public string GitFetchResult { get; set; }
        public string AssetStoreFetchResult { get; set; }
        public bool GitFetchCalled { get; private set; }
        public bool AssetStoreFetchCalled { get; private set; }
        public string UpdateBranch { get; set; } = "main";

        public override string GetGitUpdateBranch(string currentVersion) => UpdateBranch;

        public override bool IsGitInstallation()
        {
            return IsGitInstallationResult;
        }

        protected override string FetchLatestVersionFromGitHub(string branch)
        {
            GitFetchCalled = true;
            return GitFetchResult;
        }

        protected override string FetchLatestVersionFromAssetStoreJson()
        {
            AssetStoreFetchCalled = true;
            return AssetStoreFetchResult;
        }
    }
}
