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
