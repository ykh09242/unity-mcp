using System;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using MCPForUnity.Editor.Services.AssetGen.Providers;
using NUnit.Framework;
using UnityEngine;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class DownloadLimitSecurityTests
    {
        private string root;

        [SetUp]
        public void SetUp()
        {
            root = Path.Combine(Path.GetDirectoryName(Application.dataPath), "Temp", "artifact_stream_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
        }

        [TearDown]
        public void TearDown()
        {
            Assert.IsTrue(Path.GetFileName(root).StartsWith("artifact_stream_", StringComparison.Ordinal));
            Assert.IsEmpty(Directory.GetFiles(root, "*.part", SearchOption.AllDirectories), "Every staged file must release its owned handle.");
            Directory.Delete(root, true);
        }

        [Test]
        public void DownloadPolicyAdmitsMultiGiBArtifacts()
        {
            Assert.That((long)AssetDownloadTransport.MaxDownloadBytes, Is.GreaterThanOrEqualTo(8L * 1024 * 1024 * 1024));
        }

        [Test]
        public void ApiBodyTransfersItsLogicalSliceAndSurvivesHandlerDisposal()
        {
            var handler = new BoundedDownloadHandler(1024);
            object body;
            try
            {
                var receive = typeof(BoundedDownloadHandler).GetMethod("ReceiveData", BindingFlags.NonPublic | BindingFlags.Instance);
                Assert.IsTrue((bool)receive.Invoke(handler, new object[] { new byte[] { 1, 2, 3, 4 }, 3 }));
                body = handler.GetBody();
            }
            finally
            {
                handler.Dispose();
            }
            Assert.That(body, Is.InstanceOf<System.ArraySegment<byte>>(), "Transfer the buffer with its logical length, without an eager array copy.");
            var segment = (System.ArraySegment<byte>)body;
            Assert.AreEqual(3, segment.Count);
            CollectionAssert.AreEqual(new byte[] { 1, 2, 3 }, segment);
        }

        [Test]
        public void TextIsDecodedLazilyAndInvalidatedWhenBodyIsReplaced()
        {
            var result = new HttpResult { Body = System.Text.Encoding.UTF8.GetBytes("hello 한글") };
            Assert.AreEqual("hello 한글", result.Text);
            string first = result.Text;
            Assert.AreSame(first, result.Text);
            result.Body = System.Text.Encoding.UTF8.GetBytes("next");
            Assert.AreEqual("next", result.Text);
            result.Text = "explicit";
            Assert.AreEqual("explicit", result.Text);
        }

        [Test]
        public void TransferredBodyMaterializesEachRepresentationOnlyOnDemand()
        {
            var storage = System.Text.Encoding.UTF8.GetBytes("xhello 한글x");
            var result = new HttpResult();
            var slice = new System.ArraySegment<byte>(storage, 1, storage.Length - 2);
            result.SetBody(slice);
            Assert.Greater(ManagedAllocation.Count(() => System.GC.KeepAlive(new byte[16])), 0);
            Assert.AreEqual(0, ManagedAllocation.Count(() => result.SetBody(slice)));
            Assert.AreEqual("hello 한글", result.Text);
            Assert.AreEqual(0, ManagedAllocation.Count(() => System.GC.KeepAlive(result.Text)));
            CollectionAssert.AreEqual(System.Text.Encoding.UTF8.GetBytes("hello 한글"), result.Body);
            Assert.AreEqual(0, ManagedAllocation.Count(() => System.GC.KeepAlive(result.Body)));
        }

        [TestCase(-1)]
        [TestCase(1)]
        [TestCase(100)]
        public void ArtifactBodiesCannotExceedLimitEvenWithMissingOrFalseLength(long declaredLength)
        {
            using var stream = new MemoryStream(new byte[100]);
            Assert.ThrowsAsync<IOException>(async () => await DownloadedArtifact.ReadAsync(stream, declaredLength, 50, root, CancellationToken.None));
        }

        [Test]
        public async Task ArtifactAtLimitIsAccepted()
        {
            using var stream = new MemoryStream(new byte[50]);
            using var artifact = await DownloadedArtifact.ReadAsync(stream, -1, 50, root, CancellationToken.None);
            Assert.AreEqual(50, artifact.Length);
            Assert.IsTrue(File.Exists(artifact.Path));
            using var sha = SHA256.Create();
            Assert.AreEqual(BitConverter.ToString(sha.ComputeHash(new byte[50])).Replace("-", "").ToLowerInvariant(), artifact.Sha256);
            Assert.AreEqual(50, artifact.Rewind().Length);
        }

        [TestCase(49)]
        [TestCase(51)]
        public void IncorrectDeclaredLengthIsRejectedAndStagingIsDeleted(long declared)
        {
            using var input = new MemoryStream(new byte[50]);
            Assert.ThrowsAsync<IOException>(() => DownloadedArtifact.ReadAsync(input, declared, 100, root, CancellationToken.None));
        }

        [Test]
        public async Task ArtifactDoesNotMaterializeAResponseBody()
        {
            // Stream 16 MiB with a tiny fixture header, not a same-sized input/output array.
            using var input = new VirtualArtifact(16L * 1024 * 1024, new byte[] { 1, 2, 3 });
            using var result = new HttpResult
            {
                IsSuccess = true,
                DownloadedFile = await DownloadedArtifact.ReadAsync(input, input.Length, AssetDownloadTransport.MaxDownloadBytes, root, CancellationToken.None),
            };
            Assert.AreEqual(input.Length, result.DownloadedFile.Length);
            Assert.IsNull(result.Body);
            Assert.IsNull(result.Text);
            Assert.AreEqual(1, result.DownloadedFile.Rewind().ReadByte());
            string path = result.DownloadedFile.Path;
            result.Dispose();
            Assert.IsFalse(File.Exists(path));
        }

        [Test]
        public void MultiGiBDeclarationReachesStreamingInputInsteadOfTheOldSizeRefusal()
        {
            using var input = new VirtualArtifact(3L * 1024 * 1024 * 1024) { FailAfter = 0 };
            var error = Assert.ThrowsAsync<IOException>(() =>
                DownloadedArtifact.ReadAsync(input, input.Length, AssetDownloadTransport.MaxDownloadBytes, root, CancellationToken.None)
            );
            Assert.AreEqual("fixture read failure", error.Message);
            Assert.AreEqual(1, input.ReadCalls);
        }

        [Test]
        public void ExcessiveDeclaredLengthIsRejectedBeforeReadingOrCreatingFiles()
        {
            using var input = new VirtualArtifact(AssetDownloadTransport.MaxDownloadBytes + 1);
            Assert.ThrowsAsync<IOException>(() =>
                DownloadedArtifact.ReadAsync(input, input.Length, AssetDownloadTransport.MaxDownloadBytes, root, CancellationToken.None)
            );
            Assert.Zero(input.ReadCalls);
            Assert.IsEmpty(Directory.GetFileSystemEntries(root));
        }

        [TestCase(false)]
        [TestCase(true)]
        public void PartialReadFailureOrCancellationDeletesStaging(bool cancel)
        {
            using var cts = new CancellationTokenSource();
            using var input = new VirtualArtifact(1000000)
            {
                FailAfter = cancel ? long.MaxValue : 65536,
                OnRead = position =>
                {
                    if (cancel && position > 65536)
                        cts.Cancel();
                },
            };
            if (cancel)
                Assert.CatchAsync<OperationCanceledException>(() => DownloadedArtifact.ReadAsync(input, -1, 1000000, root, cts.Token));
            else
                Assert.ThrowsAsync<IOException>(() => DownloadedArtifact.ReadAsync(input, -1, 1000000, root, cts.Token));
        }

        [TestCase(false)]
        [TestCase(true)]
        public async Task RedirectOrLateCanceledResponseReleasesStagedFile(bool cancel)
        {
            using var cts = new CancellationTokenSource();
            using var input = new MemoryStream(new byte[] { 1 });
            var artifact = await DownloadedArtifact.ReadAsync(input, -1, 100, root, CancellationToken.None);
            string path = artifact.Path;
            var transport = new AssetDownloadTransport(
                _ => Task.FromResult(new[] { IPAddress.Parse("8.8.8.8") }),
                (_, __, ___) =>
                {
                    if (cancel)
                        cts.Cancel();
                    return Task.FromResult(
                        new HttpResult
                        {
                            Status = cancel ? 200 : 302,
                            IsSuccess = cancel,
                            RedirectLocation = "https://localhost/private",
                            DownloadedFile = artifact,
                        }
                    );
                }
            );
            if (cancel)
                Assert.CatchAsync<OperationCanceledException>(() => transport.DownloadAsync("meshy", "https://assets.meshy.ai/model.glb", cts.Token));
            else
                Assert.ThrowsAsync<InvalidOperationException>(() => transport.DownloadAsync("meshy", "https://assets.meshy.ai/model.glb", cts.Token));
            Assert.IsFalse(File.Exists(path));
        }

        [Test]
        public void MultiGiBGlbValidationReadsOnlyContainerMetadata()
        {
            byte[] jsonOnly = ModelImportPipelineTests.ContainerOnlyGlb();
            const long total = 3L * 1024 * 1024 * 1024;
            using var prefix = new MemoryStream();
            prefix.Write(jsonOnly, 0, jsonOnly.Length);
            using (var writer = new BinaryWriter(prefix, System.Text.Encoding.UTF8, true))
            {
                writer.Write((uint)(total - jsonOnly.Length - 8));
                writer.Write(0x004E4942u);
                prefix.Position = 8;
                writer.Write((uint)total);
            }
            using var input = new VirtualArtifact(total, prefix.ToArray());
            Assert.AreEqual("glb", TripoAdapter.ValidateModelStream(input, "glb"));
            Assert.Less(input.BytesRead, 1024);
        }

        private sealed class VirtualArtifact : Stream
        {
            private readonly long length;
            private readonly byte[] prefix;
            public long FailAfter = long.MaxValue;
            public Action<long> OnRead;
            public int ReadCalls;
            public long BytesRead;

            public VirtualArtifact(long length, byte[] prefix = null)
            {
                this.length = length;
                this.prefix = prefix ?? Array.Empty<byte>();
            }

            public override bool CanRead => true;
            public override bool CanSeek => true;
            public override bool CanWrite => false;
            public override long Length => length;
            public override long Position { get; set; }

            public override int Read(byte[] buffer, int offset, int count)
            {
                ReadCalls++;
                if (Position >= FailAfter)
                    throw new IOException("fixture read failure");
                int n = (int)Math.Min(count, length - Position);
                Array.Clear(buffer, offset, n);
                if (Position < prefix.Length)
                    Array.Copy(prefix, Position, buffer, offset, Math.Min(n, prefix.Length - Position));
                Position += n;
                BytesRead += n;
                OnRead?.Invoke(Position);
                return n;
            }

            public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken ct) => Task.FromResult(Read(buffer, offset, count));

            public override long Seek(long offset, SeekOrigin origin) =>
                Position = origin == SeekOrigin.Begin ? offset : (origin == SeekOrigin.Current ? Position : Length) + offset;

            public override void Flush() { }

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        }

        [Test]
        public void StreamingApiResponseStopsBeforeBufferingExcessBytes()
        {
            using var handler = new BoundedDownloadHandler(10);
            var receive = typeof(BoundedDownloadHandler).GetMethod("ReceiveData", BindingFlags.NonPublic | BindingFlags.Instance);
            Assert.IsTrue((bool)receive.Invoke(handler, new object[] { new byte[6], 6 }));
            Assert.IsFalse((bool)receive.Invoke(handler, new object[] { new byte[6], 6 }));
            Assert.Throws<IOException>(() => handler.GetBody());
        }

        [Test]
        public void OversizedContentLengthPreventsApiBodyConsumption()
        {
            using var handler = new BoundedDownloadHandler(10);
            typeof(BoundedDownloadHandler)
                .GetMethod("ReceiveContentLengthHeader", BindingFlags.NonPublic | BindingFlags.Instance)
                .Invoke(handler, new object[] { 11UL });
            Assert.Throws<IOException>(() => handler.GetBody());
        }
    }
}
