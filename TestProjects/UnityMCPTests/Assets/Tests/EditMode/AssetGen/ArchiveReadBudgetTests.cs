using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ArchiveReadBudgetTests
    {
        private static readonly HashSet<string> Allowed = new(StringComparer.OrdinalIgnoreCase) { ".gltf", ".bin" };

        private static byte[] SharedBufferArchive(int models, int imagesPerModel, int prefixBytes)
        {
            byte[] image = AssetAdmissionTests.Png(1, 1);
            var images = new JArray();
            for (int i = 0; i < imagesPerModel; i++)
                images.Add(new JObject { ["bufferView"] = 0, ["mimeType"] = "image/png" });
            var document = new JObject
            {
                ["asset"] = new JObject { ["version"] = "2.0" },
                ["buffers"] = new JArray(new JObject { ["uri"] = "stored.bin", ["byteLength"] = prefixBytes + image.Length }),
                ["bufferViews"] = new JArray(
                    new JObject
                    {
                        ["buffer"] = 0,
                        ["byteOffset"] = prefixBytes,
                        ["byteLength"] = image.Length,
                    }
                ),
                ["images"] = images,
            };
            var entries = new List<(string name, byte[] data)>();
            for (int i = 0; i < models; i++)
                entries.Add(("model" + i + ".gltf", Encoding.UTF8.GetBytes(document.ToString(Newtonsoft.Json.Formatting.None))));
            var buffer = new byte[prefixBytes + image.Length];
            Buffer.BlockCopy(image, 0, buffer, prefixBytes, image.Length);
            entries.Add(("stored.bin", buffer));
            return StoredArchive(entries);
        }

        // Unity's Mono ZIP writer can deflate even CompressionLevel.NoCompression.
        // Explicit method-zero records make physical source reads observable without
        // substituting a fake entry stream for the production ZipArchive reader.
        private static byte[] StoredArchive(List<(string name, byte[] data)> entries)
        {
            using var bytes = new MemoryStream();
            using var directory = new MemoryStream();
            using var writer = new BinaryWriter(bytes);
            using var central = new BinaryWriter(directory);
            foreach (var entry in entries)
            {
                byte[] name = Encoding.UTF8.GetBytes(entry.name);
                uint crc = uint.MaxValue;
                foreach (byte value in entry.data)
                {
                    crc ^= value;
                    for (int bit = 0; bit < 8; bit++)
                        crc = (crc >> 1) ^ ((crc & 1) != 0 ? 0xedb88320u : 0);
                }
                crc = ~crc;
                uint localOffset = (uint)bytes.Position;
                void Header(BinaryWriter target)
                {
                    target.Write((ushort)20); // Version needed.
                    target.Write((ushort)0); // Flags.
                    target.Write((ushort)0); // Stored compression method.
                    target.Write(0u); // Timestamp.
                    target.Write(crc);
                    target.Write((uint)entry.data.Length);
                    target.Write((uint)entry.data.Length);
                    target.Write((ushort)name.Length);
                    target.Write((ushort)0); // Extra field length.
                }
                writer.Write(0x04034b50u);
                Header(writer);
                writer.Write(name);
                writer.Write(entry.data);

                central.Write(0x02014b50u);
                central.Write((ushort)20); // Version made by.
                Header(central);
                central.Write((ushort)0); // Comment length.
                central.Write((ushort)0); // Disk start.
                central.Write((ushort)0); // Internal attributes.
                central.Write(0u); // External attributes.
                central.Write(localOffset);
                central.Write(name);
            }
            uint directoryStart = (uint)bytes.Position;
            writer.Write(directory.ToArray());
            writer.Write(0x06054b50u);
            writer.Write(0u); // Disk numbers.
            writer.Write((ushort)entries.Count);
            writer.Write((ushort)entries.Count);
            writer.Write((uint)directory.Length);
            writer.Write(directoryStart);
            writer.Write((ushort)0); // Archive comment length.
            byte[] result = bytes.ToArray();
            // Assert both local and central framing so this fixture can never silently
            // become compressible and invalidate the physical read/cancellation evidence.
            Assert.AreEqual(0, BitConverter.ToUInt16(result, 8));
            Assert.AreEqual(0, BitConverter.ToUInt16(result, (int)directoryStart + 10));
            using var verify = new ZipArchive(new MemoryStream(result), ZipArchiveMode.Read);
            foreach (ZipArchiveEntry entry in verify.Entries)
                Assert.AreEqual(entry.Length, entry.CompressedLength);
            return result;
        }

        [TestCase(1, 16)]
        [TestCase(2, 1)]
        public void RepeatedBufferViewsShareReadBudgetAcrossEntryOpensAndModels(int models, int images)
        {
            const int prefix = 16384;
            const long limit = 25000;
            using var bytes = new CountingInput(SharedBufferArchive(models, images, prefix));
            using var archive = new ZipArchive(bytes, ZipArchiveMode.Read, true);
            _ = archive.Entries.Count;
            bytes.BytesRead = 0;
            var error = Assert.Throws<InvalidDataException>(() => ArchiveAssetAdmission.Validate(archive, Allowed, maxReadBytes: limit));
            StringAssert.Contains("aggregate read budget", error.Message);
            Assert.Greater(bytes.BytesRead, prefix, "The first image's prefix must run before another view is rejected; the signature itself is buffered.");
            Assert.Less(bytes.BytesRead, limit, "An unaffordable repeated prefix must be rejected before any of it is decompressed.");
        }

        [Test]
        public void UnaffordableInitialPrefixFailsBeforeReadingPayload()
        {
            const int prefix = 16384;
            using var bytes = new CountingInput(SharedBufferArchive(1, 1, prefix));
            using var archive = new ZipArchive(bytes, ZipArchiveMode.Read, true);
            _ = archive.Entries.Count;
            bytes.BytesRead = 0;
            var error = Assert.Throws<InvalidDataException>(() => ArchiveAssetAdmission.Validate(archive, Allowed, maxReadBytes: prefix - 1));
            StringAssert.Contains("aggregate read budget", error.Message);
            Assert.Greater(bytes.BytesRead, 0, "Model JSON and entry headers must be inspected before rejecting the declared image offset.");
            Assert.Less(bytes.BytesRead, 4096, "An unaffordable initial prefix must not be partially decompressed.");
        }

        [Test]
        public void ProductionBudgetRejectsRepeatedTailImagesWithoutReadingEveryView()
        {
            using var bytes = new CountingInput(SharedBufferArchive(1, 512, 256 * 1024));
            using var archive = new ZipArchive(bytes, ZipArchiveMode.Read, true);
            _ = archive.Entries.Count;
            bytes.BytesRead = 0;
            var error = Assert.Throws<InvalidDataException>(() => ArchiveAssetAdmission.Validate(archive, Allowed));
            StringAssert.Contains("aggregate read budget", error.Message);
            Assert.Less(bytes.BytesRead, 66L * 1024 * 1024, "Production admission must stop before the roughly 128 MiB complete rescan.");
        }

        [Test]
        public void NormalSmallSharedBufferImagesRemainAdmissible()
        {
            using var bytes = new MemoryStream(SharedBufferArchive(2, 32, 64));
            using var archive = new ZipArchive(bytes, ZipArchiveMode.Read);
            Assert.DoesNotThrow(() => ArchiveAssetAdmission.Validate(archive, Allowed));
        }

        [Test]
        public void CancellationStopsDuringPrefixDecompressionAndKeepsArchiveReusable()
        {
            byte[] original = SharedBufferArchive(1, 16, 65536);
            using var cancel = new CancellationTokenSource();
            using var bytes = new CountingInput(original);
            using (var archive = new ZipArchive(bytes, ZipArchiveMode.Read, true))
            {
                _ = archive.Entries.Count;
                bytes.BytesRead = 0;
                bytes.AfterRead = () =>
                {
                    if (bytes.BytesRead >= 12000)
                        cancel.Cancel();
                };
                Assert.Throws<OperationCanceledException>(() => ArchiveAssetAdmission.Validate(archive, Allowed, cancel.Token));
                Assert.Less(bytes.BytesRead, 24000, "Cancellation must interrupt the prefix, not wait for the whole image or model.");
                bytes.AfterRead = null;
                Assert.DoesNotThrow(() => ArchiveAssetAdmission.Validate(archive, Allowed));
            }
            CollectionAssert.AreEqual(original, bytes.ToArray());
        }

        [TestCase(false)]
        [TestCase(true)]
        public void CanceledInspectionOrExtractionPublishesNoFilesAndPreservesSource(bool duringExtraction)
        {
            string root = Path.Combine(Path.GetTempPath(), "archive_read_budget_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string zip = Path.Combine(root, "source.zip"),
                output = Path.Combine(root, "output");
            byte[] original = SharedBufferArchive(1, 1, 64);
            File.WriteAllBytes(zip, original);
            Directory.CreateDirectory(output);
            string preserved = Path.Combine(output, "existing.txt");
            File.WriteAllText(preserved, "preserve");
            using var cancel = new CancellationTokenSource();
            int directories = 0;
            try
            {
                Assert.Throws<OperationCanceledException>(() =>
                    SafeZipExtractor.ExtractToWithInspection(
                        zip,
                        output,
                        Allowed,
                        archive =>
                        {
                            ArchiveAssetAdmission.Validate(archive, Allowed, cancel.Token);
                            if (!duringExtraction)
                                cancel.Cancel();
                        },
                        directory =>
                        {
                            Directory.CreateDirectory(directory);
                            if (++directories == 2)
                                cancel.Cancel();
                        },
                        null,
                        cancel.Token
                    )
                );
                CollectionAssert.AreEqual(new[] { preserved }, Directory.GetFiles(output, "*", SearchOption.AllDirectories));
                Assert.AreEqual("preserve", File.ReadAllText(preserved));
                CollectionAssert.AreEqual(original, File.ReadAllBytes(zip));
                Assert.Zero(AssetDiskBudget.ReservedBytesForTests(output));
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void PreCanceledModelImportPublishesNoAsset()
        {
            using var cancel = new CancellationTokenSource();
            cancel.Cancel();
            var job = new AssetGenJob();
            ModelImportPipeline.ImportInto(job, null, false, cancel.Token);
            Assert.AreEqual(AssetGenJobState.Canceled, job.State);
            Assert.IsNull(job.AssetPath);
            Assert.IsNull(job.AssetGuid);
        }

        private sealed class CountingInput : Stream
        {
            private readonly MemoryStream source;
            internal long BytesRead;
            internal Action AfterRead;

            internal CountingInput(byte[] bytes) => source = new MemoryStream(bytes, false);

            internal byte[] ToArray() => source.ToArray();

            public override bool CanRead => true;
            public override bool CanSeek => true;
            public override bool CanWrite => false;
            public override long Length => source.Length;
            public override long Position
            {
                get => source.Position;
                set => source.Position = value;
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                int read = source.Read(buffer, offset, count);
                BytesRead += read;
                AfterRead?.Invoke();
                return read;
            }

            public override long Seek(long offset, SeekOrigin origin) => source.Seek(offset, origin);

            public override void Flush() { }

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

            protected override void Dispose(bool disposing)
            {
                if (disposing)
                    source.Dispose();
                base.Dispose(disposing);
            }
        }
    }
}
