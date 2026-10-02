using System;
using System.IO;
using System.IO.Compression;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ArchiveLimitSecurityTests
    {
        private string root;
        [SetUp] public void SetUp() => root = Path.Combine(Path.GetTempPath(), "mcp-zip-security-" + Guid.NewGuid().ToString("N"));
        [TearDown] public void TearDown() { if (Directory.Exists(root)) Directory.Delete(root, true); }

        private string MakeZip(int count, int bytes)
        {
            Directory.CreateDirectory(root);
            string zip = Path.Combine(root, "input.zip");
            using (var archive = ZipFile.Open(zip, ZipArchiveMode.Create))
                for (int i = 0; i < count; i++)
                    using (var output = archive.CreateEntry(i + ".png").Open())
                        output.Write(new byte[bytes], 0, bytes);
            return zip;
        }

        [TestCase(3, 10, 2, 100, 1000, 200)]
        [TestCase(1, 101, 2, 100, 1000, 200)]
        [TestCase(2, 60, 2, 100, 100, 200)]
        [TestCase(1, 10000, 2, 20000, 20000, 10)]
        public void ArchiveBudgetsRejectBeforeWriting(int count, int size, int maxEntries, int maxEntry, int maxTotal, int ratio)
        {
            string zip = MakeZip(count, size);
            string output = Path.Combine(root, "out");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, output, null,
                CancellationToken.None, maxEntries, maxEntry, maxTotal, ratio));
            Assert.IsFalse(Directory.Exists(output));
        }

        [Test]
        public void CanceledExtractionDoesNotCreateOutput()
        {
            string zip = MakeZip(1, 10);
            using var cts = new CancellationTokenSource();
            cts.Cancel();
            Assert.Throws<OperationCanceledException>(() => SafeZipExtractor.ExtractTo(zip, Path.Combine(root, "out"), null, cts.Token));
        }

        [Test]
        public void FailureRemovesOnlyFilesCreatedByThisExtraction()
        {
            string zip = MakeZip(2, 10);
            string output = Path.Combine(root, "out");
            Directory.CreateDirectory(output);
            File.WriteAllText(Path.Combine(output, "1.png"), "existing");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, output));
            Assert.IsFalse(File.Exists(Path.Combine(output, "0.png")));
            Assert.AreEqual("existing", File.ReadAllText(Path.Combine(output, "1.png")));
        }
    }
}
