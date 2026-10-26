using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    [Parallelizable(ParallelScope.None)]
    public class AssetDiskBudgetTests
    {
        private string project;

        [SetUp]
        public void SetUp()
        {
            project = Path.Combine(Path.GetTempPath(), "mcp_disk_budget_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(project);
            AssetDiskBudget.FreeBytesForTests = _ => 1000;
            AssetDiskBudget.AggregateBytesForTests = 20;
            AssetDiskBudget.HeadroomBytesForTests = 1;
        }

        [TearDown]
        public void TearDown()
        {
            PreparedArtifact.DeleteStageOverrideForTests = null;
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
            AssetDiskBudget.FreeBytesForTests = null;
            AssetDiskBudget.ProbeFreeBytesForTests = null;
            AssetDiskBudget.AggregateBytesForTests = null;
            AssetDiskBudget.HeadroomBytesForTests = null;
            Directory.Delete(project, true);
        }

        [Test]
        public void ParallelReservationsCannotExceedAggregateAndReleaseOnDispose()
        {
            using (var first = AssetDiskBudget.ReserveDownloadAsync(project, 12, CancellationToken.None).GetAwaiter().GetResult())
            {
                Assert.AreEqual(12, AssetDiskBudget.ReservedBytesForTests(project));
                Assert.ThrowsAsync<IOException>(async () => await AssetDiskBudget.ReserveDownloadAsync(project, 9, CancellationToken.None));
                Assert.AreEqual(12, AssetDiskBudget.ReservedBytesForTests(project));
            }
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
            using var next = AssetDiskBudget.ReserveDownloadAsync(project, 20, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(20, AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void UnknownLengthGrowsBeforeWritesAndRejectsExcess()
        {
            using var lease = AssetDiskBudget.ReserveDownloadAsync(project, -1, CancellationToken.None).GetAwaiter().GetResult();
            lease.Increase(13);
            lease.RecordWritten(13);
            Assert.Throws<IOException>(() => lease.Increase(8));
            Assert.AreEqual(13, AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void UnknownLengthStreamStopsBeforeExcessWriteAndDeletesPartialStage()
        {
            AssetDiskBudget.AggregateBytesForTests = 6;
            using var input = new MemoryStream(new byte[8]);
            Assert.ThrowsAsync<IOException>(async () => await DownloadedArtifact.ReadAsync(input, -1, 8, project, CancellationToken.None));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
            Assert.IsEmpty(Directory.GetFiles(project, "*.part", SearchOption.AllDirectories));
        }

        [Test]
        public void FreeSpaceAccountsForUnwrittenReservationsWithoutCountingWrittenBytesTwice()
        {
            long free = 20;
            AssetDiskBudget.FreeBytesForTests = _ => free;
            using var staged = AssetDiskBudget.ReserveDownloadAsync(project, 8, CancellationToken.None).GetAwaiter().GetResult();
            staged.RecordWritten(8);
            staged.CompleteTransfer();
            free -= 8;
            using var extracted = AssetDiskBudget.ReserveExtraction(project, 8, CancellationToken.None);
            Assert.AreEqual(16, AssetDiskBudget.ReservedBytesForTests(project));
            extracted.RecordWritten(8);
            free = 1; // A separate process consumed the remaining headroom.
            Assert.Throws<IOException>(() => extracted.Increase(1));
        }

        [Test]
        public void FreeSpaceProbeUsesTheExistingProjectDirectoryRatherThanItsFilesystemRoot()
        {
            string nestedProject = Path.Combine(project, "mounted", "Project");
            Directory.CreateDirectory(nestedProject);
            string observed = null;
            AssetDiskBudget.FreeBytesForTests = null;
            AssetDiskBudget.ProbeFreeBytesForTests = path =>
            {
                observed = path;
                return 1000;
            };
            using (var lease = AssetDiskBudget.ReserveDownloadAsync(nestedProject, 1, CancellationToken.None).GetAwaiter().GetResult())
            {
                Assert.AreNotEqual(Path.GetPathRoot(nestedProject), observed);
                Assert.AreEqual(Path.GetFullPath(nestedProject), observed.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            }
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(nestedProject));
        }

        [Test]
        public void MissingOutputFolderProbesItsNearestExistingDirectory()
        {
            string mountedFolder = Path.Combine(project, "mounted");
            Directory.CreateDirectory(mountedFolder);
            string output = Path.Combine(mountedFolder, "NewOutput", "Model");
            string observed = null;
            AssetDiskBudget.FreeBytesForTests = null;
            AssetDiskBudget.ProbeFreeBytesForTests = path =>
            {
                observed = path;
                return 1000;
            };
            using (var lease = AssetDiskBudget.ReserveExtraction(output, 1, CancellationToken.None))
                Assert.AreEqual(Path.GetFullPath(mountedFolder), observed.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(output));
        }

        [Test]
        public void FailedPlatformCapacityQueryDoesNotReserveBytes()
        {
            AssetDiskBudget.FreeBytesForTests = null;
            AssetDiskBudget.ProbeFreeBytesForTests = _ => throw new IOException("capacity unavailable");
            Assert.ThrowsAsync<IOException>(async () => await AssetDiskBudget.ReserveDownloadAsync(project, 1, CancellationToken.None));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void ExistingProjectCanUseTheActualPlatformCapacityProbe()
        {
            AssetDiskBudget.FreeBytesForTests = null;
            AssetDiskBudget.ProbeFreeBytesForTests = null;
            AssetDiskBudget.HeadroomBytesForTests = 0;
            using var lease = AssetDiskBudget.ReserveDownloadAsync(project, 1, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(1, AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void UnixMountSelectionUsesComponentBoundariesAndLongestMatch()
        {
            string[] mounts = { "/", "/mnt/data", "/mnt/data/models" };
            Assert.AreEqual(0, AssetDiskBudget.SelectUnixDriveIndex("/mnt/database/Project", mounts));
            Assert.AreEqual(0, AssetDiskBudget.SelectUnixDriveIndex("/mnt/data2/Project", mounts));
            Assert.AreEqual(1, AssetDiskBudget.SelectUnixDriveIndex("/mnt/data/Project", mounts));
            Assert.AreEqual(2, AssetDiskBudget.SelectUnixDriveIndex("/mnt/data/models/Project", mounts));
            Assert.AreEqual(2, AssetDiskBudget.SelectUnixDriveIndex("/mnt/data/models", mounts));
        }

        [Test]
        public void UnixMountSelectionKeepsCaseDistinctPathsOnTheirActualMount()
        {
            string[] mounts = { "/", "/mnt/data" };
            Assert.AreEqual(0, AssetDiskBudget.SelectUnixDriveIndex("/mnt/DATA/Project", mounts));
            Assert.AreEqual(1, AssetDiskBudget.SelectUnixDriveIndex("/mnt/data/Project", mounts));
        }

        [Test]
        public void MacMountSelectionRejectsAmbiguousNoncanonicalCasing()
        {
            string[] mounts = { "/", "/Volumes/Data" };
            Assert.Throws<IOException>(() => AssetDiskBudget.SelectUnixDriveIndex("/Volumes/data/Project", mounts, isMacOS: true));
            Assert.AreEqual(1, AssetDiskBudget.SelectUnixDriveIndex("/Volumes/Data/Project", mounts, isMacOS: true));
        }

        [Test]
        public void ThirdTransferWaitsAndCancellationDoesNotConsumeAQuotaSlot()
        {
            using var first = AssetDiskBudget.ReserveDownloadAsync(project, 0, CancellationToken.None).GetAwaiter().GetResult();
            using var second = AssetDiskBudget.ReserveDownloadAsync(project, 0, CancellationToken.None).GetAwaiter().GetResult();
            using var cts = new CancellationTokenSource();
            Task<AssetDiskBudget.Lease> waiting = AssetDiskBudget.ReserveDownloadAsync(project, 0, cts.Token);
            Assert.IsFalse(waiting.IsCompleted);
            cts.Cancel();
            Assert.Catch<OperationCanceledException>(() => waiting.GetAwaiter().GetResult());
            first.CompleteTransfer();
            using var next = AssetDiskBudget.ReserveDownloadAsync(project, 0, CancellationToken.None).GetAwaiter().GetResult();
        }

        [Test]
        public void NestedAssetsFolderCannotSplitStageAndExtractionBudget()
        {
            using var input = new MemoryStream(new byte[12]);
            using var artifact = DownloadedArtifact.ReadAsync(input, 12, 12, project, CancellationToken.None).GetAwaiter().GetResult();
            string zip = Path.Combine(project, "archive.zip");
            CreateZip(zip, "model.obj", new byte[9]);
            string destination = Path.Combine(project, "Assets", "Generated", "Assets", "Model");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractToWithInspection(zip, destination, null, null, null, project));
            Assert.IsFalse(File.Exists(Path.Combine(destination, "model.obj")));
            Assert.AreEqual(12, AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void StagedBytesRemainReservedUntilTheFileHandleDeletesThem()
        {
            using var input = new MemoryStream(new byte[8]);
            var artifact = DownloadedArtifact.ReadAsync(input, 8, 8, project, CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(8, AssetDiskBudget.ReservedBytesForTests(project));
            Assert.IsTrue(File.Exists(artifact.Path));
            artifact.Dispose();
            Assert.IsFalse(File.Exists(artifact.Path));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void PreparationStageClaimsItsOwnBytesAndReleasesThemAfterCleanup()
        {
            using var input = new HttpResult { Body = new byte[12] };
            var prepared = PreparedArtifact.CreateAsync(input, project, "model", "meshy", "obj", "obj", CancellationToken.None).GetAwaiter().GetResult();
            Assert.AreEqual(12, AssetDiskBudget.ReservedBytesForTests(project));
            prepared.Dispose();
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void RepeatedDisposeAfterFailedStageDeletionRetainsCreditUntilCleanupSucceeds()
        {
            var prepared = PreparedArtifact
                .CreateAsync(new HttpResult { Body = new byte[12] }, project, "model", "meshy", "obj", "obj", CancellationToken.None)
                .GetAwaiter()
                .GetResult();
            string stage = (string)typeof(PreparedArtifact).GetField("path", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(prepared);
            Assert.IsTrue(File.Exists(stage));
            try
            {
                PreparedArtifact.DeleteStageOverrideForTests = _ => { };
                prepared.Dispose();
                prepared.Dispose();
                Assert.IsTrue(File.Exists(stage));
                Assert.AreEqual(12, AssetDiskBudget.ReservedBytesForTests(project));
            }
            finally
            {
                PreparedArtifact.DeleteStageOverrideForTests = null;
                prepared.Dispose();
            }
            Assert.IsFalse(File.Exists(stage));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void PreparationRejectsCombinedDownloadAndCopyBeforeCreatingAnotherStage()
        {
            using var source = new MemoryStream(new byte[12]);
            var artifact = DownloadedArtifact.ReadAsync(source, 12, 12, project, CancellationToken.None).GetAwaiter().GetResult();
            string stagedPath = artifact.Path;
            var input = new HttpResult { DownloadedFile = artifact };
            Assert.ThrowsAsync<IOException>(async () =>
                await PreparedArtifact.CreateAsync(input, project, "model", "meshy", "obj", "obj", CancellationToken.None)
            );
            Assert.IsFalse(File.Exists(stagedPath));
            Assert.IsEmpty(Directory.GetFiles(project, "*.part", SearchOption.AllDirectories));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void ArchiveExpansionSharesStagingBudgetBeforeCreatingOutputs()
        {
            using var input = new MemoryStream(new byte[12]);
            using var artifact = DownloadedArtifact.ReadAsync(input, 12, 12, project, CancellationToken.None).GetAwaiter().GetResult();
            string zip = Path.Combine(project, "archive.zip");
            CreateZip(zip, "model.obj", new byte[9]);
            string destination = Path.Combine(project, "Assets", "Generated");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractToWithInspection(zip, destination, null, null, null, project));
            Assert.IsFalse(File.Exists(Path.Combine(destination, "model.obj")));
            Assert.AreEqual(12, AssetDiskBudget.ReservedBytesForTests(project));
        }

        [Test]
        public void FailedExtractionRollsBackFilesAndReleasesReservation()
        {
            string zip = Path.Combine(project, "archive.zip");
            using (var file = File.Create(zip))
            using (var archive = new ZipArchive(file, ZipArchiveMode.Create))
            {
                using (Stream output = archive.CreateEntry("model.obj").Open())
                    output.WriteByte(1);
                using (Stream output = archive.CreateEntry("../outside.obj").Open())
                    output.WriteByte(1);
            }
            string destination = Path.Combine(project, "Assets", "Generated");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractToWithInspection(zip, destination, null, null, null, project));
            Assert.IsFalse(File.Exists(Path.Combine(destination, "model.obj")));
            Assert.Zero(AssetDiskBudget.ReservedBytesForTests(project));
        }

        private static void CreateZip(string path, string name, byte[] bytes)
        {
            using var file = File.Create(path);
            using var archive = new ZipArchive(file, ZipArchiveMode.Create);
            using Stream output = archive.CreateEntry(name).Open();
            output.Write(bytes, 0, bytes.Length);
        }
    }
}
