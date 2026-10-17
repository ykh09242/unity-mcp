using System;
using System.Collections;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Services.Transport;
using MCPForUnity.Editor.Services.Transport.Transports;
using MCPForUnity.Editor.Tools;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace MCPForUnityTests.Editor.Services
{
    /// <summary>
    /// Tests that StdioBridgeHost correctly handles client reconnection scenarios.
    /// After an abrupt client disconnect, a new client must be able to connect and
    /// have its commands processed — this was broken by the zombie state bug (#785).
    /// </summary>
    [TestFixture]
    [Parallelizable(ParallelScope.None)]
    public class StdioBridgeReconnectTests
    {
        private const int ConnectTimeoutMs = 5000;
        private const int ReadTimeoutMs = 10000;

        private sealed class TimingValue
        {
            internal int Reads;
            internal string Text;
            public string Payload
            {
                get
                {
                    Reads++;
                    return Text;
                }
            }
        }

        // Only admitted by sole QA in a fresh owned scratch Editor. Never stops an existing host.
        private sealed class TimingHostScope : IDisposable
        {
            private const BindingFlags Flags = BindingFlags.NonPublic | BindingFlags.Static;
            private readonly FieldInfo _logging = typeof(McpLogRecord).GetField("_isEnabledCached", Flags);
            private readonly FieldInfo _sampling = typeof(CommandPhaseTiming).GetField("_admissions", Flags);
            private readonly bool _previousLogging;
            private readonly long _previousSampling;
            private readonly IDictionary _handlers;
            private readonly object _previousHandler;
            private readonly string _logPath;
            private readonly long _logOffset;
            private readonly System.Collections.Generic.List<Task> _work = new();
            internal readonly string Name = "core13_owned_" + Guid.NewGuid().ToString("N");
            internal readonly string Token = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N");
            internal int Port => StdioBridgeHost.GetCurrentPort();

            internal Task<T> Track<T>(Task<T> task)
            {
                _work.Add(task);
                return task;
            }

            internal TimingHostScope(Func<JObject, Task<object>> handler)
            {
                Assert.IsFalse(StdioBridgeHost.IsRunning, "This fixture requires an idle owned scratch Editor; never replace another host.");
                CommandRegistry.Initialize();
                _handlers = (IDictionary)typeof(CommandRegistry).GetField("_handlers", Flags).GetValue(null);
                _previousHandler = _handlers[Name];
                _previousLogging = (bool)_logging.GetValue(null);
                _previousSampling = (long)_sampling.GetValue(null);
                _logPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "Library", "MCPForUnity", "Logs", "mcp.log"));
                _logOffset = File.Exists(_logPath) ? new FileInfo(_logPath).Length : 0;
                Assert.Less(_logOffset, 512 * 1024, "Use a fresh owned case log; do not truncate or rotate pre-existing evidence.");
                try
                {
                    var handlerType = typeof(CommandRegistry).Assembly.GetType("MCPForUnity.Editor.Tools.HandlerInfo", true);
                    _handlers[Name] = Activator.CreateInstance(handlerType, new object[] { Name, null, handler });
                    // Cache only: do not call the EditorPrefs-writing property setter.
                    _logging.SetValue(null, true);
                    _sampling.SetValue(null, 0L);
                    StdioBridgeHost.StartOwned(0, Token);
                    Assert.IsTrue(StdioBridgeHost.IsRunning);
                }
                catch
                {
                    Dispose();
                    throw;
                }
            }

            internal JObject[] TimingRows()
            {
                lock (typeof(McpLogRecord).GetField("_logLock", Flags).GetValue(null))
                {
                    if (!File.Exists(_logPath))
                        return Array.Empty<JObject>();
                    using var stream = new FileStream(_logPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
                    Assert.GreaterOrEqual(stream.Length, _logOffset, "Owned fixture log unexpectedly rotated.");
                    stream.Position = _logOffset;
                    using var reader = new StreamReader(stream, Encoding.UTF8);
                    var rows = new System.Collections.Generic.List<JObject>();
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        var row = JObject.Parse(line);
                        if (row.Value<string>("event") == "phase_timing" && row.Value<string>("transport") == "stdio")
                            rows.Add(row);
                    }
                    return rows.ToArray();
                }
            }

            public void Dispose()
            {
                try
                {
                    StdioBridgeHost.Stop();
                    foreach (var connection in _work.OfType<Task<TcpClient>>())
                        if (connection.Status == TaskStatus.RanToCompletion)
                            connection.Result.Dispose();
                    try
                    {
                        Task.WaitAll(_work.ToArray(), 5000);
                    }
                    catch (AggregateException) { /* Read failures after an owned disconnect are observed below. */ }
                    foreach (var task in _work)
                        if (task.IsFaulted)
                            _ = task.Exception;
                    foreach (var connection in _work.OfType<Task<TcpClient>>())
                        if (connection.Status == TaskStatus.RanToCompletion)
                            connection.Result.Dispose();
                    Assert.IsTrue(_work.All(task => task.IsCompleted), "Owned network tasks must be terminal after host/socket cleanup.");
                }
                finally
                {
                    if (_previousHandler == null)
                        _handlers.Remove(Name);
                    else
                        _handlers[Name] = _previousHandler;
                    _logging.SetValue(null, _previousLogging);
                    _sampling.SetValue(null, _previousSampling);
                }
            }
        }

        private static TcpClient ConnectTimingPeer(int port, string token)
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
                SendFrame(stream, Encoding.UTF8.GetBytes(request.ToString(Formatting.None)));
                var response = JObject.Parse(Encoding.UTF8.GetString(ReadFrame(stream, ReadTimeoutMs)));
                Assert.AreEqual("authenticated", response.Value<string>("type"));
                Assert.AreEqual(2, response.Value<int>("version"));
                string session = response.Value<string>("session_id");
                Assert.IsTrue(Guid.TryParseExact(session, "N", out _));
                Assert.AreEqual(StdioBridgeAuthentication.Proof(token, "server", generation, challenge, nonce, session), response.Value<string>("proof"));
                return client;
            }
            catch
            {
                client.Dispose();
                throw;
            }
        }

        private static IEnumerator WaitTimingTask(Task task, bool allowFault = false)
        {
            var watch = System.Diagnostics.Stopwatch.StartNew();
            while (!task.IsCompleted && watch.ElapsedMilliseconds < 20000)
                yield return null;
            Assert.IsTrue(task.IsCompleted, "Owned timing task exceeded its finite watchdog.");
            if (task.IsFaulted)
            {
                var observed = task.Exception;
                if (!allowFault)
                    throw observed.GetBaseException();
            }
        }

        private static IEnumerator WaitTimingCondition(Func<bool> predicate)
        {
            var watch = System.Diagnostics.Stopwatch.StartNew();
            while (!predicate() && watch.ElapsedMilliseconds < 20000)
                yield return null;
            Assert.IsTrue(predicate(), "Owned timing condition exceeded its finite watchdog.");
        }

        private static byte[] ExpectedTimingFrame(byte[] payload)
        {
            var frame = new byte[8 + payload.Length];
            ulong length = (ulong)payload.Length;
            for (int i = 7; i >= 0; i--)
            {
                frame[i] = (byte)length;
                length >>= 8;
            }
            Buffer.BlockCopy(payload, 0, frame, 8, payload.Length);
            return frame;
        }

        private static byte[] ReadTimingFrame(NetworkStream stream)
        {
            stream.ReadTimeout = ReadTimeoutMs;
            byte[] header = ReadExact(stream, 8, ReadTimeoutMs);
            ulong length = 0;
            foreach (byte part in header)
                length = (length << 8) | part;
            if (length == 0 || length > 16 * 1024 * 1024)
                throw new IOException("Invalid owned timing frame length");
            byte[] payload = ReadExact(stream, (int)length, ReadTimeoutMs);
            var frame = new byte[8 + payload.Length];
            Buffer.BlockCopy(header, 0, frame, 0, 8);
            Buffer.BlockCopy(payload, 0, frame, 8, payload.Length);
            return frame;
        }

        [UnityTest]
        public IEnumerator TimingOwnedHostNormalAndLargeUnicodeFramesPreserveExactBytes()
        {
            TimingValue current = null;
            int calls = 0;
            using var scope = new TimingHostScope(_ =>
            {
                Interlocked.Increment(ref calls);
                return Task.FromResult<object>(current);
            });
            var connected = scope.Track(Task.Run(() => ConnectTimingPeer(scope.Port, scope.Token)));
            yield return WaitTimingTask(connected);
            using var peer = connected.Result;
            foreach (int characters in new[] { 32, 300000 })
            {
                string text = new string('\u6f22', characters) + "\ud83d\ude80";
                current = new TimingValue { Text = text };
                byte[] expected = Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(new { status = "success", result = new { Payload = text } }));
                string command = JsonConvert.SerializeObject(new { type = scope.Name });
                SendFrame(peer.GetStream(), Encoding.UTF8.GetBytes(command));
                var received = scope.Track(Task.Run(() => ReadTimingFrame(peer.GetStream())));
                yield return WaitTimingTask(received);
                CollectionAssert.AreEqual(ExpectedTimingFrame(expected), received.Result);
                Assert.AreEqual(1, current.Reads);
                yield return WaitTimingCondition(() => StdioBridgeHost.QueuedCommandCount == 0);
            }
            Assert.AreEqual(2, calls);
            yield return WaitTimingCondition(() => scope.TimingRows().Length == 1);
            var row = scope.TimingRows().Single();
            Assert.AreEqual("sent", row.Value<string>("delivery"));
            Assert.IsNotNull(row["serialization_ms"]);
            Assert.IsNotNull(row["send_ms"]);
            StringAssert.DoesNotContain("Payload", row.ToString());
        }

        [UnityTest]
        public IEnumerator TimingOwnedResendPreservesCanceledEnvelopeAndOneExecutionUntilSettlement()
        {
            var deferred = new TaskCompletionSource<object>(TaskCreationOptions.RunContinuationsAsynchronously);
            int calls = 0;
            using var scope = new TimingHostScope(_ =>
                Interlocked.Increment(ref calls) == 1 ? deferred.Task : Task.FromResult<object>(new { Payload = "after-settlement" })
            );
            TcpClient first = null;
            TcpClient second = null;
            Task<byte[]> firstRead = null;
            try
            {
                var firstConnected = scope.Track(Task.Run(() => ConnectTimingPeer(scope.Port, scope.Token)));
                yield return WaitTimingTask(firstConnected);
                first = firstConnected.Result;
                string command = JsonConvert.SerializeObject(new { type = scope.Name });
                SendFrame(first.GetStream(), Encoding.UTF8.GetBytes(command));
                firstRead = scope.Track(Task.Run(() => ReadTimingFrame(first.GetStream())));
                yield return WaitTimingCondition(() => Volatile.Read(ref calls) == 1);
                var secondConnected = scope.Track(Task.Run(() => ConnectTimingPeer(scope.Port, scope.Token)));
                yield return WaitTimingTask(secondConnected);
                second = secondConnected.Result;
                SendFrame(second.GetStream(), Encoding.UTF8.GetBytes(command));
                var secondRead = scope.Track(Task.Run(() => ReadTimingFrame(second.GetStream())));
                yield return WaitTimingTask(secondRead);
                int fallback = (int)typeof(StdioBridgeHost).GetField("FrameIOTimeoutMs", BindingFlags.NonPublic | BindingFlags.Static).GetValue(null);
                int timeout = StdioBridgeHost.ResolveCommandTimeoutMs(command, fallback);
                byte[] expected = Encoding.UTF8.GetBytes(
                    JsonConvert.SerializeObject(new { status = "error", error = $"Command processing timed out after {timeout} ms" })
                );
                CollectionAssert.AreEqual(ExpectedTimingFrame(expected), secondRead.Result);
                Assert.AreEqual(1, Volatile.Read(ref calls));
                Assert.IsFalse(deferred.Task.IsCompleted);
                Assert.AreEqual(0, scope.TimingRows().Length, "Do not finalize execution timing merely because delivery was canceled.");
                deferred.SetResult(new { Payload = "late-original" });
                yield return WaitTimingCondition(() => StdioBridgeHost.QueuedCommandCount == 0 && scope.TimingRows().Length == 1);
                var row = scope.TimingRows().Single();
                Assert.AreEqual("canceled", row.Value<string>("delivery"));
                Assert.IsTrue(row.Value<bool>("execution_settled_after_delivery"));
                Assert.IsNotNull(row["handler_ms"]);
                Assert.IsNull(row["send_ms"]);
                SendFrame(second.GetStream(), Encoding.UTF8.GetBytes(command));
                var followup = scope.Track(Task.Run(() => ReadTimingFrame(second.GetStream())));
                yield return WaitTimingTask(followup);
                CollectionAssert.AreEqual(
                    ExpectedTimingFrame(Encoding.UTF8.GetBytes("{\"status\":\"success\",\"result\":{\"Payload\":\"after-settlement\"}}")),
                    followup.Result
                );
                Assert.AreEqual(2, Volatile.Read(ref calls), "Only the post-settlement follow-up may start another execution.");
                yield return WaitTimingCondition(() => StdioBridgeHost.QueuedCommandCount == 0);
            }
            finally
            {
                deferred.TrySetCanceled();
                first?.Dispose();
                second?.Dispose();
                if (firstRead?.IsFaulted == true)
                    _ = firstRead.Exception;
            }
        }

        [UnityTest]
        public IEnumerator NewClient_AfterAbruptDisconnect_CanSendAndReceiveCommands()
        {
            if (!StdioBridgeHost.IsRunning)
            {
                Assert.Ignore("StdioBridgeHost is not running; skipping reconnect test.");
                yield break;
            }

            int port = StdioBridgeHost.GetCurrentPort();

            // --- First client: connect, verify ping/pong, then abruptly close ---
            using (var client1 = new TcpClient())
            {
                Assert.IsTrue(client1.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "First client connect timed out");
                client1.ReceiveTimeout = ReadTimeoutMs;
                var stream1 = client1.GetStream();

                string handshake1 = ReadLine(stream1, ReadTimeoutMs);
                Assert.That(handshake1, Does.Contain("FRAMING=1"), "First client should receive handshake");

                // Send a framed ping
                SendFrame(stream1, Encoding.UTF8.GetBytes("ping"));
                byte[] pongBytes = ReadFrame(stream1, ReadTimeoutMs);
                string pong1 = Encoding.UTF8.GetString(pongBytes);
                Assert.That(pong1, Does.Contain("pong"), "First client should get pong response");

                // Abrupt close — simulates server crash / domain reload disconnect
                client1.Client.LingerState = new LingerOption(true, 0);
                client1.Close();
            }

            // Wait a few frames for cleanup
            for (int i = 0; i < 10; i++)
                yield return null;

            // --- Second client: connect and verify commands still work ---
            using (var client2 = new TcpClient())
            {
                Assert.IsTrue(client2.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "Second client connect timed out");
                client2.ReceiveTimeout = ReadTimeoutMs;
                var stream2 = client2.GetStream();

                string handshake2 = ReadLine(stream2, ReadTimeoutMs);
                Assert.That(handshake2, Does.Contain("FRAMING=1"), "Second client should receive handshake");

                // Send a framed ping — this is the critical check that would fail
                // if the bridge is in zombie state.
                SendFrame(stream2, Encoding.UTF8.GetBytes("ping"));
                byte[] pongBytes2 = ReadFrame(stream2, ReadTimeoutMs);
                string pong2 = Encoding.UTF8.GetString(pongBytes2);
                Assert.That(pong2, Does.Contain("pong"), "Second client should get pong response after reconnect");

                client2.Close();
            }
        }

        [UnityTest]
        public IEnumerator NewClient_WhileOldClientStillConnected_ClosesStaleClient()
        {
            if (!StdioBridgeHost.IsRunning)
            {
                Assert.Ignore("StdioBridgeHost is not running; skipping reconnect test.");
                yield break;
            }

            int port = StdioBridgeHost.GetCurrentPort();

            // --- First client: connect and verify handshake (but don't close) ---
            var client1 = new TcpClient();
            try
            {
                Assert.IsTrue(client1.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "First client connect timed out");
                client1.ReceiveTimeout = ReadTimeoutMs;
                var stream1 = client1.GetStream();

                string handshake1 = ReadLine(stream1, ReadTimeoutMs);
                Assert.That(handshake1, Does.Contain("FRAMING=1"), "First client should receive handshake");

                // Verify ping works on first client
                SendFrame(stream1, Encoding.UTF8.GetBytes("ping"));
                byte[] pong1Bytes = ReadFrame(stream1, ReadTimeoutMs);
                Assert.That(Encoding.UTF8.GetString(pong1Bytes), Does.Contain("pong"));

                // --- Second client: connect while first is still open ---
                using (var client2 = new TcpClient())
                {
                    Assert.IsTrue(client2.ConnectAsync("127.0.0.1", port).Wait(ConnectTimeoutMs), "Second client connect timed out");
                    client2.ReceiveTimeout = ReadTimeoutMs;
                    var stream2 = client2.GetStream();

                    string handshake2 = ReadLine(stream2, ReadTimeoutMs);
                    Assert.That(handshake2, Does.Contain("FRAMING=1"), "Second client should receive handshake");

                    // Stale-client cleanup runs synchronously in HandleClientAsync before
                    // the read loop, so by the time we read the handshake it's already done.
                    // No yield needed — yielding here creates a window for the MCP Python
                    // server to reconnect and close our test client as stale.
                    SendFrame(stream2, Encoding.UTF8.GetBytes("ping"));
                    byte[] pong2Bytes = ReadFrame(stream2, ReadTimeoutMs);
                    Assert.That(Encoding.UTF8.GetString(pong2Bytes), Does.Contain("pong"), "Second client should get pong after stale client cleanup");

                    client2.Close();
                }

                // First client should now be disconnected by the bridge.
                // A read attempt should throw or return 0 bytes.
                yield return null;
                bool firstClientDisconnected = false;
                try
                {
                    SendFrame(stream1, Encoding.UTF8.GetBytes("ping"));
                    ReadFrame(stream1, 2000);
                }
                catch
                {
                    firstClientDisconnected = true;
                }

                Assert.IsTrue(firstClientDisconnected, "First client should be disconnected after second client connects");
            }
            finally
            {
                try
                {
                    client1.Close();
                }
                catch { }
            }
        }

        #region Frame protocol helpers

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

        private static void SendFrame(NetworkStream stream, byte[] payload)
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

        private static byte[] ReadFrame(NetworkStream stream, int timeoutMs)
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

        #endregion
    }
}
