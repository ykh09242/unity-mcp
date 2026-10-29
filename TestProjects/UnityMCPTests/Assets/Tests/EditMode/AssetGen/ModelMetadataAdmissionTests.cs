using System;
using System.IO;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.AssetGen.Import;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    [Parallelizable(ParallelScope.None)]
    public class ModelMetadataAdmissionTests
    {
        private static readonly byte[] Png = Convert.FromBase64String(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aF9sAAAAASUVORK5CYII="
        );

        [Test]
        public void SharedEmbeddedBufferIsValidatedOnceForManyDistinctViews()
        {
            JObject document = Model(512);
            var admission = new ImageAdmission();
            long validated = Inspect(document, admission);
            // A 1 MiB buffer encodes to 1,398,104 characters, including padding.
            Assert.AreEqual(1398104L, validated);
            Assert.AreEqual(512 * 8, admission.WorkBytes);
        }

        [Test]
        public void BufferValidationCacheEndsWithEachInspection()
        {
            JObject document = Model(32);
            var buffers = (JArray)document["buffers"];
            buffers.Add(buffers[0].DeepClone());
            document["bufferViews"][1]["buffer"] = 1;
            Assert.AreEqual(2796208L, Inspect(document, new ImageAdmission()));
            Assert.AreEqual(2796208L, Inspect(document, new ImageAdmission()));
        }

        [Test]
        public void SharedBufferMalformedOutsideImageViewIsRejectedBeforeImageAdmission()
        {
            JObject document = Model(512);
            string uri = document["buffers"][0]["uri"].Value<string>();
            document["buffers"][0]["uri"] = uri.Substring(0, uri.Length - 4) + "!AAA";
            var admission = new ImageAdmission();
            Assert.Throws<InvalidDataException>(() => Inspect(document, admission));
            Assert.AreEqual(0, admission.WorkBytes);
        }

        [Test]
        public void ReusedBufferStillChargesEveryImageAgainstAggregateBudget()
        {
            var admission = new ImageAdmission(new ImageAdmission.Limits { MaxOperationBytes = 24 });
            Assert.Throws<InvalidDataException>(() => Inspect(Model(4), admission));
            Assert.AreEqual(24, admission.WorkBytes);
        }

        [Test]
        public void CancelledMetadataReadStopsBeforeReadingWholeDocument()
        {
            using var cancellation = new CancellationTokenSource();
            byte[] bytes = Encoding.UTF8.GetBytes(Model(1).ToString(Newtonsoft.Json.Formatting.None));
            using var stream = new CancellingReadStream(bytes, cancellation);
            Assert.Throws<OperationCanceledException>(() => Inspect(stream, new ImageAdmission(), cancellation.Token));
            Assert.LessOrEqual(stream.BytesRead, 4096);
            Assert.IsTrue(stream.CanRead);
        }

        [Test]
        public void PreCancelledInspectionDoesNotReadInput()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            using var stream = new MemoryStream(new byte[] { 0 }, false);
            Assert.Throws<OperationCanceledException>(() => Inspect(stream, new ImageAdmission(), cancellation.Token));
            Assert.AreEqual(0, stream.Position);
        }

        [Test]
        public void FileRouteRejectsMalformedSharedBuffer()
        {
            JObject document = Model(32);
            document["buffers"][0]["uri"] = "data:application/octet-stream;base64,!!!!";
            string path = Path.Combine(Path.GetTempPath(), "metadata-admission-" + Guid.NewGuid().ToString("N") + ".gltf");
            try
            {
                System.IO.File.WriteAllText(path, document.ToString(Newtonsoft.Json.Formatting.None));
                Assert.Throws<InvalidDataException>(() => ModelMetadataAdmission.File(path));
            }
            finally
            {
                System.IO.File.Delete(path);
            }
        }

        private static JObject Model(int count)
        {
            byte[] buffer = new byte[1024 * 1024];
            Array.Copy(Png, 0, buffer, 128, Png.Length);
            Array.Copy(Png, 0, buffer, 1024, Png.Length);
            var views = new JArray();
            var images = new JArray();
            for (int i = 0; i < count; i++)
            {
                views.Add(
                    new JObject
                    {
                        ["buffer"] = 0,
                        ["byteOffset"] = i % 2 == 0 ? 128 : 1024,
                        ["byteLength"] = Png.Length,
                    }
                );
                images.Add(new JObject { ["bufferView"] = i, ["mimeType"] = "image/png" });
            }
            return new JObject
            {
                ["asset"] = new JObject { ["version"] = "2.0" },
                ["buffers"] = new JArray(
                    new JObject { ["byteLength"] = buffer.Length, ["uri"] = "data:application/octet-stream;base64," + Convert.ToBase64String(buffer) }
                ),
                ["bufferViews"] = views,
                ["images"] = images,
            };
        }

        private static long Inspect(JObject document, ImageAdmission admission)
        {
            using var stream = new MemoryStream(Encoding.UTF8.GetBytes(document.ToString(Newtonsoft.Json.Formatting.None)), false);
            long result = Inspect(stream, admission);
            Assert.IsTrue(stream.CanRead);
            return result;
        }

        private static long Inspect(Stream stream, ImageAdmission admission, CancellationToken token = default) =>
            ModelMetadataAdmission.Inspect(stream, stream.Length, ".gltf", admission, cancellationToken: token);

        private sealed class CancellingReadStream : MemoryStream
        {
            private readonly CancellationTokenSource cancellation;
            internal int BytesRead;

            internal CancellingReadStream(byte[] bytes, CancellationTokenSource cancellation)
                : base(bytes, false) => this.cancellation = cancellation;

            public override int Read(byte[] buffer, int offset, int count)
            {
                int read = base.Read(buffer, offset, count);
                BytesRead += read;
                cancellation.Cancel();
                return read;
            }
        }
    }
}
