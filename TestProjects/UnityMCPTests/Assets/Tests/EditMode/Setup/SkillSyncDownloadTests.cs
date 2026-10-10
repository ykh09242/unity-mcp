using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Setup;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Setup
{
    public class SkillSyncDownloadTests
    {
        private const BindingFlags Internal = BindingFlags.Static | BindingFlags.NonPublic;
        private static readonly Type Service = typeof(SkillSyncService);

        private static object Invoke(MethodInfo method, params object[] arguments)
        {
            try
            {
                return method.Invoke(null, arguments);
            }
            catch (TargetInvocationException e)
            {
                throw e.InnerException ?? e;
            }
        }

        private static string Download(HttpMessageHandler handler, int limit = 8)
        {
            MethodInfo method = Service.GetMethod("DownloadString", Internal);
            Type clientType = method.GetParameters()[0].ParameterType;
            using var client = (System.Net.Http.HttpClient)Activator.CreateInstance(clientType, handler);
            return (string)Invoke(
                method,
                method.GetParameters().Length == 2
                    ? new object[] { client, "https://fixture.invalid/tree" }
                    : new object[] { client, "https://fixture.invalid/tree", limit }
            );
        }

        [TestCase(null)]
        [TestCase(1L)]
        public void ActualBytes_MissingOrMisleadingHeader_RejectBeforeOverrun(long? declared)
        {
            var stream = new ObservedStream("123456789", 2);
            var handler = new ResponseHandler(stream, declared);
            Assert.Throws<IOException>(() => Download(handler));
            Assert.AreEqual(9, stream.BytesRead);
            Assert.LessOrEqual(stream.LargestRequest, 9);
            Assert.IsTrue(stream.Disposed);
        }

        [Test]
        public void DeclaredOverLimit_RejectsBeforeReadingContent()
        {
            var stream = new ObservedStream("tiny", 1);
            Assert.Throws<IOException>(() => Download(new ResponseHandler(stream, 9)));
            Assert.AreEqual(0, stream.BytesRead);
            Assert.IsTrue(stream.Disposed);
        }

        [TestCase(null)]
        [TestCase(8L)]
        public void ExactByteBoundary_PartialReadsSucceed(long? declared)
        {
            var stream = new ObservedStream("12345678", 2);
            Assert.AreEqual("12345678", Download(new ResponseHandler(stream, declared)));
            Assert.AreEqual(8, stream.BytesRead);
            Assert.IsTrue(stream.Disposed);
        }

        [Test]
        public void FailedHttpStatus_DoesNotReadErrorBody()
        {
            var stream = new ObservedStream("fixture error body", 1);
            Assert.Throws<InvalidOperationException>(() => Download(new ResponseHandler(stream, null, HttpStatusCode.BadGateway)));
            Assert.AreEqual(0, stream.BytesRead);
            Assert.IsTrue(stream.Disposed);
        }

        [Test]
        public void CancellationDuringBodyRead_DisposesContentWithoutResult()
        {
            var stream = new ObservedStream("cancel", 2) { CancelRead = true };
            Assert.Catch<OperationCanceledException>(() => Download(new ResponseHandler(stream, null)));
            Assert.IsTrue(stream.SawCancelableToken);
            Assert.IsTrue(stream.Disposed);
        }

        [Test]
        public void TextDecode_BomAndUnicodePreserved()
        {
            var stream = new ObservedStream("\ufeff\uD55C\uAE00", 2);
            Assert.AreEqual("\uD55C\uAE00", Download(new ResponseHandler(stream, null), 32));
        }

        [Test]
        public void AggregateBudget_ExactBoundaryAndRemainingAdmissionWithoutLargeArrays()
        {
            Type type = Service.Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncDownload+ByteBudget");
            object budget = Activator.CreateInstance(type, true);
            MethodInfo admit = type.GetMethod("Admit", BindingFlags.Instance | BindingFlags.NonPublic);
            PropertyInfo next = type.GetProperty("NextBlobLimit", BindingFlags.Instance | BindingFlags.NonPublic);
            PropertyInfo remaining = type.GetProperty("Remaining", BindingFlags.Instance | BindingFlags.NonPublic);
            const long blob = 64L * 1024 * 1024;
            for (int i = 0; i < 3; i++)
                admit.Invoke(budget, new object[] { blob });
            admit.Invoke(budget, new object[] { blob - 4 });
            Assert.AreEqual(4L, next.GetValue(budget));
            admit.Invoke(budget, new object[] { 4L });
            Assert.AreEqual(0L, remaining.GetValue(budget));
            Assert.AreEqual(0L, next.GetValue(budget));
            var error = Assert.Throws<TargetInvocationException>(() => admit.Invoke(budget, new object[] { 1L }));
            Assert.IsInstanceOf<IOException>(error.InnerException);
            Assert.AreEqual(0L, remaining.GetValue(budget));
            error = Assert.Throws<TargetInvocationException>(() => admit.Invoke(budget, new object[] { long.MaxValue }));
            Assert.IsInstanceOf<IOException>(error.InnerException);
        }

        [TestCase("", true)]
        [TestCase("x", false)]
        public void ZeroRemainingBodyBudget_OnlyEmptyContentSucceeds(string body, bool accepted)
        {
            Type type = Service.Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncDownload");
            MethodInfo method = type.GetMethod("ReadBounded", Internal);
            var stream = new ObservedStream(body, 1);
            if (accepted)
                CollectionAssert.IsEmpty((byte[])Invoke(method, stream, 0L, CancellationToken.None));
            else
                Assert.Throws<IOException>(() => Invoke(method, stream, 0L, CancellationToken.None));
            Assert.LessOrEqual(stream.BytesRead, 1);
            Assert.LessOrEqual(stream.LargestRequest, 1);
            stream.Dispose();
        }

        [TestCase(4096L, true)]
        [TestCase(4097L, false)]
        [TestCase(long.MaxValue, false)]
        public void FileCount_ExactBoundaryAndOverflow(long count, bool accepted)
        {
            Type type = Service.Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncDownload");
            MethodInfo method = type.GetMethod("RequireFileCount", Internal);
            if (accepted)
                Invoke(method, count);
            else
                Assert.Throws<IOException>(() => Invoke(method, count));
        }

        [TestCase("{\"tree\":[{},{},{}]}", true)]
        [TestCase("{\"tree\":[{},{},{},{}]}", false)]
        [TestCase("{\"tree\":[{},null,{},{}]}", false)]
        [TestCase("{\"tree\":[{}],\"tree\":[{}]}", false)]
        [TestCase("{\"Tree\":[{}]}", false)]
        [TestCase("{\"TREE\":[{}]}", false)]
        [TestCase("{\"tree\":[{}],\"Tree\":[{}]}", false)]
        public void TreeEntries_StreamingCountPrecedesArrayMaterialization(string json, bool accepted)
        {
            Type type = Service.Assembly.GetType("MCPForUnity.Editor.Setup.SkillSyncDownload");
            MethodInfo method = type.GetMethod("ValidateTreeJson", Internal);
            if (accepted)
                Invoke(method, json, 3);
            else
                Assert.Throws<IOException>(() => Invoke(method, json, 3));
        }

        private sealed class ResponseHandler : HttpMessageHandler
        {
            private readonly Stream _stream;
            private readonly long? _declared;
            private readonly HttpStatusCode _status;

            public ResponseHandler(Stream stream, long? declared, HttpStatusCode status = HttpStatusCode.OK)
            {
                _stream = stream;
                _declared = declared;
                _status = status;
            }

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken token)
            {
                var content = new StreamContent(_stream);
                if (_declared.HasValue)
                    content.Headers.ContentLength = _declared;
                return Task.FromResult(new HttpResponseMessage(_status) { Content = content });
            }
        }

        private sealed class ObservedStream : Stream
        {
            private readonly MemoryStream _inner;
            private readonly int _chunk;
            public int BytesRead,
                LargestRequest;
            public bool Disposed,
                CancelRead,
                SawCancelableToken;

            public ObservedStream(string text, int chunk)
            {
                _inner = new MemoryStream(Encoding.UTF8.GetBytes(text));
                _chunk = chunk;
            }

            // Prevent StreamContent inferring a Content-Length when the scenario omits it.
            public override bool CanSeek => false;
            public override bool CanRead => true;
            public override bool CanWrite => false;
            public override long Length => throw new NotSupportedException();
            public override long Position
            {
                get => throw new NotSupportedException();
                set => throw new NotSupportedException();
            }

            public override void Flush() { }

            public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

            public override void SetLength(long value) => throw new NotSupportedException();

            public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

            public override int Read(byte[] buffer, int offset, int count)
            {
                LargestRequest = Math.Max(LargestRequest, count);
                int read = _inner.Read(buffer, offset, Math.Min(count, _chunk));
                BytesRead += read;
                return read;
            }

            public override Task<int> ReadAsync(byte[] buffer, int offset, int count, CancellationToken token)
            {
                SawCancelableToken |= token.CanBeCanceled;
                if (CancelRead)
                    return Task.FromCanceled<int>(new CancellationToken(true));
                return Task.FromResult(Read(buffer, offset, count));
            }

            protected override void Dispose(bool disposing)
            {
                Disposed = true;
                if (disposing)
                    _inner.Dispose();
                base.Dispose(disposing);
            }
        }
    }
}
