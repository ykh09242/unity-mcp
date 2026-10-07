using System;
using System.Collections.Generic;
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

        [SetUp]
        public void SetUp() => root = Path.Combine(Path.GetTempPath(), "mcp-zip-security-" + Guid.NewGuid().ToString("N"));

        [TearDown]
        public void TearDown()
        {
            if (Directory.Exists(root))
                Directory.Delete(root, true);
        }

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

        private string MakeDeclaredSizeZip(params uint[] declaredSizes)
        {
            Directory.CreateDirectory(root);
            using var stream = new MemoryStream();
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Create, true))
                for (int i = 0; i < declaredSizes.Length; i++)
                    archive.CreateEntry(i + ".cs");
            byte[] bytes = stream.ToArray();
            int end = bytes.Length - 22;
            int central = (int)BitConverter.ToUInt32(bytes, end + 16);
            foreach (uint size in declaredSizes)
            {
                Assert.AreEqual(0x02014b50u, BitConverter.ToUInt32(bytes, central));
                // Advertise matching compressed/uncompressed sizes to isolate size budgets.
                // These disallowed entries are skipped before opening their empty bodies.
                Buffer.BlockCopy(BitConverter.GetBytes(size), 0, bytes, central + 20, 4);
                Buffer.BlockCopy(BitConverter.GetBytes(size), 0, bytes, central + 24, 4);
                central +=
                    46 + BitConverter.ToUInt16(bytes, central + 28) + BitConverter.ToUInt16(bytes, central + 30) + BitConverter.ToUInt16(bytes, central + 32);
            }
            string zip = Path.Combine(root, "declared.zip");
            File.WriteAllBytes(zip, bytes);
            return zip;
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DefaultPerEntryBudget_Accepts512MiBAndRejectsNextByte(bool exceeds)
        {
            string zip = MakeDeclaredSizeZip(512u * 1024 * 1024 + (exceeds ? 1u : 0u));
            string output = Path.Combine(root, "out");
            var allowed = new HashSet<string> { ".obj" };
            if (exceeds)
                Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, output, allowed));
            else
                Assert.DoesNotThrow(() => SafeZipExtractor.ExtractTo(zip, output, allowed));
            Assert.IsFalse(Directory.Exists(output), "metadata tests must not open payloads or create output");
        }

        [TestCase(false)]
        [TestCase(true)]
        public void DefaultTotalBudget_Accepts2GiBAndRejectsNextByte(bool exceeds)
        {
            // Entries stay within the former per-entry budget, so the aggregate gate is isolated.
            var sizes = new List<uint>();
            for (int i = 0; i < 8; i++)
                sizes.Add(256u * 1024 * 1024);
            if (exceeds)
                sizes.Add(1);
            string zip = MakeDeclaredSizeZip(sizes.ToArray());
            string output = Path.Combine(root, "out");
            var allowed = new HashSet<string> { ".obj" };
            if (exceeds)
                Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, output, allowed));
            else
                Assert.DoesNotThrow(() => SafeZipExtractor.ExtractTo(zip, output, allowed));
            Assert.IsFalse(Directory.Exists(output), "aggregate arithmetic must not allocate the declared payloads");
        }

        [Test]
        public void ActualSmallArchiveAtEntryAndTotalLimits_ExtractsNormally()
        {
            string zip = MakeZip(2, 10);
            string output = Path.Combine(root, "out");
            SafeZipExtractor.ExtractTo(zip, output, null, CancellationToken.None, 2, 10, 20, 200);
            Assert.AreEqual(10, new FileInfo(Path.Combine(output, "0.png")).Length);
            Assert.AreEqual(10, new FileInfo(Path.Combine(output, "1.png")).Length);
        }

        [TestCase(3, 10, 2, 100, 1000, 200)]
        [TestCase(1, 101, 2, 100, 1000, 200)]
        [TestCase(2, 60, 2, 100, 100, 200)]
        [TestCase(1, 10000, 2, 20000, 20000, 10)]
        public void ArchiveBudgetsRejectBeforeWriting(int count, int size, int maxEntries, int maxEntry, int maxTotal, int ratio)
        {
            string zip = MakeZip(count, size);
            string output = Path.Combine(root, "out");
            Assert.Throws<IOException>(() => SafeZipExtractor.ExtractTo(zip, output, null, CancellationToken.None, maxEntries, maxEntry, maxTotal, ratio));
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
