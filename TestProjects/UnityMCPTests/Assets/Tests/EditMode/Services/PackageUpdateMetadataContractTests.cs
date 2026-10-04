using MCPForUnity.Editor.Services;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public class PackageUpdateMetadataContractTests
    {
        [TestCase("10.2.1-beta.2147483648", "10.2.0")]
        [TestCase("10.2.1-beta.1", "10.2.1-beta.2147483648")]
        [TestCase("9999999999.0.0", "10.2.0")]
        [TestCase("garbage", "10.2.0")]
        public void InvalidVersion_CannotParticipateInNewerComparison(string latest, string current)
        {
            Assert.IsFalse(new PackageUpdateService().IsNewerVersion(latest, current));
        }

        [TestCase("10.2.1-beta.6", "10.2.1-beta.5")]
        [TestCase("10.2.1", "10.2.1-beta.6")]
        [TestCase("v10.2.1-beta.6", "V10.2.1-beta.5")]
        [TestCase("10.2.1-beta.1", "10.2.1-alpha.9")]
        [TestCase("10.2.1-rc.1", "10.2.1-beta.9")]
        [TestCase("10.2.1-preview.1", "10.2.1-rc.9")]
        [TestCase("10.2.1-beta.2147483647", "10.2.1-beta.6")]
        public void EstablishedVersionFormatsAndRanks_RemainSupported(string latest, string current)
        {
            Assert.IsTrue(new PackageUpdateService().IsNewerVersion(latest, current));
            Assert.IsFalse(new PackageUpdateService().IsNewerVersion(current, latest));
        }

        [TestCase("garbage", true)]
        [TestCase(" ", true)]
        [TestCase("10.2.1-beta.2147483648", true)]
        public void InvalidFetchedMetadata_FailsInsteadOfReportingNoUpdate(string fetched, bool git)
        {
            var service = new MetadataService { Fetched = fetched };
            var result = service.FetchAndCompare("10.2.0", git, "main");
            Assert.IsFalse(result.CheckSucceeded);
            Assert.IsFalse(result.UpdateAvailable);
            Assert.IsNull(result.LatestVersion);
            StringAssert.Contains("invalid version metadata", result.Message);
            Assert.AreEqual(1, service.Fetches);
        }

        [TestCase(null, true)]
        [TestCase("", true)]
        public void MissingFetchedMetadata_KeepsOfflineFailureContract(string fetched, bool git)
        {
            var service = new MetadataService { Fetched = fetched };
            var result = service.FetchAndCompare("10.2.0", git, "main");
            Assert.IsFalse(result.CheckSucceeded);
            Assert.IsFalse(result.UpdateAvailable);
            Assert.IsNull(result.LatestVersion);
            Assert.AreEqual(git ? "Failed to check for updates (network issue or offline)"
                : "Failed to check for Asset Store updates (network issue or offline)", result.Message);
        }

        [TestCase(true)]
        public void ValidFetchedVersion_StillReportsAvailableUpdate(bool git)
        {
            var service = new MetadataService { Fetched = "10.2.2" };
            var result = service.FetchAndCompare("10.2.0", git, "main");
            Assert.IsTrue(result.CheckSucceeded);
            Assert.IsTrue(result.UpdateAvailable);
            Assert.AreEqual("10.2.2", result.LatestVersion);
            Assert.AreEqual(1, service.Fetches);
        }

        [TestCase(null)]
        [TestCase("garbage")]
        [TestCase("10.2.2")]
        public void LocalInstallation_DoesNotConsultAnyUpdateProvider(string fetched)
        {
            var service = new MetadataService { Fetched = fetched };
            var result = service.FetchAndCompare("10.2.0", false, "main");
            Assert.IsFalse(result.CheckSucceeded);
            Assert.IsFalse(result.UpdateAvailable);
            Assert.IsNull(result.LatestVersion);
            StringAssert.Contains("require a Git Package Manager installation", result.Message);
            Assert.AreEqual(0, service.Fetches);
        }

        private sealed class MetadataService : PackageUpdateService
        {
            public string Fetched;
            public int Fetches;
            protected override string FetchLatestVersionFromGitHub(string branch)
            { Fetches++; return Fetched; }
            protected override string FetchLatestVersionFromAssetStoreJson()
            { Fetches++; return Fetched; }
        }
    }
}
