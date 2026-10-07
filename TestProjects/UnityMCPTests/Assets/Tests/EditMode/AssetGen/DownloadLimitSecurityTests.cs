using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Services.AssetGen.Http;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.AssetGen
{
    public class DownloadLimitSecurityTests
    {
        [TestCase(-1)]
        [TestCase(1)]
        [TestCase(100)]
        public void ArtifactBodiesCannotExceedLimitEvenWithMissingOrFalseLength(long declaredLength)
        {
            using var stream = new MemoryStream(new byte[100]);
            Assert.ThrowsAsync<IOException>(async () => await AssetDownloadTransport.ReadLimitedAsync(stream, declaredLength, 50, CancellationToken.None));
        }

        [Test]
        public async Task ArtifactAtLimitIsAccepted()
        {
            using var stream = new MemoryStream(new byte[50]);
            Assert.AreEqual(50, (await AssetDownloadTransport.ReadLimitedAsync(stream, -1, 50, CancellationToken.None)).Length);
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
