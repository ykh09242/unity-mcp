using System;
using System.IO;
using System.Text;
using System.Threading;
using MCPForUnity.Editor.Services.Blender;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Blender
{
    /// <summary>
    /// Covers the framing-free protocol core (parse-when-complete + status unwrap) without a socket.
    /// </summary>
    public class BlenderSocketClientTests
    {
        private static byte[] Bytes(string s) => Encoding.UTF8.GetBytes(s);

        [Test]
        public void TryParseResponse_IsFalse_ForAPartialPrefix()
        {
            byte[] buf = Bytes("{\"status\": \"success\", \"result\": {\"name\": \"Sce");
            Assert.IsFalse(BlenderSocketClient.TryParseResponse(buf, buf.Length, out JObject parsed));
            Assert.IsNull(parsed);
        }

        [Test]
        public void TryParseResponse_IsTrue_OnceTheObjectIsComplete()
        {
            byte[] buf = Bytes("{\"status\": \"success\", \"result\": {\"name\": \"Scene\", \"object_count\": 3}}");
            Assert.IsTrue(BlenderSocketClient.TryParseResponse(buf, buf.Length, out JObject parsed));
            Assert.AreEqual("Scene", (string)parsed["result"]["name"]);
        }

        [Test]
        public void TryParseResponse_HonoursLength_NotBufferCapacity()
        {
            string json = "{\"status\": \"success\", \"result\": 1}";
            byte[] buf = new byte[256];
            byte[] src = Bytes(json);
            src.CopyTo(buf, 0);
            Assert.IsTrue(BlenderSocketClient.TryParseResponse(buf, src.Length, out JObject parsed));
            Assert.AreEqual(1, (int)parsed["result"]);
        }

        [Test]
        public void Unwrap_ReturnsResult_OnSuccess()
        {
            var response = JObject.Parse("{\"status\": \"success\", \"result\": {\"executed\": true}}");
            JToken result = BlenderSocketClient.Unwrap(response, "execute_code");
            Assert.IsTrue((bool)result["executed"]);
        }

        [Test]
        public void Unwrap_Throws_WithAddonMessage_OnError()
        {
            var response = JObject.Parse("{\"status\": \"error\", \"message\": \"boom\"}");
            var ex = Assert.Throws<BlenderCommandException>(() => BlenderSocketClient.Unwrap(response, "get_scene_info"));
            StringAssert.Contains("boom", ex.Message);
            StringAssert.Contains("get_scene_info", ex.Message);
        }

        [Test]
        public void Unwrap_Rejects_UnknownOrMissingStatus()
        {
            var unknown = JObject.Parse("{\"status\": \"pending\", \"result\": 1}");
            var ex = Assert.Throws<InvalidDataException>(() => BlenderSocketClient.Unwrap(unknown, "get_scene_info"));
            StringAssert.Contains("pending", ex.Message);

            var missing = JObject.Parse("{\"result\": 1}");
            Assert.Throws<InvalidDataException>(() => BlenderSocketClient.Unwrap(missing, "get_scene_info"));
        }

        [Test]
        public void BlenderEndpoint_FormatsAsHostPort()
        {
            Assert.AreEqual("127.0.0.1:9876", new BlenderEndpoint("127.0.0.1", 9876).ToString());
        }

        [Test]
        public void Unwrap_RejectsSuccessWithoutResult()
        {
            Assert.Throws<InvalidDataException>(() => BlenderSocketClient.Unwrap(JObject.Parse("{\"status\":\"success\"}"), "get_scene_info"));
        }

        [Test]
        public void ReadResponse_RejectsUnboundedIncompleteJson()
        {
            using var stream = new TestStream(new byte[BlenderSocketClient.MaxResponseBytes + 1]);
            Assert.Throws<InvalidDataException>(() => BlenderSocketClient.ReadResponse(stream, "get_scene_info", 10));
        }

        [Test]
        public void ReadResponse_UsesRemainingDeadlineForEachRead()
        {
            using var stream = new TestStream(Bytes("{\"status\":\"success\",\"result\":1}"), split: true);
            Assert.AreEqual(1, (int)BlenderSocketClient.ReadResponse(stream, "get_scene_info", 1));
            Assert.Less(stream.LastTimeout, stream.FirstTimeout);
        }

        [Test]
        public void Send_WithCancelledToken_DoesNotConnect()
        {
            using var cancellation = new CancellationTokenSource();
            cancellation.Cancel();
            Assert.Throws<OperationCanceledException>(() => BlenderSocketClient.Send(new BlenderEndpoint("invalid", 0), "get_scene_info", cancellationToken: cancellation.Token));
        }

        private sealed class TestStream : MemoryStream
        {
            private readonly bool split;
            private int reads;
            public int FirstTimeout { get; private set; }
            public int LastTimeout { get; private set; }
            public override int ReadTimeout
            {
                get => LastTimeout;
                set { if (FirstTimeout == 0) FirstTimeout = value; LastTimeout = value; }
            }
            public TestStream(byte[] bytes, bool split = false) : base(bytes) { this.split = split; }
            public override int Read(byte[] buffer, int offset, int count)
            {
                if (split && reads++ == 0)
                {
                    Thread.Sleep(30);
                    count = 1;
                }
                return base.Read(buffer, offset, count);
            }
        }
    }
}
