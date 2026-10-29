using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Import;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    [Parallelizable(ParallelScope.None)]
    public class AssetAdmissionTests
    {
        // Complete 1x1 RGB baseline JPEG, encoded from an owned red pixel with System.Drawing.
        internal static byte[] OnePixelJpeg() =>
            Convert.FromBase64String(
                "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
                    + "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/"
                    + "8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2Jy"
                    + "ggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLD"
                    + "xMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3"
                    + "AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6"
                    + "goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK+ZP3E//Z"
            );

        [Test]
        public void Jpeg_CompleteFixturePasses_AndTruncatedSegmentFails()
        {
            var admission = new ImageAdmission();
            Inspect(admission, OnePixelJpeg(), ".jpg");
            Assert.AreEqual(8, admission.WorkBytes);
            var error = Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), new byte[] { 255, 216, 255, 224, 0, 16 }, ".jpg"));
            StringAssert.Contains("exceeds file length", error.Message);
        }

        [Test]
        public void ImagePath_DirectoryRejectedWithoutRemovingIt()
        {
            string directory = Path.Combine(Path.GetTempPath(), "asset-admission-" + Guid.NewGuid().ToString("N") + ".png");
            Directory.CreateDirectory(directory);
            try
            {
                var error = Assert.Throws<InvalidDataException>(() => new ImageAdmission().File(directory));
                StringAssert.Contains("image path is a directory", error.Message);
                Assert.IsTrue(Directory.Exists(directory));
            }
            finally
            {
                Directory.Delete(directory);
            }
        }

        [Test]
        public void CatalogMetadataPagesAndDetailsFitProviderAdmission()
        {
            int seen = 0;
            string cursor = null;
            do
            {
                string url = "https://api.fal.ai/v1/models?category=text-to-audio&limit=100" + (cursor == null ? "" : "&cursor=" + cursor);
                using var response = FalModelCatalogTests.PagedCatalogResponse(new HttpRequestSpec { Url = url }, 201);
                var page = BoundedJson.ParseObject(response.Text);
                var models = (Newtonsoft.Json.Linq.JArray)page["models"];
                Assert.AreEqual(Math.Min(100, 201 - seen), models.Count);
                Assert.IsTrue(models.All(model => model["openapi"] == null));
                seen += models.Count;
                cursor = (string)page["next_cursor"];
                Assert.AreEqual(seen < 201, (bool)page["has_more"]);
            } while (cursor != null);
            Assert.AreEqual(201, seen);
            using var details = FalModelCatalogTests.PagedCatalogResponse(
                new HttpRequestSpec { Url = "https://api.fal.ai/v1/models?endpoint_id=test%2Fmusic-0000&expand=openapi-3.0&limit=5" },
                201
            );
            var expanded = BoundedJson.ParseObject(details.Text);
            Assert.IsNotNull(expanded["models"][0]["openapi"]);
        }

        private static byte[] Png(int width, int height, byte depth = 8, int frames = 1)
        {
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream);
            writer.Write(Convert.FromBase64String("iVBORw0KGgo="));
            void Big(uint value)
            {
                writer.Write((byte)(value >> 24));
                writer.Write((byte)(value >> 16));
                writer.Write((byte)(value >> 8));
                writer.Write((byte)value);
            }
            void Chunk(string name, byte[] data)
            {
                Big((uint)data.Length);
                writer.Write(Encoding.ASCII.GetBytes(name));
                writer.Write(data);
                Big(0);
            }
            byte[] ihdr = new byte[13];
            void Put(int offset, uint value)
            {
                ihdr[offset] = (byte)(value >> 24);
                ihdr[offset + 1] = (byte)(value >> 16);
                ihdr[offset + 2] = (byte)(value >> 8);
                ihdr[offset + 3] = (byte)value;
            }
            Put(0, (uint)width);
            Put(4, (uint)height);
            ihdr[8] = depth;
            ihdr[9] = 6;
            Chunk("IHDR", ihdr);
            if (frames != 1)
            {
                byte[] animation = new byte[8];
                animation[3] = (byte)frames;
                Chunk("acTL", animation);
                for (int i = 0; i < frames; i++)
                {
                    byte[] control = new byte[26];
                    Array.Copy(ihdr, 0, control, 4, 8);
                    Chunk("fcTL", control);
                }
            }
            Chunk("IDAT", new byte[] { 1 });
            Chunk("IEND", Array.Empty<byte>());
            return stream.ToArray();
        }

        private static void Inspect(ImageAdmission admission, byte[] bytes, string extension)
        {
            using var stream = new MemoryStream(bytes, false);
            admission.Inspect(stream, stream.Length, extension);
        }

        private static byte[] PngDataLayout(params byte[][] chunks)
        {
            byte[] original = Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==");
            using var stream = new MemoryStream();
            stream.Write(original, 0, 33);
            void Big(uint value)
            {
                for (int shift = 24; shift >= 0; shift -= 8)
                    stream.WriteByte((byte)(value >> shift));
            }
            foreach (byte[] chunk in chunks)
            {
                Big((uint)chunk.Length);
                byte[] content = Encoding.ASCII.GetBytes("IDAT").Concat(chunk).ToArray();
                stream.Write(content, 0, content.Length);
                uint crc = uint.MaxValue;
                foreach (byte value in content)
                {
                    crc ^= value;
                    for (int bit = 0; bit < 8; bit++)
                        crc = (crc >> 1) ^ ((crc & 1) == 0 ? 0 : 0xedb88320u);
                }
                Big(~crc);
            }
            stream.Write(original, original.Length - 12, 12);
            return stream.ToArray();
        }

        [Test]
        public void PngEmptyDataChunksPreserveImageAdmission()
        {
            byte[] payload = Convert.FromBase64String("eJxj+M/A8B8ABQAB/w==");
            foreach (
                byte[] image in new[]
                {
                    PngDataLayout(Array.Empty<byte>(), payload, Array.Empty<byte>()),
                    PngDataLayout(Array.Empty<byte>(), Array.Empty<byte>(), payload),
                    PngDataLayout(payload.Take(4).ToArray(), Array.Empty<byte>(), payload.Skip(4).ToArray()),
                }
            )
            {
                var admission = new ImageAdmission();
                Inspect(admission, image, ".png");
                Assert.AreEqual(8, admission.WorkBytes);
            }
        }

        [Test]
        public void PngEmptyDataChunksDoNotSupplyImageDataOrBypassBounds()
        {
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), PngDataLayout(), ".png"));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), PngDataLayout(Array.Empty<byte>(), Array.Empty<byte>()), ".png"));
            byte[] valid = PngDataLayout(Array.Empty<byte>(), Convert.FromBase64String("eJxj+M/A8B8ABQAB/w=="));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), valid.Take(valid.Length - 3).ToArray(), ".png"));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 7 }), valid, ".png"));
        }

        [Test]
        public void PngEmptyDataChunkStillStartsAnimationData()
        {
            byte[] emptyFirst = PngDataLayout(Array.Empty<byte>()).Take(45).Concat(Png(1, 1, frames: 2).Skip(33)).ToArray();
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), emptyFirst, ".png"));
        }

        private static byte[] Build(Action<BinaryWriter> write)
        {
            using var s = new MemoryStream();
            using var w = new BinaryWriter(s);
            write(w);
            return s.ToArray();
        }

        private static byte[] Format(string extension)
        {
            return Build(w =>
            {
                void Ascii(string value) => w.Write(Encoding.ASCII.GetBytes(value));
                if (extension == ".tga")
                {
                    w.Write(new byte[] { 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1, 0, 24, 0 });
                    w.Write(new byte[3]);
                }
                else if (extension == ".hdr")
                {
                    Ascii("#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X 1\n");
                    w.Write(new byte[] { 128, 64, 32, 128 });
                }
                else if (extension == ".webp")
                {
                    Ascii("RIFF");
                    w.Write(18u);
                    Ascii("WEBPVP8L");
                    w.Write(5u);
                    w.Write(new byte[] { 0x2f, 0, 0, 0, 0, 0 });
                }
                else if (extension == ".jpg")
                {
                    w.Write(new byte[] { 255, 216, 255, 192, 0, 11, 8, 0, 1, 0, 1, 1, 1, 0x11, 0, 255, 218, 0, 8, 1, 1, 0, 0, 63, 0, 0, 255, 217 });
                }
                else if (extension == ".bmp")
                {
                    Ascii("BM");
                    w.Write(58u);
                    w.Write(0u);
                    w.Write(54u);
                    w.Write(40u);
                    w.Write(1);
                    w.Write(1);
                    w.Write((ushort)1);
                    w.Write((ushort)24);
                    w.Write(0u);
                    w.Write(4u);
                    w.Write(new byte[16]);
                    w.Write(new byte[4]);
                }
                else if (extension == ".dds")
                {
                    Ascii("DDS ");
                    w.Write(124u);
                    w.Write(0x1007u);
                    w.Write(1u);
                    w.Write(1u);
                    w.Write(4u);
                    w.Write(0u);
                    w.Write(1u);
                    w.Write(new byte[44]);
                    w.Write(32u);
                    w.Write(0x41u);
                    w.Write(0u);
                    w.Write(32u);
                    w.Write(new byte[16]);
                    w.Write(0x1000u);
                    w.Write(new byte[16]);
                    w.Write(new byte[4]);
                }
                else if (extension == ".ktx")
                {
                    w.Write(Convert.FromBase64String("q0tUWCAxMbsNChoK"));
                    w.Write(0x04030201u);
                    w.Write(0x1401u);
                    w.Write(1u);
                    w.Write(0x1908u);
                    w.Write(0x8058u);
                    w.Write(0x1908u);
                    w.Write(1u);
                    w.Write(1u);
                    w.Write(0u);
                    w.Write(0u);
                    w.Write(1u);
                    w.Write(1u);
                    w.Write(0u);
                    w.Write(4u);
                    w.Write(new byte[4]);
                }
                else if (extension == ".ktx2")
                {
                    w.Write(Convert.FromBase64String("q0tUWCAyMLsNChoK"));
                    foreach (uint n in new uint[] { 37, 1, 1, 1, 0, 0, 1, 1, 0, 104, 44, 0, 0 })
                        w.Write(n);
                    w.Write(0UL);
                    w.Write(0UL);
                    w.Write(148UL);
                    w.Write(4UL);
                    w.Write(4UL);
                    w.Write(44u);
                    w.Write(0u);
                    w.Write((ushort)2);
                    w.Write((ushort)40);
                    w.Write(new byte[16]);
                    w.Write((ushort)0);
                    w.Write((byte)7);
                    w.Write((byte)0);
                    w.Write(new byte[12]);
                    w.Write(new byte[4]);
                }
                else if (extension == ".basis")
                {
                    byte[] header = new byte[77];
                    header[0] = 0x73;
                    header[1] = 0x42;
                    header[2] = 0x10;
                    header[4] = 77;
                    header[8] = 39;
                    header[14] = 1;
                    header[17] = 1;
                    header[20] = 1;
                    header[65] = 77;
                    w.Write(header);
                    byte[] slice = new byte[23];
                    slice[5] = slice[7] = slice[9] = slice[11] = 1;
                    slice[13] = 100;
                    slice[17] = 16;
                    w.Write(slice);
                    w.Write(new byte[16]);
                }
                else if (extension == ".exr")
                {
                    w.Write(20000630u);
                    w.Write(2u);
                    void Attribute(string name, string type, byte[] value)
                    {
                        Ascii(name + "\0" + type + "\0");
                        w.Write(value.Length);
                        w.Write(value);
                    }
                    Attribute(
                        "channels",
                        "chlist",
                        Build(c =>
                        {
                            c.Write(Encoding.ASCII.GetBytes("R\0"));
                            c.Write(1u);
                            c.Write(0u);
                            c.Write(1u);
                            c.Write(1u);
                            c.Write((byte)0);
                        })
                    );
                    Attribute("dataWindow", "box2i", new byte[16]);
                    Attribute("displayWindow", "box2i", new byte[16]);
                    Attribute("compression", "compression", new byte[] { 0 });
                    w.Write((byte)0);
                    w.Write((ulong)w.BaseStream.Position + 8);
                    w.Write(0);
                    w.Write(2u);
                    w.Write((ushort)0);
                }
            });
        }

        [TestCase(".jpg")]
        [TestCase(".tga")]
        [TestCase(".bmp")]
        [TestCase(".hdr")]
        [TestCase(".exr")]
        [TestCase(".webp")]
        [TestCase(".dds")]
        [TestCase(".ktx")]
        [TestCase(".ktx2")]
        [TestCase(".basis")]
        public void ExistingTextureFamiliesHaveBoundedHeaderAdmission(string extension)
        {
            byte[] bytes = Format(extension);
            Assert.DoesNotThrow(() => Inspect(new ImageAdmission(), bytes, extension));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), bytes.Take(Math.Min(12, bytes.Length / 2)).ToArray(), extension));
        }

        [TestCase(4096)]
        [TestCase(8192)]
        [TestCase(16384)]
        public void LargeNormalPngDimensionsRemainAdmissibleWithoutDecoding(int side)
        {
            var admission = new ImageAdmission();
            Inspect(admission, Png(side, side), ".png");
            Assert.AreEqual((long)side * side * 8, admission.WorkBytes);
        }

        [Test]
        public void ImageBudgetsCountPrecisionFramesAndAggregateWork()
        {
            var admission = new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 1024, MaxOperationBytes = 1500 });
            Inspect(admission, Png(8, 8, 16), ".png");
            Assert.AreEqual(1024, admission.WorkBytes);
            Assert.Throws<InvalidDataException>(() => Inspect(admission, Png(8, 8), ".png"));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 1000 }), Png(8, 8, 8, 2), ".png"));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), Png(int.MaxValue, int.MaxValue), ".png"));
        }

        [Test]
        public void ImageFramingRejectsTruncationSignatureMismatchAndInvalidDepth()
        {
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), Png(2, 2).Take(25).ToArray(), ".png"));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), Png(2, 2), ".jpg"));
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), Png(2, 2, 3), ".png"));
        }

        [Test]
        public void ArchiveTextureAdmissionPrecedesAnyExtraction()
        {
            using var bytes = new MemoryStream();
            using (var zip = new ZipArchive(bytes, ZipArchiveMode.Create, true))
            {
                using var output = zip.CreateEntry("textures/bomb.png").Open();
                byte[] image = Png(100000, 100000);
                output.Write(image, 0, image.Length);
            }
            bytes.Position = 0;
            using var archive = new ZipArchive(bytes, ZipArchiveMode.Read);
            Assert.Throws<InvalidDataException>(() =>
                ArchiveAssetAdmission.Validate(archive, new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ".png" })
            );
        }

        [Test]
        public void JsonBreadthAdmissionRejectsBeforeMaterializationAtExactSmallBoundaries()
        {
            var limits = new BoundedJson.Limits
            {
                MaxTokens = 8,
                MaxProperties = 1,
                MaxContainerItems = 3,
            };
            Assert.DoesNotThrow(() => BoundedJson.ParseObject("{\"a\":[0,1,2]}", limits));
            Assert.Throws<InvalidDataException>(() => BoundedJson.ParseObject("{\"a\":[0,1,2,3]}", limits));
            Assert.Throws<InvalidDataException>(() => BoundedJson.ParseObject("{\"a\":0,\"b\":0}", limits));
            limits.MaxContainerItems = 2;
            Assert.Throws<InvalidDataException>(() => BoundedJson.ParseObject("{\"a\":[0,1,2]}", limits));
        }

        [Test]
        public void JsonAdmissionRejectsDuplicatePropertiesTruncationAndDeepGraphs()
        {
            Assert.Throws<Newtonsoft.Json.JsonReaderException>(() => BoundedJson.ParseObject("{\"a\":1,\"a\":2}"));
            Assert.Throws<InvalidDataException>(() => BoundedJson.ParseObject("{\"a\":[1"));
            Assert.Throws<Newtonsoft.Json.JsonReaderException>(() => BoundedJson.ParseObject("{\"a\":{\"b\":{}}}", new BoundedJson.Limits { MaxDepth = 2 }));
        }

        [Test]
        public void EmbeddedGltfDataImageIsInspectedWithoutPixelDecode()
        {
            string Json(byte[] image) =>
                "{\"asset\":{\"version\":\"2.0\"},\"images\":[{\"uri\":\"data:image/png;base64," + Convert.ToBase64String(image) + "\"}]}";
            void Admit(byte[] image)
            {
                byte[] bytes = Encoding.UTF8.GetBytes(Json(image));
                using var stream = new MemoryStream(bytes);
                ModelMetadataAdmission.Inspect(stream, bytes.Length, ".gltf", new ImageAdmission());
            }
            Assert.DoesNotThrow(() => Admit(Png(8192, 8192)));
            Assert.Throws<InvalidDataException>(() => Admit(Png(65536, 65536)));
            Assert.Throws<InvalidDataException>(() => Admit(new byte[] { 1, 2, 3 }));
        }

        private static byte[] Tiff(bool wide, bool cycle = false)
        {
            using var stream = new MemoryStream();
            using var w = new BinaryWriter(stream);
            w.Write((byte)'I');
            w.Write((byte)'I');
            w.Write((ushort)(wide ? 43 : 42));
            void Offset(ulong value)
            {
                if (wide)
                    w.Write(value);
                else
                    w.Write((uint)value);
            }
            if (wide)
            {
                w.Write((ushort)8);
                w.Write((ushort)0);
            }
            Offset(wide ? 16UL : 8UL);
            if (wide)
                w.Write(7UL);
            else
                w.Write((ushort)7);
            ulong pixelOffset = (ulong)(wide ? 16 + 8 + 7 * 20 + 8 : 8 + 2 + 7 * 12 + 4);
            void Tag(ushort tag, ulong value)
            {
                w.Write(tag);
                w.Write((ushort)(wide ? 16 : 4));
                Offset(1);
                Offset(value);
            }
            Tag(256, 2);
            Tag(257, 2);
            Tag(258, 16);
            Tag(277, 4);
            Tag(273, pixelOffset);
            Tag(279, 32);
            Tag(259, 1);
            Offset(
                cycle
                    ? wide
                        ? 16UL
                        : 8UL
                    : 0
            );
            w.Write(new byte[32]);
            return stream.ToArray();
        }

        [TestCase(false)]
        [TestCase(true)]
        public void ClassicAndBigTiffCountPrecisionAndRejectIfdCycles(bool wide)
        {
            var admission = new ImageAdmission();
            Inspect(admission, Tiff(wide), ".tiff");
            Assert.AreEqual(64, admission.WorkBytes);
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), Tiff(wide, true), ".tif"));
        }

        [Test]
        public void TiffSubIfdsAndKtxExpandedLevelsShareImageWorkBudget()
        {
            byte[] parent = Tiff(false),
                child = Tiff(false);
            // Replace the final compression tag with a SubIFD pointer to a second image.
            parent[82] = 0x4a;
            parent[83] = 1;
            Array.Copy(BitConverter.GetBytes((uint)parent.Length), 0, parent, 90, 4);
            Array.Copy(BitConverter.GetBytes((uint)(parent.Length + 90)), 0, child, 66, 4);
            byte[] combined = parent.Concat(child.Skip(8)).ToArray();
            var admission = new ImageAdmission();
            Inspect(admission, combined, ".tif");
            Assert.AreEqual(128, admission.WorkBytes);
            byte[] ktx = Format(".ktx2");
            ktx[96] = 32;
            Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 64 }), ktx, ".ktx2");
            ktx[96] = 33;
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 64 }), ktx, ".ktx2"));
        }

        private static byte[] TiffGeometry(
            bool wide = false,
            uint tileWidth = 32,
            uint tileHeight = 32,
            uint imageDepth = 1,
            uint tileDepth = 1,
            int bits = 8,
            int planar = 1,
            bool tiled = true,
            uint rows = uint.MaxValue,
            bool mixed = false,
            int payloadBytes = 4096
        )
        {
            // Small real uncompressed tiles reproduce the native working-buffer geometry.
            // Hostile dimension variants keep the payload bounded; they must fail on metadata.
            var tags = new SortedDictionary<ushort, (ushort type, ulong[] values)>
            {
                [256] = (4, new[] { 1UL }),
                [257] = (4, new[] { 1UL }),
                [258] = (3, Enumerable.Repeat((ulong)bits, 4).ToArray()),
                [259] = (3, new[] { 1UL }),
                [262] = (3, new[] { 2UL }),
                [277] = (3, new[] { 4UL }),
                [284] = (3, new[] { (ulong)planar }),
                [338] = (3, new[] { 2UL }),
            };
            int planes = planar == 2 ? 4 : 1;
            if (tiled)
            {
                tags[322] = (4, new[] { (ulong)tileWidth });
                tags[323] = (4, new[] { (ulong)tileHeight });
                tags[324] = (4, new ulong[planes]);
                tags[325] = (4, Enumerable.Repeat((ulong)payloadBytes, planes).ToArray());
                if (tileDepth != 1)
                    tags[32998] = (4, new[] { (ulong)tileDepth });
            }
            if (!tiled || mixed)
            {
                tags[273] = (4, new ulong[planes]);
                tags[278] = (4, new[] { (ulong)rows });
                tags[279] = (4, Enumerable.Repeat((ulong)payloadBytes, planes).ToArray());
            }
            if (imageDepth != 1)
                tags[32997] = (4, new[] { (ulong)imageDepth });
            int inline = wide ? 8 : 4;
            int offset = (wide ? 16 + 8 + 8 : 8 + 2 + 4) + tags.Count * (wide ? 20 : 12);
            var external = new Dictionary<ushort, int>();
            foreach (var tag in tags)
            {
                int size = (tag.Value.type == 3 ? 2 : 4) * tag.Value.values.Length;
                if (size > inline)
                {
                    external[tag.Key] = offset;
                    offset += size;
                }
            }
            foreach (ushort tag in new ushort[] { 273, 324 })
                if (tags.TryGetValue(tag, out var value))
                    for (int i = 0; i < planes; i++)
                        value.values[i] = (ulong)(offset + i * payloadBytes);
            return Build(w =>
            {
                w.Write((ushort)0x4949);
                w.Write((ushort)(wide ? 43 : 42));
                void Offset(ulong n)
                {
                    if (wide)
                        w.Write(n);
                    else
                        w.Write((uint)n);
                }
                if (wide)
                {
                    w.Write((ushort)8);
                    w.Write((ushort)0);
                }
                Offset(wide ? 16UL : 8UL);
                if (wide)
                    w.Write((ulong)tags.Count);
                else
                    w.Write((ushort)tags.Count);
                void Values((ushort type, ulong[] values) tag)
                {
                    foreach (ulong n in tag.values)
                        if (tag.type == 3)
                            w.Write((ushort)n);
                        else
                            w.Write((uint)n);
                }
                foreach (var tag in tags)
                {
                    w.Write(tag.Key);
                    w.Write(tag.Value.type);
                    Offset((ulong)tag.Value.values.Length);
                    if (external.TryGetValue(tag.Key, out int position))
                        Offset((ulong)position);
                    else
                    {
                        long start = w.BaseStream.Position;
                        Values(tag.Value);
                        w.Write(new byte[inline - (int)(w.BaseStream.Position - start)]);
                    }
                }
                Offset(0);
                foreach (var tag in tags)
                    if (external.ContainsKey(tag.Key))
                        Values(tag.Value);
                w.Write(new byte[payloadBytes * planes]);
            });
        }

        [TestCase(false)]
        [TestCase(true)]
        public void TiffWorkingTileBudgetIncludesPaddedPixelsBeyondCanvas(bool wide)
        {
            byte[] image = TiffGeometry(wide);
            if (!wide)
                Assert.AreEqual(4262, image.Length, "Exact reviewed 1x1 RGBA8 / 32x32 tile reproduction.");
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 1024 }), image, ".tif"));
            var admission = new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 8192 });
            Inspect(admission, image, ".tif");
            Assert.AreEqual(8192, admission.WorkBytes);
            Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = 8191 }), image, ".tif"));
        }

        [Test]
        public void TiffWorkingGeometryAccountsPrecisionPlanesAndDepth()
        {
            void AtBoundary(byte[] bytes, long expected)
            {
                var admission = new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = expected });
                Inspect(admission, bytes, ".tif");
                Assert.AreEqual(expected, admission.WorkBytes);
                Assert.Throws<InvalidDataException>(() =>
                    Inspect(new ImageAdmission(new ImageAdmission.Limits { MaxImageBytes = expected - 1 }), bytes, ".tif")
                );
            }
            AtBoundary(TiffGeometry(bits: 16, payloadBytes: 8192), 16384);
            AtBoundary(TiffGeometry(planar: 2, payloadBytes: 1024), 8192);
            AtBoundary(TiffGeometry(tileDepth: 2, payloadBytes: 8192), 16384);
            AtBoundary(TiffGeometry(tiled: false, imageDepth: 128, payloadBytes: 512), 1024);
            AtBoundary(TiffGeometry(tiled: false, payloadBytes: 4), 8);
        }

        [Test]
        public void TiffWorkingGeometryRejectsInvalidOrAmbiguousDeclarations()
        {
            foreach (
                byte[] bytes in new[]
                {
                    TiffGeometry(tileWidth: 0),
                    TiffGeometry(tileHeight: uint.MaxValue),
                    TiffGeometry(imageDepth: uint.MaxValue),
                    TiffGeometry(tileDepth: uint.MaxValue),
                    TiffGeometry(planar: 3),
                    TiffGeometry(tiled: false, rows: 0),
                    TiffGeometry(mixed: true),
                }
            )
                Assert.Throws<InvalidDataException>(() => Inspect(new ImageAdmission(), bytes, ".tif"));
        }

        [TestCase("model.bin")]
        [TestCase("./model.bin")]
        [TestCase("././model.bin")]
        [TestCase(".//model.bin")]
        [TestCase("%2E/model.bin")]
        public void ArchiveEmbeddedImageBufferAcceptsContainedDotSegments(string uri)
        {
            byte[] image = Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==");
            string json =
                "{\"asset\":{\"version\":\"2.0\"},\"buffers\":[{\"uri\":\""
                + uri
                + "\",\"byteLength\":"
                + image.Length
                + "}],\"bufferViews\":[{\"buffer\":0,\"byteLength\":"
                + image.Length
                + "}],\"images\":[{\"bufferView\":0,\"mimeType\":\"image/png\"}]}";
            using var bytes = new MemoryStream();
            using (var zip = new ZipArchive(bytes, ZipArchiveMode.Create, true))
            {
                using (var writer = new StreamWriter(zip.CreateEntry("scene.gltf").Open()))
                    writer.Write(json);
                using var output = zip.CreateEntry("model.bin").Open();
                output.Write(image, 0, image.Length);
            }
            bytes.Position = 0;
            using var archive = new ZipArchive(bytes, ZipArchiveMode.Read);
            Assert.DoesNotThrow(() => ArchiveAssetAdmission.Validate(archive, new HashSet<string>(StringComparer.OrdinalIgnoreCase) { ".gltf", ".bin" }));
        }

        [TestCase("../model.bin")]
        [TestCase("%2E%2E/model.bin")]
        [TestCase("/model.bin")]
        [TestCase("%2Fmodel.bin")]
        [TestCase("https://example.invalid/model.bin")]
        public void ModelResourcePathsRejectTraversalAndRootedForms(string uri)
        {
            Assert.Throws<InvalidDataException>(() => ModelMetadataAdmission.LocalUri(uri));
        }

        [Test]
        public void LocalCopySharesReservationsCancelsRollsBackAndPreservesSource()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp_admission_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string source = Path.Combine(root, "source"),
                output = Path.Combine(root, "output");
            byte[] payload = new byte[100000];
            System.IO.File.WriteAllBytes(source, payload);
            AssetDiskBudget.FreeBytesForTests = _ => 10000000;
            AssetDiskBudget.AggregateBytesForTests = 150000;
            AssetDiskBudget.HeadroomBytesForTests = 1;
            try
            {
                using (var existing = AssetDiskBudget.ReserveDownloadAsync(root, 60000, default).GetAwaiter().GetResult())
                    Assert.Throws<IOException>(() => new LocalAssetCopy(root, payload.Length, default));
                using (var cancel = new CancellationTokenSource())
                using (var copy = new LocalAssetCopy(root, payload.Length, cancel.Token))
                    Assert.Throws<OperationCanceledException>(() => copy.Copy(source, output, payload.Length, progress: _ => cancel.Cancel()));
                Assert.False(System.IO.File.Exists(output));
                Assert.AreEqual(payload.Length, new FileInfo(source).Length);
                Assert.Zero(AssetDiskBudget.ReservedBytesForTests(root));
                using (var copy = new LocalAssetCopy(root, payload.Length, default))
                    Assert.Throws<IOException>(() => copy.Copy(source, output, payload.Length - 1));
                System.IO.File.WriteAllText(output, "pre-existing");
                using (var copy = new LocalAssetCopy(root, payload.Length, default))
                    Assert.Throws<IOException>(() => copy.Copy(source, output, payload.Length));
                Assert.AreEqual("pre-existing", System.IO.File.ReadAllText(output));
            }
            finally
            {
                AssetDiskBudget.FreeBytesForTests = null;
                AssetDiskBudget.AggregateBytesForTests = null;
                AssetDiskBudget.HeadroomBytesForTests = null;
                Directory.Delete(root, true);
            }
        }

        [Test]
        public void FailedCopyCleanupRetainsCreditUntilOwnedOutputDisappears()
        {
            string root = Path.Combine(Path.GetTempPath(), "mcp_copy_credit_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            string source = Path.Combine(root, "source"),
                output = Path.Combine(root, "output");
            System.IO.File.WriteAllBytes(source, new byte[16]);
            AssetDiskBudget.FreeBytesForTests = _ => 1000;
            AssetDiskBudget.AggregateBytesForTests = 32;
            AssetDiskBudget.HeadroomBytesForTests = 1;
            var copy = new LocalAssetCopy(root, 16, default);
            try
            {
                copy.Copy(source, output, 16);
                LocalAssetCopy.DeleteForTests = _ => throw new IOException("owned test failure");
                copy.Dispose();
                Assert.AreEqual(16, AssetDiskBudget.ReservedBytesForTests(root));
                Assert.True(System.IO.File.Exists(output));
                LocalAssetCopy.DeleteForTests = null;
                copy.Dispose();
                Assert.Zero(AssetDiskBudget.ReservedBytesForTests(root));
                Assert.False(System.IO.File.Exists(output));
                Assert.True(System.IO.File.Exists(source));
            }
            finally
            {
                LocalAssetCopy.DeleteForTests = null;
                copy.Dispose();
                AssetDiskBudget.FreeBytesForTests = null;
                AssetDiskBudget.AggregateBytesForTests = null;
                AssetDiskBudget.HeadroomBytesForTests = null;
                Directory.Delete(root, true);
            }
        }
    }
}
