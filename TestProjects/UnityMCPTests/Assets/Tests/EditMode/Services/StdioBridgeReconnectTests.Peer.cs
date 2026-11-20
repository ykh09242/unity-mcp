using System;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Text;
using MCPForUnity.Editor.Services.Transport.Transports;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;

namespace MCPForUnityTests.Editor.Services
{
    public partial class StdioBridgeReconnectTests
    {
        private const int ConnectTimeoutMs = 5000;
        private const int ReadTimeoutMs = 10000;

        internal static TcpClient ConnectProgressPeer(int port, string token, JToken offered, out JObject reply, bool expectAdmitted = true)
        {
            var client = new TcpClient();
            try
            {
                Assert.IsTrue(client.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs));
                var stream = client.GetStream();
                string[] banner = ReadLine(stream, ReadTimeoutMs).Trim().Split(' ');
                string generation = banner.Single(part => part.StartsWith("SERVER=", StringComparison.Ordinal)).Substring(7);
                string challenge = banner.Single(part => part.StartsWith("CHALLENGE=", StringComparison.Ordinal)).Substring(10);
                string nonce = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
                var request = new JObject
                {
                    ["type"] = "authenticate",
                    ["version"] = 2,
                    ["client_nonce"] = nonce,
                    ["proof"] = StdioBridgeAuthentication.Proof(token, "client", generation, challenge, nonce),
                };
                if (offered != null)
                    request["capabilities"] = offered;
                SendFrame(stream, Encoding.UTF8.GetBytes(request.ToString(Formatting.None)));
                reply = JObject.Parse(Encoding.UTF8.GetString(ReadFrame(stream, ReadTimeoutMs)));
                if (!expectAdmitted)
                {
                    Assert.AreEqual("authentication_refused", reply.Value<string>("type"));
                    Assert.AreEqual("client_admission_refused", reply.Value<string>("code"));
                    return client;
                }
                Assert.AreEqual("authenticated", reply.Value<string>("type"));
                Assert.AreEqual(2, reply.Value<int>("version"));
                string session = reply.Value<string>("session_id");
                Assert.IsTrue(Guid.TryParseExact(session, "N", out _));
                Assert.AreEqual(StdioBridgeAuthentication.Proof(token, "server", generation, challenge, nonce, session), reply.Value<string>("proof"));
                return client;
            }
            catch
            {
                client.Dispose();
                throw;
            }
        }

        private static string ReadLine(NetworkStream stream, int timeoutMs)
        {
            var sb = new StringBuilder();
            var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            stream.ReadTimeout = timeoutMs;

            while (DateTime.UtcNow < deadline)
            {
                int b = stream.ReadByte();
                if (b < 0)
                    throw new IOException("Connection closed while reading line");
                if (b == '\n')
                    return sb.ToString();
                sb.Append((char)b);
            }
            throw new TimeoutException("Timed out reading line from stream");
        }

        internal static void SendFrame(NetworkStream stream, byte[] payload)
        {
            byte[] header = new byte[8];
            ulong len = (ulong)payload.LongLength;
            header[0] = (byte)(len >> 56);
            header[1] = (byte)(len >> 48);
            header[2] = (byte)(len >> 40);
            header[3] = (byte)(len >> 32);
            header[4] = (byte)(len >> 24);
            header[5] = (byte)(len >> 16);
            header[6] = (byte)(len >> 8);
            header[7] = (byte)(len);
            stream.Write(header, 0, 8);
            stream.Write(payload, 0, payload.Length);
            stream.Flush();
        }

        internal static byte[] ReadFrame(NetworkStream stream, int timeoutMs)
        {
            stream.ReadTimeout = timeoutMs;

            byte[] header = ReadExact(stream, 8, timeoutMs);
            ulong payloadLen =
                ((ulong)header[0] << 56)
                | ((ulong)header[1] << 48)
                | ((ulong)header[2] << 40)
                | ((ulong)header[3] << 32)
                | ((ulong)header[4] << 24)
                | ((ulong)header[5] << 16)
                | ((ulong)header[6] << 8)
                | header[7];

            if (payloadLen == 0 || payloadLen > 16 * 1024 * 1024)
                throw new IOException($"Invalid frame length: {payloadLen}");

            return ReadExact(stream, (int)payloadLen, timeoutMs);
        }

        private static byte[] ReadExact(NetworkStream stream, int count, int timeoutMs)
        {
            byte[] buffer = new byte[count];
            int offset = 0;
            var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);

            while (offset < count)
            {
                if (DateTime.UtcNow > deadline)
                    throw new TimeoutException($"Timed out reading {count} bytes (got {offset})");

                int remaining = count - offset;
                int read = stream.Read(buffer, offset, remaining);
                if (read == 0)
                    throw new IOException("Connection closed before reading expected bytes");
                offset += read;
            }

            return buffer;
        }
    }
}
