using System;
using System.IO;
using System.IO.Compression;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class ZipMetadataSecurityTests
    {
        private static byte[] Archive(int count = 1)
        {
            using var stream = new MemoryStream();
            using (var zip = new ZipArchive(stream, ZipArchiveMode.Create, true))
                for (int i = 0; i < count; i++)
                    zip.CreateEntry(i + ".png");
            return stream.ToArray();
        }

        private static void Set(byte[] data, int offset, byte[] value) => Buffer.BlockCopy(value, 0, data, offset, value.Length);

        [TestCase("count")]
        [TestCase("size")]
        [TestCase("hidden")]
        [TestCase("offset")]
        [TestCase("truncated")]
        public void RejectsUnboundedOrInconsistentMetadata(string kind)
        {
            byte[] data = Archive(2);
            int end = data.Length - 22;
            switch (kind)
            {
                case "count":
                    Set(data, end + 8, BitConverter.GetBytes((ushort)5000));
                    Set(data, end + 10, BitConverter.GetBytes((ushort)5000));
                    break;
                case "size":
                    Set(data, end + 12, BitConverter.GetBytes((uint)ZipMetadataPreflight.MaxDirectoryBytes + 1));
                    break;
                case "hidden":
                    Set(data, end + 8, BitConverter.GetBytes((ushort)1));
                    Set(data, end + 10, BitConverter.GetBytes((ushort)1));
                    break;
                case "offset":
                    Set(data, end + 16, BitConverter.GetBytes(uint.MaxValue - 1));
                    break;
                case "truncated":
                    Array.Resize(ref data, data.Length - 1);
                    break;
            }
            using var stream = new MemoryStream(data);
            Assert.Throws<IOException>(() => ZipMetadataPreflight.Validate(stream, 4096, CancellationToken.None));
        }

        private static byte[] Zip64(ulong count = 1, bool inconsistent = false)
        {
            byte[] regular = Archive();
            int end = regular.Length - 22;
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            writer.Write(regular, 0, end);
            writer.Write(0x06064b50u);
            writer.Write(44UL);
            writer.Write((ushort)45);
            writer.Write((ushort)45);
            writer.Write(0u);
            writer.Write(0u);
            writer.Write(count);
            writer.Write(count);
            writer.Write((ulong)BitConverter.ToUInt32(regular, end + 12));
            writer.Write((ulong)BitConverter.ToUInt32(regular, end + 16));
            writer.Write(0x07064b50u);
            writer.Write(0u);
            writer.Write((ulong)end);
            writer.Write(1u);
            Set(regular, end + 8, BitConverter.GetBytes(ushort.MaxValue));
            Set(regular, end + 10, BitConverter.GetBytes(inconsistent ? (ushort)2 : ushort.MaxValue));
            Set(regular, end + 12, BitConverter.GetBytes(uint.MaxValue));
            Set(regular, end + 16, BitConverter.GetBytes(uint.MaxValue));
            writer.Write(regular, end, 22);
            return stream.ToArray();
        }

        [Test]
        public void AcceptsSmallZip64ArchiveAndExtractsIt()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp-zip64-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            try
            {
                string zip = Path.Combine(root, "input.zip");
                File.WriteAllBytes(zip, Zip64());
                SafeZipExtractor.ExtractTo(zip, Path.Combine(root, "output"));
                Assert.IsTrue(File.Exists(Path.Combine(root, "output/0.png")));
            }
            finally
            {
                Directory.Delete(root, true);
            }
        }

        [TestCase(5000UL, false)]
        [TestCase(ulong.MaxValue, false)]
        [TestCase(1UL, true)]
        public void RejectsZip64CountsBeforeMaterializingEntries(ulong count, bool inconsistent)
        {
            using var stream = new MemoryStream(Zip64(count, inconsistent));
            Assert.Throws<IOException>(() => ZipMetadataPreflight.Validate(stream, 4096, CancellationToken.None));
        }

        [Test]
        public void EmptyArchiveIsValid()
        {
            using var stream = new MemoryStream(Archive(0));
            Assert.DoesNotThrow(() => ZipMetadataPreflight.Validate(stream, 4096, CancellationToken.None));
        }
    }
}
